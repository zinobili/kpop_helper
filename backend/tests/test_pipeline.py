import pytest

from app import cache, captions, config, pipeline, translate
from app.vtt_utils import Cue


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets its own empty DB, never the real project data."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "cache.sqlite3")


def _cue(text="hi"):
    return Cue(start=0.0, end=1.0, text=text)


def _stub_lookup(monkeypatch, video_id="vid1", title="T"):
    monkeypatch.setattr(captions, "extract_video_id_from_url", lambda url: None)
    monkeypatch.setattr(captions, "extract_video_info", lambda url: {"id": video_id, "title": title})


def test_caption_lang_is_passed_through_to_fetch_captions(monkeypatch):
    _stub_lookup(monkeypatch)
    captured = {}

    def fake_fetch_captions(url, lang_preference=None):
        captured["lang_preference"] = lang_preference
        return [_cue("hello")], "en", "manual", {}

    monkeypatch.setattr(captions, "fetch_captions", fake_fetch_captions)
    monkeypatch.setattr(
        translate,
        "translate_cues",
        lambda cues, source_lang, on_batch=None, model=None, provider=None: ["嗨"],
    )

    result = pipeline.process_video("https://youtu.be/vid1", caption_lang="en")

    assert captured["lang_preference"] == ["en"]
    assert result["source_lang"] == "en"


def test_caption_lang_raises_clear_error_when_not_found(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: (None, None, None, {})
    )

    with pytest.raises(RuntimeError, match="No 'en' captions found"):
        pipeline.process_video("https://youtu.be/vid1", caption_lang="en")


def test_caption_lang_ko_still_falls_back_to_whisper(monkeypatch):
    from app import transcribe

    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: (None, None, None, {})
    )
    monkeypatch.setattr(transcribe, "download_audio", lambda url, video_id: "audio.wav")
    monkeypatch.setattr(transcribe, "transcribe", lambda audio_path: [_cue("hello")])
    monkeypatch.setattr(
        translate,
        "translate_cues",
        lambda cues, source_lang, on_batch=None, model=None, provider=None: ["嗨"],
    )

    result = pipeline.process_video("https://youtu.be/vid1", caption_lang="ko")

    assert result["source_type"] == "whisper"


def test_caption_lang_result_is_not_cached(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "en", "manual", {})
    )
    monkeypatch.setattr(
        translate,
        "translate_cues",
        lambda cues, source_lang, on_batch=None, model=None, provider=None: ["嗨"],
    )

    pipeline.process_video("https://youtu.be/vid1", caption_lang="en")

    assert cache.get("vid1") is None


def test_llm_model_is_passed_through_to_translate_cues(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    captured = {}

    def fake_translate_cues(cues, source_lang, on_batch=None, model=None, provider=None):
        captured["model"] = model
        return ["嗨"]

    monkeypatch.setattr(translate, "translate_cues", fake_translate_cues)

    pipeline.process_video("https://youtu.be/vid1", llm_model="llama3")

    assert captured["model"] == "llama3"


def test_result_records_translation_provider_and_resolved_model(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(
        translate,
        "translate_cues",
        lambda cues, source_lang, on_batch=None, model=None, provider=None: ["嗨"],
    )

    result = pipeline.process_video("https://youtu.be/vid1")

    assert result["translation_provider"] == "gemini"
    assert result["translation_model"] == "gemini-3.6-flash"
    cached = cache.get("vid1")
    assert cached["translation_provider"] == "gemini"
    assert cached["translation_model"] == "gemini-3.6-flash"


def test_skip_translation_leaves_provider_and_model_unset(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )

    result = pipeline.process_video("https://youtu.be/vid1", skip_translation=True)

    assert result["translation_provider"] is None
    assert result["translation_model"] is None


def test_local_provider_model_resolution_prefers_explicit_llm_model(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "local")
    monkeypatch.setattr(config, "LOCAL_LLM_MODEL", "fallback-model")
    monkeypatch.setattr(
        translate,
        "translate_cues",
        lambda cues, source_lang, on_batch=None, model=None, provider=None: ["嗨"],
    )

    result = pipeline.process_video("https://youtu.be/vid1", llm_model="llama3")

    assert result["translation_provider"] == "local"
    assert result["translation_model"] == "llama3"


def test_provider_override_is_passed_through_to_translate_cues(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "deepseek")
    captured = {}

    def fake_translate_cues(cues, source_lang, on_batch=None, model=None, provider=None):
        captured["provider"] = provider
        return ["嗨"]

    monkeypatch.setattr(translate, "translate_cues", fake_translate_cues)

    result = pipeline.process_video("https://youtu.be/vid1", provider="anthropic")

    assert captured["provider"] == "anthropic"
    assert result["translation_provider"] == "anthropic"


def test_provider_override_result_is_not_cached(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    monkeypatch.setattr(
        translate,
        "translate_cues",
        lambda cues, source_lang, on_batch=None, model=None, provider=None: ["嗨"],
    )

    pipeline.process_video("https://youtu.be/vid1", provider="anthropic")

    assert cache.get("vid1") is None
