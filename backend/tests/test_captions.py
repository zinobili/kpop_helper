import pytest

from app.captions import extract_video_id_from_url

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
