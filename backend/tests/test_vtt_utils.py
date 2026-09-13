from app.vtt_utils import parse_vtt

# A real excerpt of YouTube's auto-generated caption format, captured from an actual video.
# Exercises: the interior single-space line that must NOT be treated as a block separator,
# the near-zero-duration "carry" blocks that repeat prior text (must be deduped, not doubled),
# per-word <c> timing tags, and an HTML-escaped ">>" speaker marker (must be unescaped, then
# the leading ">>" stripped as a caption artifact).
# Built via explicit line concatenation (rather than a triple-quoted blob) so the
# single-space "quirk" lines below are unambiguous - they are easy to accidentally collapse
# into true blank lines when hand-editing a multi-line string, which silently reproduces the
# exact block-splitting bug this fixture exists to catch.
AUTO_CAPTION_SAMPLE = (
    "WEBVTT\n"
    "Kind: captions\n"
    "Language: ko\n"
    "\n"
    "00:00:05.240 --> 00:00:09.815 align:start position:0%\n"
    " \n"  # single-space interior line - must NOT be treated as a block separator
    "야,<00:00:05.480><c> 천</c><00:00:06.919><c> 격치이</c><00:00:07.319><c> 없기다.</c>\n"
    "\n"
    "00:00:09.815 --> 00:00:09.825 align:start position:0%\n"
    "야, 천 격치이 없기다.\n"
    " \n"
    "\n"
    "00:00:09.825 --> 00:00:10.990 align:start position:0%\n"
    "야, 천 격치이 없기다.\n"
    "[한숨]\n"
    "\n"
    "00:00:10.990 --> 00:00:11.000 align:start position:0%\n"
    "[한숨]\n"
    " \n"
    "\n"
    "00:00:11.000 --> 00:00:12.830 align:start position:0%\n"
    "[한숨]\n"
    "&gt;&gt; 야,<00:00:11.240><c> 끝.</c><00:00:11.559><c> 이겨다.</c><00:00:12.040><c> 다음</c><00:00:12.240><c> 도전자</c>\n"
    "\n"
    "00:00:12.830 --> 00:00:12.840 align:start position:0%\n"
    "&gt;&gt; 야, 끝. 이겨다. 다음 도전자\n"
    " \n"
    "\n"
    "00:00:12.840 --> 00:00:17.990 align:start position:0%\n"
    "&gt;&gt; 야, 끝. 이겨다. 다음 도전자\n"
    "나가.<00:00:13.920><c> 씨</c>\n"
)


def test_parses_auto_captions_without_duplication():
    cues = parse_vtt(AUTO_CAPTION_SAMPLE)
    texts = [c.text for c in cues]
    assert texts == [
        "야, 천 격치이 없기다.",
        "[한숨]",
        "야, 끝. 이겨다. 다음 도전자",
        "나가. 씨",
    ]


def test_auto_caption_timings_span_the_carry_blocks():
    cues = parse_vtt(AUTO_CAPTION_SAMPLE)
    # The first cue's real speech starts at 5.24s (the tagged block), not 9.815s (the carry
    # block) - a regression here previously lost the first cue entirely (see git history).
    assert cues[0].start == 5.24
    assert cues[0].end == 9.825
    assert cues[2].start == 11.0
    assert cues[2].end == 12.84


def test_html_entities_unescaped_and_speaker_marker_stripped():
    cues = parse_vtt(AUTO_CAPTION_SAMPLE)
    assert "&gt;" not in cues[2].text
    assert not cues[2].text.startswith(">>")


MANUAL_CAPTION_SAMPLE = """WEBVTT

00:00:01.000 --> 00:00:03.000
Hello there,
this is one cue.

00:00:03.000 --> 00:00:05.000
A second cue.
"""


def test_parses_manual_captions_without_tags():
    # Manual captions have no <c> word-timing tags, so multi-line payloads are joined as one
    # cue rather than treating only the last line as "new" (that heuristic is auto-caption-only).
    cues = parse_vtt(MANUAL_CAPTION_SAMPLE)
    assert len(cues) == 2
    assert cues[0].text == "Hello there, this is one cue."
    assert cues[1].text == "A second cue."
    assert cues[0].start == 1.0
    assert cues[0].end == 3.0
