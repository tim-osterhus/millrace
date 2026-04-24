from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import Plane
from millrace_ai.errors import AssetValidationError, MillraceError
from millrace_ai.modes import (
    SHIPPED_MODE_IDS,
    ModeAssetError,
    load_builtin_loop_definition,
    load_builtin_mode_bundle,
    load_builtin_mode_definition,
    resolve_builtin_mode_id,
    validate_shipped_mode_same_graph,
)
from millrace_ai.paths import bootstrap_workspace


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def test_modes_module_is_assets_facade() -> None:
    modes_facade = importlib.import_module("millrace_ai.modes")
    modes_module = importlib.import_module("millrace_ai.assets.modes")

    assert modes_facade.load_builtin_mode_bundle is modes_module.load_builtin_mode_bundle
    assert modes_facade.load_builtin_mode_definition is modes_module.load_builtin_mode_definition
    assert modes_facade.ModeBundle.__module__ == "millrace_ai.assets.modes"


def test_builtin_loops_load_and_validate() -> None:
    execution = load_builtin_loop_definition("execution.standard")
    planning = load_builtin_loop_definition("planning.standard")

    assert execution.plane is Plane.EXECUTION
    assert planning.plane is Plane.PLANNING
    assert execution.entry_stage.value == "builder"
    assert planning.entry_stage.value == "planner"
    assert "arbiter" in [stage.value for stage in planning.stages]
    assert planning.completion_behavior is not None
    assert planning.completion_behavior.stage.value == "arbiter"
    assert planning.completion_behavior.on_pass_terminal_result.value == "ARBITER_COMPLETE"
    assert planning.completion_behavior.on_gap_terminal_result.value == "REMEDIATION_NEEDED"


def test_builtin_modes_load_and_validate() -> None:
    bundle = load_builtin_mode_bundle("standard_plain")

    assert bundle.mode.mode_id == "default_codex"
    assert bundle.execution_loop.loop_id == "execution.standard"
    assert bundle.planning_loop.loop_id == "planning.standard"
    assert bundle.planning_loop.completion_behavior is not None


def test_shipped_modes_same_graph_rule_returns_plain_baseline_graph() -> None:
    assert validate_shipped_mode_same_graph() == ("execution.standard", "planning.standard")


def test_builtin_mode_alias_resolves_to_canonical_default_codex() -> None:
    assert resolve_builtin_mode_id("standard_plain") == "default_codex"
    assert resolve_builtin_mode_id("default_codex") == "default_codex"
    assert load_builtin_mode_definition("standard_plain").mode_id == "default_codex"


def test_builtin_modes_load_new_canonical_codex_and_pi_presets() -> None:
    codex_bundle = load_builtin_mode_bundle("default_codex")
    pi_bundle = load_builtin_mode_bundle("default_pi")

    assert codex_bundle.mode.mode_id == "default_codex"
    assert pi_bundle.mode.mode_id == "default_pi"
    assert codex_bundle.execution_loop.loop_id == pi_bundle.execution_loop.loop_id
    assert codex_bundle.planning_loop.loop_id == pi_bundle.planning_loop.loop_id
    assert codex_bundle.mode.stage_runner_bindings
    assert pi_bundle.mode.stage_runner_bindings
    assert set(codex_bundle.mode.stage_runner_bindings.values()) == {"codex_cli"}
    assert set(pi_bundle.mode.stage_runner_bindings.values()) == {"pi_rpc"}


def test_mode_asset_errors_use_project_error_hierarchy() -> None:
    assert issubclass(AssetValidationError, MillraceError)
    assert issubclass(ModeAssetError, AssetValidationError)


def test_unknown_mode_fails_deterministically() -> None:
    with pytest.raises(ModeAssetError, match=r"^Unknown built-in mode id: no_such_mode$"):
        load_builtin_mode_definition("no_such_mode")


def test_invalid_mode_json_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "default_codex.json"
    mode_path.write_text("{not-valid-json", encoding="utf-8")

    with pytest.raises(ModeAssetError, match="Invalid JSON in mode asset"):
        load_builtin_mode_definition("standard_plain", assets_root=assets_root)


def test_unknown_loop_reference_in_mode_bundle_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "default_codex.json"

    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["planning_loop_id"] = "planning.unknown"
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ModeAssetError, match=r"^Unknown built-in loop id: planning\.unknown$"):
        load_builtin_mode_bundle("standard_plain", assets_root=assets_root)


def test_shipped_mode_ids_are_stable() -> None:
    assert SHIPPED_MODE_IDS == ("default_codex", "default_pi")


def test_removed_role_augmented_mode_is_unknown() -> None:
    with pytest.raises(ModeAssetError, match=r"^Unknown built-in mode id: standard_role_augmented$"):
        load_builtin_mode_definition("standard_role_augmented")


def test_standard_plain_compiles_for_bootstrapped_workspace_without_role_overlays(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "default_codex"
    assert outcome.active_plan.execution_loop_id == "execution.standard"
    assert outcome.active_plan.planning_loop_id == "planning.standard"
    assert all(
        "role_overlays" not in stage_plan.model_dump(mode="json")
        for stage_plan in (
            *outcome.active_plan.execution_graph.nodes,
            *outcome.active_plan.planning_graph.nodes,
        )
    )
