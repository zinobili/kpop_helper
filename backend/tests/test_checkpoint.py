import logging

import pytest

from app import cache, captions, config, pipeline, translate
from app.translate import translate_cues
from app.vtt_utils import Cue


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "cache.sqlite3")


# ---------- cache: in-progress translation batches ----------


def test_get_batch_returns_none_when_not_saved():
    assert cache.get_batch("t1", "deepseek", "m", "hash") is None


def test_put_then_get_batch_round_trips_unicode():
    cache.put_batch("t1", "deepseek", "m", "hash", ["你好", "再見"])
    assert cache.get_batch("t1", "deepseek", "m", "hash") == ["你好", "再見"]


def test_batches_are_isolated_by_provider_model_transcript_and_prompt_hash():
    cache.put_batch("t1", "deepseek", "m", "hash", ["a"])
    assert cache.get_batch("t1", "gemini", "m", "hash") is None
    assert cache.get_batch("t1", "deepseek", "other-model", "hash") is None
    assert cache.get_batch("t1", "deepseek", "m", "other-hash") is None
    assert cache.get_batch("t2", "deepseek", "m", "hash") is None


def test_get_batch_treats_corrupt_json_as_a_miss():
    cache.put_batch("t1", "deepseek", "m", "hash", ["a"])
    with cache._connect() as conn:
        conn.execute("UPDATE translation_batches SET translations_json = 'not json'")
    assert cache.get_batch("t1", "deepseek", "m", "hash") is None


def test_clear_batches_only_removes_that_transcript_provider_model():
    cache.put_batch("t1", "deepseek", "m", "h1", ["a"])
    cache.put_batch("t1", "deepseek", "m", "h2", ["b"])
    cache.put_batch("t1", "gemini", "m", "h1", ["c"])
    cache.put_batch("t2", "deepseek", "m", "h1", ["d"])

    assert cache.clear_batches("t1", "deepseek", "m") == 2

    assert cache.get_batch("t1", "deepseek", "m", "h1") is None
    assert cache.get_batch("t1", "gemini", "m", "h1") == ["c"]
    assert cache.get_batch("t2", "deepseek", "m", "h1") == ["d"]


def test_clear_batches_handles_a_null_model():
    cache.put_batch("t1", "gemini", None, "h", ["a"])
    assert cache.clear_batches("t1", "gemini", None) == 1
    assert cache.get_batch("t1", "gemini", None, "h") is None


def test_purge_stale_batches_removes_only_old_rows():
    cache.put_batch("t1", "deepseek", "m", "old", ["a"])
    cache.put_batch("t1", "deepseek", "m", "new", ["b"])
    with cache._connect() as conn:
        conn.execute(
            "UPDATE translation_batches SET created_at = '2000-01-01T00:00:00+00:00' "
            "WHERE translations_json = ?",
            ('["a"]',),
        )

    assert cache.purge_stale_batches() == 1

    assert cache.get_batch("t1", "deepseek", "m", "old") is None
    assert cache.get_batch("t1", "deepseek", "m", "new") == ["b"]


def test_batch_checkpoint_counts_resumed_loads():
    checkpoint = cache.BatchCheckpoint("t1", "deepseek", "m")
    assert checkpoint.load("h") is None
    assert checkpoint.resumed == 0

    checkpoint.save("h", ["a"])
    assert checkpoint.load("h") == ["a"]
    assert checkpoint.resumed == 1

    assert checkpoint.clear() == 1
    assert checkpoint.load("h") is None


# ---------- translate_cues with a checkpoint ----------


class _DictCheckpoint:
    def __init__(self):
        self.store = {}

    def load(self, prompt_hash):
        return self.store.get(prompt_hash)

    def save(self, prompt_hash, translations):
        self.store[prompt_hash] = translations


def _echo_batches(monkeypatch, fail_on_call=None):
    """Fake provider that 'translates' by upper-casing; optionally raises a non-retryable error
    on the Nth call (1-based). Returns the list of batches it was asked for."""
    calls = []

    def fake_once(texts, source_lang, model=None, provider=None):
        calls.append(list(texts))
        if fail_on_call is not None and len(calls) == fail_on_call:
            raise RuntimeError("quota exhausted")
        return [t.upper() for t in texts]

    monkeypatch.setattr(translate, "_translate_batch_once", fake_once)
    return calls


def _cues(*texts):
    return [Cue(0, 1, t) for t in texts]


def test_batch_prompt_hash_changes_with_text_lang_and_glossary(monkeypatch):
    base = translate._batch_prompt_hash(["a", "b"], "ko")
    assert translate._batch_prompt_hash(["a", "b"], "ko") == base
    assert translate._batch_prompt_hash(["a", "c"], "ko") != base
    assert translate._batch_prompt_hash(["a", "b"], "en") != base

    monkeypatch.setattr(translate.glossary, "format_for_prompt", lambda: "- new -> term")
    assert translate._batch_prompt_hash(["a", "b"], "ko") != base


def test_translate_cues_saves_each_finished_batch_to_the_checkpoint(monkeypatch):
    monkeypatch.setattr(config, "TRANSLATE_BATCH_SIZE", 2)
    _echo_batches(monkeypatch)
    checkpoint = _DictCheckpoint()

    result = translate_cues(_cues("a", "b", "c"), "ko", checkpoint=checkpoint)

    assert result == ["A", "B", "C"]
    assert sorted(checkpoint.store.values()) == [["A", "B"], ["C"]]


def test_failed_run_keeps_finished_batches_and_rerun_only_translates_the_rest(monkeypatch):
    monkeypatch.setattr(config, "TRANSLATE_BATCH_SIZE", 1)
    checkpoint = _DictCheckpoint()
    cues = _cues("a", "b", "c", "d")

    first_calls = _echo_batches(monkeypatch, fail_on_call=3)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        translate_cues(cues, "ko", checkpoint=checkpoint)
    assert first_calls == [["a"], ["b"], ["c"]]

    second_calls = _echo_batches(monkeypatch)
    result = translate_cues(cues, "ko", checkpoint=checkpoint)

    assert result == ["A", "B", "C", "D"]
    assert second_calls == [["c"], ["d"]]  # a and b came from the checkpoint


def test_checkpoint_is_not_reused_when_the_glossary_changed(monkeypatch):
    monkeypatch.setattr(config, "TRANSLATE_BATCH_SIZE", 5)
    checkpoint = _DictCheckpoint()
    _echo_batches(monkeypatch)
    translate_cues(_cues("a"), "ko", checkpoint=checkpoint)

    monkeypatch.setattr(translate.glossary, "format_for_prompt", lambda: "- changed -> entry")
    calls = _echo_batches(monkeypatch)
    translate_cues(_cues("a"), "ko", checkpoint=checkpoint)

    assert calls == [["a"]]


def test_checkpoint_with_wrong_line_count_is_ignored(monkeypatch):
    monkeypatch.setattr(config, "TRANSLATE_BATCH_SIZE", 5)
    checkpoint = _DictCheckpoint()
    checkpoint.store[translate._batch_prompt_hash(["a", "b"], "ko")] = ["only one"]
    calls = _echo_batches(monkeypatch)

    result = translate_cues(_cues("a", "b"), "ko", checkpoint=checkpoint)

    assert result == ["A", "B"]
    assert calls == [["a", "b"]]


def test_a_failing_checkpoint_save_does_not_fail_the_translation(monkeypatch, caplog):
    monkeypatch.setattr(config, "TRANSLATE_BATCH_SIZE", 5)
    _echo_batches(monkeypatch)

    class BrokenCheckpoint(_DictCheckpoint):
        def save(self, prompt_hash, translations):
            raise OSError("database is locked")

    with caplog.at_level(logging.WARNING, logger="kpop_helper.translate"):
        result = translate_cues(_cues("a"), "ko", checkpoint=BrokenCheckpoint())

    assert result == ["A"]
    assert "Could not save checkpoint" in caplog.text


def test_resumed_batches_are_reported_in_the_summary_and_skip_on_batch(monkeypatch, caplog):
    monkeypatch.setattr(config, "TRANSLATE_BATCH_SIZE", 1)
    checkpoint = _DictCheckpoint()
    checkpoint.store[translate._batch_prompt_hash(["a"], "ko")] = ["A"]
    _echo_batches(monkeypatch)
    reported = []

    with caplog.at_level(logging.INFO, logger="kpop_helper.translate"):
        translate_cues(
            _cues("a", "b"), "ko", on_batch=lambda n, t: reported.append(n), checkpoint=checkpoint
        )

    assert reported == [2]  # the resumed batch 1 is never reported as "translating"
    assert "resumed=1" in caplog.text
    assert "batches=2/2" in caplog.text


def test_translate_cues_without_a_checkpoint_behaves_as_before(monkeypatch):
    monkeypatch.setattr(config, "TRANSLATE_BATCH_SIZE", 2)
    calls = _echo_batches(monkeypatch)
    assert translate_cues(_cues("a", "b", "c"), "ko") == ["A", "B", "C"]
    assert calls == [["a", "b"], ["c"]]


# ---------- pipeline: resuming a failed translation ----------


def _stub_one_video(monkeypatch, texts):
    monkeypatch.setattr(captions, "extract_video_id_from_url", lambda url: None)
    monkeypatch.setattr(captions, "extract_video_info", lambda url: {"id": "vid1", "title": "T"})
    cues = _cues(*texts)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: (cues, "ko", "manual", {})
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(config, "TRANSLATE_BATCH_SIZE", 1)


def _provider_calls(monkeypatch, fail_on_call=None):
    """Fakes the LLM call itself (not translate_cues), so the real checkpoint wiring runs."""
    calls = []

    def fake_once(texts, source_lang, model=None, provider=None):
        calls.append(list(texts))
        if fail_on_call is not None and len(calls) == fail_on_call:
            raise RuntimeError("quota exhausted")
        return [f"zh-{t}" for t in texts]

    monkeypatch.setattr(translate, "_translate_batch_once", fake_once)
    return calls


def test_failed_translation_resumes_from_finished_batches_on_the_next_run(monkeypatch):
    _stub_one_video(monkeypatch, ["a", "b", "c"])

    first = _provider_calls(monkeypatch, fail_on_call=3)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        pipeline.process_video("https://youtu.be/vid1")
    assert first == [["a"], ["b"], ["c"]]

    second = _provider_calls(monkeypatch)
    result = pipeline.process_video("https://youtu.be/vid1")

    assert second == [["c"]]
    assert [c["text_zh"] for c in result["cues"]] == ["zh-a", "zh-b", "zh-c"]


def test_successful_translation_clears_its_checkpoints(monkeypatch):
    _stub_one_video(monkeypatch, ["a", "b"])
    _provider_calls(monkeypatch)

    result = pipeline.process_video("https://youtu.be/vid1")

    checkpoint = cache.BatchCheckpoint(result["transcript_id"], "deepseek", "deepseek-chat")
    assert checkpoint.clear() == 0


def test_force_refresh_discards_checkpoints_and_translates_everything(monkeypatch):
    _stub_one_video(monkeypatch, ["a", "b"])
    _provider_calls(monkeypatch, fail_on_call=2)
    with pytest.raises(RuntimeError):
        pipeline.process_video("https://youtu.be/vid1")

    calls = _provider_calls(monkeypatch)
    pipeline.process_video("https://youtu.be/vid1", force_refresh=True)

    assert calls == [["a"], ["b"]]


def test_checkpoints_are_not_shared_across_providers(monkeypatch):
    _stub_one_video(monkeypatch, ["a", "b"])
    _provider_calls(monkeypatch, fail_on_call=2)
    with pytest.raises(RuntimeError):
        pipeline.process_video("https://youtu.be/vid1")  # deepseek: a saved, b failed

    calls = _provider_calls(monkeypatch)
    pipeline.process_video("https://youtu.be/vid1", provider="gemini")

    assert calls == [["a"], ["b"]]


def test_resumed_count_shows_up_in_the_translating_progress_detail(monkeypatch):
    _stub_one_video(monkeypatch, ["a", "b", "c"])
    _provider_calls(monkeypatch, fail_on_call=3)
    with pytest.raises(RuntimeError):
        pipeline.process_video("https://youtu.be/vid1")

    _provider_calls(monkeypatch)
    details = []

    def on_progress(stage, detail=None, **meta):
        if stage == "translating":
            details.append(detail)

    pipeline.process_video("https://youtu.be/vid1", on_progress=on_progress)

    assert details == ["batch 3/3 (2 resumed from an earlier run)"]
