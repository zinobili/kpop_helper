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


def _fake_translate(monkeypatch, text="嗨", captured=None):
    def fake(cues, source_lang, on_batch=None, model=None, provider=None):
        if captured is not None:
            captured["model"] = model
            captured["provider"] = provider
        return [text] * len(cues)

    monkeypatch.setattr(translate, "translate_cues", fake)


def test_caption_lang_is_passed_through_to_fetch_captions(monkeypatch):
    _stub_lookup(monkeypatch)
    captured = {}

    def fake_fetch_captions(url, lang_preference=None):
        captured["lang_preference"] = lang_preference
        return [_cue("hello")], "en", "manual", {}

    monkeypatch.setattr(captions, "fetch_captions", fake_fetch_captions)
    _fake_translate(monkeypatch)

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
    _fake_translate(monkeypatch)

    result = pipeline.process_video("https://youtu.be/vid1", caption_lang="ko")

    assert result["source_type"] == "whisper"


def test_caption_lang_result_is_cached_as_its_own_variant(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "en", "manual", {})
    )
    _fake_translate(monkeypatch)

    pipeline.process_video("https://youtu.be/vid1", caption_lang="en")

    assert cache.get_transcript("vid1", "en", "manual") is not None
    transcript = cache.get_transcript("vid1", "en", "manual")
    assert cache.list_translations_for_transcript(transcript["transcript_id"])
    # A Korean transcript/translation for the same video is untouched (never created here).
    assert cache.get_transcript("vid1", "ko", "manual") is None


def test_llm_model_is_passed_through_to_translate_cues(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    captured = {}
    _fake_translate(monkeypatch, captured=captured)

    pipeline.process_video("https://youtu.be/vid1", llm_model="llama3")

    assert captured["model"] == "llama3"


def test_result_records_translation_provider_and_resolved_model(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.6-flash")
    _fake_translate(monkeypatch)

    result = pipeline.process_video("https://youtu.be/vid1")

    assert result["translation_provider"] == "gemini"
    assert result["translation_model"] == "gemini-3.6-flash"
    transcript = cache.get_transcript("vid1", "ko", "manual")
    cached = cache.get_translation(transcript["transcript_id"], "gemini", "gemini-3.6-flash")
    assert cached is not None


def test_skip_translation_leaves_provider_and_model_unset(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )

    result = pipeline.process_video("https://youtu.be/vid1", skip_translation=True)

    assert result["translation_provider"] is None
    assert result["translation_model"] is None
    assert result["variant_id"] is None


def test_local_provider_model_resolution_prefers_explicit_llm_model(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "local")
    monkeypatch.setattr(config, "LOCAL_LLM_MODEL", "fallback-model")
    _fake_translate(monkeypatch)

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
    _fake_translate(monkeypatch, captured=captured)

    result = pipeline.process_video("https://youtu.be/vid1", provider="anthropic")

    assert captured["provider"] == "anthropic"
    assert result["translation_provider"] == "anthropic"


def test_provider_override_result_is_cached_as_its_own_variant(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setattr(config, "ANTHROPIC_MODEL", "claude-sonnet-5")
    _fake_translate(monkeypatch)

    pipeline.process_video("https://youtu.be/vid1", provider="anthropic")

    transcript = cache.get_transcript("vid1", "ko", "manual")
    assert cache.get_translation(transcript["transcript_id"], "anthropic", "claude-sonnet-5")
    # The .env default provider was never actually called, so no variant for it exists.
    assert cache.get_translation(transcript["transcript_id"], "deepseek", "deepseek-chat") is None


# ---------- multi-variant reuse ----------


def test_second_call_with_same_settings_is_a_cache_hit_no_llm_call(monkeypatch):
    _stub_lookup(monkeypatch)
    fetch_calls = {"n": 0}

    def fake_fetch_captions(url, lang_preference=None):
        fetch_calls["n"] += 1
        return [_cue()], "ko", "manual", {}

    monkeypatch.setattr(captions, "fetch_captions", fake_fetch_captions)
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.6-flash")
    translate_calls = {"n": 0}

    def fake_translate(cues, source_lang, on_batch=None, model=None, provider=None):
        translate_calls["n"] += 1
        return ["嗨"] * len(cues)

    monkeypatch.setattr(translate, "translate_cues", fake_translate)

    first = pipeline.process_video("https://youtu.be/vid1")
    second = pipeline.process_video("https://youtu.be/vid1")

    assert first["cached"] is False
    assert second["cached"] is True
    assert translate_calls["n"] == 1  # second call never hit the LLM
    assert second["variant_id"] == first["variant_id"]


def test_different_provider_creates_a_new_variant_without_touching_the_first(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(config, "ANTHROPIC_MODEL", "claude-sonnet-5")
    _fake_translate(monkeypatch)

    first = pipeline.process_video("https://youtu.be/vid1")
    second = pipeline.process_video("https://youtu.be/vid1", provider="anthropic")

    assert first["variant_id"] != second["variant_id"]
    transcript = cache.get_transcript("vid1", "ko", "manual")
    variants = cache.list_translations_for_transcript(transcript["transcript_id"])
    assert {v["translation_provider"] for v in variants} == {"gemini", "anthropic"}


def test_different_caption_lang_creates_a_separate_transcript(monkeypatch):
    _stub_lookup(monkeypatch)

    def fake_fetch_captions(url, lang_preference=None):
        lang = (lang_preference or ["ko"])[0]
        return [_cue()], lang, "manual", {}

    monkeypatch.setattr(captions, "fetch_captions", fake_fetch_captions)
    _fake_translate(monkeypatch)

    pipeline.process_video("https://youtu.be/vid1", caption_lang="ko")
    pipeline.process_video("https://youtu.be/vid1", caption_lang="en")

    assert cache.get_transcript("vid1", "ko", "manual") is not None
    assert cache.get_transcript("vid1", "en", "manual") is not None


def test_force_refresh_redoes_translation_without_deleting_other_variants(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "fetch_captions", lambda url, lang_preference=None: ([_cue()], "ko", "manual", {})
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(config, "ANTHROPIC_MODEL", "claude-sonnet-5")
    _fake_translate(monkeypatch)

    pipeline.process_video("https://youtu.be/vid1")
    pipeline.process_video("https://youtu.be/vid1", provider="anthropic")
    pipeline.process_video("https://youtu.be/vid1", force_refresh=True)

    transcript = cache.get_transcript("vid1", "ko", "manual")
    variants = cache.list_translations_for_transcript(transcript["transcript_id"])
    assert {v["translation_provider"] for v in variants} == {"gemini", "anthropic"}


def test_quick_id_short_circuit_skips_network_on_cache_hit(monkeypatch):
    """A network-free video-id parse plus a cache hit should never call extract_video_info -
    this is what keeps cached videos working while YouTube bot-walls yt-dlp."""
    monkeypatch.setattr(captions, "extract_video_id_from_url", lambda url: "vid1")

    def boom(url):
        raise AssertionError("extract_video_info should not be called on a cache hit")

    monkeypatch.setattr(captions, "extract_video_info", boom)
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.6-flash")

    transcript_id = cache.put_transcript("vid1", "Title", "ko", "manual", [_cue()])
    cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", [_cue("嗨")])

    result = pipeline.process_video("https://youtu.be/vid1")

    assert result["cached"] is True
    assert result["title"] == "Title"


# ---------- preview_translation_options ----------


def test_preview_reports_no_alternates_when_nothing_cached(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "pick_caption_track", lambda info, lang_preference=None: ("url", "ko", "manual")
    )

    preview = pipeline.preview_translation_options("https://youtu.be/vid1")

    assert preview["exact_match"] is False
    assert preview["alternates"] == []
    assert preview["source_lang"] == "ko"
    assert preview["source_type"] == "manual"


def test_preview_reports_exact_match(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "pick_caption_track", lambda info, lang_preference=None: ("url", "ko", "manual")
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.6-flash")

    transcript_id = cache.put_transcript("vid1", "T", "ko", "manual", [_cue()])
    cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", [_cue("嗨")])

    preview = pipeline.preview_translation_options("https://youtu.be/vid1")

    assert preview["exact_match"] is True
    assert preview["alternates"] == []


def test_preview_reports_alternates_when_settings_differ(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "pick_caption_track", lambda info, lang_preference=None: ("url", "ko", "manual")
    )
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.6-flash")

    transcript_id = cache.put_transcript("vid1", "T", "ko", "manual", [_cue()])
    cache.put_translation(transcript_id, "anthropic", "claude-sonnet-5", [_cue("嗨")])

    preview = pipeline.preview_translation_options("https://youtu.be/vid1")

    assert preview["exact_match"] is False
    assert len(preview["alternates"]) == 1
    assert preview["alternates"][0]["translation_provider"] == "anthropic"


def test_preview_no_track_found_for_explicit_lang_returns_none_identity(monkeypatch):
    _stub_lookup(monkeypatch)
    monkeypatch.setattr(
        captions, "pick_caption_track", lambda info, lang_preference=None: (None, None, None)
    )

    preview = pipeline.preview_translation_options("https://youtu.be/vid1", caption_lang="en")

    assert preview["source_lang"] is None
    assert preview["exact_match"] is False
    assert preview["alternates"] == []


# ---------- get_variant_result ----------


def test_get_variant_result_returns_none_for_unknown_id():
    assert pipeline.get_variant_result("nope") is None


def test_get_variant_result_includes_original_and_translated_text():
    transcript_id = cache.put_transcript("vid1", "T", "ko", "manual", [_cue("hi")])
    variant_id = cache.put_translation(transcript_id, "gemini", "gemini-3.6-flash", [_cue("嗨")])

    result = pipeline.get_variant_result(variant_id)

    assert result["video_id"] == "vid1"
    assert result["cached"] is True
    assert result["cues"][0]["text_ko"] == "hi"
    assert result["cues"][0]["text_zh"] == "嗨"
