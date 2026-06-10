from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest

from millrace_ai.architecture import (
    GraphLoopCounterName,
    GraphLoopNodeDefinition,
    GraphLoopTerminalClass,
)
from millrace_ai.contracts import Plane
from millrace_ai.errors import AssetValidationError, MillraceError
from millrace_ai.loop_graphs import (
    SHIPPED_GRAPH_LOOP_IDS,
    GraphLoopAssetError,
    discover_graph_loop_definitions,
    load_builtin_graph_loop_definition,
    load_builtin_graph_loop_definitions,
    load_graph_loop_definition,
)


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _write_synthetic_stage_kind_asset(assets_root: Path) -> None:
    stage_kind_path = (
        assets_root / "registry" / "stage_kinds" / "execution" / "synthetic_worker.json"
    )
    payload = {
        "schema_version": "1.0",
        "kind": "registered_stage_kind",
        "stage_kind_id": "synthetic_worker",
        "runtime_stage": "builder",
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
        "allowed_work_item_families": ["task"],
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
                "terminal_action_id": "complete_work_item",
                "writes_status": "SYNTHETIC_COMPLETE",
                "emits_artifacts": ["stage_result", "report"],
            },
            {
                "terminal_state_id": "blocked",
                "terminal_class": "blocked",
                "terminal_action_id": "block_work_item",
                "writes_status": "BLOCKED",
                "emits_artifacts": ["stage_result", "report"],
            },
        ],
    }
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_loop_graphs_module_is_assets_facade() -> None:
    loop_graphs_facade = importlib.import_module("millrace_ai.loop_graphs")
    loop_graphs_module = importlib.import_module("millrace_ai.assets.loop_graphs")
    assets_public_module = importlib.import_module("millrace_ai.assets")

    assert (
        loop_graphs_facade.load_builtin_graph_loop_definition
        is loop_graphs_module.load_builtin_graph_loop_definition
    )
    assert (
        loop_graphs_facade.load_builtin_graph_loop_definitions
        is loop_graphs_module.load_builtin_graph_loop_definitions
    )
    assert loop_graphs_facade.SHIPPED_GRAPH_LOOP_IDS == loop_graphs_module.SHIPPED_GRAPH_LOOP_IDS
    assert (
        assets_public_module.load_builtin_graph_loop_definition
        is loop_graphs_module.load_builtin_graph_loop_definition
    )
    assert assets_public_module.GraphLoopAssetError is loop_graphs_module.GraphLoopAssetError


def test_builtin_graph_loops_load_and_validate() -> None:
    graph_loops = load_builtin_graph_loop_definitions()

    assert [graph.loop_id for graph in graph_loops] == list(SHIPPED_GRAPH_LOOP_IDS)
    assert {graph.plane for graph in graph_loops} == {
        Plane.EXECUTION,
        Plane.PLANNING,
        Plane.LEARNING,
    }
    assert all(graph.nodes for graph in graph_loops)
    assert all(graph.edges for graph in graph_loops)
    assert all(graph.terminal_states for graph in graph_loops)


def test_builtin_graph_nodes_declare_request_context_authority() -> None:
    for graph in load_builtin_graph_loop_definitions():
        for node in graph.nodes:
            assert node.request_context_profile_id is not None, (
                f"{graph.loop_id}:{node.node_id} is missing request_context_profile_id"
            )
            assert node.context_render_plan_id is not None, (
                f"{graph.loop_id}:{node.node_id} is missing context_render_plan_id"
            )


def test_shipped_graph_loop_ids_are_stable() -> None:
    assert SHIPPED_GRAPH_LOOP_IDS == (
        "execution.standard",
        "execution.with_integrator",
        "learning.standard",
        "planning.standard",
        "planning.blueprint",
    )


def test_specific_builtin_graph_loop_fields_are_expected() -> None:
    execution = load_builtin_graph_loop_definition("execution.standard")
    planning = load_builtin_graph_loop_definition("planning.standard")
    execution_entry_nodes = {entry.entry_key.value: entry.node_id for entry in execution.entry_nodes}
    planning_entry_nodes = {entry.entry_key.value: entry.node_id for entry in planning.entry_nodes}
    execution_edges = {edge.edge_id: edge for edge in execution.edges}
    execution_dynamic = execution.dynamic_policies
    planning_dynamic = planning.dynamic_policies

    assert execution.plane is Plane.EXECUTION
    assert execution_entry_nodes == {"task": "builder"}
    assert [node.stage_kind_id for node in execution.nodes][:3] == ["builder", "checker", "fixer"]
    assert execution_edges["troubleshooter-complete-to-builder"].to_node_id == "builder"
    assert execution_edges["troubleshooter-blocked-to-consultant"].to_node_id == "consultant"
    assert execution_dynamic is not None
    assert {policy.policy_id for policy in execution_dynamic.resume_policies} == {
        "execution.troubleshooter.resume",
        "execution.consultant.resume",
    }
    assert {policy.policy_id for policy in execution_dynamic.threshold_policies} == {
        "execution.fix-needed.exhaustion",
        "execution.blocked.recovery",
    }
    blocked_policy = next(
        policy
        for policy in execution_dynamic.threshold_policies
        if policy.policy_id == "execution.blocked.recovery"
    )
    assert blocked_policy.counter_name is GraphLoopCounterName.TROUBLESHOOT_ATTEMPT_COUNT
    assert blocked_policy.exhausted_target_node_id == "consultant"
    assert execution.runtime_failure_recovery is not None
    assert execution.runtime_failure_recovery.default_repair_node_id == "troubleshooter"
    assert execution.runtime_failure_recovery.counter_name is GraphLoopCounterName.TROUBLESHOOT_ATTEMPT_COUNT
    assert {state.terminal_state_id for state in execution.terminal_states} == {
        "update_complete",
        "needs_planning",
        "blocked",
    }

    assert planning.plane is Plane.PLANNING
    assert planning_entry_nodes == {"incident": "auditor", "probe": "recon", "spec": "planner"}
    assert planning_dynamic is not None
    assert {policy.policy_id for policy in planning_dynamic.resume_policies} == {
        "planning.mechanic.resume"
    }
    assert {policy.policy_id for policy in planning_dynamic.threshold_policies} == {
        "planning.blocked.recovery"
    }
    assert planning.completion_behavior is not None
    assert planning.runtime_failure_recovery is not None
    assert planning.runtime_failure_recovery.default_repair_node_id == "mechanic"
    assert planning.runtime_failure_recovery.counter_name is GraphLoopCounterName.MECHANIC_ATTEMPT_COUNT
    assert planning.completion_behavior.target_node_id == "arbiter"
    assert planning.completion_behavior.root_source_policy.accepted_kinds == (
        "idea",
        "probe",
        "manual",
        "spec",
        "incident",
    )
    assert planning.completion_behavior.on_gap_terminal_state_id == "remediation_needed"
    assert any(
        state.terminal_class is GraphLoopTerminalClass.FOLLOWUP_NEEDED
        for state in planning.terminal_states
    )


def test_integrated_execution_graph_runs_integrator_after_builder() -> None:
    execution = load_builtin_graph_loop_definition("execution.with_integrator")
    entry_nodes = {entry.entry_key.value: entry.node_id for entry in execution.entry_nodes}
    edges = {edge.edge_id: edge for edge in execution.edges}
    blocked_policy = next(
        policy
        for policy in execution.dynamic_policies.threshold_policies
        if policy.policy_id == "execution.blocked.recovery"
    )

    assert execution.plane is Plane.EXECUTION
    assert entry_nodes == {"task": "builder"}
    assert [node.stage_kind_id for node in execution.nodes][:3] == [
        "builder",
        "integrator",
        "checker",
    ]
    assert edges["builder-complete-to-integrator"].to_node_id == "integrator"
    assert edges["integrator-complete-to-checker"].to_node_id == "checker"
    assert edges["integrator-blocked-to-troubleshooter"].to_node_id == "troubleshooter"
    assert "integrator" in blocked_policy.source_node_ids


def test_learning_graph_loop_exposes_learning_request_entrypoint() -> None:
    learning = load_builtin_graph_loop_definition("learning.standard")

    assert learning.plane is Plane.LEARNING
    assert {entry.entry_key.value: entry.node_id for entry in learning.entry_nodes} == {
        "learning_request": "analyst"
    }
    assert [node.stage_kind_id for node in learning.nodes] == [
        "analyst",
        "professor",
        "curator",
        "librarian",
    ]
    assert {state.terminal_state_id for state in learning.terminal_states} == {
        "analyst_noop",
        "learning_complete",
        "professor_noop",
        "curator_noop",
        "librarian_complete",
        "librarian_noop",
        "blocked",
    }
    edges = {edge.edge_id: edge for edge in learning.edges}
    assert edges["librarian-complete-to-terminal-librarian-complete"].terminal_state_id == "librarian_complete"
    assert edges["librarian-noop-to-terminal-librarian-noop"].terminal_state_id == "librarian_noop"
    assert edges["librarian-blocked-to-terminal-blocked"].terminal_state_id == "blocked"
    assert all(
        edge.to_node_id not in {"analyst", "professor", "curator"}
        for edge in learning.edges
        if edge.from_node_id == "librarian"
    )
    assert {
        state.terminal_class
        for state in learning.terminal_states
        if state.terminal_state_id.endswith("_noop")
    } == {GraphLoopTerminalClass.NO_OP}


def test_graph_driven_planning_graph_routes_drafts_through_contract_review() -> None:
    planning = load_builtin_graph_loop_definition("planning.blueprint")
    entry_nodes = {entry.entry_key.value: entry.node_id for entry in planning.entry_nodes}
    nodes = {node.node_id: node for node in planning.nodes}
    edges = {edge.edge_id: edge for edge in planning.edges}
    terminal_states = {state.terminal_state_id: state for state in planning.terminal_states}

    assert planning.plane is Plane.PLANNING
    assert entry_nodes == {
        "probe": "recon",
        "spec": "planner",
        "incident": "auditor",
        "blueprint_draft": "contractor_blueprint",
    }
    assert nodes["manager_blueprint"].stage_kind_id == "manager_blueprint"
    assert nodes["contractor_blueprint"].stage_kind_id == "contractor_blueprint"
    assert nodes["evaluator_blueprint"].stage_kind_id == "evaluator_blueprint"
    assert nodes["mechanic_blueprint"].stage_kind_id == "mechanic_blueprint"

    assert edges["planner-complete-to-manager-blueprint"].to_node_id == "manager_blueprint"
    assert edges["manager-blueprint-complete-to-terminal"].terminal_state_id == (
        "manager_blueprint_complete"
    )
    assert edges["contractor-candidate-ready-to-evaluator"].to_node_id == "evaluator_blueprint"
    assert edges["evaluator-approved-to-terminal"].terminal_state_id == "blueprint_approved"
    assert edges["evaluator-rejected-to-contractor"].to_node_id == "contractor_blueprint"
    assert edges["evaluator-blocked-to-mechanic-blueprint"].to_node_id == "mechanic_blueprint"

    assert terminal_states["manager_blueprint_complete"].terminal_class is GraphLoopTerminalClass.SUCCESS
    assert terminal_states["manager_blueprint_complete"].emits_artifacts == (
        "stage_result",
        "blueprint_manifest",
        "blueprint_drafts",
    )
    assert terminal_states["blueprint_approved"].terminal_class is GraphLoopTerminalClass.SUCCESS
    assert terminal_states["blueprint_approved"].emits_artifacts == (
        "stage_result",
        "blueprint_evaluation",
        "generated_task",
    )
    assert planning.runtime_failure_recovery is not None
    assert planning.runtime_failure_recovery.default_repair_node_id == "mechanic_blueprint"
    assert planning.runtime_failure_recovery.counter_name is GraphLoopCounterName.MECHANIC_ATTEMPT_COUNT
    assert planning.completion_behavior is not None
    assert planning.completion_behavior.target_node_id == "arbiter"


def test_graph_loop_node_declares_thinking_level_override() -> None:
    node = GraphLoopNodeDefinition(
        node_id="builder",
        stage_kind_id="builder",
        thinking_level="high",
    )

    assert node.thinking_level == "high"
    assert "thinking_level" in node.declared_override_names()


def test_graph_loop_asset_errors_use_project_error_hierarchy() -> None:
    assert issubclass(AssetValidationError, MillraceError)
    assert issubclass(GraphLoopAssetError, AssetValidationError)


def test_unknown_graph_loop_fails_deterministically() -> None:
    with pytest.raises(GraphLoopAssetError, match=r"^Unknown graph loop id: execution\.custom$"):
        load_builtin_graph_loop_definition("execution.custom")


def test_builtin_graph_loop_loader_accepts_workspace_local_graph(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    _write_synthetic_stage_kind_asset(assets_root)
    _write_synthetic_graph_loop_asset(assets_root)

    synthetic = load_builtin_graph_loop_definition("execution.synthetic", assets_root=assets_root)

    assert synthetic.loop_id == "execution.synthetic"
    assert synthetic.nodes[0].stage_kind_id == "synthetic_worker"


def test_invalid_graph_loop_json_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    graph_path.write_text("{not-valid-json", encoding="utf-8")

    with pytest.raises(GraphLoopAssetError, match="Invalid JSON in graph loop asset"):
        load_builtin_graph_loop_definition("execution.standard", assets_root=assets_root)


def test_unknown_stage_kind_reference_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    payload["nodes"][0]["stage_kind_id"] = "no_such_stage"
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(GraphLoopAssetError, match="references unknown stage_kind_id"):
        load_builtin_graph_loop_definition("execution.standard", assets_root=assets_root)


def test_graph_loop_surfaces_invalid_stage_kind_result_class_policy(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    stage_kind_path = assets_root / "registry" / "stage_kinds" / "execution" / "builder.json"
    payload = json.loads(stage_kind_path.read_text(encoding="utf-8"))
    payload.pop("allowed_result_classes_by_outcome", None)
    stage_kind_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(GraphLoopAssetError, match="cannot validate stage kinds"):
        load_builtin_graph_loop_definition("execution.standard", assets_root=assets_root)


def test_illegal_edge_outcome_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    payload["edges"][0]["on_outcomes"] = ["PLANNER_COMPLETE"]
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(GraphLoopAssetError, match="declares illegal outcome"):
        load_builtin_graph_loop_definition("execution.standard", assets_root=assets_root)


def test_broken_edge_target_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    graph_path = assets_root / "graphs" / "planning" / "standard.json"
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    payload["edges"][0]["to_node_id"] = "missing_node"
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(GraphLoopAssetError, match="references unknown to_node_id"):
        load_builtin_graph_loop_definition("planning.standard", assets_root=assets_root)


def test_recon_handoff_edge_to_planner_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    graph_path = assets_root / "graphs" / "planning" / "standard.json"
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    for edge in payload["edges"]:
        if edge["edge_id"] == "recon-to-planning-to-terminal-recon-to-planning":
            edge.pop("terminal_state_id")
            edge["to_node_id"] = "planner"
            edge["kind"] = "normal"
            break
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(
        GraphLoopAssetError,
        match="Handoff terminal-only outcomes must target terminal states",
    ):
        load_builtin_graph_loop_definition("planning.standard", assets_root=assets_root)


def test_missing_terminal_action_id_fails_asset_validation(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    payload["terminal_states"][0].pop("terminal_action_id", None)
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(GraphLoopAssetError, match="terminal_action_id"):
        load_builtin_graph_loop_definition("execution.standard", assets_root=assets_root)


def test_missing_completion_root_source_policy_fails_asset_validation(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    graph_path = assets_root / "graphs" / "planning" / "standard.json"
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    payload["completion_behavior"].pop("root_source_policy", None)
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(GraphLoopAssetError, match="root_source_policy"):
        load_builtin_graph_loop_definition("planning.standard", assets_root=assets_root)


def test_invalid_resume_policy_target_fails_deterministically(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    payload["dynamic_policies"]["resume_policies"][0]["default_target_node_id"] = "missing_node"
    graph_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(
        GraphLoopAssetError,
        match="resume policy execution.troubleshooter.resume references unknown default_target_node_id",
    ):
        load_builtin_graph_loop_definition("execution.standard", assets_root=assets_root)


def test_fixture_graph_loops_are_discoverable() -> None:
    discovered = {graph.loop_id: graph for graph in discover_graph_loop_definitions()}

    for loop_id in (
        "execution.minimal_three_plane",
        "planning.minimal_three_plane",
        "learning.minimal_three_plane",
    ):
        assert loop_id in discovered
        assert load_graph_loop_definition(loop_id) == discovered[loop_id]


def test_fixture_graph_loops_canonical_entry_keys_and_minimal_terminal_wiring() -> None:
    execution = load_graph_loop_definition("execution.minimal_three_plane")
    planning = load_graph_loop_definition("planning.minimal_three_plane")
    learning = load_graph_loop_definition("learning.minimal_three_plane")

    execution_entry_nodes = {entry.entry_key.value: entry.node_id for entry in execution.entry_nodes}
    planning_entry_nodes = {entry.entry_key.value: entry.node_id for entry in planning.entry_nodes}
    learning_entry_nodes = {entry.entry_key.value: entry.node_id for entry in learning.entry_nodes}

    assert execution.plane is Plane.EXECUTION
    assert execution_entry_nodes == {"task": "basic_worker"}
    assert [node.stage_kind_id for node in execution.nodes] == ["basic_worker"]
    assert {state.terminal_state_id for state in execution.terminal_states} == {
        "worker_complete",
        "blocked",
    }
    assert {
        state.terminal_state_id: state.terminal_class
        for state in execution.terminal_states
    } == {
        "worker_complete": GraphLoopTerminalClass.SUCCESS,
        "blocked": GraphLoopTerminalClass.BLOCKED,
    }

    assert planning.plane is Plane.PLANNING
    assert planning_entry_nodes == {"spec": "basic_planner"}
    assert [node.stage_kind_id for node in planning.nodes] == ["basic_planner"]
    assert {state.terminal_state_id for state in planning.terminal_states} == {
        "planner_complete",
        "blocked",
    }
    assert {
        state.terminal_state_id: state.terminal_class
        for state in planning.terminal_states
    } == {
        "planner_complete": GraphLoopTerminalClass.SUCCESS,
        "blocked": GraphLoopTerminalClass.BLOCKED,
    }

    assert learning.plane is Plane.LEARNING
    assert learning_entry_nodes == {"learning_request": "basic_learner"}
    assert [node.stage_kind_id for node in learning.nodes] == ["basic_learner"]
    assert {state.terminal_state_id for state in learning.terminal_states} == {
        "learner_complete",
        "learner_noop",
        "blocked",
    }
    assert {
        state.terminal_state_id: state.terminal_class
        for state in learning.terminal_states
    } == {
        "learner_complete": GraphLoopTerminalClass.SUCCESS,
        "learner_noop": GraphLoopTerminalClass.NO_OP,
        "blocked": GraphLoopTerminalClass.BLOCKED,
    }


def test_discover_graph_loop_definitions_includes_synthetic_graph_loop(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    _write_synthetic_stage_kind_asset(assets_root)
    _write_synthetic_graph_loop_asset(assets_root)

    discovered = discover_graph_loop_definitions(assets_root=assets_root)
    discovered_ids = [graph.loop_id for graph in discovered]
    synthetic = load_graph_loop_definition("execution.synthetic", assets_root=assets_root)

    assert "execution.synthetic" in discovered_ids
    assert synthetic.loop_id == "execution.synthetic"
    assert synthetic.nodes[0].stage_kind_id == "synthetic_worker"
    assert synthetic.entry_nodes[0].node_id == "synthetic_worker"
