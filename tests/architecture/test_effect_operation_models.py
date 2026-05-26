from __future__ import annotations

import pytest
from pydantic import ValidationError

from millrace_ai.architecture import (
    RuntimeEffectIdempotencyDefinition,
    RuntimeEffectMutationJournalDefinition,
    RuntimeEffectOperationDefinition,
    RuntimeEffectOperationStepDefinition,
    RuntimeEffectStoreDefinition,
    RuntimeEffectValidatorDefinition,
)


def _journal() -> RuntimeEffectMutationJournalDefinition:
    return RuntimeEffectMutationJournalDefinition(
        entry_id_template="{operation_id}:{run_id}:{step_id}",
        required_fields=("operation_id", "rule_id", "run_id", "step_id"),
        record_step_ids=("dispatch_legacy_handler",),
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
        mutation_journal=_journal(),
        partial_commit_policy="block_source",
    )

    payload = operation.model_dump(mode="json")

    assert payload["operation_id"] == "manager_blueprint_manifest_to_blueprint_drafts"
    assert payload["steps"][1]["writes_store"] is True
    assert payload["mutation_journal"]["record_step_ids"] == ["dispatch_legacy_handler"]


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


def test_runtime_effect_validator_rejects_duplicate_inputs() -> None:
    with pytest.raises(ValidationError, match="duplicate input_artifact_ids"):
        RuntimeEffectValidatorDefinition(
            validator_id="manager.required_artifacts",
            primitive_id="artifact_presence",
            input_artifact_ids=("blueprint_manifest", "blueprint_manifest"),
            failure_class="blueprint_manifest_missing",
        )
