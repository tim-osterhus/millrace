from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

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
            handler_id="custom_registry_handler",
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
        )

    registry = RuntimeEffectHandlerRegistry.from_registrations(
        (
            RuntimeEffectHandlerRegistration(
                handler_id="custom_registry_handler",
                runner_id="legacy_python_handler",
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
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id="custom_registry_handler",
                destination_family_id=None,
            ),
        ),
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
    assert stage_result.metadata["runtime_effect_handler_id"] == "custom_registry_handler"
