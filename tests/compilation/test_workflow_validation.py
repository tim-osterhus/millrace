from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from millrace_ai.assets import discover_stage_kind_definitions
from millrace_ai.compilation.outcomes import CompilerValidationError
from millrace_ai.compilation.validation.graphs import validate_structural_graph_smoke
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import Plane
from millrace_ai.paths import bootstrap_workspace

FIXTURE_ASSETS_ROOT = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "non_default_effect_assets"
)
FIXTURE_REPAIR_POLICY_ID = "fixture_non_default_repair_route"
FIXTURE_REPAIR_OPERATION_ID = "fixture_echo_repair_apply"
FIXTURE_REPAIR_RULE_ID = "fixture_echo_repair_apply_on_mechanic_complete"
FIXTURE_SECOND_OPERATION_ID = "fixture_echo_followup_effect"


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _copy_non_default_fixture_assets(tmp_path: Path) -> Path:
    copied_root = _copy_builtin_assets(tmp_path)
    shutil.copytree(FIXTURE_ASSETS_ROOT, copied_root, dirs_exist_ok=True)
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


def _stage_kind_path(assets_root: Path, plane: str, stage_kind_id: str) -> Path:
    return assets_root / "registry" / "stage_kinds" / plane / f"{stage_kind_id}.json"


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


def _runtime_effect_operations_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_effect_operations"
        / "default_runtime_effect_operations.json"
    )


def _lifecycle_mutation_plans_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "lifecycle_mutation_plans"
        / "default_lifecycle_mutations.json"
    )


def _fixture_artifact_contracts_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "artifact_contracts"
        / "fixture_effect_artifacts.json"
    )


def _blueprint_graph_path(assets_root: Path) -> Path:
    return assets_root / "graphs" / "planning" / "blueprint.json"


def _planning_standard_graph_path(assets_root: Path) -> Path:
    return assets_root / "graphs" / "planning" / "standard.json"


def _fixture_runtime_effect_rules_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_effect_rules"
        / "fixture_effect_rules.json"
    )


def _fixture_runtime_effect_operations_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_effect_operations"
        / "fixture_runtime_effect_operations.json"
    )


def _fixture_runtime_effect_runners_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_effect_runners"
        / "fixture_runtime_effect_runners.json"
    )


def _fixture_runtime_effect_handlers_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_effect_handlers"
        / "fixture_effect_handlers.json"
    )


def _configure_non_default_repair_route(assets_root: Path) -> None:
    operations_path = _fixture_runtime_effect_operations_path(assets_root)
    operations_payload = _load_json(operations_path)
    fixture_operation = next(
        definition
        for definition in operations_payload["definitions"]
        if definition["operation_id"] == "fixture_echo_effect"
    )
    fixture_operation["repair_closure_contracts"] = [
        {
            "failure_class": "fixture_effect_input_missing",
            "repair_operation_id": FIXTURE_REPAIR_OPERATION_ID,
            "target_node_id": "mechanic",
            "target_terminal_outcome": "MECHANIC_COMPLETE",
            "required_repair_evidence_artifact_ids": ["report"],
            "affected_source_family_id": "spec",
            "source_lifecycle_behavior_on_repair_success": "complete_source_work_item",
            "source_lifecycle_behavior_on_repair_failure": "block_source_work_item",
            "supports_partial_mutation": False,
            "requires_resume_guard": True,
        }
    ]
    operations_payload["definitions"].append(
        {
            "schema_version": "1.0",
            "kind": "runtime_effect_operation",
            "operation_id": FIXTURE_REPAIR_OPERATION_ID,
            "display_name": "Non-Blueprint fixture repair apply operation",
            "legacy_handler_ids": [],
            "required_artifacts": ["report"],
            "steps": [
                {
                    "step_id": "dispatch_fixture_repair_runner",
                    "primitive_id": "legacy_python_handler",
                    "mutation_phase": "unknown",
                    "reads_artifact_ids": ["report"],
                    "store_id": "fixture_effect_log",
                    "writes_store": True,
                    "journal_event_type": "fixture_repair_result",
                }
            ],
            "idempotency": {
                "duplicate_policy": "fail",
                "replay_policy": "resume_idempotently",
            },
            "failure_mappings": [
                {
                    "failure_class": "legacy_handler_failure",
                    "mutation_phase": "unknown",
                }
            ],
            "mutation_journal": {
                "entry_id_template": "{operation_id}:{run_id}:{step_id}",
                "required_fields": [
                    "operation_id",
                    "rule_id",
                    "run_id",
                    "step_id",
                    "mutation_phase",
                ],
                "record_step_ids": ["dispatch_fixture_repair_runner"],
            },
            "partial_commit_policy": "block_source",
        }
    )
    _write_json(operations_path, operations_payload)

    runners_path = _fixture_runtime_effect_runners_path(assets_root)
    runners_payload = _load_json(runners_path)
    runners_payload["definitions"].append(
        {
            "schema_version": "1.0",
            "kind": "runtime_effect_runner",
            "runner_id": "fixture_repair_runner",
            "operation_ids": [FIXTURE_REPAIR_OPERATION_ID],
            "required_runtime_capabilities": [],
            "legacy_handler_ids": [],
            "result_display_aliases": {},
        }
    )
    _write_json(runners_path, runners_payload)

    rules_path = _fixture_runtime_effect_rules_path(assets_root)
    rules_payload = _load_json(rules_path)
    rules_payload["definitions"].append(
        {
            "schema_version": "1.0",
            "kind": "runtime_effect_rule",
            "rule_id": FIXTURE_REPAIR_RULE_ID,
            "effect_operation_id": FIXTURE_REPAIR_OPERATION_ID,
            "source_node_id": "mechanic",
            "on_outcomes": ["MECHANIC_COMPLETE"],
            "required_run_artifacts": ["report"],
            "creates_work_items": False,
            "duplicate_policy": "fail",
            "partial_commit_policy": "block_source",
            "replay_policy": "resume_idempotently",
            "lineage_policy": "preserve_root",
            "applies_before_route": False,
            "lifecycle_mutation_plan_id": None,
        }
    )
    _write_json(rules_path, rules_payload)

    policies_path = _runtime_failure_policies_path(assets_root)
    policies_payload = _load_json(policies_path)
    policies_payload["definitions"].append(
        {
            "schema_version": "1.0",
            "kind": "runtime_failure_policy",
            "policy_id": FIXTURE_REPAIR_POLICY_ID,
            "applies_to_origins": ["runtime_effect"],
            "applies_to_planes": ["planning"],
            "applies_to_families": ["spec"],
            "applies_to_failure_classes": ["fixture_effect_input_missing"],
            "applies_to_mutation_phases": ["pre_mutation"],
            "applies_to_operation_ids": ["fixture_echo_effect"],
            "applies_to_handler_ids": ["fixture_echo_effect"],
            "applies_to_source_node_ids": ["manager"],
            "applies_to_source_terminal_state_ids": ["manager_complete"],
            "action": "route_to_node",
            "target_node_id": "mechanic",
            "failure_class_template": "runtime_effect_failure",
        }
    )
    _write_json(policies_path, policies_payload)


def _append_second_non_default_fixture_operation(assets_root: Path) -> None:
    artifact_contracts_path = _fixture_artifact_contracts_path(assets_root)
    artifact_contracts_payload = _load_json(artifact_contracts_path)
    fixture_artifact = next(
        definition
        for definition in artifact_contracts_payload["definitions"]
        if definition["artifact_id"] == "fixture_effect_input"
    )
    fixture_artifact["consumer_handler_ids"].append(FIXTURE_SECOND_OPERATION_ID)
    fixture_artifact["consumer_operation_ids"].append(FIXTURE_SECOND_OPERATION_ID)
    _write_json(artifact_contracts_path, artifact_contracts_payload)

    handlers_path = _fixture_runtime_effect_handlers_path(assets_root)
    handlers_payload = _load_json(handlers_path)
    handlers_payload["definitions"].append(
        {
            "schema_version": "1.0",
            "kind": "runtime_effect_handler",
            "handler_id": FIXTURE_SECOND_OPERATION_ID,
            "source_planes": ["planning"],
            "allowed_source_families": ["spec"],
            "destination_kinds": [],
            "required_artifacts": ["fixture_effect_input"],
            "optional_artifacts": [],
            "returns_source_lifecycle_intent": False,
            "requires_lifecycle_mutation_plan": False,
            "creates_work_items": False,
            "declared_capabilities": [],
            "failure_classes": [
                "fixture_effect_input_missing",
                "fixture_effect_invalid",
                "legacy_handler_failure",
            ],
        }
    )
    _write_json(handlers_path, handlers_payload)

    operations_path = _fixture_runtime_effect_operations_path(assets_root)
    operations_payload = _load_json(operations_path)
    operations_payload["definitions"].append(
        {
            "schema_version": "1.0",
            "kind": "runtime_effect_operation",
            "operation_id": FIXTURE_SECOND_OPERATION_ID,
            "display_name": "Non-Blueprint fixture followup effect",
            "legacy_handler_ids": [FIXTURE_SECOND_OPERATION_ID],
            "required_artifacts": ["fixture_effect_input"],
            "steps": [
                {
                    "step_id": "validate_required_artifacts",
                    "primitive_id": "artifact_presence",
                    "mutation_phase": "pre_mutation",
                    "reads_artifact_ids": ["fixture_effect_input"],
                    "validator_ids": ["fixture_echo_effect.required_artifacts"],
                },
                {
                    "step_id": "dispatch_fixture_followup_runner",
                    "primitive_id": "legacy_python_handler",
                    "mutation_phase": "unknown",
                    "reads_artifact_ids": ["fixture_effect_input"],
                    "store_id": "fixture_effect_log",
                    "writes_store": True,
                    "journal_event_type": "fixture_followup_result",
                },
            ],
            "idempotency": {
                "duplicate_policy": "fail",
                "replay_policy": "resume_idempotently",
            },
            "failure_mappings": [
                {
                    "failure_class": "fixture_effect_input_missing",
                    "mutation_phase": "pre_mutation",
                    "validator_id": "fixture_echo_effect.required_artifacts",
                },
                {
                    "failure_class": "legacy_handler_failure",
                    "mutation_phase": "unknown",
                },
            ],
            "mutation_journal": {
                "entry_id_template": "{operation_id}:{run_id}:{step_id}",
                "required_fields": [
                    "operation_id",
                    "rule_id",
                    "run_id",
                    "step_id",
                    "mutation_phase",
                ],
                "record_step_ids": ["dispatch_fixture_followup_runner"],
            },
            "partial_commit_policy": "block_source",
        }
    )
    _write_json(operations_path, operations_payload)

    runners_path = _fixture_runtime_effect_runners_path(assets_root)
    runners_payload = _load_json(runners_path)
    runners_payload["definitions"].append(
        {
            "schema_version": "1.0",
            "kind": "runtime_effect_runner",
            "runner_id": "fixture_followup_runner",
            "operation_ids": [FIXTURE_SECOND_OPERATION_ID],
            "required_runtime_capabilities": [],
            "legacy_handler_ids": [FIXTURE_SECOND_OPERATION_ID],
            "result_display_aliases": {
                FIXTURE_SECOND_OPERATION_ID: FIXTURE_SECOND_OPERATION_ID,
            },
        }
    )
    _write_json(runners_path, runners_payload)


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
        requested_mode_id="blueprint_" "codex",
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
    policy_path = assets_root / "registry" / "scheduler_policies" / "default_two_plane.json"
    payload = _load_json(policy_path)
    scheduler_policy = next(
        definition
        for definition in payload["definitions"]
        if definition["policy_id"] == "default.two_plane.blueprint"
    )
    planning_policy = scheduler_policy["claim_policies_by_plane"]["planning"]
    planning_policy["family_order"] = [
        family_id
        for family_id in planning_policy["family_order"]
        if family_id != "blueprint_draft"
    ]
    planning_lane = next(
        lane
        for lane in scheduler_policy["lanes"]
        if lane["claim_policy_id"] == "planning.blueprint"
    )
    planning_lane["allowed_family_ids"] = [
        family_id
        for family_id in planning_lane["allowed_family_ids"]
        if family_id != "blueprint_draft"
    ]
    _write_json(policy_path, payload)

    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "codex",
        assets_root=assets_root,
    )

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "graph planning.blueprint entry blueprint_draft uses family blueprint_draft "
        "missing from queue claim policy planning.blueprint"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_queue_claim_family_with_unknown_queue_lifecycle_adapter(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    task_path = assets_root / "registry" / "work_item_families" / "task.json"
    payload = _load_json(task_path)
    payload["queue_lifecycle_adapter_id"] = "missing.queue_lifecycle.adapter"
    _write_json(task_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "queue claim family task references unknown queue lifecycle adapter "
        "missing.queue_lifecycle.adapter"
    ) in _diagnostic_text(outcome)


def test_compile_backfills_builtin_queue_lifecycle_adapter_for_legacy_family_asset(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    task_path = assets_root / "registry" / "work_item_families" / "task.json"
    payload = _load_json(task_path)
    payload.pop("queue_lifecycle_adapter_id", None)
    _write_json(task_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True, outcome.diagnostics.errors
    assert outcome.active_plan is not None
    assert (
        outcome.active_plan.work_item_families_by_id["task"].queue_lifecycle_adapter_id
        == "builtin.queue_lifecycle.task"
    )


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


def test_compile_rejects_mode_stage_model_binding_outside_selected_loops(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = _load_json(mode_path)
    payload["stage_model_bindings"]["professor"] = "gpt-5.5"
    _write_json(mode_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "Mode map `stage_model_bindings` references stage outside selected loops: professor"
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
    assert (
        "terminal state blocked references unknown terminal action block_work_item"
        in _diagnostic_text(outcome)
    )


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


def test_compile_rejects_terminal_action_with_unknown_effect_rule(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    actions_path = assets_root / "registry" / "terminal_actions" / "default_terminal_actions.json"
    payload = _load_json(actions_path)
    payload["definitions"][0]["effect_rule_ids"] = ["ghost_effect_rule"]
    _write_json(actions_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "terminal action complete_work_item references unknown runtime effect rule "
        "ghost_effect_rule"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_lifecycle_plan_with_unknown_source_family(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    plans_path = _lifecycle_mutation_plans_path(assets_root)
    payload = _load_json(plans_path)
    payload["definitions"][0]["source_family_scope"] = "family"
    payload["definitions"][0]["source_family_id"] = "ghost_family"
    plan_id = payload["definitions"][0]["plan_id"]
    _write_json(plans_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        f"lifecycle mutation plan {plan_id} references unknown source family ghost_family"
        in _diagnostic_text(outcome)
    )


def test_compile_rejects_lifecycle_plan_with_unknown_source_node(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    plans_path = _lifecycle_mutation_plans_path(assets_root)
    payload = _load_json(plans_path)
    payload["definitions"][0]["source_scope"] = "graph_node"
    payload["definitions"][0]["source_graph_node_id"] = "ghost_node"
    plan_id = payload["definitions"][0]["plan_id"]
    _write_json(plans_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        f"lifecycle mutation plan {plan_id} references unknown source graph node ghost_node"
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


def test_compile_rejects_stage_kind_output_artifact_without_contract_producer_match(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    builder_path = assets_root / "registry" / "stage_kinds" / "execution" / "builder.json"
    payload = _load_json(builder_path)
    payload["declared_output_artifacts"].append("blueprint_packet")
    _write_json(builder_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "stage kind builder declares output artifact blueprint_packet, but artifact contract "
        "blueprint_packet does not list that stage kind as a producer"
    ) in _diagnostic_text(outcome)


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
            applies_to_families=["blueprint_draft"],
            applies_to_failure_classes=["generated_task_missing"],
            applies_to_mutation_phases=["partial_mutation"],
            applies_to_handler_ids=["evaluator_blueprint_approved_to_task"],
            applies_to_source_node_ids=["evaluator_blueprint"],
            applies_to_source_terminal_state_ids=["blueprint_approved"],
            target_node_id="mechanic_blueprint",
        ),
    )

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy bad_partial_mutation_route applies to partial mutation but "
        "repair closure evaluator_blueprint_approved_to_task/generated_task_missing does not "
        "support partial mutation"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_repair_route_with_wildcard_family_scope(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policies_path = _runtime_failure_policies_path(assets_root)
    payload = _load_json(policies_path)
    blueprint_policy = next(
        definition
        for definition in payload["definitions"]
        if definition["policy_id"] == "blueprint_approval_pre_mutation_effect_validation"
    )
    blueprint_policy["applies_to_families"] = []
    _write_json(policies_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy blueprint_approval_pre_mutation_effect_validation "
        "route_to_node repair closure must declare applies_to_families matching "
        "repair closure families: blueprint_draft"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_repair_route_with_extra_family_scope(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policies_path = _runtime_failure_policies_path(assets_root)
    payload = _load_json(policies_path)
    blueprint_policy = next(
        definition
        for definition in payload["definitions"]
        if definition["policy_id"] == "blueprint_approval_pre_mutation_effect_validation"
    )
    blueprint_policy["applies_to_families"] = ["blueprint_draft", "spec"]
    _write_json(policies_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy blueprint_approval_pre_mutation_effect_validation "
        "applies_to_families blueprint_draft, spec must exactly match repair "
        "closure affected source families: blueprint_draft"
    ) in _diagnostic_text(outcome)


def test_compile_accepts_non_default_repair_route_from_operation_contract(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_default_fixture_assets(tmp_path)
    _configure_non_default_repair_route(assets_root)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True, outcome.diagnostics.errors
    assert outcome.active_plan is not None
    policy = outcome.active_plan.runtime_failure_policies_by_id[FIXTURE_REPAIR_POLICY_ID]
    assert policy.action == "route_to_node"
    assert policy.target_node_id == "mechanic"
    assert policy.applies_to_operation_ids == ("fixture_echo_effect",)
    repair_rule = next(
        rule
        for rule in outcome.active_plan.runtime_effect_rules
        if rule.rule_id == FIXTURE_REPAIR_RULE_ID
    )
    assert repair_rule.effect_operation_id == FIXTURE_REPAIR_OPERATION_ID
    assert repair_rule.on_outcomes == ("MECHANIC_COMPLETE",)


def test_compile_rejects_non_default_repair_route_with_unknown_repair_operation(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_default_fixture_assets(tmp_path)
    _configure_non_default_repair_route(assets_root)

    operations_path = _fixture_runtime_effect_operations_path(assets_root)
    operations_payload = _load_json(operations_path)
    fixture_operation = next(
        definition
        for definition in operations_payload["definitions"]
        if definition["operation_id"] == "fixture_echo_effect"
    )
    fixture_operation["repair_closure_contracts"][0]["repair_operation_id"] = "missing_fixture_repair"
    _write_json(operations_path, operations_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        f"runtime failure policy {FIXTURE_REPAIR_POLICY_ID} repair closure "
        "fixture_echo_effect/fixture_effect_input_missing references unknown repair operation "
        "missing_fixture_repair"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_non_default_repair_route_missing_rule_artifact(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_default_fixture_assets(tmp_path)
    _configure_non_default_repair_route(assets_root)

    rules_path = _fixture_runtime_effect_rules_path(assets_root)
    rules_payload = _load_json(rules_path)
    repair_rule = next(
        definition
        for definition in rules_payload["definitions"]
        if definition["rule_id"] == FIXTURE_REPAIR_RULE_ID
    )
    repair_rule["required_run_artifacts"] = []
    _write_json(rules_path, rules_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        f"runtime failure policy {FIXTURE_REPAIR_POLICY_ID} target node mechanic outcome "
        f"MECHANIC_COMPLETE rule {FIXTURE_REPAIR_RULE_ID} is missing required repair evidence "
        "artifact report"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_non_default_repair_route_with_wrong_family_scope(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_default_fixture_assets(tmp_path)
    _configure_non_default_repair_route(assets_root)

    policies_path = _runtime_failure_policies_path(assets_root)
    policies_payload = _load_json(policies_path)
    fixture_policy = next(
        definition
        for definition in policies_payload["definitions"]
        if definition["policy_id"] == FIXTURE_REPAIR_POLICY_ID
    )
    fixture_policy["applies_to_families"] = ["incident"]
    _write_json(policies_path, policies_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        f"runtime failure policy {FIXTURE_REPAIR_POLICY_ID} applies_to_families incident "
        "must exactly match repair closure affected source families: spec"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_non_default_repair_route_with_wrong_target_plane(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_default_fixture_assets(tmp_path)
    _configure_non_default_repair_route(assets_root)

    policies_path = _runtime_failure_policies_path(assets_root)
    policies_payload = _load_json(policies_path)
    fixture_policy = next(
        definition
        for definition in policies_payload["definitions"]
        if definition["policy_id"] == FIXTURE_REPAIR_POLICY_ID
    )
    fixture_policy["target_node_id"] = "builder"
    _write_json(policies_path, policies_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        f"runtime failure policy {FIXTURE_REPAIR_POLICY_ID} target node builder "
        "is not in plane planning"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_non_default_repair_route_with_wrong_target_terminal_outcome(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_default_fixture_assets(tmp_path)
    _configure_non_default_repair_route(assets_root)

    operations_path = _fixture_runtime_effect_operations_path(assets_root)
    operations_payload = _load_json(operations_path)
    fixture_operation = next(
        definition
        for definition in operations_payload["definitions"]
        if definition["operation_id"] == "fixture_echo_effect"
    )
    fixture_operation["repair_closure_contracts"][0]["target_terminal_outcome"] = "BLOCKED"
    _write_json(operations_path, operations_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        f"runtime failure policy {FIXTURE_REPAIR_POLICY_ID} target node mechanic outcome BLOCKED "
        f"does not invoke repair operation {FIXTURE_REPAIR_OPERATION_ID}"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_non_default_repair_route_without_explicit_operation_scope_when_ambiguous(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_default_fixture_assets(tmp_path)
    _configure_non_default_repair_route(assets_root)
    _append_second_non_default_fixture_operation(assets_root)

    policies_path = _runtime_failure_policies_path(assets_root)
    policies_payload = _load_json(policies_path)
    fixture_policy = next(
        definition
        for definition in policies_payload["definitions"]
        if definition["policy_id"] == FIXTURE_REPAIR_POLICY_ID
    )
    fixture_policy.pop("applies_to_operation_ids")
    fixture_policy["applies_to_handler_ids"] = [
        "fixture_echo_effect",
        FIXTURE_SECOND_OPERATION_ID,
    ]
    _write_json(policies_path, policies_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        f"runtime failure policy {FIXTURE_REPAIR_POLICY_ID} must declare "
        "repair_closure_mappings for multi-operation or multi-failure-class "
        "route_to_node scope"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_non_default_repair_route_without_resume_guard(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_default_fixture_assets(tmp_path)
    _configure_non_default_repair_route(assets_root)

    graph_path = _planning_standard_graph_path(assets_root)
    graph_payload = _load_json(graph_path)
    graph_payload["dynamic_policies"]["resume_policies"] = [
        policy
        for policy in graph_payload["dynamic_policies"]["resume_policies"]
        if policy["source_node_id"] != "mechanic"
    ]
    _write_json(graph_path, graph_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        f"runtime failure policy {FIXTURE_REPAIR_POLICY_ID} target node mechanic "
        "lacks resume guard for MECHANIC_COMPLETE"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_non_default_repair_route_partial_mutation_without_support(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_default_fixture_assets(tmp_path)
    _configure_non_default_repair_route(assets_root)

    policies_path = _runtime_failure_policies_path(assets_root)
    policies_payload = _load_json(policies_path)
    fixture_policy = next(
        definition
        for definition in policies_payload["definitions"]
        if definition["policy_id"] == FIXTURE_REPAIR_POLICY_ID
    )
    fixture_policy["applies_to_mutation_phases"] = ["partial_mutation"]
    _write_json(policies_path, policies_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        f"runtime failure policy {FIXTURE_REPAIR_POLICY_ID} applies to partial mutation but "
        "repair closure fixture_echo_effect/fixture_effect_input_missing does not support "
        "partial mutation"
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
        "runtime failure policy blueprint_approval_pre_mutation_effect_validation target node "
        "mechanic_blueprint outcome MECHANIC_BLUEPRINT_COMPLETE does not invoke repair "
        "operation mechanic_blueprint_repair_apply"
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
        "runtime failure policy blueprint_approval_pre_mutation_effect_validation target node "
        "mechanic_blueprint lacks resume guard for MECHANIC_BLUEPRINT_COMPLETE"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_blueprint_recovery_route_without_declared_repair_artifact_emission(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    operations_path = _runtime_effect_operations_path(assets_root)
    operations_payload = _load_json(operations_path)
    evaluator_operation = next(
        definition
        for definition in operations_payload["definitions"]
        if definition["operation_id"] == "evaluator_blueprint_approved_to_task"
    )
    for contract in evaluator_operation["repair_closure_contracts"]:
        if contract["failure_class"] == "generated_task_missing":
            contract["required_repair_evidence_artifact_ids"] = [
                "blueprint_repair_decision",
                "mechanic_report",
                "generated_task",
            ]
    _write_json(operations_path, operations_payload)

    policies_path = _runtime_failure_policies_path(assets_root)
    policies_payload = _load_json(policies_path)
    policy = next(
        definition
        for definition in policies_payload["definitions"]
        if definition["policy_id"] == "blueprint_approval_pre_mutation_effect_validation"
    )
    for mapping in policy["repair_closure_mappings"]:
        if mapping["failure_class"] == "generated_task_missing":
            mapping["required_repair_evidence_artifact_ids"] = [
                "blueprint_repair_decision",
                "mechanic_report",
                "generated_task",
            ]
    _write_json(policies_path, policies_payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy blueprint_approval_pre_mutation_effect_validation repair "
        "closure evaluator_blueprint_approved_to_task/generated_task_missing requires evidence "
        "artifact generated_task not emitted by target node mechanic_blueprint"
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


def test_compile_rejects_route_to_node_policy_with_ambiguous_multiclass_closure_without_explicit_mappings(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policies_path = _runtime_failure_policies_path(assets_root)
    payload = _load_json(policies_path)
    blueprint_policy = next(
        definition
        for definition in payload["definitions"]
        if definition["policy_id"] == "blueprint_approval_pre_mutation_effect_validation"
    )
    blueprint_policy.pop("repair_closure_mappings")
    _write_json(policies_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy blueprint_approval_pre_mutation_effect_validation must declare "
        "repair_closure_mappings for multi-operation or multi-failure-class route_to_node scope"
    ) in _diagnostic_text(outcome)


def test_compile_accepts_single_closure_route_to_node_policy_without_explicit_mapping(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policies_path = _runtime_failure_policies_path(assets_root)
    payload = _load_json(policies_path)
    blueprint_policy = next(
        definition
        for definition in payload["definitions"]
        if definition["policy_id"] == "blueprint_approval_pre_mutation_effect_validation"
    )
    blueprint_policy["applies_to_failure_classes"] = ["generated_task_missing"]
    blueprint_policy.pop("repair_closure_mappings")
    _write_json(policies_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True, outcome.diagnostics.errors
    assert outcome.active_plan is not None


def test_compile_rejects_route_to_node_policy_target_without_local_repair_role(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    _append_runtime_failure_policy(
        assets_root,
        _runtime_effect_policy(
            policy_id="target_without_repair_role",
            target_node_id="contractor_blueprint",
        ),
    )

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy target_without_repair_role target node contractor_blueprint "
        "must declare recovery_role=local_repair"
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


def test_compile_materializes_stage_kind_request_context_authority_when_graph_omits_it(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = _load_json(graph_path)
    payload["nodes"][0].pop("request_context_profile_id", None)
    payload["nodes"][0].pop("context_render_plan_id", None)
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True, outcome.diagnostics.errors
    assert outcome.active_plan is not None
    builder = next(
        node for node in outcome.active_plan.execution_graph.nodes if node.node_id == "builder"
    )
    assert builder.request_context_profile_id == "builder.default"
    assert builder.context_render_plan_id == "stage_request.default.v1"


def test_compile_rejects_missing_request_context_authority_from_graph_and_stage_kind(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    graph_payload = _load_json(graph_path)
    graph_payload["nodes"][0].pop("request_context_profile_id", None)
    graph_payload["nodes"][0].pop("context_render_plan_id", None)
    _write_json(graph_path, graph_payload)

    stage_kind_path = _stage_kind_path(assets_root, "execution", "builder")
    stage_kind_payload = _load_json(stage_kind_path)
    stage_kind_payload.pop("request_context_profile_id", None)
    stage_kind_payload.pop("context_render_plan_id", None)
    _write_json(stage_kind_path, stage_kind_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "graph node builder has no request context profile" in _diagnostic_text(outcome)


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


def test_compile_rejects_graph_with_duplicate_outcome_routes(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = assets_root / "graphs" / "execution" / "standard.json"
    payload = _load_json(graph_path)
    duplicate = next(
        edge
        for edge in payload["edges"]
        if edge["edge_id"] == "builder-complete-to-checker"
    ).copy()
    duplicate["edge_id"] = "builder-complete-to-checker-duplicate"
    payload["edges"].append(duplicate)
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "graph execution.standard node builder has multiple routes for outcome "
        "BUILDER_COMPLETE"
    ) in _diagnostic_text(outcome)


def test_graph_validator_rejects_entry_walk_with_unknown_target_node(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True, _diagnostic_text(outcome)
    assert outcome.active_plan is not None

    execution_graph = outcome.active_plan.execution_graph
    transitions = list(execution_graph.compiled_transitions)
    mutated_transition_index = next(
        index
        for index, transition in enumerate(transitions)
        if transition.edge_id == "builder-complete-to-checker"
    )
    transitions[mutated_transition_index] = transitions[mutated_transition_index].model_copy(
        update={"target_node_id": "ghost_node"}
    )
    mutated_execution_graph = execution_graph.model_copy(
        update={"compiled_transitions": tuple(transitions)}
    )
    stage_kinds = {
        stage_kind.stage_kind_id: stage_kind
        for stage_kind in discover_stage_kind_definitions(assets_root=assets_root)
    }

    with pytest.raises(CompilerValidationError, match="entry walk reached unknown node ghost_node"):
        validate_structural_graph_smoke(
            graphs_by_plane={Plane.EXECUTION: mutated_execution_graph},
            stage_kinds=stage_kinds,
            terminal_actions_by_id=outcome.active_plan.terminal_actions_by_id,
        )


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


def test_compile_rejects_custom_stage_kind_without_runtime_stage(
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
    stage_kind_payload.pop("runtime_stage", None)
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
    assert "Invalid stage kind definition in asset" in _diagnostic_text(outcome)
    assert "diagnostician.json" in _diagnostic_text(outcome)


def test_compile_rejects_stage_kind_runtime_stage_outside_plane(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    builder_path = assets_root / "registry" / "stage_kinds" / "execution" / "builder.json"
    builder_payload = _load_json(builder_path)
    builder_payload["runtime_stage"] = "manager"
    _write_json(builder_path, builder_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "Invalid stage kind definition in asset" in _diagnostic_text(outcome)
    assert "builder.json" in _diagnostic_text(outcome)


def test_compile_rejects_runtime_failure_recovery_node_without_local_repair_role(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    graph_path = _planning_standard_graph_path(assets_root)
    payload = _load_json(graph_path)
    payload["runtime_failure_recovery"]["default_repair_node_id"] = "manager"
    _write_json(graph_path, payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "graph planning.standard runtime failure recovery node manager must declare "
        "recovery_role=local_repair"
    ) in _diagnostic_text(outcome)


def test_compile_accepts_runtime_failure_recovery_node_with_noncanonical_runtime_stage(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    stage_kind_path = assets_root / "registry" / "stage_kinds" / "planning" / "mechanic.json"
    stage_kind_payload = _load_json(stage_kind_path)
    stage_kind_payload.update(
        {
            "stage_kind_id": "diagnostician",
            "runtime_stage": "mechanic",
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
    for map_name in (
        "stage_runner_bindings",
        "stage_model_bindings",
        "stage_thinking_bindings",
        "stage_entrypoint_overrides",
        "stage_skill_additions",
    ):
        mapping = mode_payload.get(map_name)
        if not isinstance(mapping, dict) or "mechanic" not in mapping:
            continue
        mapping["diagnostician"] = mapping.pop("mechanic")
    _write_json(mode_path, mode_payload)

    outcome = _compile_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    mechanic_node = next(
        node for node in outcome.active_plan.planning_graph.nodes if node.node_id == "mechanic"
    )
    assert mechanic_node.stage_kind_id == "diagnostician"
    assert mechanic_node.runtime_stage is not None
    assert mechanic_node.runtime_stage.value == "mechanic"


# ---------------------------------------------------------------------------
# Config-driven behavior tests: graph-only route changes
# ---------------------------------------------------------------------------


class TestConfigDrivenGraphRouting:
    """A graph-only route change (different loop graph asset) alters
    runtime dispatch without runtime code edits.

    Config dependency:
    - assets/graphs/execution/standard.json — builder→checker
    - assets/graphs/execution/with_integrator.json — builder→integrator
    """

    def test_standard_graph_routes_builder_complete_to_checker(
        self, tmp_path: Path
    ) -> None:
        """With the standard execution graph, BUILDER_COMPLETE routes to
        checker.

        Config asset: assets/graphs/execution/standard.json
        """
        assets_root = _copy_builtin_assets(tmp_path)
        outcome = _compile_with_assets(tmp_path, assets_root)
        assert outcome.diagnostics.ok
        assert outcome.active_plan is not None

        exec_graph = outcome.active_plan.graphs_by_plane[Plane.EXECUTION]

        # Find builder→checker transition in compiled graph
        builder_to_checker = [
            t for t in exec_graph.compiled_transitions
            if t.source_node_id == "builder"
            and t.outcome == "BUILDER_COMPLETE"
        ]
        assert len(builder_to_checker) == 1
        assert builder_to_checker[0].target_node_id == "checker"

    def test_integrator_graph_routes_builder_complete_to_integrator(
        self, tmp_path: Path
    ) -> None:
        """With the with_integrator execution graph, BUILDER_COMPLETE routes
        to integrator instead of checker.

        Config asset: assets/graphs/execution/with_integrator.json
        """
        assets_root = _copy_builtin_assets(tmp_path)

        # Use default_codex_integrated which references execution.with_integrator
        workspace_root = tmp_path / "workspace"
        bootstrap_workspace(workspace_root)
        outcome = compile_and_persist_workspace_plan(
            workspace_root,
            config=RuntimeConfig(),
            requested_mode_id="default_codex_integrated",
            assets_root=assets_root,
        )
        assert outcome.diagnostics.ok
        assert outcome.active_plan is not None

        exec_graph = outcome.active_plan.graphs_by_plane[Plane.EXECUTION]

        # Find builder→integrator transition
        builder_transitions = [
            t for t in exec_graph.compiled_transitions
            if t.source_node_id == "builder"
            and t.outcome == "BUILDER_COMPLETE"
        ]
        assert len(builder_transitions) == 1
        assert builder_transitions[0].target_node_id == "integrator"

    def test_graph_only_change_alters_dispatch_without_code_edits(
        self, tmp_path: Path
    ) -> None:
        """Two modes that differ only in their loop graph asset produce
        different compiled transitions for the same stage outcome.
        No runtime code edits are needed.

        Config assets:
        - assets/graphs/execution/standard.json
        - assets/graphs/execution/with_integrator.json
        """
        assets_root = _copy_builtin_assets(tmp_path)

        # Compile with standard graph
        ws_standard = tmp_path / "ws_standard"
        bootstrap_workspace(ws_standard)
        outcome_standard = compile_and_persist_workspace_plan(
            ws_standard,
            config=RuntimeConfig(),
            requested_mode_id="default_codex",
            assets_root=assets_root,
        )

        # Compile with integrator graph
        ws_integrator = tmp_path / "ws_integrator"
        bootstrap_workspace(ws_integrator)
        outcome_integrator = compile_and_persist_workspace_plan(
            ws_integrator,
            config=RuntimeConfig(),
            requested_mode_id="default_codex_integrated",
            assets_root=assets_root,
        )

        assert outcome_standard.diagnostics.ok
        assert outcome_integrator.diagnostics.ok
        assert outcome_standard.active_plan is not None
        assert outcome_integrator.active_plan is not None

        graph_std = outcome_standard.active_plan.graphs_by_plane[Plane.EXECUTION]
        graph_int = outcome_integrator.active_plan.graphs_by_plane[Plane.EXECUTION]

        # Same outcome, different routing targets
        std_target = next(
            t.target_node_id for t in graph_std.compiled_transitions
            if t.source_node_id == "builder" and t.outcome == "BUILDER_COMPLETE"
        )
        int_target = next(
            t.target_node_id for t in graph_int.compiled_transitions
            if t.source_node_id == "builder" and t.outcome == "BUILDER_COMPLETE"
        )

        # The graph-only change produces different dispatch
        assert std_target != int_target
        assert std_target == "checker"
        assert int_target == "integrator"
