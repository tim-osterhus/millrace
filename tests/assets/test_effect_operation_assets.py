from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest

from millrace_ai.assets import (
    EffectOperationAssetError,
    discover_effect_store_definitions,
    discover_effect_validator_definitions,
    discover_runtime_effect_operation_definitions,
    discover_runtime_effect_runner_definitions,
    load_builtin_workflow_primitives,
)
from millrace_ai.errors import AssetValidationError, MillraceError

ASSETS_ROOT = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"


def _copy_builtin_assets(tmp_path: Path) -> Path:
    copied_root = tmp_path / "assets"
    shutil.copytree(ASSETS_ROOT, copied_root)
    return copied_root


def test_effect_operations_module_is_assets_facade() -> None:
    effect_operations_module = importlib.import_module("millrace_ai.assets.effect_operations")
    assets_public_module = importlib.import_module("millrace_ai.assets")

    assert (
        assets_public_module.discover_runtime_effect_operation_definitions
        is effect_operations_module.discover_runtime_effect_operation_definitions
    )
    assert (
        assets_public_module.discover_effect_store_definitions
        is effect_operations_module.discover_effect_store_definitions
    )
    assert assets_public_module.EffectOperationAssetError is effect_operations_module.EffectOperationAssetError


def test_effect_operation_errors_use_project_error_hierarchy() -> None:
    assert issubclass(EffectOperationAssetError, AssetValidationError)
    assert issubclass(EffectOperationAssetError, MillraceError)


def test_builtin_effect_operation_assets_load() -> None:
    operations = discover_runtime_effect_operation_definitions()
    runners = discover_runtime_effect_runner_definitions()
    stores = discover_effect_store_definitions()
    validators = discover_effect_validator_definitions()

    assert {operation.operation_id for operation in operations} >= {
        "planner_disposition",
        "manager_blueprint_manifest_to_blueprint_drafts",
        "contractor_blueprint_candidate_persist",
        "evaluator_blueprint_approved_to_task",
        "evaluator_blueprint_rejected_to_draft_revision",
        "mechanic_blueprint_repair_apply",
    }
    assert {runner.runner_id for runner in runners} >= {"legacy_python_handler"}
    legacy_runner = next(
        runner
        for runner in runners
        if runner.runner_id == "legacy_python_handler"
    )
    assert set(legacy_runner.operation_ids) >= {
        "planner_disposition",
        "manager_blueprint_manifest_to_blueprint_drafts",
        "evaluator_blueprint_approved_to_task",
    }
    assert {store.store_id for store in stores} >= {
        "run_artifacts",
        "mutation_journal",
        "blueprint_manifests",
        "blueprint_draft_queue",
        "blueprint_active_drafts",
        "blueprint_approved_drafts",
        "blueprint_candidate_packets",
        "blueprint_candidate_markdown",
        "blueprint_rejected_packets",
        "blueprint_rejected_markdown",
        "blueprint_rejected_markdown_checksums",
        "blueprint_approved_packets",
        "blueprint_approved_markdown",
        "blueprint_approved_markdown_checksums",
        "blueprint_evaluations",
        "blueprint_critiques_open",
        "blueprint_promotions",
        "task_queue",
    }
    assert {
        validator.validator_id for validator in validators
    } >= {
        "planner_disposition.required_artifacts",
        "evaluator_blueprint_approved_to_task.generated_task_scope",
        "evaluator_blueprint_rejected_to_draft_revision.rejection_context",
        "mechanic_blueprint_repair_apply.required_artifacts",
        "mechanic_blueprint_repair_apply.repair_context",
    }
    approval_operation = next(
        operation
        for operation in operations
        if operation.operation_id == "evaluator_blueprint_approved_to_task"
    )
    assert {contract.failure_class for contract in approval_operation.repair_closure_contracts} == {
        "generated_task_missing",
        "generated_task_invalid",
    }
    assert {contract.repair_operation_id for contract in approval_operation.repair_closure_contracts} == {
        "mechanic_blueprint_repair_apply"
    }


def test_workflow_primitive_bundle_includes_effect_operation_catalogs() -> None:
    bundle = load_builtin_workflow_primitives()

    assert "planner_disposition" in {
        operation.operation_id for operation in bundle.runtime_effect_operations
    }
    assert "legacy_python_handler" in {
        runner.runner_id for runner in bundle.runtime_effect_runners
    }
    assert "mutation_journal" in {store.store_id for store in bundle.effect_stores}
    assert "planner_disposition.required_artifacts" in {
        validator.validator_id for validator in bundle.effect_validators
    }
    policies_by_id = {
        policy.policy_id: policy
        for policy in bundle.runtime_failure_policies
    }
    repair_policy = policies_by_id["blueprint_approval_pre_mutation_effect_validation"]
    assert {
        (mapping.source_operation_id, mapping.failure_class)
        for mapping in repair_policy.repair_closure_mappings
    } == {
        ("evaluator_blueprint_approved_to_task", "generated_task_missing"),
        ("evaluator_blueprint_approved_to_task", "generated_task_invalid"),
    }
    assert {mapping.repair_operation_id for mapping in repair_policy.repair_closure_mappings} == {
        "mechanic_blueprint_repair_apply"
    }


def test_discover_runtime_effect_operations_rejects_duplicate_ids(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    operations_dir = assets_root / "registry" / "runtime_effect_operations"
    default_path = operations_dir / "default_runtime_effect_operations.json"
    duplicate_path = operations_dir / "duplicate_planner.json"
    payload = json.loads(default_path.read_text(encoding="utf-8"))
    duplicate_path.write_text(
        json.dumps(payload["definitions"][0], indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        EffectOperationAssetError,
        match=r"Duplicate discovered runtime effect operation id: planner_disposition",
    ):
        discover_runtime_effect_operation_definitions(assets_root=assets_root)


def test_discover_runtime_effect_runners_rejects_duplicate_ids(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    runners_dir = assets_root / "registry" / "runtime_effect_runners"
    default_path = runners_dir / "default_effect_runners.json"
    duplicate_path = runners_dir / "duplicate_legacy_runner.json"
    payload = json.loads(default_path.read_text(encoding="utf-8"))
    duplicate_path.write_text(
        json.dumps(payload["definitions"][0], indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        AssetValidationError,
        match=r"Duplicate discovered runtime effect runner id: legacy_python_handler",
    ):
        discover_runtime_effect_runner_definitions(assets_root=assets_root)


def test_invalid_effect_store_error_includes_path(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    stores_path = assets_root / "registry" / "effect_stores" / "default_effect_stores.json"
    payload = json.loads(stores_path.read_text(encoding="utf-8"))
    payload["definitions"][0]["runtime_relative_root"] = "../runs"
    stores_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(
        EffectOperationAssetError,
        match=r"Invalid runtime effect store definition in asset: .*default_effect_stores\.json",
    ):
        discover_effect_store_definitions(assets_root=assets_root)
