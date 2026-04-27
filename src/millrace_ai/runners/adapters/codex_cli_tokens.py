"""Codex CLI token usage extraction."""

from __future__ import annotations

import json
from pathlib import Path

from millrace_ai.contracts import TokenUsage


def extract_token_usage(event_log_path: Path | None) -> TokenUsage | None:
    if event_log_path is None or not event_log_path.exists():
        return None

    best: TokenUsage | None = None
    try:
        lines = event_log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        candidate = token_usage_from_line(line)
        if candidate is None:
            continue
        if best is None or candidate.total_tokens >= best.total_tokens:
            best = candidate
    return best


def token_usage_from_line(line: str) -> TokenUsage | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return token_usage_from_payload(payload)


def token_usage_from_payload(payload: object) -> TokenUsage | None:
    if not isinstance(payload, dict):
        return None

    payload_type = payload.get("type")
    nested_payload = payload.get("payload")
    if payload_type == "event_msg" and isinstance(nested_payload, dict):
        return token_usage_from_payload(nested_payload)

    if payload_type != "token_count":
        return None

    info = payload.get("info")
    if not isinstance(info, dict):
        return None

    usage_payload = info.get("total_token_usage")
    if not isinstance(usage_payload, dict):
        usage_payload = info.get("last_token_usage")
    if not isinstance(usage_payload, dict):
        return None
    return token_usage_from_dict(usage_payload)


def token_usage_from_dict(payload: dict[str, object]) -> TokenUsage | None:
    input_tokens = int_from_payload(payload, "input_tokens")
    output_tokens = int_from_payload(payload, "output_tokens")
    if input_tokens is None or output_tokens is None:
        return None

    cached_input_tokens = int_from_payload(payload, "cached_input_tokens", default=0) or 0
    thinking_tokens = (
        int_from_payload(
            payload,
            "reasoning_output_tokens",
            "thinking_tokens",
            "reasoning_tokens",
            default=0,
        )
        or 0
    )
    total_tokens = int_from_payload(payload, "total_tokens", default=input_tokens + output_tokens)
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens

    return TokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        total_tokens=total_tokens,
    )


def int_from_payload(
    payload: dict[str, object],
    *keys: str,
    default: int | None = None,
) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return default
    return default


__all__ = [
    "extract_token_usage",
    "int_from_payload",
    "token_usage_from_dict",
    "token_usage_from_line",
    "token_usage_from_payload",
]
