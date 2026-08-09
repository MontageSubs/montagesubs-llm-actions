#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
# Name: router.py
# Version: 1.0.0
# Organization: MontageSubs (蒙太奇字幕社区)
# Contributors: Meow P (小p)
# License: MIT License
# Source: https://github.com/MontageSubs/montagesubs-llm-actions
#
# Description / 描述:
#   供应商无关的调度层，唯一对外接口是 call(package, history) -> CoreResult。
#   职责边界见《调用者-Core 交互规范》：结构校验、token 预估拦截、格式无关的
#   轮次拼装、调用供应商、机械层面的输出异常标记（suspect）、结构化日志。
#   不持有任何具体模型名/思考预算映射——那些是供应商专属知识，本文件只把
#   调用者给出的抽象tier/thinking原样透传给供应商，由供应商自行决定用哪个
#   具体模型、怎么配置思考参数。不理解任何任务语义，不对生成内容的正确性
#   做判断——那是调用者（各任务 main.py）的职责。
# ============================================================================
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import logging
import os
import time
from dataclasses import dataclass

from google import PROVIDERS, ProviderError, ProviderResult

logger = logging.getLogger("core.router")

VALID_TIERS = {"strong", "weak"}
VALID_THINKING = {"high", "medium", "low", "off"}
INPUT_TOKEN_LIMIT = 250_000
OUTPUT_TOKEN_LIMIT = 65_535


@dataclass
class LLMResponse:
    text: str
    thinking_text: str = ""
    thinking_tokens: int = 0
    output_tokens: int = 0
    input_tokens: int = 0
    suspect: bool = False


@dataclass
class CoreError:
    code: str
    message: str
    estimated_tokens: int | None = None
    limit_tokens: int | None = None


@dataclass
class CoreResult:
    status: str
    correlation_id: str
    response: LLMResponse | None = None
    error: CoreError | None = None


def resolve_provider() -> str:
    return os.environ.get("LLM_PROVIDER", "gemini")


def validate_package(package: dict) -> str | None:
    for field in ("task", "tier", "thinking", "rounds", "correlation_id"):
        if not package.get(field):
            return f"missing required field: {field}"
    if package["tier"] not in VALID_TIERS:
        return f"unsupported tier: {package['tier']}"
    if package["thinking"] not in VALID_THINKING:
        return f"unsupported thinking: {package['thinking']}"
    seen_indices = []
    for round_ in package["rounds"]:
        seen_indices.append(round_["round_index"])
        blocks = round_.get("blocks") or []
        if not any(b["role"] == "system" for b in blocks) or not any(b["role"] == "user" for b in blocks):
            return f"round {round_['round_index']} lacks a system+user block pair"
    if seen_indices != sorted(set(seen_indices)):
        return "round_index values must be unique and ascending"
    return None


def estimate_input_tokens(package: dict) -> int:
    total_chars = sum(len(b["text"]) for r in package["rounds"] for b in r["blocks"])
    return total_chars // 4


def assemble_messages(package: dict, history: list[LLMResponse]) -> tuple[str, list[dict]]:
    round_ = package["rounds"][-1]
    system_text = "\n\n".join(b["text"] for b in round_["blocks"] if b["role"] == "system")
    user_text = "\n\n".join(b["text"] for b in round_["blocks"] if b["role"] == "user")
    cacheable = any(b["role"] == "system" and b.get("cacheable") for b in round_["blocks"])

    messages = []
    if round_.get("expects_history"):
        for turn in history:
            messages.append({"role": "assistant", "content": turn.text})
    messages.append({"role": "user", "content": user_text, "cacheable": cacheable})
    return system_text, messages


def call(package: dict, history: list[LLMResponse] | None = None) -> CoreResult:
    correlation_id = package["correlation_id"]
    round_index = package["rounds"][-1]["round_index"]
    started = time.monotonic()

    def log_event(status: str, **fields):
        logger.info(
            "correlation_id=%s task=%s round=%d status=%s duration_ms=%d %s",
            correlation_id, package["task"], round_index, status,
            int((time.monotonic() - started) * 1000),
            " ".join(f"{k}={v}" for k, v in fields.items()),
        )

    invalid_reason = validate_package(package)
    if invalid_reason:
        log_event("invalid_package", reason=invalid_reason)
        return CoreResult(
            status="invalid_package", correlation_id=correlation_id,
            error=CoreError(code="malformed_package", message=invalid_reason),
        )

    estimated_input_tokens = estimate_input_tokens(package)
    if estimated_input_tokens > INPUT_TOKEN_LIMIT:
        log_event("overflow", estimated=estimated_input_tokens, limit=INPUT_TOKEN_LIMIT)
        return CoreResult(
            status="overflow", correlation_id=correlation_id,
            error=CoreError(
                code="input_overflow", message="estimated input tokens exceed model limit",
                estimated_tokens=estimated_input_tokens, limit_tokens=INPUT_TOKEN_LIMIT,
            ),
        )

    system_text, messages = assemble_messages(package, history or [])
    max_output_tokens = package.get("max_output_tokens") or OUTPUT_TOKEN_LIMIT
    provider = resolve_provider()

    if provider not in PROVIDERS:
        reason = f"unknown provider: {provider}"
        log_event("invalid_package", reason=reason)
        return CoreResult(
            status="invalid_package", correlation_id=correlation_id,
            error=CoreError(code="malformed_package", message=reason),
        )

    try:
        result: ProviderResult = PROVIDERS[provider](
            task=package["task"], tier=package["tier"], thinking=package["thinking"], system=system_text,
            messages=messages, max_output_tokens=max_output_tokens,
        )
    except ProviderError as exc:
        log_event("provider_error", code=exc.code, message=exc.message, attempts=len(exc.attempts))
        return CoreResult(
            status="provider_error", correlation_id=correlation_id,
            error=CoreError(code=exc.code, message=exc.message),
        )

    suspect = (package["thinking"] != "off" and result.thinking_tokens == 0) or not result.text
    response = LLMResponse(
        text=result.text, thinking_text=result.thinking_text, thinking_tokens=result.thinking_tokens,
        output_tokens=result.output_tokens, input_tokens=result.input_tokens, suspect=suspect,
    )

    if not result.text:
        log_event("suspect_output", reason="empty response text")
        return CoreResult(status="suspect_output", correlation_id=correlation_id, response=response)

    log_event(
        "ok", suspect=suspect, model=result.model, input_tokens=response.input_tokens,
        output_tokens=response.output_tokens, thinking_tokens=response.thinking_tokens,
    )
    return CoreResult(status="ok", correlation_id=correlation_id, response=response)
