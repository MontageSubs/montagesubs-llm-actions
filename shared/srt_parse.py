from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass

logger = logging.getLogger("srt_parse")

TIMESTAMP_LINE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


@dataclass
class Cue:
    index: int
    start: int
    end: int
    text: str


def _to_ms(h: str, m: str, s: str, ms: str) -> int:
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def parse(path: str) -> list[Cue]:
    raw = open(path, encoding="utf-8-sig").read()
    lines = raw.splitlines()
    cues: list[Cue] = []
    i = 0
    while i < len(lines):
        match = TIMESTAMP_LINE.match(lines[i].strip())
        if not match:
            i += 1
            continue
        start = _to_ms(*match.groups()[0:4])
        end = _to_ms(*match.groups()[4:8])
        i += 1
        text_lines = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].rstrip())
            i += 1
        cues.append(Cue(len(cues) + 1, start, end, "\n".join(text_lines)))
    logger.info("parsed %d cues from %s", len(cues), path)
    return cues


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="SRT to structured cue list (JSON)")
    parser.add_argument("input")
    parser.add_argument("-o", "--output")
    args = parser.parse_args()

    cues = [asdict(c) for c in parse(args.input)]
    payload = json.dumps(cues, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(payload)
        logger.info("written to %s", args.output)
    else:
        print(payload)


if __name__ == "__main__":
    main()
