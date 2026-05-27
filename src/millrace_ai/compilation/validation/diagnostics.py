"""Shared diagnostics helpers for compiler validation."""

from __future__ import annotations

from millrace_ai.contracts import StageMapKey


def stage_key_value(stage: StageMapKey) -> str:
    return stage.value if hasattr(stage, "value") else str(stage)


__all__ = ["stage_key_value"]
