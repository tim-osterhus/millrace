"""Constructors for empty kernel runtime state."""

from __future__ import annotations

from millrace.contracts.state import RuntimeState


def empty_runtime_state() -> RuntimeState:
    return RuntimeState()


__all__ = ("empty_runtime_state",)
