"""Pause-source helpers for operator and runtime-owned pauses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from millrace_ai.contracts import RuntimeSnapshot

PauseSource = Literal["operator", "usage_governance"]
OPERATOR_PAUSE_SOURCE: PauseSource = "operator"
USAGE_GOVERNANCE_PAUSE_SOURCE: PauseSource = "usage_governance"

_SOURCE_ORDER: tuple[PauseSource, ...] = (
    OPERATOR_PAUSE_SOURCE,
    USAGE_GOVERNANCE_PAUSE_SOURCE,
)


def add_pause_source(
    snapshot: RuntimeSnapshot,
    *,
    source: PauseSource,
    now: datetime,
) -> RuntimeSnapshot:
    sources: set[PauseSource] = set(snapshot.pause_sources)
    sources.add(source)
    return snapshot.model_copy(
        update={
            "paused": True,
            "pause_sources": _ordered_sources(sources),
            "updated_at": now,
        }
    )


def remove_pause_source(
    snapshot: RuntimeSnapshot,
    *,
    source: PauseSource,
    now: datetime,
) -> RuntimeSnapshot:
    sources: set[PauseSource] = set(snapshot.pause_sources)
    sources.discard(source)
    ordered = _ordered_sources(sources)
    return snapshot.model_copy(
        update={
            "paused": bool(ordered),
            "pause_sources": ordered,
            "updated_at": now,
        }
    )


def clear_pause_sources(snapshot: RuntimeSnapshot, *, now: datetime) -> RuntimeSnapshot:
    return snapshot.model_copy(
        update={
            "paused": False,
            "pause_sources": (),
            "updated_at": now,
        }
    )


def has_pause_source(snapshot: RuntimeSnapshot, source: PauseSource) -> bool:
    return source in snapshot.pause_sources


def pause_sources_label(snapshot: RuntimeSnapshot) -> str:
    if snapshot.pause_sources:
        return ",".join(snapshot.pause_sources)
    if snapshot.paused:
        return OPERATOR_PAUSE_SOURCE
    return "none"


def _ordered_sources(sources: set[PauseSource]) -> tuple[PauseSource, ...]:
    return tuple(source for source in _SOURCE_ORDER if source in sources)


__all__ = [
    "OPERATOR_PAUSE_SOURCE",
    "PauseSource",
    "USAGE_GOVERNANCE_PAUSE_SOURCE",
    "add_pause_source",
    "clear_pause_sources",
    "has_pause_source",
    "pause_sources_label",
    "remove_pause_source",
]
