"""Compatibility module for Blueprint runtime-effect recovery diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.paths import WorkspacePaths

BLUEPRINT_APPROVAL_REPAIR_HANDLER_ID = "evaluator_blueprint_approved_to_task"
BLUEPRINT_APPROVAL_REPAIR_OPERATION_ID = "evaluator_blueprint_approved_to_task"
BLUEPRINT_APPROVAL_REPAIR_POLICY_ID = "blueprint_approval_pre_mutation_effect_validation"
BLUEPRINT_APPROVAL_REPAIR_FAILURE_CLASSES = {
    "generated_task_invalid",
    "generated_task_missing",
}
BLUEPRINT_REPAIR_APPLY_HANDLER_ID = "mechanic_blueprint_repair_apply"
BLUEPRINT_REPAIR_APPLY_OPERATION_ID = "mechanic_blueprint_repair_apply"

BLUEPRINT_REPAIR_CONTRACT_STATUS = (
    "action=apply_repaired_generated_task "
    "artifacts=blueprint_repair_decision,repaired_generated_task,mechanic_report "
    "repaired_artifact=repaired_generated_task"
)
BLUEPRINT_REPLAY_CONFLICT_CLASSES_STATUS = (
    "candidate=blueprint_candidate_duplicate_conflict,blueprint_candidate_markdown_conflict "
    "approval=blueprint_evaluation_duplicate_conflict,blueprint_approved_packet_conflict,"
    "blueprint_approved_markdown_conflict,blueprint_task_duplicate,"
    "blueprint_promotion_duplicate_conflict"
)
BLUEPRINT_INERT_ARTIFACT_GUARD_STATUS = (
    "repaired_blueprint_artifact.md ignored; mechanic_report.md evidence only"
)
BLUEPRINT_RUNTIME_OWNERSHIP_BOUNDARY_STATUS = (
    "mechanic writes repair artifacts only; runtime owns queues and canonical Blueprint state"
)

_RUNTIME_EFFECT_STATUS_KEYS = {
    "latest_runtime_effect_handler_id": "runtime_effect_handler_id",
    "latest_runtime_effect_operation_id": "runtime_effect_operation_id",
    "latest_runtime_effect_legacy_handler_id": "runtime_effect_legacy_handler_id",
    "latest_runtime_effect_decision": "runtime_effect_decision",
    "latest_runtime_effect_failure_class": "runtime_effect_failure_class",
    "latest_runtime_effect_failure_message": "runtime_effect_failure_message",
    "latest_runtime_effect_mutation_phase": "runtime_effect_mutation_phase",
    "latest_runtime_effect_failure_policy_id": "runtime_effect_failure_policy_id",
    "latest_runtime_effect_recovery_action": "runtime_effect_recovery_action",
}


@dataclass(frozen=True, slots=True)
class LatestRuntimeEffectStageResult:
    path: Path
    stage_result: StageResultEnvelope


def latest_runtime_effect_status_metadata(
    paths: WorkspacePaths,
    stage_result_path: str | None,
) -> dict[str, str]:
    latest = latest_runtime_effect_stage_result(paths, stage_result_path)
    if latest is None:
        return {}
    return runtime_effect_status_metadata_from_stage_result(latest.stage_result)


def latest_runtime_effect_stage_result(
    paths: WorkspacePaths,
    stage_result_path: str | None,
) -> LatestRuntimeEffectStageResult | None:
    if stage_result_path is None:
        return None
    path = Path(stage_result_path)
    if not path.is_absolute():
        path = paths.root / path
    stage_result = load_stage_result(path)
    if stage_result is None:
        return None
    latest = _latest_sibling_runtime_effect_stage_result(path, stage_result)
    if latest is not None:
        return latest
    if runtime_effect_status_metadata_from_stage_result(stage_result):
        return LatestRuntimeEffectStageResult(path=path, stage_result=stage_result)
    return None


def load_stage_result(path: Path) -> StageResultEnvelope | None:
    try:
        return StageResultEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def runtime_effect_status_metadata_from_stage_result(
    stage_result: StageResultEnvelope,
) -> dict[str, str]:
    metadata = stage_result.metadata
    values = {
        status_key: metadata.get(metadata_key)
        for status_key, metadata_key in _RUNTIME_EFFECT_STATUS_KEYS.items()
    }
    rendered = {
        key: value
        for key, value in values.items()
        if isinstance(value, str) and value
    }
    rendered.update(blueprint_repair_diagnostic_status_values(rendered))
    return rendered


def blueprint_repair_diagnostic_status_values(
    runtime_effect_values: Mapping[str, str],
) -> dict[str, str]:
    if not _is_blueprint_approval_repair_context(runtime_effect_values):
        return {}
    handler_id = _display_runtime_effect_handler_id(
        runtime_effect_values,
        fallback=BLUEPRINT_APPROVAL_REPAIR_HANDLER_ID,
    )
    failure_class = runtime_effect_values["latest_runtime_effect_failure_class"]
    mutation_phase = runtime_effect_values["latest_runtime_effect_mutation_phase"]
    policy_id = runtime_effect_values.get(
        "latest_runtime_effect_failure_policy_id",
        "unknown",
    )
    recovery_action = runtime_effect_values.get(
        "latest_runtime_effect_recovery_action",
        "unknown",
    )
    return {
        "latest_blueprint_repair_context": (
            f"failed_handler={handler_id} "
            f"failure_class={failure_class} "
            f"mutation_phase={mutation_phase} "
            f"policy={policy_id} "
            f"recovery_action={recovery_action}"
        ),
        "latest_blueprint_repair_contract": BLUEPRINT_REPAIR_CONTRACT_STATUS,
        "latest_blueprint_replay_conflict_classes": BLUEPRINT_REPLAY_CONFLICT_CLASSES_STATUS,
        "latest_blueprint_inert_artifact_guard": BLUEPRINT_INERT_ARTIFACT_GUARD_STATUS,
        "latest_blueprint_runtime_ownership_boundary": (
            BLUEPRINT_RUNTIME_OWNERSHIP_BOUNDARY_STATUS
        ),
    }


def _is_blueprint_approval_repair_context(
    runtime_effect_values: Mapping[str, str],
) -> bool:
    return (
        _runtime_effect_identity_matches(
            runtime_effect_values,
            expected_handler_id=BLUEPRINT_APPROVAL_REPAIR_HANDLER_ID,
            expected_operation_id=BLUEPRINT_APPROVAL_REPAIR_OPERATION_ID,
        )
        and runtime_effect_values.get("latest_runtime_effect_decision")
        == "request_block_source"
        and runtime_effect_values.get("latest_runtime_effect_failure_class")
        in BLUEPRINT_APPROVAL_REPAIR_FAILURE_CLASSES
        and runtime_effect_values.get("latest_runtime_effect_mutation_phase") == "pre_mutation"
        and runtime_effect_values.get("latest_runtime_effect_failure_policy_id")
        == BLUEPRINT_APPROVAL_REPAIR_POLICY_ID
        and runtime_effect_values.get("latest_runtime_effect_recovery_action") == "route_to_node"
    )


def _is_blueprint_repair_apply_context(
    runtime_effect_values: Mapping[str, str],
) -> bool:
    return (
        _runtime_effect_identity_matches(
            runtime_effect_values,
            expected_handler_id=BLUEPRINT_REPAIR_APPLY_HANDLER_ID,
            expected_operation_id=BLUEPRINT_REPAIR_APPLY_OPERATION_ID,
        )
        and runtime_effect_values.get("latest_runtime_effect_decision")
        == "request_complete_source"
    )


def _runtime_effect_identity_matches(
    runtime_effect_values: Mapping[str, str],
    *,
    expected_handler_id: str,
    expected_operation_id: str,
) -> bool:
    return (
        runtime_effect_values.get("latest_runtime_effect_handler_id") == expected_handler_id
        or runtime_effect_values.get("latest_runtime_effect_legacy_handler_id")
        == expected_handler_id
        or runtime_effect_values.get("latest_runtime_effect_operation_id")
        == expected_operation_id
    )


def _display_runtime_effect_handler_id(
    runtime_effect_values: Mapping[str, str],
    *,
    fallback: str,
) -> str:
    return (
        runtime_effect_values.get("latest_runtime_effect_legacy_handler_id")
        or runtime_effect_values.get("latest_runtime_effect_handler_id")
        or runtime_effect_values.get("latest_runtime_effect_operation_id")
        or fallback
    )


def _latest_sibling_runtime_effect_stage_result(
    path: Path,
    stage_result: StageResultEnvelope,
) -> LatestRuntimeEffectStageResult | None:
    if not path.parent.is_dir():
        return None
    current_sort_key = _stage_result_sort_key(stage_result, path)
    candidates: list[
        tuple[tuple[str, str, str], LatestRuntimeEffectStageResult, dict[str, str]]
    ] = []
    for sibling in path.parent.iterdir():
        if not sibling.is_file() or sibling.suffix != ".json":
            continue
        sibling_stage_result = load_stage_result(sibling)
        if sibling_stage_result is None:
            continue
        sort_key = _stage_result_sort_key(sibling_stage_result, sibling)
        if sort_key > current_sort_key:
            continue
        metadata = runtime_effect_status_metadata_from_stage_result(sibling_stage_result)
        if not metadata:
            continue
        candidates.append(
            (
                sort_key,
                LatestRuntimeEffectStageResult(
                    path=sibling,
                    stage_result=sibling_stage_result,
                ),
                metadata,
            )
        )
    if not candidates:
        return None
    _, latest, latest_metadata = sorted(candidates, key=lambda item: item[0])[-1]
    if _is_blueprint_repair_apply_context(latest_metadata):
        repair_context = _latest_blueprint_approval_repair_context(candidates)
        if repair_context is not None:
            return repair_context
    return latest


def _latest_blueprint_approval_repair_context(
    candidates: list[
        tuple[tuple[str, str, str], LatestRuntimeEffectStageResult, dict[str, str]]
    ],
) -> LatestRuntimeEffectStageResult | None:
    for _, candidate, metadata in reversed(sorted(candidates, key=lambda item: item[0])):
        if _is_blueprint_approval_repair_context(metadata):
            return candidate
    return None


def _stage_result_sort_key(
    stage_result: StageResultEnvelope,
    path: Path,
) -> tuple[str, str, str]:
    return (
        stage_result.completed_at.isoformat(),
        stage_result.started_at.isoformat(),
        path.name,
    )
