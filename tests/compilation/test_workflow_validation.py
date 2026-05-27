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


def _artifact_contracts_path(assets_root: Path) -> Path:
    return assets_root / "registry" / "artifact_contracts" / "default_artifact_contracts.json"


def _request_context_profiles_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "request_context_profiles"
        / "default_request_context_profiles.json"
    )


def _request_context_providers_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "request_context_providers"
        / "default_request_context_providers.json"
    )


def _request_context_render_plans_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "request_context_render_plans"
        / "default_request_context_render_plans.json"
    )


def _runtime_failure_policies_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_failure_policies"
        / "default_runtime_failure_policies.json"
    )


def _runtime_effect_handlers_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_effect_handlers"
        / "default_effect_handlers.json"
    )


def _runtime_effect_rules_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_effect_rules"
        / "blueprint_effect_rules.json"
    )


def _blueprint_graph_path(assets_root: Path) -> Path:
    return assets_root / "graphs" / "planning" / "blueprint.json"


def _append_runtime_failure_policy(assets_root: Path, policy: dict) -> None:
    path = _runtime_failure_policies_path(assets_root)
    payload = _load_json(path)
    payload["definitions"].append(policy)
    _write_json(path, payload)


def _runtime_effect_policy(**updates) -> dict:
    policy = {
        "schema_version": "1.0",
        "kind": "runtime_failure_policy",
        "policy_id": "test_effect_failure_policy",
        "applies_to_origins": ["runtime_effect"],
        "applies_to_planes": ["planning"],
        "applies_to_families": ["blueprint_draft"],
        "applies_to_failure_classes": ["generated_task_missing"],
        "applies_to_mutation_phases": ["pre_mutation"],
        "applies_to_handler_ids": ["evaluator_blueprint_approved_to_task"],
        "applies_to_source_node_ids": ["evaluator_blueprint"],
        "action": "route_to_node",
        "target_node_id": "mechanic_blueprint",
        "failure_class_template": "runtime_effect_failure",
    }
    policy.update(updates)
    return policy


def _mutate_artifact_contract(assets_root: Path, artifact_id: str, updates: dict) -> None:
    path = _artifact_contracts_path(assets_root)
    payload = _load_json(path)
    for definition in payload["definitions"]:
        if definition["artifact_id"] == artifact_id:
            definition.update(updates)
            _write_json(path, payload)
            return
    raise AssertionError(f"missing artifact contract fixture: {artifact_id}")


def _diagnostic_text(outcome) -> str:
    return "\n".join(outcome.diagnostics.errors)


def _compile_blueprint_with_assets(tmp_path: Path, assets_root: Path):
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    return compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_codex",
        assets_root=assets_root,
    )


def test_compile_input_fingerprint_changes_when_workflow_primitive_asset_changes(
    tmp_path: Path,
) -> None:
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
    task_family_path = assets_root / "registry" / "work_item_families" / "task.json"
    payload = json.loads(task_family_path.read_text(encoding="utf-8"))
    payload["display_name"] = "Execution Task"
    task_family_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    mutated = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
        assets_root=assets_root,
    )

    assert mutated.diagnostics.ok is True
    assert mutated.active_plan is not None
    assert (
        mutated.active_plan.compile_input_fingerprint.assets_fingerprint
        != baseline.active_plan.compile_input_fingerprint.assets_fingerprint
    )
    assert mutated.active_plan.work_item_families_by_id["task"].display_name == "Execution Task"


def test_compile_rejects_queue_claim_policy_with_unknown_family(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policy_path = assets_root / "registry" / "queue_claim_policies" / "default_queue_claim_policies.json"
    payload = _load_json(policy_path)
    payload["definitions"][1]["family_order"].append("ghost_family")
    _write_json(policy_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "queue claim policy planning.default references unknown work item family ghost_family" in _diagnostic_text(outcome)


def test_compile_rejects_entry_family_missing_from_plane_claim_policy(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policy_path = assets_root / "registry" / "queue_claim_policies" / "default_queue_claim_policies.json"
    payload = _load_json(policy_path)
    planning_policy = payload["definitions"][1]
    planning_policy["family_order"] = [
        family_id
        for family_id in planning_policy["family_order"]
        if family_id != "blueprint_draft"
    ]
    _write_json(policy_path, payload)

    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_codex",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "graph planning.blueprint entry blueprint_draft uses family blueprint_draft "
        "missing from queue claim policy planning.default"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_mode_stage_map_outside_selected_loops(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = _load_json(mode_path)
    payload["stage_runner_bindings"]["professor"] = "codex_cli"
    _write_json(mode_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "Mode map `stage_runner_bindings` references stage outside selected loops: professor"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_terminal_state_without_terminal_action(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    actions_path = assets_root / "registry" / "terminal_actions" / "default_terminal_actions.json"
    payload = _load_json(actions_path)
    payload["definitions"] = [
        definition
        for definition in payload["definitions"]
        if definition["terminal_class"] != "blocked"
    ]
    _write_json(actions_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "terminal state blocked uses terminal class blocked without a terminal action" in _diagnostic_text(outcome)


def test_compile_rejects_terminal_action_with_unknown_lifecycle_plan(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    actions_path = assets_root / "registry" / "terminal_actions" / "default_terminal_actions.json"
    payload = _load_json(actions_path)
    payload["definitions"][0]["lifecycle_mutation_plan_id"] = "missing_plan"
    _write_json(actions_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "terminal action complete_work_item references unknown lifecycle mutation plan missing_plan"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_effect_rule_with_unknown_handler(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    rules_dir = assets_root / "registry" / "runtime_effect_rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        rules_dir / "bad_rule.json",
        {
            "schema_version": "1.0",
            "kind": "runtime_effect_rule",
            "rule_id": "bad_rule",
            "effect_operation_id": "enqueue_task",
            "source_node_id": "builder",
            "on_outcomes": ["BUILDER_COMPLETE"],
            "handler_id": "missing_handler",
            "destination_family_id": "task",
            "creates_work_items": True,
            "duplicate_policy": "fail",
            "partial_commit_policy": "block_source",
            "replay_policy": "resume_idempotently",
            "lineage_policy": "preserve_root",
            "applies_before_route": False,
        },
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "runtime effect rule bad_rule references unknown handler missing_handler" in _diagnostic_text(outcome)


def test_compile_rejects_effect_rule_with_unknown_operation(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    handlers_dir = assets_root / "registry" / "runtime_effect_handlers"
    handlers_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        handlers_dir / "unknown_operation_handler.json",
        {
            "schema_version": "1.0",
            "kind": "runtime_effect_handler",
            "handler_id": "declared_unknown_operation_handler",
            "source_planes": ["execution"],
            "allowed_source_families": ["task"],
            "destination_kinds": [],
            "required_artifacts": [],
            "returns_source_lifecycle_intent": False,
            "requires_lifecycle_mutation_plan": False,
            "creates_work_items": False,
            "failure_classes": ["effect_validation_failure"],
        },
    )
    rules_dir = assets_root / "registry" / "runtime_effect_rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        rules_dir / "unknown_operation_rule.json",
        {
            "schema_version": "1.0",
            "kind": "runtime_effect_rule",
            "rule_id": "unknown_operation_rule",
            "effect_operation_id": "enqueue_task",
            "source_node_id": "builder",
            "on_outcomes": ["BUILDER_COMPLETE"],
            "handler_id": "declared_unknown_operation_handler",
            "destination_family_id": "task",
            "creates_work_items": False,
            "duplicate_policy": "fail",
            "partial_commit_policy": "block_source",
            "replay_policy": "resume_idempotently",
            "lineage_policy": "preserve_root",
            "applies_before_route": False,
        },
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect rule unknown_operation_rule references unknown operation enqueue_task"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_stage_kind_with_unknown_output_artifact(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    builder_path = assets_root / "registry" / "stage_kinds" / "execution" / "builder.json"
    payload = _load_json(builder_path)
    payload["declared_output_artifacts"].append("ghost_artifact")
    _write_json(builder_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "stage kind builder declares unknown output artifact ghost_artifact" in _diagnostic_text(outcome)


def test_compile_rejects_stage_kind_with_unknown_input_artifact(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    integrator_path = assets_root / "registry" / "stage_kinds" / "execution" / "integrator.json"
    payload = _load_json(integrator_path)
    payload["allowed_input_artifacts"].append("ghost_artifact")
    _write_json(integrator_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "stage kind integrator allows unknown input artifact ghost_artifact" in _diagnostic_text(outcome)


def test_compile_rejects_terminal_state_with_unknown_artifact(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = _load_json(graph_path)
    payload["terminal_states"][0]["emits_artifacts"].append("ghost_artifact")
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "graph execution.standard terminal update_complete emits unknown artifact ghost_artifact"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_runtime_effect_handler_with_unknown_artifact(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    handlers_path = assets_root / "registry" / "runtime_effect_handlers" / "default_effect_handlers.json"
    payload = _load_json(handlers_path)
    handler = next(
        definition
        for definition in payload["definitions"]
        if definition["handler_id"] == "manager_blueprint_manifest_to_blueprint_drafts"
    )
    handler["required_artifacts"].append("ghost_artifact")
    _write_json(handlers_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect handler manager_blueprint_manifest_to_blueprint_drafts "
        "requires unknown artifact ghost_artifact"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_runtime_effect_rule_with_unknown_required_artifact(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    rules_path = assets_root / "registry" / "runtime_effect_rules" / "blueprint_effect_rules.json"
    payload = _load_json(rules_path)
    payload["definitions"][0]["required_run_artifacts"].append("ghost_artifact")
    _write_json(rules_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect rule manager_blueprint_manifest_to_blueprint_drafts "
        "requires unknown artifact ghost_artifact"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_runtime_effect_failure_policy_with_unknown_target_node(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="bad_effect_target",
            target_node_id="ghost_node",
        ),
    )

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy bad_effect_target references unknown target node ghost_node"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_runtime_effect_failure_policy_with_illegal_source_family_target(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="bad_effect_family_target",
            target_node_id="auditor",
        ),
    )

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy bad_effect_family_target target node auditor cannot start "
        "family blueprint_draft"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_runtime_effect_failure_policy_with_wrong_source_plane(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="bad_effect_source_plane",
            applies_to_planes=["execution"],
            applies_to_families=["task"],
            applies_to_source_node_ids=["evaluator_blueprint"],
            target_node_id="builder",
        ),
    )

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy bad_effect_source_plane source node evaluator_blueprint "
        "is not in plane execution"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_runtime_effect_failure_policy_with_wrong_target_plane(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="bad_effect_target_plane",
            applies_to_planes=["execution"],
            applies_to_source_node_ids=["builder"],
        ),
    )

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy bad_effect_target_plane target node mechanic_blueprint "
        "is not in plane execution"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_runtime_effect_failure_policy_source_alias_when_policy_binds_graph(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="bad_effect_source_alias",
            applies_to_families=["spec"],
            applies_to_source_node_ids=["contractor_blueprint"],
            target_node_id="manager",
        ),
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy bad_effect_source_alias source node contractor_blueprint "
        "is not in plane planning"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_runtime_effect_failure_policy_target_alias_when_policy_binds_graph(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="bad_effect_target_alias",
            applies_to_source_node_ids=["planner"],
            target_node_id="contractor_blueprint",
        ),
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy bad_effect_target_alias target node contractor_blueprint "
        "is not in plane planning"
    ) in _diagnostic_text(outcome)


def test_compile_ignores_optional_runtime_effect_failure_policy_terminal_for_unselected_graph(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="optional_blueprint_effect_terminal",
            applies_to_source_terminal_state_ids=["blueprint_approved"],
        ),
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None


def test_compile_rejects_runtime_effect_failure_policy_with_unknown_terminal_state(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="bad_effect_terminal",
            action="block_source",
            target_node_id=None,
            target_terminal_state_id="ghost_terminal",
        ),
    )

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy bad_effect_terminal references unknown terminal state ghost_terminal"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_runtime_effect_failure_policy_with_undeclared_failure_class(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="bad_effect_failure_class",
            applies_to_failure_classes=["generated_task_missing", "ghost_effect_failure"],
        ),
    )

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy bad_effect_failure_class references undeclared runtime "
        "effect failure class ghost_effect_failure"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_runtime_effect_failure_policy_partial_mutation_route_to_node(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="bad_partial_mutation_route",
            applies_to_families=["spec"],
            applies_to_failure_classes=["blueprint_partial_mutation"],
            applies_to_mutation_phases=["partial_mutation"],
            applies_to_handler_ids=["manager_blueprint_manifest_to_blueprint_drafts"],
            applies_to_source_node_ids=["manager_blueprint"],
            target_node_id="mechanic_blueprint",
        ),
    )

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy bad_partial_mutation_route cannot route partial mutation "
        "runtime effect failures to node mechanic_blueprint"
    ) in _diagnostic_text(outcome)


def test_compile_blueprint_accepts_closed_runtime_effect_recovery_route(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    plan = outcome.active_plan
    policy = plan.runtime_failure_policies_by_id["blueprint_approval_pre_mutation_effect_validation"]
    assert policy.action == "route_to_node"
    assert policy.target_node_id == "mechanic_blueprint"
    assert policy.applies_to_failure_classes == (
        "generated_task_missing",
        "generated_task_invalid",
    )
    repair_rule = next(
        rule
        for rule in plan.runtime_effect_rules
        if rule.rule_id == "mechanic_blueprint_repair_apply"
    )
    assert repair_rule.source_node_id == "mechanic_blueprint"
    assert repair_rule.on_outcomes == ("MECHANIC_BLUEPRINT_COMPLETE",)


def test_compile_rejects_blueprint_recovery_route_without_mechanic_repair_effect(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    rules_path = _runtime_effect_rules_path(assets_root)
    payload = _load_json(rules_path)
    payload["definitions"] = [
        definition
        for definition in payload["definitions"]
        if definition["rule_id"] != "mechanic_blueprint_repair_apply"
    ]
    _write_json(rules_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy blueprint_approval_pre_mutation_effect_validation routes to "
        "mechanic_blueprint but recovery node lacks closed repair effect on "
        "MECHANIC_BLUEPRINT_COMPLETE"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_blueprint_recovery_route_without_mechanic_resume_guard(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = _blueprint_graph_path(assets_root)
    payload = _load_json(graph_path)
    payload["dynamic_policies"]["resume_policies"] = [
        policy
        for policy in payload["dynamic_policies"]["resume_policies"]
        if policy["source_node_id"] != "mechanic_blueprint"
    ]
    _write_json(graph_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy blueprint_approval_pre_mutation_effect_validation routes to "
        "mechanic_blueprint but recovery node lacks resume guard for "
        "MECHANIC_BLUEPRINT_COMPLETE"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_blueprint_recovery_route_without_repair_capability(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    handlers_path = _runtime_effect_handlers_path(assets_root)
    payload = _load_json(handlers_path)
    handler = next(
        definition
        for definition in payload["definitions"]
        if definition["handler_id"] == "mechanic_blueprint_repair_apply"
    )
    handler["declared_capabilities"] = [
        capability
        for capability in handler["declared_capabilities"]
        if capability != "repair.generated_task_invalid"
    ]
    _write_json(handlers_path, payload)
    rules_path = _runtime_effect_rules_path(assets_root)
    rules_payload = _load_json(rules_path)
    rule = next(
        definition
        for definition in rules_payload["definitions"]
        if definition["rule_id"] == "mechanic_blueprint_repair_apply"
    )
    rule["required_handler_capabilities"] = [
        capability
        for capability in rule["required_handler_capabilities"]
        if capability != "repair.generated_task_invalid"
    ]
    _write_json(rules_path, rules_payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy blueprint_approval_pre_mutation_effect_validation routes "
        "generated_task_invalid to mechanic_blueprint but repair effect "
        "mechanic_blueprint_repair_apply lacks capability repair.generated_task_invalid"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_runtime_effect_rule_with_missing_handler_capability(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    rules_path = _runtime_effect_rules_path(assets_root)
    payload = _load_json(rules_path)
    rule = next(
        definition
        for definition in payload["definitions"]
        if definition["rule_id"] == "contractor_blueprint_candidate_persist"
    )
    rule["required_handler_capabilities"].append("repair.apply_repaired_generated_task")
    _write_json(rules_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect rule contractor_blueprint_candidate_persist requires runner "
        "capability repair.apply_repaired_generated_task not declared by runner "
        "legacy_python_handler"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_artifact_contract_with_unknown_destination_family(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _mutate_artifact_contract(
        assets_root,
        "generated_task",
        {"destination_family_id": "ghost_family"},
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "artifact contract generated_task references unknown destination family ghost_family"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_artifact_contract_with_unknown_parser_id(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    contracts_path = _artifact_contracts_path(assets_root)
    payload = _load_json(contracts_path)
    for definition in payload["definitions"]:
        if definition["artifact_id"] == "generated_task":
            definition["filename_adapters"][0]["parser_id"] = "missing_parser"
            break
    _write_json(contracts_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "artifact contract generated_task filename generated_task.json references "
        "unknown parser missing_parser"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_artifact_contract_with_unknown_renderer_id(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    contracts_path = _artifact_contracts_path(assets_root)
    payload = _load_json(contracts_path)
    for definition in payload["definitions"]:
        if definition["artifact_id"] == "generated_task":
            definition["filename_adapters"][0]["renderer_id"] = "missing_renderer"
            break
    _write_json(contracts_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "artifact contract generated_task filename generated_task.json references "
        "unknown renderer missing_renderer"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_artifact_contract_with_parser_format_mismatch(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    contracts_path = _artifact_contracts_path(assets_root)
    payload = _load_json(contracts_path)
    for definition in payload["definitions"]:
        if definition["artifact_id"] == "generated_task":
            definition["filename_adapters"][0]["parser_id"] = "builtin.markdown"
            break
    _write_json(contracts_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "artifact contract generated_task filename generated_task.json declares "
        "format json but parser builtin.markdown handles markdown"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_artifact_contract_with_renderer_format_mismatch(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    contracts_path = _artifact_contracts_path(assets_root)
    payload = _load_json(contracts_path)
    for definition in payload["definitions"]:
        if definition["artifact_id"] == "generated_task":
            definition["filename_adapters"][0]["renderer_id"] = "builtin.markdown"
            break
    _write_json(contracts_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "artifact contract generated_task filename generated_task.json declares "
        "format json but renderer builtin.markdown handles markdown"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_artifact_contract_parser_adapter_without_parse_capability(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    adapter_path = (
        assets_root
        / "registry"
        / "document_adapters"
        / "artifact_report_markdown_v1.json"
    )
    adapter_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "work_item_document_adapter",
                "adapter_id": "artifact_report_markdown_v1",
                "schema_id": "markdown_report_v1",
                "supported_file_extensions": [".md"],
                "family_ids": ["task"],
                "can_parse": False,
                "can_render": True,
                "can_summarize": False,
                "supports_dependencies": False,
                "supports_lineage": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _mutate_artifact_contract(
        assets_root,
        "report",
        {
            "filename_adapters": [
                {
                    "filename": "report.md",
                    "format": "markdown",
                    "parser_id": "artifact_report_markdown_v1",
                    "renderer_id": "builtin.markdown",
                },
                {
                    "filename": "arbiter_report.md",
                    "format": "markdown",
                    "parser_id": "builtin.markdown",
                    "renderer_id": "builtin.markdown",
                },
                {
                    "filename": "contractor_blueprint_report.md",
                    "format": "markdown",
                    "parser_id": "builtin.markdown",
                    "renderer_id": "builtin.markdown",
                },
            ]
        },
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "artifact contract report filename report.md uses parser "
        "artifact_report_markdown_v1 without parse capability"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_artifact_contract_renderer_adapter_without_render_capability(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    adapter_path = (
        assets_root
        / "registry"
        / "document_adapters"
        / "artifact_report_markdown_v1.json"
    )
    adapter_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "work_item_document_adapter",
                "adapter_id": "artifact_report_markdown_v1",
                "schema_id": "markdown_report_v1",
                "supported_file_extensions": [".md"],
                "family_ids": ["task"],
                "can_parse": True,
                "can_render": False,
                "can_summarize": False,
                "supports_dependencies": False,
                "supports_lineage": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _mutate_artifact_contract(
        assets_root,
        "report",
        {
            "filename_adapters": [
                {
                    "filename": "report.md",
                    "format": "markdown",
                    "parser_id": "builtin.markdown",
                    "renderer_id": "artifact_report_markdown_v1",
                },
                {
                    "filename": "arbiter_report.md",
                    "format": "markdown",
                    "parser_id": "builtin.markdown",
                    "renderer_id": "builtin.markdown",
                },
                {
                    "filename": "contractor_blueprint_report.md",
                    "format": "markdown",
                    "parser_id": "builtin.markdown",
                    "renderer_id": "builtin.markdown",
                },
            ]
        },
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "artifact contract report filename report.md uses renderer "
        "artifact_report_markdown_v1 without render capability"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_artifact_contract_with_unknown_producer_stage(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _mutate_artifact_contract(
        assets_root,
        "generated_task",
        {"producer_stage_kind_ids": ["evaluator_blueprint", "ghost_stage"]},
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "artifact contract generated_task references unknown producer stage kind ghost_stage"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_artifact_contract_consumer_handler_mismatch(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _mutate_artifact_contract(
        assets_root,
        "generated_task",
        {"consumer_handler_ids": ["manager_blueprint_manifest_to_blueprint_drafts"]},
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "artifact contract generated_task declares consumer handler "
        "manager_blueprint_manifest_to_blueprint_drafts, but that handler does not consume generated_task"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_request_context_profile_with_unknown_output_artifact(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    profiles_path = _request_context_profiles_path(assets_root)
    payload = _load_json(profiles_path)
    payload["definitions"][0]["output_path_preferences"]["ghost_artifact"] = "ghost_artifact.md"
    _write_json(profiles_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "request context profile builder.default references unknown output artifact ghost_artifact"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_request_context_profile_with_invalid_output_filename(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    profiles_path = _request_context_profiles_path(assets_root)
    payload = _load_json(profiles_path)
    payload["definitions"][0]["output_path_preferences"] = {"report": "not_report.md"}
    _write_json(profiles_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "request context profile builder.default maps artifact report to filename "
        "not_report.md; artifact contract mismatch"
    ) in _diagnostic_text(outcome)


def test_compile_materializes_request_context_provider_and_render_plan_authority(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True, outcome.diagnostics.errors
    assert outcome.active_plan is not None
    plan = outcome.active_plan
    assert "builder.default" in plan.request_context_profiles_by_id
    assert "generic.active_work_item" in plan.request_context_providers_by_id
    assert "stage_request.default.v1" in plan.request_context_render_plans_by_id
    builder = next(node for node in plan.execution_graph.nodes if node.node_id == "builder")
    assert builder.request_context_profile_id == "builder.default"
    assert builder.context_render_plan_id == "stage_request.default.v1"
    assert any(
        ref.asset_family == "request_context_provider"
        and ref.logical_id == "request_context_provider:generic.active_work_item"
        for ref in plan.resolved_assets
    )
    assert any(
        ref.asset_family == "request_context_render_plan"
        and ref.logical_id == "request_context_render_plan:stage_request.default.v1"
        for ref in plan.resolved_assets
    )


def test_compile_rejects_request_context_profile_with_unknown_provider(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    profiles_path = _request_context_profiles_path(assets_root)
    payload = _load_json(profiles_path)
    payload["definitions"][0]["provider_id"] = "ghost.provider"
    _write_json(profiles_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "request context profile builder.default references unknown provider ghost.provider"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_request_context_profile_with_unknown_render_plan(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    profiles_path = _request_context_profiles_path(assets_root)
    payload = _load_json(profiles_path)
    payload["definitions"][0]["primary_render_plan_id"] = "ghost.render_plan"
    _write_json(profiles_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "request context profile builder.default references unknown render plan ghost.render_plan"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_request_context_provider_profile_kind_mismatch(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    profiles_path = _request_context_profiles_path(assets_root)
    payload = _load_json(profiles_path)
    payload["definitions"][0]["request_kind"] = "closure_target"
    _write_json(profiles_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "request context profile builder.default request kind closure_target is not "
        "supported by provider generic.active_work_item"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_request_context_render_plan_missing_provider_capability(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    render_plans_path = _request_context_render_plans_path(assets_root)
    payload = _load_json(render_plans_path)
    payload["definitions"][0]["required_provider_capabilities"].append("ghost_capability")
    _write_json(render_plans_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "requires provider capabilities not declared by generic.active_work_item: "
        "ghost_capability"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_disallowed_node_context_render_plan_override(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = _load_json(graph_path)
    payload["nodes"][0]["context_render_plan_id"] = "closure_target.default.v1"
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "graph node builder overrides request context render plan "
        "stage_request.default.v1 with closure_target.default.v1, but profile "
        "builder.default does not allow render plan overrides"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_duplicate_effect_rule_binding(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    rules_dir = assets_root / "registry" / "runtime_effect_rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        rules_dir / "duplicate_rules.json",
        {
            "definitions": [
                {
                    "schema_version": "1.0",
                    "kind": "runtime_effect_rule",
                    "rule_id": "duplicate_builder_effect_a",
                    "effect_operation_id": "mechanic_blueprint_repair_apply",
                    "source_node_id": "mechanic_blueprint",
                    "on_outcomes": ["BLOCKED"],
                    "handler_id": "mechanic_blueprint_repair_apply",
                    "required_run_artifacts": ["blueprint_repair_decision", "mechanic_report"],
                    "destination_family_id": "task",
                    "creates_work_items": True,
                    "duplicate_policy": "fail",
                    "partial_commit_policy": "block_source",
                    "replay_policy": "resume_idempotently",
                    "lineage_policy": "preserve_root",
                    "applies_before_route": False,
                },
                {
                    "schema_version": "1.0",
                    "kind": "runtime_effect_rule",
                    "rule_id": "duplicate_builder_effect_b",
                    "effect_operation_id": "mechanic_blueprint_repair_apply",
                    "source_node_id": "mechanic_blueprint",
                    "on_outcomes": ["BLOCKED"],
                    "handler_id": "mechanic_blueprint_repair_apply",
                    "required_run_artifacts": ["blueprint_repair_decision", "mechanic_report"],
                    "destination_family_id": "task",
                    "creates_work_items": True,
                    "duplicate_policy": "fail",
                    "partial_commit_policy": "block_source",
                    "replay_policy": "resume_idempotently",
                    "lineage_policy": "preserve_root",
                    "applies_before_route": False,
                },
            ]
        },
    )

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect rules duplicate_builder_effect_a and duplicate_builder_effect_b "
        "both bind mechanic_blueprint outcome BLOCKED"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_entry_stage_without_family_ownership(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "planning" / "standard.json"
    payload = _load_json(graph_path)
    for entry in payload["entry_nodes"]:
        if entry["entry_key"] == "incident":
            entry["node_id"] = "recon"
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "entry incident routes to stage kind recon, which cannot start family incident" in _diagnostic_text(outcome)


def test_compile_rejects_graph_with_unrouted_legal_outcome(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = _load_json(graph_path)
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if edge["edge_id"] != "builder-complete-to-checker"
    ]
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "graph execution.standard node builder has no route for legal outcome BUILDER_COMPLETE" in _diagnostic_text(outcome)


def test_compile_rejects_graph_route_with_illegal_source_outcome(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = _load_json(graph_path)
    payload["edges"][0]["on_outcomes"] = ["NOT_LEGAL"]
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "declares illegal outcome NOT_LEGAL for stage kind builder" in _diagnostic_text(outcome)


def test_compile_rejects_unmapped_runtime_failure_recovery_stage_kind(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    stage_kind_path = assets_root / "registry" / "stage_kinds" / "planning" / "mechanic.json"
    stage_kind_payload = _load_json(stage_kind_path)
    stage_kind_payload.update(
        {
            "stage_kind_id": "diagnostician",
            "display_name": "Diagnostician",
            "running_status_marker": "DIAGNOSTICIAN_RUNNING",
        }
    )
    diagnostician_path = stage_kind_path.with_name("diagnostician.json")
    _write_json(diagnostician_path, stage_kind_payload)

    graph_path = assets_root / "graphs" / "planning" / "standard.json"
    graph_payload = _load_json(graph_path)
    for node in graph_payload["nodes"]:
        if node["node_id"] == "mechanic":
            node["stage_kind_id"] = "diagnostician"
            break
    _write_json(graph_path, graph_payload)
    mode_path = assets_root / "modes" / "default_codex.json"
    mode_payload = _load_json(mode_path)
    mode_payload["stage_runner_bindings"]["diagnostician"] = mode_payload[
        "stage_runner_bindings"
    ].pop("mechanic")
    _write_json(mode_path, mode_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "graph planning.standard runtime failure recovery node mechanic "
        "uses unmapped stage kind diagnostician"
    ) in _diagnostic_text(outcome)
