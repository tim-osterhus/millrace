"""Compatibility facade for runtime request-context rendering."""

from __future__ import annotations

from .context import (
    RenderedRequestContext,
    RequestContextRenderPlan,
    attach_default_request_context,
    render_request_context,
)

__all__ = [
    "RenderedRequestContext",
    "RequestContextRenderPlan",
    "attach_default_request_context",
    "render_request_context",
]
