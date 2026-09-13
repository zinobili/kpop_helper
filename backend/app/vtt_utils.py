import html
import re
from dataclasses import dataclass
from typing import List

_TIMESTAMP_RE = re.compile(
    r"(\d{2}:)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}:)?(\d{2}):(\d{2})[.,](\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_WORD_TIMING_RE = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _parse_timestamp(hours: str, minutes: str, seconds: str, millis: str) -> float:
    h = int(hours[:-1]) if hours else 0
    m = int(minutes)
    s = int(seconds)
    ms = int(millis)
    return h * 3600 + m * 60 + s + ms / 1000.0


def _clean_text(text: str) -> str:
    text = html.unescape(text)
    text = _TAG_RE.sub("", text)
    text = re.sub(r"^\s*>>\s*", "", text)
    return text.replace("\n", " ").strip()


def parse_vtt(content: str) -> List[Cue]:
    """Parse WebVTT content into cues.

    YouTube's auto-generated captions use a rolling display: each real phrase is sent as
    a block with per-word <hh:mm:ss.mmm><c>word</c> timing tags, immediately followed by a
    near-zero-duration "carry" block that just repeats the same plain text (to seed the
    next block's leading context line). We only take the block that actually carries the
    word-timing tags as the source of truth for both text and timing, which yields a
    clean, non-overlapping transcript. Manual (non-auto) captions have no timing tags at
    all, so each block is used directly as one cue.
    """
    # Auto-generated captions carry each new phrase as the LAST payload line of its block,
    # with any earlier line(s) being leftover context already emitted by a prior block.
    # Manual captions have no word-timing tags at all and use all payload lines as one cue.
    is_auto = bool(_WORD_TIMING_RE.search(content))

    # Split only on truly empty lines. YouTube's auto-captions sometimes include an
    # interior line containing a single space (not a real blank line) as a formatting
    # quirk; splitting on that too would tear a timestamp away from its payload.
    blocks = re.split(r"\n\n+", content)
    cues: List[Cue] = []

    for block in blocks:
        start = end = None
        payload_lines: List[str] = []
        for line in block.splitlines():
            if start is None:
                match = _TIMESTAMP_RE.search(line)
                if match:
                    start = _parse_timestamp(*match.groups()[0:4])
                    end = _parse_timestamp(*match.groups()[4:8])
                    continue
            elif line.strip():
                payload_lines.append(line)

        if start is None or not payload_lines:
            continue

        text = _clean_text(payload_lines[-1] if is_auto else " ".join(payload_lines))
        if not text:
            continue

        cues.append(Cue(start=start, end=end, text=text))

    # Safety net for any exact-duplicate consecutive cues (can happen in manual captions).
    deduped: List[Cue] = []
    for cue in cues:
        if deduped and cue.text == deduped[-1].text:
            deduped[-1] = Cue(start=deduped[-1].start, end=cue.end, text=cue.text)
            continue
        deduped.append(cue)
    return deduped
