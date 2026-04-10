"""Shared helpers for stable slug and run-id generation."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_TRUNCATED_DIGEST_LENGTH = 12


def stable_slug(value: str, *, fallback: str, max_length: int | None = None) -> str:
    slug = _TOKEN_RE.sub("-", value.strip().lower()).strip("-") or fallback
    if max_length is None or len(slug) <= max_length:
        return slug
    if max_length <= _TRUNCATED_DIGEST_LENGTH + 1:
        return hashlib.sha1(slug.encode("utf-8")).hexdigest()[:max_length]
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:_TRUNCATED_DIGEST_LENGTH]
    head = slug[: max_length - _TRUNCATED_DIGEST_LENGTH - 1].rstrip("-")
    return f"{head or fallback}-{digest}"


def timestamped_slug_id(
    label: str,
    *,
    fallback: str,
    moment: datetime | None = None,
    max_length: int | None = None,
) -> str:
    observed_at = moment or datetime.now(timezone.utc)
    timestamp = observed_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}__{stable_slug(label, fallback=fallback, max_length=max_length)}"


__all__ = ["stable_slug", "timestamped_slug_id"]
