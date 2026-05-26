from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from millrace_ai.compilation import validation as validation_module
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import Plane, ResultClass, StageResultEnvelope, WorkItemKind
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runtime import effect_execution
from millrace_ai.runtime.effects import RuntimeEffectDecision, RuntimeEffectResult
from millrace_ai.runtime.effects.registry import (
    RuntimeEffectHandlerRegistration,
    RuntimeEffectHandlerRegistry,
)

ASSETS_ROOT = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
FIXTURE_ASSETS_ROOT = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "non_blueprint_effect_assets"
)
NOW = datetime(2026, 5, 26, tzinfo=UTC)
FIXTURE_HANDLER_ID = "fixture_echo_effect"
FIXTURE_RUNNER_ID = "fixture_test_runner"


def _copy_assets_with_non_blueprint_fixture(tmp_path: Path) -> Path:
    copied_root = tmp_path / "assets"
    shutil.copytree(ASSETS_ROOT, copied_root)
    shutil.copytree(FIXTURE_ASSETS_ROOT, copied_root, dirs_exist_ok=True)
    return copied_root


def _allow_fixture_handler_implementation(monkeypatch: Any) -> None:
    # Packet 04 uses a test-local runner; later migration packets replace this allow-list.
    monkeypatch.setattr(
        validation_module,
        "_RUNTIME_EFFECT_HANDLER_IMPLEMENTATION_IDS",
        validation_module._RUNTIME_EFFECT_HANDLER_IMPLEMENTATION_IDS | {FIXTURE_HANDLER_ID},
    )


def _compile_fixture_plan(tmp_path: Path, monkeypatch: Any):
    _allow_fixture_handler_implementation(monkeypatch)
    assets_root = _copy_assets_with_non_blueprint_fixture(tmp_path)
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=assets_root,
    )
    assert outcome.diagnostics.ok is True, outcome.diagnostics.errors
    assert outcome.active_plan is not None
    return workspace_root, outcome.active_plan


def _manager_complete_stage_result() -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-fixture-001",
        plane=Plane.PLANNING,
        stage="manager",
        node_id="manager",
        stage_kind_id="manager",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-fixture-001",
        terminal_result="MANAGER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MANAGER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )


def _fixture_echo_handler(
    paths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan,
) -> RuntimeEffectResult:
    payload = json.loads((run_dir / "fixture_effect_input.json").read_text(encoding="utf-8"))
    output_path = paths.runtime_root / "fixture-effects" / f"{stage_result.run_id}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "operation_id": compiled_plan.runtime_effect_operations_by_id[
                    FIXTURE_HANDLER_ID
                ].operation_id,
                "source_node_id": stage_result.node_id,
                "message": payload["message"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return RuntimeEffectResult(
        handler_id=FIXTURE_HANDLER_ID,
        decision=RuntimeEffectDecision.CONTINUE_ROUTE,
        created_paths=(str(output_path),),
        message="fixture echo effect applied",
    )


def _install_fixture_registry(monkeypatch: Any) -> None:
    registry = RuntimeEffectHandlerRegistry.from_registrations(
        (
            RuntimeEffectHandlerRegistration(
                handler_id=FIXTURE_HANDLER_ID,
                runner_id=FIXTURE_RUNNER_ID,
                handler=_fixture_echo_handler,
            ),
        )
    )
    monkeypatch.setattr(effect_execution, "_RUNTIME_EFFECT_HANDLER_REGISTRY", registry)
    monkeypatch.setattr(effect_execution, "_HANDLERS_BY_ID", registry.handlers_by_id)


def test_non_blueprint_fixture_effect_compiles_through_normal_plan_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _workspace_root, plan = _compile_fixture_plan(tmp_path, monkeypatch)

    assert FIXTURE_HANDLER_ID in plan.runtime_effect_operations_by_id
    assert any(
        rule.rule_id == "fixture_echo_effect_on_manager_complete"
        and rule.source_node_id == "manager"
        and rule.effect_operation_id == FIXTURE_HANDLER_ID
        for rule in plan.runtime_effect_rules
    )
    assert not (ASSETS_ROOT.parent / "runtime" / "fixture_effects.py").exists()


def test_non_blueprint_fixture_effect_executes_via_registered_operation_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root, plan = _compile_fixture_plan(tmp_path, monkeypatch)
    paths = workspace_paths(workspace_root)
    run_dir = paths.runs_dir / "run-fixture-001"
    run_dir.mkdir(parents=True)
    (run_dir / "fixture_effect_input.json").write_text(
        json.dumps({"message": "hello from fixture"}) + "\n",
        encoding="utf-8",
    )
    _install_fixture_registry(monkeypatch)
    stage_result = _manager_complete_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="manager_complete",
    )

    application = effect_execution.apply_runtime_effect_for_stage_result(
        SimpleNamespace(paths=paths, compiled_plan=plan),
        request=SimpleNamespace(run_dir=run_dir),
        stage_result=stage_result,
        router_decision=router_decision,
        compiled_plan=plan,
    )

    output_path = paths.runtime_root / "fixture-effects" / "run-fixture-001.json"
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert application.router_decision == router_decision
    assert output == {
        "operation_id": FIXTURE_HANDLER_ID,
        "source_node_id": "manager",
        "message": "hello from fixture",
    }
    assert stage_result.metadata["runtime_effect_operation_id"] == FIXTURE_HANDLER_ID
    assert stage_result.metadata["runtime_effect_runner_id"] == FIXTURE_RUNNER_ID
    assert stage_result.metadata["runtime_effect_legacy_handler_id"] == FIXTURE_HANDLER_ID
