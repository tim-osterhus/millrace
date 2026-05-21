from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from millrace_ai.assets import (
    SHIPPED_WORK_ITEM_FAMILY_IDS,
    WorkflowAssetError,
    discover_artifact_contract_definitions,
    discover_plane_queue_claim_policy_definitions,
    discover_request_context_profile_definitions,
    discover_work_item_family_definitions,
    discover_workspace_schema_epoch_definitions,
    load_builtin_work_item_family_definitions,
    load_builtin_workflow_primitives,
    load_workspace_schema_epoch_definition,
)
from millrace_ai.contracts import Plane
from millrace_ai.errors import AssetValidationError, MillraceError

ASSETS_ROOT = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "millrace_ai"

_ARTIFACT_REFERENCE_FIELDS = frozenset(
    {
        "allowed_input_artifacts",
        "declared_output_artifacts",
        "emits_artifacts",
        "required_artifacts",
        "optional_artifacts",
        "required_run_artifacts",
        "output_path_preferences",
    }
)

_KNOWN_REQUIRED_ARTIFACT_IDS = frozenset(
    {
        "blueprint_candidate",
        "blueprint_critique",
        "blueprint_drafts",
        "blueprint_evaluation",
        "blueprint_evaluation_report",
        "blueprint_failure",
        "blueprint_manifest",
        "blueprint_markdown",
        "blueprint_packet",
        "generated_spec",
        "generated_task",
        "incident_packet",
        "incident_report",
        "manager_blueprint_report",
        "recon_packet",
        "repaired_blueprint_artifact",
        "planner_disposition",
        "report",
        "research_packet",
        "rubric",
        "skill_install_report",
        "spec",
        "spec_packet",
        "stage_result",
        "task",
        "task_cards",
        "task_packet",
        "verdict",
    }
)

_REQUEST_CONTEXT_FILENAME_TO_ARTIFACT_ID = {
    "arbiter_report.md": "report",
    "blueprint.md": "blueprint_markdown",
    "blueprint_drafts.json": "blueprint_drafts",
    "blueprint_evaluation.json": "blueprint_evaluation",
    "blueprint_evaluation_report.md": "blueprint_evaluation_report",
    "blueprint_manifest.json": "blueprint_manifest",
    "blueprint_packet.json": "blueprint_packet",
    "contractor_blueprint_report.md": "report",
    "critique_packet.json": "blueprint_critique",
    "generated_spec.md": "generated_spec",
    "generated_task.md": "generated_task",
    "manager_blueprint_report.md": "manager_blueprint_report",
    "mechanic_blueprint_report.md": "mechanic_report",
    "planner_disposition.json": "planner_disposition",
    "recon_packet.md": "recon_packet",
    "runtime_error_report.md": "runtime_error_context",
    "troubleshoot_report.md": "troubleshoot_report",
}


def _copy_builtin_assets(tmp_path: Path) -> Path:
    copied_root = tmp_path / "assets"
    shutil.copytree(ASSETS_ROOT, copied_root)
    return copied_root


def _walk_artifact_references(payload: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in _ARTIFACT_REFERENCE_FIELDS:
                if isinstance(value, dict):
                    found.update(str(item) for item in value)
                elif isinstance(value, list):
                    found.update(str(item) for item in value)
                elif isinstance(value, tuple):
                    found.update(str(item) for item in value)
            found.update(_walk_artifact_references(value))
    elif isinstance(payload, list):
        for item in payload:
            found.update(_walk_artifact_references(item))
    return found


def _shipped_json_artifact_references() -> set[str]:
    artifact_ids: set[str] = set()
    for path in sorted((ASSETS_ROOT / "graphs").rglob("*.json")):
        artifact_ids.update(_walk_artifact_references(json.loads(path.read_text(encoding="utf-8"))))
    for registry_dir in (
        ASSETS_ROOT / "registry" / "stage_kinds",
        ASSETS_ROOT / "registry" / "runtime_effect_handlers",
        ASSETS_ROOT / "registry" / "runtime_effect_rules",
        ASSETS_ROOT / "registry" / "request_context_profiles",
    ):
        for path in sorted(registry_dir.rglob("*.json")):
            artifact_ids.update(_walk_artifact_references(json.loads(path.read_text(encoding="utf-8"))))
    return artifact_ids


def _known_request_context_and_handoff_artifact_references() -> set[str]:
    sources = (
        SOURCE_ROOT / "runtime" / "request_context.py",
        SOURCE_ROOT / "runtime" / "stage_requests.py",
        SOURCE_ROOT / "runtime" / "recon_transitions.py",
        SOURCE_ROOT / "runtime" / "error_recovery.py",
    )
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    artifact_ids: set[str] = set()
    for filename, artifact_id in _REQUEST_CONTEXT_FILENAME_TO_ARTIFACT_ID.items():
        if filename in source_text:
            artifact_ids.add(artifact_id)
    return artifact_ids


def _write_custom_family_assets(assets_root: Path) -> None:
    family_dir = assets_root / "registry" / "work_item_families"
    adapter_dir = assets_root / "registry" / "document_adapters"
    family_dir.mkdir(parents=True, exist_ok=True)
    adapter_dir.mkdir(parents=True, exist_ok=True)
    (family_dir / "custom_review.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "work_item_family",
                "family_id": "custom_review",
                "plane": "planning",
                "entry_key": "custom_review",
                "display_name": "Custom Review",
                "document_kind": "custom_review",
                "runtime_relative_dir": "custom/reviews",
                "file_extension": ".json",
                "schema_id": "custom_review_document_v1",
                "document_adapter_id": "custom_review_json_v1",
                "queue_dirs": {
                    "queue": "custom/reviews/queue",
                    "active": "custom/reviews/active",
                    "done": "custom/reviews/done",
                    "blocked": "custom/reviews/blocked",
                    "canceled": "custom/reviews/canceled",
                },
                "lifecycle_states": ["queue", "active", "done", "blocked", "canceled"],
                "claimable_state": "queue",
                "active_state": "active",
                "done_state": "done",
                "blocked_state": "blocked",
                "canceled_state": "canceled",
                "closure_blocking_states": ["queue", "active", "blocked"],
                "default_entry_key": "custom_review",
                "id_field": "custom_id",
                "created_at_field": "created_at",
                "lineage_fields": ["root_spec_id"],
                "operator_capabilities": ["cancel", "inspect"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (adapter_dir / "custom_review_json_v1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "work_item_document_adapter",
                "adapter_id": "custom_review_json_v1",
                "schema_id": "custom_review_document_v1",
                "supported_file_extensions": [".json"],
                "family_ids": ["custom_review"],
                "can_parse": True,
                "can_render": True,
                "can_summarize": True,
                "supports_dependencies": False,
                "supports_lineage": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_workflow_assets_module_is_assets_facade() -> None:
    workflows_module = importlib.import_module("millrace_ai.assets.workflows")
    assets_public_module = importlib.import_module("millrace_ai.assets")

    assert (
        assets_public_module.load_builtin_workflow_primitives
        is workflows_module.load_builtin_workflow_primitives
    )
    assert (
        assets_public_module.discover_work_item_family_definitions
        is workflows_module.discover_work_item_family_definitions
    )
    assert assets_public_module.WorkflowAssetError is workflows_module.WorkflowAssetError


def test_workflow_asset_errors_use_project_error_hierarchy() -> None:
    assert issubclass(WorkflowAssetError, AssetValidationError)
    assert issubclass(WorkflowAssetError, MillraceError)


def test_builtin_work_item_families_load_current_queue_families() -> None:
    families = load_builtin_work_item_family_definitions()
    by_id = {family.family_id: family for family in families}

    assert [family.family_id for family in families] == list(SHIPPED_WORK_ITEM_FAMILY_IDS)
    assert set(by_id) == {
        "task",
        "spec",
        "probe",
        "incident",
        "learning_request",
        "blueprint_draft",
        "blueprint_draft",
    }
    assert by_id["task"].plane is Plane.EXECUTION
    assert by_id["task"].queue_dirs.queue == "tasks/queue"
    assert by_id["task"].done_state == "done"
    assert by_id["spec"].plane is Plane.PLANNING
    assert by_id["probe"].plane is Plane.PLANNING
    assert by_id["incident"].claimable_state == "incoming"
    assert by_id["incident"].done_state == "resolved"
    assert by_id["learning_request"].plane is Plane.LEARNING
    assert by_id["blueprint_draft"].plane is Plane.PLANNING
    assert by_id["blueprint_draft"].file_extension == ".json"


def test_builtin_workflow_primitives_load_as_bundle() -> None:
    bundle = load_builtin_workflow_primitives()

    assert {family.family_id for family in bundle.work_item_families} == {
        "task",
        "spec",
        "probe",
        "incident",
        "learning_request",
        "blueprint_draft",
    }
    assert {adapter.adapter_id for adapter in bundle.document_adapters} == {
        "builtin_markdown_v1",
        "blueprint_draft_markdown_v1",
    }
    adapter = next(
        adapter for adapter in bundle.document_adapters if adapter.adapter_id == "builtin_markdown_v1"
    )
    assert set(adapter.family_ids) == {
        "task",
        "spec",
        "probe",
        "incident",
        "learning_request",
    }
    assert {policy.plane for policy in bundle.queue_claim_policies} == {
        Plane.EXECUTION,
        Plane.PLANNING,
        Plane.LEARNING,
    }
    planning_claim_policy = next(
        policy for policy in bundle.queue_claim_policies if policy.plane is Plane.PLANNING
    )
    assert planning_claim_policy.family_order == ("incident", "blueprint_draft", "probe", "spec")
    assert {action.terminal_action_id for action in bundle.terminal_actions} >= {
        "complete_work_item",
        "block_work_item",
        "idle_plane",
    }
    assert {plan.plan_id for plan in bundle.lifecycle_mutation_plans} >= {
        "complete_work_item",
        "block_work_item",
    }
    assert {handler.handler_id for handler in bundle.runtime_effect_handlers} >= {
        "planner_disposition",
        "manager_blueprint_manifest_to_blueprint_drafts",
        "contractor_blueprint_candidate_persist",
        "evaluator_blueprint_approved_to_task",
        "evaluator_blueprint_rejected_to_draft_revision",
    }
    assert {rule.rule_id for rule in bundle.runtime_effect_rules} >= {
        "planner_disposition_on_complete",
        "planner_disposition_on_blocked",
        "manager_blueprint_manifest_to_blueprint_drafts",
        "contractor_blueprint_candidate_persist",
        "evaluator_blueprint_approved_to_task",
        "evaluator_blueprint_rejected_to_draft_revision",
    }
    assert "generated_task" in {contract.artifact_id for contract in bundle.artifact_contracts}
    assert "evaluator_blueprint.default" in {
        profile.profile_id for profile in bundle.request_context_profiles
    }
    assert bundle.recovery_policies
    assert bundle.runtime_failure_policies
    assert bundle.workspace_schema_epoch is not None
    assert bundle.workspace_schema_epoch.epoch_id == "v0.20"


def test_shipped_artifact_inventory_has_packaged_contracts() -> None:
    referenced_artifact_ids = (
        _shipped_json_artifact_references()
        | _known_request_context_and_handoff_artifact_references()
        | _KNOWN_REQUIRED_ARTIFACT_IDS
    )
    contract_ids = {contract.artifact_id for contract in discover_artifact_contract_definitions()}

    assert referenced_artifact_ids <= contract_ids


def test_discover_artifact_contract_definitions_loads_default_contracts() -> None:
    contracts = discover_artifact_contract_definitions()
    by_id = {contract.artifact_id: contract for contract in contracts}

    assert "generated_task" in by_id
    generated_task = by_id["generated_task"]
    assert generated_task.canonical_filename == "generated_task.json"
    assert generated_task.accepted_filenames == ("generated_task.md",)
    assert generated_task.filename_adapters_by_name["generated_task.json"].parser_id == "builtin.json"
    assert generated_task.filename_adapters_by_name["generated_task.md"].parser_id == "builtin.markdown"
    assert by_id["blueprint_critique"].canonical_filename == "blueprint_critique.json"
    assert "critique_packet.json" in by_id["blueprint_critique"].accepted_filenames
    assert by_id["blueprint_evaluation_report"].canonical_filename == "evaluator_blueprint_report.md"
    assert "blueprint_evaluation_report.md" in by_id["blueprint_evaluation_report"].accepted_filenames
    assert by_id["planner_disposition"].canonical_filename == "planner_disposition.json"
    assert by_id["planner_disposition"].schema_id == "planner_disposition_document_v1"


def test_shipped_artifact_contract_filenames_have_one_owner() -> None:
    collisions: dict[str, list[str]] = {}

    for contract in discover_artifact_contract_definitions():
        for filename in contract.all_filenames:
            collisions.setdefault(filename, []).append(contract.artifact_id)

    assert {
        filename: artifact_ids
        for filename, artifact_ids in collisions.items()
        if len(artifact_ids) > 1
    } == {}


def test_discover_request_context_profile_definitions_loads_default_profiles() -> None:
    profiles = discover_request_context_profile_definitions()
    by_id = {profile.profile_id: profile for profile in profiles}

    assert by_id["builder.default"].output_path_preferences == {
        "builder_summary": "builder_summary.md",
    }
    assert by_id["checker.default"].output_path_preferences == {
        "report": "report.md",
    }
    assert "evaluator_blueprint.default" in by_id
    assert by_id["evaluator_blueprint.default"].output_path_preferences == {
        "blueprint_evaluation": "blueprint_evaluation.json",
        "blueprint_critique": "blueprint_critique.json",
        "generated_task": "generated_task.json",
        "blueprint_evaluation_report": "evaluator_blueprint_report.md",
    }


def test_workflow_primitives_discover_custom_work_item_families(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    _write_custom_family_assets(assets_root)

    bundle = load_builtin_workflow_primitives(assets_root=assets_root)

    assert "custom_review" in {family.family_id for family in bundle.work_item_families}
    assert "custom_review_json_v1" in {adapter.adapter_id for adapter in bundle.document_adapters}


def test_builtin_queue_claim_policies_and_schema_epoch_load() -> None:
    policies = discover_plane_queue_claim_policy_definitions()
    by_plane = {policy.plane: policy for policy in policies}

    assert set(by_plane) == {Plane.EXECUTION, Plane.PLANNING, Plane.LEARNING}
    assert by_plane[Plane.EXECUTION].family_order == ("task",)
    assert by_plane[Plane.PLANNING].empty_behavior == "check_completion"

    epochs = discover_workspace_schema_epoch_definitions()
    assert [epoch.epoch_id for epoch in epochs] == ["v0.20"]
    assert load_workspace_schema_epoch_definition() == epochs[0]


def test_discover_work_item_family_definitions_rejects_duplicate_ids(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    task_path = assets_root / "registry" / "work_item_families" / "task.json"
    duplicate_path = assets_root / "registry" / "work_item_families" / "duplicate_task.json"
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    payload["display_name"] = "Duplicate Task"
    duplicate_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(WorkflowAssetError, match=r"Duplicate discovered work item family id: task"):
        discover_work_item_family_definitions(assets_root=assets_root)


def test_invalid_workflow_asset_error_includes_path(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    task_path = assets_root / "registry" / "work_item_families" / "task.json"
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    payload["runtime_relative_dir"] = "../tasks"
    task_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(WorkflowAssetError, match=r"Invalid work item family definition in asset: .*task\.json"):
        load_builtin_work_item_family_definitions(assets_root=assets_root)
