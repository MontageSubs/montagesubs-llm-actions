#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
# Name: context_format.py
# Version: 1.4.0
# Organization: MontageSubs (蒙太奇字幕社区)
# Contributors: Meow P (小p)
# License: MIT License
# Source: https://github.com/MontageSubs/montagesubs-llm-actions
#
# Description / 描述:
#    Aligns "clean" subtitles with SDH subtitles to extract speaker names and 
#    sound effects (SFX). It maps SDH metadata back to clean cues using 
#    SequenceMatcher and outputs a custom XML-like stream for analysis.
#    将“纯净版”字幕与 SDH（听障辅助）字幕进行语义对齐，以提取说话人姓名和 
#    音效（SFX）信息。通过 SequenceMatcher 将 SDH 元数据映射回纯净版字幕，
#    并输出用于分析阶段的自定义 XML 流。
#
# Usage / 用法:
#    python context_format.py --clean clean.srt --sdh sdh.srt
#    python context_format.py --clean clean.srt --sdh sdh.srt -o result.xml
#
#    Required: --clean (Path to clean SRT). 
#    Optional: --sdh (Path to SDH SRT), -o (Output file), 
#    --gap-threshold (Seconds to trigger <gap> tag).
#    必须参数：--clean（纯净版 SRT 路径）。
#    可选参数：--sdh（SDH 版 SRT 路径）、-o（输出文件）、
#    --gap-threshold（触发 <gap> 标签的时间间隔秒数）。
#
# Output / 输出:
#    An XML-formatted sequence containing:
#      - <sfx>...</sfx>: Sound effects found in SDH before the cue.
#      - <c0000 speaker="...">...</c>: Dialogue with mapped speaker info.
#      - <gap sec="..."/>: Time gaps exceeding the threshold.
#    一个 XML 格式的序列，包含：
#      - <sfx>...</sfx>：该条字幕前在 SDH 中出现的音效。
#      - <c0000 speaker="...">...</c>：映射了说话人信息的对话。
#      - <gap sec="..."/>：超过阈值的时间间隔。
#
# Example execution / 执行示例:
#    $ python context_format.py --clean clean.srt --sdh sdh.srt
#    INFO context_format: cues mapped: 115/120, unmatched high-confidence cues: 2
#    <sfx>[door slams]</sfx>
#    <c0001 speaker="JOHN">Hello there!</c>
#    <gap sec="7.50"/>
#    <c0002 speaker="MARY">I didn't see you come in.</c>
#
# Exit codes / 退出码:
#    0    normal completion / 正常完成
#
# ============================================================================
from __future__ import annotations

import argparse
import bisect
import itertools
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from srt_parse import Cue, parse  # noqa: E402

logger = logging.getLogger("context_format")

ASS_TAG = re.compile(r"\{\\[^}]*\}")
HTML_TAG = re.compile(r"</?[a-zA-Z][^<>]*>")
DASH_PREFIX = re.compile(r"^-\s*")
WHITESPACE = re.compile(r"\s+")

BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}"}
SPEAKER_LINE = re.compile(r"^([A-Z][A-Z0-9 .,'\-]{0,29}):\s*(.*)$")
NARRATOR_EXCLUDE = (
    "HTTP", ", ", "PREVIOUSLY ON", "IMPROVED BY",
    " IS ", " ARE ", " WERE ", " WAS ", " THINK ", " GUESS ",
    " WILL ", " BELIEVE ", " SAY ", " SAID ", " DO ", " WANT ", "THAT'S ",
)
NARRATOR_PUNCT = set("!?¿¡")


@dataclass
class SdhEntry:
    kind: str
    text: str
    time: int
    speaker: str | None = None
    manner: str | None = None


def strip_tags(text: str) -> str:
    return HTML_TAG.sub("", ASS_TAG.sub("", text)).strip()


def normalize(text: str) -> str:
    lines = (strip_tags(line) for line in text.split("\n"))
    merged = " ".join(DASH_PREFIX.sub("", line).strip() for line in lines)
    return WHITESPACE.sub(" ", merged).strip()


def _strip_line(raw: str) -> str:
    line = strip_tags(raw)
    return DASH_PREFIX.sub("", line).strip()


def _bracket_closed(line: str) -> bool:
    close = BRACKET_PAIRS.get(line[:1])
    return bool(close) and (line.endswith(close) or line.endswith(f"{close}:"))


def _merge_bracket_continuations(lines: list[str]) -> list[str]:
    merged, opener, buffer = [], None, None
    for line in lines:
        if buffer is not None:
            buffer = f"{buffer} {line}"
            if line.endswith(BRACKET_PAIRS[opener]) or line.endswith(f"{BRACKET_PAIRS[opener]}:"):
                merged.append(buffer)
                buffer, opener = None, None
            continue
        if line[:1] in BRACKET_PAIRS and not _bracket_closed(line):
            opener, buffer = line[0], line
        else:
            merged.append(line)
    if buffer is not None:
        merged.append(buffer)
    return merged


def _is_narrator_prefix(pre: str) -> bool:
    upper = f" {pre.upper()} "
    if any(token in upper for token in NARRATOR_EXCLUDE):
        return False
    return not NARRATOR_PUNCT.intersection(pre)


def _classify(line: str, time: int) -> SdhEntry:
    close = BRACKET_PAIRS.get(line[:1])
    if close:
        depth, contents, buf, valid, expected = 0, [], [], True, ""
        for i, c in enumerate(line):
            if depth == 0 and c in BRACKET_PAIRS:
                expected = BRACKET_PAIRS[c]
                depth += 1
            elif depth > 0 and c == expected:
                depth -= 1
                if depth == 0:
                    val = "".join(buf).strip()
                    if val:
                        contents.append(val)
                    buf.clear()
                else:
                    buf.append(c)
            elif depth > 0:
                if c in BRACKET_PAIRS and BRACKET_PAIRS[c] == expected:
                    depth += 1
                buf.append(c)
            elif not c.isspace():
                if c == ":" and i == len(line) - 1 and len(contents) == 1:
                    return SdhEntry("manner", contents[0].lower(), time)
                valid = False
                break
        if valid and depth == 0 and contents and not line.endswith(":"):
            return SdhEntry("sfx", "; ".join(contents), time)
    if line[:1] == "?" and line.endswith("?") and len(line) > 2:
        return SdhEntry("sfx", line[1:-1].strip(), time)
    speaker = SPEAKER_LINE.match(line)
    if speaker and _is_narrator_prefix(speaker.group(1)):
        residual = speaker.group(2).strip()
        if residual:
            return SdhEntry("dialogue", residual, time, speaker.group(1).strip())
        return SdhEntry("sfx", "", time)
    return SdhEntry("dialogue", line, time)


def extract_entries(sdh_cues: list[Cue]) -> list[SdhEntry]:
    entries = []
    for cue in sdh_cues:
        lines = [_strip_line(raw) for raw in cue.text.split("\n")]
        lines = [line for line in lines if line]
        pending_manner = None
        for line in _merge_bracket_continuations(lines):
            entry = _classify(line, cue.start)
            if entry.kind == "manner":
                pending_manner = entry.text
                continue
            if entry.kind == "dialogue" and pending_manner:
                entry.manner, pending_manner = pending_manner, None
            if entry.text:
                entries.append(entry)
    return entries


def _get_segments(text: str) -> list[str]:
    clean_txt = strip_tags(text)
    segs = []
    for l in (line.strip() for line in clean_txt.split("\n") if line.strip()):
        if l.startswith("-"):
            segs.extend(DASH_PREFIX.sub("", p).strip() for p in re.split(r"\s+-", l) if p.strip())
        else:
            segs.append(l)
    return segs or [clean_txt]


def _match_anchors(clean_cues: list[Cue], entries: list[SdhEntry], min_words: int, min_block: int):
    dpos = [i for i, e in enumerate(entries) if e.kind == "dialogue"]
    clean_tokens, clean_owner, sdh_tokens, sdh_owner = [], [], [], []

    for ci, cue in enumerate(clean_cues):
        segs = _get_segments(cue.text)
        for s_idx, seg in enumerate(segs, 1):
            words = normalize(seg).split()
            clean_tokens.extend(words)
            clean_owner.extend([(ci, s_idx, len(segs))] * len(words))

    for pos in dpos:
        words = normalize(entries[pos].text).split()
        sdh_tokens.extend(words)
        sdh_owner.extend([pos] * len(words))

    matcher = SequenceMatcher(None, clean_tokens, sdh_tokens, autojunk=True)
    dialogue_clean, total_segs_map, speaker_map, manner_map = {}, {}, defaultdict(dict), {}
    mapped_cues = set()

    for block in matcher.get_matching_blocks():
        if block.size < min_block: continue
        for offset in range(block.size):
            ci, s_idx, t_segs = clean_owner[block.a + offset]
            pos = sdh_owner[block.b + offset]
            dialogue_clean[pos] = ci
            mapped_cues.add(ci)
            total_segs_map[ci] = t_segs
            if entries[pos].speaker:
                speaker_map[ci][s_idx] = entries[pos].speaker
            if entries[pos].manner:
                manner_map[ci] = entries[pos].manner

    unmatched = sum(
        1 for ci, cue in enumerate(clean_cues)
        if ci not in mapped_cues and len(normalize(cue.text).split()) >= min_words
        and not logger.warning("no anchor for cue %d: %r", cue.index, cue.text)
    )

    logger.info("cues mapped: %d/%d, unmatched high-confidence cues: %d", len(mapped_cues), len(clean_cues), unmatched)
    return dialogue_clean, speaker_map, manner_map, total_segs_map



def _anchor_axes(dialogue_clean: dict[int, int], entries: list[SdhEntry], clean_cues: list[Cue]) -> tuple[list[int], list[int]]:
    pairs = sorted((entries[pos].time, clean_cues[ci].start) for pos, ci in dialogue_clean.items())
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _project(time: int, sdh_axis: list[int], clean_axis: list[int]) -> int | None:
    if not sdh_axis:
        return None
    i = min(bisect.bisect_left(sdh_axis, time), len(sdh_axis) - 1)
    if i > 0 and abs(time - sdh_axis[i - 1]) <= abs(sdh_axis[i] - time):
        i -= 1
    return clean_axis[i] + (time - sdh_axis[i])


def _collapse_sfx(items: list[str], edge_keep: int) -> str:
    deduped = [text for text, _ in itertools.groupby(items)]
    if len(deduped) > edge_keep * 2:
        deduped = deduped[:edge_keep] + deduped[-edge_keep:]
    return "; ".join(deduped)


def _wrap(cue: Cue, seg_speakers: dict[int, str], manner: str | None, total_segs: int, width: int = 4) -> str:
    attrs = [f's="{seg_speakers[1]}"'] if total_segs <= 1 and 1 in seg_speakers else [f's{k}="{v}"' for k, v in sorted(seg_speakers.items())]
    if manner:
        attrs.append(f'm="{manner}"')
    attr_str = "".join(f" {a}" for a in attrs)
    text = strip_tags(cue.text.replace("\n", " "))
    return f"<c{cue.index:0{width}d}{attr_str}>{text}</c>"


def build(
    clean_cues: list[Cue],
    sdh_cues: list[Cue] | None,
    gap_threshold: float = 6.0,
    min_anchor_words: int = 4,
    min_block: int = 2,
    edge_keep: int = 3,
) -> str:
    entries = extract_entries(sdh_cues or [])
    dialogue_clean, speaker_map, manner_map, total_segs_map = _match_anchors(clean_cues, entries, min_anchor_words, min_block)
    
    sdh_axis, clean_axis = _anchor_axes(dialogue_clean, entries, clean_cues)
    clean_starts, sfx_by_target = [c.start for c in clean_cues], defaultdict(list)
    
    for entry in (e for e in entries if e.kind == "sfx"):
        target = _project(entry.time, sdh_axis, clean_axis)
        ci = min(bisect.bisect_right(clean_starts, target), len(clean_cues) - 1) if target is not None else len(clean_cues) - 1
        sfx_by_target[ci].append((target, entry.text))

    lines, last_end = [], None
    for ci, cue in enumerate(clean_cues):
        sfx_items = sfx_by_target.get(ci, [])
        if sfx_items:
            first_tgt = sfx_items[0][0] if sfx_items[0][0] is not None else cue.start
            if last_end is not None and (gap := first_tgt - last_end) > gap_threshold * 1000:
                lines.append(f'<gap sec="{gap / 1000:.2f}"/>')
            if merged := _collapse_sfx([t for _, t in sfx_items], edge_keep):
                lines.append(f"<sfx>{merged}</sfx>")
            last_tgt = sfx_items[-1][0] if sfx_items[-1][0] is not None else cue.start
            if (gap := cue.start - last_tgt) > gap_threshold * 1000:
                lines.append(f'<gap sec="{gap / 1000:.2f}"/>')
        else:
            if last_end is not None and (gap := cue.start - last_end) > gap_threshold * 1000:
                lines.append(f'<gap sec="{gap / 1000:.2f}"/>')
        
        t_segs = total_segs_map.get(ci) or len(_get_segments(cue.text))
        lines.append(_wrap(cue, speaker_map.get(ci, {}), manner_map.get(ci), t_segs))
        last_end = cue.end

    logger.info(
        "clean cues: %d, cues with speaker hint: %d, sfx slots: %d",
        len(clean_cues), len(speaker_map), len(sfx_by_target),
    )
    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="clean+SDH srt to analysis-stage XML")
    parser.add_argument("--clean", required=True)
    parser.add_argument("--sdh")
    parser.add_argument("--gap-threshold", type=float, default=6.0)
    parser.add_argument("--min-anchor-words", type=int, default=4)
    parser.add_argument("--min-block", type=int, default=2)
    parser.add_argument("--edge-keep", type=int, default=3)
    parser.add_argument("-o", "--output")
    args = parser.parse_args()

    clean_cues = parse(args.clean)
    sdh_cues = parse(args.sdh) if args.sdh else None
    result = build(
        clean_cues, sdh_cues,
        args.gap_threshold, args.min_anchor_words, args.min_block, args.edge_keep,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)
        logger.info("written to %s", args.output)
    else:
        print(result)


if __name__ == "__main__":
    main()
