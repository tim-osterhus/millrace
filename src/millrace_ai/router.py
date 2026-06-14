"""Legacy compatibility facade for router decision contracts.

Active routing is owned by ``millrace_ai.runtime.graph_authority.routing``.
This module intentionally keeps only import-compatible contract exports and
does not contain hardcoded stage transition authority.
"""

from __future__ import annotations

from millrace_ai.contracts.router import (
    RouterAction,
    RouterDecision,
    counter_key_for_failure_class,
    normalize_failure_class,
)

__all__ = [
    "RouterAction",
    "RouterDecision",
    "counter_key_for_failure_class",
    "normalize_failure_class",
]
