#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
# Name: main.py
# Version: 1.0.0
# Organization: MontageSubs (蒙太奇字幕社区)
# Contributors: Meow P (小p)
# License: MIT License
# Source: https://github.com/MontageSubs/montagesubs-llm-actions
#
# Description / 描述:
#    场景分析任务入口，组装 PromptPackage 并驱动续写循环直至产出完整的
#    SCENE_ANALYSIS.md。负责模板加载、cue_align.py 结果与剧情简介/
#    用词表/角色语音画像的整合、失焦重试、续写循环终止判定，最终写回
#    字幕仓库既定路径。不感知任何具体供应商细节，只通过 router.call() 与
#    LLM Core 交互。
#    Entry point for the scene-analysis task. Assembles PromptPackages and
#    drives the continuation loop until a complete SCENE_ANALYSIS.md is
#    produced. Owns template loading, merging cue_align.py output with
#    synopsis/glossary/character-voice context, suspect-output retries, and
#    continuation-loop termination — then writes the result back into the
#    subtitle repository. Provider details stay opaque; the only contact
#    point with LLM Core is router.call().
#
# Usage / 用法:
#    python main.py --repository <path> --source clean.srt [options]
#    python main.py --repository <path> --source clean.srt --dry-run
#
#    --dry-run assembles the PromptPackage without spending quota; pair
#    with --round/--history to inspect continuation rounds locally.
#    --dry-run 仅组装 PromptPackage 而不消耗配额；配合 --round/--history
#    可在本地检查续写轮次的组装结果。
#
# Output / 输出:
#    stdout: a single JSON StepResult object {"success", "step",
#    "correlation_id", "detail"/"error"}.
#    stderr: diagnostic logs.
#    标准输出：单个 JSON StepResult 对象 {"success", "step",
#    "correlation_id", "detail"/"error"}。
#    标准错误：诊断日志。
#
# Example execution / 执行示例:
#    $ python main.py --repository ./Film_2024 --source subtitles/web/work/source/en.srt
#    INFO analysis.main: analysis written path=docs/synopsis/SCENE_ANALYSIS.md rounds=1
#    {"success": true, "step": "analysis.run", "correlation_id": "...", "detail": {...}}
#
# Exit codes / 退出码:
#    0    normal completion, success reflected in the emitted StepResult JSON
#    0    正常完成，成功与否体现在输出的 StepResult JSON 中
#
# ============================================================================
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
import logging
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import router as core_router
from router import LLMResponse

logger = logging.getLogger("analysis.main")

COMPLETION_MARKER = "<!-- ANALYSIS_COMPLETE -->"
SUSPECT_RETRY_LIMIT = 2
DEFAULT_MAX_OUTPUT_TOKENS = 65_535

TEMPLATE_SECTION_HEADING = re.compile(r"^##\s+(\w+)\s*$", re.MULTILINE)
FENCE = re.compile(r"```\n(.*?)\n```", re.DOTALL)
REQUIRED_SECTIONS = ("system", "continuation", "closing")

MARKDOWN_SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
ADMONITION_LINE = re.compile(r"^>.*$", re.MULTILINE)
GLOSSARY_TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")
GLOSSARY_SEPARATOR_ROW = re.compile(r"^[\s|:-]+$")

SYNOPSIS_OVERVIEW_HEADINGS = ("简介", "情节线", "背景故事")
SYNOPSIS_PLOT_HEADING = "剧情"
GLOSSARY_TERMS_HEADING = "人物与专有名词"

BOT_COMMITTER_NAME = "montagesubs-llm-bot"


@dataclass
class Block:
    role: str
    text: str
    cacheable: bool = False
    volatile: bool = False


@dataclass
class Round:
    round_index: int
    blocks: list[Block]
    expects_history: bool = False


@dataclass
class PromptPackage:
    task: str
    tier: str
    thinking: str
    rounds: list[Round]
    correlation_id: str
    max_output_tokens: int | None = None


@dataclass
class StepResult:
    success: bool
    step: str
    correlation_id: str
    detail: dict | None = None
    error: dict | None = None


class CallerError(Exception):
    pass


def in_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def last_committer(path: Path, cwd: Path | None = None) -> str | None:
    if not path.exists():
        return None
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cn", "--", str(path)],
        cwd=cwd, capture_output=True, text=True,
    )
    return result.stdout.strip() or None


def resolve_glossary_mode(path: Path, cwd: Path | None = None) -> str:
    if not in_github_actions():
        return "auto"
    committer = last_committer(path, cwd=cwd)
    return "locked" if committer and committer != BOT_COMMITTER_NAME else "auto"


def load_template(path: Path) -> dict[str, str]:
    content = path.read_text(encoding="utf-8")
    headings = list(TEMPLATE_SECTION_HEADING.finditer(content))
    sections: dict[str, str] = {}
    for i, match in enumerate(headings):
        name = match.group(1)
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        fence = FENCE.search(content[start:end])
        if not fence:
            raise CallerError(f"template section '{name}' has no fenced code block")
        sections[name] = fence.group(1)
    missing = [name for name in REQUIRED_SECTIONS if name not in sections]
    if missing:
        raise CallerError(f"template missing required sections: {missing}")
    logger.info("template loaded path=%s sections=%s", path, list(sections))
    return sections


def read_optional(path: Path | None) -> str:
    if path is None:
        return ""
    if not path.exists():
        logger.info("optional context file not found, skipping path=%s", path)
        return ""
    text = path.read_text(encoding="utf-8").strip()
    logger.info("context file loaded path=%s chars=%d", path, len(text))
    return text


def run_cue_align(
    script: Path,
    source: Path,
    sdh: Path | None,
    gap_threshold: float,
    min_anchor_words: int,
    min_block: int,
    edge_keep: int,
) -> dict:
    if not source.exists():
        raise CallerError(f"source srt not found: {source}")
    cmd = [
        sys.executable, str(script),
        "--source", str(source),
        "--gap-threshold", str(gap_threshold),
        "--min-anchor-words", str(min_anchor_words),
        "--min-block", str(min_block),
        "--edge-keep", str(edge_keep),
    ]
    if sdh:
        cmd += ["--sdh", str(sdh)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stderr:
        for line in proc.stderr.strip().splitlines():
            logger.info("cue_align: %s", line)
    if proc.returncode != 0:
        raise CallerError(f"cue_align exited {proc.returncode}: {proc.stderr.strip()}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CallerError(f"cue_align returned malformed JSON: {exc}") from exc

    if not payload.get("success"):
        raise CallerError("cue_align reported failure")

    logger.info(
        "cue_align done total_cues=%d mapped_cues=%d unmatched_cues=%d",
        payload["total_cues"], payload["mapped_cues"], payload["unmatched_cues"],
    )
    return payload


def extract_markdown_sections(markdown: str) -> dict[str, str]:
    headings = list(MARKDOWN_SECTION_HEADING.finditer(markdown))
    sections: dict[str, str] = {}
    for i, match in enumerate(headings):
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown)
        sections[match.group(1).strip()] = markdown[start:end].strip()
    return sections


def build_synopsis_context(markdown: str) -> str:
    sections = extract_markdown_sections(markdown)
    blocks = []

    intro = ADMONITION_LINE.sub("", sections.get(SYNOPSIS_OVERVIEW_HEADINGS[0], "")).strip()
    overview = "\n\n".join(filter(None, [intro, *(sections.get(h, "") for h in SYNOPSIS_OVERVIEW_HEADINGS[1:])]))
    if overview:
        blocks.append(f'<context type="overview">\n{overview}\n</context>')

    plot = sections.get(SYNOPSIS_PLOT_HEADING, "").strip()
    if plot:
        blocks.append(f'<context type="plot">\n{plot}\n</context>')

    return "\n\n".join(blocks)


def escape_attr(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;")


def build_glossary_context(markdown: str) -> str:
    table_text = extract_markdown_sections(markdown).get(GLOSSARY_TERMS_HEADING, "")
    rows = []
    for line in table_text.splitlines():
        match = GLOSSARY_TABLE_ROW.match(line.strip())
        if not match or GLOSSARY_SEPARATOR_ROW.match(match.group(1)):
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if len(cells) < 3 or cells[0] == "原文":
            continue
        source, target, role = cells[0], cells[1], cells[2]
        rows.append(f'<term en="{escape_attr(source)}" zh="{escape_attr(target)}">{role}</term>')
    if not rows:
        return ""
    return '<context type="glossary">\n' + "\n".join(rows) + "\n</context>"


def assemble_context_text(synopsis_context: str, glossary_context: str, character_voices: str, cue_xml: str) -> str:
    blocks = [block for block in (synopsis_context, glossary_context) if block]
    if character_voices:
        blocks.append(f'<context type="character_voices">\n{character_voices}\n</context>')
    blocks.append(f'<context type="cues">\n{cue_xml}\n</context>')
    return "\n\n".join(blocks)


def build_package(
    sections: dict[str, str],
    context_text: str,
    round_index: int,
    thinking: str,
    correlation_id: str,
    max_output_tokens: int | None,
    glossary_mode: str = "n/a",
) -> PromptPackage:
    system_block = Block(role="system", text=sections["system"], cacheable=True)

    if round_index == 1:
        blocks = [system_block, Block(role="user", text=context_text, cacheable=True)]
        if glossary_mode == "auto" and "glossary_regen" in sections:
            blocks.append(Block(role="user", text=sections["glossary_regen"]))
        blocks.append(Block(role="user", text=sections["closing"]))
        expects_history = False
    else:
        blocks = [
            system_block,
            Block(role="user", text=sections["continuation"]),
            Block(role="user", text=sections["closing"]),
        ]
        expects_history = True

    return PromptPackage(
        task="analysis",
        tier="strong",
        thinking=thinking,
        rounds=[Round(round_index=round_index, blocks=[b for b in blocks if b.text], expects_history=expects_history)],
        correlation_id=correlation_id,
        max_output_tokens=max_output_tokens,
    )


def load_history(path: Path | None) -> list[LLMResponse]:
    if path is None:
        return []
    if not path.exists():
        raise CallerError(f"history file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise CallerError("history file must contain a JSON array of LLMResponse objects")
    history = [LLMResponse(**item) for item in raw]
    logger.info("history loaded entries=%d", len(history))
    return history


def resolve_final_output_path(repository: Path, edition: str, episode: str | None) -> Path:
    suffix = f"_{episode}" if episode else (f"_{edition}" if edition else "")
    return repository / "docs" / "synopsis" / f"SCENE_ANALYSIS{suffix}.md"


def estimate_tokens(package: PromptPackage) -> int:
    total_chars = sum(len(block.text) for r in package.rounds for block in r.blocks)
    return total_chars // 4


def render_prompt_transcript(package: PromptPackage) -> str:
    blocks = package.rounds[-1].blocks
    return "\n\n".join(f"[{block.role}]\n{block.text}" for block in blocks)


def write_debug_artifact(debug_dir: Path | None, name: str, text: str) -> None:
    if debug_dir is None or not text:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / name).write_text(text, encoding="utf-8")


def run_continuation_loop(
    sections: dict[str, str],
    context_text: str,
    thinking: str,
    correlation_id: str,
    max_output_tokens: int | None,
    glossary_mode: str,
    debug_dir: Path | None = None,
) -> tuple[str, list[LLMResponse]]:
    history: list[LLMResponse] = []
    accumulated = ""
    round_index = 1
    attempt_index = 0
    suspect_retries = 0
    effective_limit = max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS

    while True:
        attempt_index += 1
        package = build_package(sections, context_text, round_index, thinking, correlation_id, max_output_tokens, glossary_mode)
        write_debug_artifact(debug_dir, f"attempt{attempt_index:02d}_round{round_index}_prompt.txt", render_prompt_transcript(package))
        result = core_router.call(asdict(package), history=history)

        if result.status == "invalid_package":
            raise CallerError(f"invalid_package: {result.error.message}")
        if result.status == "overflow":
            raise CallerError(f"input_overflow: {result.error.estimated_tokens}/{result.error.limit_tokens}")
        if result.status == "provider_error":
            raise CallerError(f"provider_error[{result.error.code}]: {result.error.message}")
        if result.status == "suspect_output":
            suspect_retries += 1
            if suspect_retries > SUSPECT_RETRY_LIMIT:
                raise CallerError("suspect_output retries exhausted")
            continue

        response = result.response
        write_debug_artifact(debug_dir, f"attempt{attempt_index:02d}_round{round_index}_thinking.txt", response.thinking_text)
        write_debug_artifact(debug_dir, f"attempt{attempt_index:02d}_round{round_index}_response.txt", response.text)

        if response.suspect:
            suspect_retries += 1
            logger.info("suspect response discarded round=%d retries=%d", round_index, suspect_retries)
            if suspect_retries > SUSPECT_RETRY_LIMIT:
                raise CallerError("suspect response retries exhausted")
            continue

        suspect_retries = 0
        accumulated += response.text
        history.append(response)
        logger.info(
            "round complete round=%d output_tokens=%d thinking_tokens=%d",
            round_index, response.output_tokens, response.thinking_tokens,
        )

        if COMPLETION_MARKER in response.text:
            accumulated = accumulated.replace(COMPLETION_MARKER, "")
            break
        if response.output_tokens < effective_limit:
            break

        round_index += 1

    return accumulated, history


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analysis task caller (Core not wired yet; emits the assembled request for local inspection)")
    parser.add_argument("--repository", required=True, type=Path, help="path to the target subtitle repository checkout")
    parser.add_argument("--edition", default="", help="edition slug, e.g. web/bluray; empty for single-edition films")
    parser.add_argument("--episode", default=None, help="episode label, e.g. E01; overrides --edition in output filename")
    parser.add_argument("--thinking", default="high", choices=["high", "medium", "low", "off"])
    parser.add_argument("--round", type=int, default=1, help="round index to assemble; use >1 with --history to test continuation rounds")
    parser.add_argument("--history", type=Path, default=None, help="JSON array of prior-round LLMResponse objects, required when --round > 1")
    parser.add_argument("--correlation-id", default=None, help="override generated correlation id, useful for reproducible test runs")
    parser.add_argument("--template", type=Path, default=Path(__file__).resolve().parent / "ANALYSIS.md")
    parser.add_argument("--source", type=Path, required=True, help="clean source SRT path")
    parser.add_argument("--sdh", type=Path, default=None, help="optional SDH SRT path")
    parser.add_argument("--synopsis", type=Path, default=None)
    parser.add_argument("--glossary", type=Path, default=None)
    parser.add_argument("--character-voices", type=Path, default=None)
    parser.add_argument("--cue-align-script", type=Path, default=Path(__file__).resolve().parent / "cue_align.py")
    parser.add_argument("--gap-threshold", type=float, default=6.0)
    parser.add_argument("--min-anchor-words", type=int, default=4)
    parser.add_argument("--min-block", type=int, default=2)
    parser.add_argument("--edge-keep", type=int, default=3)
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--debug", action="store_true", help="verbose logging; also makes the provider print actual streamed content on each heartbeat instead of just char counts")
    parser.add_argument("--debug-dir", type=Path, default=None, help="dump each round's assembled prompt, raw thinking text, and raw response text as files under this directory")
    parser.add_argument("--dry-run", action="store_true", help="assemble the PromptPackage and stop, without calling the router (no quota spent)")
    parser.add_argument("--package-output", type=Path, default=None, help="dry-run only: where to write the assembled PromptPackage JSON")
    return parser.parse_args()


def run(args: argparse.Namespace, correlation_id: str) -> StepResult:
    sections = load_template(args.template)
    cue_payload = run_cue_align(
        args.cue_align_script, args.source, args.sdh,
        args.gap_threshold, args.min_anchor_words, args.min_block, args.edge_keep,
    )
    synopsis_raw = read_optional(args.synopsis)
    glossary_raw = read_optional(args.glossary)
    glossary_mode = resolve_glossary_mode(args.glossary, cwd=args.repository) if args.glossary else "n/a"
    logger.info("glossary mode resolved path=%s mode=%s", args.glossary, glossary_mode)
    context_text = assemble_context_text(
        build_synopsis_context(synopsis_raw) if synopsis_raw else "",
        build_glossary_context(glossary_raw) if glossary_raw else "",
        read_optional(args.character_voices),
        cue_payload["xml"],
    )

    if args.dry_run:
        step = f"analysis.round{args.round}"
        history = load_history(args.history)
        if args.round == 1 and history:
            logger.info("round 1 requested but history was provided, history will be ignored")
        if args.round > 1 and not history:
            raise CallerError("round > 1 requires --history pointing to prior LLMResponse entries")

        package = build_package(
            sections, context_text if args.round == 1 else "", args.round,
            args.thinking, correlation_id, args.max_output_tokens, glossary_mode,
        )
        estimated_input_tokens = estimate_tokens(package)
        logger.info("package assembled round=%d estimated_input_tokens=%d", args.round, estimated_input_tokens)

        payload = {"package": asdict(package), "history": [asdict(r) for r in history]}
        detail = {
            "round_index": args.round,
            "estimated_input_tokens": estimated_input_tokens,
            "final_output_path": str(resolve_final_output_path(args.repository, args.edition, args.episode)),
            "glossary_mode": glossary_mode,
        }
        if args.package_output:
            args.package_output.parent.mkdir(parents=True, exist_ok=True)
            args.package_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            detail["package_output"] = str(args.package_output)
            logger.info("package written path=%s", args.package_output)
        else:
            detail["package"] = payload["package"]
            detail["history"] = payload["history"]

        return StepResult(success=True, step=step, correlation_id=correlation_id, detail=detail)

    analysis_text, history = run_continuation_loop(
        sections, context_text, args.thinking, correlation_id, args.max_output_tokens, glossary_mode, args.debug_dir,
    )
    output_path = resolve_final_output_path(args.repository, args.edition, args.episode)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(analysis_text, encoding="utf-8")
    logger.info("analysis written path=%s rounds=%d", output_path, len(history))

    return StepResult(
        success=True, step="analysis.run", correlation_id=correlation_id,
        detail={
            "output_path": str(output_path),
            "rounds": len(history),
            "output_tokens": sum(r.output_tokens for r in history),
            "glossary_mode": glossary_mode,
            "debug_dir": str(args.debug_dir) if args.debug_dir else None,
        },
    )


def main() -> None:
    args = parse_args()
    if args.debug:
        os.environ["DEBUG"] = "1"
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr,
    )
    correlation_id = args.correlation_id or str(uuid.uuid4())

    try:
        result = run(args, correlation_id)
    except CallerError as exc:
        result = StepResult(
            success=False, step=f"analysis.round{args.round}" if args.dry_run else "analysis.run",
            correlation_id=correlation_id, error={"code": "caller_error", "message": str(exc)},
        )
    except Exception as exc:
        logger.exception("unexpected failure")
        result = StepResult(
            success=False, step=f"analysis.round{args.round}" if args.dry_run else "analysis.run",
            correlation_id=correlation_id, error={"code": "caller_error", "message": f"{type(exc).__name__}: {exc}"},
        )

    print(json.dumps(asdict(result), ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
