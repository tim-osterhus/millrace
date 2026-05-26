from __future__ import annotations

import json
import shutil
from pathlib import Path

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import bootstrap_workspace

ASSETS_ROOT = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"


def _copy_builtin_assets(tmp_path: Path) -> Path:
    copied_root = tmp_path / "assets"
    shutil.copytree(ASSETS_ROOT, copied_root)
    return copied_root


def _compile_blueprint_with_assets(tmp_path: Path, assets_root: Path):
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    return compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_codex",
        assets_root=assets_root,
    )


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _runtime_effect_rules_path(assets_root: Path) -> Path:
    return assets_root / "registry" / "runtime_effect_rules" / "blueprint_effect_rules.json"


def _runtime_effect_operations_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_effect_operations"
        / "default_runtime_effect_operations.json"
    )


def _effect_validators_path(assets_root: Path) -> Path:
    return assets_root / "registry" / "effect_validators" / "default_effect_validators.json"


def _effect_stores_path(assets_root: Path) -> Path:
    return assets_root / "registry" / "effect_stores" / "default_effect_stores.json"


def _diagnostic_text(outcome) -> str:
    return "\n".join(outcome.diagnostics.errors)


def _manager_operation(payload: dict) -> dict:
    return next(
        definition
        for definition in payload["definitions"]
        if definition["operation_id"] == "manager_blueprint_manifest_to_blueprint_drafts"
    )


def test_compile_freezes_effect_operation_catalogs(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    plan = outcome.active_plan
    assert "manager_blueprint_manifest_to_blueprint_drafts" in plan.runtime_effect_operations_by_id
    assert "mutation_journal" in plan.effect_stores_by_id
    assert "manager_blueprint_manifest_to_blueprint_drafts.required_artifacts" in plan.effect_validators_by_id
    assert any(
        ref.asset_family == "runtime_effect_operation"
        and ref.logical_id == "runtime_effect_operation:manager_blueprint_manifest_to_blueprint_drafts"
        for ref in plan.resolved_assets
    )


def test_compile_rejects_effect_rule_with_unknown_operation(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    rules_path = _runtime_effect_rules_path(assets_root)
    payload = _load_json(rules_path)
    payload["definitions"][0]["effect_operation_id"] = "missing_operation"
    _write_json(rules_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect rule manager_blueprint_manifest_to_blueprint_drafts "
        "references unknown operation missing_operation"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_effect_operation_with_unknown_primitive(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    operations_path = _runtime_effect_operations_path(assets_root)
    payload = _load_json(operations_path)
    _manager_operation(payload)["steps"][0]["primitive_id"] = "unknown_primitive"
    _write_json(operations_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect operation manager_blueprint_manifest_to_blueprint_drafts step "
        "validate_required_artifacts references unknown primitive unknown_primitive"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_effect_operation_with_unknown_store(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    operations_path = _runtime_effect_operations_path(assets_root)
    payload = _load_json(operations_path)
    _manager_operation(payload)["steps"][1]["store_id"] = "missing_store"
    _write_json(operations_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect operation manager_blueprint_manifest_to_blueprint_drafts step "
        "dispatch_legacy_handler references unknown store missing_store"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_effect_operation_with_unknown_validator(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    operations_path = _runtime_effect_operations_path(assets_root)
    payload = _load_json(operations_path)
    _manager_operation(payload)["steps"][0]["validator_ids"].append("missing_validator")
    _write_json(operations_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect operation manager_blueprint_manifest_to_blueprint_drafts step "
        "validate_required_artifacts references unknown validator missing_validator"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_effect_validator_bound_to_unowned_artifact(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    validators_path = _effect_validators_path(assets_root)
    payload = _load_json(validators_path)
    validator = next(
        definition
        for definition in payload["definitions"]
        if definition["validator_id"] == "manager_blueprint_manifest_to_blueprint_drafts.required_artifacts"
    )
    validator["input_artifact_ids"].append("generated_task")
    _write_json(validators_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect operation manager_blueprint_manifest_to_blueprint_drafts binds validator "
        "manager_blueprint_manifest_to_blueprint_drafts.required_artifacts to artifact generated_task "
        "not declared by the operation"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_unsafe_effect_store_path(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    stores_path = _effect_stores_path(assets_root)
    payload = _load_json(stores_path)
    payload["definitions"][0]["runtime_relative_root"] = "../runs"
    _write_json(stores_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "Invalid runtime effect store definition in asset" in _diagnostic_text(outcome)


def test_compile_rejects_multi_write_operation_without_partial_policy(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    operations_path = _runtime_effect_operations_path(assets_root)
    payload = _load_json(operations_path)
    operation = _manager_operation(payload)
    operation["steps"].append(
        {
            "step_id": "append_second_journal_record",
            "primitive_id": "mutation_journal_append",
            "mutation_phase": "partial_mutation",
            "store_id": "mutation_journal",
            "writes_store": True,
        }
    )
    operation["partial_commit_policy"] = None
    _write_json(operations_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect operation manager_blueprint_manifest_to_blueprint_drafts "
        "has multiple write steps without partial_commit_policy"
    ) in _diagnostic_text(outcome)
