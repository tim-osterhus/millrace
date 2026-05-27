"""Runtime request-context planning and rendering package."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.runners import StageRunRequest

from .models import RenderedRequestContext, RequestContextRenderPlan
from .providers import (
    build_request_context_plan,
    default_request_context_provider_registry,
    validate_stage_request_context_provider_implementation,
)
from .rendering import render_request_context


def attach_default_request_context(
    *,
    workspace_root: Path,
    request: StageRunRequest,
    compiled_plan: CompiledRunPlan | None = None,
) -> StageRunRequest:
    """Write the default stage request context artifacts and return an enriched request."""

    plan = build_request_context_plan(
        workspace_root=workspace_root,
        request=request,
        compiled_plan=compiled_plan,
    )
    rendered = render_request_context(plan, workspace_root=workspace_root)
    return request.model_copy(
        update={
            "request_context_profile_id": plan.profile_id,
            "context_bundle_path": rendered.context_bundle_path,
            "context_artifact_refs": plan.visible_artifact_refs,
            "context_render_plan_id": plan.render_plan_id,
            "rendered_prompt_context_path": rendered.rendered_prompt_context_path,
        }
    )


__all__ = [
    "RenderedRequestContext",
    "RequestContextRenderPlan",
    "attach_default_request_context",
    "default_request_context_provider_registry",
    "render_request_context",
    "validate_stage_request_context_provider_implementation",
]
