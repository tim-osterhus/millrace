"""Shared helpers for stable slug and run-id generation."""

from __future__ import annotations

from datetime import datetime, timezone
import re


_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def stable_slug(value: str, *, fallback: str) -> str:
    return _TOKEN_RE.sub("-", value.strip().lower()).strip("-") or fallback


def timestamped_slug_id(
    label: str,
    *,
    fallback: str,
    moment: datetime | None = None,
) -> str:
    observed_at = moment or datetime.now(timezone.utc)
    timestamp = observed_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}__{stable_slug(label, fallback=fallback)}"


__all__ = ["stable_slug", "timestamped_slug_id"]
