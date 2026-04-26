from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from millrace_ai.contracts import (
    ClosureTargetState,
    CompileDiagnostics,
    CompletionBehaviorDefinition,
    IncidentDocument,
    LearningRequestDocument,
    LearningStageName,
    LearningTerminalResult,
    LoopConfigDefinition,
    MailboxCommandEnvelope,
    ModeDefinition,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
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
        required_checks=["pytest tests/runtime/test_contracts.py -q"],
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
            required_checks=["pytest tests/runtime/test_contracts.py -q"],
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


def test_learning_request_document_valid_payload() -> None:
    doc = LearningRequestDocument(
        learning_request_id="learn-001",
        title="Improve checker skill",
        summary="Use observed run evidence to improve checker guidance",
        requested_action="improve",
        target_skill_id="checker-core",
        target_stage="curator",
        source_refs=["run:run-001"],
        preferred_output_paths=["millrace-agents/skills/stage/execution/checker-core/SKILL.md"],
        trigger_metadata={"source_stage": "doublechecker", "terminal_result": "DOUBLECHECK_PASS"},
        originating_run_ids=["run-001"],
        artifact_paths=["millrace-agents/runs/run-001/stage_results/request-001.json"],
        created_at=NOW,
        created_by="tester",
    )

    assert doc.kind == "learning_request"
    assert doc.requested_action == "improve"
    assert doc.target_skill_id == "checker-core"
    assert doc.target_stage is LearningStageName.CURATOR


def test_learning_stage_result_envelope_valid_payload() -> None:
    env = StageResultEnvelope(
        run_id="run-001",
        plane="learning",
        stage="curator",
        work_item_kind="learning_request",
        work_item_id="learn-001",
        terminal_result="CURATOR_COMPLETE",
        result_class="success",
        summary_status_marker="### CURATOR_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )

    assert env.stage is LearningStageName.CURATOR
    assert env.terminal_result is LearningTerminalResult.CURATOR_COMPLETE


def test_work_documents_accept_root_lineage_fields() -> None:
    task = TaskDocument(
        task_id="task-001",
        title="Implement contracts",
        root_idea_id="idea-001",
        root_spec_id="spec-root-001",
        target_paths=["millrace/contracts.py"],
        acceptance=["contracts validate"],
        required_checks=["pytest tests/runtime/test_contracts.py -q"],
        references=["lab/specs/drafts/millrace-typed-artifact-schemas.md"],
        risk=["schema drift"],
        created_at=NOW,
        created_by="tester",
    )
    spec = SpecDocument(
        spec_id="spec-root-001",
        title="Contracts spec",
        summary="Define canonical runtime contracts",
        source_type="manual",
        root_idea_id="idea-001",
        root_spec_id="spec-root-001",
        goals=["define typed models"],
        constraints=["keep scope small"],
        acceptance=["tests pass"],
        references=["lab/specs/drafts/millrace-typed-artifact-schemas.md"],
        created_at=NOW,
        created_by="tester",
    )
    incident = IncidentDocument(
        incident_id="inc-001",
        title="Parity gap",
        summary="Closure needs remediation",
        root_idea_id="idea-001",
        root_spec_id="spec-root-001",
        source_stage="auditor",
        source_plane="planning",
        failure_class="arbiter_parity_gap",
        trigger_reason="parity gap found",
        consultant_decision="needs_planning",
        opened_at=NOW,
        opened_by="tester",
    )

    assert task.root_idea_id == "idea-001"
    assert task.root_spec_id == "spec-root-001"
    assert spec.root_spec_id == "spec-root-001"
    assert incident.root_spec_id == "spec-root-001"


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


def test_loop_config_definition_accepts_typed_completion_behavior_for_arbiter() -> None:
    loop = LoopConfigDefinition(
        loop_id="planning.standard",
        plane="planning",
        stages=["planner", "manager", "mechanic", "auditor", "arbiter"],
        entry_stage="planner",
        edges=[
            {
                "source_stage": "planner",
                "on_terminal_result": "PLANNER_COMPLETE",
                "target_stage": "manager",
            },
            {
                "source_stage": "manager",
                "on_terminal_result": "MANAGER_COMPLETE",
                "terminal_result": "MANAGER_COMPLETE",
                "edge_kind": "terminal",
            },
            {
                "source_stage": "arbiter",
                "on_terminal_result": "ARBITER_COMPLETE",
                "terminal_result": "ARBITER_COMPLETE",
                "edge_kind": "terminal",
            },
            {
                "source_stage": "arbiter",
                "on_terminal_result": "REMEDIATION_NEEDED",
                "terminal_result": "REMEDIATION_NEEDED",
                "edge_kind": "terminal",
            },
        ],
        terminal_results=["MANAGER_COMPLETE", "ARBITER_COMPLETE", "REMEDIATION_NEEDED", "BLOCKED"],
        completion_behavior={
            "trigger": "backlog_drained",
            "readiness_rule": "no_open_lineage_work",
            "stage": "arbiter",
            "request_kind": "closure_target",
            "target_selector": "active_closure_target",
            "rubric_policy": "reuse_or_create",
            "blocked_work_policy": "suppress",
            "skip_if_already_closed": True,
            "on_pass_terminal_result": "ARBITER_COMPLETE",
            "on_gap_terminal_result": "REMEDIATION_NEEDED",
            "create_incident_on_gap": True,
        },
    )

    assert isinstance(loop.completion_behavior, CompletionBehaviorDefinition)
    assert loop.completion_behavior.stage is PlanningStageName.ARBITER
    assert loop.completion_behavior.on_gap_terminal_result is PlanningTerminalResult.REMEDIATION_NEEDED


def test_mode_definition_rejects_unknown_stage_key() -> None:
    with pytest.raises(ValidationError):
        ModeDefinition(
            mode_id="standard_plain",
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            stage_entrypoint_overrides={"not_a_stage": "assets/foo.md"},
        )


def test_mode_definition_is_skill_only() -> None:
    mode = ModeDefinition(
        mode_id="standard_plain",
        loop_ids_by_plane={
            "execution": "execution.standard",
            "planning": "planning.standard",
        },
        stage_skill_additions={"builder": ("skills/execution/builder.md",)},
    )

    assert "stage_role_overlays" not in mode.model_dump(mode="json")
    assert "execution_loop_id" not in mode.model_dump(mode="json")
    assert mode.loop_ids_by_plane[Plane.EXECUTION] == "execution.standard"


def test_mode_definition_supports_learning_plane_bindings_and_triggers() -> None:
    mode = ModeDefinition(
        mode_id="learning_codex",
        loop_ids_by_plane={
            "execution": "execution.standard",
            "planning": "planning.standard",
            "learning": "learning.standard",
        },
        learning_trigger_rules=[
            {
                "rule_id": "execution.doublechecker.success-to-curator",
                "source_plane": "execution",
                "source_stage": "doublechecker",
                "on_terminal_results": ["DOUBLECHECK_PASS"],
                "target_stage": "curator",
                "requested_action": "improve",
            }
        ],
    )

    assert mode.learning_enabled is True
    assert mode.learning_loop_id == "learning.standard"
    assert mode.learning_trigger_rules[0].target_stage is LearningStageName.CURATOR


def test_stage_result_envelope_accepts_arbiter_remediation_needed() -> None:
    env = StageResultEnvelope(
        run_id="run-001",
        plane="planning",
        stage="arbiter",
        work_item_kind="spec",
        work_item_id="spec-root-001",
        terminal_result="REMEDIATION_NEEDED",
        result_class="followup_needed",
        summary_status_marker="### REMEDIATION_NEEDED",
        success=False,
        started_at=NOW,
        completed_at=NOW,
    )

    assert env.stage is PlanningStageName.ARBITER
    assert env.terminal_result is PlanningTerminalResult.REMEDIATION_NEEDED


def test_closure_target_state_valid_payload() -> None:
    target = ClosureTargetState(
        root_spec_id="spec-root-001",
        root_idea_id="idea-001",
        root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
        root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
        rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
        latest_verdict_path="millrace-agents/arbiter/verdicts/spec-root-001.json",
        latest_report_path="millrace-agents/arbiter/reports/run-001.md",
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=[],
        opened_at=NOW,
        last_arbiter_run_id="run-001",
    )

    assert target.root_spec_id == "spec-root-001"
    assert target.last_arbiter_run_id == "run-001"


def test_compile_diagnostics_requires_errors_on_failure() -> None:
    with pytest.raises(ValidationError):
        CompileDiagnostics(
            ok=False,
            mode_id="standard_plain",
            errors=[],
            emitted_at=NOW,
        )
