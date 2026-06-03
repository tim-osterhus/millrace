from __future__ import annotations

import json
import shutil
from pathlib import Path

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import bootstrap_workspace


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _compile_with_assets(tmp_path: Path, assets_root: Path):
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    return compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _mutate_lifecycle_plan(assets_root: Path, plan_id: str, **updates: object) -> None:
    path = assets_root / "registry" / "lifecycle_mutation_plans" / "default_lifecycle_mutations.json"
    payload = _load_json(path)
    for definition in payload["definitions"]:
        if definition["plan_id"] == plan_id:
            definition.update(updates)
            _write_json(path, payload)
            return
    raise AssertionError(f"missing lifecycle plan {plan_id}")


def _append_lifecycle_plan(assets_root: Path, definition: dict) -> None:
    path = assets_root / "registry" / "lifecycle_mutation_plans" / "default_lifecycle_mutations.json"
    payload = _load_json(path)
    payload["definitions"].append(definition)
    _write_json(path, payload)


def _append_terminal_action(assets_root: Path, definition: dict) -> None:
    path = assets_root / "registry" / "terminal_actions" / "default_terminal_actions.json"
    payload = _load_json(path)
    payload["definitions"].append(definition)
    _write_json(path, payload)


def _add_updater_alias_terminal(
    assets_root: Path,
    *,
    terminal_action_id: str = "complete_work_item",
) -> None:
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = _load_json(graph_path)
    payload["nodes"].append(
        {
            "node_id": "updater_alias",
            "stage_kind_id": "updater",
            "request_context_profile_id": "updater.default",
            "context_render_plan_id": "stage_request.default.v1",
        }
    )
    payload["edges"].append(
        {
            "edge_id": "updater-alias-complete-to-terminal",
            "from_node_id": "updater_alias",
            "terminal_state_id": "updater_alias_complete",
            "on_outcomes": ["UPDATE_COMPLETE"],
            "kind": "terminal",
        }
    )
    payload["edges"].append(
        {
            "edge_id": "updater-alias-blocked-to-troubleshooter",
            "from_node_id": "updater_alias",
            "to_node_id": "troubleshooter",
            "on_outcomes": ["BLOCKED"],
        }
    )
    payload["terminal_states"].append(
        {
            "terminal_state_id": "updater_alias_complete",
            "terminal_class": "success",
            "terminal_action_id": terminal_action_id,
            "writes_status": "UPDATE_COMPLETE",
            "router_reason": "updater_alias_complete",
            "emits_artifacts": ["stage_result", "report"],
        }
    )
    _write_json(graph_path, payload)


def _diagnostic_text(outcome) -> str:
    return "\n".join((*outcome.diagnostics.errors, *outcome.diagnostics.warnings))


def test_compile_requires_terminal_state_action_id(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = _load_json(graph_path)
    payload["terminal_states"][0].pop("terminal_action_id", None)
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "terminal state update_complete must declare terminal_action_id"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_terminal_state_unknown_action_id(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = _load_json(graph_path)
    payload["terminal_states"][0]["terminal_action_id"] = "missing_terminal_action"
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "terminal state update_complete references unknown terminal action "
        "missing_terminal_action"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_terminal_state_action_class_mismatch(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = _load_json(graph_path)
    payload["terminal_states"][0]["terminal_action_id"] = "block_work_item"
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "terminal state update_complete uses terminal action block_work_item "
        "with class blocked but state class is success"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_unknown_terminal_action_runtime_operation_id(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    actions_path = assets_root / "registry" / "terminal_actions" / "default_terminal_actions.json"
    payload = _load_json(actions_path)
    payload["definitions"][0]["runtime_operation_id"] = "recon.missing_operation"
    _write_json(actions_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "terminal action complete_work_item references unknown runtime operation "
        "recon.missing_operation"
    ) in _diagnostic_text(outcome)


def test_compile_accepts_recon_terminal_action_runtime_operation_ids(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    actions = outcome.active_plan.terminal_actions_by_id
    assert actions["recon_enqueue_task"].runtime_operation_id == "recon.enqueue_task"
    assert actions["recon_enqueue_spec"].runtime_operation_id == "recon.enqueue_spec"
    assert actions["recon_noop"].runtime_operation_id == "recon.noop"
    assert actions["recon_block_work_item"].runtime_operation_id == "recon.block_work_item"


def test_compile_accepts_any_lifecycle_source_family_and_edge_context(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    complete_plan = outcome.active_plan.lifecycle_mutation_plans_by_id["complete_work_item"]
    assert complete_plan.source_family_scope == "any"
    assert complete_plan.source_scope == "any"
    assert complete_plan.outcome_scope == "any"
    assert "graph_transition" in complete_plan.applicability_contexts
    block_plan = outcome.active_plan.lifecycle_mutation_plans_by_id["block_work_item"]
    assert block_plan.outcome_scope == "any"


def test_compile_rejects_terminal_action_lifecycle_source_node_mismatch(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _mutate_lifecycle_plan(
        assets_root,
        "complete_work_item",
        source_scope="graph_node",
        source_graph_node_id="builder",
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "source_graph_node_id builder does not apply" in _diagnostic_text(outcome)


def test_compile_accepts_stage_kind_scope_for_distinct_graph_nodes(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _add_updater_alias_terminal(assets_root, terminal_action_id="test_alias_complete")
    _append_lifecycle_plan(
        assets_root,
        {
            "schema_version": "1.0",
            "kind": "lifecycle_mutation_plan",
            "plan_id": "test_alias_complete",
            "source_scope": "stage_kind",
            "source_stage_kind_id": "updater",
            "outcome_scope": "outcome",
            "outcome_id": "UPDATE_COMPLETE",
            "source_family_scope": "any",
            "applicability_contexts": ["graph_transition"],
            "owner": "terminal_action",
            "source_from_state": "active",
            "source_to_state": "done",
            "ordering": "after_route",
            "lifecycle_action_id": "complete",
        },
    )
    _append_terminal_action(
        assets_root,
        {
            "schema_version": "1.0",
            "kind": "terminal_action",
            "terminal_action_id": "test_alias_complete",
            "terminal_class": "success",
            "lifecycle_mutation_plan_id": "test_alias_complete",
            "router_consequence": "idle",
        },
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None


def test_compile_rejects_stage_kind_target_when_graph_node_scope_required(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _add_updater_alias_terminal(assets_root, terminal_action_id="test_alias_complete")
    _append_lifecycle_plan(
        assets_root,
        {
            "schema_version": "1.0",
            "kind": "lifecycle_mutation_plan",
            "plan_id": "test_alias_complete",
            "source_scope": "graph_node",
            "source_graph_node_id": "updater",
            "outcome_scope": "outcome",
            "outcome_id": "UPDATE_COMPLETE",
            "source_family_scope": "any",
            "applicability_contexts": ["graph_transition"],
            "owner": "terminal_action",
            "source_from_state": "active",
            "source_to_state": "done",
            "ordering": "after_route",
            "lifecycle_action_id": "complete",
        },
    )
    _append_terminal_action(
        assets_root,
        {
            "schema_version": "1.0",
            "kind": "terminal_action",
            "terminal_action_id": "test_alias_complete",
            "terminal_class": "success",
            "lifecycle_mutation_plan_id": "test_alias_complete",
            "router_consequence": "idle",
        },
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "source_graph_node_id updater does not apply" in _diagnostic_text(outcome)
    assert "source node updater_alias" in _diagnostic_text(outcome)


def test_compile_rejects_graph_node_target_when_stage_kind_scope_required(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _add_updater_alias_terminal(assets_root, terminal_action_id="test_alias_complete")
    _append_lifecycle_plan(
        assets_root,
        {
            "schema_version": "1.0",
            "kind": "lifecycle_mutation_plan",
            "plan_id": "test_alias_complete",
            "source_scope": "stage_kind",
            "source_stage_kind_id": "updater_alias",
            "outcome_scope": "outcome",
            "outcome_id": "UPDATE_COMPLETE",
            "source_family_scope": "any",
            "applicability_contexts": ["graph_transition"],
            "owner": "terminal_action",
            "source_from_state": "active",
            "source_to_state": "done",
            "ordering": "after_route",
            "lifecycle_action_id": "complete",
        },
    )
    _append_terminal_action(
        assets_root,
        {
            "schema_version": "1.0",
            "kind": "terminal_action",
            "terminal_action_id": "test_alias_complete",
            "terminal_class": "success",
            "lifecycle_mutation_plan_id": "test_alias_complete",
            "router_consequence": "idle",
        },
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "source stage kind updater_alias" in _diagnostic_text(outcome)


def test_compile_rejects_terminal_action_lifecycle_outcome_mismatch(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _mutate_lifecycle_plan(
        assets_root,
        "complete_work_item",
        outcome_scope="outcome",
        outcome_id="BUILDER_COMPLETE",
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "outcome_id BUILDER_COMPLETE does not apply" in _diagnostic_text(outcome)


def test_compile_rejects_terminal_action_lifecycle_source_family_mismatch(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _mutate_lifecycle_plan(
        assets_root,
        "complete_work_item",
        source_family_scope="family",
        source_family_id="spec",
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "source_family_id spec is incompatible" in _diagnostic_text(outcome)


def test_compile_rejects_threshold_exhaustion_lifecycle_outcome_mismatch(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_lifecycle_plan(
        assets_root,
        {
            "schema_version": "1.0",
            "kind": "lifecycle_mutation_plan",
            "plan_id": "test_threshold_block",
            "source_scope": "any",
            "outcome_scope": "outcome",
            "outcome_id": "RECON_BLOCKED",
            "source_family_scope": "any",
            "applicability_contexts": ["threshold_exhaustion"],
            "owner": "terminal_action",
            "source_from_state": "active",
            "source_to_state": "blocked",
            "ordering": "after_route",
            "lifecycle_action_id": "block",
        },
    )
    _append_terminal_action(
        assets_root,
        {
            "schema_version": "1.0",
            "kind": "terminal_action",
            "terminal_action_id": "test_threshold_block",
            "terminal_class": "blocked",
            "lifecycle_mutation_plan_id": "test_threshold_block",
            "router_consequence": "blocked",
        },
    )
    graph_path = assets_root / "graphs" / "planning" / "standard.json"
    payload = _load_json(graph_path)
    payload["terminal_states"].append(
        {
            "terminal_state_id": "test_threshold_blocked",
            "terminal_class": "blocked",
            "terminal_action_id": "test_threshold_block",
            "writes_status": "BLOCKED",
            "emits_artifacts": ["stage_result", "report"],
        }
    )
    for policy in payload["dynamic_policies"]["threshold_policies"]:
        if policy["policy_id"] == "planning.blocked.recovery":
            policy["exhausted_terminal_state_id"] = "test_threshold_blocked"
            break
    else:
        raise AssertionError("missing planning.blocked.recovery policy")
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "threshold policy planning.blocked.recovery" in _diagnostic_text(outcome)
    assert "outcome BLOCKED" in _diagnostic_text(outcome)


def test_compile_rejects_runtime_failure_exhaustion_without_any_outcome(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_lifecycle_plan(
        assets_root,
        {
            "schema_version": "1.0",
            "kind": "lifecycle_mutation_plan",
            "plan_id": "test_runtime_failure_block",
            "source_scope": "any",
            "outcome_scope": "outcome",
            "outcome_id": "BLOCKED",
            "source_family_scope": "any",
            "applicability_contexts": ["runtime_failure_exhaustion"],
            "owner": "terminal_action",
            "source_from_state": "active",
            "source_to_state": "blocked",
            "ordering": "after_route",
            "lifecycle_action_id": "block",
        },
    )
    _append_terminal_action(
        assets_root,
        {
            "schema_version": "1.0",
            "kind": "terminal_action",
            "terminal_action_id": "test_runtime_failure_block",
            "terminal_class": "blocked",
            "lifecycle_mutation_plan_id": "test_runtime_failure_block",
            "router_consequence": "blocked",
        },
    )
    graph_path = assets_root / "graphs" / "planning" / "standard.json"
    payload = _load_json(graph_path)
    payload["terminal_states"].append(
        {
            "terminal_state_id": "test_runtime_failure_blocked",
            "terminal_class": "blocked",
            "terminal_action_id": "test_runtime_failure_block",
            "writes_status": "BLOCKED",
            "emits_artifacts": ["stage_result", "report"],
        }
    )
    payload["runtime_failure_recovery"][
        "exhausted_terminal_state_id"
    ] = "test_runtime_failure_blocked"
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "runtime failure recovery exhaustion" in _diagnostic_text(outcome)
    assert "outcome RUNTIME_FAILURE_EXHAUSTED" in _diagnostic_text(outcome)


def test_compile_accepts_runtime_failure_exhaustion_with_any_outcome(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _mutate_lifecycle_plan(
        assets_root,
        "block_work_item",
        outcome_scope="any",
        outcome_id=None,
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None


def test_compile_rejects_non_mutating_terminal_action_for_reachable_source(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "planning" / "standard.json"
    payload = _load_json(graph_path)
    for terminal_state in payload["terminal_states"]:
        if terminal_state["terminal_state_id"] == "recon_noop":
            terminal_state["terminal_action_id"] = "idle_plane"
            break
    else:
        raise AssertionError("missing recon_noop terminal state")
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "non-mutating terminal action idle_plane" in _diagnostic_text(outcome)


def test_terminal_actions_declare_router_consequences(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    actions = outcome.active_plan.terminal_actions_by_id
    assert actions["complete_work_item"].router_consequence == "idle"
    assert actions["block_work_item"].router_consequence == "blocked"
    handoff = actions["escalate_to_planning"]
    assert handoff.router_consequence == "handoff"
    assert handoff.handoff_plane == "planning"
    assert handoff.handoff_entry_key == "incident"
    assert handoff.create_incident is True
    assert handoff.failure_class == "terminal_escalate_planning"
