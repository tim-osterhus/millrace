from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from millrace_ai.contracts import (
    CompileDiagnostics,
    ExecutionStageName,
    FrozenRunPlan,
    FrozenStagePlan,
    IncidentDocument,
    LoopConfigDefinition,
    MailboxCommandEnvelope,
    ModeDefinition,
    Plane,
    RecoveryCounters,
    RuntimeSnapshot,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
)

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def test_task_document_valid_minimal_payload() -> None:
    doc = TaskDocument(
        task_id="task-001",
        title="Implement contracts",
        target_paths=["millrace/contracts.py"],
        acceptance=["contracts validate"],
        required_checks=["pytest tests/test_contracts.py -q"],
        references=["lab/specs/drafts/millrace-typed-artifact-schemas.md"],
        risk=["schema drift"],
        created_at=NOW,
        created_by="tester",
    )

    assert doc.kind == "task"
    assert doc.schema_version == "1.0"


def test_task_document_rejects_empty_required_collections() -> None:
    with pytest.raises(ValidationError):
        TaskDocument(
            task_id="task-001",
            title="Implement contracts",
            target_paths=[],
            acceptance=["contracts validate"],
            required_checks=["pytest tests/test_contracts.py -q"],
            references=["lab/specs/drafts/millrace-typed-artifact-schemas.md"],
            risk=["schema drift"],
            created_at=NOW,
            created_by="tester",
        )


def test_spec_document_valid_minimal_payload() -> None:
    doc = SpecDocument(
        spec_id="spec-001",
        title="Contracts spec",
        summary="Define canonical runtime contracts",
        source_type="manual",
        goals=["define typed models"],
        constraints=["keep scope small"],
        acceptance=["tests pass"],
        references=["lab/specs/drafts/millrace-typed-artifact-schemas.md"],
        created_at=NOW,
        created_by="tester",
    )

    assert doc.kind == "spec"


def test_spec_document_rejects_empty_required_collections() -> None:
    with pytest.raises(ValidationError):
        SpecDocument(
            spec_id="spec-001",
            title="Contracts spec",
            summary="Define canonical runtime contracts",
            source_type="manual",
            goals=["define typed models"],
            constraints=[],
            acceptance=["tests pass"],
            references=["lab/specs/drafts/millrace-typed-artifact-schemas.md"],
            created_at=NOW,
            created_by="tester",
        )


def test_incident_document_rejects_stage_plane_mismatch() -> None:
    with pytest.raises(ValidationError):
        IncidentDocument(
            incident_id="inc-001",
            title="Mismatch incident",
            summary="stage and plane disagree",
            source_stage="builder",
            source_plane="planning",
            failure_class="illegal_state",
            trigger_reason="bad routing",
            consultant_decision="blocked",
            opened_at=NOW,
            opened_by="tester",
        )


def test_stage_result_envelope_valid_payload() -> None:
    env = StageResultEnvelope(
        run_id="run-001",
        plane="execution",
        stage="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BUILDER_COMPLETE",
        result_class="success",
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )

    assert env.kind == "stage_result"
    assert env.retryable is False


def test_stage_result_envelope_rejects_illegal_terminal_result_for_stage() -> None:
    with pytest.raises(ValidationError):
        StageResultEnvelope(
            run_id="run-001",
            plane="execution",
            stage="builder",
            work_item_kind="task",
            work_item_id="task-001",
            terminal_result="PLANNER_COMPLETE",
            result_class="success",
            summary_status_marker="### PLANNER_COMPLETE",
            success=True,
            started_at=NOW,
            completed_at=NOW,
        )


def test_stage_result_envelope_rejects_inconsistent_semantics() -> None:
    with pytest.raises(ValidationError):
        StageResultEnvelope(
            run_id="run-001",
            plane="execution",
            stage="builder",
            work_item_kind="task",
            work_item_id="task-001",
            terminal_result="BUILDER_COMPLETE",
            result_class="blocked",
            summary_status_marker="### BUILDER_COMPLETE",
            success=True,
            duration_seconds=-1.0,
            started_at=NOW,
            completed_at=NOW,
        )


def test_runtime_snapshot_rejects_active_stage_from_wrong_plane() -> None:
    with pytest.raises(ValidationError):
        RuntimeSnapshot(
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id="plan-001",
            compiled_plan_path="state/compiled_plan.json",
            active_plane="execution",
            active_stage="planner",
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="cfg-001",
            watcher_mode="watch",
            updated_at=NOW,
        )


def test_runtime_snapshot_rejects_active_work_item_without_stage() -> None:
    with pytest.raises(ValidationError):
        RuntimeSnapshot(
            runtime_mode="daemon",
            process_running=True,
            paused=False,
            active_mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            compiled_plan_id="plan-001",
            compiled_plan_path="state/compiled_plan.json",
            active_run_id="run-001",
            active_work_item_kind="task",
            active_work_item_id="task-001",
            execution_status_marker="### IDLE",
            planning_status_marker="### IDLE",
            config_version="cfg-001",
            watcher_mode="watch",
            updated_at=NOW,
        )


def test_recovery_counters_valid_payload() -> None:
    counters = RecoveryCounters(
        entries=[
            {
                "failure_class": "missing_terminal_result",
                "work_item_id": "task-001",
                "work_item_kind": "task",
                "last_updated_at": NOW,
            }
        ]
    )

    assert counters.kind == "recovery_counters"


def test_recovery_counters_reject_negative_counts() -> None:
    with pytest.raises(ValidationError):
        RecoveryCounters(
            entries=[
                {
                    "failure_class": "missing_terminal_result",
                    "work_item_id": "task-001",
                    "work_item_kind": "task",
                    "troubleshoot_attempt_count": -1,
                    "last_updated_at": NOW,
                }
            ]
        )


def test_mailbox_command_envelope_rejects_unknown_command() -> None:
    with pytest.raises(ValidationError):
        MailboxCommandEnvelope(
            command_id="cmd-001",
            command="nuke",
            issued_at=NOW,
            issuer="operator",
        )


def test_mailbox_command_envelope_rejects_dead_start_command() -> None:
    with pytest.raises(ValidationError):
        MailboxCommandEnvelope(
            command_id="cmd-002",
            command="start",
            issued_at=NOW,
            issuer="operator",
        )


def test_loop_config_definition_rejects_edge_with_unknown_target_stage() -> None:
    with pytest.raises(ValidationError):
        LoopConfigDefinition(
            loop_id="execution.standard",
            plane="execution",
            stages=["builder", "checker"],
            entry_stage="builder",
            edges=[
                {
                    "source_stage": "builder",
                    "on_terminal_result": "BUILDER_COMPLETE",
                    "target_stage": "planner",
                }
            ],
            terminal_results=["CHECKER_PASS"],
        )


def test_mode_definition_rejects_unknown_stage_key() -> None:
    with pytest.raises(ValidationError):
        ModeDefinition(
            mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            stage_entrypoint_overrides={"not_a_stage": "assets/foo.md"},
        )


def test_mode_definition_and_frozen_stage_plan_are_skill_only() -> None:
    mode = ModeDefinition(
        mode_id="standard_plain",
        execution_loop_id="execution.standard",
        planning_loop_id="planning.standard",
        stage_skill_additions={"builder": ("skills/execution/builder.md",)},
    )
    stage_plan = FrozenStagePlan(
        stage=ExecutionStageName.BUILDER,
        plane=Plane.EXECUTION,
        entrypoint_path="entrypoints/execution/builder.md",
        required_skills=("skills/README.md",),
        attached_skill_additions=("skills/execution/builder.md",),
    )

    assert "stage_role_overlays" not in mode.model_dump(mode="json")
    assert "role_overlays" not in stage_plan.model_dump(mode="json")


def test_frozen_run_plan_rejects_duplicate_stage_entries() -> None:
    with pytest.raises(ValidationError):
        FrozenRunPlan(
            compiled_plan_id="plan-001",
            mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            stage_plans=[
                {
                    "stage": "builder",
                    "plane": "execution",
                    "entrypoint_path": "assets/entrypoints/execution/builder.md",
                },
                {
                    "stage": "builder",
                    "plane": "execution",
                    "entrypoint_path": "assets/entrypoints/execution/builder-v2.md",
                },
            ],
            compiled_at=NOW,
        )


def test_compile_diagnostics_requires_errors_on_failure() -> None:
    with pytest.raises(ValidationError):
        CompileDiagnostics(
            ok=False,
            mode_id="standard_plain",
            errors=[],
            emitted_at=NOW,
        )
