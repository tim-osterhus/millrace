from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import ExecutionStageName, LearningStageName, ModeDefinition, Plane, PlanningStageName
from millrace_ai.errors import AssetValidationError, MillraceError
from millrace_ai.modes import (
    BUILTIN_MODE_ALIASES,
    SHIPPED_MODE_IDS,
    ModeAssetError,
    load_builtin_loop_definition,
    load_builtin_mode_bundle,
    load_builtin_mode_definition,
    mode_asset_relative_path,
    resolve_builtin_mode_id,
    validate_shipped_mode_same_graph,
)
from millrace_ai.paths import bootstrap_workspace


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _write_workspace_local_mode_assets(assets_root: Path) -> None:
    execution_loop_path = assets_root / "loops" / "execution" / "local_review.json"
    execution_loop = json.loads(
        (assets_root / "loops" / "execution" / "lad.json").read_text(encoding="utf-8")
    )
    execution_loop["loop_id"] = "execution.local_review"
    execution_loop_path.write_text(json.dumps(execution_loop, indent=2) + "\n", encoding="utf-8")

    planning_loop_path = assets_root / "loops" / "planning" / "local_review.json"
    planning_loop = json.loads(
        (assets_root / "loops" / "planning" / "lad.json").read_text(encoding="utf-8")
    )
    planning_loop["loop_id"] = "planning.local_review"
    planning_loop_path.write_text(json.dumps(planning_loop, indent=2) + "\n", encoding="utf-8")

    mode_path = assets_root / "modes" / "local_review_codex.json"
    mode_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "mode",
                "mode_id": "local_review_codex",
                "loop_ids_by_plane": {
                    "execution": "execution.local_review",
                    "planning": "planning.local_review",
                },
                "stage_entrypoint_overrides": {},
                "stage_skill_additions": {},
                "stage_model_bindings": {"checker": "gpt-5.4"},
                "stage_thinking_bindings": {"checker": "high", "updater": None},
                "stage_runner_bindings": {
                    "builder": "codex_cli",
                    "checker": "codex_cli",
                    "fixer": "codex_cli",
                    "doublechecker": "codex_cli",
                    "updater": "codex_cli",
                    "troubleshooter": "codex_cli",
                    "consultant": "codex_cli",
                    "planner": "codex_cli",
                    "manager": "codex_cli",
                    "mechanic": "codex_cli",
                    "auditor": "codex_cli",
                    "arbiter": "codex_cli",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_modes_module_is_assets_facade() -> None:
    modes_facade = importlib.import_module("millrace_ai.modes")
    modes_module = importlib.import_module("millrace_ai.assets.modes")

    assert modes_facade.load_builtin_mode_bundle is modes_module.load_builtin_mode_bundle
    assert modes_facade.load_builtin_mode_definition is modes_module.load_builtin_mode_definition
    assert modes_facade.ModeBundle.__module__ == "millrace_ai.assets.modes"


def test_builtin_loops_load_and_validate() -> None:
    execution = load_builtin_loop_definition("execution.lad")
    planning = load_builtin_loop_definition("planning.lad")

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

    assert bundle.mode.mode_id == "lad_codex"
    assert bundle.execution_loop.loop_id == "execution.lad"
    assert bundle.planning_loop.loop_id == "planning.lad"
    assert bundle.planning_loop.completion_behavior is not None


def test_shipped_modes_same_graph_rule_returns_plain_baseline_graph() -> None:
    assert validate_shipped_mode_same_graph() == ("execution.lad", "planning.lad")


def test_builtin_mode_aliases_resolve_to_lad_canonical_ids() -> None:
    expected_aliases = {
        "default_codex": "lad_codex",
        "default_pi": "lad_pi",
        "learning_codex": "learning_lad_codex",
        "efficient_learning_mixed": "efficient_learning_lad_mixed",
        "learning_pi": "learning_lad_pi",
        "default_codex_integrated": "lad_codex_integrated",
        "learning_codex_integrated": "learning_lad_codex_integrated",
        "blueprint_codex": "blueprint_lad_codex",
        "blueprint_learning_codex": "blueprint_learning_lad_codex",
        "standard_plain": "lad_codex",
        "standard_millrace": "lad_pi",
        "learning_enabled_millrace": "learning_lad_pi",
    }

    for alias, canonical in expected_aliases.items():
        assert BUILTIN_MODE_ALIASES[alias] == canonical
        assert resolve_builtin_mode_id(alias) == canonical
        assert load_builtin_mode_definition(alias).mode_id == canonical

    assert resolve_builtin_mode_id("lad_codex") == "lad_codex"
    assert mode_asset_relative_path("lad_codex").as_posix() == "modes/lad_codex.json"


def test_builtin_modes_load_new_canonical_codex_and_pi_presets() -> None:
    codex_bundle = load_builtin_mode_bundle("lad_codex")
    pi_bundle = load_builtin_mode_bundle("lad_pi")

    assert codex_bundle.mode.mode_id == "lad_codex"
    assert pi_bundle.mode.mode_id == "lad_pi"
    assert codex_bundle.execution_loop.loop_id == pi_bundle.execution_loop.loop_id
    assert codex_bundle.planning_loop.loop_id == pi_bundle.planning_loop.loop_id
    assert codex_bundle.mode.stage_runner_bindings
    assert pi_bundle.mode.stage_runner_bindings
    assert set(codex_bundle.mode.stage_runner_bindings.values()) == {"codex_cli"}
    assert set(pi_bundle.mode.stage_runner_bindings.values()) == {"pi_rpc"}


def test_learning_modes_load_learning_plane_without_changing_default_modes() -> None:
    default_bundle = load_builtin_mode_bundle("lad_codex")
    learning_bundle = load_builtin_mode_bundle("learning_lad_codex")

    assert default_bundle.mode.learning_enabled is False
    assert set(default_bundle.mode.loop_ids_by_plane) == {Plane.EXECUTION, Plane.PLANNING}
    assert learning_bundle.mode.learning_enabled is True
    assert learning_bundle.learning_loop is not None
    assert learning_bundle.learning_loop.loop_id == "learning.standard"
    assert learning_bundle.learning_loop.plane is Plane.LEARNING
    assert learning_bundle.mode.learning_trigger_rules
    assert set(learning_bundle.mode.stage_runner_bindings.values()) == {"codex_cli"}
    assert {
        (rule.rule_id, rule.target_stage.value)
        for rule in learning_bundle.mode.learning_trigger_rules
    } >= {
        ("execution.doublechecker.success-to-analyst", "analyst"),
    }
    assert all(
        rule.target_stage.value != "curator" or rule.target_skill_id or rule.preferred_output_paths
        for rule in learning_bundle.mode.learning_trigger_rules
    )


def test_efficient_learning_mixed_mode_loads_alias_plan_with_integrator_off() -> None:
    bundle = load_builtin_mode_bundle("efficient_learning_lad_mixed")
    mode = bundle.mode

    assert mode.mode_id == "efficient_learning_lad_mixed"
    assert bundle.execution_loop.loop_id == "execution.lad"
    assert bundle.planning_loop.loop_id == "planning.lad"
    assert bundle.learning_loop is not None
    assert bundle.learning_loop.loop_id == "learning.standard"
    assert ExecutionStageName.INTEGRATOR not in bundle.execution_loop.stages
    assert mode.stage_runner_bindings["builder"] == "pi_rpc"
    assert mode.stage_runner_bindings["checker"] == "pi_rpc"
    assert mode.stage_runner_bindings["doublechecker"] == "pi_rpc"
    assert "integrator" not in mode.stage_runner_bindings
    assert mode.stage_runner_bindings["analyst"] == "pi_rpc"
    assert mode.stage_runner_bindings["updater"] == "codex_cli"
    assert mode.model_aliases["codex_max"].model == "gpt-5.5"
    assert mode.model_aliases["codex_max"].thinking_level == "xhigh"
    assert mode.model_aliases["codex_med"].model == "gpt-5.5"
    assert mode.model_aliases["codex_med"].thinking_level == "medium"
    assert mode.model_aliases["codex_fast"].model == "gpt-5.4-mini"
    assert mode.model_aliases["codex_fast"].thinking_level == "xhigh"
    assert mode.model_aliases["deepseek_max"].model == "deepseek-v4-pro"
    assert mode.model_aliases["deepseek_max"].thinking_level == "max"
    assert mode.model_aliases["deepseek_med"].model == "deepseek-v4-pro"
    assert mode.model_aliases["deepseek_med"].thinking_level == "high"
    assert mode.model_aliases["deepseek_fast"].model == "deepseek-v4-flash"
    assert mode.model_aliases["deepseek_fast"].thinking_level == "max"
    assert mode.model_assignment.by_stage["planner"] == "codex_max"
    assert mode.model_assignment.by_stage["analyst"] == "deepseek_med"
    assert mode.model_assignment.by_stage["builder"] == "deepseek_med"
    assert mode.model_assignment.by_stage["checker"] == "deepseek_max"
    assert mode.model_assignment.by_stage["fixer"] == "deepseek_fast"
    assert mode.model_assignment.by_stage["doublechecker"] == "deepseek_max"
    assert mode.model_assignment.by_stage["troubleshooter"] == "codex_max"
    assert "integrator" not in mode.model_assignment.by_stage
    assert mode.model_assignment.by_stage["updater"] == "codex_fast"
    assert mode.model_assignment.by_stage["consultant"] == "codex_max"


def test_graph_driven_learning_codex_mode_selects_blueprint_planning_with_learning() -> None:
    mode = load_builtin_mode_definition("blueprint_" "learning_lad_codex")
    blueprint_mode = load_builtin_mode_definition("blueprint_" "lad_codex")

    assert blueprint_mode.learning_enabled is False
    assert mode.mode_id == "blueprint_" "learning_lad_codex"
    assert mode.execution_loop_id == "execution.lad"
    assert mode.planning_loop_id == "planning.blueprint"
    assert mode.learning_loop_id == "learning.standard"
    assert mode.learning_enabled is True
    assert mode.stage_runner_bindings[PlanningStageName.PLANNER] == "codex_cli"
    assert mode.stage_runner_bindings["manager_blueprint"] == "codex_cli"
    assert mode.stage_runner_bindings[LearningStageName.LIBRARIAN] == "codex_cli"
    assert set(mode.stage_runner_bindings.values()) == {"codex_cli"}


def test_integrated_codex_modes_load_quality_execution_loop() -> None:
    default_bundle = load_builtin_mode_bundle("lad_codex_integrated")
    learning_bundle = load_builtin_mode_bundle("learning_lad_codex_integrated")

    assert default_bundle.mode.mode_id == "lad_codex_integrated"
    assert default_bundle.execution_loop.loop_id == "execution.lad_integrator"
    assert default_bundle.planning_loop.loop_id == "planning.lad"
    assert default_bundle.mode.learning_enabled is False
    assert default_bundle.mode.stage_runner_bindings[ExecutionStageName.INTEGRATOR] == "codex_cli"

    assert learning_bundle.mode.mode_id == "learning_lad_codex_integrated"
    assert learning_bundle.execution_loop.loop_id == "execution.lad_integrator"
    assert learning_bundle.planning_loop.loop_id == "planning.lad"
    assert learning_bundle.learning_loop is not None
    assert learning_bundle.learning_loop.loop_id == "learning.standard"
    assert learning_bundle.mode.learning_enabled is True
    assert learning_bundle.mode.stage_runner_bindings[ExecutionStageName.INTEGRATOR] == "codex_cli"


def test_learning_enabled_modes_trigger_librarian_after_planner_complete() -> None:
    for mode_id in (
        "learning_lad_codex",
        "efficient_learning_lad_mixed",
        "learning_lad_pi",
        "learning_lad_codex_integrated",
        "blueprint_" "learning_lad_codex",
    ):
        mode = load_builtin_mode_definition(mode_id)
        rule_by_id = {rule.rule_id: rule for rule in mode.learning_trigger_rules}

        rule = rule_by_id["planning.planner.complete-to-librarian"]
        assert rule.source_plane is Plane.PLANNING
        assert rule.source_stage is PlanningStageName.PLANNER
        assert rule.on_terminal_results == ("PLANNER_COMPLETE",)
        assert rule.target_stage is LearningStageName.LIBRARIAN
        assert rule.requested_action.value == "install"


def test_default_modes_do_not_trigger_librarian() -> None:
    for mode_id in (
        "lad_codex",
        "lad_pi",
        "lad_codex_integrated",
        "blueprint_" "lad_codex",
    ):
        mode = load_builtin_mode_definition(mode_id)
        assert all(
            rule.target_stage is not LearningStageName.LIBRARIAN
            for rule in mode.learning_trigger_rules
        )


def test_graph_driven_codex_mode_selects_blueprint_planning_graph_without_changing_defaults() -> None:
    mode = load_builtin_mode_definition("blueprint_" "lad_codex")
    default_mode = load_builtin_mode_definition("lad_codex")

    assert mode.mode_id == "blueprint_" "lad_codex"
    assert mode.execution_loop_id == "execution.lad"
    assert mode.planning_loop_id == "planning.blueprint"
    assert mode.learning_enabled is False
    assert default_mode.planning_loop_id == "planning.lad"
    assert mode.stage_runner_bindings["manager_blueprint"] == "codex_cli"
    assert mode.stage_runner_bindings["contractor_blueprint"] == "codex_cli"
    assert mode.stage_runner_bindings["evaluator_blueprint"] == "codex_cli"
    assert mode.stage_runner_bindings["mechanic_blueprint"] == "codex_cli"
    assert set(mode.stage_runner_bindings.values()) == {"codex_cli"}


def test_workspace_local_mode_loads_discovered_loops_and_stage_bindings(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    _write_workspace_local_mode_assets(assets_root)

    bundle = load_builtin_mode_bundle("local_review_codex", assets_root=assets_root)

    assert bundle.mode.mode_id == "local_review_codex"
    assert bundle.execution_loop.loop_id == "execution.local_review"
    assert bundle.planning_loop.loop_id == "planning.local_review"
    assert bundle.mode.stage_model_bindings["checker"] == "gpt-5.4"
    assert bundle.mode.stage_thinking_bindings["checker"] == "high"
    assert bundle.mode.stage_thinking_bindings["updater"] is None
    assert set(bundle.mode.stage_runner_bindings.values()) == {"codex_cli"}


def test_mode_definition_accepts_stage_thinking_bindings_with_null_defaults() -> None:
    mode = ModeDefinition(
        mode_id="custom_mode",
        loop_ids_by_plane={
            "execution": "execution.lad",
            "planning": "planning.lad",
        },
        stage_thinking_bindings={
            "checker": "high",
            "updater": None,
        },
    )

    assert mode.stage_thinking_bindings["checker"] == "high"
    assert mode.stage_thinking_bindings["updater"] is None


def test_mode_definition_rejects_empty_stage_thinking_binding() -> None:
    with pytest.raises(ValidationError, match="stage_thinking_bindings"):
        ModeDefinition(
            mode_id="custom_mode",
            loop_ids_by_plane={
                "execution": "execution.lad",
                "planning": "planning.lad",
            },
            stage_thinking_bindings={"checker": " "},
        )


def test_mode_asset_errors_use_project_error_hierarchy() -> None:
    assert issubclass(AssetValidationError, MillraceError)
    assert issubclass(ModeAssetError, AssetValidationError)


def test_unknown_mode_fails_deterministically() -> None:
    with pytest.raises(ModeAssetError, match=r"^Unknown mode id: no_such_mode$"):
        load_builtin_mode_definition("no_such_mode")


def test_invalid_mode_json_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "lad_codex.json"
    mode_path.write_text("{not-valid-json", encoding="utf-8")

    with pytest.raises(ModeAssetError, match="Invalid JSON in mode asset"):
        load_builtin_mode_definition("standard_plain", assets_root=assets_root)


def test_unknown_loop_reference_in_mode_bundle_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "lad_codex.json"

    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["planning_loop_id"] = "planning.unknown"
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ModeAssetError, match=r"^Unknown loop id: planning\.unknown$"):
        load_builtin_mode_bundle("standard_plain", assets_root=assets_root)


def test_shipped_mode_ids_are_stable() -> None:
    assert SHIPPED_MODE_IDS == (
        "lad_codex",
        "lad_pi",
        "learning_lad_codex",
        "efficient_learning_lad_mixed",
        "learning_lad_pi",
        "lad_codex_integrated",
        "learning_lad_codex_integrated",
        "blueprint_" "lad_codex",
        "blueprint_" "learning_lad_codex",
    )


def test_removed_role_augmented_mode_is_unknown() -> None:
    with pytest.raises(ModeAssetError, match=r"^Unknown mode id: standard_role_augmented$"):
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
    assert outcome.active_plan.mode_id == "lad_codex"
    assert outcome.active_plan.execution_loop_id == "execution.lad"
    assert outcome.active_plan.planning_loop_id == "planning.lad"
    assert all(
        "role_overlays" not in stage_plan.model_dump(mode="json")
        for stage_plan in (
            *outcome.active_plan.execution_graph.nodes,
            *outcome.active_plan.planning_graph.nodes,
        )
    )


def test_default_codex_integrated_compiles_for_bootstrapped_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex_integrated",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "lad_codex_integrated"
    assert outcome.active_plan.execution_loop_id == "execution.lad_integrator"
    assert outcome.active_plan.planning_loop_id == "planning.lad"
    assert [node.stage_kind_id for node in outcome.active_plan.execution_graph.nodes][:3] == [
        "lad_builder",
        "lad_integrator",
        "lad_checker",
    ]
    assert all(
        "blueprint" not in node.stage_kind_id
        for node in outcome.active_plan.planning_graph.nodes
    )


def test_graph_driven_codex_compiles_for_bootstrapped_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "lad_codex",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "blueprint_" "lad_codex"
    assert outcome.active_plan.execution_loop_id == "execution.lad"
    assert outcome.active_plan.planning_loop_id == "planning.blueprint"
    assert {entry.entry_key.value: entry.node_id for entry in outcome.active_plan.planning_graph.compiled_entries} == {
        "probe": "recon",
        "spec": "planner",
        "incident": "auditor",
        "blueprint_draft": "contractor_blueprint",
    }
    planning_nodes = {node.stage_kind_id: node for node in outcome.active_plan.planning_graph.nodes}
    assert planning_nodes["manager_blueprint"].runner_name == "codex_cli"
    assert planning_nodes["contractor_blueprint"].required_skill_paths == (
        "skills/stage/planning/contractor-blueprint-core/SKILL.md",
    )
    assert planning_nodes["evaluator_blueprint"].allowed_work_item_families == (
        "blueprint_draft",
    )


def test_graph_driven_learning_codex_compiles_for_bootstrapped_workspace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "learning_lad_codex",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "blueprint_" "learning_lad_codex"
    assert outcome.active_plan.execution_loop_id == "execution.lad"
    assert outcome.active_plan.planning_loop_id == "planning.blueprint"
    assert outcome.active_plan.learning_loop_id == "learning.standard"
    assert outcome.active_plan.learning_graph is not None
    assert {
        (rule.source_stage.value, rule.on_terminal_results, rule.target_stage.value)
        for rule in outcome.active_plan.learning_trigger_rules
    } >= {
        ("planner", ("PLANNER_COMPLETE",), "librarian"),
    }


def test_efficient_learning_mixed_compiles_with_mode_stage_aliases(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="efficient_learning_lad_mixed",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "efficient_learning_lad_mixed"
    assert outcome.active_plan.execution_loop_id == "execution.lad"
    assert outcome.active_plan.learning_loop_id == "learning.standard"

    nodes = {
        node.stage_kind_id: node
        for graph in outcome.active_plan.graphs_by_plane.values()
        for node in graph.nodes
    }
    assert "integrator" not in nodes

    expected = {
        "lad_builder": ("deepseek_med", "pi_rpc", "deepseek-v4-pro", "high"),
        "lad_checker": ("deepseek_max", "pi_rpc", "deepseek-v4-pro", "max"),
        "lad_fixer": ("deepseek_fast", "pi_rpc", "deepseek-v4-flash", "max"),
        "lad_doublechecker": ("deepseek_max", "pi_rpc", "deepseek-v4-pro", "max"),
        "lad_troubleshooter": ("codex_max", "codex_cli", "gpt-5.5", "xhigh"),
        "lad_updater": ("codex_fast", "codex_cli", "gpt-5.4-mini", "xhigh"),
        "lad_consultant": ("codex_max", "codex_cli", "gpt-5.5", "xhigh"),
        "recon": ("codex_max", "codex_cli", "gpt-5.5", "xhigh"),
        "lad_planner": ("codex_max", "codex_cli", "gpt-5.5", "xhigh"),
        "lad_manager": ("deepseek_max", "pi_rpc", "deepseek-v4-pro", "max"),
        "lad_mechanic": ("codex_max", "codex_cli", "gpt-5.5", "xhigh"),
        "lad_auditor": ("codex_max", "codex_cli", "gpt-5.5", "xhigh"),
        "lad_arbiter": ("codex_max", "codex_cli", "gpt-5.5", "xhigh"),
        "analyst": ("deepseek_med", "pi_rpc", "deepseek-v4-pro", "high"),
        "professor": ("codex_med", "codex_cli", "gpt-5.5", "medium"),
        "curator": ("codex_med", "codex_cli", "gpt-5.5", "medium"),
        "librarian": ("codex_med", "codex_cli", "gpt-5.5", "medium"),
    }
    for stage_kind_id, (alias_id, runner_name, model_name, thinking_level) in expected.items():
        node = nodes[stage_kind_id]
        assignment_stage = stage_kind_id.removeprefix("lad_")
        assert node.model_assignment_alias_id == alias_id
        assert node.model_assignment_source == f"mode:stage:{assignment_stage}"
        assert node.runner_name == runner_name
        assert node.model_name == model_name
        assert node.thinking_level == thinking_level
        if runner_name == "codex_cli":
            assert node.model_reasoning_effort == thinking_level
        else:
            assert node.model_reasoning_effort is None
    assert {
        (rule.source_stage.value, rule.on_terminal_results, rule.target_stage.value)
        for rule in outcome.active_plan.learning_trigger_rules
    } >= {
        ("planner", ("PLANNER_COMPLETE",), "librarian"),
    }


def test_minimal_three_plane_mode_loads_through_asset_discovery() -> None:
    mode = load_builtin_mode_definition("minimal_three_plane")

    assert mode.mode_id == "minimal_three_plane"
    assert "minimal_three_plane" not in SHIPPED_MODE_IDS
    assert mode.loop_ids_by_plane == {
        Plane.EXECUTION: "execution.minimal_three_plane",
        Plane.PLANNING: "planning.minimal_three_plane",
        Plane.LEARNING: "learning.minimal_three_plane",
    }
    assert mode.learning_enabled is True
    assert mode.stage_runner_bindings == {
        "basic_worker": "pi_rpc",
        "basic_planner": "pi_rpc",
        "basic_learner": "pi_rpc",
    }


def test_learning_codex_integrated_compiles_with_learning_plane(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="learning_lad_codex_integrated",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "learning_lad_codex_integrated"
    assert outcome.active_plan.execution_loop_id == "execution.lad_integrator"
    assert outcome.active_plan.learning_loop_id == "learning.standard"
    assert outcome.active_plan.learning_graph is not None


# ── config-swap tests ──────────────────────────────────────────────────────


def _compile_mode(tmp_path: Path, mode_id: str, **config_kwargs) -> "CompiledRunPlan":
    workspace_root = tmp_path / f"workspace-{mode_id}"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(**config_kwargs),
        requested_mode_id=mode_id,
    )

    assert outcome.diagnostics.ok is True, (
        f"Config-swap compilation failed for mode `{mode_id}`: {outcome.diagnostics.errors}"
    )
    assert outcome.active_plan is not None, (
        f"Config-swap compilation produced no plan for mode `{mode_id}`"
    )
    return outcome.active_plan


def _stage_kind_ids(plan: "CompiledRunPlan") -> set[str]:
    return {
        node.stage_kind_id
        for graph in plan.graphs_by_plane.values()
        for node in graph.nodes
    }


def test_shipped_closure_modes_assign_arbiter_explicit_high_reasoning_alias(
    tmp_path: Path,
) -> None:
    expected_alias_by_mode = {
        "efficient_learning_lad_mixed": "codex_max",
    }

    for mode_id in (*SHIPPED_MODE_IDS, "recovery_heavy_millrace"):
        mode = load_builtin_mode_definition(mode_id)
        assert mode.model_assignment.by_stage["arbiter"] == expected_alias_by_mode.get(
            mode_id,
            "deep",
        )

        plan = _compile_mode(tmp_path, mode_id)
        arbiter_nodes = [
            node
            for graph in plan.graphs_by_plane.values()
            for node in graph.nodes
            if node.stage_kind_id == "lad_arbiter"
        ]
        assert len(arbiter_nodes) == 1
        arbiter = arbiter_nodes[0]
        expected_alias = expected_alias_by_mode.get(mode_id, "deep")
        assert arbiter.model_assignment_alias_id == expected_alias
        assert arbiter.model_assignment_source == "mode:stage:arbiter"
        assert arbiter.model_name is not None
        assert arbiter.thinking_level in {"xhigh", "max"}
        if arbiter.runner_name == "codex_cli":
            assert arbiter.model_reasoning_effort == arbiter.thinking_level
        else:
            assert arbiter.model_reasoning_effort is None


def test_config_swap_minimal_three_plane_compiles_without_blueprint_recon_closure_or_learning(
    tmp_path: Path,
) -> None:
    """The same kernel compiles minimal_three_plane without importing Blueprint,
    Recon, closure, or Learning domain code."""
    plan = _compile_mode(tmp_path, "minimal_three_plane")

    assert plan.mode_id == "minimal_three_plane"
    assert plan.learning_graph is not None
    assert Plane.LEARNING in plan.loop_ids_by_plane

    stage_ids = _stage_kind_ids(plan)

    # No blueprint stages
    assert not any("blueprint" in sid for sid in stage_ids), (
        f"minimal_three_plane contains blueprint stage kinds: {sorted(stage_ids)}"
    )

    # No standard execution stages (builder, checker, fixer, etc.)
    domain_execution_stages = {
        "builder", "checker", "fixer", "doublechecker",
        "updater", "troubleshooter", "consultant", "integrator",
    }
    assert stage_ids.isdisjoint(domain_execution_stages), (
        f"minimal_three_plane contains domain execution stages: "
        f"{sorted(stage_ids & domain_execution_stages)}"
    )

    # No standard planning stages (recon, planner, manager, etc.)
    domain_planning_stages = {
        "recon", "planner", "manager", "mechanic", "auditor", "arbiter",
        "manager_blueprint", "contractor_blueprint", "evaluator_blueprint",
        "mechanic_blueprint",
    }
    assert stage_ids.isdisjoint(domain_planning_stages), (
        f"minimal_three_plane contains domain planning stages: "
        f"{sorted(stage_ids & domain_planning_stages)}"
    )

    # No learning domain stages (analyst, professor, curator, librarian)
    domain_learning_stages = {"analyst", "professor", "curator", "librarian"}
    assert stage_ids.isdisjoint(domain_learning_stages), (
        f"minimal_three_plane contains domain learning stages: "
        f"{sorted(stage_ids & domain_learning_stages)}"
    )

    # Only basic_* stages
    assert stage_ids == {"basic_worker", "basic_planner", "basic_learner"}, (
        f"minimal_three_plane has unexpected stage kinds: {sorted(stage_ids)}"
    )


def test_config_swap_standard_millrace_compiles_with_generic_recon_closure(
    tmp_path: Path,
) -> None:
    """The same kernel compiles standard_millrace with generic, Recon, and
    closure extensions."""
    plan = _compile_mode(tmp_path, "standard_millrace")

    assert plan.mode_id == "lad_pi"
    assert plan.learning_graph is None
    assert Plane.LEARNING not in plan.loop_ids_by_plane

    stage_ids = _stage_kind_ids(plan)

    # Standard execution stages
    assert "lad_builder" in stage_ids
    assert "lad_checker" in stage_ids
    assert "lad_fixer" in stage_ids
    assert "lad_troubleshooter" in stage_ids

    # Recon and closure are reflected in planning stages
    assert "recon" in stage_ids
    assert "lad_auditor" in stage_ids
    assert "lad_arbiter" in stage_ids

    # pi_rpc runner bound everywhere
    assert all(
        node.runner_name == "pi_rpc"
        for graph in plan.graphs_by_plane.values()
        for node in graph.nodes
    )


def test_config_swap_learning_enabled_millrace_has_learning_triggers(
    tmp_path: Path,
) -> None:
    """The same kernel compiles learning_enabled_millrace with Learning
    extension enabled and Learning triggers defined."""
    plan = _compile_mode(tmp_path, "learning_enabled_millrace")

    assert plan.mode_id == "learning_lad_pi"
    assert plan.learning_graph is not None
    assert Plane.LEARNING in plan.loop_ids_by_plane

    # Learning plane graph is populated
    learning_stage_ids = {node.stage_kind_id for node in plan.learning_graph.nodes}
    assert learning_stage_ids == {"analyst", "professor", "curator", "librarian"}

    # Learning triggers are present
    trigger_ids = {rule.rule_id for rule in plan.learning_trigger_rules}
    assert "execution.doublechecker.success-to-analyst" in trigger_ids
    assert "execution.troubleshooter.recovery-to-analyst" in trigger_ids
    assert "execution.consultant.recovery-to-analyst" in trigger_ids
    assert "planning.planner.complete-to-librarian" in trigger_ids

    # pip_rpc runner bound everywhere including learning stages
    assert all(
        node.runner_name == "pi_rpc"
        for graph in plan.graphs_by_plane.values()
        for node in graph.nodes
    )
    assert all(
        node.runner_name == "pi_rpc" for node in plan.learning_graph.nodes
    )


def test_config_swap_recovery_heavy_millrace_has_different_recovery_thresholds(
    tmp_path: Path,
) -> None:
    """recovery_heavy_millrace compiles with different recovery-policy
    thresholds than standard configs, driven by mode-declared policy selection."""
    recovery_heavy = _compile_mode(
        tmp_path,
        "recovery_heavy_millrace",
    )

    assert recovery_heavy.mode_id == "recovery_heavy_millrace"

    heavy_thresholds = {
        (p.policy_id, p.threshold)
        for graph in recovery_heavy.graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }

    # Compile standard_millrace with default thresholds
    standard_tmp = tmp_path / "standard_ws"
    standard = _compile_mode(standard_tmp, "standard_millrace")

    standard_thresholds = {
        (p.policy_id, p.threshold)
        for graph in standard.graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }

    assert recovery_heavy.mode_id != standard.mode_id

    # Recovery-heavy thresholds must differ from standard defaults
    assert heavy_thresholds != standard_thresholds, (
        f"recovery_heavy thresholds ({heavy_thresholds}) "
        f"should differ from standard thresholds ({standard_thresholds})"
    )

    # Recovery-heavy mode selects recovery_heavy_policies.json which
    # lowers blocked-recovery thresholds to 1.
    assert ("execution.blocked.recovery", 1) in heavy_thresholds
    assert ("planning.blocked.recovery", 1) in heavy_thresholds


def test_config_swap_generic_two_plane_fixture_compiles_without_domain_vocabulary(
    tmp_path: Path,
) -> None:
    """generic_two_plane_fixture compiles without execution, planning,
    or learning vocabulary. Uses two planes (execution + planning) with only
    basic_worker / basic_planner stage kinds and millrace.generic; the
    basic_worker → builder and basic_planner → planner runtime-stage
    bindings are a runner-contract compatibility layer, not arbitrary
    stage support."""
    plan = _compile_mode(tmp_path, "generic_two_plane_fixture")

    assert plan.mode_id == "generic_two_plane_fixture"
    assert plan.learning_graph is None
    assert Plane.LEARNING not in plan.loop_ids_by_plane

    stage_ids = _stage_kind_ids(plan)

    # No standard execution stages
    assert "builder" not in stage_ids
    assert "checker" not in stage_ids
    assert "fixer" not in stage_ids
    assert "updater" not in stage_ids
    assert "troubleshooter" not in stage_ids
    assert "consultant" not in stage_ids

    # No standard planning stages
    assert "recon" not in stage_ids
    assert "planner" not in stage_ids
    assert "manager" not in stage_ids
    assert "auditor" not in stage_ids
    assert "arbiter" not in stage_ids

    # No learning stages
    assert "analyst" not in stage_ids
    assert "professor" not in stage_ids
    assert "curator" not in stage_ids
    assert "librarian" not in stage_ids

    # Only minimal stage kinds
    assert stage_ids == {"basic_worker", "basic_planner"}, (
        f"generic_two_plane_fixture has unexpected stage kinds: {sorted(stage_ids)}"
    )


def test_config_swap_all_five_configs_compile_from_same_kernel(
    tmp_path: Path,
) -> None:
    """All five config-swap configs compile successfully from the same kernel
    without any source-code changes. Reports which config failed."""
    configs_and_runtime = [
        ("minimal_three_plane", RuntimeConfig()),
        ("standard_millrace", RuntimeConfig()),
        ("learning_enabled_millrace", RuntimeConfig()),
        ("recovery_heavy_millrace", RuntimeConfig()),
        ("generic_two_plane_fixture", RuntimeConfig()),
    ]

    results: dict[str, "CompiledRunPlan"] = {}

    for mode_id, rt_config in configs_and_runtime:
        workspace_root = tmp_path / mode_id
        bootstrap_workspace(workspace_root)

        outcome = compile_and_persist_workspace_plan(
            workspace_root,
            config=rt_config,
            requested_mode_id=mode_id,
        )

        assert outcome.diagnostics.ok is True, (
            f"Config-swap failed for `{mode_id}`: {outcome.diagnostics.errors}"
        )
        assert outcome.active_plan is not None, (
            f"Config-swap produced no plan for `{mode_id}`"
        )
        results[mode_id] = outcome.active_plan

    # Prove behavior differs across configs
    stage_ids_by_config = {
        mode_id: _stage_kind_ids(plan) for mode_id, plan in results.items()
    }

    # minimal_three_plane and generic_two_plane_fixture have minimal stage kinds
    for mode_id in ("minimal_three_plane", "generic_two_plane_fixture"):
        for forbidden in ("builder", "checker", "recon", "planner", "analyst"):
            assert forbidden not in stage_ids_by_config[mode_id], (
                f"{mode_id} contains forbidden stage `{forbidden}`"
            )

    # standard_millrace has full domain stages
    assert "lad_builder" in stage_ids_by_config["standard_millrace"]
    assert "recon" in stage_ids_by_config["standard_millrace"]

    # learning_enabled_millrace has learning triggers
    assert len(results["learning_enabled_millrace"].learning_trigger_rules) > 0
    assert results["standard_millrace"].learning_trigger_rules == ()
    assert results["learning_enabled_millrace"].learning_graph is not None
    assert results["standard_millrace"].learning_graph is None

    # recovery_heavy has different thresholds
    heavy_thresholds = {
        (p.policy_id, p.threshold)
        for graph in results["recovery_heavy_millrace"].graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }
    standard_thresholds = {
        (p.policy_id, p.threshold)
        for graph in results["standard_millrace"].graphs_by_plane.values()
        for p in graph.compiled_threshold_policies
    }
    assert heavy_thresholds != standard_thresholds, (
        f"recovery_heavy thresholds ({heavy_thresholds}) "
        f"identical to standard ({standard_thresholds})"
    )

    # All plans have unique mode_ids
    mode_ids = {plan.mode_id for plan in results.values()}
    assert len(mode_ids) == len(results), (
        f"Duplicate mode_ids across configs: {mode_ids}"
    )

    # No config-swap test mutated kernel source code
    # (proven by compile_and_persist_workspace_plan working identically for all)
