#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
# Name: srt_parse.py
# Version: 1.1
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
#    cat input.srt | python srt_parse.py
#
#    input is optional; omit it to read raw SRT text from stdin (CI/action
#    friendly). Use -o/--output to save the resulting JSON to a file;
#    otherwise the result is printed to stdout.
#    input 参数可省略，省略时从 stdin 读取原始 SRT 文本（便于 CI/action 场景）。
#    使用 -o 或 --output 将结果 JSON 保存到文件；否则输出至标准输出 (stdout)。
#
# Output / 输出:
#    stdout: a single JSON object {"success": bool, "cues": [...]}, each
#    cue containing index/start/end/text (start/end in milliseconds).
#    stderr: diagnostic logs.
#    标准输出：单个 JSON 对象 {"success": bool, "cues": [...]}，每条 cue
#    包含 index/start/end/text（start/end 为毫秒）。
#    标准错误：诊断日志。
#
# Example execution / 执行示例:
#    $ python srt_parse.py example.srt
#    INFO srt_parse: parsed 120 cues from example.srt
#    {"success": true, "cues": [{"index": 1, "start": 1000, "end": 4500, "text": "Hello, welcome to the video!"}, ...]}
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
import sys
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


def parse_content(content: str) -> list[Cue]:
    lines = content.splitlines()
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
    return cues


def parse(source: str | None) -> list[Cue]:
    content = open(source, encoding="utf-8-sig").read() if source else sys.stdin.read()
    cues = parse_content(content)
    logger.info("parsed %d cues from %s", len(cues), source or "<stdin>")
    return cues


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="SRT to structured cue list (JSON)")
    parser.add_argument("input", nargs="?", help="SRT file path; omit to read raw SRT text from stdin")
    parser.add_argument("-o", "--output")
    args = parser.parse_args()

    cues = parse(args.input)
    payload = json.dumps({"success": bool(cues), "cues": [asdict(c) for c in cues]}, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(payload)
        logger.info("written to %s", args.output)
    else:
        print(payload)


if __name__ == "__main__":
    main()
