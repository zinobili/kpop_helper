import pytest

from app.captions import _pick_track_url, extract_video_id_from_url

VALID_ID = "yLGXM5O8v5Q"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VALID_ID}",
        f"https://www.youtube.com/watch?v={VALID_ID}&t=42s",
        f"http://youtube.com/watch?v={VALID_ID}",
        f"https://youtu.be/{VALID_ID}",
        f"https://youtu.be/{VALID_ID}?t=10",
        f"https://www.youtube.com/shorts/{VALID_ID}",
        f"https://www.youtube.com/embed/{VALID_ID}",
        f"https://www.youtube.com/live/{VALID_ID}",
    ],
)
def test_extracts_id_from_common_url_shapes(url):
    assert extract_video_id_from_url(url) == VALID_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch",  # no v= param
        "https://www.youtube.com/",  # homepage
        "https://example.com/watch?v=" + VALID_ID,  # not youtube at all
        "https://www.youtube.com/watch?v=too-short",  # not 11 chars
        "not a url at all",
    ],
)
def test_returns_none_for_unrecognized_or_invalid_urls(url):
    assert extract_video_id_from_url(url) is None


def _info(manual=None, auto=None):
    return {"subtitles": manual or {}, "automatic_captions": auto or {}}


def test_pick_track_url_defaults_to_korean_over_english():
    info = _info(manual={
        "ko": [{"ext": "vtt", "url": "ko.vtt"}],
        "en": [{"ext": "vtt", "url": "en.vtt"}],
    })
    assert _pick_track_url(info) == ("ko.vtt", "ko", "manual")


def test_pick_track_url_falls_back_to_english_when_no_korean():
    info = _info(manual={"en": [{"ext": "vtt", "url": "en.vtt"}]})
    assert _pick_track_url(info) == ("en.vtt", "en", "manual")


def test_pick_track_url_lang_preference_overrides_default_order():
    info = _info(manual={
        "ko": [{"ext": "vtt", "url": "ko.vtt"}],
        "en": [{"ext": "vtt", "url": "en.vtt"}],
    })
    assert _pick_track_url(info, lang_preference=["en"]) == ("en.vtt", "en", "manual")


def test_pick_track_url_lang_preference_has_no_fallback_when_missing():
    info = _info(manual={"ko": [{"ext": "vtt", "url": "ko.vtt"}]})
    assert _pick_track_url(info, lang_preference=["en"]) == (None, None, None)
