from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.assets import (
    SHIPPED_WORK_ITEM_FAMILY_IDS,
    discover_artifact_contract_definitions,
    load_builtin_work_item_family_definition,
    load_builtin_workflow_primitives,
)
from millrace_ai.contracts import Plane, TaskDocument
from millrace_ai.runtime.artifact_contracts import parse_declared_artifact


def test_blueprint_draft_work_item_family_asset_loads() -> None:
    family = load_builtin_work_item_family_definition("blueprint_draft")

    assert "blueprint_draft" in SHIPPED_WORK_ITEM_FAMILY_IDS
    assert family.family_id == "blueprint_draft"
    assert family.plane is Plane.PLANNING
    assert family.file_extension == ".json"
    assert family.queue_dirs.queue == "blueprints/drafts/queue"
    assert family.queue_dirs.active == "blueprints/drafts/active"
    assert family.queue_dirs.done == "blueprints/drafts/approved"
    assert family.queue_dirs.blocked == "blueprints/drafts/blocked"
    assert family.queue_dirs.canceled == "blueprints/drafts/canceled"
    assert family.done_state == "approved"
    assert family.canceled_state == "canceled"
    assert family.dependency_field == "depends_on_draft_ids"
    assert family.document_adapter_id == "blueprint_draft_markdown_v1"


def test_blueprint_draft_document_adapter_is_packaged() -> None:
    bundle = load_builtin_workflow_primitives()
    adapters = {adapter.adapter_id: adapter for adapter in bundle.document_adapters}

    assert "blueprint_draft" in {family.family_id for family in bundle.work_item_families}
    adapter = adapters["blueprint_draft_markdown_v1"]
    assert adapter.family_ids == ("blueprint_draft",)
    assert adapter.supported_file_extensions == (".json",)
    assert adapter.supports_dependencies is True
    assert adapter.supports_lineage is True


def test_mechanic_blueprint_repair_artifact_contracts_are_structured(
    tmp_path: Path,
) -> None:
    contracts = {
        contract.artifact_id: contract
        for contract in discover_artifact_contract_definitions()
    }

    repair_decision = contracts["blueprint_repair_decision"]
    assert repair_decision.canonical_filename == "blueprint_repair_decision.json"
    assert repair_decision.schema_id == "blueprint_repair_decision_document_v1"
    assert repair_decision.producer_stage_kind_ids == ("mechanic_blueprint",)
    assert repair_decision.consumer_handler_ids == ("mechanic_blueprint_repair_apply",)

    repaired_task = contracts["repaired_generated_task"]
    assert repaired_task.canonical_filename == "repaired_generated_task.json"
    assert repaired_task.schema_id == "task_document_v1"
    assert repaired_task.producer_stage_kind_ids == ("mechanic_blueprint",)
    assert repaired_task.consumer_handler_ids == ("mechanic_blueprint_repair_apply",)
    assert repaired_task.destination_family_id == "task"

    task_path = tmp_path / repaired_task.canonical_filename
    task_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "task",
                "task_id": "task-001",
                "title": "Repair generated task",
                "summary": "Repair generated task after approval failure.",
                "root_idea_id": "idea-001",
                "root_spec_id": "spec-001",
                "spec_id": "spec-001",
                "target_paths": ["src/millrace_ai/runtime/blueprint_effects.py"],
                "acceptance": ["Runtime-owned mutation can validate the repaired task."],
                "required_checks": ["pytest tests/blueprint/test_effects.py -q"],
                "references": ["run-evaluator-001/blueprint_evaluation.json"],
                "risk": ["Repair context must match the failed approval."],
                "created_at": datetime(2026, 5, 19, 12, tzinfo=timezone.utc).isoformat(),
                "created_by": "mechanic_blueprint",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    parsed = parse_declared_artifact(repaired_task, task_path)

    assert isinstance(parsed, TaskDocument)


def test_blueprint_runtime_effect_recovery_closure_assets_are_declared() -> None:
    bundle = load_builtin_workflow_primitives()
    handlers = {handler.handler_id: handler for handler in bundle.runtime_effect_handlers}
    rules = {rule.rule_id: rule for rule in bundle.runtime_effect_rules}

    mechanic = handlers["mechanic_blueprint_repair_apply"]
    assert set(mechanic.declared_capabilities) >= {
        "repair.apply_repaired_generated_task",
        "repair.generated_task_missing",
        "repair.generated_task_invalid",
        "conflict.blueprint_repair_context_mismatch",
    }

    contractor = handlers["contractor_blueprint_candidate_persist"]
    assert set(contractor.declared_capabilities) >= {
        "replay.blueprint_candidate_equivalent",
        "duplicate.blueprint_candidate_same_id",
        "conflict.blueprint_candidate_duplicate_conflict",
        "conflict.blueprint_candidate_markdown_conflict",
    }

    evaluator = handlers["evaluator_blueprint_approved_to_task"]
    assert set(evaluator.declared_capabilities) >= {
        "replay.blueprint_approval_equivalent",
        "duplicate.generated_task_same_id",
        "conflict.blueprint_task_duplicate",
        "conflict.blueprint_approved_markdown_conflict",
    }

    repair_rule = rules["mechanic_blueprint_repair_apply"]
    assert repair_rule.source_node_id == "mechanic_blueprint"
    assert repair_rule.on_outcomes == ("MECHANIC_BLUEPRINT_COMPLETE",)
    assert set(repair_rule.required_run_artifacts) >= {
        "blueprint_repair_decision",
        "mechanic_report",
        "repaired_generated_task",
    }
    assert set(repair_rule.required_handler_capabilities) >= {
        "repair.apply_repaired_generated_task",
        "repair.generated_task_missing",
        "repair.generated_task_invalid",
    }
