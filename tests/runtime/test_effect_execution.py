from __future__ import annotations

import sys
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
from millrace_ai.runtime.effects.interpreter import (
    INTERPRETED_RUNNER_ID,
)
from millrace_ai.runtime.effects.legacy import (
    LEGACY_PYTHON_EFFECT_RUNNER_ID,
    default_legacy_runtime_effect_handler_registry,
)

NOW = datetime(2026, 5, 26, tzinfo=timezone.utc)
BLUEPRINT_IMPL_MODULE_PREFIX = "millrace_ai.extensions.builtin.blueprint"


def _reset_runtime_effect_handler_state() -> None:
    effect_execution._RUNTIME_EFFECT_HANDLER_REGISTRY = None
    effect_execution._EXTENSION_HANDLER_IDS = set()
    effect_execution._EXTENSION_OPERATION_IDS = set()
    effect_execution._HANDLERS_BY_ID = {}
    effect_execution._HANDLERS_BY_OPERATION_ID = {}


def _unload_blueprint_impl_modules() -> None:
    for name in list(sys.modules):
        if name.startswith(BLUEPRINT_IMPL_MODULE_PREFIX):
            del sys.modules[name]


def _loaded_blueprint_impl_modules() -> list[str]:
    return sorted(
        name for name in sys.modules if name.startswith(BLUEPRINT_IMPL_MODULE_PREFIX)
    )


def test_legacy_runtime_effect_registry_exposes_existing_handler_ids() -> None:
    registry = default_legacy_runtime_effect_handler_registry()

    assert set(registry.handlers_by_id) == {"planner_disposition"}
    assert all(
        runner_id == LEGACY_PYTHON_EFFECT_RUNNER_ID
        for runner_id in registry.runner_ids_by_handler_id.values()
    )


@pytest.mark.parametrize(
    ("operation_id", "legacy_handler_id"),
    [
        ("arbitrary_blueprint_operation", None),
        ("arbitrary_operation_blueprint", None),
        ("arbitrary_operation", "manager_blueprint_manifest_to_blueprint_drafts"),
    ],
)
def test_runtime_effect_handler_lookup_does_not_select_blueprint_by_spelling(
    operation_id: str,
    legacy_handler_id: str | None,
) -> None:
    _reset_runtime_effect_handler_state()
    _unload_blueprint_impl_modules()

    handler = effect_execution._handler_for_operation(
        operation_id,
        legacy_handler_id=legacy_handler_id,
    )

    assert handler is None
    assert _loaded_blueprint_impl_modules() == []


def test_runtime_effect_handler_lookup_does_not_reuse_cached_blueprint_handlers_without_compiled_metadata(
    tmp_path: Path,
) -> None:
    _reset_runtime_effect_handler_state()
    _unload_blueprint_impl_modules()

    operation_id = "manager_blueprint_manifest_to_blueprint_drafts"
    compiled_plan = SimpleNamespace(runtime_effect_handlers_by_id={operation_id: object()})

    valid_handler = effect_execution._handler_for_operation(
        operation_id,
        legacy_handler_id=operation_id,
        compiled_plan=compiled_plan,
    )

    assert valid_handler is not None
    loaded_after_compiled_lookup = set(_loaded_blueprint_impl_modules())
    assert loaded_after_compiled_lookup

    no_plan_same_op_handler = effect_execution._handler_for_operation(
        operation_id,
        legacy_handler_id=None,
    )
    assert no_plan_same_op_handler is None
    assert set(_loaded_blueprint_impl_modules()) == loaded_after_compiled_lookup

    no_plan_legacy_alias_handler = effect_execution._handler_for_operation(
        "arbitrary_operation",
        legacy_handler_id=operation_id,
    )
    assert no_plan_legacy_alias_handler is None
    assert set(_loaded_blueprint_impl_modules()) == loaded_after_compiled_lookup


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


def test_runtime_effect_dispatch_resolves_source_lifecycle_from_rule_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    captured: list[RuntimeEffectResult] = []

    def _operation_handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            operation_id="complete_source_effect",
            decision=RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE,
            message="completed by runner without lifecycle plan authority",
        )

    def _apply_effect_result(paths, effect_result, *, compiled_plan=None):
        captured.append(effect_result)
        return effect_result

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_OPERATION_ID,
        "complete_source_effect",
        _operation_handler,
    )
    monkeypatch.setattr(effect_execution, "apply_runtime_effect_result", _apply_effect_result)
    monkeypatch.setattr(effect_execution, "_clear_active_source_after_effect", lambda *args, **kwargs: None)
    stage_result = StageResultEnvelope(
        run_id="run-rule-lifecycle",
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_family_id="task",
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
                rule_id="complete_source_rule",
                effect_operation_id="complete_source_effect",
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id=None,
                destination_family_id=None,
                lifecycle_mutation_plan_id="complete_source_after_effect",
            ),
        ),
        runtime_effect_runners_by_id={
            "operation_only_runner": RuntimeEffectOperationRunnerDefinition(
                runner_id="operation_only_runner",
                operation_ids=("complete_source_effect",),
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

    assert application.router_decision.action is RouterAction.IDLE
    assert application.source_lifecycle_applied is True
    assert captured
    assert captured[0].source_lifecycle_intent is not None
    assert captured[0].source_lifecycle_intent.lifecycle_plan_id == "complete_source_after_effect"
    assert captured[0].source_lifecycle_intent.work_item_family_id == "task"
    assert captured[0].source_lifecycle_intent.work_item_id == "task-001"
    assert stage_result.metadata["runtime_effect_source_lifecycle_plan_id"] == (
        "complete_source_after_effect"
    )


def test_interpreted_runner_bypasses_legacy_handler_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a runner declares runner_id == INTERPRETED_RUNNER_ID, the
    effect execution dispatch layer must route through interpret_operation
    without calling _handler_for_operation or _handler_for_operation_id."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    seen_interpreted: list[str] = []

    def _fake_interpret(
        workspace_paths, stage_result, run_dir, compiled_plan,
        *, operation_id, runner_id, registry=None,
    ):
        seen_interpreted.append(operation_id)
        return RuntimeEffectResult(
            operation_id=operation_id,
            runner_id=runner_id,
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
        )

    monkeypatch.setattr(effect_execution, "interpret_operation", _fake_interpret)

    # Also guard against stale legacy handler dispatch
    legacy_called: list[str] = []
    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "legacy_stale_handler",
        lambda *a, **kw: legacy_called.append("legacy"),
    )
    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_OPERATION_ID,
        "test_interpreted_op",
        lambda *a, **kw: legacy_called.append("legacy_op"),
    )

    stage_result = StageResultEnvelope(
        run_id="run-interpreted-dispatch",
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
                rule_id="interpreted_dispatch_rule",
                effect_operation_id="test_interpreted_op",
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id=None,
                destination_family_id=None,
            ),
        ),
        runtime_effect_runners_by_id={
            "interpreted_test_runner": RuntimeEffectOperationRunnerDefinition(
                runner_id=INTERPRETED_RUNNER_ID,
                operation_ids=("test_interpreted_op",),
                legacy_handler_ids=("legacy_stale_handler",),
                legacy_handler_operation_ids={
                    "legacy_stale_handler": "test_interpreted_op",
                },
            )
        },
        runtime_failure_policies_by_id={},
        work_item_families_by_id={},
    )

    application = apply_runtime_effect_for_stage_result(
        SimpleNamespace(paths=paths, compiled_plan=compiled_plan),
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="builder_complete",
        ),
        compiled_plan=compiled_plan,
    )

    assert seen_interpreted == ["test_interpreted_op"]
    assert legacy_called == []
    assert application.router_decision.action is RouterAction.IDLE
    assert stage_result.metadata["runtime_effect_operation_id"] == "test_interpreted_op"
    assert stage_result.metadata["runtime_effect_runner_id"] == INTERPRETED_RUNNER_ID
    assert stage_result.metadata["runtime_effect_legacy_handler_id"] is None


def test_handler_for_operation_raises_and_interpreted_dispatch_still_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interpreted-runner dispatch must bypass _handler_for_operation.

    Monkeypatch _handler_for_operation to raise unconditionally, then prove
    that an interpreted-runner operation completes successfully without
    triggering the patched guard."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    def _handler_for_operation_guard(*args, **kwargs):
        raise AssertionError(
            "_handler_for_operation was called but interpreted dispatch "
            "must bypass it completely"
        )

    monkeypatch.setattr(
        effect_execution,
        "_handler_for_operation",
        _handler_for_operation_guard,
    )
    # Also guard the registry-level lookup to catch any indirect path.
    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "stale_handler",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("stale handler called for interpreted dispatch")
        ),
    )
    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_OPERATION_ID,
        "interpreted_direct_op",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("stale operation handler called for interpreted dispatch")
        ),
    )

    seen_interpreted: list[str] = []

    def _fake_interpret(
        workspace_paths, stage_result, run_dir, compiled_plan,
        *, operation_id, runner_id, registry=None,
    ):
        seen_interpreted.append(operation_id)
        return RuntimeEffectResult(
            operation_id=operation_id,
            runner_id=runner_id,
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
        )

    monkeypatch.setattr(effect_execution, "interpret_operation", _fake_interpret)

    stage_result = StageResultEnvelope(
        run_id="run-handler-bypass",
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
                rule_id="handler_bypass_rule",
                effect_operation_id="interpreted_direct_op",
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id=None,
                destination_family_id=None,
            ),
        ),
        runtime_effect_runners_by_id={
            "handler_bypass_runner": RuntimeEffectOperationRunnerDefinition(
                runner_id=INTERPRETED_RUNNER_ID,
                operation_ids=("interpreted_direct_op",),
                legacy_handler_ids=("stale_handler",),
                legacy_handler_operation_ids={
                    "stale_handler": "interpreted_direct_op",
                },
            )
        },
        runtime_failure_policies_by_id={},
        work_item_families_by_id={},
    )

    application = apply_runtime_effect_for_stage_result(
        SimpleNamespace(paths=paths, compiled_plan=compiled_plan),
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="builder_complete",
        ),
        compiled_plan=compiled_plan,
    )

    assert seen_interpreted == ["interpreted_direct_op"]
    assert application.router_decision.action is RouterAction.IDLE
    assert stage_result.metadata["runtime_effect_operation_id"] == "interpreted_direct_op"
    assert stage_result.metadata["runtime_effect_runner_id"] == INTERPRETED_RUNNER_ID
    assert stage_result.metadata["runtime_effect_legacy_handler_id"] is None
