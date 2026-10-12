"""Kernel transition API and authenticated run-settlement query."""

from millrace.kernel.lookups import run_is_settled
from millrace.kernel.state import empty_runtime_state
from millrace.kernel.transition import (
    StateConcurrencyError,
    UnsupportedMutationError,
    apply,
    decide,
)

__all__ = (
    "StateConcurrencyError",
    "UnsupportedMutationError",
    "apply",
    "decide",
    "empty_runtime_state",
    "run_is_settled",
)
