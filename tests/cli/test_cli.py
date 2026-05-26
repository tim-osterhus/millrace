from __future__ import annotations

import importlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from millrace_ai import cli
from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.cli.commands import skills as skills_commands
from millrace_ai.compiler import CompileOutcome, compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ActiveRunState,
    ApprovalPolicyRef,
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintPacketDocument,
    BlueprintPromotionRecord,
    CapabilityDecisionState,
    CapabilityEnforcementMode,
    CapabilityScope,
    ClosureTargetState,
    CompileDiagnostics,
    ExecutionCapabilityGrant,
    ExecutionStageName,
    LaneRuntimeState,
    LearningRequestDocument,
    LearningStageName,
    MailboxCommand,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    ReloadOutcome,
    ResultClass,
    RuntimeErrorCode,
    RuntimeErrorContext,
    RuntimeFailureOrigin,
    RuntimeMode,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    TokenUsage,
    WorkItemKind,
)
from millrace_ai.control import ControlActionResult
from millrace_ai.mailbox import read_pending_mailbox_commands
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.run_inspection import InspectedRunSummary, InspectedStageResult
from millrace_ai.runtime.approvals import ensure_execution_capability_approval
from millrace_ai.runtime.monitoring import RuntimeMonitorEvent
from millrace_ai.runtime.usage_governance import (
    SubscriptionQuotaStatus,
    UsageGovernanceBlocker,
    UsageGovernanceState,
    save_usage_governance_state,
)
from millrace_ai.runtime_lock import acquire_runtime_ownership_lock
from millrace_ai.state_store import load_snapshot, save_snapshot
from millrace_ai.workspace.arbiter_state import save_closure_target_state
from millrace_ai.workspace.blueprint_state import (
    claim_next_blueprint_draft,
    enqueue_blueprint_draft,
    persist_blueprint_critique,
    persist_blueprint_evaluation,
    persist_blueprint_packet,
    persist_blueprint_promotion,
)

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _copy_assets(tmp_path: Path) -> Path:
    source_assets = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    destination = tmp_path / "assets"
    shutil.copytree(source_assets, destination)
    return destination


def _write_runtime_effect_then_recovery_stage_results(paths, run_id: str) -> tuple[Path, Path]:
    stage_results_dir = paths.runs_dir / run_id / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    effect_result_path = stage_results_dir / "request-001.json"
    recovery_result_path = stage_results_dir / "request-002.json"
    effect_result = StageResultEnvelope(
        run_id=run_id,
        plane=Plane.PLANNING,
        stage=PlanningStageName.MANAGER,
        node_id="manager_blueprint",
        stage_kind_id="manager_blueprint",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-blueprint-001",
        terminal_result=PlanningTerminalResult.MANAGER_BLUEPRINT_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MANAGER_BLUEPRINT_COMPLETE",
        success=True,
        metadata={
            "runtime_effect_handler_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "blueprint_manifest_parse_error",
            "runtime_effect_failure_message": "blueprint_manifest.json is malformed",
            "runtime_effect_mutation_phase": "pre_mutation",
            "runtime_effect_failure_policy_id": "manager_blueprint_pre_mutation_artifact_repair",
            "runtime_effect_recovery_action": "route_to_node",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    recovery_result = StageResultEnvelope(
        run_id=run_id,
        plane=Plane.PLANNING,
        stage=PlanningStageName.MECHANIC,
        node_id="mechanic_blueprint",
        stage_kind_id="mechanic_blueprint",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-blueprint-001",
        terminal_result=PlanningTerminalResult.MECHANIC_BLUEPRINT_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MECHANIC_BLUEPRINT_COMPLETE",
        success=True,
        metadata={"recovery_result": "artifact repaired"},
        started_at=NOW,
        completed_at=datetime(2026, 4, 15, 12, 0, 1, tzinfo=timezone.utc),
    )
    effect_result_path.write_text(
        effect_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    recovery_result_path.write_text(
        recovery_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return effect_result_path, recovery_result_path


def _write_blueprint_repair_failure_then_apply_stage_results(
    paths,
    run_id: str,
) -> tuple[Path, Path]:
    stage_results_dir = paths.runs_dir / run_id / "stage_results"
    stage_results_dir.mkdir(parents=True, exist_ok=True)
    failure_result_path = stage_results_dir / "request-001.json"
    repair_result_path = stage_results_dir / "request-002.json"
    failure_result = StageResultEnvelope(
        run_id=run_id,
        plane=Plane.PLANNING,
        stage=PlanningStageName.MANAGER,
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        work_item_id="draft-blueprint-001",
        terminal_result=PlanningTerminalResult.BLUEPRINT_APPROVED,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        metadata={
            "runtime_effect_handler_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "generated_task_invalid",
            "runtime_effect_failure_message": "generated_task.md failed schema validation",
            "runtime_effect_mutation_phase": "pre_mutation",
            "runtime_effect_failure_policy_id": (
                "blueprint_approval_pre_mutation_effect_validation"
            ),
            "runtime_effect_recovery_action": "route_to_node",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    repair_result = StageResultEnvelope(
        run_id=run_id,
        plane=Plane.PLANNING,
        stage=PlanningStageName.MECHANIC,
        node_id="mechanic_blueprint",
        stage_kind_id="mechanic_blueprint",
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        work_item_id="draft-blueprint-001",
        terminal_result=PlanningTerminalResult.MECHANIC_BLUEPRINT_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MECHANIC_BLUEPRINT_COMPLETE",
        success=True,
        metadata={
            "runtime_effect_handler_id": "mechanic_blueprint_repair_apply",
            "runtime_effect_decision": "request_complete_source",
            "runtime_effect_failure_message": "promoted blueprint to repaired task",
            "runtime_effect_mutation_phase": "unknown",
        },
        started_at=NOW,
        completed_at=datetime(2026, 4, 15, 12, 0, 1, tzinfo=timezone.utc),
    )
    failure_result_path.write_text(
        failure_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    repair_result_path.write_text(
        repair_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return failure_result_path, repair_result_path


def test_init_command_creates_workspace_baseline(tmp_path: Path) -> None:
    root = tmp_path / "workspace"

    runner = CliRunner()
    result = runner.invoke(cli.app, ["init", "--workspace", str(root)])

    paths = workspace_paths(root)

    assert result.exit_code == 0
    assert "workspace:" in result.output
    assert "initialized: true" in result.output
    assert paths.runtime_root.is_dir()
    assert paths.runtime_root.joinpath("millrace.toml").is_file()
    assert paths.runtime_snapshot_file.is_file()


@pytest.mark.parametrize(
    ("argv"),
    [
        ["run", "daemon"],
        ["compile", "validate"],
        ["queue", "ls"],
        ["status"],
        ["runs", "ls"],
        ["control", "pause"],
        ["skills", "ls"],
        ["doctor"],
    ],
)
def test_operational_commands_refuse_uninitialized_workspace(
    tmp_path: Path,
    argv: list[str],
) -> None:
    root = tmp_path / "workspace"

    runner = CliRunner()
    result = runner.invoke(cli.app, [*argv, "--workspace", str(root)])

    assert result.exit_code == 1
    assert "error: workspace is not initialized" in result.output
    assert "millrace init --workspace" in result.output
    assert not (root / "millrace-agents").exists()


def test_cli_import_surface_moves_to_package_directory() -> None:
    assert Path(cli.__file__).as_posix().endswith("/cli/__init__.py")


def test_cli_package_exposes_split_command_modules() -> None:
    run_module = importlib.import_module("millrace_ai.cli.commands.run")
    app_module = importlib.import_module("millrace_ai.cli.app")
    skills_module = importlib.import_module("millrace_ai.cli.commands.skills")

    assert not hasattr(run_module, "run_once")
    assert hasattr(run_module, "run_daemon")
    assert hasattr(app_module, "app")
    assert hasattr(skills_module, "skills_app")


def test_cli_package_consumes_public_runtime_control_facade() -> None:
    control_module = importlib.import_module("millrace_ai.control")

    assert cli.RuntimeControl is control_module.RuntimeControl
    assert cli.ControlActionResult is control_module.ControlActionResult
    assert cli.RuntimeControl.__module__ == "millrace_ai.runtime.control"


def test_cli_version_surfaces_package_version() -> None:
    top_level = CliRunner().invoke(cli.app, ["--version"])
    command = CliRunner().invoke(cli.app, ["version"])

    assert top_level.exit_code == 0
    assert command.exit_code == 0
    assert "millrace" in top_level.output
    assert cli.__version__ in top_level.output
    assert cli.__version__ in command.output


def _task_payload(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "summary": "cli task",
        "target_paths": ["src/millrace_ai/runtime.py"],
        "acceptance": ["runtime loop runs"],
        "required_checks": ["uv run pytest tests/cli/test_cli.py -q"],
        "references": ["lab/specs/drafts/millrace-mvp-implementation-slice.md"],
        "risk": ["none"],
        "created_at": NOW.isoformat(),
        "created_by": "tests",
    }


def _spec_payload(spec_id: str) -> dict[str, object]:
    return {
        "spec_id": spec_id,
        "title": f"Spec {spec_id}",
        "summary": "cli spec",
        "source_type": "manual",
        "goals": ["ship CLI"],
        "constraints": ["MVP surface only"],
        "acceptance": ["command set works"],
        "references": ["lab/specs/drafts/millrace-runtime-module-and-cli-plan.md"],
        "created_at": NOW.isoformat(),
        "created_by": "tests",
    }


def _probe_payload(probe_id: str) -> dict[str, object]:
    return {
        "probe_id": probe_id,
        "title": f"Probe {probe_id}",
        "summary": "cli probe",
        "request": "Research the codebase and route the smallest safe change.",
        "target_paths": ["src/millrace_ai/runtime.py"],
        "constraints": ["Do not implement during recon."],
        "acceptance": ["recon routes the probe"],
        "risk_notes": ["ambiguous codebase request"],
        "references": ["operator request"],
        "created_at": NOW.isoformat(),
        "created_by": "tests",
    }


def _pending_commands(paths) -> set[MailboxCommand]:
    return {envelope.command for envelope in read_pending_mailbox_commands(paths)}


def _approval_grant() -> ExecutionCapabilityGrant:
    return ExecutionCapabilityGrant(
        grant_id="grant-package-install",
        request_id="request-package-install",
        capability_id="package.install",
        access="execute",
        scope=CapabilityScope(kind="package_manager", value="uv"),
        decision_state=CapabilityDecisionState.APPROVAL_REQUIRED,
        enforcement_mode=CapabilityEnforcementMode.NOT_APPLICABLE,
        approval_policy_ref=ApprovalPolicyRef(policy_id="operator", gate_scope="stage"),
        decision_reason="package install requires operator approval",
        resolved_by="runtime_config",
    )


def _inspected_run_summary(
    run_id: str = "run-001",
    *,
    run_dir: str | None = None,
    status: str = "valid",
    artifact_status: str = "valid",
    runtime_outcome: str = "complete",
    failure_class: str | None = None,
    report_artifact: str | None = "troubleshoot_report.md",
    compiled_plan_id: str | None = "plan-001",
    mode_id: str | None = "default_codex",
    request_kind: str | None = None,
    closure_target_root_spec_id: str | None = None,
    closure_target_root_source_kind: str | None = None,
    closure_target_root_source_id: str | None = None,
    closure_target_root_source_path: str | None = None,
    failure_origin: str | None = None,
    request_context_profile_id: str | None = None,
    context_bundle_path: str | None = None,
    context_artifact_refs: tuple[str, ...] = (),
    context_render_plan_id: str | None = None,
    rendered_prompt_context_path: str | None = None,
    thinking_level: str | None = None,
    model_reasoning_effort: str | None = None,
    capability_grant_summaries: tuple[str, ...] = (),
    capability_support_summaries: tuple[str, ...] = (),
    runtime_effect_handler_id: str | None = None,
    runtime_effect_decision: str | None = None,
    runtime_effect_failure_class: str | None = None,
    runtime_effect_failure_message: str | None = None,
    runtime_effect_mutation_phase: str | None = None,
    runtime_effect_created_paths: tuple[str, ...] = (),
    runtime_effect_source_lifecycle_plan_id: str | None = None,
    runtime_effect_source_lifecycle_action: str | None = None,
    runtime_effect_failure_policy_id: str | None = None,
    runtime_effect_recovery_action: str | None = None,
) -> InspectedRunSummary:
    artifact_paths = tuple(path for path in (report_artifact, "runner_stdout.txt") if path is not None)
    stage_result = InspectedStageResult(
        stage_result_path="stage_results/request-001.json",
        request_id="request-001",
        compiled_plan_id=compiled_plan_id,
        mode_id=mode_id,
        stage="checker",
        node_id="execution.checker.primary",
        stage_kind_id="checker",
        request_kind=request_kind,
        closure_target_root_spec_id=closure_target_root_spec_id,
        closure_target_root_source_kind=closure_target_root_source_kind,
        closure_target_root_source_id=closure_target_root_source_id,
        closure_target_root_source_path=closure_target_root_source_path,
        terminal_result="CHECKER_PASS",
        result_class="success",
        work_item_kind="task",
        work_item_id="task-001",
        failure_class=failure_class,
        failure_origin=failure_origin,
        request_context_profile_id=request_context_profile_id,
        context_bundle_path=context_bundle_path,
        context_artifact_refs=context_artifact_refs,
        context_render_plan_id=context_render_plan_id,
        rendered_prompt_context_path=rendered_prompt_context_path,
        stdout_path="runner_stdout.txt",
        stderr_path="runner_stderr.txt",
        report_artifact=report_artifact,
        artifact_paths=artifact_paths,
        runner_name="codex-cli",
        model_name="gpt-5.4",
        thinking_level=thinking_level,
        model_reasoning_effort=model_reasoning_effort,
        capability_grant_summaries=capability_grant_summaries,
        capability_support_summaries=capability_support_summaries,
        runtime_effect_handler_id=runtime_effect_handler_id,
        runtime_effect_decision=runtime_effect_decision,
        runtime_effect_failure_class=runtime_effect_failure_class,
        runtime_effect_failure_message=runtime_effect_failure_message,
        runtime_effect_mutation_phase=runtime_effect_mutation_phase,
        runtime_effect_created_paths=runtime_effect_created_paths,
        runtime_effect_source_lifecycle_plan_id=runtime_effect_source_lifecycle_plan_id,
        runtime_effect_source_lifecycle_action=runtime_effect_source_lifecycle_action,
        runtime_effect_failure_policy_id=runtime_effect_failure_policy_id,
        runtime_effect_recovery_action=runtime_effect_recovery_action,
        started_at=NOW.isoformat(),
        completed_at=NOW.isoformat(),
        duration_seconds=3.0,
        token_usage=TokenUsage(
            input_tokens=100,
            cached_input_tokens=30,
            output_tokens=12,
            thinking_tokens=5,
            total_tokens=112,
        ),
    )
    return InspectedRunSummary(
        run_id=run_id,
        run_dir=run_dir or f"/tmp/{run_id}",
        status=status,
        artifact_status=artifact_status,
        runtime_outcome=runtime_outcome,
        compiled_plan_id=compiled_plan_id,
        mode_id=mode_id,
        request_kind=request_kind,
        closure_target_root_spec_id=closure_target_root_spec_id,
        closure_target_root_source_kind=closure_target_root_source_kind,
        closure_target_root_source_id=closure_target_root_source_id,
        closure_target_root_source_path=closure_target_root_source_path,
        work_item_kind="task",
        work_item_id="task-001",
        failure_class=failure_class,
        failure_origin=failure_origin,
        runtime_effect_handler_id=runtime_effect_handler_id,
        runtime_effect_decision=runtime_effect_decision,
        runtime_effect_failure_class=runtime_effect_failure_class,
        runtime_effect_failure_message=runtime_effect_failure_message,
        runtime_effect_mutation_phase=runtime_effect_mutation_phase,
        runtime_effect_failure_policy_id=runtime_effect_failure_policy_id,
        runtime_effect_recovery_action=runtime_effect_recovery_action,
        troubleshoot_report_path=report_artifact,
        primary_stdout_path="runner_stdout.txt",
        primary_stderr_path="runner_stderr.txt",
        stage_results=(stage_result,),
        notes=(),
        started_at=NOW.isoformat(),
        completed_at=NOW.isoformat(),
        duration_seconds=3.0,
        token_usage=TokenUsage(
            input_tokens=100,
            cached_input_tokens=30,
            output_tokens=12,
            thinking_tokens=5,
            total_tokens=112,
        ),
    )


def _blueprint_draft_doc(
    draft_id: str = "draft-blueprint-001",
    *,
    latest_blueprint_id: str | None = None,
    latest_critique_id: str | None = None,
) -> BlueprintDraftDocument:
    return BlueprintDraftDocument(
        draft_id=draft_id,
        manifest_id="manifest-blueprint-001",
        root_spec_id="spec-blueprint-001",
        root_idea_id="idea-blueprint-001",
        source_spec_id="spec-blueprint-001",
        draft_index=1,
        title="Blueprint Draft 001",
        summary="Blueprint operator surface fixture.",
        scope=("src/millrace_ai/runtime/inspection.py",),
        target_paths=("src/millrace_ai/runtime/inspection.py",),
        acceptance_intent=("Status surfaces Blueprint draft state.",),
        verification_intent=("pytest tests/cli/test_cli.py -q",),
        integration_boundary_notes=("Fixture for operator status output.",),
        context_excerpt="Blueprint status fixture.",
        current_revision=1 if latest_blueprint_id else 0,
        latest_blueprint_id=latest_blueprint_id,
        latest_critique_id=latest_critique_id,
        references=("tests/cli/test_cli.py",),
        created_at=NOW,
    )


def _custom_planning_family() -> WorkItemFamilyDefinition:
    return WorkItemFamilyDefinition(
        family_id="custom_review",
        plane=Plane.PLANNING,
        entry_key="custom_review",
        display_name="Custom Review",
        document_kind="custom_review",
        runtime_relative_dir="custom/reviews",
        file_extension=".json",
        schema_id="custom_review_document_v1",
        document_adapter_id="custom_review_json_v1",
        queue_dirs={
            "queue": "custom/reviews/queue",
            "active": "custom/reviews/active",
            "done": "custom/reviews/done",
            "blocked": "custom/reviews/blocked",
            "canceled": "custom/reviews/canceled",
        },
        lifecycle_states=("queue", "active", "done", "blocked", "canceled"),
        claimable_state="queue",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        canceled_state="canceled",
        closure_blocking_states=("queue", "active", "blocked"),
        default_entry_key="custom_review",
        id_field="custom_id",
        created_at_field="created_at",
        lineage_fields=("root_spec_id",),
        operator_capabilities=("cancel", "inspect"),
    )


def _custom_learning_family() -> WorkItemFamilyDefinition:
    return WorkItemFamilyDefinition(
        family_id="custom_learning",
        plane=Plane.LEARNING,
        entry_key="custom_learning",
        display_name="Custom Learning",
        document_kind="custom_learning",
        runtime_relative_dir="custom/learning",
        file_extension=".json",
        schema_id="custom_learning_document_v1",
        document_adapter_id="custom_learning_json_v1",
        queue_dirs={
            "queue": "custom/learning/queue",
            "active": "custom/learning/active",
            "done": "custom/learning/done",
            "blocked": "custom/learning/blocked",
            "canceled": "custom/learning/canceled",
        },
        lifecycle_states=("queue", "active", "done", "blocked", "canceled"),
        claimable_state="queue",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        canceled_state="canceled",
        closure_blocking_states=("queue", "active", "blocked"),
        default_entry_key="custom_learning",
        id_field="custom_id",
        created_at_field="created_at",
        lineage_fields=("root_spec_id",),
        operator_capabilities=("cancel", "inspect"),
    )


def _persist_custom_family(paths, family: WorkItemFamilyDefinition) -> None:
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None
    updated = outcome.active_plan.model_copy(
        update={
            "work_item_families_by_id": {
                **outcome.active_plan.work_item_families_by_id,
                family.family_id: family,
            }
        }
    )
    (paths.state_dir / "compiled_plan.json").write_text(
        updated.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _blueprint_packet_doc() -> BlueprintPacketDocument:
    return BlueprintPacketDocument(
        blueprint_id="blueprint-draft-blueprint-001-r1",
        draft_id="draft-blueprint-001",
        manifest_id="manifest-blueprint-001",
        root_spec_id="spec-blueprint-001",
        root_idea_id="idea-blueprint-001",
        revision=1,
        title="Blueprint Packet 001",
        implementation_scope=("Surface Blueprint status.",),
        intended_files=("src/millrace_ai/cli/status_view.py",),
        design_decisions=("Keep CLI output compact.",),
        verification_plan=("pytest tests/cli/test_cli.py -q",),
        task_acceptance=("Blueprint status output includes packet references.",),
        required_checks=("pytest tests/cli/test_cli.py -q",),
        risk_notes=("Operator visibility can drift.",),
        references=("tests/cli/test_cli.py",),
        created_at=NOW,
    )


def _blueprint_evaluation_doc() -> BlueprintEvaluationDocument:
    return BlueprintEvaluationDocument(
        evaluation_id="evaluation-blueprint-001",
        blueprint_id="blueprint-draft-blueprint-001-r1",
        draft_id="draft-blueprint-001",
        manifest_id="manifest-blueprint-001",
        root_spec_id="spec-blueprint-001",
        root_idea_id="idea-blueprint-001",
        decision="approved",
        rubric_findings=("Blueprint is ready.",),
        required_task_fields=("task_id", "target_paths"),
        references=("tests/cli/test_cli.py",),
        created_at=NOW,
    )


def _blueprint_critique_doc() -> BlueprintCritiqueDocument:
    return BlueprintCritiqueDocument(
        critique_id="critique-blueprint-001",
        evaluation_id="evaluation-blueprint-001",
        blueprint_id="blueprint-draft-blueprint-001-r1",
        draft_id="draft-blueprint-001",
        manifest_id="manifest-blueprint-001",
        root_spec_id="spec-blueprint-001",
        root_idea_id="idea-blueprint-001",
        revision=1,
        required_changes=("Tighten status output.",),
        blocking_reason="Status output needs more context.",
        references=("tests/cli/test_cli.py",),
        created_at=NOW,
    )


def _blueprint_promotion_doc() -> BlueprintPromotionRecord:
    return BlueprintPromotionRecord(
        promotion_id="promotion-evaluation-blueprint-001",
        blueprint_id="blueprint-draft-blueprint-001-r1",
        evaluation_id="evaluation-blueprint-001",
        draft_id="draft-blueprint-001",
        manifest_id="manifest-blueprint-001",
        root_spec_id="spec-blueprint-001",
        root_idea_id="idea-blueprint-001",
        generated_task_id="task-blueprint-001",
        generated_task_path="millrace-agents/tasks/queue/task-blueprint-001.md",
        approved_blueprint_path=(
            "millrace-agents/blueprints/packets/approved/"
            "blueprint-draft-blueprint-001-r1.json"
        ),
        evaluation_path="millrace-agents/blueprints/evaluations/evaluation-blueprint-001.json",
        promoted_at=NOW,
    )


def test_run_once_command_is_not_exposed(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "once", "--workspace", str(paths.root)],
    )

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_run_daemon_respects_max_ticks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    calls: dict[str, object] = {"tick": 0, "stage_runner": None}
    sentinel_runner = object()

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, config_path, mode_id, assets_root, monitor
            calls["stage_runner"] = stage_runner
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=True)

        def startup(self):
            return SimpleNamespace(
                active_mode_id="standard_plain",
                compiled_plan_id="plan-001",
            )

        def tick(self):
            calls["tick"] += 1
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    monkeypatch.setattr(cli, "_build_stage_runner", lambda **kwargs: sentinel_runner)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "3"],
    )

    assert result.exit_code == 0
    assert calls["tick"] == 3
    assert calls["stage_runner"] is sentinel_runner
    assert "run_mode: daemon" in result.output
    assert "ticks: 3" in result.output


def test_run_daemon_max_ticks_one_is_supported_one_off_surface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    calls: dict[str, object] = {"tick": 0}

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, stage_runner, config_path, mode_id, assets_root, monitor
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=True)

        def startup(self):
            return SimpleNamespace(
                active_mode_id="standard_plain",
                compiled_plan_id="plan-001",
            )

        def tick(self):
            calls["tick"] = int(calls["tick"]) + 1
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "1"],
    )

    assert result.exit_code == 0
    assert calls["tick"] == 1
    assert "run_mode: daemon" in result.output
    assert "ticks: 1" in result.output


def test_run_daemon_with_monitor_basic_installs_monitor_and_prints_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, stage_runner, config_path, mode_id, assets_root
            self.monitor = monitor
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=False)

        def startup(self):
            assert self.monitor is not None
            self.monitor.emit(
                RuntimeMonitorEvent(
                    event_type="runtime_started",
                    occurred_at=NOW,
                    payload={
                        "mode_id": "standard_plain",
                        "compiled_plan_id": "plan-001",
                        "compiled_plan_currentness": "current",
                        "baseline_manifest_id": "baseline-001",
                        "baseline_seed_package_version": "0.15.5",
                        "loop_ids_by_plane": {
                            "execution": "execution.standard",
                            "planning": "planning.standard",
                        },
                        "concurrency_policy": None,
                        "status_markers_by_plane": {
                            "execution": "### IDLE",
                            "planning": "### IDLE",
                            "learning": "### IDLE",
                        },
                        "queue_depths_by_plane": {
                            "execution": 0,
                            "planning": 0,
                            "learning": 0,
                        },
                    },
                )
            )
            return SimpleNamespace(active_mode_id="standard_plain", compiled_plan_id="plan-001")

        def tick(self):
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    result = CliRunner().invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--monitor", "basic", "--max-ticks", "1"],
    )

    assert result.exit_code == 0
    assert "runtime started mode=standard_plain" in result.output


def test_run_daemon_without_monitor_stays_quiet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, stage_runner, config_path, mode_id, assets_root
            self.monitor = monitor
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=False)

        def startup(self):
            assert self.monitor is not None
            self.monitor.emit(
                RuntimeMonitorEvent(
                    event_type="runtime_started",
                    occurred_at=NOW,
                    payload={"mode_id": "standard_plain", "compiled_plan_id": "plan-001"},
                )
            )
            return SimpleNamespace(active_mode_id="standard_plain", compiled_plan_id="plan-001")

        def tick(self):
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    result = CliRunner().invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "1"],
    )

    assert result.exit_code == 0
    assert "runtime started" not in result.output


def test_run_daemon_can_write_basic_monitor_log_without_stdout_monitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    monitor_log = tmp_path / "monitor.log"

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, stage_runner, config_path, mode_id, assets_root
            self.monitor = monitor
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=False)

        def startup(self):
            assert self.monitor is not None
            self.monitor.emit(
                RuntimeMonitorEvent(
                    event_type="runtime_started",
                    occurred_at=NOW,
                    payload={
                        "mode_id": "standard_plain",
                        "compiled_plan_id": "plan-001",
                        "compiled_plan_currentness": "current",
                        "baseline_manifest_id": "baseline-001",
                        "baseline_seed_package_version": "0.15.7",
                    },
                )
            )
            return SimpleNamespace(active_mode_id="standard_plain", compiled_plan_id="plan-001")

        def tick(self):
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "daemon",
            "--workspace",
            str(paths.root),
            "--monitor-log",
            str(monitor_log),
            "--max-ticks",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "runtime started mode=standard_plain" not in result.output
    assert "runtime started mode=standard_plain" in monitor_log.read_text(encoding="utf-8")


def test_run_daemon_monitor_records_unexpected_tick_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, stage_runner, config_path, mode_id, assets_root
            self.monitor = monitor
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=True)

        def startup(self):
            return SimpleNamespace(active_mode_id="standard_plain", compiled_plan_id="plan-001")

        def tick(self):
            raise RuntimeError("tick exploded")

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    result = CliRunner().invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--monitor", "basic", "--max-ticks", "1"],
    )

    assert result.exit_code == 1
    assert "unexpected daemon exit phase=tick exception=RuntimeError error=tick exploded" in result.output


def test_skills_install_copies_local_skill_and_updates_workspace_index(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    source_skill = tmp_path / "source-skill"
    source_skill.mkdir()
    source_skill.joinpath("SKILL.md").write_text(
        "---\nname: source-skill\ndescription: A test skill\n---\n# Source Skill\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["skills", "install", str(source_skill), "--workspace", str(paths.root)],
    )

    installed_skill = paths.skills_dir / "source-skill" / "SKILL.md"
    index_text = paths.skills_dir.joinpath("skills_index.md").read_text(encoding="utf-8")

    assert result.exit_code == 0
    assert installed_skill.is_file()
    assert "installed_skill: source-skill" in result.output
    assert "- source-skill: source-skill/SKILL.md" in index_text


def test_skills_install_refuses_existing_skill_without_force(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    source_skill = tmp_path / "source-skill"
    source_skill.mkdir()
    source_skill.joinpath("SKILL.md").write_text("# Source Skill\n", encoding="utf-8")

    runner = CliRunner()
    first = runner.invoke(
        cli.app,
        ["skills", "install", str(source_skill), "--workspace", str(paths.root)],
    )
    second = runner.invoke(
        cli.app,
        ["skills", "install", str(source_skill), "--workspace", str(paths.root)],
    )

    assert first.exit_code == 0
    assert second.exit_code == 1
    assert "skill already exists" in second.output


def test_skills_install_resolves_remote_skill_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    def fake_install_remote_skill(*args, **kwargs):
        skills_dir, skill_ref = args
        assert skills_dir == paths.skills_dir
        assert skill_ref == "browser-local-qa"
        assert kwargs["force"] is False
        assert kwargs["update"] is False
        skill_dir = paths.skills_dir / skill_ref
        skill_dir.mkdir()
        skill_dir.joinpath("SKILL.md").write_text("# Browser Local QA\n", encoding="utf-8")
        return SimpleNamespace(
            skill_id=skill_ref,
            destination=skill_dir,
            installed_files=("SKILL.md",),
            source_index_url="https://raw.githubusercontent.com/tim-osterhus/millrace-skills/main/index.md",
        )

    monkeypatch.setattr(skills_commands, "install_remote_skill", fake_install_remote_skill)

    result = CliRunner().invoke(
        cli.app,
        ["skills", "install", "browser-local-qa", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert "installed_skill: browser-local-qa" in result.output
    assert "source: remote" in result.output


def test_skills_refresh_remote_index_writes_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    def fake_refresh_remote_skill_index(skills_dir):
        assert skills_dir == paths.skills_dir
        destination = skills_dir / "remote_skills_index.md"
        destination.write_text("# Remote Skills\n", encoding="utf-8")
        return destination

    monkeypatch.setattr(
        skills_commands,
        "refresh_remote_skill_index",
        fake_refresh_remote_skill_index,
    )

    result = CliRunner().invoke(
        cli.app,
        ["skills", "refresh-remote-index", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert "remote_skills_index:" in result.output
    assert "remote_skills_index.md" in result.output


def test_skills_create_refuses_when_learning_plane_is_not_enabled(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["skills", "create", "write a checker skill", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 1
    assert "current mode does not enable the learning plane" in result.output
    assert not any(paths.learning_requests_queue_dir.glob("*.md"))


def test_run_daemon_sleeps_between_ticks_when_unbounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    calls: dict[str, object] = {"tick": 0, "sleep": 0}
    sentinel_runner = object()

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, config_path, mode_id, assets_root, stage_runner, monitor
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=True)

        def startup(self):
            return SimpleNamespace(
                active_mode_id="standard_plain",
                compiled_plan_id="plan-001",
            )

        def tick(self):
            calls["tick"] = int(calls["tick"]) + 1
            if int(calls["tick"]) >= 2:
                self.snapshot.stop_requested = True
                self.snapshot.process_running = False
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    monkeypatch.setattr(cli, "_build_stage_runner", lambda **kwargs: sentinel_runner)
    monkeypatch.setattr(cli.time, "sleep", lambda _: calls.__setitem__("sleep", int(calls["sleep"]) + 1))

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert calls["tick"] == 2
    assert calls["sleep"] == 1


def test_run_daemon_fails_fast_when_workspace_daemon_lock_is_held(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="cli-lock-holder",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "1"],
    )

    assert result.exit_code == 1
    assert "error:" in result.output
    assert "workspace runtime ownership lock" in result.output


def test_run_daemon_rejects_legacy_run_once_config(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "1"],
    )

    assert result.exit_code == 1
    assert "error:" in result.output
    assert "run_style" in result.output
    assert "daemon" in result.output


def test_run_daemon_fails_fast_on_unknown_configured_stage_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[stages.builder]",
                'runner = "does_not_exist"',
            ]
        ),
        encoding="utf-8",
    )

    class FakeRuntimeEngine:
        def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - should not run
            raise AssertionError("RuntimeEngine should not be constructed")

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "1"],
    )

    assert result.exit_code == 1
    assert "Unknown configured stage runner" in result.output


def test_status_surfaces_active_mode_and_compiled_plan_id(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths).model_copy(
        update={
            "active_mode_id": "default_codex",
            "compiled_plan_id": "plan-status-123",
            "compiled_plan_fingerprint": "fingerprint-status-123",
            "pending_compiled_plan_id": "plan-pending-456",
            "pending_compiled_plan_fingerprint": "fingerprint-pending-456",
            "pending_compiled_plan_path": "millrace-agents/state/compiled_plans/plan-pending-456.json",
            "queue_depth_execution": 4,
            "queue_depth_planning": 2,
            "lanes_by_id": {
                "execution.main": LaneRuntimeState(
                    lane_id="execution.main",
                    plane=Plane.EXECUTION,
                    status="idle",
                    compiled_plan_id="plan-status-123",
                    compiled_plan_fingerprint="fingerprint-status-123",
                    last_terminal_outcome="CHECKER_PASS",
                )
            },
        }
    )
    save_snapshot(paths, snapshot)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "active_mode_id: default_codex" in result.output
    assert "compiled_plan_id: plan-status-123" in result.output
    assert "compiled_plan_fingerprint: fingerprint-status-123" in result.output
    assert "pending_compiled_plan_id: plan-pending-456" in result.output
    assert "pending_compiled_plan_fingerprint: fingerprint-pending-456" in result.output
    assert (
        "pending_compiled_plan_path: millrace-agents/state/compiled_plans/plan-pending-456.json"
    ) in result.output
    assert (
        "lane: id=execution.main plane=execution status=idle plan=plan-status-123 "
        "fingerprint=fingerprint-status-123 active_runs=none active_work=none "
        "last_terminal=CHECKER_PASS"
    ) in result.output


def test_status_surfaces_baseline_manifest_identity_and_compile_currentness(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    cli.compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=paths.runtime_root,
    )

    runner = CliRunner()
    current = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert current.exit_code == 0
    assert "baseline_manifest_id:" in current.output
    assert "compiled_plan_currentness: current" in current.output

    (paths.runtime_root / "entrypoints" / "execution" / "builder.md").write_text(
        "stale builder override\n",
        encoding="utf-8",
    )
    stale = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert stale.exit_code == 0
    assert "compiled_plan_currentness: stale" in stale.output


def test_status_surfaces_learning_plane_depth_and_status(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="learn-001",
            title="Learn from checker",
            requested_action="improve",
            created_at=NOW,
            created_by="tests",
        )
    )
    snapshot = load_snapshot(paths).model_copy(
        update={
            "active_mode_id": "learning_codex",
            "learning_loop_id": "learning.standard",
            "learning_status_marker": "### ANALYST_COMPLETE",
        }
    )
    save_snapshot(paths, snapshot)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "learning_queue_depth: 1" in result.output
    assert "learning_status_marker: ### ANALYST_COMPLETE" in result.output


def test_status_surfaces_latest_operator_intervention(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(TaskDocument.model_validate(_task_payload("task-status-cancel")))
    cli.RuntimeControl(paths).cancel_work_item(
        work_item_id="task-status-cancel",
        work_item_kind=WorkItemKind.TASK,
        reason="operator cancelled bad intake",
    )

    result = CliRunner().invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "latest_operator_intervention: event=work_item_cancelled" in result.output
    assert "work_item_id=task-status-cancel" in result.output


def test_status_surfaces_multiple_active_runs_by_plane(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths).model_copy(
        update={
            "active_runs_by_plane": {
                Plane.EXECUTION: ActiveRunState(
                    plane=Plane.EXECUTION,
                    lane_id="execution.main",
                    stage=ExecutionStageName.BUILDER,
                    node_id="builder",
                    stage_kind_id="builder",
                    run_id="run-abcdef0123456789",
                    compiled_plan_id="bootstrap",
                    compiled_plan_fingerprint="bootstrap",
                    request_kind="active_work_item",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    active_since=NOW,
                ),
                Plane.LEARNING: ActiveRunState(
                    plane=Plane.LEARNING,
                    lane_id="learning.main",
                    stage=LearningStageName.CURATOR,
                    node_id="curator",
                    stage_kind_id="curator",
                    run_id="run-1234567890abcdef",
                    compiled_plan_id="bootstrap",
                    compiled_plan_fingerprint="bootstrap",
                    request_kind="learning_request",
                    work_item_kind=WorkItemKind.LEARNING_REQUEST,
                    work_item_id="learn-001",
                    active_since=NOW,
                ),
            },
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, snapshot)

    result = CliRunner().invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "active_run_count: 2" in result.output
    assert (
        "active_run: plane=execution stage=builder node=builder stage_kind=builder "
        "lane=execution.main launch_plan=bootstrap launch_fingerprint=bootstrap "
        "request_kind=active_work_item work_item_kind=task work_item_id=task-001 "
        "run=abcdef012345"
    ) in result.output
    assert (
        "active_run: plane=learning stage=curator node=curator stage_kind=curator "
        "lane=learning.main launch_plan=bootstrap launch_fingerprint=bootstrap "
        "request_kind=learning_request work_item_kind=learning_request work_item_id=learn-001 "
        "run=1234567890ab"
    ) in result.output


def test_status_surfaces_usage_governance_pause_context(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    (paths.runtime_root / "millrace.toml").write_text(
        "\n".join(
            [
                "[runtime]",
                'default_mode = "default_codex"',
                "",
                "[usage_governance]",
                "enabled = true",
            ]
        ),
        encoding="utf-8",
    )
    snapshot = load_snapshot(paths).model_copy(
        update={
            "paused": True,
            "pause_sources": ("usage_governance",),
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, snapshot)
    save_usage_governance_state(
        paths,
        UsageGovernanceState(
            enabled=True,
            auto_resume=True,
            auto_resume_possible=True,
            last_evaluated_at=NOW,
            active_blockers=(
                UsageGovernanceBlocker(
                    source="runtime_token",
                    rule_id="test-rolling",
                    window="rolling_5h",
                    metric="total_tokens",
                    observed=125,
                    threshold=100,
                    next_auto_resume_at=NOW,
                ),
            ),
            paused_by_governance=True,
            next_auto_resume_at=NOW,
            subscription_quota_status=SubscriptionQuotaStatus(
                enabled=True,
                provider="codex_chatgpt_oauth",
                state="degraded",
                degraded_policy="fail_open",
                detail="quota_telemetry_unavailable",
                last_refreshed_at=NOW,
            ),
        ),
    )

    result = CliRunner().invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "pause_sources: usage_governance" in result.output
    assert "usage_governance_enabled: true" in result.output
    assert "usage_governance_paused: true" in result.output
    assert "usage_governance_blocker_count: 1" in result.output
    assert "usage_governance_subscription_status: degraded" in result.output
    assert "usage_governance_blocker: source=runtime_token rule=test-rolling" in result.output


def test_status_surfaces_failure_class_and_retry_counters(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths).model_copy(
        update={
            "current_failure_class": "missing_terminal_result",
            "troubleshoot_attempt_count": 2,
            "fix_cycle_count": 1,
            "consultant_invocations": 1,
        }
    )
    save_snapshot(paths, snapshot)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "current_failure_class: missing_terminal_result" in result.output
    assert "troubleshoot_attempt_count: 2" in result.output
    assert "fix_cycle_count: 1" in result.output
    assert "consultant_invocations: 1" in result.output


def test_status_surfaces_latest_runtime_effect_failure_metadata(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    stage_result_path = paths.runs_dir / "run-effect" / "stage_results" / "request-001.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)
    stage_result = StageResultEnvelope(
        run_id="run-effect",
        plane=Plane.PLANNING,
        stage=PlanningStageName.MANAGER,
        node_id="manager_blueprint",
        stage_kind_id="manager_blueprint",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-blueprint-001",
        terminal_result=PlanningTerminalResult.MANAGER_BLUEPRINT_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MANAGER_BLUEPRINT_COMPLETE",
        success=True,
        metadata={
            "runtime_effect_handler_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "blueprint_manifest_parse_error",
            "runtime_effect_failure_message": "blueprint_manifest.json is malformed",
            "runtime_effect_mutation_phase": "pre_mutation",
            "runtime_effect_failure_policy_id": "manager_blueprint_pre_mutation_artifact_repair",
            "runtime_effect_recovery_action": "route_to_node",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    snapshot = load_snapshot(paths).model_copy(
        update={
            "last_stage_result_path": str(stage_result_path.relative_to(paths.root)),
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, snapshot)

    result = CliRunner().invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert (
        "latest_runtime_effect_handler_id: manager_blueprint_manifest_to_blueprint_drafts"
        in result.output
    )
    assert "latest_runtime_effect_failure_class: blueprint_manifest_parse_error" in result.output
    assert (
        "latest_runtime_effect_failure_message: blueprint_manifest.json is malformed"
        in result.output
    )
    assert "latest_runtime_effect_mutation_phase: pre_mutation" in result.output
    assert (
        "latest_runtime_effect_failure_policy_id: manager_blueprint_pre_mutation_artifact_repair"
        in result.output
    )
    assert "latest_runtime_effect_recovery_action: route_to_node" in result.output


def test_status_surfaces_blueprint_repair_runtime_effect_diagnostics(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    stage_result_path = paths.runs_dir / "run-effect" / "stage_results" / "request-approval.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)
    stage_result = StageResultEnvelope(
        run_id="run-effect",
        plane=Plane.PLANNING,
        stage=PlanningStageName.MANAGER,
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        work_item_id="draft-blueprint-001",
        terminal_result=PlanningTerminalResult.BLUEPRINT_APPROVED,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        metadata={
            "runtime_effect_handler_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "generated_task_invalid",
            "runtime_effect_failure_message": "generated_task.md failed schema validation",
            "runtime_effect_mutation_phase": "pre_mutation",
            "runtime_effect_failure_policy_id": (
                "blueprint_approval_pre_mutation_effect_validation"
            ),
            "runtime_effect_recovery_action": "route_to_node",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    snapshot = load_snapshot(paths).model_copy(
        update={
            "last_stage_result_path": str(stage_result_path.relative_to(paths.root)),
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, snapshot)

    result = CliRunner().invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "latest_runtime_effect_failure_class: generated_task_invalid" in result.output
    assert (
        "latest_blueprint_repair_context: "
        "failed_handler=evaluator_blueprint_approved_to_task "
        "failure_class=generated_task_invalid "
        "mutation_phase=pre_mutation "
        "policy=blueprint_approval_pre_mutation_effect_validation "
        "recovery_action=route_to_node"
    ) in result.output
    assert (
        "latest_blueprint_repair_contract: "
        "action=apply_repaired_generated_task "
        "artifacts=blueprint_repair_decision,repaired_generated_task,mechanic_report "
        "repaired_artifact=repaired_generated_task"
    ) in result.output
    assert (
        "latest_blueprint_replay_conflict_classes: "
        "candidate=blueprint_candidate_duplicate_conflict,blueprint_candidate_markdown_conflict "
        "approval=blueprint_evaluation_duplicate_conflict,blueprint_approved_packet_conflict,"
        "blueprint_approved_markdown_conflict,blueprint_task_duplicate,"
        "blueprint_promotion_duplicate_conflict"
    ) in result.output
    assert (
        "latest_blueprint_inert_artifact_guard: "
        "repaired_blueprint_artifact.md ignored; mechanic_report.md evidence only"
    ) in result.output
    assert (
        "latest_blueprint_runtime_ownership_boundary: "
        "mechanic writes repair artifacts only; runtime owns queues and canonical Blueprint state"
    ) in result.output


def test_status_keeps_blueprint_repair_diagnostics_after_mechanic_apply_runtime_effect(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _, repair_result_path = _write_blueprint_repair_failure_then_apply_stage_results(
        paths,
        "run-effect-recovery",
    )
    snapshot = load_snapshot(paths).model_copy(
        update={
            "last_stage_result_path": str(repair_result_path.relative_to(paths.root)),
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, snapshot)

    result = CliRunner().invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert (
        "latest_runtime_effect_handler_id: evaluator_blueprint_approved_to_task"
        in result.output
    )
    assert "latest_runtime_effect_decision: request_block_source" in result.output
    assert "latest_runtime_effect_failure_class: generated_task_invalid" in result.output
    assert (
        "latest_runtime_effect_failure_message: generated_task.md failed schema validation"
        in result.output
    )
    assert "latest_runtime_effect_mutation_phase: pre_mutation" in result.output
    assert (
        "latest_runtime_effect_failure_policy_id: "
        "blueprint_approval_pre_mutation_effect_validation"
    ) in result.output
    assert "latest_runtime_effect_recovery_action: route_to_node" in result.output
    assert (
        "latest_blueprint_repair_context: "
        "failed_handler=evaluator_blueprint_approved_to_task "
        "failure_class=generated_task_invalid "
        "mutation_phase=pre_mutation "
        "policy=blueprint_approval_pre_mutation_effect_validation "
        "recovery_action=route_to_node"
    ) in result.output
    assert "latest_blueprint_repair_contract: action=apply_repaired_generated_task" in (
        result.output
    )


def test_status_uses_latest_prior_runtime_effect_metadata_when_last_stage_is_recovery(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _, recovery_result_path = _write_runtime_effect_then_recovery_stage_results(
        paths,
        "run-effect-recovery",
    )
    snapshot = load_snapshot(paths).model_copy(
        update={
            "last_stage_result_path": str(recovery_result_path.relative_to(paths.root)),
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, snapshot)

    result = CliRunner().invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert (
        "latest_runtime_effect_handler_id: manager_blueprint_manifest_to_blueprint_drafts"
        in result.output
    )
    assert "latest_runtime_effect_failure_class: blueprint_manifest_parse_error" in result.output
    assert (
        "latest_runtime_effect_failure_message: blueprint_manifest.json is malformed"
        in result.output
    )
    assert "latest_runtime_effect_mutation_phase: pre_mutation" in result.output
    assert (
        "latest_runtime_effect_failure_policy_id: manager_blueprint_pre_mutation_artifact_repair"
        in result.output
    )
    assert "latest_runtime_effect_recovery_action: route_to_node" in result.output


def test_status_json_surfaces_blocked_idle_context_and_runtime_error_report(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    report_path = paths.runs_dir / "run-recon" / "runtime_error_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Runtime Error Report\n", encoding="utf-8")
    paths.runtime_error_context_file.write_text(
        RuntimeErrorContext(
            error_code=RuntimeErrorCode.RECON_HANDOFF_INVALID,
            plane=Plane.PLANNING,
            failed_stage=PlanningStageName.RECON,
            repair_stage=PlanningStageName.RECON,
            work_item_kind=WorkItemKind.PROBE,
            work_item_id="probe-001",
            run_id="run-recon",
            router_action="idle",
            terminal_result=PlanningTerminalResult.RECON_TO_PLANNING,
            stage_result_path="millrace-agents/runs/run-recon/stage_results/request-001.json",
            report_path=str(report_path),
            exception_type="ValidationError",
            exception_message="Emitted-Spec-ID is required for to_planning decisions",
            failure_origin=RuntimeFailureOrigin.DOCUMENT_ADAPTER_VALIDATION_FAILURE,
            captured_at=NOW,
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-blocked",
            root_idea_id="idea-blocked",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-blocked.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-blocked.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-blocked.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=True,
            blocking_work_ids=("probe-001",),
            opened_at=NOW,
        ),
    )
    snapshot = load_snapshot(paths).model_copy(
        update={
            "runtime_mode": RuntimeMode.DAEMON,
            "process_running": True,
            "planning_status_marker": "### BLOCKED",
            "current_failure_class": "recon_handoff_invalid",
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, snapshot)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="status-json-tests",
        acquired_at=NOW,
    )

    result = CliRunner().invoke(cli.app, ["status", "show", "--format", "json", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["process_running"] is True
    assert payload["active_run_count"] == 0
    assert payload["planning_queue_depth"] == 0
    assert payload["closure_target_open"] is True
    assert payload["closure_target_blocked_by_lineage_work"] is True
    assert payload["blocked_idle"] is True
    assert payload["current_failure_class"] == "recon_handoff_invalid"
    assert payload["latest_runtime_error_report_path"] == str(report_path)
    assert payload["latest_runtime_failure_origin"] == "document_adapter_validation_failure"


def test_status_surfaces_closure_target_state(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_spec(
        SpecDocument(
            spec_id="spec-root-002",
            title="Deferred root spec",
            summary="deferred while another closure target is open",
            source_type="idea",
            source_id="idea-002",
            root_idea_id="idea-002",
            root_spec_id="spec-root-002",
            goals=("verify deferred status count",),
            constraints=("do not claim while spec-root-001 is open",),
            acceptance=("status reports deferred root specs",),
            references=("ideas/inbox/idea-002.md",),
            created_at=NOW,
            created_by="tests",
        )
    )
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            latest_verdict_path="millrace-agents/arbiter/verdicts/spec-root-001.json",
            latest_report_path="millrace-agents/arbiter/reports/run-001.md",
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "closure_target_root_spec_id: spec-root-001" in result.output
    assert "closure_target_open: true" in result.output
    assert "planning_root_specs_deferred_by_closure_target: 1" in result.output
    assert "closure_target_latest_verdict_path: millrace-agents/arbiter/verdicts/spec-root-001.json" in result.output
    assert "closure_target_latest_report_path: millrace-agents/arbiter/reports/run-001.md" in result.output


def test_status_prefers_actionable_closure_target_when_blocked_targets_remain(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-blocked",
            root_idea_id="idea-blocked",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-blocked.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-blocked.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-blocked.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=True,
            blocking_work_ids=("task-blocked",),
            opened_at=NOW,
        ),
    )
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-active",
            root_idea_id="idea-active",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-active.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-active.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-active.md",
            latest_verdict_path=None,
            latest_report_path=None,
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "invalid_multiple_open_targets" not in result.output
    assert "closure_target_root_spec_id: spec-root-active" in result.output
    assert "closure_target_blocked_by_lineage_work: false" in result.output


def test_status_surfaces_blueprint_operator_state(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    enqueue_blueprint_draft(
        paths,
        _blueprint_draft_doc(
            latest_blueprint_id="blueprint-draft-blueprint-001-r1",
            latest_critique_id="critique-blueprint-001",
        ),
    )
    assert claim_next_blueprint_draft(paths) is not None
    persist_blueprint_packet(paths, _blueprint_packet_doc(), packet_state="candidates")
    persist_blueprint_critique(paths, _blueprint_critique_doc(), critique_state="open")
    persist_blueprint_evaluation(paths, _blueprint_evaluation_doc())
    persist_blueprint_promotion(paths, _blueprint_promotion_doc())

    runner = CliRunner()
    result = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])
    json_result = runner.invoke(
        cli.app,
        ["status", "--workspace", str(paths.root), "--format", "json"],
    )

    assert result.exit_code == 0
    assert "blueprint_draft_queue_depth: 0" in result.output
    assert "blueprint_draft_active_count: 1" in result.output
    assert "blueprint_packet_candidate_count: 1" in result.output
    assert "blueprint_critique_open_count: 1" in result.output
    assert "blueprint_evaluation_count: 1" in result.output
    assert "blueprint_promotion_count: 1" in result.output
    assert "blueprint_draft: state=active draft=draft-blueprint-001" in result.output
    assert "latest_blueprint=blueprint-draft-blueprint-001-r1" in result.output
    assert "latest_critique=critique-blueprint-001" in result.output
    assert "blueprint_packet: state=candidates blueprint=blueprint-draft-blueprint-001-r1" in result.output
    assert "blueprint_critique: state=open critique=critique-blueprint-001" in result.output
    assert "blueprint_evaluation: evaluation=evaluation-blueprint-001 decision=approved" in result.output
    assert "blueprint_promotion: promotion=promotion-evaluation-blueprint-001" in result.output
    assert "generated_task=task-blueprint-001" in result.output

    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["blueprints"]["draft_counts"]["active"] == 1
    assert payload["blueprints"]["drafts"][0]["latest_critique_id"] == "critique-blueprint-001"
    assert payload["blueprints"]["promotions"][0]["generated_task_id"] == "task-blueprint-001"


def test_runs_ls_uses_run_inspection_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    seen = []

    def fake_list_runs(target):
        seen.append(target)
        return (_inspected_run_summary(),)

    monkeypatch.setattr(cli, "list_runs", fake_list_runs)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "ls", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert seen
    assert "run_id: run-001" in result.output
    assert "status: valid" in result.output
    assert "artifact_status: valid" in result.output
    assert "runtime_outcome: complete" in result.output
    assert "compiled_plan_id: plan-001" in result.output
    assert "work_item_id: task-001" in result.output
    assert "failure_origin: none" in result.output


def test_runs_ls_exposes_blocked_runtime_effect_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    monkeypatch.setattr(
        cli,
        "list_runs",
        lambda target: (
            _inspected_run_summary(
                status="valid",
                artifact_status="valid",
                runtime_outcome="blocked",
                failure_class="generated_task_missing",
                runtime_effect_handler_id="evaluator_blueprint_approved_to_task",
                runtime_effect_decision="request_block_source",
                runtime_effect_failure_class="generated_task_missing",
                runtime_effect_failure_message="generated task artifact was not created",
                runtime_effect_mutation_phase="pre_mutation",
                runtime_effect_failure_policy_id="blueprint_approval_pre_mutation_effect_validation",
                runtime_effect_recovery_action="route_to_node",
            ),
        ),
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "ls", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "status: valid" in result.output
    assert "artifact_status: valid" in result.output
    assert "runtime_outcome: blocked" in result.output
    assert "runtime_effect_handler_id: evaluator_blueprint_approved_to_task" in result.output
    assert "runtime_effect_decision: request_block_source" in result.output
    assert "runtime_effect_failure_class: generated_task_missing" in result.output
    assert "runtime_effect_failure_message: generated task artifact was not created" in result.output
    assert "runtime_effect_mutation_phase: pre_mutation" in result.output
    assert (
        "runtime_effect_failure_policy_id: blueprint_approval_pre_mutation_effect_validation"
        in result.output
    )
    assert "runtime_effect_recovery_action: route_to_node" in result.output
    assert "failure_class: generated_task_missing" in result.output


def test_runs_ls_and_show_use_latest_prior_runtime_effect_metadata_after_recovery(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _write_runtime_effect_then_recovery_stage_results(paths, "run-effect-recovery")

    runner = CliRunner()
    list_result = runner.invoke(cli.app, ["runs", "ls", "--workspace", str(paths.root)])
    show_result = runner.invoke(
        cli.app,
        ["runs", "show", "run-effect-recovery", "--workspace", str(paths.root)],
    )

    assert list_result.exit_code == 0
    assert (
        "runtime_effect_handler_id: manager_blueprint_manifest_to_blueprint_drafts"
        in list_result.output
    )
    assert "runtime_effect_failure_class: blueprint_manifest_parse_error" in list_result.output
    assert (
        "runtime_effect_failure_message: blueprint_manifest.json is malformed"
        in list_result.output
    )
    assert "runtime_effect_mutation_phase: pre_mutation" in list_result.output
    assert (
        "runtime_effect_failure_policy_id: manager_blueprint_pre_mutation_artifact_repair"
        in list_result.output
    )
    assert "runtime_effect_recovery_action: route_to_node" in list_result.output

    assert show_result.exit_code == 0
    summary_block = show_result.output.split("stage_result_path:", 1)[0]
    assert (
        "runtime_effect_handler_id: manager_blueprint_manifest_to_blueprint_drafts"
        in summary_block
    )
    assert "runtime_effect_failure_class: blueprint_manifest_parse_error" in summary_block
    assert (
        "runtime_effect_failure_message: blueprint_manifest.json is malformed"
        in summary_block
    )
    assert "runtime_effect_mutation_phase: pre_mutation" in summary_block
    assert (
        "runtime_effect_failure_policy_id: manager_blueprint_pre_mutation_artifact_repair"
        in summary_block
    )
    assert "runtime_effect_recovery_action: route_to_node" in summary_block


def test_runs_ls_and_show_keep_blueprint_failure_metadata_after_repair_apply_runtime_effect(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _write_blueprint_repair_failure_then_apply_stage_results(paths, "run-effect-recovery")

    runner = CliRunner()
    list_result = runner.invoke(cli.app, ["runs", "ls", "--workspace", str(paths.root)])
    show_result = runner.invoke(
        cli.app,
        ["runs", "show", "run-effect-recovery", "--workspace", str(paths.root)],
    )

    assert list_result.exit_code == 0
    assert (
        "runtime_effect_handler_id: evaluator_blueprint_approved_to_task"
        in list_result.output
    )
    assert "runtime_effect_decision: request_block_source" in list_result.output
    assert "runtime_effect_failure_class: generated_task_invalid" in list_result.output
    assert (
        "runtime_effect_failure_message: generated_task.md failed schema validation"
        in list_result.output
    )
    assert "runtime_effect_mutation_phase: pre_mutation" in list_result.output
    assert (
        "runtime_effect_failure_policy_id: "
        "blueprint_approval_pre_mutation_effect_validation"
    ) in list_result.output
    assert "runtime_effect_recovery_action: route_to_node" in list_result.output

    assert show_result.exit_code == 0
    summary_block = show_result.output.split("stage_result_path:", 1)[0]
    assert (
        "runtime_effect_handler_id: evaluator_blueprint_approved_to_task"
        in summary_block
    )
    assert "runtime_effect_decision: request_block_source" in summary_block
    assert "runtime_effect_failure_class: generated_task_invalid" in summary_block
    assert (
        "runtime_effect_failure_message: generated_task.md failed schema validation"
        in summary_block
    )
    assert "runtime_effect_mutation_phase: pre_mutation" in summary_block
    assert (
        "runtime_effect_failure_policy_id: "
        "blueprint_approval_pre_mutation_effect_validation"
    ) in summary_block
    assert "runtime_effect_recovery_action: route_to_node" in summary_block


def test_runs_show_prints_stage_terminal_and_artifact_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    monkeypatch.setattr(
        cli,
        "inspect_run_id",
        lambda target, run_id: _inspected_run_summary(
            run_id,
            failure_class="network_unavailable",
            failure_origin="network_unavailable",
            request_context_profile_id="checker.default",
            context_bundle_path="context/context.json",
            context_artifact_refs=("task:task-001",),
            context_render_plan_id="stage_request.default.v1",
            rendered_prompt_context_path="context/prompt_context.md",
            thinking_level="high",
            model_reasoning_effort="high",
            capability_grant_summaries=(
                "grant_id=grant-checker-runner capability=runner.invoke decision=granted enforcement=runtime_enforced",
            ),
            capability_support_summaries=(
                "grant_id=grant-checker-runner runner=codex_cli support=supported enforcement=runtime_enforced",
            ),
        ),
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "show", "run-001", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "run_id: run-001" in result.output
    assert "status: valid" in result.output
    assert "artifact_status: valid" in result.output
    assert "runtime_outcome: complete" in result.output
    assert "compiled_plan_id: plan-001" in result.output
    assert "mode_id: default_codex" in result.output
    assert "request_id: request-001" in result.output
    assert "stage: checker" in result.output
    assert "node_id: execution.checker.primary" in result.output
    assert "stage_kind_id: checker" in result.output
    assert "terminal_result: CHECKER_PASS" in result.output
    assert "failure_class: network_unavailable" in result.output
    assert "failure_origin: network_unavailable" in result.output
    assert "request_context_profile_id: checker.default" in result.output
    assert "context_bundle_path: context/context.json" in result.output
    assert "context_render_plan_id: stage_request.default.v1" in result.output
    assert "rendered_prompt_context_path: context/prompt_context.md" in result.output
    assert "context_artifact_ref: task:task-001" in result.output
    assert "runner_name: codex-cli" in result.output
    assert "model_name: gpt-5.4" in result.output
    assert "thinking_level: high" in result.output
    assert "model_reasoning_effort: high" in result.output
    assert "duration_seconds: 3.0" in result.output
    assert "input_tokens: 100" in result.output
    assert "cached_input_tokens: 30" in result.output
    assert "output_tokens: 12" in result.output
    assert "thinking_tokens: 5" in result.output
    assert "report_artifact: troubleshoot_report.md" in result.output
    assert "capability_grant: grant_id=grant-checker-runner capability=runner.invoke" in result.output
    assert "capability_support: grant_id=grant-checker-runner runner=codex_cli" in result.output


def test_runs_show_surfaces_closure_target_request_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    monkeypatch.setattr(
        cli,
        "inspect_run_id",
        lambda target, run_id: _inspected_run_summary(
            run_id,
            request_kind="closure_target",
            closure_target_root_spec_id="spec-root-001",
        ),
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "show", "run-001", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "request_kind: closure_target" in result.output
    assert "closure_target_root_spec_id: spec-root-001" in result.output


def test_runs_show_surfaces_runtime_effect_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    monkeypatch.setattr(
        cli,
        "inspect_run_id",
        lambda target, run_id: _inspected_run_summary(
            run_id,
            runtime_effect_handler_id="evaluator_blueprint_approved_to_task",
            runtime_effect_decision="request_block_source",
            runtime_effect_failure_class="generated_task_invalid",
            runtime_effect_failure_message="generated task payload failed validation",
            runtime_effect_mutation_phase="pre_mutation",
            runtime_effect_created_paths=(
                "millrace-agents/blueprints/evaluations/evaluation-blueprint-001.json",
                "millrace-agents/tasks/queue/task-blueprint-001.md",
            ),
            runtime_effect_source_lifecycle_plan_id="approve_blueprint_draft_after_effect",
            runtime_effect_source_lifecycle_action="block",
            runtime_effect_failure_policy_id="blueprint_approval_pre_mutation_effect_validation",
            runtime_effect_recovery_action="route_to_node",
        ),
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "show", "run-001", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "runtime_effect_handler_id: evaluator_blueprint_approved_to_task" in result.output
    assert "runtime_effect_decision: request_block_source" in result.output
    assert "runtime_effect_failure_class: generated_task_invalid" in result.output
    assert (
        "runtime_effect_failure_message: generated task payload failed validation"
        in result.output
    )
    assert "runtime_effect_mutation_phase: pre_mutation" in result.output
    assert (
        "runtime_effect_failure_policy_id: blueprint_approval_pre_mutation_effect_validation"
        in result.output
    )
    assert "runtime_effect_recovery_action: route_to_node" in result.output
    assert (
        "runtime_effect_source_lifecycle_plan_id: approve_blueprint_draft_after_effect"
        in result.output
    )
    assert "runtime_effect_source_lifecycle_action: block" in result.output
    assert (
        "runtime_effect_created_path: "
        "millrace-agents/blueprints/evaluations/evaluation-blueprint-001.json"
        in result.output
    )
    assert (
        "runtime_effect_created_path: millrace-agents/tasks/queue/task-blueprint-001.md"
        in result.output
    )


def test_runs_tail_chooses_primary_artifact_by_documented_priority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = tmp_path / "run-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "troubleshoot_report.md").write_text("report wins\n", encoding="utf-8")
    (run_dir / "runner_stdout.txt").write_text("stdout fallback\n", encoding="utf-8")
    (run_dir / "runner_stderr.txt").write_text("stderr fallback\n", encoding="utf-8")

    monkeypatch.setattr(
        cli,
        "inspect_run_id",
        lambda target, run_id: _inspected_run_summary(run_id, run_dir=str(run_dir)),
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "tail", "run-001", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "report wins" in result.output
    assert "stdout fallback" not in result.output


def test_status_and_queue_ls_count_queued_blueprint_drafts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    enqueue_blueprint_draft(paths, _blueprint_draft_doc())

    runner = CliRunner()
    status = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])
    queue = runner.invoke(cli.app, ["queue", "ls", "--workspace", str(paths.root)])

    assert status.exit_code == 0
    assert queue.exit_code == 0
    assert "planning_queue_depth: 1" in status.output
    assert "blueprint_draft_queue_depth: 1" in status.output
    assert "planning_queue_depth: 1" in queue.output
    assert "blueprint_draft_queue_depth: 1" in queue.output


def test_direct_add_spec_preserves_blueprint_draft_queue_depth(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    enqueue_blueprint_draft(paths, _blueprint_draft_doc())

    result = cli.RuntimeControl(paths).add_spec(SpecDocument.model_validate(_spec_payload("spec-queued")))

    assert result.applied is True
    snapshot = load_snapshot(paths)
    assert snapshot.queue_depth_planning == 2


def test_queue_ls_uses_compiled_inventory_for_custom_active_family(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    family = _custom_planning_family()
    _persist_custom_family(paths, family)
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    active_dir = paths.runtime_root / family.queue_dirs.active
    blocked_dir = paths.runtime_root / family.queue_dirs.blocked
    queue_dir.mkdir(parents=True, exist_ok=True)
    active_dir.mkdir(parents=True, exist_ok=True)
    blocked_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "custom-queued.json").write_text('{"custom_id":"custom-queued"}\n', encoding="utf-8")
    (active_dir / "custom-001.json").write_text('{"custom_id":"custom-001"}\n', encoding="utf-8")
    (blocked_dir / "custom-blocked.json").write_text('{"custom_id":"custom-blocked"}\n', encoding="utf-8")

    result = CliRunner().invoke(cli.app, ["queue", "ls", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "planning_queue_depth: 1" in result.output
    assert "planning_active: 1" in result.output
    assert "custom_review_queue_depth: 1" in result.output
    assert "active_custom_review_count: 1" in result.output
    assert "blocked_custom_review_count: 1" in result.output


def test_status_uses_inventory_for_custom_family_visibility(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    family = _custom_planning_family()
    _persist_custom_family(paths, family)
    for state, item_id in (
        ("queue", "custom-queued"),
        ("active", "custom-active"),
        ("blocked", "custom-blocked"),
    ):
        directory = paths.runtime_root / getattr(family.queue_dirs, state)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{item_id}.json").write_text(f'{{"custom_id":"{item_id}"}}\n', encoding="utf-8")

    result = CliRunner().invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "planning_queue_depth: 1" in result.output
    assert (
        "work_item_family: family=custom_review plane=planning queue=1 active=1 blocked=1"
        in result.output
    )


def test_queue_ls_keeps_learning_request_count_family_specific(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    family = _custom_learning_family()
    _persist_custom_family(paths, family)
    active_dir = paths.runtime_root / family.queue_dirs.active
    active_dir.mkdir(parents=True, exist_ok=True)
    (active_dir / "custom-001.json").write_text('{"custom_id":"custom-001"}\n', encoding="utf-8")

    result = CliRunner().invoke(cli.app, ["queue", "ls", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "learning_active: 1" in result.output
    assert "active_learning_request_count: 0" in result.output
    assert "active_custom_learning_count: 1" in result.output


def test_add_task_add_spec_and_queue_ls(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    task_doc = tmp_path / "task-import.json"
    spec_doc = tmp_path / "spec-import.json"
    task_doc.write_text(json.dumps(_task_payload("task-001")), encoding="utf-8")
    spec_doc.write_text(json.dumps(_spec_payload("spec-001")), encoding="utf-8")

    runner = CliRunner()

    add_task = runner.invoke(
        cli.app,
        ["add-task", str(task_doc), "--workspace", str(paths.root)],
    )
    add_spec = runner.invoke(
        cli.app,
        ["add-spec", str(spec_doc), "--workspace", str(paths.root)],
    )
    ls = runner.invoke(cli.app, ["queue", "ls", "--workspace", str(paths.root)])

    assert add_task.exit_code == 0
    assert add_spec.exit_code == 0
    assert ls.exit_code == 0
    assert (paths.tasks_queue_dir / "task-001.md").is_file()
    assert (paths.specs_queue_dir / "spec-001.md").is_file()
    assert "execution_queue_depth: 1" in ls.output
    assert "planning_queue_depth: 1" in ls.output
    assert "learning_queue_depth: 0" in ls.output


def test_add_probe_and_queue_show(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    probe_doc = tmp_path / "probe-import.json"
    probe_doc.write_text(json.dumps(_probe_payload("probe-001")), encoding="utf-8")

    runner = CliRunner()
    add_probe = runner.invoke(
        cli.app,
        ["add-probe", str(probe_doc), "--workspace", str(paths.root)],
    )
    ls = runner.invoke(cli.app, ["queue", "ls", "--workspace", str(paths.root)])
    show = runner.invoke(cli.app, ["queue", "show", "probe-001", "--workspace", str(paths.root)])

    assert add_probe.exit_code == 0
    assert ls.exit_code == 0
    assert show.exit_code == 0
    assert (paths.probes_queue_dir / "probe-001.md").is_file()
    assert "planning_queue_depth: 1" in ls.output
    assert "probe_queue_depth: 1" in ls.output
    assert "work_item_kind: probe" in show.output
    assert "work_item_state: queue" in show.output


def test_queue_ls_reports_active_counts_by_queue_family(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(TaskDocument.model_validate(_task_payload("task-active")))
    queue.enqueue_spec(SpecDocument.model_validate(_spec_payload("spec-active")))
    queue.enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="learn-active",
            title="Learn active",
            requested_action="improve",
            created_at=NOW,
            created_by="tests",
        )
    )
    assert queue.claim_next_execution_task() is not None
    assert queue.claim_next_planning_item() is not None
    assert queue.claim_next_learning_request() is not None

    result = CliRunner().invoke(cli.app, ["queue", "ls", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "execution_active: 1" in result.output
    assert "planning_active: 1" in result.output
    assert "learning_active: 1" in result.output
    assert "active_task_count: 1" in result.output
    assert "active_spec_count: 1" in result.output
    assert "active_incident_count: 0" in result.output
    assert "active_learning_request_count: 1" in result.output


def test_queue_add_commands_and_show_are_available_under_namespaced_surface(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    task_doc = tmp_path / "task-import.json"
    spec_doc = tmp_path / "spec-import.json"
    probe_doc = tmp_path / "probe-import.json"
    task_doc.write_text(json.dumps(_task_payload("task-001")), encoding="utf-8")
    spec_doc.write_text(json.dumps(_spec_payload("spec-001")), encoding="utf-8")
    probe_doc.write_text(json.dumps(_probe_payload("probe-001")), encoding="utf-8")

    runner = CliRunner()
    add_task = runner.invoke(
        cli.app,
        ["queue", "add-task", str(task_doc), "--workspace", str(paths.root)],
    )
    add_spec = runner.invoke(
        cli.app,
        ["queue", "add-spec", str(spec_doc), "--workspace", str(paths.root)],
    )
    add_probe = runner.invoke(
        cli.app,
        ["queue", "add-probe", str(probe_doc), "--workspace", str(paths.root)],
    )
    show = runner.invoke(
        cli.app,
        ["queue", "show", "task-001", "--workspace", str(paths.root)],
    )

    assert add_task.exit_code == 0
    assert add_spec.exit_code == 0
    assert add_probe.exit_code == 0
    assert show.exit_code == 0
    assert "work_item_id: task-001" in show.output
    assert "work_item_kind: task" in show.output
    assert "work_item_state: queue" in show.output


def test_queue_retry_blocked_requeues_retryable_blocked_task(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(TaskDocument.model_validate(_task_payload("task-retry")))
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_blocked("task-retry")
    metadata_dir = paths.runtime_root / "diagnostics" / "blocked"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "task-task-retry.json").write_text(
        json.dumps(
            {
                "work_item_kind": "task",
                "work_item_id": "task-retry",
                "blocked_at": NOW.isoformat(),
                "blocked_origin": "runner_failure",
                "failure_class": "network_unavailable",
                "failure_scope": "environment",
                "auto_requeue_candidate": True,
                "source_run_id": "run-001",
                "source_plane": "execution",
                "source_stage": "builder",
                "terminal_result": "BLOCKED",
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "queue",
            "retry-blocked",
            "task-retry",
            "--workspace",
            str(paths.root),
            "--reason",
            "retry after network outage",
        ],
    )

    assert result.exit_code == 0
    assert "requeued_task: task-retry" in result.output
    assert "source_state: blocked" in result.output
    assert "destination_state: queue" in result.output
    assert (paths.tasks_queue_dir / "task-retry.md").is_file()
    assert not (paths.tasks_blocked_dir / "task-retry.md").exists()


def test_queue_retry_blocked_requeues_family_selected_blocked_spec(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(SpecDocument.model_validate(_spec_payload("spec-retry")))
    assert queue.claim_next_planning_item() is not None
    queue.mark_spec_blocked("spec-retry")

    result = CliRunner().invoke(
        cli.app,
        [
            "queue",
            "retry-blocked",
            "spec-retry",
            "--family",
            "spec",
            "--workspace",
            str(paths.root),
            "--reason",
            "operator retry after fixing input",
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert "requeued_work_item: spec-retry" in result.output
    assert "work_item_family_id: spec" in result.output
    assert "work_item_kind: spec" in result.output
    assert "source_state: blocked" in result.output
    assert "destination_state: queue" in result.output
    assert (paths.specs_queue_dir / "spec-retry.md").is_file()
    assert not (paths.specs_blocked_dir / "spec-retry.md").exists()


def test_queue_retry_blocked_refuses_non_retryable_task_without_force(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(TaskDocument.model_validate(_task_payload("task-semantic-blocked")))
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_blocked("task-semantic-blocked")

    result = CliRunner().invoke(
        cli.app,
        [
            "queue",
            "retry-blocked",
            "task-semantic-blocked",
            "--workspace",
            str(paths.root),
            "--reason",
            "operator wants retry",
        ],
    )

    assert result.exit_code == 1
    assert "blocked task is not retryable" in result.output
    assert (paths.tasks_blocked_dir / "task-semantic-blocked.md").is_file()


def test_queue_retry_blocked_refuses_live_daemon_ownership_lock(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(TaskDocument.model_validate(_task_payload("task-retry-locked")))
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_blocked("task-retry-locked")
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="cli-retry-blocked-lock",
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "queue",
            "retry-blocked",
            "task-retry-locked",
            "--workspace",
            str(paths.root),
            "--reason",
            "retry after network outage",
            "--force",
        ],
    )

    assert result.exit_code == 1
    assert "active runtime ownership lock prevents blocked retry" in result.output
    assert (paths.tasks_blocked_dir / "task-retry-locked.md").is_file()


def test_queue_cancel_command_archives_queued_task(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(TaskDocument.model_validate(_task_payload("task-cancel-cli")))

    result = CliRunner().invoke(
        cli.app,
        [
            "queue",
            "cancel",
            "task-cancel-cli",
            "--kind",
            "task",
            "--workspace",
            str(paths.root),
            "--reason",
            "operator cancelled bad intake",
        ],
    )

    assert result.exit_code == 0
    assert "action: cancel_work_item" in result.output
    assert "mode: direct" in result.output
    assert "applied: true" in result.output
    assert not (paths.tasks_queue_dir / "task-cancel-cli.md").exists()
    assert tuple((paths.tasks_queue_dir / "cancelled").glob("task-cancel-cli.*.md"))


def test_queue_cancel_command_supports_custom_family(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    family = _custom_planning_family()
    _persist_custom_family(paths, family)
    queue_dir = paths.runtime_root / family.queue_dirs.queue
    queue_dir.mkdir(parents=True, exist_ok=True)
    source = queue_dir / "custom-001.json"
    source.write_text('{"custom_id":"custom-001"}\n', encoding="utf-8")
    remaining = queue_dir / "custom-002.json"
    remaining.write_text('{"custom_id":"custom-002"}\n', encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        [
            "queue",
            "cancel",
            "custom-001",
            "--family",
            "custom_review",
            "--workspace",
            str(paths.root),
            "--reason",
            "operator cancelled custom item",
        ],
    )

    assert result.exit_code == 0
    assert "detail: work_item_cancelled: custom_review custom-001" in result.output
    assert not source.exists()
    assert remaining.is_file()
    assert tuple((paths.runtime_root / family.queue_dirs.canceled).glob("custom-001.*.json"))
    snapshot = load_snapshot(paths)
    assert snapshot.queue_depth_planning == 1


def test_queue_supersede_command_moves_old_task_to_superseded_archive(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(TaskDocument.model_validate(_task_payload("task-old-cli")))
    assert queue.claim_next_execution_task() is not None
    queue.mark_task_blocked("task-old-cli")
    queue.enqueue_task(TaskDocument.model_validate(_task_payload("task-new-cli")))

    result = CliRunner().invoke(
        cli.app,
        [
            "queue",
            "supersede",
            "task-old-cli",
            "--replacement",
            "task-new-cli",
            "--workspace",
            str(paths.root),
            "--reason",
            "replacement has corrected scope",
        ],
    )

    assert result.exit_code == 0
    assert "action: supersede_task" in result.output
    assert "mode: direct" in result.output
    assert "applied: true" in result.output
    assert tuple((paths.tasks_blocked_dir / "superseded").glob("task-old-cli.*.md"))


def test_incident_cancel_command_archives_active_incident(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    incident = {
        "incident_id": "incident-cli",
        "title": "Incident CLI",
        "summary": "bad intake incident",
        "source_stage": "consultant",
        "source_plane": "execution",
        "failure_class": "bad_intake",
        "trigger_reason": "known bad intake",
        "consultant_decision": "needs_planning",
        "opened_at": NOW.isoformat(),
        "opened_by": "tests",
    }
    from millrace_ai.contracts import IncidentDocument

    queue = QueueStore(paths)
    queue.enqueue_incident(IncidentDocument.model_validate(incident))
    assert queue.claim_next_planning_item() is not None

    result = CliRunner().invoke(
        cli.app,
        [
            "incident",
            "cancel",
            "incident-cli",
            "--workspace",
            str(paths.root),
            "--reason",
            "incident came from bad intake",
        ],
    )

    assert result.exit_code == 0
    assert "action: cancel_incident" in result.output
    assert "mode: direct" in result.output
    assert "applied: true" in result.output
    assert tuple((paths.incidents_active_dir / "cancelled").glob("incident-cli.*.md"))


def test_queue_add_idea_stages_markdown_in_ideas_inbox(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    idea_doc = tmp_path / "idea-001.md"
    idea_doc.write_text("# Idea 001\n\nShip this\n", encoding="utf-8")

    runner = CliRunner()
    add_idea = runner.invoke(
        cli.app,
        ["queue", "add-idea", str(idea_doc), "--workspace", str(paths.root)],
    )

    assert add_idea.exit_code == 0
    staged = paths.root / "ideas" / "inbox" / "idea-001.md"
    assert staged.is_file()


def test_queue_add_commands_route_to_mailbox_when_daemon_owns_workspace(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": True,
                "updated_at": NOW,
            }
        ),
    )
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="cli-queue-mailbox",
    )

    task_doc = tmp_path / "task-import.json"
    spec_doc = tmp_path / "spec-import.json"
    probe_doc = tmp_path / "probe-import.json"
    idea_doc = tmp_path / "idea-queue-mailbox.md"
    task_doc.write_text(json.dumps(_task_payload("task-mailbox")), encoding="utf-8")
    spec_doc.write_text(json.dumps(_spec_payload("spec-mailbox")), encoding="utf-8")
    probe_doc.write_text(json.dumps(_probe_payload("probe-mailbox")), encoding="utf-8")
    idea_doc.write_text("# Mailbox idea\n", encoding="utf-8")

    runner = CliRunner()
    add_task = runner.invoke(
        cli.app,
        ["queue", "add-task", str(task_doc), "--workspace", str(paths.root)],
    )
    add_spec = runner.invoke(
        cli.app,
        ["queue", "add-spec", str(spec_doc), "--workspace", str(paths.root)],
    )
    add_probe = runner.invoke(
        cli.app,
        ["queue", "add-probe", str(probe_doc), "--workspace", str(paths.root)],
    )
    add_idea = runner.invoke(
        cli.app,
        ["queue", "add-idea", str(idea_doc), "--workspace", str(paths.root)],
    )

    assert add_task.exit_code == 0
    assert add_spec.exit_code == 0
    assert add_probe.exit_code == 0
    assert add_idea.exit_code == 0
    assert "mode: mailbox" in add_task.output
    assert "mode: mailbox" in add_spec.output
    assert "mode: mailbox" in add_probe.output
    assert "mode: mailbox" in add_idea.output

    pending = _pending_commands(paths)
    assert MailboxCommand.ADD_TASK in pending
    assert MailboxCommand.ADD_SPEC in pending
    assert MailboxCommand.ADD_PROBE in pending
    assert MailboxCommand.ADD_IDEA in pending
    assert not (paths.tasks_queue_dir / "task-mailbox.md").exists()
    assert not (paths.specs_queue_dir / "spec-mailbox.md").exists()
    assert not (paths.probes_queue_dir / "probe-mailbox.md").exists()
    assert not (paths.root / "ideas" / "inbox" / "idea-queue-mailbox.md").exists()


def test_queue_add_task_rejects_unsafe_task_id(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    task_doc = tmp_path / "task-import-unsafe.json"
    payload = _task_payload("task-safe")
    payload["task_id"] = "../escape"
    task_doc.write_text(json.dumps(payload), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["queue", "add-task", str(task_doc), "--workspace", str(paths.root)],
    )

    assert result.exit_code == 1
    assert "failed to add task" in result.output
    assert not (paths.root / "escape.md").exists()


def test_queue_show_rejects_unsafe_work_item_id(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["queue", "show", "../../escape", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 1
    assert "invalid work item id" in result.output


def test_queue_repair_lineage_previews_and_applies_safe_task_drift(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    canonical_root = "idea-idea-2026-04-27-browser-local-qa"
    stale_root = "idea-2026-04-27-browser-local-qa"
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id=canonical_root,
            root_idea_id=canonical_root,
            root_spec_path=f"millrace-agents/arbiter/contracts/root-specs/{canonical_root}.md",
            root_idea_path=f"millrace-agents/arbiter/contracts/ideas/{canonical_root}.md",
            rubric_path=f"millrace-agents/arbiter/rubrics/{canonical_root}.md",
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )
    QueueStore(paths).enqueue_task(
        TaskDocument(
            task_id="task-browser-local-qa",
            title="Task browser local qa",
            summary="drifted task",
            root_idea_id=canonical_root,
            root_spec_id=stale_root,
            spec_id=stale_root,
            target_paths=["src/millrace_ai/runtime.py"],
            acceptance=["repair drift"],
            required_checks=["uv run --extra dev python -m pytest tests/cli/test_cli.py -q"],
            references=["lab/misc/millrace-failure-mode.md"],
            risk=["closure loop"],
            created_at=NOW,
            created_by="tests",
        )
    )

    runner = CliRunner()
    preview = runner.invoke(
        cli.app,
        [
            "queue",
            "repair-lineage",
            "--workspace",
            str(paths.root),
            "--root-spec-id",
            canonical_root,
        ],
    )
    queued_path = paths.tasks_queue_dir / "task-browser-local-qa.md"
    assert preview.exit_code == 0
    assert "apply: false" in preview.output
    assert "repair_count: 1" in preview.output
    assert "task-browser-local-qa" in preview.output
    assert f"Root-Spec-ID: {stale_root}" in queued_path.read_text(encoding="utf-8")

    applied = runner.invoke(
        cli.app,
        [
            "queue",
            "repair-lineage",
            "--workspace",
            str(paths.root),
            "--root-spec-id",
            canonical_root,
            "--apply",
        ],
    )

    repaired_text = queued_path.read_text(encoding="utf-8")
    assert applied.exit_code == 0
    assert "apply: true" in applied.output
    assert "repaired_count: 1" in applied.output
    assert f"Root-Spec-ID: {canonical_root}" in repaired_text
    assert f"Spec-ID: {canonical_root}" in repaired_text


def test_queue_repair_lineage_refuses_live_daemon_lock(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    canonical_root = "idea-idea-2026-04-27-browser-local-qa"
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id=canonical_root,
            root_idea_id=canonical_root,
            root_spec_path=f"millrace-agents/arbiter/contracts/root-specs/{canonical_root}.md",
            root_idea_path=f"millrace-agents/arbiter/contracts/ideas/{canonical_root}.md",
            rubric_path=f"millrace-agents/arbiter/rubrics/{canonical_root}.md",
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="lineage-repair-lock",
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "queue",
            "repair-lineage",
            "--workspace",
            str(paths.root),
            "--root-spec-id",
            canonical_root,
            "--apply",
        ],
    )

    assert result.exit_code == 1
    assert "active runtime ownership lock" in result.output


@pytest.mark.parametrize(
    ("argv", "action"),
    (
        (["pause"], MailboxCommand.PAUSE),
        (["resume"], MailboxCommand.RESUME),
        (["stop"], MailboxCommand.STOP),
        (["retry-active"], MailboxCommand.RETRY_ACTIVE),
    ),
)
def test_control_commands_delegate_to_runtime_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    action: MailboxCommand,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            del target

        def pause_runtime(self, *, issuer: str = "operator"):
            seen.append("pause")
            del issuer
            return ControlActionResult(action=MailboxCommand.PAUSE, mode="direct", applied=True, detail="ok")

        def resume_runtime(self, *, issuer: str = "operator"):
            seen.append("resume")
            del issuer
            return ControlActionResult(action=MailboxCommand.RESUME, mode="direct", applied=True, detail="ok")

        def stop_runtime(self, *, issuer: str = "operator"):
            seen.append("stop")
            del issuer
            return ControlActionResult(action=MailboxCommand.STOP, mode="direct", applied=True, detail="ok")

        def retry_active(self, *, reason: str = "operator requested retry", issuer: str = "operator"):
            seen.append("retry-active")
            del reason, issuer
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=True,
                detail="ok",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    runner = CliRunner()
    result = runner.invoke(cli.app, [*argv, "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert seen
    assert f"action: {action.value}" in result.output


@pytest.mark.parametrize(
    ("argv", "action"),
    (
        (["control", "pause"], MailboxCommand.PAUSE),
        (["control", "resume"], MailboxCommand.RESUME),
        (["control", "stop"], MailboxCommand.STOP),
        (["control", "retry-active"], MailboxCommand.RETRY_ACTIVE),
        (["control", "clear-stale-state"], MailboxCommand.CLEAR_STALE_STATE),
        (["control", "reload-config"], MailboxCommand.RELOAD_CONFIG),
    ),
)
def test_namespaced_control_commands_delegate_to_runtime_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    action: MailboxCommand,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            del target

        def pause_runtime(self, *, issuer: str = "operator"):
            seen.append("pause")
            del issuer
            return ControlActionResult(action=MailboxCommand.PAUSE, mode="direct", applied=True, detail="ok")

        def resume_runtime(self, *, issuer: str = "operator"):
            seen.append("resume")
            del issuer
            return ControlActionResult(action=MailboxCommand.RESUME, mode="direct", applied=True, detail="ok")

        def stop_runtime(self, *, issuer: str = "operator"):
            seen.append("stop")
            del issuer
            return ControlActionResult(action=MailboxCommand.STOP, mode="direct", applied=True, detail="ok")

        def retry_active(self, *, reason: str = "operator requested retry", issuer: str = "operator"):
            seen.append("retry-active")
            del reason, issuer
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=True,
                detail="ok",
            )

        def clear_stale_state(
            self,
            *,
            reason: str = "operator requested stale-state clear",
            issuer: str = "operator",
        ):
            seen.append("clear-stale-state")
            del reason, issuer
            return ControlActionResult(
                action=MailboxCommand.CLEAR_STALE_STATE,
                mode="direct",
                applied=True,
                detail="ok",
            )

        def reload_config(self, *, issuer: str = "operator"):
            seen.append("reload-config")
            del issuer
            return ControlActionResult(
                action=MailboxCommand.RELOAD_CONFIG,
                mode="direct",
                applied=True,
                detail="ok",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    runner = CliRunner()
    result = runner.invoke(cli.app, [*argv, "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert seen
    assert f"action: {action.value}" in result.output


def test_planning_retry_active_command_delegates_to_runtime_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            del target

        def retry_active_planning(self, *, reason: str = "operator requested retry", issuer: str = "operator"):
            seen.append(f"{reason}|{issuer}")
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=True,
                detail="planning retry applied",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["planning", "retry-active", "--workspace", str(paths.root), "--reason", "planning retry"],
    )

    assert result.exit_code == 0
    assert seen == ["planning retry|operator"]
    assert "detail: planning retry applied" in result.output


def test_top_level_clear_stale_state_alias_delegates_to_runtime_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            del target

        def clear_stale_state(
            self,
            *,
            reason: str = "operator requested stale-state clear",
            issuer: str = "operator",
        ):
            seen.append("clear-stale-state")
            del reason, issuer
            return ControlActionResult(
                action=MailboxCommand.CLEAR_STALE_STATE,
                mode="direct",
                applied=True,
                detail="ok",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["clear-stale-state", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert seen == ["clear-stale-state"]
    assert "action: clear_stale_state" in result.output


def test_reload_config_routes_to_mailbox_when_workspace_has_daemon_owner(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": True,
                "updated_at": NOW,
            }
        ),
    )
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="cli-reload-mailbox",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["control", "reload-config", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert "mode: mailbox" in result.output
    assert MailboxCommand.RELOAD_CONFIG in _pending_commands(paths)


def test_approvals_commands_list_show_and_approve_pending_request(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    approval = ensure_execution_capability_approval(
        paths,
        request_id="request-001",
        run_id="run-001",
        plane=Plane.EXECUTION,
        node_id="execution.builder.primary",
        stage_kind_id="builder",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        grant=_approval_grant(),
        now=NOW,
    )

    runner = CliRunner()
    list_result = runner.invoke(cli.app, ["approvals", "ls", "--workspace", str(paths.root)])
    show_result = runner.invoke(
        cli.app,
        ["approvals", "show", approval.approval_id, "--workspace", str(paths.root)],
    )
    approve_result = runner.invoke(
        cli.app,
        [
            "approvals",
            "approve",
            approval.approval_id,
            "--reason",
            "operator accepted package install",
            "--workspace",
            str(paths.root),
        ],
    )

    assert list_result.exit_code == 0
    assert f"approval_id: {approval.approval_id}" in list_result.output
    assert "status: pending" in list_result.output
    assert "capability_id: package.install" in list_result.output
    assert show_result.exit_code == 0
    assert '"approval_id":' in show_result.output
    assert approve_result.exit_code == 0
    assert "action: approve_execution_capability" in approve_result.output
    assert "applied: true" in approve_result.output


def test_config_show_renders_effective_runtime_and_reload_state(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'default_mode = "standard_plain"',
                'run_style = "daemon"',
                "",
                "[watchers]",
                "enabled = true",
            ]
        ),
        encoding="utf-8",
    )
    snapshot = load_snapshot(paths).model_copy(
        update={
            "config_version": "cfg-active-123",
            "last_reload_outcome": ReloadOutcome.FAILED_RETAINED_PREVIOUS_PLAN,
            "last_reload_error": "mode lookup failed",
        }
    )
    save_snapshot(paths, snapshot)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "show", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "default_mode: standard_plain" in result.output
    assert "run_style: daemon" in result.output
    assert "watchers.enabled: true" in result.output
    assert "auto_recovery.enabled: true" in result.output
    assert "model_assignment.enabled: true" in result.output
    assert "model_assignment.default_alias: standard" in result.output
    assert "model_alias.fast: model=gpt-5.4-mini thinking_level=high" in result.output
    assert "model_alias.standard: model=gpt-5.5 thinking_level=medium" in result.output
    assert "model_alias.deep: model=gpt-5.5 thinking_level=xhigh" in result.output
    assert "execution_capabilities.enabled: true" in result.output
    assert "execution_capabilities.allow_advisory_grants: true" in result.output
    assert "execution_capabilities.fail_required_advisory: false" in result.output
    assert "config_version: cfg-active-123" in result.output
    assert "last_reload_outcome: failed_retained_previous_plan" in result.output
    assert "last_reload_error: mode lookup failed" in result.output


def test_model_aliases_list_shows_defaults(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    result = CliRunner().invoke(cli.app, ["model-aliases", "list", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "fast: model=gpt-5.4-mini thinking_level=high" in result.output
    assert "standard: model=gpt-5.5 thinking_level=medium" in result.output
    assert "deep: model=gpt-5.5 thinking_level=xhigh" in result.output
    assert "assignment: enabled=true default_alias=standard" in result.output


def test_model_aliases_set_updates_toml_and_requests_reload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            seen.append(str(target.root))

        def reload_config(self, *, issuer: str = "operator"):
            seen.append(issuer)
            return ControlActionResult(
                action=MailboxCommand.RELOAD_CONFIG,
                mode="direct",
                applied=True,
                detail="config reload applied",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    result = CliRunner().invoke(
        cli.app,
        [
            "model-aliases",
            "set",
            "audit",
            "--model",
            "gpt-5.5",
            "--thinking-level",
            "high",
            "--workspace",
            str(paths.root),
        ],
    )

    assert result.exit_code == 0
    assert seen == [str(paths.root), "operator"]
    config_text = (paths.runtime_root / "millrace.toml").read_text(encoding="utf-8")
    assert "[model_aliases.audit]" in config_text
    assert 'model = "gpt-5.5"' in config_text
    assert 'thinking_level = "high"' in config_text
    assert "detail: config reload applied" in result.output


def test_model_aliases_assign_loop_updates_toml(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    result = CliRunner().invoke(
        cli.app,
        [
            "model-aliases",
            "assign-loop",
            "planning.blueprint",
            "deep",
            "--workspace",
            str(paths.root),
            "--no-reload",
        ],
    )

    assert result.exit_code == 0
    assert "updated: true" in result.output
    assert (
        '"planning.blueprint" = "deep"'
        in (paths.runtime_root / "millrace.toml").read_text(encoding="utf-8")
    )


def test_config_validate_returns_nonzero_for_invalid_config(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[compile]",
                'default_execution_loop = "execution.standard"',
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "validate", "--workspace", str(paths.root)])

    assert result.exit_code == 1
    assert "error:" in result.output


def test_config_reload_command_delegates_to_runtime_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            del target

        def reload_config(self, *, issuer: str = "operator"):
            seen.append(issuer)
            return ControlActionResult(
                action=MailboxCommand.RELOAD_CONFIG,
                mode="direct",
                applied=True,
                detail="config reload applied",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "reload", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert seen == ["operator"]
    assert "detail: config reload applied" in result.output


def test_modes_list_outputs_shipped_modes() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["modes", "list"])

    assert result.exit_code == 0
    assert "default_codex" in result.output
    assert "default_pi" in result.output
    assert "standard_plain -> default_codex" in result.output
    assert "default_codex_integrated" in result.output
    assert "learning_codex_integrated" in result.output
    assert "standard_role_augmented" not in result.output


def test_modes_show_reports_alias_resolution_for_standard_plain() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["modes", "show", "standard_plain"])

    assert result.exit_code == 0
    assert "alias_of: default_codex" in result.output
    assert "mode_id: default_codex" in result.output


def test_compile_validate_returns_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def fake_load_runtime_config(config_path=None, *, mailbox_overrides=None, cli_overrides=None):
        del config_path, mailbox_overrides, cli_overrides
        return RuntimeConfig()

    def fake_compile_and_persist_workspace_plan(
        target,
        *,
        config,
        requested_mode_id=None,
        assets_root=None,
        now=None,
    ):
        del target, config, requested_mode_id, assets_root, now
        diagnostics = CompileDiagnostics(
            ok=False,
            mode_id="broken-mode",
            errors=("mode lookup failed",),
            emitted_at=NOW,
        )
        return CompileOutcome(active_plan=None, diagnostics=diagnostics, used_last_known_good=False)

    monkeypatch.setattr(cli, "load_runtime_config", fake_load_runtime_config)
    monkeypatch.setattr(cli, "compile_and_persist_workspace_plan", fake_compile_and_persist_workspace_plan)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["compile", "validate", "--workspace", str(paths.root), "--mode", "broken-mode"],
    )

    assert result.exit_code == 1
    assert "ok: false" in result.output
    assert "mode lookup failed" in result.output


def test_compile_show_surfaces_compiled_plan_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    observed: dict[str, object] = {}

    def fake_load_runtime_config(config_path=None, *, mailbox_overrides=None, cli_overrides=None):
        del config_path, mailbox_overrides, cli_overrides
        return RuntimeConfig()

    def fake_compile_and_persist_workspace_plan(
        target,
        *,
        config,
        requested_mode_id=None,
        assets_root=None,
        now=None,
    ):
        del target, config, requested_mode_id, now
        observed["assets_root"] = assets_root
        diagnostics = CompileDiagnostics(
            ok=True,
            mode_id="standard_plain",
            errors=(),
            emitted_at=NOW,
        )
        active_plan = SimpleNamespace(
            compiled_plan_id="plan-001",
            mode_id="standard_plain",
            learning_graph=None,
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            execution_graph=SimpleNamespace(
                nodes=(
                    SimpleNamespace(
                        plane=Plane.EXECUTION,
                        node_id="builder",
                        stage_kind_id="builder",
                        running_status_marker="BUILDER_RUNNING",
                        entrypoint_path="entrypoints/execution/builder.md",
                        entrypoint_contract_id="builder.contract.v1",
                        required_skill_paths=("skills/stage/execution/builder-core/SKILL.md",),
                        attached_skill_additions=(),
                        runner_name="codex_cli",
                        model_name=None,
                        thinking_level="high",
                        model_reasoning_effort="high",
                        timeout_seconds=3600,
                        execution_capability_grants=(
                            SimpleNamespace(
                                grant_id="grant-builder-runner",
                                capability_id="runner.invoke",
                                decision_state=SimpleNamespace(value="granted"),
                                enforcement_mode=SimpleNamespace(value="runtime_enforced"),
                                required=True,
                            ),
                        ),
                        execution_capability_warnings=("builder:workspace.read is required but advisory_only",),
                    ),
                ),
                compiled_entries=(
                    SimpleNamespace(
                        entry_key=SimpleNamespace(value="task"),
                        node_id="builder",
                    ),
                ),
                compiled_completion_entry=None,
            ),
            planning_graph=SimpleNamespace(
                nodes=(
                    SimpleNamespace(
                        plane=Plane.PLANNING,
                        node_id="arbiter",
                        stage_kind_id="arbiter",
                        running_status_marker="ARBITER_RUNNING",
                        entrypoint_path="entrypoints/planning/arbiter.md",
                        entrypoint_contract_id="arbiter.contract.v1",
                        required_skill_paths=("skills/stage/planning/arbiter-core/SKILL.md",),
                        attached_skill_additions=(),
                        runner_name="codex_cli",
                        model_name=None,
                        thinking_level=None,
                        model_reasoning_effort=None,
                        timeout_seconds=3600,
                        execution_capability_grants=(),
                        execution_capability_warnings=(),
                    ),
                ),
                compiled_entries=(
                    SimpleNamespace(
                        entry_key=SimpleNamespace(value="spec"),
                        node_id="planner",
                    ),
                    SimpleNamespace(
                        entry_key=SimpleNamespace(value="incident"),
                        node_id="auditor",
                    ),
                ),
                compiled_completion_entry=SimpleNamespace(
                    entry_key=SimpleNamespace(value="closure_target"),
                    node_id="arbiter",
                ),
                completion_behavior=SimpleNamespace(
                    trigger="backlog_drained",
                    readiness_rule="no_open_lineage_work",
                    request_kind="closure_target",
                    target_selector="active_closure_target",
                    root_source_policy=SimpleNamespace(
                        accepted_kinds=("idea", "probe", "manual", "spec", "incident"),
                        resolution="runtime_inventory",
                    ),
                    rubric_policy="reuse_or_create",
                    blocked_work_policy="suppress",
                    skip_if_already_closed=True,
                    on_pass_terminal_state_id="arbiter_complete",
                    on_gap_terminal_state_id="remediation_needed",
                    create_incident_on_gap=True,
                ),
            ),
        )
        return CompileOutcome(
            active_plan=active_plan,
            diagnostics=diagnostics,
            used_last_known_good=False,
            compile_input_fingerprint=SimpleNamespace(
                mode_id="standard_plain",
                config_fingerprint="cfg-001",
                assets_fingerprint="assets-001",
            ),
        )

    monkeypatch.setattr(cli, "load_runtime_config", fake_load_runtime_config)
    monkeypatch.setattr(cli, "compile_and_persist_workspace_plan", fake_compile_and_persist_workspace_plan)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["compile", "show", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert observed["assets_root"] == paths.runtime_root
    assert "graph_authoritative_for_runtime_execution:" not in result.output
    assert "graph_legacy_equivalence_ready_for_cutover:" not in result.output
    assert "graph_legacy_equivalence_issues:" not in result.output
    assert "entry: execution.task -> builder" in result.output
    assert "entry: planning.spec -> planner" in result.output
    assert "entry: planning.incident -> auditor" in result.output
    assert "completion: closure_target -> arbiter" in result.output
    assert "baseline_manifest_id:" in result.output
    assert "compiled_plan_currentness: current" in result.output
    assert "compile_input.mode_id: standard_plain" in result.output
    assert "compile_input.config_fingerprint: cfg-001" in result.output
    assert "compile_input.assets_fingerprint: assets-001" in result.output
    assert "compiled_plan_id: plan-001" in result.output
    assert "stage: execution.builder" in result.output
    assert "stage_kind_id: builder" in result.output
    assert "running_status_marker: BUILDER_RUNNING" in result.output
    assert "entrypoint_path: entrypoints/execution/builder.md" in result.output
    assert "entrypoint_contract_id: builder.contract.v1" in result.output
    assert "required_skills: skills/stage/execution/builder-core/SKILL.md" in result.output
    assert "attached_skills: none" in result.output
    assert "runner_name: codex_cli" in result.output
    assert "model_name: none" in result.output
    assert "thinking_level: high" in result.output
    assert "model_reasoning_effort: high" in result.output
    assert "timeout_seconds: 3600" in result.output
    assert "execution_capability_grant: grant-builder-runner capability=runner.invoke" in result.output
    assert "decision=granted enforcement=runtime_enforced required=true" in result.output
    assert "execution_capability_warning: builder:workspace.read is required but advisory_only" in result.output
    assert "completion_behavior.trigger: backlog_drained" in result.output
    assert "completion_behavior.request_kind: closure_target" in result.output
    assert (
        "completion_behavior.root_source_policy.accepted_kinds: idea, probe, manual, spec, incident"
        in result.output
    )
    assert "completion_behavior.on_gap_terminal_state_id: remediation_needed" in result.output
    assert "role_overlays:" not in result.output


def test_doctor_command_surfaces_workspace_diagnostics(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["doctor", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "ok: true" in result.output


def test_doctor_warns_on_latest_blueprint_repair_runtime_effect_context(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    stage_result_path = paths.runs_dir / "run-effect" / "stage_results" / "request-approval.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)
    stage_result = StageResultEnvelope(
        run_id="run-effect",
        plane=Plane.PLANNING,
        stage=PlanningStageName.MANAGER,
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        work_item_id="draft-blueprint-001",
        terminal_result=PlanningTerminalResult.BLUEPRINT_APPROVED,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        metadata={
            "runtime_effect_handler_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "generated_task_invalid",
            "runtime_effect_failure_message": "generated_task.md failed schema validation",
            "runtime_effect_mutation_phase": "pre_mutation",
            "runtime_effect_failure_policy_id": (
                "blueprint_approval_pre_mutation_effect_validation"
            ),
            "runtime_effect_recovery_action": "route_to_node",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    snapshot = load_snapshot(paths).model_copy(
        update={
            "last_stage_result_path": str(stage_result_path.relative_to(paths.root)),
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, snapshot)

    result = CliRunner().invoke(cli.app, ["doctor", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "ok: true" in result.output
    assert "warning: blueprint_runtime_effect_recovery_context" in result.output
    assert "failed_handler=evaluator_blueprint_approved_to_task" in result.output
    assert "failure_class=generated_task_invalid" in result.output
    assert "action=apply_repaired_generated_task" in result.output
    assert "blueprint_repair_decision,repaired_generated_task,mechanic_report" in result.output
    assert "blueprint_candidate_duplicate_conflict" in result.output
    assert "repaired_blueprint_artifact.md ignored" in result.output
    assert "mechanic writes repair artifacts only" in result.output


def test_doctor_keeps_blueprint_repair_context_after_mechanic_apply_runtime_effect(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _, repair_result_path = _write_blueprint_repair_failure_then_apply_stage_results(
        paths,
        "run-effect-recovery",
    )
    snapshot = load_snapshot(paths).model_copy(
        update={
            "last_stage_result_path": str(repair_result_path.relative_to(paths.root)),
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, snapshot)

    result = CliRunner().invoke(cli.app, ["doctor", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "ok: true" in result.output
    assert "warning: blueprint_runtime_effect_recovery_context" in result.output
    assert "failed_handler=evaluator_blueprint_approved_to_task" in result.output
    assert "failure_class=generated_task_invalid" in result.output
    assert "action=apply_repaired_generated_task" in result.output
    assert "blueprint_repair_decision,repaired_generated_task,mechanic_report" in result.output
    assert "blueprint_candidate_duplicate_conflict" in result.output
    assert "repaired_blueprint_artifact.md ignored" in result.output
    assert "mechanic writes repair artifacts only" in result.output


def test_upgrade_command_previews_three_way_classification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    assets_root = _copy_assets(tmp_path)
    (assets_root / "entrypoints" / "execution" / "builder.md").write_text(
        "candidate builder update\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "millrace_ai.workspace.asset_deployment.resolve_asset_source_root",
        lambda _: assets_root,
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["upgrade", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "applied: false" in result.output
    assert "baseline_manifest_id:" in result.output
    assert "candidate_manifest_id:" in result.output
    assert "entry: entrypoints/execution/builder.md safe_package_update" in result.output


def test_upgrade_command_apply_refreshes_managed_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    assets_root = _copy_assets(tmp_path)
    (assets_root / "entrypoints" / "execution" / "builder.md").write_text(
        "candidate builder update\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "millrace_ai.workspace.asset_deployment.resolve_asset_source_root",
        lambda _: assets_root,
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["upgrade", "--apply", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "applied: true" in result.output
    assert "result_manifest_id:" in result.output
    assert (paths.runtime_root / "entrypoints" / "execution" / "builder.md").read_text(
        encoding="utf-8"
    ) == "candidate builder update\n"


def test_upgrade_command_localizes_removed_managed_asset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    assets_root = _copy_assets(tmp_path)
    removed_relative_path = "entrypoints/execution/builder.md"
    (assets_root / removed_relative_path).unlink()

    monkeypatch.setattr(
        "millrace_ai.workspace.asset_deployment.resolve_asset_source_root",
        lambda _: assets_root,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "upgrade",
            "--workspace",
            str(paths.root),
            "--localize-removed",
            removed_relative_path,
        ],
    )

    assert result.exit_code == 0
    assert "localized_removed: 1" in result.output
    assert f"entry: {removed_relative_path} localized_removed" in result.output


def test_status_watch_outputs_multiple_updates_with_bound(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "status",
            "watch",
            "--workspace",
            str(paths.root),
            "--max-updates",
            "2",
            "--interval-seconds",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert result.output.count("runtime_mode:") >= 2


def test_status_watch_can_observe_multiple_workspaces_in_one_session(tmp_path: Path) -> None:
    first_paths = _workspace(tmp_path / "first")
    second_paths = _workspace(tmp_path / "second")
    first_lock_path = first_paths.runtime_lock_file
    second_lock_path = second_paths.runtime_lock_file
    assert first_lock_path.exists() is False
    assert second_lock_path.exists() is False

    first_snapshot = load_snapshot(first_paths).model_copy(
        update={
            "active_mode_id": "standard_plain",
            "compiled_plan_id": "plan-first",
            "runtime_mode": RuntimeMode.DAEMON,
        }
    )
    second_snapshot = load_snapshot(second_paths).model_copy(
        update={
            "active_mode_id": "standard_plain",
            "compiled_plan_id": "plan-second",
            "runtime_mode": RuntimeMode.DAEMON,
        }
    )
    save_snapshot(first_paths, first_snapshot)
    save_snapshot(second_paths, second_snapshot)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "status",
            "watch",
            "--workspace",
            str(first_paths.root),
            "--workspace",
            str(second_paths.root),
            "--max-updates",
            "1",
            "--interval-seconds",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert f"workspace: {first_paths.root}" in result.output
    assert f"workspace: {second_paths.root}" in result.output
    assert "compiled_plan_id: plan-first" in result.output
    assert "compiled_plan_id: plan-second" in result.output
    assert first_lock_path.exists() is False
    assert second_lock_path.exists() is False


def test_main_passes_provided_argv_through_to_typer_app(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_app(*, args=None, standalone_mode=False):
        observed["args"] = args
        observed["standalone_mode"] = standalone_mode

    monkeypatch.setattr(cli, "app", fake_app)
    argv = ["status", "--workspace", "/tmp/workspace"]

    exit_code = cli.main(argv)

    assert exit_code == 0
    assert observed["args"] is argv
    assert observed["standalone_mode"] is False


def test_main_with_none_argv_does_not_inject_fallback_args(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_app(*, args=None, standalone_mode=False):
        observed["args"] = args
        observed["standalone_mode"] = standalone_mode

    monkeypatch.setattr(cli, "app", fake_app)

    exit_code = cli.main()

    assert exit_code == 0
    assert observed["args"] is None
    assert observed["standalone_mode"] is False


def test_main_returns_nonzero_when_typer_app_returns_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_app(*, args=None, standalone_mode=False):
        del args, standalone_mode
        return 3

    monkeypatch.setattr(cli, "app", fake_app)

    exit_code = cli.main(["status"])

    assert exit_code == 3


def test_run_daemon_defaults_config_to_workspace_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    observed: dict[str, object] = {}

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, stage_runner, mode_id, assets_root, monitor
            observed["config_path"] = config_path
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=True)

        def startup(self):
            return SimpleNamespace(
                active_mode_id="standard_plain",
                compiled_plan_id="plan-001",
            )

        def tick(self):
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "1"],
    )

    assert result.exit_code == 0
    assert observed["config_path"] == paths.runtime_root / "millrace.toml"


def test_compile_validate_defaults_config_to_workspace_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    observed: dict[str, object] = {}

    def fake_load_runtime_config(config_path=None, *, mailbox_overrides=None, cli_overrides=None):
        del mailbox_overrides, cli_overrides
        observed["config_path"] = config_path
        return RuntimeConfig()

    def fake_compile_and_persist_workspace_plan(
        target,
        *,
        config,
        requested_mode_id=None,
        assets_root=None,
        now=None,
    ):
        del target, config, requested_mode_id, assets_root, now
        diagnostics = CompileDiagnostics(
            ok=True,
            mode_id="standard_plain",
            errors=(),
            emitted_at=NOW,
        )
        return CompileOutcome(active_plan=None, diagnostics=diagnostics, used_last_known_good=False)

    monkeypatch.setattr(cli, "load_runtime_config", fake_load_runtime_config)
    monkeypatch.setattr(cli, "compile_and_persist_workspace_plan", fake_compile_and_persist_workspace_plan)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["compile", "validate", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert observed["config_path"] == paths.runtime_root / "millrace.toml"
