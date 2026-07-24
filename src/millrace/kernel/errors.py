"""Shared kernel transition exceptions.

This module owns exception categories used by decision and mutation code. It
must not own transition decisions, state mutation logic, or persistence
semantics.
"""

from __future__ import annotations


class StateConcurrencyError(RuntimeError):
    """Raised when apply-time state no longer matches a transition decision."""


class UnsupportedMutationError(RuntimeError):
    """Raised when a transition decision contains an unsupported mutation."""


__all__ = (
    "StateConcurrencyError",
    "UnsupportedMutationError",
)
