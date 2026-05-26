"""Runtime effect contracts and source lifecycle ordering helpers."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.contracts import ResultClass, StageResultEnvelope, WorkItemKind
from millrace_ai.contracts.work_refs import coerce_family_and_kind
from millrace_ai.events import write_runtime_event
from millrace_ai.workspace.paths import WorkspacePaths

from .lifecycle_interpreter import apply_source_lifecycle_intent


class RuntimeEffectDecision(str, Enum):
    CONTINUE_ROUTE = "continue_route"
    REQUEST_COMPLETE_SOURCE = "request_complete_source"
    REQUEST_BLOCK_SOURCE = "request_block_source"
    RETRY_RECOVERY = "retry_recovery"


class RuntimeEffectMutationPhase(str, Enum):
    PRE_MUTATION = "pre_mutation"
    PARTIAL_MUTATION = "partial_mutation"
    UNKNOWN = "unknown"


class SourceLifecycleAction(str, Enum):
    COMPLETE = "complete"
    BLOCK = "block"


class SourceLifecycleIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lifecycle_plan_id: str
    action: SourceLifecycleAction
    work_item_family_id: str | None = None
    work_item_kind: WorkItemKind | None = None
    work_item_id: str

    @model_validator(mode="after")
    def validate_work_ref(self) -> "SourceLifecycleIntent":
        family_id, work_item_kind = coerce_family_and_kind(
            family_id=self.work_item_family_id,
            work_item_kind=self.work_item_kind,
        )
        if family_id is None:
            raise ValueError("source lifecycle intent requires work_item_family_id or work_item_kind")
        self.work_item_family_id = family_id
        self.work_item_kind = work_item_kind
        return self


class RuntimeEffectResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handler_id: str
    decision: RuntimeEffectDecision = RuntimeEffectDecision.CONTINUE_ROUTE
    created_paths: tuple[str, ...] = ()
    source_lifecycle_intent: SourceLifecycleIntent | None = None
    failure_class: str | None = None
    message: str | None = None
    mutation_phase: RuntimeEffectMutationPhase = RuntimeEffectMutationPhase.UNKNOWN


def lifecycle_intent_for_terminal_result(
    stage_result: StageResultEnvelope,
    *,
    lifecycle_plan_id: str,
) -> SourceLifecycleIntent:
    action = (
        SourceLifecycleAction.COMPLETE
        if stage_result.result_class is ResultClass.SUCCESS
        else SourceLifecycleAction.BLOCK
    )
    return SourceLifecycleIntent(
        lifecycle_plan_id=lifecycle_plan_id,
        action=action,
        work_item_family_id=stage_result.work_item_family_id,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
    )


def apply_runtime_effect_result(
    paths: WorkspacePaths,
    effect_result: RuntimeEffectResult,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    missing_paths = tuple(
        path
        for path in effect_result.created_paths
        if not _effect_path(paths, path).exists()
    )
    if missing_paths:
        intent = effect_result.source_lifecycle_intent
        write_runtime_event(
            paths,
            event_type="runtime_effect_destination_missing",
            data={
                "handler_id": effect_result.handler_id,
                "missing_paths": list(missing_paths),
                "work_item_family_id": intent.work_item_family_id if intent is not None else None,
                "work_item_kind": (
                    intent.work_item_kind.value
                    if intent is not None and intent.work_item_kind is not None
                    else None
                ),
                "work_item_id": intent.work_item_id if intent is not None else None,
                "lifecycle_plan_id": intent.lifecycle_plan_id if intent is not None else None,
            },
        )
        return effect_result.model_copy(
            update={
                "decision": RuntimeEffectDecision.RETRY_RECOVERY,
                "failure_class": "runtime_effect_destination_missing",
            }
        )

    if effect_result.source_lifecycle_intent is not None:
        apply_source_lifecycle_intent(
            paths,
            effect_result.source_lifecycle_intent,
            compiled_plan=compiled_plan,
        )
    return effect_result


def _effect_path(paths: WorkspacePaths, path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return paths.root / candidate


__all__ = [
    "RuntimeEffectDecision",
    "RuntimeEffectMutationPhase",
    "RuntimeEffectResult",
    "SourceLifecycleAction",
    "SourceLifecycleIntent",
    "apply_runtime_effect_result",
    "lifecycle_intent_for_terminal_result",
]
