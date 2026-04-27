from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.compiler import (
    CompilerValidationError,
    compile_and_persist_workspace_plan,
    inspect_workspace_plan_currentness,
    preview_graph_loop_plan,
)
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import CompileDiagnostics, Plane, ResultClass
from millrace_ai.errors import ConfigurationError, MillraceError
from millrace_ai.paths import bootstrap_workspace, workspace_paths


def test_compiler_consumes_config_and_assets_package_surfaces() -> None:
    assets_package = importlib.import_module("millrace_ai.assets")
    compiler_module = importlib.import_module("millrace_ai.compiler")
    config_module = importlib.import_module("millrace_ai.config")

    assert compiler_module.RuntimeConfig is config_module.RuntimeConfig
    assert compiler_module.load_builtin_mode_definition is assets_package.load_builtin_mode_definition


def test_compiler_public_exports_remain_importable() -> None:
    compiler_module = importlib.import_module("millrace_ai.compiler")

    for name in compiler_module.__all__:
        assert hasattr(compiler_module, name), name


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _write_workspace_local_graph_mode_assets(assets_root: Path) -> None:
    execution_graph_path = assets_root / "graphs" / "execution" / "local_review.json"
    execution_graph = json.loads(
        (assets_root / "graphs" / "execution" / "standard.json").read_text(encoding="utf-8")
    )
    execution_graph["loop_id"] = "execution.local_review"
    execution_graph_path.write_text(json.dumps(execution_graph, indent=2) + "\n", encoding="utf-8")

    planning_graph_path = assets_root / "graphs" / "planning" / "local_review.json"
    planning_graph = json.loads(
        (assets_root / "graphs" / "planning" / "standard.json").read_text(encoding="utf-8")
    )
    planning_graph["loop_id"] = "planning.local_review"
    planning_graph_path.write_text(json.dumps(planning_graph, indent=2) + "\n", encoding="utf-8")

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


def _write_synthetic_stage_kind_asset(assets_root: Path) -> None:
    stage_kind_path = (
        assets_root / "registry" / "stage_kinds" / "execution" / "synthetic_worker.json"
    )
    payload = {
        "schema_version": "1.0",
        "kind": "registered_stage_kind",
        "stage_kind_id": "synthetic_worker",
        "plane": "execution",
        "display_name": "Synthetic Worker",
        "default_entrypoint_path": "entrypoints/execution/builder.md",
        "required_skill_paths": ["skills/stage/execution/builder-core/SKILL.md"],
        "suggested_skill_paths": [],
        "running_status_marker": "SYNTHETIC_RUNNING",
        "legal_outcomes": ["SYNTHETIC_COMPLETE", "BLOCKED"],
        "success_outcomes": ["SYNTHETIC_COMPLETE"],
        "failure_outcomes": ["BLOCKED"],
        "allowed_result_classes_by_outcome": {
            "SYNTHETIC_COMPLETE": ["success"],
            "BLOCKED": ["blocked", "recoverable_failure"],
        },
        "allowed_input_artifacts": [],
        "declared_output_artifacts": ["stage_result", "report"],
        "idempotence_policy": "retry_safe_with_key",
        "allowed_overrides": [
            "entrypoint_path",
            "runner_name",
            "model_name",
            "timeout_seconds",
            "attached_skill_additions",
        ],
        "can_start_tasks": True,
        "can_start_specs": False,
        "can_start_incidents": False,
        "recovery_role": None,
        "closure_role": False,
    }
    stage_kind_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_synthetic_graph_loop_asset(assets_root: Path) -> None:
    graph_path = assets_root / "graphs" / "execution" / "synthetic.json"
    payload = {
        "schema_version": "1.0",
        "kind": "graph_loop",
        "loop_id": "execution.synthetic",
        "plane": "execution",
        "nodes": [{"node_id": "synthetic_worker", "stage_kind_id": "synthetic_worker"}],
        "entry_nodes": [{"entry_key": "task", "node_id": "synthetic_worker"}],
        "edges": [
            {
                "edge_id": "synthetic-complete-to-terminal",
                "from_node_id": "synthetic_worker",
                "terminal_state_id": "synthetic_complete",
                "on_outcomes": ["SYNTHETIC_COMPLETE"],
                "kind": "terminal",
            },
            {
                "edge_id": "synthetic-blocked-to-terminal",
                "from_node_id": "synthetic_worker",
                "terminal_state_id": "blocked",
                "on_outcomes": ["BLOCKED"],
                "kind": "terminal",
            },
        ],
        "terminal_states": [
            {
                "terminal_state_id": "synthetic_complete",
                "terminal_class": "success",
                "writes_status": "SYNTHETIC_COMPLETE",
                "emits_artifacts": ["stage_result", "report"],
            },
            {
                "terminal_state_id": "blocked",
                "terminal_class": "blocked",
                "writes_status": "BLOCKED",
                "emits_artifacts": ["stage_result", "report"],
            },
        ],
    }
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _all_nodes(plan: CompiledRunPlan):
    return tuple(node for graph in plan.graphs_by_plane.values() for node in graph.nodes)


def test_compiler_validation_errors_use_project_error_hierarchy() -> None:
    assert issubclass(ConfigurationError, MillraceError)
    assert issubclass(CompilerValidationError, ConfigurationError)


def test_compile_writes_compiled_plan_and_diagnostics_artifacts(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    paths = workspace_paths(workspace_root)
    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.used_last_known_good is False
    assert compiled_plan_path.is_file()
    assert not (paths.state_dir / "compiled_graph_plan.json").exists()
    assert diagnostics_path.is_file()

    persisted_plan = CompiledRunPlan.model_validate_json(compiled_plan_path.read_text(encoding="utf-8"))
    persisted_diagnostics = CompileDiagnostics.model_validate_json(
        diagnostics_path.read_text(encoding="utf-8")
    )

    assert persisted_plan.mode_id == "default_codex"
    assert persisted_plan.loop_ids_by_plane == {
        Plane.EXECUTION: "execution.standard",
        Plane.PLANNING: "planning.standard",
    }
    assert set(persisted_plan.graphs_by_plane) == {Plane.EXECUTION, Plane.PLANNING}
    assert persisted_plan.execution_loop_id == "execution.standard"
    assert persisted_plan.planning_loop_id == "planning.standard"
    assert persisted_plan.learning_graph is None
    assert persisted_plan.learning_trigger_rules == ()
    assert persisted_plan.resolved_assets
    assert {entry.entry_key.value: entry.node_id for entry in persisted_plan.execution_graph.compiled_entries} == {
        "task": "builder"
    }
    assert {entry.entry_key.value: entry.node_id for entry in persisted_plan.planning_graph.compiled_entries} == {
        "incident": "auditor",
        "spec": "planner",
    }
    assert persisted_plan.planning_graph.compiled_completion_entry is not None
    assert persisted_plan.planning_graph.compiled_completion_entry.node_id == "arbiter"
    assert any(ref.startswith("graph_completion_behavior:") for ref in persisted_plan.source_refs)
    assert persisted_diagnostics.ok is True
    assert persisted_diagnostics.mode_id == "default_codex"


def test_compile_materializes_compiled_plan_graph_surface(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    plan = outcome.active_plan
    builder_node = next(node for node in plan.execution_graph.nodes if node.node_id == "builder")
    arbiter_node = next(node for node in plan.planning_graph.nodes if node.node_id == "arbiter")

    assert builder_node.entrypoint_contract_id == "builder.contract.v1"
    assert builder_node.running_status_marker == "BUILDER_RUNNING"
    assert builder_node.allowed_result_classes_by_outcome["BLOCKED"] == (
        ResultClass.BLOCKED,
        ResultClass.RECOVERABLE_FAILURE,
    )
    assert builder_node.declared_output_artifacts == ("stage_result", "report")
    assert arbiter_node.entrypoint_contract_id == "arbiter.contract.v1"
    assert arbiter_node.running_status_marker == "ARBITER_RUNNING"
    assert any(ref.logical_id == "mode:default_codex" for ref in plan.resolved_assets)
    assert any(ref.logical_id == "stage_kind:builder" for ref in plan.resolved_assets)
    assert any(ref.logical_id == "entrypoint:entrypoints/execution/builder.md" for ref in plan.resolved_assets)
    assert {
        (
            policy.policy_id,
            policy.source_node_id,
            policy.on_outcome,
            policy.default_target_node_id,
            policy.metadata_stage_keys,
        )
        for policy in plan.execution_graph.compiled_resume_policies
    } == {
        (
            "execution.troubleshooter.resume",
            "troubleshooter",
            "TROUBLESHOOT_COMPLETE",
            "builder",
            ("resume_stage",),
        ),
        (
            "execution.consultant.resume",
            "consultant",
            "CONSULT_COMPLETE",
            "troubleshooter",
            ("target_stage", "resume_stage"),
        ),
    }
    assert {
        (
            policy.policy_id,
            policy.counter_name.value,
            policy.threshold,
            policy.exhausted_target_node_id,
            policy.exhausted_terminal_state_id,
        )
        for policy in plan.execution_graph.compiled_threshold_policies
    } == {
        (
            "execution.fix-needed.exhaustion",
            "fix_cycle_count",
            2,
            "troubleshooter",
            None,
        ),
        (
            "execution.blocked.recovery",
            "troubleshoot_attempt_count",
            2,
            "consultant",
            None,
        ),
    }
    assert {
        (transition.source_node_id, transition.outcome, transition.target_node_id)
        for transition in plan.execution_graph.compiled_transitions
        if transition.target_node_id is not None
    } >= {
        ("builder", "BUILDER_COMPLETE", "checker"),
        ("checker", "CHECKER_PASS", "updater"),
        ("fixer", "FIXER_COMPLETE", "doublechecker"),
        ("troubleshooter", "TROUBLESHOOT_COMPLETE", "builder"),
    }
    assert plan.planning_graph.completion_behavior is not None
    assert plan.planning_graph.completion_behavior.target_node_id == "arbiter"
    assert plan.planning_graph.compiled_completion_entry is not None
    assert plan.planning_graph.compiled_completion_entry.entry_key.value == "closure_target"
    assert plan.planning_graph.compiled_completion_entry.node_id == "arbiter"
    assert plan.planning_graph.compiled_completion_entry.stage_kind_id == "arbiter"
    assert plan.planning_graph.compiled_completion_entry.request_kind == "closure_target"
    assert plan.planning_graph.compiled_completion_entry.target_selector == "active_closure_target"
    assert plan.execution_graph.transitions
    assert plan.planning_graph.transitions


def test_compile_materializes_configured_recovery_thresholds_into_compiled_plan(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(
            recovery={
                "max_fix_cycles": 5,
                "max_troubleshoot_attempts_before_consult": 4,
                "max_mechanic_attempts": 3,
            }
        ),
        requested_mode_id="standard_plain",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert {
        (policy.policy_id, policy.threshold)
        for policy in outcome.active_plan.execution_graph.compiled_threshold_policies
    } == {
        ("execution.fix-needed.exhaustion", 5),
        ("execution.blocked.recovery", 4),
    }
    assert {
        (policy.policy_id, policy.threshold)
        for policy in outcome.active_plan.planning_graph.compiled_threshold_policies
    } == {
        ("planning.blocked.recovery", 3),
    }


def test_compile_materializes_workspace_local_mode_contract(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    _write_workspace_local_graph_mode_assets(assets_root)
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root, assets_root=assets_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(recovery={"max_fix_cycles": 5}),
        requested_mode_id="local_review_codex",
        assets_root=workspace_root / "millrace-agents",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "local_review_codex"
    assert outcome.active_plan.execution_loop_id == "execution.local_review"
    assert outcome.active_plan.planning_loop_id == "planning.local_review"

    execution_nodes = {node.node_id: node for node in outcome.active_plan.execution_graph.nodes}
    fix_threshold = next(
        policy
        for policy in outcome.active_plan.execution_graph.compiled_threshold_policies
        if policy.policy_id == "execution.fix-needed.exhaustion"
    )

    assert execution_nodes["checker"].model_name == "gpt-5.4"
    assert fix_threshold.threshold == 5
    assert {
        (asset.asset_family, asset.logical_id, asset.compile_time_path)
        for asset in outcome.active_plan.resolved_assets
        if asset.logical_id in {
            "mode:local_review_codex",
            "graph_loop:execution.local_review",
            "graph_loop:planning.local_review",
        }
    } == {
        ("mode", "mode:local_review_codex", "modes/local_review_codex.json"),
        ("graph_loop", "graph_loop:execution.local_review", "graphs/execution/local_review.json"),
        ("graph_loop", "graph_loop:planning.local_review", "graphs/planning/local_review.json"),
    }


def test_compile_materializes_learning_mode_planes_and_trigger_rules(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(
            stages={
                "professor": {
                    "model": "gpt-5.4",
                    "model_reasoning_effort": "high",
                },
            }
        ),
        requested_mode_id="learning_codex",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "learning_codex"
    assert outcome.active_plan.loop_ids_by_plane == {
        Plane.EXECUTION: "execution.standard",
        Plane.PLANNING: "planning.standard",
        Plane.LEARNING: "learning.standard",
    }
    assert outcome.active_plan.learning_graph is not None
    assert [node.node_id for node in outcome.active_plan.learning_graph.nodes] == [
        "analyst",
        "professor",
        "curator",
    ]
    learning_nodes = {node.node_id: node for node in outcome.active_plan.learning_graph.nodes}
    assert learning_nodes["professor"].model_name == "gpt-5.4"
    assert learning_nodes["professor"].model_reasoning_effort == "high"
    assert {node.runner_name for node in _all_nodes(outcome.active_plan)} == {"codex_cli"}
    assert {
        (
            rule.source_stage.value,
            rule.on_terminal_results,
            rule.target_stage.value,
            rule.requested_action,
        )
        for rule in outcome.active_plan.learning_trigger_rules
    } == {
        ("doublechecker", ("DOUBLECHECK_PASS",), "curator", "improve"),
        ("troubleshooter", ("TROUBLESHOOT_COMPLETE", "BLOCKED"), "analyst", "improve"),
        ("consultant", ("CONSULT_COMPLETE", "NEEDS_PLANNING", "BLOCKED"), "analyst", "improve"),
    }
    assert "graph_loop:learning.standard" in outcome.active_plan.source_refs


def test_preview_graph_loop_plan_compiles_synthetic_discovered_loop(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    _write_synthetic_stage_kind_asset(assets_root)
    _write_synthetic_graph_loop_asset(assets_root)

    graph_plan = preview_graph_loop_plan(
        "execution.synthetic",
        config=RuntimeConfig(),
        assets_root=assets_root,
    )

    entry_nodes = {entry.entry_key.value: entry.node_id for entry in graph_plan.entry_nodes}

    assert graph_plan.loop_id == "execution.synthetic"
    assert graph_plan.plane is Plane.EXECUTION
    assert [node.stage_kind_id for node in graph_plan.nodes] == ["synthetic_worker"]
    assert entry_nodes == {"task": "synthetic_worker"}
    assert {state.terminal_state_id for state in graph_plan.terminal_states} == {
        "synthetic_complete",
        "blocked",
    }


def test_standard_plain_alias_and_default_codex_compile_to_identical_plan_ids(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    alias_outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    canonical_outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
    )

    assert alias_outcome.diagnostics.ok is True
    assert canonical_outcome.diagnostics.ok is True
    assert alias_outcome.active_plan is not None
    assert canonical_outcome.active_plan is not None
    assert alias_outcome.active_plan.mode_id == "default_codex"
    assert canonical_outcome.active_plan.mode_id == "default_codex"
    assert alias_outcome.active_plan.compiled_plan_id == canonical_outcome.active_plan.compiled_plan_id


def test_default_pi_compiles_with_pi_runner_bound_for_every_node(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_pi",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.mode_id == "default_pi"
    assert {node.runner_name for node in _all_nodes(outcome.active_plan)} == {"pi_rpc"}


def test_compile_resolves_minimal_required_stage_skills(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    required_by_stage = {
        node.node_id: node.required_skill_paths
        for node in _all_nodes(outcome.active_plan)
    }

    assert len(required_by_stage) == 12
    assert required_by_stage["builder"] == ("skills/stage/execution/builder-core/SKILL.md",)
    assert required_by_stage["checker"] == ("skills/stage/execution/checker-core/SKILL.md",)
    assert required_by_stage["planner"] == ("skills/stage/planning/planner-core/SKILL.md",)
    assert required_by_stage["auditor"] == ("skills/stage/planning/auditor-core/SKILL.md",)
    assert required_by_stage["arbiter"] == ("skills/stage/planning/arbiter-core/SKILL.md",)


def test_compile_plan_identity_changes_when_graph_completion_behavior_changes(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    baseline = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )

    assert baseline.diagnostics.ok is True
    assert baseline.active_plan is not None

    assets_root = _copy_builtin_assets(tmp_path / "mutated")
    planning_graph_path = assets_root / "graphs" / "planning" / "standard.json"
    payload = json.loads(planning_graph_path.read_text(encoding="utf-8"))
    payload["completion_behavior"]["skip_if_already_closed"] = False
    planning_graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    mutated = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert mutated.diagnostics.ok is True
    assert mutated.active_plan is not None
    assert mutated.active_plan.compiled_plan_id != baseline.active_plan.compiled_plan_id


def test_compile_uses_one_hour_default_stage_timeout_when_stage_config_omits_it(
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
    assert {node.timeout_seconds for node in _all_nodes(outcome.active_plan)} == {3600}


def test_compile_surfaces_stage_skill_attachments_without_role_overlays(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    workspace_skill = workspace_paths(workspace_root).runtime_root / "skills" / "execution" / "builder.md"
    workspace_skill.parent.mkdir(parents=True, exist_ok=True)
    workspace_skill.write_text("builder attached skill\n", encoding="utf-8")

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_skill_additions"] = {
        "builder": ["skills/execution/builder.md"],
    }
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    builder_plan = next(node for node in outcome.active_plan.execution_graph.nodes if node.node_id == "builder")

    assert builder_plan.required_skill_paths == ("skills/stage/execution/builder-core/SKILL.md",)
    assert builder_plan.attached_skill_additions == ("skills/execution/builder.md",)
    assert "role_overlays" not in builder_plan.model_dump(mode="json")


def test_compile_rejects_invalid_entrypoint_override_deterministically(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_entrypoint_overrides"] = {"builder": "roles/not-an-entrypoint.md"}
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert outcome.used_last_known_good is False
    assert outcome.diagnostics.errors == (
        "Invalid entrypoint override for stage `builder`: roles/not-an-entrypoint.md",
    )


def test_compile_rejects_entrypoint_override_path_traversal(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_entrypoint_overrides"] = {"builder": "../entrypoints/execution/builder.md"}
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert outcome.used_last_known_good is False
    assert outcome.diagnostics.errors == (
        "Invalid entrypoint override for stage `builder`: ../entrypoints/execution/builder.md",
    )


def test_compile_ignores_removed_stage_role_overlay_field_in_mode_assets(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_role_overlays"] = {"builder": ["roles/execution/builder_advisory.md"]}
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert outcome.used_last_known_good is False
    assert outcome.diagnostics.errors == (
        "Invalid mode definition in asset: "
        f"{assets_root / 'modes' / 'default_codex.json'}",
    )


def test_recompile_failure_keeps_last_known_good_plan(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    initial = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert initial.diagnostics.ok is True
    assert initial.active_plan is not None

    paths = workspace_paths(workspace_root)
    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    baseline_plan_text = compiled_plan_path.read_text(encoding="utf-8")

    assets_root = _copy_builtin_assets(tmp_path / "recompile")
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["planning_loop_id"] = "planning.unknown"
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    failed = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert failed.diagnostics.ok is False
    assert failed.active_plan is not None
    assert failed.used_last_known_good is True
    assert failed.active_plan.compiled_plan_id == initial.active_plan.compiled_plan_id
    assert compiled_plan_path.read_text(encoding="utf-8") == baseline_plan_text

    diagnostics_path = paths.state_dir / "compile_diagnostics.json"
    diagnostics = CompileDiagnostics.model_validate_json(diagnostics_path.read_text(encoding="utf-8"))
    assert diagnostics.ok is False
    assert diagnostics.mode_id == "default_codex"
    assert diagnostics.errors[0] == "Unknown graph loop id: planning.unknown"


def test_inspect_workspace_plan_currentness_detects_current_and_stale_inputs(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)

    compiled = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=paths.runtime_root,
    )
    assert compiled.active_plan is not None

    current = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=paths.runtime_root,
    )
    assert current.state == "current"
    assert current.persisted_plan_id == compiled.active_plan.compiled_plan_id

    (paths.runtime_root / "entrypoints" / "execution" / "builder.md").write_text(
        "stale builder entrypoint\n",
        encoding="utf-8",
    )
    stale = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=paths.runtime_root,
    )

    assert stale.state == "stale"
    assert stale.persisted_plan_id == compiled.active_plan.compiled_plan_id


def test_inspect_workspace_plan_currentness_ignores_unreferenced_asset_changes(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)

    compiled = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=paths.runtime_root,
    )
    assert compiled.active_plan is not None

    (paths.runtime_root / "skills" / "unused-skill.md").write_text(
        "unused skill drift\n",
        encoding="utf-8",
    )
    (paths.runtime_root / "entrypoints" / "execution" / "unused.md").write_text(
        "unused entrypoint drift\n",
        encoding="utf-8",
    )

    current = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=paths.runtime_root,
    )

    assert current.state == "current"
    assert current.persisted_plan_id == compiled.active_plan.compiled_plan_id


def test_inspect_workspace_plan_currentness_detects_attached_skill_drift(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)
    workspace_skill = paths.runtime_root / "skills" / "execution" / "builder.md"
    workspace_skill.parent.mkdir(parents=True, exist_ok=True)
    workspace_skill.write_text("builder attached skill\n", encoding="utf-8")

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_skill_additions"] = {
        "builder": ["skills/execution/builder.md"],
    }
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    compiled = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )
    assert compiled.active_plan is not None

    workspace_skill.write_text("attached skill drift\n", encoding="utf-8")

    stale = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert stale.state == "stale"
    assert stale.persisted_plan_id == compiled.active_plan.compiled_plan_id


def test_inspect_workspace_plan_currentness_detects_missing_attached_skill_becoming_present(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)

    assets_root = _copy_builtin_assets(tmp_path)
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["stage_skill_additions"] = {
        "builder": ["skills/execution/builder.md"],
    }
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    compiled = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )
    assert compiled.active_plan is not None

    current = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )
    assert current.state == "current"

    workspace_skill = paths.runtime_root / "skills" / "execution" / "builder.md"
    workspace_skill.parent.mkdir(parents=True, exist_ok=True)
    workspace_skill.write_text("late attached skill\n", encoding="utf-8")

    stale = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert stale.state == "stale"
    assert stale.persisted_plan_id == compiled.active_plan.compiled_plan_id


def test_compile_refuses_stale_last_known_good_when_requested(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)

    initial = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=paths.runtime_root,
    )
    assert initial.diagnostics.ok is True
    assert initial.active_plan is not None

    mode_path = paths.runtime_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["loop_ids_by_plane"]["planning"] = "planning.unknown"
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    failed = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=paths.runtime_root,
        refuse_stale_last_known_good=True,
    )

    assert failed.diagnostics.ok is False
    assert failed.active_plan is None
    assert failed.used_last_known_good is False
