from __future__ import annotations

import json
import shutil
from pathlib import Path

from millrace_ai.compilation.fingerprints import build_existing_plan_input_fingerprint
from millrace_ai.compiler import compile_and_persist_workspace_plan, inspect_workspace_plan_currentness
from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import bootstrap_workspace, workspace_paths

ASSETS_ROOT = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
FIXTURE_HANDLER_ID = "fixture_echo_effect"
FIXTURE_ASSETS_ROOT = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "non_blueprint_effect_assets"
)


def _copy_builtin_assets(tmp_path: Path) -> Path:
    copied_root = tmp_path / "assets"
    shutil.copytree(ASSETS_ROOT, copied_root)
    return copied_root


def _copy_non_blueprint_fixture_assets(tmp_path: Path) -> Path:
    copied_root = _copy_builtin_assets(tmp_path)
    shutil.copytree(FIXTURE_ASSETS_ROOT, copied_root, dirs_exist_ok=True)
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


def _compile_default_with_assets(tmp_path: Path, assets_root: Path):
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    return compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
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


def _runtime_effect_runners_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_effect_runners"
        / "default_effect_runners.json"
    )


def _runtime_effect_handlers_path(assets_root: Path) -> Path:
    return assets_root / "registry" / "runtime_effect_handlers" / "default_effect_handlers.json"


def _artifact_contracts_path(assets_root: Path) -> Path:
    return assets_root / "registry" / "artifact_contracts" / "default_artifact_contracts.json"


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


def _fixture_artifact_contracts_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "artifact_contracts"
        / "fixture_effect_artifacts.json"
    )


def _fixture_runtime_effect_rules_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_effect_rules"
        / "fixture_effect_rules.json"
    )


def _effect_validators_path(assets_root: Path) -> Path:
    return assets_root / "registry" / "effect_validators" / "default_effect_validators.json"


def _fixture_effect_stores_path(assets_root: Path) -> Path:
    return assets_root / "registry" / "effect_stores" / "fixture_effect_stores.json"


def _effect_stores_path(assets_root: Path) -> Path:
    return assets_root / "registry" / "effect_stores" / "default_effect_stores.json"


def _runtime_failure_policies_path(assets_root: Path) -> Path:
    return (
        assets_root
        / "registry"
        / "runtime_failure_policies"
        / "default_runtime_failure_policies.json"
    )


def _diagnostic_text(outcome) -> str:
    return "\n".join(outcome.diagnostics.errors)


def _manager_operation(payload: dict) -> dict:
    return next(
        definition
        for definition in payload["definitions"]
        if definition["operation_id"] == "manager_blueprint_manifest_to_blueprint_drafts"
    )


def _fixture_operation(payload: dict) -> dict:
    return next(
        definition
        for definition in payload["definitions"]
        if definition["operation_id"] == "fixture_echo_effect"
    )


def test_compile_freezes_effect_operation_catalogs(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    plan = outcome.active_plan
    assert "legacy_python_handler" in plan.runtime_effect_runners_by_id
    assert "manager_blueprint_manifest_to_blueprint_drafts" in plan.runtime_effect_operations_by_id
    assert "mutation_journal" in plan.effect_stores_by_id
    assert "manager_blueprint_manifest_to_blueprint_drafts.required_artifacts" in plan.effect_validators_by_id
    assert any(
        ref.asset_family == "runtime_effect_operation"
        and ref.logical_id == "runtime_effect_operation:manager_blueprint_manifest_to_blueprint_drafts"
        for ref in plan.resolved_assets
    )
    assert any(
        ref.asset_family == "runtime_effect_runner"
        and ref.logical_id == "runtime_effect_runner:legacy_python_handler"
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


def test_compile_rejects_effect_rule_without_operation_runner(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    runners_path = _runtime_effect_runners_path(assets_root)
    payload = _load_json(runners_path)
    legacy_runner = payload["definitions"][0]
    legacy_runner["operation_ids"].remove("manager_blueprint_manifest_to_blueprint_drafts")
    legacy_runner["legacy_handler_ids"].remove("manager_blueprint_manifest_to_blueprint_drafts")
    legacy_runner["required_runtime_capabilities_by_operation_id"].pop(
        "manager_blueprint_manifest_to_blueprint_drafts"
    )
    legacy_runner["legacy_handler_operation_ids"].pop("manager_blueprint_manifest_to_blueprint_drafts")
    legacy_runner["result_display_aliases"].pop("manager_blueprint_manifest_to_blueprint_drafts")
    _write_json(runners_path, payload)

    rules_path = _runtime_effect_rules_path(assets_root)
    rules_payload = _load_json(rules_path)
    rules_payload["definitions"][0].pop("handler_id")
    _write_json(rules_path, rules_payload)

    contracts_path = _artifact_contracts_path(assets_root)
    contracts_payload = _load_json(contracts_path)
    for contract in contracts_payload["definitions"]:
        if "manager_blueprint_manifest_to_blueprint_drafts" in contract.get("consumer_handler_ids", []):
            contract.pop("consumer_handler_ids")
            contract["consumer_operation_ids"] = ["manager_blueprint_manifest_to_blueprint_drafts"]
    _write_json(contracts_path, contracts_payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect rule manager_blueprint_manifest_to_blueprint_drafts references operation "
        "manager_blueprint_manifest_to_blueprint_drafts without a runtime effect runner"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_effect_rule_operation_handler_drift(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    rules_path = _runtime_effect_rules_path(assets_root)
    payload = _load_json(rules_path)
    payload["definitions"][0]["effect_operation_id"] = "planner_disposition"
    _write_json(rules_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect rule manager_blueprint_manifest_to_blueprint_drafts handler "
        "manager_blueprint_manifest_to_blueprint_drafts is not a legacy alias for operation "
        "planner_disposition"
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


def test_compile_rejects_runtime_failure_policy_with_unknown_operation(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    policies_path = _runtime_failure_policies_path(assets_root)
    payload = _load_json(policies_path)
    manager_policy = next(
        definition
        for definition in payload["definitions"]
        if definition["policy_id"] == "manager_blueprint_pre_mutation_artifact_repair"
    )
    manager_policy["applies_to_operation_ids"] = ["missing_operation"]
    _write_json(policies_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy manager_blueprint_pre_mutation_artifact_repair references unknown "
        "runtime effect operation missing_operation"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_runtime_failure_policy_operation_handler_drift(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    policies_path = _runtime_failure_policies_path(assets_root)
    payload = _load_json(policies_path)
    manager_policy = next(
        definition
        for definition in payload["definitions"]
        if definition["policy_id"] == "manager_blueprint_pre_mutation_artifact_repair"
    )
    manager_policy["applies_to_operation_ids"] = ["evaluator_blueprint_approved_to_task"]
    _write_json(policies_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime failure policy manager_blueprint_pre_mutation_artifact_repair handler "
        "manager_blueprint_manifest_to_blueprint_drafts is not a legacy alias for operation "
        "ids evaluator_blueprint_approved_to_task"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_stale_operation_legacy_alias_not_declared_by_runner(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    operations_path = _runtime_effect_operations_path(assets_root)
    payload = _load_json(operations_path)
    _manager_operation(payload)["legacy_handler_ids"].append("evaluator_blueprint_approved_to_task")
    _write_json(operations_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect operation manager_blueprint_manifest_to_blueprint_drafts legacy handler "
        "evaluator_blueprint_approved_to_task is not mapped by runner legacy_python_handler"
    ) in _diagnostic_text(outcome)


def test_compile_uses_runner_aliases_for_runtime_failure_policy_compatibility(
    tmp_path: Path,
) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    operations_path = _runtime_effect_operations_path(assets_root)
    payload = _load_json(operations_path)
    _manager_operation(payload)["legacy_handler_ids"] = []
    _write_json(operations_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None


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
        "parse_manifest references unknown store missing_store"
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


def test_compile_rejects_non_blueprint_fixture_unknown_validator(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_blueprint_fixture_assets(tmp_path)
    operations_path = _fixture_runtime_effect_operations_path(assets_root)
    payload = _load_json(operations_path)
    _fixture_operation(payload)["steps"][0]["validator_ids"].append("missing_fixture_validator")
    _write_json(operations_path, payload)

    outcome = _compile_default_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect operation fixture_echo_effect step validate_required_artifacts "
        "references unknown validator missing_fixture_validator"
    ) in _diagnostic_text(outcome)


def test_compile_accepts_operation_only_rule_without_legacy_handler_definition(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_blueprint_fixture_assets(tmp_path)
    rules_path = _fixture_runtime_effect_rules_path(assets_root)
    rules_payload = _load_json(rules_path)
    rules_payload["definitions"][0].pop("handler_id")
    _write_json(rules_path, rules_payload)

    contracts_path = _fixture_artifact_contracts_path(assets_root)
    contracts_payload = _load_json(contracts_path)
    contracts_payload["definitions"][0].pop("consumer_handler_ids")
    contracts_payload["definitions"][0]["consumer_operation_ids"] = ["fixture_echo_effect"]
    _write_json(contracts_path, contracts_payload)

    handlers_path = _fixture_runtime_effect_handlers_path(assets_root)
    handlers_payload = _load_json(handlers_path)
    handlers_payload["definitions"] = []
    _write_json(handlers_path, handlers_payload)

    outcome = _compile_default_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    rule = next(
        rule
        for rule in outcome.active_plan.runtime_effect_rules
        if rule.rule_id == "fixture_echo_effect_on_manager_complete"
    )
    assert rule.handler_id is None


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


def test_compile_rejects_effect_validator_with_unmapped_failure_class(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    operations_path = _runtime_effect_operations_path(assets_root)
    payload = _load_json(operations_path)
    operation = _manager_operation(payload)
    operation["failure_mappings"] = [
        mapping
        for mapping in operation["failure_mappings"]
        if mapping["failure_class"] != "blueprint_manifest_missing"
    ]
    _write_json(operations_path, payload)

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect operation manager_blueprint_manifest_to_blueprint_drafts binds validator "
        "manager_blueprint_manifest_to_blueprint_drafts.required_artifacts with unmapped failure class "
        "blueprint_manifest_missing"
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


def test_compile_rejects_non_blueprint_fixture_unsafe_store_path(tmp_path: Path) -> None:
    assets_root = _copy_non_blueprint_fixture_assets(tmp_path)
    stores_path = _fixture_effect_stores_path(assets_root)
    payload = _load_json(stores_path)
    payload["definitions"][0]["runtime_relative_root"] = "../fixture-effects"
    _write_json(stores_path, payload)

    outcome = _compile_default_with_assets(tmp_path, assets_root)

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


def test_compile_rejects_duplicate_operation_runner_ownership(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    runners_dir = assets_root / "registry" / "runtime_effect_runners"
    duplicate_path = runners_dir / "duplicate_operation_owner.json"
    duplicate_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "runtime_effect_runner",
                "runner_id": "duplicate_planner_runner",
                "operation_ids": ["planner_disposition"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    outcome = _compile_blueprint_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "runtime effect operation planner_disposition is owned by multiple runners"
    ) in _diagnostic_text(outcome)


def test_compile_rejects_legacy_handler_alias_mapped_to_multiple_runners(
    tmp_path: Path,
) -> None:
    assets_root = _copy_non_blueprint_fixture_assets(tmp_path)
    operations_path = _fixture_runtime_effect_operations_path(assets_root)
    operations_payload = _load_json(operations_path)
    second_operation = dict(operations_payload["definitions"][0])
    second_operation["operation_id"] = "fixture_echo_effect_revision"
    second_operation["display_name"] = "Non-Blueprint fixture echo effect revision"
    operations_payload["definitions"].append(second_operation)
    _write_json(operations_path, operations_payload)

    runners_path = _fixture_runtime_effect_runners_path(assets_root)
    runners_payload = _load_json(runners_path)
    runners_payload["definitions"].append(
        {
            "schema_version": "1.0",
            "kind": "runtime_effect_runner",
            "runner_id": "fixture_echo_revision_runner",
            "operation_ids": ["fixture_echo_effect_revision"],
            "legacy_handler_ids": ["fixture_echo_effect"],
        }
    )
    _write_json(runners_path, runners_payload)

    outcome = _compile_default_with_assets(tmp_path, assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert (
        "artifact contract fixture_effect_input legacy consumer handler fixture_echo_effect "
        "maps to multiple runtime effect operations or runners"
    ) in _diagnostic_text(outcome)


def test_compile_if_needed_refreshes_plan_missing_effect_operation_authority(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path)
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    paths = workspace_paths(workspace_root)
    config = RuntimeConfig()

    compiled = compile_and_persist_workspace_plan(
        workspace_root,
        config=config,
        requested_mode_id="blueprint_codex",
        assets_root=assets_root,
    )
    assert compiled.active_plan is not None
    plan = compiled.active_plan
    old_resolved_assets = tuple(
        ref
        for ref in plan.resolved_assets
        if ref.asset_family
        not in {
            "runtime_effect_operation",
            "runtime_effect_runner",
            "runtime_effect_store",
            "runtime_effect_validator",
        }
    )
    old_plan = plan.model_copy(
        update={
            "resolved_assets": old_resolved_assets,
            "runtime_effect_operations_by_id": {},
            "runtime_effect_runners_by_id": {},
            "effect_stores_by_id": {},
            "effect_validators_by_id": {},
        }
    )
    old_fingerprint = build_existing_plan_input_fingerprint(
        config=config,
        mode_id="blueprint_codex",
        plan=old_plan,
        paths=paths,
        assets_root=assets_root,
    )
    old_plan = old_plan.model_copy(update={"compile_input_fingerprint": old_fingerprint})
    (paths.state_dir / "compiled_plan.json").write_text(
        old_plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=config,
        requested_mode_id="blueprint_codex",
        assets_root=assets_root,
    )
    assert currentness.state == "stale"

    refreshed = compile_and_persist_workspace_plan(
        workspace_root,
        config=config,
        requested_mode_id="blueprint_codex",
        assets_root=assets_root,
        compile_if_needed=True,
    )

    assert refreshed.diagnostics.ok is True
    assert refreshed.active_plan is not None
    assert refreshed.active_plan.runtime_effect_runners_by_id
    assert refreshed.active_plan.runtime_effect_operations_by_id
    assert refreshed.active_plan.effect_stores_by_id
    assert refreshed.active_plan.effect_validators_by_id
    assert any(
        ref.asset_family == "runtime_effect_operation"
        for ref in refreshed.active_plan.resolved_assets
    )
    assert any(
        ref.asset_family == "runtime_effect_runner"
        for ref in refreshed.active_plan.resolved_assets
    )
