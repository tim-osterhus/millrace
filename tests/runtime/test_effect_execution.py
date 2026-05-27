from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace_ai.architecture import RuntimeEffectOperationRunnerDefinition
from millrace_ai.contracts import ResultClass, StageResultEnvelope
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runtime import effect_execution
from millrace_ai.runtime.effect_execution import apply_runtime_effect_for_stage_result
from millrace_ai.runtime.effects import (
    RuntimeEffectDecision,
    RuntimeEffectHandlerRegistration,
    RuntimeEffectHandlerRegistry,
    RuntimeEffectResult,
)
from millrace_ai.runtime.effects.legacy import (
    LEGACY_PYTHON_EFFECT_RUNNER_ID,
    default_legacy_runtime_effect_handler_registry,
)

NOW = datetime(2026, 5, 26, tzinfo=timezone.utc)


def test_legacy_runtime_effect_registry_exposes_existing_handler_ids() -> None:
    registry = default_legacy_runtime_effect_handler_registry()

    assert set(registry.handlers_by_id) == {
        "planner_disposition",
        "manager_blueprint_manifest_to_blueprint_drafts",
        "contractor_blueprint_candidate_persist",
        "evaluator_blueprint_approved_to_task",
        "evaluator_blueprint_rejected_to_draft_revision",
        "mechanic_blueprint_repair_apply",
    }
    assert all(
        runner_id == LEGACY_PYTHON_EFFECT_RUNNER_ID
        for runner_id in registry.runner_ids_by_handler_id.values()
    )


def test_runtime_effect_dispatch_uses_registry_lookup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    seen: list[str] = []

    def _custom_handler(paths, stage_result, run_dir, compiled_plan):
        seen.append(stage_result.run_id)
        return RuntimeEffectResult(
            operation_id="custom_effect_operation",
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
        )

    registry = RuntimeEffectHandlerRegistry.from_registrations(
        (
            RuntimeEffectHandlerRegistration(
                handler_id="custom_legacy_handler",
                operation_id="custom_effect_operation",
                runner_id="custom_effect_runner",
                handler=_custom_handler,
            ),
        )
    )
    monkeypatch.setattr(effect_execution, "_RUNTIME_EFFECT_HANDLER_REGISTRY", registry)

    stage_result = StageResultEnvelope(
        run_id="run-registry",
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BUILDER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    compiled_plan = SimpleNamespace(
        runtime_effect_rules=(
            SimpleNamespace(
                rule_id="custom_registry_handler_rule",
                effect_operation_id="custom_effect_operation",
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id=None,
                destination_family_id=None,
            ),
        ),
        runtime_effect_runners_by_id={
            "custom_effect_runner": RuntimeEffectOperationRunnerDefinition(
                runner_id="custom_effect_runner",
                operation_ids=("custom_effect_operation",),
                legacy_handler_ids=("custom_legacy_handler",),
                legacy_handler_operation_ids={
                    "custom_legacy_handler": "custom_effect_operation"
                },
                result_display_aliases={
                    "custom_effect_operation": "custom_legacy_handler"
                },
            )
        },
        runtime_failure_policies_by_id={},
        work_item_families_by_id={},
    )

    application = apply_runtime_effect_for_stage_result(
        SimpleNamespace(paths=paths, compiled_plan=compiled_plan),
        request=SimpleNamespace(run_dir=str(tmp_path / "run")),
        stage_result=stage_result,
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="builder_complete",
        ),
        compiled_plan=compiled_plan,
    )

    assert seen == ["run-registry"]
    assert application.router_decision.reason == "builder_complete"
    assert stage_result.metadata["runtime_effect_handler_id"] == "custom_legacy_handler"
    assert stage_result.metadata["runtime_effect_operation_id"] == "custom_effect_operation"
    assert stage_result.metadata["runtime_effect_runner_id"] == "custom_effect_runner"
    assert stage_result.metadata["runtime_effect_legacy_handler_id"] == "custom_legacy_handler"


def test_runtime_effect_dispatch_ignores_stale_handler_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    seen: list[str] = []

    def _operation_handler(paths, stage_result, run_dir, compiled_plan):
        seen.append("operation")
        return RuntimeEffectResult(
            handler_id="custom_legacy_handler",
            operation_id="stale_handler_operation",
            runner_id="stale_handler_runner",
            legacy_handler_id="stale_legacy_handler",
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
        )

    def _stale_legacy_handler(paths, stage_result, run_dir, compiled_plan):
        seen.append("legacy")
        return RuntimeEffectResult(
            handler_id="custom_legacy_handler",
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
        )

    registry = RuntimeEffectHandlerRegistry.from_registrations(
        (
            RuntimeEffectHandlerRegistration(
                handler_id="custom_legacy_handler",
                operation_id="custom_effect_operation",
                runner_id="custom_effect_runner",
                handler=_operation_handler,
            ),
        )
    )
    monkeypatch.setattr(effect_execution, "_RUNTIME_EFFECT_HANDLER_REGISTRY", registry)
    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "custom_legacy_handler",
        _stale_legacy_handler,
    )

    stage_result = StageResultEnvelope(
        run_id="run-stale-identity",
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BUILDER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    compiled_plan = SimpleNamespace(
        runtime_effect_rules=(
            SimpleNamespace(
                rule_id="custom_stale_identity_rule",
                effect_operation_id="custom_effect_operation",
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id=None,
                destination_family_id=None,
            ),
        ),
        runtime_effect_runners_by_id={
            "custom_effect_runner": RuntimeEffectOperationRunnerDefinition(
                runner_id="custom_effect_runner",
                operation_ids=("custom_effect_operation",),
                legacy_handler_ids=("custom_legacy_handler",),
                legacy_handler_operation_ids={
                    "custom_legacy_handler": "custom_effect_operation"
                },
                result_display_aliases={
                    "custom_effect_operation": "custom_legacy_handler"
                },
            )
        },
        runtime_failure_policies_by_id={},
        work_item_families_by_id={},
    )

    application = apply_runtime_effect_for_stage_result(
        SimpleNamespace(paths=paths, compiled_plan=compiled_plan),
        request=SimpleNamespace(run_dir=str(tmp_path / "run")),
        stage_result=stage_result,
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="builder_complete",
        ),
        compiled_plan=compiled_plan,
    )

    assert seen == ["operation"]
    assert application.router_decision.reason == "builder_complete"
    assert stage_result.metadata["runtime_effect_operation_id"] == "custom_effect_operation"
    assert stage_result.metadata["runtime_effect_runner_id"] == "custom_effect_runner"
    assert stage_result.metadata["runtime_effect_legacy_handler_id"] == "custom_legacy_handler"


def test_runtime_effect_dispatch_reports_missing_operation_runner(
    tmp_path: Path,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    stage_result = StageResultEnvelope(
        run_id="run-missing-runner",
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BUILDER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    compiled_plan = SimpleNamespace(
        runtime_effect_rules=(
            SimpleNamespace(
                rule_id="custom_missing_runner_rule",
                effect_operation_id="custom_missing_runner_operation",
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id=None,
                destination_family_id=None,
            ),
        ),
        runtime_effect_runners_by_id={},
        runtime_failure_policies_by_id={},
        work_item_families_by_id={},
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "custom_missing_runner_operation.*builder.*"
            "custom_missing_runner_rule"
        ),
    ):
        apply_runtime_effect_for_stage_result(
            SimpleNamespace(paths=paths, compiled_plan=compiled_plan),
            request=SimpleNamespace(run_dir=str(tmp_path / "run")),
            stage_result=stage_result,
            router_decision=RouterDecision(
                action=RouterAction.IDLE,
                next_plane=None,
                next_stage=None,
                reason="builder_complete",
            ),
            compiled_plan=compiled_plan,
        )


def test_runtime_effect_operation_only_metadata_can_omit_legacy_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))

    def _operation_handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            operation_id="operation_only_effect",
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_OPERATION_ID,
        "operation_only_effect",
        _operation_handler,
    )
    stage_result = StageResultEnvelope(
        run_id="run-operation-only",
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BUILDER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    compiled_plan = SimpleNamespace(
        runtime_effect_rules=(
            SimpleNamespace(
                rule_id="operation_only_effect_rule",
                effect_operation_id="operation_only_effect",
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id=None,
                destination_family_id=None,
            ),
        ),
        runtime_effect_runners_by_id={
            "operation_only_runner": RuntimeEffectOperationRunnerDefinition(
                runner_id="operation_only_runner",
                operation_ids=("operation_only_effect",),
            )
        },
        runtime_failure_policies_by_id={},
        work_item_families_by_id={},
    )

    apply_runtime_effect_for_stage_result(
        SimpleNamespace(paths=paths, compiled_plan=compiled_plan),
        request=SimpleNamespace(run_dir=str(tmp_path / "run")),
        stage_result=stage_result,
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="builder_complete",
        ),
        compiled_plan=compiled_plan,
    )

    assert stage_result.metadata["runtime_effect_handler_id"] is None
    assert stage_result.metadata["runtime_effect_operation_id"] == "operation_only_effect"
    assert stage_result.metadata["runtime_effect_runner_id"] == "operation_only_runner"
    assert stage_result.metadata["runtime_effect_legacy_handler_id"] is None
