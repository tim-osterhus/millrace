"""Built-in generic request-context provider implementations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import Plane
from millrace_ai.runners import StageRunRequest

from .models import RequestContextAuthority, RequestContextRenderPlan

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan


def generic_active_work_item_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    if request.plane is Plane.EXECUTION:
        return generic_execution_context_plan(workspace_root, request, authority, compiled_plan)
    if request.plane is Plane.LEARNING:
        return generic_learning_context_plan(workspace_root, request, authority, compiled_plan)
    return generic_planning_context_plan(workspace_root, request, authority, compiled_plan)


def generic_execution_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    del workspace_root, compiled_plan
    return _default_context_plan(request, authority)


def generic_planning_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    del workspace_root, compiled_plan
    return _default_context_plan(request, authority)


def generic_learning_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    del workspace_root, compiled_plan
    return _default_context_plan(request, authority)


def recon_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    del workspace_root, compiled_plan
    return _default_context_plan(request, authority)


def integrator_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    del workspace_root, compiled_plan
    return _default_context_plan(request, authority)


def generic_closure_target_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    del workspace_root, compiled_plan
    return _default_context_plan(request, authority)


def built_in_generic_provider_registrations() -> tuple[tuple[str, object], ...]:
    return (
        ("generic.active_work_item", generic_active_work_item_context_plan),
        ("generic.closure_target", generic_closure_target_context_plan),
        ("generic.execution", generic_execution_context_plan),
        ("generic.planning", generic_planning_context_plan),
        ("generic.learning", generic_learning_context_plan),
        ("recon", recon_context_plan),
        ("integrator", integrator_context_plan),
    )


def _default_context_plan(
    request: StageRunRequest,
    authority: RequestContextAuthority,
) -> RequestContextRenderPlan:
    run_dir = Path(request.run_dir)
    context_dir = run_dir / "context"
    context_bundle_path = context_dir / "context.json"
    rendered_prompt_context_path = context_dir / "prompt_context.md"
    return RequestContextRenderPlan(
        render_plan_id=authority.render_plan_id,
        provider_id=authority.provider_id,
        profile_id=authority.profile_id,
        context_bundle_path=str(context_bundle_path),
        rendered_prompt_context_path=str(rendered_prompt_context_path),
        visible_artifact_refs=visible_artifact_refs(request),
        operator_only_artifact_refs=(
            f"runtime_snapshot:{request.runtime_snapshot_path}",
            f"recovery_counters:{request.recovery_counters_path}",
        ),
        inline_sections=("active_work_item",) if request.active_work_item_path else (),
        omitted_provider_ids=(),
    )


def visible_artifact_refs(request: StageRunRequest) -> tuple[str, ...]:
    if request.active_work_item_family_id is not None and request.active_work_item_id is not None:
        return (f"{request.active_work_item_family_id}:{request.active_work_item_id}",)
    if request.closure_target_root_spec_id is not None:
        return (f"closure_target:{request.closure_target_root_spec_id}",)
    return ()


__all__ = [
    "built_in_generic_provider_registrations",
    "visible_artifact_refs",
]
