"""Public facade for kernel transition decisions and mutation application."""

from __future__ import annotations

from millrace.kernel.decision import decide
from millrace.kernel.mutations import (
    StateConcurrencyError,
    UnsupportedMutationError,
    apply,
)

__all__ = (
    "StateConcurrencyError",
    "UnsupportedMutationError",
    "apply",
    "decide",
)
