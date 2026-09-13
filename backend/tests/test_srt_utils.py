from app.srt_utils import cues_to_srt
from app.vtt_utils import Cue


def test_formats_single_cue():
    cues = [Cue(start=1.5, end=3.25, text="Hello")]
    srt = cues_to_srt(cues)
    assert srt == "1\n00:00:01,500 --> 00:00:03,250\nHello\n"


def test_formats_multiple_cues_and_numbers_sequentially():
    cues = [
        Cue(start=0.0, end=1.0, text="First"),
        Cue(start=1.0, end=2.0, text="Second"),
    ]
    srt = cues_to_srt(cues)
    lines = srt.split("\n")
    assert lines[0] == "1"
    assert lines[4] == "2"


def test_formats_hours():
    cues = [Cue(start=3661.234, end=3662.0, text="Over an hour in")]
    srt = cues_to_srt(cues)
    assert "01:01:01,234" in srt
