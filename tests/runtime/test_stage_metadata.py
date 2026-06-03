from __future__ import annotations

import pytest

from millrace_ai.architecture import MaterializedGraphNodePlan
from millrace_ai.assets import load_builtin_stage_kind_definitions
from millrace_ai.contracts import (
    ExecutionStageName,
    LearningStageName,
    Plane,
    PlanningStageName,
    ResultClass,
    WorkItemKind,
)
from millrace_ai.contracts.stage_metadata import (
    STAGE_METADATA_BY_VALUE,
    allowed_result_classes_by_outcome,
    blocked_terminal_for_plane,
    legal_terminal_markers,
    legal_terminal_results,
    running_status_marker,
    stage_metadata,
    stage_name_for_plane,
    stage_name_for_value,
    stage_plane,
    terminal_result_for_plane,
)
from millrace_ai.errors import StageWorkItemOwnershipError
from millrace_ai.runners import StageRunRequest
from millrace_ai.runtime.stage_requests import validate_stage_work_item_ownership


def _all_stage_values() -> set[str]:
    return {
        *(stage.value for stage in ExecutionStageName),
        *(stage.value for stage in PlanningStageName),
        *(stage.value for stage in LearningStageName),
    }


def test_every_stage_has_exactly_one_metadata_entry_and_plane() -> None:
    assert set(STAGE_METADATA_BY_VALUE) == _all_stage_values()

    for stage_value, metadata in STAGE_METADATA_BY_VALUE.items():
        assert stage_name_for_value(stage_value) is metadata.stage
        assert stage_plane(metadata.stage) is metadata.plane
        assert stage_name_for_plane(metadata.plane, stage_value) is metadata.stage


def test_legal_terminal_markers_derive_from_stage_metadata() -> None:
    for metadata in STAGE_METADATA_BY_VALUE.values():
        expected_markers = tuple(f"### {outcome}" for outcome in metadata.legal_terminal_results)

        assert legal_terminal_markers(metadata.stage) == expected_markers
        assert legal_terminal_results(metadata.stage) == set(metadata.legal_terminal_results)
        for outcome in metadata.legal_terminal_results:
            terminal_result = terminal_result_for_plane(metadata.plane, outcome)
            assert terminal_result is not None
            assert terminal_result.value == outcome


def test_stage_run_request_prompt_defaults_stay_metadata_driven() -> None:
    request = StageRunRequest(
        request_id="request-001",
        run_id="run-001",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
        mode_id="default_codex",
        compiled_plan_id="plan-001",
        entrypoint_path="entrypoints/execution/builder.md",
        active_work_item_kind="task",
        active_work_item_id="task-001",
        active_work_item_path="millrace-agents/tasks/active/task-001.md",
        run_dir="millrace-agents/runs/run-001",
        summary_status_path="millrace-agents/state/execution_status.md",
        runtime_snapshot_path="millrace-agents/state/runtime_snapshot.json",
        recovery_counters_path="millrace-agents/state/recovery_counters.json",
    )

    assert request.running_status_marker == running_status_marker(ExecutionStageName.BUILDER)
    assert request.legal_terminal_markers == legal_terminal_markers(ExecutionStageName.BUILDER)
    assert request.allowed_result_classes_by_outcome == allowed_result_classes_by_outcome(
        ExecutionStageName.BUILDER
    )


def _compiled_node_plan(
    *,
    stage_kind_id: str,
    allowed_work_item_families: tuple[str, ...],
) -> MaterializedGraphNodePlan:
    return MaterializedGraphNodePlan(
        node_id=stage_kind_id,
        stage_kind_id=stage_kind_id,
        runtime_stage=PlanningStageName(stage_kind_id),
        plane=Plane.PLANNING,
        entrypoint_path="entrypoints/planning/planner.md",
        running_status_marker="PLANNER_RUNNING",
        allowed_result_classes_by_outcome={"PLANNER_COMPLETE": (ResultClass.SUCCESS,)},
        allowed_work_item_families=allowed_work_item_families,
    )


def test_stage_work_item_ownership_uses_compiled_node_allowed_families() -> None:
    plan = _compiled_node_plan(
        stage_kind_id=PlanningStageName.MANAGER.value,
        allowed_work_item_families=("probe",),
    )

    validate_stage_work_item_ownership(
        plan,
        request_kind="active_work_item",
        active_work_item_kind=WorkItemKind.PROBE,
    )


def test_stage_work_item_ownership_rejects_families_missing_from_compiled_node() -> None:
    plan = _compiled_node_plan(
        stage_kind_id=PlanningStageName.MANAGER.value,
        allowed_work_item_families=("spec", "incident"),
    )

    with pytest.raises(StageWorkItemOwnershipError, match="requires work item family incident, spec"):
        validate_stage_work_item_ownership(
            plan,
            request_kind="active_work_item",
            active_work_item_kind=WorkItemKind.PROBE,
        )


def test_stage_work_item_ownership_skips_non_active_work_item_requests() -> None:
    plan = _compiled_node_plan(
        stage_kind_id=PlanningStageName.ARBITER.value,
        allowed_work_item_families=(),
    )

    validate_stage_work_item_ownership(
        plan,
        request_kind="closure_target",
        active_work_item_kind=None,
    )


def test_builtin_stage_kind_registry_assets_match_stage_metadata() -> None:
    stage_kinds = load_builtin_stage_kind_definitions()

    assert {stage_kind.stage_kind_id for stage_kind in stage_kinds} == set(
        STAGE_METADATA_BY_VALUE
    )
    for stage_kind in stage_kinds:
        metadata = stage_metadata(stage_kind.stage_kind_id)

        assert stage_kind.plane is metadata.plane
        assert stage_kind.running_status_marker == metadata.running_status_marker
        assert stage_kind.legal_outcomes == metadata.legal_terminal_results
        assert stage_kind.allowed_result_classes_by_outcome == dict(
            metadata.allowed_result_classes_by_outcome
        )


def test_unknown_or_wrong_plane_stage_lookup_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown stage value"):
        stage_name_for_value("fake_stage")

    with pytest.raises(ValueError, match="does not belong to plane"):
        stage_name_for_plane(Plane.PLANNING, ExecutionStageName.BUILDER.value)

    assert blocked_terminal_for_plane(Plane.LEARNING).value == "BLOCKED"
