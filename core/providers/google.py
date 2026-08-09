#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
# Name: google.py
# Version: 1.1.0
# Organization: MontageSubs (蒙太奇字幕社区)
# Contributors: Meow P (小p)
# License: MIT License
# Source: https://github.com/MontageSubs/montagesubs-llm-actions/
#
# Description / 描述:
#    Gemini供应商实现，持有router.py不该知道的一切供应商专属知识：模型
#    fallback链、思考档位映射、按task分池的token管理。
#
#    Token池：GOOGLE_LLM_TOKEN_ANALYSIS / _TRANSLATE / _REVIEW 供人工按task
#    独立配置；GOOGLE_LLM_TOKEN不由人工设置，运行时自动取三者中"已配置"部分
#    的并集去重，作为未单独配置task的兜底池——一旦某task配了专属池，就只用
#    专属池，不会再掉入兜底池。
#    池内顺序：每个task的池在本进程内首次用到时做一次随机打乱并缓存，之后
#    整个进程生命周期内顺序不变；一次调用失败即把该task的游标移到下一个
#    token，天然让多轮调用之间轮换、不总打同一个token的量。
#    不可用判定按task+token哈希隔离，不跨task共享：auth_error判定为该token
#    在本次任务（本进程）内永久不可用，不再重试；rate_limit判定为限流，
#    进入COOLDOWN_SECONDS秒冷却后可重试；其余错误（网络/服务端/空响应等）
#    不标记该token不可用，仅移动游标换下一个，因为这类错误不代表token本身
#    有问题。全部状态存于进程内存，不落盘——CI一次运行一个进程，无需跨运行
#    持久化。
#    日志：非DEBUG只报每个task池的可用/不可用数量；DEBUG额外逐个打印token
#    的SHA256摘要与状态，全程不输出token明文。
#
#    Provider owns everything router.py must not know: model fallback
#    chains, thinking-level mapping, and per-task token pool management.
#    State (shuffle order, cursor, dead set, cooldown map) lives in
#    process memory only, scoped per task — never shared across tasks,
#    never persisted to disk.
# ============================================================================
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import json
import logging
import os
import random
import hashlib
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger("core.provider.google")

TOKEN_ENV = "GOOGLE_LLM_TOKEN"
TASK_TOKEN_ENVS = (f"{TOKEN_ENV}_ANALYSIS", f"{TOKEN_ENV}_TRANSLATE", f"{TOKEN_ENV}_REVIEW")
DEBUG_ENV = "DEBUG"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
REQUEST_TIMEOUT_SECONDS = 1800
HEARTBEAT_INTERVAL_SECONDS = 20
COOLDOWN_SECONDS = 60.0
USER_AGENT = "montagesubs-llm-actions/google"

MODEL_CHAINS = {
    "strong": ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash"],
    "weak": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
}

THINKING_LEVEL_MAP = {"off": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
THINKING_HEADROOM = {"off": 1000, "low": 4000, "medium": 12000, "high": 24000}

RETRY_SAME_TARGET_CODES = {"network_error", "server_error", "empty_response"}
SAME_TARGET_ATTEMPTS = 2
BACKOFF_SECONDS = 2.0

_pool_order: dict[str, list[str]] = {}
_pool_cursor: dict[str, int] = {}
_dead_tokens: dict[str, set[str]] = {}
_cooldown_until: dict[str, dict[str, float]] = {}


@dataclass
class ProviderResult:
    text: str
    thinking_text: str
    thinking_tokens: int
    output_tokens: int
    input_tokens: int
    model: str


class ProviderError(Exception):
    def __init__(self, code: str, message: str, attempts: list[dict] | None = None):
        self.code = code
        self.message = message
        self.attempts = attempts or []
        super().__init__(message)


def is_debug() -> bool:
    return os.environ.get(DEBUG_ENV) == "1"


def parse_token_pool(raw: str | None) -> list[str]:
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load_task_pool(task: str) -> list[str]:
    dedicated = parse_token_pool(os.environ.get(f"{TOKEN_ENV}_{task.upper()}"))
    if dedicated:
        return dedicated
    union, seen = [], set()
    for env_name in TASK_TOKEN_ENVS:
        for token in parse_token_pool(os.environ.get(env_name)):
            if token not in seen:
                seen.add(token)
                union.append(token)
    return union


def _ordered_pool(task: str) -> list[str]:
    if task not in _pool_order:
        pool = _load_task_pool(task)
        random.shuffle(pool)
        _pool_order[task] = pool
        _pool_cursor[task] = 0
    return _pool_order[task]


def _is_available(task: str, digest: str) -> bool:
    if digest in _dead_tokens.get(task, ()):
        return False
    cooldown_until = _cooldown_until.get(task, {}).get(digest)
    return cooldown_until is None or time.monotonic() >= cooldown_until


def _advance_cursor(task: str, pool_size: int) -> None:
    _pool_cursor[task] = (_pool_cursor[task] + 1) % pool_size


def _mark_dead(task: str, digest: str) -> None:
    _dead_tokens.setdefault(task, set()).add(digest)


def _mark_cooldown(task: str, digest: str) -> None:
    _cooldown_until.setdefault(task, {})[digest] = time.monotonic() + COOLDOWN_SECONDS


def _log_pool_summary(task: str) -> None:
    pool = _ordered_pool(task)
    available = [t for t in pool if _is_available(task, token_digest(t))]
    logger.info("token_pool task=%s size=%d unavailable=%d", task, len(pool), len(pool) - len(available))
    if is_debug():
        for token in pool:
            digest = token_digest(token)
            status = "dead" if digest in _dead_tokens.get(task, ()) else (
                "available" if _is_available(task, digest) else
                f"cooldown remaining={_cooldown_until[task][digest] - time.monotonic():.0f}s"
            )
            logger.info("token_pool task=%s token=%s status=%s", task, digest[:16], status)


def resolve_thinking(thinking: str) -> tuple[dict, int]:
    level = THINKING_LEVEL_MAP.get(thinking)
    if level is None:
        raise ProviderError("bad_request", f"unsupported thinking level: {thinking}")
    return {"thinkingLevel": level}, THINKING_HEADROOM[thinking]


def _classify_http_error(status: int, body: str) -> tuple[str, str]:
    lowered = body.lower()
    if status in (401, 403):
        return "auth_error", f"http {status}: {body}"
    if status == 429 or "quota" in lowered or "resource_exhausted" in lowered:
        return "rate_limit", f"http {status}: {body}"
    if status in (400, 404, 413, 422):
        return "bad_request", f"http {status}: {body}"
    if status >= 500:
        return "server_error", f"http {status}: {body}"
    return "unknown", f"http {status}: {body}"


def _build_contents(messages: list[dict]) -> list[dict]:
    contents = []
    for message in messages:
        role = "model" if message["role"] == "assistant" else "user"
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append({"text": message["content"]})
        else:
            contents.append({"role": role, "parts": [{"text": message["content"]}]})
    return contents or [{"role": "user", "parts": [{"text": ""}]}]


def _iter_sse_data(response):
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data and data != "[DONE]":
            yield data


def _call_model(
    model: str, token: str, thinking: str,
    system_text: str, messages: list[dict], max_output_tokens: int,
) -> ProviderResult:
    thinking_config, headroom = resolve_thinking(thinking)
    config = {
        "maxOutputTokens": max_output_tokens + headroom,
        "thinkingConfig": {**thinking_config, "includeThoughts": True},
    }
    payload = {"contents": _build_contents(messages), "generationConfig": config}
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    request = urllib.request.Request(
        ENDPOINT.format(model=model),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": token, "User-Agent": USER_AGENT},
        method="POST",
    )

    debug = is_debug()
    logger.info("[%s] request thinking=%s(%s) payload_bytes=%d", model, thinking, thinking_config["thinkingLevel"], len(request.data))

    answer_parts, thinking_parts, finish_reason, usage = [], [], None, {}
    started = time.monotonic()
    last_heartbeat = started
    chars_received = 0
    debug_buffer: list[str] = []

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            for data in _iter_sse_data(response):
                chunk = json.loads(data)
                for candidate in chunk.get("candidates") or []:
                    for part in (candidate.get("content") or {}).get("parts") or []:
                        text_piece = part.get("text") or ""
                        if part.get("thought"):
                            thinking_parts.append(text_piece)
                        else:
                            answer_parts.append(text_piece)
                            chars_received += len(text_piece)
                            if debug:
                                debug_buffer.append(text_piece)
                    finish_reason = candidate.get("finishReason") or finish_reason
                if chunk.get("usageMetadata"):
                    usage = chunk["usageMetadata"]

                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    if debug:
                        logger.info("[%s] heartbeat %.0fs chars=%d content=%r", model, now - started, chars_received, "".join(debug_buffer))
                        debug_buffer.clear()
                    else:
                        logger.info("[%s] heartbeat %.0fs chars=%d", model, now - started, chars_received)
                    last_heartbeat = now
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        code, message = _classify_http_error(exc.code, body)
        raise ProviderError(code, message) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        raise ProviderError("network_error", str(exc) or "connection lost mid-stream") from exc
    except json.JSONDecodeError as exc:
        raise ProviderError("unknown", f"malformed stream chunk: {exc}") from exc

    if finish_reason in ("SAFETY", "RECITATION", "BLOCKLIST"):
        raise ProviderError("bad_request", f"content blocked by provider ({finish_reason})")

    text = "".join(answer_parts)
    if not text:
        raise ProviderError("empty_response", "no text content in response")

    logger.info(
        "[%s] stream complete elapsed=%.1fs input_tokens=%s output_tokens=%s thinking_tokens=%s",
        model, time.monotonic() - started,
        usage.get("promptTokenCount"), usage.get("candidatesTokenCount"), usage.get("thoughtsTokenCount"),
    )

    return ProviderResult(
        text=text,
        thinking_text="".join(thinking_parts),
        thinking_tokens=usage.get("thoughtsTokenCount") or 0,
        output_tokens=usage.get("candidatesTokenCount") or 0,
        input_tokens=usage.get("promptTokenCount") or 0,
        model=model,
    )


def complete(
    task: str, tier: str, thinking: str, system: str, messages: list[dict], max_output_tokens: int,
) -> ProviderResult:
    chain = MODEL_CHAINS.get(tier)
    if not chain:
        raise ProviderError("bad_request", f"no model chain configured for tier={tier}")

    pool = _ordered_pool(task)
    if not pool:
        raise ProviderError("auth_error", f"no tokens configured for task={task} (dedicated or shared pool)")
    pool_size = len(pool)

    attempts: list[dict] = []
    last_error: ProviderError | None = None

    for model in chain:
        checked = 0
        while checked < pool_size:
            token = pool[_pool_cursor[task]]
            digest = token_digest(token)
            checked += 1
            if not _is_available(task, digest):
                _advance_cursor(task, pool_size)
                continue

            for attempt_number in range(1, SAME_TARGET_ATTEMPTS + 1):
                try:
                    result = _call_model(model, token, thinking, system, messages, max_output_tokens)
                    attempts.append({"model": model, "token": digest[:16], "outcome": "ok"})
                    _log_pool_summary(task)
                    return result
                except ProviderError as exc:
                    attempts.append({"model": model, "token": digest[:16], "outcome": exc.code, "message": exc.message})
                    last_error = exc
                    logger.info("[%s] attempt failed task=%s token=%s outcome=%s message=%s", model, task, digest[:16], exc.code, exc.message)
                    if exc.code == "auth_error":
                        _mark_dead(task, digest)
                        _advance_cursor(task, pool_size)
                        break
                    if exc.code == "rate_limit":
                        _mark_cooldown(task, digest)
                        _advance_cursor(task, pool_size)
                        break
                    if exc.code in RETRY_SAME_TARGET_CODES and attempt_number < SAME_TARGET_ATTEMPTS:
                        time.sleep(BACKOFF_SECONDS)
                        continue
                    _advance_cursor(task, pool_size)
                    break

    _log_pool_summary(task)
    raise ProviderError(
        last_error.code if last_error else "unknown",
        f"all models/tokens exhausted after {len(attempts)} attempts, last error: "
        f"{last_error.message if last_error else 'n/a'}",
        attempts=attempts,
    )


PROVIDERS = {
    "gemini": complete,
}
