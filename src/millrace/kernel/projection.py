"""Kernel import surface for compiled terminal-action projection.

The pure implementation lives in ``millrace.contracts.projection``.
"""

from __future__ import annotations

from millrace.contracts.projection import (
    ProjectionContext,
    evaluate_projection,
    projection_context_for_run,
)

__all__ = ("ProjectionContext", "evaluate_projection", "projection_context_for_run")
