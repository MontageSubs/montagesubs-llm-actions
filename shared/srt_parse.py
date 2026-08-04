#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
# Name: srt_parse.py
# Version: 1.0
# Organization: MontageSubs (蒙太奇字幕社区)
# Contributors: Meow P (小p)
# License: MIT License
# Source: https://github.com/MontageSubs/montagesubs-llm-actions
#
# Description / 描述:
#    Parses a SubRip (.srt) subtitle file into a structured JSON list of cues.
#    Timestamps are converted from the SRT format (HH:MM:SS,mmm) to total 
#    milliseconds for easier programmatic processing.
#    将 SubRip (.srt) 字幕文件解析为结构化的 JSON 提示词列表。时间戳将从 
#    SRT 格式 (时:分:秒,毫秒) 转换为总毫秒数，以便于编程处理。
#
# Usage / 用法:
#    python srt_parse.py input.srt
#    python srt_parse.py input.srt -o output.json
#
#    The script requires one positional argument (input file path). Use 
#    -o or --output to save the resulting JSON to a file; otherwise, 
#    the result is printed to stdout.
#    脚本需要一个位置参数（输入文件路径）。使用 -o 或 --output 将结果 JSON 
#    保存到文件；否则，结果将直接输出至标准输出 (stdout)。
#
# Output / 输出:
#    A JSON array of objects. Each object contains:
#      - index: The sequence number of the cue.
#      - start: Start time in total milliseconds.
#      - end: End time in total milliseconds.
#      - text: The subtitle text content.
#    一个 JSON 数组。每个对象包含：
#      - index: 字幕序号。
#      - start: 开始时间（总毫秒数）。
#      - end: 结束时间（总毫秒数）。
#      - text: 字幕文本内容。
#
# Example execution / 执行示例:
#    $ python srt_parse.py example.srt
#    INFO srt_parse: parsed 120 cues from example.srt
#    [
#      {
#        "index": 1,
#        "start": 1000,
#        "end": 4500,
#        "text": "Hello, welcome to the video!"
#      },
#      ...
#    ]
#
# Exit codes / 退出码:
#    0    normal completion / 正常完成
#
# ============================================================================
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
