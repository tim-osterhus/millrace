"""Request identity and provenance preservation helpers."""

from __future__ import annotations

from pydantic import JsonValue

from millrace_ai.contracts import WorkItemKind
from millrace_ai.runners.requests import RunnerRawResult, StageRunRequest


def request_result_identity(request: StageRunRequest) -> tuple[str, WorkItemKind | None, str]:
    if request.request_kind == "closure_target":
        if request.closure_target_root_spec_id is None:
            raise ValueError("closure_target_root_spec_id is required for closure_target requests")
        return (WorkItemKind.SPEC.value, WorkItemKind.SPEC, request.closure_target_root_spec_id)
    if request.request_kind == "learning_request":
        if request.active_work_item_id is None:
            raise ValueError("active_work_item_id is required for learning_request requests")
        return (
            WorkItemKind.LEARNING_REQUEST.value,
            WorkItemKind.LEARNING_REQUEST,
            request.active_work_item_id,
        )

    if request.active_work_item_family_id is None or request.active_work_item_id is None:
        raise ValueError(
            "active_work_item_family_id and active_work_item_id are required to normalize stage results"
        )
    return (
        request.active_work_item_family_id,
        request.active_work_item_kind,
        request.active_work_item_id,
    )


def resolved_thinking_level(
    request: StageRunRequest,
    raw_result: RunnerRawResult,
) -> str | None:
    return (
        raw_result.thinking_level
        or request.thinking_level
        or raw_result.model_reasoning_effort
        or request.model_reasoning_effort
    )


def request_metadata(request: StageRunRequest) -> dict[str, JsonValue]:
    return {
        "request_id": request.request_id,
        "request_kind": request.request_kind,
        "mode_id": request.mode_id,
        "compiled_plan_id": request.compiled_plan_id,
        "closure_target_root_spec_id": request.closure_target_root_spec_id,
        "closure_target_root_source_kind": request.closure_target_root_source_kind,
        "closure_target_root_source_id": request.closure_target_root_source_id,
        "closure_target_root_source_path": request.closure_target_root_source_path,
        "closure_target_root_idea_id": request.closure_target_root_idea_id,
        "closure_evidence_window_path": request.closure_evidence_window_path,
        "preferred_rubric_path": request.preferred_rubric_path,
        "preferred_verdict_path": request.preferred_verdict_path,
        "preferred_report_path": request.preferred_report_path,
        "active_work_item_family_id": request.active_work_item_family_id,
        "active_work_item_kind": (
            request.active_work_item_kind.value
            if request.active_work_item_kind is not None
            else None
        ),
        "active_work_item_id": request.active_work_item_id,
        "active_work_item_path": request.active_work_item_path,
        "skill_revision_evidence_path": request.skill_revision_evidence_path,
        "request_context_profile_id": request.request_context_profile_id,
        "context_bundle_path": request.context_bundle_path,
        "context_artifact_refs": list(request.context_artifact_refs),
        "context_render_plan_id": request.context_render_plan_id,
        "rendered_prompt_context_path": request.rendered_prompt_context_path,
        "thinking_level": request.thinking_level,
        "model_reasoning_effort": request.model_reasoning_effort,
        "model_assignment_alias_id": request.model_assignment_alias_id,
        "model_assignment_source": request.model_assignment_source,
        "execution_capability_grants": [
            grant.model_dump(mode="json") for grant in request.execution_capability_grants
        ],
        "capability_support_decisions": [
            decision.model_dump(mode="json")
            for decision in request.capability_support_decisions
        ],
    }


__all__ = [
    "request_metadata",
    "request_result_identity",
    "resolved_thinking_level",
]
