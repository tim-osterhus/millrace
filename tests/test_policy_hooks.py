from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from millrace_engine.compiler import CompileStatus
from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.contracts import ControlPlane, ExecutionStatus, RunnerKind, StageType, TaskCard
from millrace_engine.policies import (
    ExecutionIntegrationEvaluator,
    ExecutionIntegrationSnapshot,
    ExecutionPolicySnapshot,
    ExecutionPacingEvaluator,
    ExecutionPacingSnapshot,
    ExecutionPreflightEvaluator,
    ExecutionUsageBudgetEvaluator,
    ExecutionUsageBudgetSnapshot,
    PolicyDecision,
    PolicyEvaluationRecord,
    PolicyEvidence,
    PolicyEvidenceKind,
    PolicyFactSnapshot,
    PolicyHook,
    PolicyHookError,
    PolicyHookRuntime,
    StageRuntimePolicyContext,
    StaticTransportProbe,
    TransportProbeResult,
    TransportReadiness,
    execution_integration_context,
    execution_preflight_context,
)
from millrace_engine.telemetry import WeeklyUsageSample
import millrace_engine.policies.usage_budget as usage_budget_module
from millrace_engine.standard_runtime import compile_standard_runtime_selection
from tests.support import load_workspace_fixture


def _compiled_plan(tmp_path: Path):
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    paths = build_runtime_paths(config)
    compile_result = compile_standard_runtime_selection(config, paths, run_id="policy-hook-fixture")

    assert compile_result.status is CompileStatus.OK
    assert compile_result.plan is not None
    assert compile_result.snapshot is not None
    return workspace, config_path, compile_result.plan, compile_result.snapshot.snapshot_id


def test_policy_hook_runtime_rejects_pre_stage_without_frozen_plan(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "golden_path")
    assert config_path.exists()

    with pytest.raises(PolicyHookError, match="pre_stage requires an active frozen execution plan"):
        PolicyHookRuntime().evaluate_pre_stage(
            run_id="missing-plan",
            routing_mode="frozen_plan",
            execution_status=ExecutionStatus.IDLE,
            active_task=None,
            backlog_depth=1,
            transition_history_count=0,
            frozen_plan=None,
            snapshot_id=None,
            stage_type=StageType.BUILDER,
            node_id="builder",
        )


def test_policy_hook_runtime_rejects_unknown_stage_node(tmp_path: Path) -> None:
    _, _, frozen_plan, snapshot_id = _compiled_plan(tmp_path)

    with pytest.raises(PolicyHookError, match="frozen plan does not define execution node: missing-node"):
        PolicyHookRuntime().evaluate_pre_stage(
            run_id="bad-node",
            routing_mode="frozen_plan",
            execution_status=ExecutionStatus.IDLE,
            active_task=None,
            backlog_depth=1,
            transition_history_count=0,
            frozen_plan=frozen_plan,
            snapshot_id=snapshot_id,
            stage_type=StageType.BUILDER,
            node_id="missing-node",
        )


def test_policy_hook_runtime_rejects_stage_type_node_mismatch(tmp_path: Path) -> None:
    _, _, frozen_plan, snapshot_id = _compiled_plan(tmp_path)

    with pytest.raises(
        PolicyHookError,
        match="stage-scoped policy hook expected integration for node integration, got builder",
    ):
        PolicyHookRuntime().evaluate_pre_stage(
            run_id="stage-mismatch",
            routing_mode="frozen_plan",
            execution_status=ExecutionStatus.IDLE,
            active_task=None,
            backlog_depth=1,
            transition_history_count=0,
            frozen_plan=frozen_plan,
            snapshot_id=snapshot_id,
            stage_type=StageType.BUILDER,
            node_id="integration",
        )


def test_policy_fact_snapshot_rejects_post_stage_payload_without_frozen_plan() -> None:
    with pytest.raises(ValidationError, match="post_stage hooks require frozen plan facts"):
        PolicyFactSnapshot.model_validate(
            {
                "hook": PolicyHook.POST_STAGE,
                "plane": ControlPlane.EXECUTION,
                "run_id": "invalid-post-stage",
                "queue": {
                    "backlog_depth": 0,
                    "backlog_empty": True,
                    "active_task_id": None,
                },
                "runtime": {"execution_status": ExecutionStatus.IDLE.value},
            }
        )


def test_policy_hook_runtime_cycle_boundary_without_evaluators_returns_scaffold_record(tmp_path: Path) -> None:
    _, _, frozen_plan, snapshot_id = _compiled_plan(tmp_path)

    records = PolicyHookRuntime().evaluate_cycle_boundary(
        run_id="cycle-boundary",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=None,
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
    )

    assert len(records) == 1
    assert records[0].hook is PolicyHook.CYCLE_BOUNDARY
    assert records[0].facts.plan is not None
    assert records[0].facts.stage is None
    assert records[0].notes == ("No concrete policy evaluator is registered for this hook yet.",)


def test_execution_preflight_evaluator_rejects_non_pre_stage_hook(tmp_path: Path) -> None:
    _, _, frozen_plan, snapshot_id = _compiled_plan(tmp_path)
    facts = PolicyHookRuntime().evaluate_pre_stage(
        run_id="preflight-facts",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=None,
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
        stage_type=StageType.BUILDER,
        node_id="builder",
    )[0].facts
    evaluator = ExecutionPreflightEvaluator(
        ExecutionPolicySnapshot(
            preflight_enabled=True,
            transport_check_enabled=True,
            execution_search_enabled=True,
            execution_search_exception=False,
            network_guard_enabled=False,
            execution_network_policy="allow",
            execution_network_exception=False,
        ),
        stage_runtime=lambda stage_type: StageRuntimePolicyContext(command=("python3", "-V")),
        transport_probe=StaticTransportProbe(
            TransportProbeResult(readiness=TransportReadiness.READY, summary="transport ready")
        ),
    )

    with pytest.raises(PolicyHookError, match="only supports pre_stage hooks"):
        evaluator(facts.model_copy(update={"hook": PolicyHook.CYCLE_BOUNDARY}))


def test_execution_preflight_evaluator_reports_missing_subprocess_command_as_env_blocked(tmp_path: Path) -> None:
    _, _, frozen_plan, snapshot_id = _compiled_plan(tmp_path)
    facts = PolicyHookRuntime().evaluate_pre_stage(
        run_id="preflight-facts",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=None,
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
        stage_type=StageType.BUILDER,
        node_id="builder",
    )[0].facts
    evaluator = ExecutionPreflightEvaluator(
        ExecutionPolicySnapshot(
            preflight_enabled=True,
            transport_check_enabled=True,
            execution_search_enabled=True,
            execution_search_exception=False,
            network_guard_enabled=False,
            execution_network_policy="allow",
            execution_network_exception=False,
        ),
        stage_runtime=lambda stage_type: StageRuntimePolicyContext(command=()),
    )

    record = evaluator(
        facts.model_copy(
            update={"stage": facts.stage.model_copy(update={"runner": RunnerKind.SUBPROCESS})}
        )
    )

    assert record.decision.value == "env_blocked"
    assert record.notes == ("Subprocess runner requires an explicit command.",)


def test_execution_integration_evaluator_runs_when_task_gate_requires_integration(tmp_path: Path) -> None:
    workspace, config_path, frozen_plan, snapshot_id = _compiled_plan(tmp_path)
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    config.execution.integration_mode = "never"
    facts = PolicyHookRuntime().evaluate_cycle_boundary(
        run_id="integration-gated",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=TaskCard.model_validate(
            {
            "heading": "## 2026-03-19 - Gate integration explicitly",
            "body": "\n".join(
                [
                    "**Gates:** INTEGRATION",
                    "- **Goal:** Ensure integration runs.",
                ]
            ),
            }
        ),
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
    )[0].facts
    evaluator = ExecutionIntegrationEvaluator(ExecutionIntegrationSnapshot.from_config(config))

    record = evaluator(facts)
    context = execution_integration_context(record)

    assert record.decision is PolicyDecision.PASS
    assert context is not None
    assert context.should_run_integration is True
    assert context.builder_success_target == "integration"
    assert context.task_gate_required is True
    assert "Task gate requires integration." in context.reason


def test_execution_integration_evaluator_task_override_can_skip_integration(tmp_path: Path) -> None:
    workspace, config_path, frozen_plan, snapshot_id = _compiled_plan(tmp_path)
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    config.execution.integration_mode = "always"
    facts = PolicyHookRuntime().evaluate_cycle_boundary(
        run_id="integration-suppressed",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=TaskCard.model_validate(
            {
            "heading": "## 2026-03-19 - Suppress integration explicitly",
            "body": "\n".join(
                [
                    "**Integration:** skip",
                    "- **Goal:** Ensure integration is skipped.",
                ]
            ),
            }
        ),
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
    )[0].facts
    evaluator = ExecutionIntegrationEvaluator(ExecutionIntegrationSnapshot.from_config(config))

    record = evaluator(facts)
    context = execution_integration_context(record)

    assert record.decision is PolicyDecision.PASS
    assert context is not None
    assert context.should_run_integration is False
    assert context.builder_success_target == "qa"
    assert context.task_integration_preference == "skip"
    assert "Task integration override suppresses integration." in context.reason


def test_execution_integration_context_rejects_malformed_persisted_boolean_fields(tmp_path: Path) -> None:
    _, _, frozen_plan, snapshot_id = _compiled_plan(tmp_path)
    facts = PolicyHookRuntime().evaluate_cycle_boundary(
        run_id="persisted-integration-context",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=TaskCard.model_validate(
            {
                "heading": "## 2026-03-19 - Force integration explicitly",
                "body": "\n".join(
                    [
                        "**Gates:** INTEGRATION",
                        "- **Goal:** Ensure integration runs.",
                    ]
                ),
            }
        ),
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
    )[0].facts
    record = PolicyEvaluationRecord(
        evaluator=ExecutionIntegrationEvaluator.evaluator_name,
        hook=PolicyHook.CYCLE_BOUNDARY,
        decision=PolicyDecision.PASS,
        facts=facts,
        evidence=(
            PolicyEvidence(
                kind=PolicyEvidenceKind.INTEGRATION_POLICY,
                summary="Task gate requires integration.",
                details={
                    "effective_mode": "never",
                    "builder_success_target": "integration",
                    "should_run_integration": "true",
                    "task_gate_required": "true",
                    "task_integration_preference": None,
                    "requested_sequence": ["integration", "qa"],
                    "effective_sequence": ["integration", "qa"],
                    "available_execution_nodes": ["builder", "integration", "qa", "update"],
                    "reason": "Task gate requires integration. Builder routes to integration.",
                },
            ),
        ),
        notes=("Task gate requires integration.",),
    )

    with pytest.raises(ValueError, match="should_run_integration must be a boolean"):
        execution_integration_context(record)


def test_execution_integration_context_rejects_missing_persisted_boolean_fields(tmp_path: Path) -> None:
    _, _, frozen_plan, snapshot_id = _compiled_plan(tmp_path)
    facts = PolicyHookRuntime().evaluate_cycle_boundary(
        run_id="persisted-integration-context-missing",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=TaskCard.model_validate(
            {
                "heading": "## 2026-03-19 - Force integration explicitly",
                "body": "\n".join(
                    [
                        "**Gates:** INTEGRATION",
                        "- **Goal:** Ensure integration runs.",
                    ]
                ),
            }
        ),
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
    )[0].facts
    record = PolicyEvaluationRecord(
        evaluator=ExecutionIntegrationEvaluator.evaluator_name,
        hook=PolicyHook.CYCLE_BOUNDARY,
        decision=PolicyDecision.PASS,
        facts=facts,
        evidence=(
            PolicyEvidence(
                kind=PolicyEvidenceKind.INTEGRATION_POLICY,
                summary="Task gate requires integration.",
                details={
                    "effective_mode": "never",
                    "builder_success_target": "integration",
                    "task_gate_required": True,
                    "task_integration_preference": None,
                    "requested_sequence": ["integration", "qa"],
                    "effective_sequence": ["integration", "qa"],
                    "available_execution_nodes": ["builder", "integration", "qa", "update"],
                    "reason": "Task gate requires integration. Builder routes to integration.",
                },
            ),
        ),
        notes=("Task gate requires integration.",),
    )

    with pytest.raises(ValueError, match="should_run_integration must be a boolean"):
        execution_integration_context(record)


def test_execution_usage_budget_evaluator_rejects_non_cycle_boundary_hook(tmp_path: Path) -> None:
    workspace, config_path, frozen_plan, snapshot_id = _compiled_plan(tmp_path)
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    paths = build_runtime_paths(config)
    facts = PolicyHookRuntime().evaluate_cycle_boundary(
        run_id="usage-budget-facts",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=None,
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
    )[0].facts
    evaluator = ExecutionUsageBudgetEvaluator(
        ExecutionUsageBudgetSnapshot.from_config(config),
        paths=paths,
    )

    with pytest.raises(PolicyHookError, match="only supports cycle_boundary hooks"):
        evaluator(facts.model_copy(update={"hook": PolicyHook.PRE_STAGE}))


def test_execution_pacing_evaluator_rejects_non_post_stage_hook() -> None:
    evaluator = ExecutionPacingEvaluator(ExecutionPacingSnapshot(delay_seconds=2))
    facts = PolicyFactSnapshot.model_validate(
        {
            "hook": PolicyHook.CYCLE_BOUNDARY,
            "plane": ControlPlane.EXECUTION,
            "run_id": "pacing-invalid-hook",
            "queue": {"backlog_depth": 1, "backlog_empty": False},
            "runtime": {"execution_status": ExecutionStatus.IDLE.value},
        }
    )

    with pytest.raises(PolicyHookError, match="only supports post_stage hooks"):
        evaluator(facts)


def test_execution_usage_budget_evaluator_records_unavailable_codex_sample_without_pause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, frozen_plan, snapshot_id = _compiled_plan(tmp_path)
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    config.policies.usage.enabled = True
    config.policies.usage.provider = "codex"
    config.policies.usage.execution.remaining_threshold = "10"
    config.policies.usage.execution.consumed_threshold = None
    paths = build_runtime_paths(config)
    facts = PolicyHookRuntime().evaluate_cycle_boundary(
        run_id="usage-budget-unavailable",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=None,
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
    )[0].facts
    sample = WeeklyUsageSample(
        ok=False,
        loop="orchestrate",
        provider="codex",
        source="codex:unavailable",
        warnings=("codex app-server probe failed: provider=codex requires the codex CLI",),
        reason="provider=codex failed and no fallback sample is available",
    )
    monkeypatch.setattr(usage_budget_module, "sample_weekly_usage", lambda **_: sample)

    evaluator = ExecutionUsageBudgetEvaluator(
        ExecutionUsageBudgetSnapshot.from_config(config),
        paths=paths,
    )

    record = evaluator(facts)
    context = usage_budget_module.execution_usage_budget_context(record)

    assert record.decision is PolicyDecision.PASS
    assert record.notes == ("Execution weekly usage current is unavailable; continuing without auto-pause.",)
    assert context is not None
    assert context.pause_requested is False
    assert context.sample.ok is False
    assert context.sample.source == "codex:unavailable"
    assert context.sample.reason == "provider=codex failed and no fallback sample is available"
    assert context.sample.warnings == (
        "codex app-server probe failed: provider=codex requires the codex CLI",
    )


def test_execution_preflight_context_rejects_malformed_persisted_boolean_fields(tmp_path: Path) -> None:
    _, _, frozen_plan, snapshot_id = _compiled_plan(tmp_path)
    facts = PolicyHookRuntime().evaluate_pre_stage(
        run_id="persisted-preflight-context",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=None,
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
        stage_type=StageType.BUILDER,
        node_id="builder",
    )[0].facts
    record = PolicyEvaluationRecord(
        evaluator=ExecutionPreflightEvaluator.evaluator_name,
        hook=PolicyHook.PRE_STAGE,
        decision=PolicyDecision.PASS,
        facts=facts,
        evidence=(
            PolicyEvidence(
                kind=PolicyEvidenceKind.TRANSPORT_CHECK,
                summary="transport ready",
                details={"readiness": TransportReadiness.READY.value, "summary": "transport ready"},
            ),
            PolicyEvidence(
                kind=PolicyEvidenceKind.NETWORK_GUARD,
                summary="search disabled by policy",
                details={
                    "preflight_outcome": PolicyDecision.PASS.value,
                    "effective_allow_search": "false",
                    "effective_allow_network": "false",
                    "reason": "search disabled by policy",
                },
            ),
        ),
        notes=("search disabled by policy",),
    )

    with pytest.raises(ValueError, match="effective_allow_search must be a boolean"):
        execution_preflight_context(record)


def test_execution_preflight_context_rejects_missing_persisted_boolean_fields(tmp_path: Path) -> None:
    _, _, frozen_plan, snapshot_id = _compiled_plan(tmp_path)
    facts = PolicyHookRuntime().evaluate_pre_stage(
        run_id="persisted-preflight-context-missing",
        routing_mode="frozen_plan",
        execution_status=ExecutionStatus.IDLE,
        active_task=None,
        backlog_depth=1,
        transition_history_count=0,
        frozen_plan=frozen_plan,
        snapshot_id=snapshot_id,
        stage_type=StageType.BUILDER,
        node_id="builder",
    )[0].facts
    record = PolicyEvaluationRecord(
        evaluator=ExecutionPreflightEvaluator.evaluator_name,
        hook=PolicyHook.PRE_STAGE,
        decision=PolicyDecision.PASS,
        facts=facts,
        evidence=(
            PolicyEvidence(
                kind=PolicyEvidenceKind.TRANSPORT_CHECK,
                summary="transport ready",
                details={"readiness": TransportReadiness.READY.value, "summary": "transport ready"},
            ),
            PolicyEvidence(
                kind=PolicyEvidenceKind.NETWORK_GUARD,
                summary="search disabled by policy",
                details={
                    "preflight_outcome": PolicyDecision.PASS.value,
                    "effective_allow_network": False,
                    "reason": "search disabled by policy",
                },
            ),
        ),
        notes=("search disabled by policy",),
    )

    with pytest.raises(ValueError, match="effective_allow_search must be a boolean"):
        execution_preflight_context(record)
