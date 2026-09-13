import argparse
import sys
from pathlib import Path

from app import cache, pipeline
from app.srt_utils import cues_to_srt
from app.vtt_utils import Cue


def main():
    parser = argparse.ArgumentParser(description="Fetch/translate K-pop video subtitles into Traditional Chinese.")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cache and reprocess")
    parser.add_argument("-o", "--output", help="Path to write the .srt file (default: <video_id>.srt)")
    args = parser.parse_args()

    print(f"Processing {args.url} ...", file=sys.stderr)
    result = pipeline.process_video(args.url, force_refresh=args.force_refresh)

    print(f"video_id={result['video_id']}  title={result['title']!r}", file=sys.stderr)
    print(f"source_lang={result['source_lang']}  source_type={result['source_type']}  cached={result['cached']}", file=sys.stderr)
    print(f"{len(result['cues'])} subtitle lines", file=sys.stderr)

    cues = [Cue(start=c["start"], end=c["end"], text=c["text_zh"]) for c in result["cues"]]
    srt_text = cues_to_srt(cues)

    out_path = Path(args.output) if args.output else Path(f"{result['video_id']}.srt")
    out_path.write_text(srt_text, encoding="utf-8")
    print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
