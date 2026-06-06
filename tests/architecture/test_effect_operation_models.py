from __future__ import annotations

import pytest
from pydantic import ValidationError

from millrace_ai.architecture import (
    RuntimeEffectIdempotencyDefinition,
    RuntimeEffectMutationJournalDefinition,
    RuntimeEffectOperationDefinition,
    RuntimeEffectOperationStepDefinition,
    RuntimeEffectRepairClosureContractDefinition,
    RuntimeEffectStoreDefinition,
    RuntimeEffectValidatorDefinition,
    RuntimeFailurePolicyDefinition,
    RuntimeFailurePolicyRepairClosureMappingDefinition,
)
from millrace_ai.contracts import Plane


def _journal() -> RuntimeEffectMutationJournalDefinition:
    return RuntimeEffectMutationJournalDefinition(
        entry_id_template="{operation_id}:{run_id}:{step_id}",
        required_fields=("operation_id", "rule_id", "run_id", "step_id"),
        record_step_ids=("dispatch_legacy_handler",),
    )


def test_repair_closure_types_are_exported_from_architecture_surface() -> None:
    assert RuntimeEffectRepairClosureContractDefinition.__name__ == (
        "RuntimeEffectRepairClosureContractDefinition"
    )
    assert RuntimeFailurePolicyRepairClosureMappingDefinition.__name__ == (
        "RuntimeFailurePolicyRepairClosureMappingDefinition"
    )


def test_runtime_effect_operation_model_round_trips_legacy_shell() -> None:
    operation = RuntimeEffectOperationDefinition(
        operation_id="manager_blueprint_manifest_to_blueprint_drafts",
        display_name="Manager Blueprint manifest legacy shell",
        legacy_handler_ids=("manager_blueprint_manifest_to_blueprint_drafts",),
        required_artifacts=("blueprint_manifest", "blueprint_drafts"),
        steps=(
            RuntimeEffectOperationStepDefinition(
                step_id="validate_required_artifacts",
                primitive_id="artifact_presence",
                reads_artifact_ids=("blueprint_manifest", "blueprint_drafts"),
                validator_ids=("manager_blueprint.required_artifacts",),
            ),
            RuntimeEffectOperationStepDefinition(
                step_id="dispatch_legacy_handler",
                primitive_id="legacy_python_handler",
                mutation_phase="unknown",
                store_id="mutation_journal",
                writes_store=True,
            ),
        ),
        idempotency=RuntimeEffectIdempotencyDefinition(
            duplicate_policy="fail",
            replay_policy="resume_idempotently",
        ),
        failure_mappings=(
            {"failure_class": "legacy_handler_failure", "mutation_phase": "unknown"},
        ),
        repair_closure_contracts=(
            RuntimeEffectRepairClosureContractDefinition(
                failure_class="legacy_handler_failure",
                repair_operation_id="mechanic_blueprint_repair_apply",
                target_node_id="mechanic_blueprint",
                target_terminal_outcome="MECHANIC_BLUEPRINT_COMPLETE",
                required_repair_evidence_artifact_ids=(
                    "blueprint_repair_decision",
                    "mechanic_report",
                    "repaired_generated_task",
                ),
                affected_source_family_id="blueprint_draft",
                source_lifecycle_behavior_on_repair_success="complete_source_work_item",
                source_lifecycle_behavior_on_repair_failure="block_source_work_item",
            ),
        ),
        mutation_journal=_journal(),
        partial_commit_policy="block_source",
    )

    payload = operation.model_dump(mode="json")

    assert payload["operation_id"] == "manager_blueprint_manifest_to_blueprint_drafts"
    assert payload["steps"][1]["writes_store"] is True
    assert payload["mutation_journal"]["record_step_ids"] == ["dispatch_legacy_handler"]
    assert payload["repair_closure_contracts"][0]["failure_class"] == "legacy_handler_failure"


def test_runtime_effect_store_rejects_unsafe_paths() -> None:
    with pytest.raises(ValidationError, match="runtime_relative_root"):
        RuntimeEffectStoreDefinition(
            store_id="bad_store",
            store_type="workspace_state",
            runtime_relative_root="../state",
        )


def test_runtime_effect_store_rejects_windows_drive_paths() -> None:
    with pytest.raises(ValidationError, match="runtime_relative_root"):
        RuntimeEffectStoreDefinition(
            store_id="bad_store",
            store_type="workspace_state",
            runtime_relative_root="C:/temp/millrace",
        )


def test_runtime_effect_step_requires_store_for_writes() -> None:
    with pytest.raises(ValidationError, match="store_id"):
        RuntimeEffectOperationStepDefinition(
            step_id="write_without_store",
            primitive_id="mutation_journal_append",
            writes_store=True,
        )


def test_runtime_effect_operation_rejects_duplicate_steps() -> None:
    with pytest.raises(ValidationError, match="duplicate step_id"):
        RuntimeEffectOperationDefinition(
            operation_id="duplicate_step_operation",
            display_name="Duplicate step operation",
            steps=(
                RuntimeEffectOperationStepDefinition(
                    step_id="same",
                    primitive_id="artifact_presence",
                ),
                RuntimeEffectOperationStepDefinition(
                    step_id="same",
                    primitive_id="legacy_python_handler",
                ),
            ),
            idempotency=RuntimeEffectIdempotencyDefinition(
                duplicate_policy="fail",
                replay_policy="resume_idempotently",
            ),
            failure_mappings=(
                {"failure_class": "legacy_handler_failure", "mutation_phase": "unknown"},
            ),
            mutation_journal=RuntimeEffectMutationJournalDefinition(
                entry_id_template="{operation_id}:{run_id}:{step_id}",
                required_fields=("operation_id",),
            ),
        )


def test_runtime_effect_operation_rejects_repair_closure_for_unknown_failure_class() -> None:
    with pytest.raises(ValidationError, match="repair_closure_contracts failure_class"):
        RuntimeEffectOperationDefinition(
            operation_id="repair_unknown_failure_operation",
            display_name="Repair unknown failure operation",
            steps=(
                RuntimeEffectOperationStepDefinition(
                    step_id="validate",
                    primitive_id="artifact_presence",
                ),
            ),
            idempotency=RuntimeEffectIdempotencyDefinition(
                duplicate_policy="fail",
                replay_policy="resume_idempotently",
            ),
            failure_mappings=(
                {"failure_class": "declared_failure", "mutation_phase": "pre_mutation"},
            ),
            repair_closure_contracts=(
                RuntimeEffectRepairClosureContractDefinition(
                    failure_class="undeclared_failure",
                    repair_operation_id="mechanic_blueprint_repair_apply",
                    target_node_id="mechanic_blueprint",
                    target_terminal_outcome="MECHANIC_BLUEPRINT_COMPLETE",
                    required_repair_evidence_artifact_ids=(
                        "blueprint_repair_decision",
                        "mechanic_report",
                        "repaired_generated_task",
                    ),
                    affected_source_family_id="blueprint_draft",
                    source_lifecycle_behavior_on_repair_success="complete_source_work_item",
                    source_lifecycle_behavior_on_repair_failure="block_source_work_item",
                ),
            ),
            mutation_journal=RuntimeEffectMutationJournalDefinition(
                entry_id_template="{operation_id}:{run_id}:{step_id}",
                required_fields=("operation_id",),
            ),
        )


def test_runtime_failure_policy_accepts_repair_closure_mapping() -> None:
    policy = RuntimeFailurePolicyDefinition(
        policy_id="repair_route_policy",
        applies_to_origins=("runtime_effect",),
        applies_to_planes=(Plane.PLANNING,),
        applies_to_families=("blueprint_draft",),
        applies_to_failure_classes=("generated_task_missing",),
        applies_to_operation_ids=("evaluator_blueprint_approved_to_task",),
        action="route_to_node",
        target_node_id="mechanic_blueprint",
        failure_class_template="runtime_effect_failure",
        repair_closure_mappings=(
            {
                "source_operation_id": "evaluator_blueprint_approved_to_task",
                "failure_class": "generated_task_missing",
                "repair_operation_id": "mechanic_blueprint_repair_apply",
                "target_node_id": "mechanic_blueprint",
                "target_terminal_outcome": "MECHANIC_BLUEPRINT_COMPLETE",
                "required_repair_evidence_artifact_ids": (
                    "blueprint_repair_decision",
                    "mechanic_report",
                    "repaired_generated_task",
                ),
                "affected_source_family_id": "blueprint_draft",
                "source_lifecycle_behavior_on_repair_success": "complete_source_work_item",
                "source_lifecycle_behavior_on_repair_failure": "block_source_work_item",
                "supports_partial_mutation": False,
                "requires_resume_guard": True,
            },
        ),
    )

    assert policy.repair_closure_mappings[0].source_operation_id == "evaluator_blueprint_approved_to_task"


def test_runtime_failure_policy_rejects_repair_mapping_with_mismatched_target_node() -> None:
    with pytest.raises(ValidationError, match="target_node_id"):
        RuntimeFailurePolicyDefinition(
            policy_id="repair_route_policy_bad_target",
            applies_to_origins=("runtime_effect",),
            applies_to_planes=(Plane.PLANNING,),
            applies_to_families=("blueprint_draft",),
            applies_to_failure_classes=("generated_task_missing",),
            applies_to_operation_ids=("evaluator_blueprint_approved_to_task",),
            action="route_to_node",
            target_node_id="mechanic_blueprint",
            failure_class_template="runtime_effect_failure",
            repair_closure_mappings=(
                {
                    "source_operation_id": "evaluator_blueprint_approved_to_task",
                    "failure_class": "generated_task_missing",
                    "repair_operation_id": "mechanic_blueprint_repair_apply",
                    "target_node_id": "other_repair_node",
                    "target_terminal_outcome": "MECHANIC_BLUEPRINT_COMPLETE",
                    "required_repair_evidence_artifact_ids": (
                        "blueprint_repair_decision",
                        "mechanic_report",
                        "repaired_generated_task",
                    ),
                    "affected_source_family_id": "blueprint_draft",
                    "source_lifecycle_behavior_on_repair_success": "complete_source_work_item",
                    "source_lifecycle_behavior_on_repair_failure": "block_source_work_item",
                },
            ),
        )


def test_runtime_effect_validator_rejects_duplicate_inputs() -> None:
    with pytest.raises(ValidationError, match="duplicate input_artifact_ids"):
        RuntimeEffectValidatorDefinition(
            validator_id="manager.required_artifacts",
            primitive_id="artifact_presence",
            input_artifact_ids=("blueprint_manifest", "blueprint_manifest"),
            failure_class="blueprint_manifest_missing",
        )


# ---------------------------------------------------------------------------
# RuntimeEffectOperationStepDefinition - binding and context model tests
# ---------------------------------------------------------------------------


def test_step_accepts_optional_binding_and_context_fields() -> None:
    step = RuntimeEffectOperationStepDefinition(
        step_id="interpret_bindings",
        primitive_id="blueprint_critique_packet_validation",
        input_bindings={
            "manifest": "$artifact.blueprint_manifest",
            "store_target": "$store.mutation_journal",
            "parent_context": "$context.prev_result",
        },
        params={"threshold": 0.95, "enabled": True},
        output_context_key="interpreted_output",
        context_read_key="prev_result",
    )
    assert step.input_bindings == {
        "manifest": "$artifact.blueprint_manifest",
        "store_target": "$store.mutation_journal",
        "parent_context": "$context.prev_result",
    }
    assert step.params == {"threshold": 0.95, "enabled": True}
    assert step.output_context_key == "interpreted_output"
    assert step.context_read_key == "prev_result"


def test_step_defaults_empty_bindings_and_params() -> None:
    step = RuntimeEffectOperationStepDefinition(
        step_id="minimal",
        primitive_id="artifact_presence",
    )
    assert step.input_bindings == {}
    assert step.params == {}
    assert step.output_context_key is None
    assert step.context_read_key is None


def test_step_round_trips_with_bindings() -> None:
    step = RuntimeEffectOperationStepDefinition(
        step_id="bound_step",
        primitive_id="artifact_presence",
        input_bindings={"a": "$artifact.blueprint_manifest"},
        params={"flag": True},
        output_context_key="result_key",
    )
    payload = step.model_dump(mode="json")
    assert payload["input_bindings"] == {"a": "$artifact.blueprint_manifest"}
    assert payload["params"] == {"flag": True}
    assert payload["output_context_key"] == "result_key"
    reparsed = RuntimeEffectOperationStepDefinition.model_validate(payload)
    assert reparsed == step


def test_binding_rejects_invalid_dollar_prefix() -> None:
    with pytest.raises(ValidationError, match="binding value"):
        RuntimeEffectOperationStepDefinition(
            step_id="bad_binding",
            primitive_id="artifact_presence",
            input_bindings={"x": "$unknown.thing"},
        )


def test_binding_rejects_bare_dollar() -> None:
    with pytest.raises(ValidationError, match="binding value"):
        RuntimeEffectOperationStepDefinition(
            step_id="bad_binding",
            primitive_id="artifact_presence",
            input_bindings={"x": "$"},
        )


def test_binding_rejects_dollar_artifact_with_spaces() -> None:
    with pytest.raises(ValidationError, match="binding value"):
        RuntimeEffectOperationStepDefinition(
            step_id="bad_binding",
            primitive_id="artifact_presence",
            input_bindings={"x": "$artifact. bad id"},
        )


def test_binding_rejects_dollar_store_with_special_chars() -> None:
    with pytest.raises(ValidationError, match="binding value"):
        RuntimeEffectOperationStepDefinition(
            step_id="bad_binding",
            primitive_id="artifact_presence",
            input_bindings={"x": "$store.bad/id"},
        )


def test_binding_rejects_path_traversal_in_literal() -> None:
    with pytest.raises(ValidationError, match="path traversal"):
        RuntimeEffectOperationStepDefinition(
            step_id="bad_binding",
            primitive_id="artifact_presence",
            input_bindings={"x": "../etc/passwd"},
        )


def test_binding_rejects_absolute_path_in_literal() -> None:
    with pytest.raises(ValidationError, match="path traversal"):
        RuntimeEffectOperationStepDefinition(
            step_id="bad_binding",
            primitive_id="artifact_presence",
            input_bindings={"x": "/etc/passwd"},
        )


def test_binding_rejects_windows_absolute_path_in_literal() -> None:
    with pytest.raises(ValidationError, match="path traversal"):
        RuntimeEffectOperationStepDefinition(
            step_id="bad_binding",
            primitive_id="artifact_presence",
            input_bindings={"x": "\\server\\share"},
        )


def test_binding_accepts_json_literal_scalars() -> None:
    step = RuntimeEffectOperationStepDefinition(
        step_id="literal_bindings",
        primitive_id="artifact_presence",
        input_bindings={
            "count": "42",
            "label": "hello world",
            "flag": "true",
        },
    )
    assert step.input_bindings["count"] == "42"
    assert step.input_bindings["label"] == "hello world"


def test_binding_accepts_valid_artifact_reference() -> None:
    step = RuntimeEffectOperationStepDefinition(
        step_id="valid_artifact",
        primitive_id="artifact_presence",
        input_bindings={"ref": "$artifact.blueprint_manifest"},
    )
    assert step.input_bindings["ref"] == "$artifact.blueprint_manifest"


def test_binding_accepts_valid_store_reference() -> None:
    step = RuntimeEffectOperationStepDefinition(
        step_id="valid_store",
        primitive_id="artifact_presence",
        input_bindings={"ref": "$store.mutation_journal"},
    )
    assert step.input_bindings["ref"] == "$store.mutation_journal"


def test_binding_accepts_valid_context_reference() -> None:
    step = RuntimeEffectOperationStepDefinition(
        step_id="valid_context",
        primitive_id="artifact_presence",
        input_bindings={"ref": "$context.my_key"},
    )
    assert step.input_bindings["ref"] == "$context.my_key"


def test_context_key_fields_are_normalized() -> None:
    step = RuntimeEffectOperationStepDefinition(
        step_id="ctx_step",
        primitive_id="artifact_presence",
        output_context_key="  My_Output  ",
        context_read_key="  my_read_key  ",
    )
    assert step.output_context_key == "my_output"
    assert step.context_read_key == "my_read_key"
