"""Explicit SQLite row records and row codecs for runtime state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from millrace.contracts.compiled_plan import AuthorityValue, SelectedCompiledPlan
from millrace.contracts.ids import (
    ActionId,
    ArtifactSchemaId,
    CompletionBehaviorId,
    CounterId,
    EffectDeclarationId,
    FanoutId,
    OperatorWaitId,
    QueueFamilyId,
    RecoveryPolicyId,
    RemediationPolicyId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.state import (
    Activation,
    ActivationRouteRecord,
    AdmittedPlan,
    ArtifactRecord,
    ClosedWorkItemRecord,
    ClosureBlockedRecord,
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    ClosureTerminalRecord,
    CooldownWaitRecord,
    CounterRecord,
    EffectProposalRecord,
    EffectReconciliationRecord,
    FanoutRecord,
    GovernanceEventRecord,
    InputReceipt,
    LineageQuarantineRecord,
    OperatorInterventionRecord,
    OperatorWaitRecord,
    PauseRecord,
    PlanRef,
    QuarantineRecord,
    RecoveryAttemptRecord,
    RemediationWorkRecord,
    RunnerObservationRecord,
    RunRecord,
    TraceRecord,
    TransitionRecord,
    TransitionRefusal,
    WorkDependencyRecord,
    WorkItem,
)
from millrace.substrate.errors import StorageIntegrityError

_PLAN_FORMAT_VERSION = SelectedCompiledPlan.schema_version


@dataclass(frozen=True, slots=True)
class AdmittedPlanPinRow:
    authority_fingerprint: str
    plan_id: str
    plan_format_version: int
    selected_plan_digest: str
    admitted_at_order: int


@dataclass(frozen=True, slots=True)
class DefaultPlanRow:
    plan_id: str
    authority_fingerprint: str
    plan_format_version: int
    selected_plan_digest: str
    set_at_order: int


@dataclass(frozen=True, slots=True)
class InputReceiptRow:
    input_id: str
    input_payload_digest: str
    transition_id: str
    accepted: int
    refusal_reason: str | None
    received_at_order: int


@dataclass(frozen=True, slots=True)
class WorkItemRow:
    work_item_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    generation: int
    payload_digest: str
    queue_family_id: str
    lineage_id: str | None
    created_by_input_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class ActivationRow:
    activation_id: str
    work_item_id: str
    lineage_id: str | None
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    queue_family_id: str
    graph_node_id: str
    stage_kind_id: str
    runner_binding_id: str
    generation: int
    created_by_input_id: str
    claimed_by_run_id: str | None
    created_at_order: int


@dataclass(frozen=True, slots=True)
class RunRow:
    run_id: str
    activation_id: str
    work_item_id: str
    claim_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    generation: int
    fencing_token: str
    stage_kind_id: str
    runner_binding_id: str
    created_by_input_id: str
    started_at_order: int


@dataclass(frozen=True, slots=True)
class RunnerObservationRow:
    observation_id: str
    run_id: str
    payload_digest: str
    created_by_input_id: str
    observed_at: int | None
    observed_at_order: int


@dataclass(frozen=True, slots=True)
class ArtifactRow:
    artifact_id: str
    work_item_id: str
    artifact_schema_id: str
    payload_digest: str
    created_by_input_id: str
    source_run_id: str
    source_action_id: str
    source_stage_kind_id: str
    source_graph_node_id: str
    artifact_payload_digest: str
    transition_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class EffectProposalRow:
    effect_id: str
    dedupe_key: str
    effect_declaration_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    selected_plan_fingerprint: str
    terminal_action_id: str
    artifact_id: str
    artifact_schema_id: str
    artifact_payload_digest: str
    source_run_id: str
    source_action_id: str
    source_input_id: str
    source_work_item_id: str
    source_activation_id: str
    source_graph_node_id: str
    source_stage_kind_id: str
    source_runner_binding_id: str
    source_queue_family_id: str
    lineage_id: str | None
    provider_ref: str
    capability_policy_ref: str
    target_ref_kind: str
    target_ref_schema: str
    target_skill_id: str | None
    target_path_ref: str | None
    status: str
    created_input_id: str
    created_transition_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class EffectReconciliationRow:
    reconciliation_id: str
    effect_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    selected_plan_fingerprint: str
    provider_ref: str
    status: str
    fake_local_result_digest: str
    created_input_id: str
    created_transition_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class ActivationRouteRow:
    record_id: str
    action_id: str
    source_run_id: str
    source_work_item_id: str
    target_work_item_id: str
    target_activation_id: str
    created_by_input_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class FanoutRow:
    record_id: str
    fanout_id: str
    source_artifact_id: str
    source_artifact_digest: str
    source_work_item_id: str
    source_run_id: str
    source_action_id: str
    target_work_item_id: str
    target_activation_id: str
    target_queue_family_id: str
    target_stage_kind_id: str
    target_graph_node_id: str
    item_key: str
    lineage_id: str | None
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    created_by_input_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class WorkDependencyRow:
    dependency_id: str
    dependent_work_item_id: str
    dependency_work_item_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    lineage_id: str | None
    fanout_record_id: str
    created_by_input_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class ClosureTargetRow:
    closure_target_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    completion_behavior_id: str
    lineage_id: str
    root_source_kind: str
    root_source_id: str
    closure_root_work_item_id: str | None
    request_kind: str
    target_graph_node_id: str
    evidence_window_digest: str
    status: str
    opened_by_input_id: str
    closed_by_record_id: str | None
    created_at_order: int


@dataclass(frozen=True, slots=True)
class ClosureEvaluationRow:
    record_id: str
    closure_target_id: str
    completion_behavior_id: str
    request_kind: str
    target_work_item_id: str
    target_activation_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    lineage_id: str
    created_by_input_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class ClosureTerminalRow:
    record_id: str
    closure_target_id: str
    completion_behavior_id: str
    terminal_kind: str
    source_run_id: str
    source_action_id: str
    source_artifact_id: str | None
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    lineage_id: str
    created_by_input_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class RemediationWorkRow:
    record_id: str
    remediation_policy_id: str
    closure_target_id: str
    source_run_id: str
    source_action_id: str
    source_artifact_id: str | None
    target_work_item_id: str
    target_activation_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    lineage_id: str
    dedupe_key: str
    created_by_input_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class ClosureBlockedRow:
    record_id: str
    closure_target_id: str
    completion_behavior_id: str
    source_run_id: str
    source_action_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    lineage_id: str
    operator_required: int
    created_by_input_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class ClosedWorkItemRow:
    record_id: str
    work_item_id: str
    source_run_id: str | None
    action_id: str | None
    operator_intervention_record_id: str | None
    close_kind: str
    created_by_input_id: str
    closed_at_order: int


@dataclass(frozen=True, slots=True)
class PauseStateRow:
    record_id: str
    source_run_id: str
    work_item_id: str
    action_id: str
    created_by_input_id: str
    paused_at_order: int


@dataclass(frozen=True, slots=True)
class QuarantineRow:
    record_id: str
    work_item_id: str
    source_run_id: str
    action_id: str
    created_by_input_id: str
    created_at_order: int


@dataclass(frozen=True, slots=True)
class LineageQuarantineRow:
    quarantine_id: str
    policy_id: str
    lineage_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    recovery_attempt_record_id: str
    original_source_run_id: str
    original_source_work_item_id: str
    original_source_activation_id: str
    emitting_recovery_activation_id: str
    emitting_recovery_run_id: str
    action_id: str
    attempt_count: int
    created_input_id: str
    actor_kind: str
    status: str
    superseded_input_id: str | None
    created_at_order: int


@dataclass(frozen=True, slots=True)
class RecoveryAttemptRow:
    record_id: str
    policy_id: str
    lineage_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    attempt_count: int
    phase: str
    source_run_id: str
    source_work_item_id: str
    source_activation_id: str
    source_graph_node_id: str
    source_stage_kind_id: str
    source_runner_binding_id: str
    source_queue_family_id: str
    recovery_action_id: str
    latest_recovery_activation_id: str | None
    latest_recovery_run_id: str | None
    latest_return_action_id: str | None
    created_by_input_id: str
    updated_by_input_id: str
    updated_at_order: int


@dataclass(frozen=True, slots=True)
class OperatorInterventionRow:
    record_id: str
    created_by_input_id: str
    input_payload_digest: str
    option_id: str
    kind: str
    result: str
    policy_id: str
    lineage_id: str
    quarantine_id: str
    recovery_attempt_record_id: str
    recovery_attempt_count: int
    attempt_effect: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    actor_kind: str
    actor_id: str
    reason: str
    target_work_item_id: str | None
    target_activation_id: str | None
    closed_work_item_ids_json: str
    closed_activation_ids_json: str
    closed_run_ids_json: str
    payload_digest: str
    payload_reference: str | None
    created_at_order: int


@dataclass(frozen=True, slots=True)
class OperatorWaitRow:
    wait_id: str
    operator_wait_id: str
    source_action_id: str
    lineage_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    source_work_item_id: str
    source_activation_id: str
    source_run_id: str
    source_stage_kind_id: str
    source_graph_node_id: str
    source_queue_family_id: str
    source_runner_binding_id: str
    source_artifact_id: str | None
    status: str
    created_input_id: str
    created_input_payload_digest: str
    resolved_input_id: str | None
    resolved_input_payload_digest: str | None
    actor_id: str | None
    actor_kind: str | None
    resolution_kind: str | None
    target_work_item_id: str | None
    target_activation_id: str | None
    closed_work_item_ids_json: str | None
    payload_digest: str | None
    payload_reference: str | None
    created_at_order: int


@dataclass(frozen=True, slots=True)
class CooldownWaitRow:
    wait_id: str
    policy_id: str
    lineage_id: str
    recovery_attempt_record_id: str
    attempt_count: int
    source_run_id: str
    source_work_item_id: str
    source_activation_id: str
    recovery_action_id: str
    target_stage_kind_id: str
    target_graph_node_id: str
    target_runner_binding_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    created_input_id: str
    created_at: int
    due_at: int
    consumed_input_id: str | None
    consumed_at: int | None
    resulting_recovery_activation_id: str | None
    updated_at_order: int


@dataclass(frozen=True, slots=True)
class CounterRow:
    record_id: str
    counter_id: str
    plan_id: str
    plan_authority_fingerprint: str
    plan_format_version: int
    lineage_id: str
    value: int
    updated_by_input_id: str
    updated_at_order: int


@dataclass(frozen=True, slots=True)
class TransitionRow:
    transition_order: int
    record_id: str
    input_id: str
    input_kind: str
    input_family: str
    accepted: int
    created_at: str


@dataclass(frozen=True, slots=True)
class GovernanceEventRow:
    record_id: str
    transition_order: int
    input_id: str
    input_kind: str
    input_family: str
    disposition: str
    plan_fingerprint: str | None
    work_item_id: str | None
    run_id: str | None
    action_id: str | None
    authority_source: str | None
    refusal_reason: str | None
    created_at_order: int


@dataclass(frozen=True, slots=True)
class TraceRow:
    record_id: str
    transition_order: int
    input_id: str
    input_kind: str
    input_family: str
    disposition: str
    plan_fingerprint: str | None
    work_item_id: str | None
    run_id: str | None
    action_id: str | None
    authority_source: str | None
    refusal_reason: str | None
    created_at_order: int


@dataclass(frozen=True, slots=True)
class RefusalRow:
    record_id: str
    transition_order: int
    input_id: str
    input_kind: str
    input_family: str
    reason: str
    detail: str | None
    created_at_order: int


def _expect_text(row: tuple[object, ...], index: int, column: str) -> str:
    value = row[index]
    if not isinstance(value, str) or value == "":
        raise StorageIntegrityError(f"{column} must be non-empty text")
    return value


def _expect_optional_text(
    row: tuple[object, ...],
    index: int,
    column: str,
    *,
    allow_empty: bool = True,
) -> str | None:
    value = row[index]
    if value is None:
        return None
    if not isinstance(value, str):
        raise StorageIntegrityError(f"{column} must be text or null")
    if value == "" and not allow_empty:
        raise StorageIntegrityError(f"{column} must be non-empty text or null")
    return value


def _json_string_tuple(values: tuple[str, ...]) -> str:
    return json.dumps(tuple(values), separators=(",", ":"), sort_keys=True)


def _expect_json_string_tuple(
    row: tuple[object, ...],
    index: int,
    column: str,
) -> tuple[str, ...]:
    value = _expect_text(row, index, column)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise StorageIntegrityError(f"{column} must be a JSON string array") from exc
    if not isinstance(parsed, list):
        raise StorageIntegrityError(f"{column} must be a JSON string array")
    strings: list[str] = []
    for item in parsed:
        if not isinstance(item, str) or not item:
            raise StorageIntegrityError(f"{column} must be a JSON string array")
        strings.append(item)
    if _json_string_tuple(tuple(strings)) != value:
        raise StorageIntegrityError(f"{column} must be canonical JSON")
    return tuple(strings)


def _expect_nonnegative_int(row: tuple[object, ...], index: int, column: str) -> int:
    value = row[index]
    if type(value) is not int or value < 0:
        raise StorageIntegrityError(f"{column} must be a non-negative integer")
    return value


def _expect_optional_nonnegative_int(
    row: tuple[object, ...],
    index: int,
    column: str,
) -> int | None:
    value = row[index]
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise StorageIntegrityError(f"{column} must be a non-negative integer or null")
    return value


def _expect_positive_int(row: tuple[object, ...], index: int, column: str) -> int:
    value = _expect_nonnegative_int(row, index, column)
    if value <= 0:
        raise StorageIntegrityError(f"{column} must be a positive integer")
    return value


def _expect_bool_int(row: tuple[object, ...], index: int, column: str) -> int:
    value = row[index]
    if type(value) is not int or value not in (0, 1):
        raise StorageIntegrityError(f"{column} must be 0 or 1")
    return value


def _expect_plan_format_version(
    row: tuple[object, ...],
    index: int,
    column: str,
) -> int:
    value = _expect_nonnegative_int(row, index, column)
    if value != _PLAN_FORMAT_VERSION:
        raise StorageIntegrityError(
            f"{column} unsupported plan_format_version: {value}"
        )
    return value


def encode_admitted_plan_pin_row(
    admitted_plan: AdmittedPlan,
    *,
    selected_plan_digest: str,
    admitted_at_order: int,
) -> AdmittedPlanPinRow:
    plan_ref = admitted_plan.plan_ref
    return AdmittedPlanPinRow(
        authority_fingerprint=plan_ref.authority_fingerprint,
        plan_id=plan_ref.plan_id,
        plan_format_version=plan_ref.plan_format_version,
        selected_plan_digest=selected_plan_digest,
        admitted_at_order=admitted_at_order,
    )


def decode_admitted_plan_pin_row(row: tuple[object, ...]) -> AdmittedPlanPinRow:
    return AdmittedPlanPinRow(
        authority_fingerprint=_expect_text(
            row,
            0,
            "admitted_plan_pins.authority_fingerprint",
        ),
        plan_id=_expect_text(row, 1, "admitted_plan_pins.plan_id"),
        plan_format_version=_expect_plan_format_version(
            row,
            2,
            "admitted_plan_pins.plan_format_version",
        ),
        selected_plan_digest=_expect_text(
            row,
            3,
            "admitted_plan_pins.selected_plan_digest",
        ),
        admitted_at_order=_expect_nonnegative_int(
            row,
            4,
            "admitted_plan_pins.admitted_at_order",
        ),
    )


def encode_default_plan_row(
    plan_ref: PlanRef | None,
    selected_plan_digests: Mapping[str, str],
) -> DefaultPlanRow | None:
    if plan_ref is None:
        return None
    return DefaultPlanRow(
        plan_id=plan_ref.plan_id,
        authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        selected_plan_digest=selected_plan_digests[plan_ref.authority_fingerprint],
        set_at_order=0,
    )


def decode_default_plan_row(row: tuple[object, ...]) -> DefaultPlanRow:
    return DefaultPlanRow(
        plan_id=_expect_text(row, 0, "default_plan.plan_id"),
        authority_fingerprint=_expect_text(
            row,
            1,
            "default_plan.authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            2,
            "default_plan.plan_format_version",
        ),
        selected_plan_digest=_expect_text(
            row,
            3,
            "default_plan.selected_plan_digest",
        ),
        set_at_order=_expect_nonnegative_int(row, 4, "default_plan.set_at_order"),
    )


def encode_input_receipt_row(
    receipt: InputReceipt,
    *,
    received_at_order: int,
) -> InputReceiptRow:
    receipt_ref = receipt.receipt_ref
    return InputReceiptRow(
        input_id=receipt_ref.input_id,
        input_payload_digest=receipt_ref.input_payload_digest,
        transition_id=receipt.transition_id,
        accepted=int(receipt.accepted),
        refusal_reason=receipt.refusal_reason,
        received_at_order=received_at_order,
    )


def decode_input_receipt_row(row: tuple[object, ...]) -> InputReceiptRow:
    return InputReceiptRow(
        input_id=_expect_text(row, 0, "input_receipts.input_id"),
        input_payload_digest=_expect_text(
            row,
            1,
            "input_receipts.input_payload_digest",
        ),
        transition_id=_expect_text(row, 2, "input_receipts.transition_id"),
        accepted=_expect_bool_int(row, 3, "input_receipts.accepted"),
        refusal_reason=_expect_optional_text(
            row,
            4,
            "input_receipts.refusal_reason",
        ),
        received_at_order=_expect_nonnegative_int(
            row,
            5,
            "input_receipts.received_at_order",
        ),
    )


def encode_work_item_row(
    work_item: WorkItem,
    *,
    payload_digest: str,
    created_at_order: int,
) -> WorkItemRow:
    plan_ref = work_item.ref.plan_ref
    return WorkItemRow(
        work_item_id=work_item.ref.work_item_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        generation=work_item.ref.generation,
        payload_digest=payload_digest,
        queue_family_id=str(work_item.queue_family_id),
        lineage_id=work_item.lineage_id,
        created_by_input_id=work_item.created_by_input_id,
        created_at_order=created_at_order,
    )


def decode_work_item_row(row: tuple[object, ...]) -> WorkItemRow:
    return WorkItemRow(
        work_item_id=_expect_text(row, 0, "work_items.work_item_id"),
        plan_id=_expect_text(row, 1, "work_items.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            2,
            "work_items.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            3,
            "work_items.plan_format_version",
        ),
        generation=_expect_nonnegative_int(row, 4, "work_items.generation"),
        payload_digest=_expect_text(row, 5, "work_items.payload_digest"),
        queue_family_id=_expect_text(row, 6, "work_items.queue_family_id"),
        lineage_id=_expect_optional_text(
            row,
            7,
            "work_items.lineage_id",
            allow_empty=False,
        ),
        created_by_input_id=_expect_text(
            row,
            8,
            "work_items.created_by_input_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            9,
            "work_items.created_at_order",
        ),
    )


def encode_activation_row(
    activation: Activation,
    *,
    created_at_order: int,
) -> ActivationRow:
    return ActivationRow(
        activation_id=activation.activation_id,
        work_item_id=activation.work_item_id,
        lineage_id=activation.lineage_id,
        plan_id=activation.plan_ref.plan_id,
        plan_authority_fingerprint=activation.plan_ref.authority_fingerprint,
        plan_format_version=activation.plan_ref.plan_format_version,
        queue_family_id=str(activation.queue_family_id),
        graph_node_id=activation.graph_node_id,
        stage_kind_id=str(activation.stage_kind_id),
        runner_binding_id=str(activation.runner_binding_id),
        generation=activation.generation,
        created_by_input_id=activation.created_by_input_id,
        claimed_by_run_id=activation.claimed_by_run_id,
        created_at_order=created_at_order,
    )


def decode_activation_row(row: tuple[object, ...]) -> ActivationRow:
    return ActivationRow(
        activation_id=_expect_text(row, 0, "activations.activation_id"),
        work_item_id=_expect_text(row, 1, "activations.work_item_id"),
        lineage_id=_expect_optional_text(
            row,
            2,
            "activations.lineage_id",
            allow_empty=False,
        ),
        plan_id=_expect_text(row, 3, "activations.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            4,
            "activations.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            5,
            "activations.plan_format_version",
        ),
        queue_family_id=_expect_text(row, 6, "activations.queue_family_id"),
        graph_node_id=_expect_text(row, 7, "activations.graph_node_id"),
        stage_kind_id=_expect_text(row, 8, "activations.stage_kind_id"),
        runner_binding_id=_expect_text(row, 9, "activations.runner_binding_id"),
        generation=_expect_nonnegative_int(row, 10, "activations.generation"),
        created_by_input_id=_expect_text(
            row,
            11,
            "activations.created_by_input_id",
        ),
        claimed_by_run_id=_expect_optional_text(
            row,
            12,
            "activations.claimed_by_run_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            13,
            "activations.created_at_order",
        ),
    )


def encode_run_row(
    run: RunRecord,
    *,
    started_at_order: int,
) -> RunRow:
    run_ref = run.run_ref
    return RunRow(
        run_id=run_ref.run_id,
        activation_id=run.activation_id,
        work_item_id=run.work_item_id,
        claim_id=run_ref.claim_id,
        plan_id=run_ref.plan_ref.plan_id,
        plan_authority_fingerprint=run_ref.plan_ref.authority_fingerprint,
        plan_format_version=run_ref.plan_ref.plan_format_version,
        generation=run_ref.generation,
        fencing_token=run_ref.fencing_token,
        stage_kind_id=str(run.stage_kind_id),
        runner_binding_id=str(run.runner_binding_id),
        created_by_input_id=run.created_by_input_id,
        started_at_order=started_at_order,
    )


def decode_run_row(row: tuple[object, ...]) -> RunRow:
    return RunRow(
        run_id=_expect_text(row, 0, "runs.run_id"),
        activation_id=_expect_text(row, 1, "runs.activation_id"),
        work_item_id=_expect_text(row, 2, "runs.work_item_id"),
        claim_id=_expect_text(row, 3, "runs.claim_id"),
        plan_id=_expect_text(row, 4, "runs.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            5,
            "runs.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            6,
            "runs.plan_format_version",
        ),
        generation=_expect_nonnegative_int(row, 7, "runs.generation"),
        fencing_token=_expect_text(row, 8, "runs.fencing_token"),
        stage_kind_id=_expect_text(row, 9, "runs.stage_kind_id"),
        runner_binding_id=_expect_text(row, 10, "runs.runner_binding_id"),
        created_by_input_id=_expect_text(row, 11, "runs.created_by_input_id"),
        started_at_order=_expect_nonnegative_int(row, 12, "runs.started_at_order"),
    )


def encode_runner_observation_row(
    observation: RunnerObservationRecord,
    *,
    payload_digest: str,
    observed_at_order: int,
) -> RunnerObservationRow:
    return RunnerObservationRow(
        observation_id=observation.observation_id,
        run_id=observation.run_id,
        payload_digest=payload_digest,
        created_by_input_id=observation.created_by_input_id,
        observed_at=observation.observed_at,
        observed_at_order=observed_at_order,
    )


def decode_runner_observation_row(row: tuple[object, ...]) -> RunnerObservationRow:
    return RunnerObservationRow(
        observation_id=_expect_text(
            row,
            0,
            "runner_observations.observation_id",
        ),
        run_id=_expect_text(row, 1, "runner_observations.run_id"),
        payload_digest=_expect_text(row, 2, "runner_observations.payload_digest"),
        created_by_input_id=_expect_text(
            row,
            3,
            "runner_observations.created_by_input_id",
        ),
        observed_at=_expect_optional_nonnegative_int(
            row,
            4,
            "runner_observations.observed_at",
        ),
        observed_at_order=_expect_nonnegative_int(
            row,
            5,
            "runner_observations.observed_at_order",
        ),
    )


def encode_artifact_row(
    artifact: ArtifactRecord,
    *,
    payload_digest: str,
    created_at_order: int,
) -> ArtifactRow:
    return ArtifactRow(
        artifact_id=artifact.artifact_id,
        work_item_id=artifact.work_item_id,
        artifact_schema_id=str(artifact.schema_id),
        payload_digest=payload_digest,
        created_by_input_id=artifact.created_by_input_id,
        source_run_id=artifact.source_run_id,
        source_action_id=str(artifact.source_action_id),
        source_stage_kind_id=str(artifact.source_stage_kind_id),
        source_graph_node_id=artifact.source_graph_node_id,
        artifact_payload_digest=artifact.payload_digest,
        transition_id=artifact.transition_id,
        created_at_order=created_at_order,
    )


def decode_artifact_row(row: tuple[object, ...]) -> ArtifactRow:
    return ArtifactRow(
        artifact_id=_expect_text(row, 0, "artifacts.artifact_id"),
        work_item_id=_expect_text(row, 1, "artifacts.work_item_id"),
        artifact_schema_id=_expect_text(row, 2, "artifacts.artifact_schema_id"),
        payload_digest=_expect_text(row, 3, "artifacts.payload_digest"),
        created_by_input_id=_expect_text(row, 4, "artifacts.created_by_input_id"),
        source_run_id=_expect_text(row, 5, "artifacts.source_run_id"),
        source_action_id=_expect_text(row, 6, "artifacts.source_action_id"),
        source_stage_kind_id=_expect_text(row, 7, "artifacts.source_stage_kind_id"),
        source_graph_node_id=_expect_text(row, 8, "artifacts.source_graph_node_id"),
        artifact_payload_digest=_expect_text(
            row,
            9,
            "artifacts.artifact_payload_digest",
        ),
        transition_id=_expect_text(row, 10, "artifacts.transition_id"),
        created_at_order=_expect_nonnegative_int(
            row,
            11,
            "artifacts.created_at_order",
        ),
    )


def encode_effect_proposal_row(
    proposal: EffectProposalRecord,
    *,
    created_at_order: int,
) -> EffectProposalRow:
    plan_ref = proposal.selected_plan_ref
    return EffectProposalRow(
        effect_id=proposal.effect_id,
        dedupe_key=proposal.dedupe_key,
        effect_declaration_id=str(proposal.effect_declaration_id),
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        selected_plan_fingerprint=proposal.selected_plan_fingerprint,
        terminal_action_id=str(proposal.terminal_action_id),
        artifact_id=proposal.artifact_id,
        artifact_schema_id=str(proposal.artifact_schema_id),
        artifact_payload_digest=proposal.artifact_payload_digest,
        source_run_id=proposal.source_run_id,
        source_action_id=str(proposal.source_action_id),
        source_input_id=proposal.source_input_id,
        source_work_item_id=proposal.source_work_item_id,
        source_activation_id=proposal.source_activation_id,
        source_graph_node_id=proposal.source_graph_node_id,
        source_stage_kind_id=str(proposal.source_stage_kind_id),
        source_runner_binding_id=str(proposal.source_runner_binding_id),
        source_queue_family_id=str(proposal.source_queue_family_id),
        lineage_id=proposal.lineage_id,
        provider_ref=proposal.provider_ref,
        capability_policy_ref=proposal.capability_policy_ref,
        target_ref_kind=proposal.target_ref_kind,
        target_ref_schema=proposal.target_ref_schema,
        target_skill_id=proposal.target_skill_id,
        target_path_ref=proposal.target_path_ref,
        status=proposal.status,
        created_input_id=proposal.created_input_id,
        created_transition_id=proposal.created_transition_id,
        created_at_order=created_at_order,
    )


def decode_effect_proposal_row(row: tuple[object, ...]) -> EffectProposalRow:
    if len(row) != 31:
        raise StorageIntegrityError("unexpected effect proposal row")
    return EffectProposalRow(
        effect_id=_expect_text(row, 0, "effect_proposals.effect_id"),
        dedupe_key=_expect_text(row, 1, "effect_proposals.dedupe_key"),
        effect_declaration_id=_expect_text(
            row,
            2,
            "effect_proposals.effect_declaration_id",
        ),
        plan_id=_expect_text(row, 3, "effect_proposals.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            4,
            "effect_proposals.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            5,
            "effect_proposals.plan_format_version",
        ),
        selected_plan_fingerprint=_expect_text(
            row,
            6,
            "effect_proposals.selected_plan_fingerprint",
        ),
        terminal_action_id=_expect_text(
            row,
            7,
            "effect_proposals.terminal_action_id",
        ),
        artifact_id=_expect_text(row, 8, "effect_proposals.artifact_id"),
        artifact_schema_id=_expect_text(
            row,
            9,
            "effect_proposals.artifact_schema_id",
        ),
        artifact_payload_digest=_expect_text(
            row,
            10,
            "effect_proposals.artifact_payload_digest",
        ),
        source_run_id=_expect_text(row, 11, "effect_proposals.source_run_id"),
        source_action_id=_expect_text(
            row,
            12,
            "effect_proposals.source_action_id",
        ),
        source_input_id=_expect_text(
            row,
            13,
            "effect_proposals.source_input_id",
        ),
        source_work_item_id=_expect_text(
            row,
            14,
            "effect_proposals.source_work_item_id",
        ),
        source_activation_id=_expect_text(
            row,
            15,
            "effect_proposals.source_activation_id",
        ),
        source_graph_node_id=_expect_text(
            row,
            16,
            "effect_proposals.source_graph_node_id",
        ),
        source_stage_kind_id=_expect_text(
            row,
            17,
            "effect_proposals.source_stage_kind_id",
        ),
        source_runner_binding_id=_expect_text(
            row,
            18,
            "effect_proposals.source_runner_binding_id",
        ),
        source_queue_family_id=_expect_text(
            row,
            19,
            "effect_proposals.source_queue_family_id",
        ),
        lineage_id=_expect_optional_text(row, 20, "effect_proposals.lineage_id"),
        provider_ref=_expect_text(row, 21, "effect_proposals.provider_ref"),
        capability_policy_ref=_expect_text(
            row,
            22,
            "effect_proposals.capability_policy_ref",
        ),
        target_ref_kind=_expect_text(row, 23, "effect_proposals.target_ref_kind"),
        target_ref_schema=_expect_text(
            row,
            24,
            "effect_proposals.target_ref_schema",
        ),
        target_skill_id=_expect_optional_text(
            row,
            25,
            "effect_proposals.target_skill_id",
        ),
        target_path_ref=_expect_optional_text(
            row,
            26,
            "effect_proposals.target_path_ref",
        ),
        status=_expect_text(row, 27, "effect_proposals.status"),
        created_input_id=_expect_text(
            row,
            28,
            "effect_proposals.created_input_id",
        ),
        created_transition_id=_expect_text(
            row,
            29,
            "effect_proposals.created_transition_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            30,
            "effect_proposals.created_at_order",
        ),
    )


def encode_effect_reconciliation_row(
    reconciliation: EffectReconciliationRecord,
    *,
    created_at_order: int,
) -> EffectReconciliationRow:
    plan_ref = reconciliation.selected_plan_ref
    return EffectReconciliationRow(
        reconciliation_id=reconciliation.reconciliation_id,
        effect_id=reconciliation.effect_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        selected_plan_fingerprint=reconciliation.selected_plan_fingerprint,
        provider_ref=reconciliation.provider_ref,
        status=reconciliation.status,
        fake_local_result_digest=reconciliation.fake_local_result_digest,
        created_input_id=reconciliation.created_input_id,
        created_transition_id=reconciliation.created_transition_id,
        created_at_order=created_at_order,
    )


def decode_effect_reconciliation_row(
    row: tuple[object, ...],
) -> EffectReconciliationRow:
    if len(row) != 12:
        raise StorageIntegrityError("unexpected effect reconciliation row")
    return EffectReconciliationRow(
        reconciliation_id=_expect_text(
            row,
            0,
            "effect_reconciliations.reconciliation_id",
        ),
        effect_id=_expect_text(row, 1, "effect_reconciliations.effect_id"),
        plan_id=_expect_text(row, 2, "effect_reconciliations.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            3,
            "effect_reconciliations.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            4,
            "effect_reconciliations.plan_format_version",
        ),
        selected_plan_fingerprint=_expect_text(
            row,
            5,
            "effect_reconciliations.selected_plan_fingerprint",
        ),
        provider_ref=_expect_text(row, 6, "effect_reconciliations.provider_ref"),
        status=_expect_text(row, 7, "effect_reconciliations.status"),
        fake_local_result_digest=_expect_text(
            row,
            8,
            "effect_reconciliations.fake_local_result_digest",
        ),
        created_input_id=_expect_text(
            row,
            9,
            "effect_reconciliations.created_input_id",
        ),
        created_transition_id=_expect_text(
            row,
            10,
            "effect_reconciliations.created_transition_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            11,
            "effect_reconciliations.created_at_order",
        ),
    )


def encode_activation_route_row(
    route: ActivationRouteRecord,
    *,
    created_at_order: int,
) -> ActivationRouteRow:
    return ActivationRouteRow(
        record_id=route.record_id,
        action_id=str(route.action_id),
        source_run_id=route.source_run_id,
        source_work_item_id=route.source_work_item_id,
        target_work_item_id=route.target_work_item_id,
        target_activation_id=route.target_activation_id,
        created_by_input_id=route.created_by_input_id,
        created_at_order=created_at_order,
    )


def decode_activation_route_row(row: tuple[object, ...]) -> ActivationRouteRow:
    return ActivationRouteRow(
        record_id=_expect_text(row, 0, "activation_routes.record_id"),
        action_id=_expect_text(row, 1, "activation_routes.action_id"),
        source_run_id=_expect_text(row, 2, "activation_routes.source_run_id"),
        source_work_item_id=_expect_text(
            row,
            3,
            "activation_routes.source_work_item_id",
        ),
        target_work_item_id=_expect_text(
            row,
            4,
            "activation_routes.target_work_item_id",
        ),
        target_activation_id=_expect_text(
            row,
            5,
            "activation_routes.target_activation_id",
        ),
        created_by_input_id=_expect_text(
            row,
            6,
            "activation_routes.created_by_input_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            7,
            "activation_routes.created_at_order",
        ),
    )


def encode_fanout_row(
    record: FanoutRecord,
    *,
    created_at_order: int,
) -> FanoutRow:
    plan_ref = record.selected_plan_ref
    return FanoutRow(
        record_id=record.record_id,
        fanout_id=str(record.fanout_id),
        source_artifact_id=record.source_artifact_id,
        source_artifact_digest=record.source_artifact_digest,
        source_work_item_id=record.source_work_item_id,
        source_run_id=record.source_run_id,
        source_action_id=str(record.source_action_id),
        target_work_item_id=record.target_work_item_id,
        target_activation_id=record.target_activation_id,
        target_queue_family_id=str(record.target_queue_family_id),
        target_stage_kind_id=str(record.target_stage_kind_id),
        target_graph_node_id=record.target_graph_node_id,
        item_key=record.item_key,
        lineage_id=record.lineage_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        created_by_input_id=record.created_by_input_id,
        created_at_order=created_at_order,
    )


def decode_fanout_row(row: tuple[object, ...]) -> FanoutRow:
    return FanoutRow(
        record_id=_expect_text(row, 0, "fanout_records.record_id"),
        fanout_id=_expect_text(row, 1, "fanout_records.fanout_id"),
        source_artifact_id=_expect_text(
            row,
            2,
            "fanout_records.source_artifact_id",
        ),
        source_artifact_digest=_expect_text(
            row,
            3,
            "fanout_records.source_artifact_digest",
        ),
        source_work_item_id=_expect_text(
            row,
            4,
            "fanout_records.source_work_item_id",
        ),
        source_run_id=_expect_text(row, 5, "fanout_records.source_run_id"),
        source_action_id=_expect_text(row, 6, "fanout_records.source_action_id"),
        target_work_item_id=_expect_text(
            row,
            7,
            "fanout_records.target_work_item_id",
        ),
        target_activation_id=_expect_text(
            row,
            8,
            "fanout_records.target_activation_id",
        ),
        target_queue_family_id=_expect_text(
            row,
            9,
            "fanout_records.target_queue_family_id",
        ),
        target_stage_kind_id=_expect_text(
            row,
            10,
            "fanout_records.target_stage_kind_id",
        ),
        target_graph_node_id=_expect_text(
            row,
            11,
            "fanout_records.target_graph_node_id",
        ),
        item_key=_expect_text(row, 12, "fanout_records.item_key"),
        lineage_id=_expect_optional_text(
            row,
            13,
            "fanout_records.lineage_id",
            allow_empty=False,
        ),
        plan_id=_expect_text(row, 14, "fanout_records.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            15,
            "fanout_records.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            16,
            "fanout_records.plan_format_version",
        ),
        created_by_input_id=_expect_text(
            row,
            17,
            "fanout_records.created_by_input_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            18,
            "fanout_records.created_at_order",
        ),
    )


def encode_work_dependency_row(
    record: WorkDependencyRecord,
    *,
    created_at_order: int,
) -> WorkDependencyRow:
    plan_ref = record.selected_plan_ref
    return WorkDependencyRow(
        dependency_id=record.dependency_id,
        dependent_work_item_id=record.dependent_work_item_id,
        dependency_work_item_id=record.dependency_work_item_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        lineage_id=record.lineage_id,
        fanout_record_id=record.fanout_record_id,
        created_by_input_id=record.created_by_input_id,
        created_at_order=created_at_order,
    )


def decode_work_dependency_row(row: tuple[object, ...]) -> WorkDependencyRow:
    return WorkDependencyRow(
        dependency_id=_expect_text(row, 0, "work_dependencies.dependency_id"),
        dependent_work_item_id=_expect_text(
            row,
            1,
            "work_dependencies.dependent_work_item_id",
        ),
        dependency_work_item_id=_expect_text(
            row,
            2,
            "work_dependencies.dependency_work_item_id",
        ),
        plan_id=_expect_text(row, 3, "work_dependencies.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            4,
            "work_dependencies.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            5,
            "work_dependencies.plan_format_version",
        ),
        lineage_id=_expect_optional_text(
            row,
            6,
            "work_dependencies.lineage_id",
            allow_empty=False,
        ),
        fanout_record_id=_expect_text(
            row,
            7,
            "work_dependencies.fanout_record_id",
        ),
        created_by_input_id=_expect_text(
            row,
            8,
            "work_dependencies.created_by_input_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            9,
            "work_dependencies.created_at_order",
        ),
    )


def encode_closure_target_row(
    record: ClosureTargetRecord,
    *,
    evidence_window_digest: str,
    created_at_order: int,
) -> ClosureTargetRow:
    plan_ref = record.selected_plan_ref
    return ClosureTargetRow(
        closure_target_id=record.closure_target_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        completion_behavior_id=str(record.completion_behavior_id),
        lineage_id=record.lineage_id,
        root_source_kind=record.root_source_kind,
        root_source_id=record.root_source_id,
        closure_root_work_item_id=record.closure_root_work_item_id,
        request_kind=record.request_kind,
        target_graph_node_id=record.target_graph_node_id,
        evidence_window_digest=evidence_window_digest,
        status=record.status,
        opened_by_input_id=record.opened_by_input_id,
        closed_by_record_id=record.closed_by_record_id,
        created_at_order=created_at_order,
    )


def decode_closure_target_row(row: tuple[object, ...]) -> ClosureTargetRow:
    return ClosureTargetRow(
        closure_target_id=_expect_text(row, 0, "closure_targets.closure_target_id"),
        plan_id=_expect_text(row, 1, "closure_targets.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            2,
            "closure_targets.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            3,
            "closure_targets.plan_format_version",
        ),
        completion_behavior_id=_expect_text(
            row,
            4,
            "closure_targets.completion_behavior_id",
        ),
        lineage_id=_expect_text(row, 5, "closure_targets.lineage_id"),
        root_source_kind=_expect_text(row, 6, "closure_targets.root_source_kind"),
        root_source_id=_expect_text(row, 7, "closure_targets.root_source_id"),
        closure_root_work_item_id=_expect_optional_text(
            row,
            8,
            "closure_targets.closure_root_work_item_id",
            allow_empty=False,
        ),
        request_kind=_expect_text(row, 9, "closure_targets.request_kind"),
        target_graph_node_id=_expect_text(
            row,
            10,
            "closure_targets.target_graph_node_id",
        ),
        evidence_window_digest=_expect_text(
            row,
            11,
            "closure_targets.evidence_window_digest",
        ),
        status=_expect_text(row, 12, "closure_targets.status"),
        opened_by_input_id=_expect_text(row, 13, "closure_targets.opened_by_input_id"),
        closed_by_record_id=_expect_optional_text(
            row,
            14,
            "closure_targets.closed_by_record_id",
            allow_empty=False,
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            15,
            "closure_targets.created_at_order",
        ),
    )


def encode_closure_evaluation_row(
    record: ClosureEvaluationRecord,
    *,
    created_at_order: int,
) -> ClosureEvaluationRow:
    plan_ref = record.selected_plan_ref
    return ClosureEvaluationRow(
        record_id=record.record_id,
        closure_target_id=record.closure_target_id,
        completion_behavior_id=str(record.completion_behavior_id),
        request_kind=record.request_kind,
        target_work_item_id=record.target_work_item_id,
        target_activation_id=record.target_activation_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        lineage_id=record.lineage_id,
        created_by_input_id=record.created_by_input_id,
        created_at_order=created_at_order,
    )


def decode_closure_evaluation_row(
    row: tuple[object, ...],
) -> ClosureEvaluationRow:
    return ClosureEvaluationRow(
        record_id=_expect_text(row, 0, "closure_evaluations.record_id"),
        closure_target_id=_expect_text(
            row,
            1,
            "closure_evaluations.closure_target_id",
        ),
        completion_behavior_id=_expect_text(
            row,
            2,
            "closure_evaluations.completion_behavior_id",
        ),
        request_kind=_expect_text(row, 3, "closure_evaluations.request_kind"),
        target_work_item_id=_expect_text(
            row,
            4,
            "closure_evaluations.target_work_item_id",
        ),
        target_activation_id=_expect_text(
            row,
            5,
            "closure_evaluations.target_activation_id",
        ),
        plan_id=_expect_text(row, 6, "closure_evaluations.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            7,
            "closure_evaluations.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            8,
            "closure_evaluations.plan_format_version",
        ),
        lineage_id=_expect_text(row, 9, "closure_evaluations.lineage_id"),
        created_by_input_id=_expect_text(
            row,
            10,
            "closure_evaluations.created_by_input_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            11,
            "closure_evaluations.created_at_order",
        ),
    )


def encode_closure_terminal_row(
    record: ClosureTerminalRecord,
    *,
    created_at_order: int,
) -> ClosureTerminalRow:
    plan_ref = record.selected_plan_ref
    return ClosureTerminalRow(
        record_id=record.record_id,
        closure_target_id=record.closure_target_id,
        completion_behavior_id=str(record.completion_behavior_id),
        terminal_kind=record.terminal_kind,
        source_run_id=record.source_run_id,
        source_action_id=str(record.source_action_id),
        source_artifact_id=record.source_artifact_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        lineage_id=record.lineage_id,
        created_by_input_id=record.created_by_input_id,
        created_at_order=created_at_order,
    )


def decode_closure_terminal_row(row: tuple[object, ...]) -> ClosureTerminalRow:
    return ClosureTerminalRow(
        record_id=_expect_text(row, 0, "closure_terminal_records.record_id"),
        closure_target_id=_expect_text(
            row,
            1,
            "closure_terminal_records.closure_target_id",
        ),
        completion_behavior_id=_expect_text(
            row,
            2,
            "closure_terminal_records.completion_behavior_id",
        ),
        terminal_kind=_expect_text(row, 3, "closure_terminal_records.terminal_kind"),
        source_run_id=_expect_text(row, 4, "closure_terminal_records.source_run_id"),
        source_action_id=_expect_text(
            row,
            5,
            "closure_terminal_records.source_action_id",
        ),
        source_artifact_id=_expect_optional_text(
            row,
            6,
            "closure_terminal_records.source_artifact_id",
            allow_empty=False,
        ),
        plan_id=_expect_text(row, 7, "closure_terminal_records.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            8,
            "closure_terminal_records.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            9,
            "closure_terminal_records.plan_format_version",
        ),
        lineage_id=_expect_text(row, 10, "closure_terminal_records.lineage_id"),
        created_by_input_id=_expect_text(
            row,
            11,
            "closure_terminal_records.created_by_input_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            12,
            "closure_terminal_records.created_at_order",
        ),
    )


def encode_remediation_work_row(
    record: RemediationWorkRecord,
    *,
    created_at_order: int,
) -> RemediationWorkRow:
    plan_ref = record.selected_plan_ref
    return RemediationWorkRow(
        record_id=record.record_id,
        remediation_policy_id=str(record.remediation_policy_id),
        closure_target_id=record.closure_target_id,
        source_run_id=record.source_run_id,
        source_action_id=str(record.source_action_id),
        source_artifact_id=record.source_artifact_id,
        target_work_item_id=record.target_work_item_id,
        target_activation_id=record.target_activation_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        lineage_id=record.lineage_id,
        dedupe_key=record.dedupe_key,
        created_by_input_id=record.created_by_input_id,
        created_at_order=created_at_order,
    )


def decode_remediation_work_row(row: tuple[object, ...]) -> RemediationWorkRow:
    return RemediationWorkRow(
        record_id=_expect_text(row, 0, "remediation_work_records.record_id"),
        remediation_policy_id=_expect_text(
            row,
            1,
            "remediation_work_records.remediation_policy_id",
        ),
        closure_target_id=_expect_text(
            row,
            2,
            "remediation_work_records.closure_target_id",
        ),
        source_run_id=_expect_text(row, 3, "remediation_work_records.source_run_id"),
        source_action_id=_expect_text(
            row,
            4,
            "remediation_work_records.source_action_id",
        ),
        source_artifact_id=_expect_optional_text(
            row,
            5,
            "remediation_work_records.source_artifact_id",
            allow_empty=False,
        ),
        target_work_item_id=_expect_text(
            row,
            6,
            "remediation_work_records.target_work_item_id",
        ),
        target_activation_id=_expect_text(
            row,
            7,
            "remediation_work_records.target_activation_id",
        ),
        plan_id=_expect_text(row, 8, "remediation_work_records.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            9,
            "remediation_work_records.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            10,
            "remediation_work_records.plan_format_version",
        ),
        lineage_id=_expect_text(row, 11, "remediation_work_records.lineage_id"),
        dedupe_key=_expect_text(row, 12, "remediation_work_records.dedupe_key"),
        created_by_input_id=_expect_text(
            row,
            13,
            "remediation_work_records.created_by_input_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            14,
            "remediation_work_records.created_at_order",
        ),
    )


def encode_closure_blocked_row(
    record: ClosureBlockedRecord,
    *,
    created_at_order: int,
) -> ClosureBlockedRow:
    plan_ref = record.selected_plan_ref
    return ClosureBlockedRow(
        record_id=record.record_id,
        closure_target_id=record.closure_target_id,
        completion_behavior_id=str(record.completion_behavior_id),
        source_run_id=record.source_run_id,
        source_action_id=str(record.source_action_id),
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        lineage_id=record.lineage_id,
        operator_required=int(record.operator_required),
        created_by_input_id=record.created_by_input_id,
        created_at_order=created_at_order,
    )


def decode_closure_blocked_row(row: tuple[object, ...]) -> ClosureBlockedRow:
    return ClosureBlockedRow(
        record_id=_expect_text(row, 0, "closure_blocked_records.record_id"),
        closure_target_id=_expect_text(
            row,
            1,
            "closure_blocked_records.closure_target_id",
        ),
        completion_behavior_id=_expect_text(
            row,
            2,
            "closure_blocked_records.completion_behavior_id",
        ),
        source_run_id=_expect_text(row, 3, "closure_blocked_records.source_run_id"),
        source_action_id=_expect_text(
            row,
            4,
            "closure_blocked_records.source_action_id",
        ),
        plan_id=_expect_text(row, 5, "closure_blocked_records.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            6,
            "closure_blocked_records.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            7,
            "closure_blocked_records.plan_format_version",
        ),
        lineage_id=_expect_text(row, 8, "closure_blocked_records.lineage_id"),
        operator_required=_expect_bool_int(
            row,
            9,
            "closure_blocked_records.operator_required",
        ),
        created_by_input_id=_expect_text(
            row,
            10,
            "closure_blocked_records.created_by_input_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            11,
            "closure_blocked_records.created_at_order",
        ),
    )


def encode_closed_work_item_row(
    record: ClosedWorkItemRecord,
    *,
    closed_at_order: int,
) -> ClosedWorkItemRow:
    return ClosedWorkItemRow(
        record_id=record.record_id,
        work_item_id=record.work_item_id,
        source_run_id=record.source_run_id,
        action_id=str(record.action_id) if record.action_id is not None else None,
        operator_intervention_record_id=record.operator_intervention_record_id,
        close_kind=record.close_kind,
        created_by_input_id=record.created_by_input_id,
        closed_at_order=closed_at_order,
    )


def decode_closed_work_item_row(row: tuple[object, ...]) -> ClosedWorkItemRow:
    return ClosedWorkItemRow(
        record_id=_expect_text(row, 0, "closed_work_items.record_id"),
        work_item_id=_expect_text(row, 1, "closed_work_items.work_item_id"),
        source_run_id=_expect_optional_text(
            row,
            2,
            "closed_work_items.source_run_id",
            allow_empty=False,
        ),
        action_id=_expect_optional_text(
            row,
            3,
            "closed_work_items.action_id",
            allow_empty=False,
        ),
        operator_intervention_record_id=_expect_optional_text(
            row,
            4,
            "closed_work_items.operator_intervention_record_id",
            allow_empty=False,
        ),
        close_kind=_expect_text(row, 5, "closed_work_items.close_kind"),
        created_by_input_id=_expect_text(
            row,
            6,
            "closed_work_items.created_by_input_id",
        ),
        closed_at_order=_expect_nonnegative_int(
            row,
            7,
            "closed_work_items.closed_at_order",
        ),
    )


def encode_pause_state_row(
    record: PauseRecord | None,
) -> PauseStateRow | None:
    if record is None:
        return None
    return PauseStateRow(
        record_id=record.record_id,
        source_run_id=record.source_run_id,
        work_item_id=record.work_item_id,
        action_id=str(record.action_id),
        created_by_input_id=record.created_by_input_id,
        paused_at_order=0,
    )


def decode_pause_state_row(row: tuple[object, ...]) -> PauseStateRow:
    return PauseStateRow(
        record_id=_expect_text(row, 0, "pause_state.record_id"),
        source_run_id=_expect_text(row, 1, "pause_state.source_run_id"),
        work_item_id=_expect_text(row, 2, "pause_state.work_item_id"),
        action_id=_expect_text(row, 3, "pause_state.action_id"),
        created_by_input_id=_expect_text(
            row,
            4,
            "pause_state.created_by_input_id",
        ),
        paused_at_order=_expect_nonnegative_int(
            row,
            5,
            "pause_state.paused_at_order",
        ),
    )


def encode_quarantine_row(
    record: QuarantineRecord,
    *,
    created_at_order: int,
) -> QuarantineRow:
    return QuarantineRow(
        record_id=record.record_id,
        work_item_id=record.work_item_id,
        source_run_id=record.source_run_id,
        action_id=str(record.action_id),
        created_by_input_id=record.created_by_input_id,
        created_at_order=created_at_order,
    )


def decode_quarantine_row(row: tuple[object, ...]) -> QuarantineRow:
    return QuarantineRow(
        record_id=_expect_text(row, 0, "quarantine_records.record_id"),
        work_item_id=_expect_text(row, 1, "quarantine_records.work_item_id"),
        source_run_id=_expect_text(row, 2, "quarantine_records.source_run_id"),
        action_id=_expect_text(row, 3, "quarantine_records.action_id"),
        created_by_input_id=_expect_text(
            row,
            4,
            "quarantine_records.created_by_input_id",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            5,
            "quarantine_records.created_at_order",
        ),
    )


def encode_lineage_quarantine_row(
    record: LineageQuarantineRecord,
    *,
    created_at_order: int,
) -> LineageQuarantineRow:
    plan_ref = record.selected_plan_ref
    return LineageQuarantineRow(
        quarantine_id=record.quarantine_id,
        policy_id=str(record.policy_id),
        lineage_id=record.lineage_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        recovery_attempt_record_id=record.recovery_attempt_record_id,
        original_source_run_id=record.original_source_run_id,
        original_source_work_item_id=record.original_source_work_item_id,
        original_source_activation_id=record.original_source_activation_id,
        emitting_recovery_activation_id=record.emitting_recovery_activation_id,
        emitting_recovery_run_id=record.emitting_recovery_run_id,
        action_id=str(record.action_id),
        attempt_count=record.attempt_count,
        created_input_id=record.created_input_id,
        actor_kind=record.actor_kind,
        status=record.status,
        superseded_input_id=record.superseded_input_id,
        created_at_order=created_at_order,
    )


def decode_lineage_quarantine_row(
    row: tuple[object, ...],
) -> LineageQuarantineRow:
    return LineageQuarantineRow(
        quarantine_id=_expect_text(row, 0, "lineage_quarantines.quarantine_id"),
        policy_id=_expect_text(row, 1, "lineage_quarantines.policy_id"),
        lineage_id=_expect_text(row, 2, "lineage_quarantines.lineage_id"),
        plan_id=_expect_text(row, 3, "lineage_quarantines.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            4,
            "lineage_quarantines.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            5,
            "lineage_quarantines.plan_format_version",
        ),
        recovery_attempt_record_id=_expect_text(
            row,
            6,
            "lineage_quarantines.recovery_attempt_record_id",
        ),
        original_source_run_id=_expect_text(
            row,
            7,
            "lineage_quarantines.original_source_run_id",
        ),
        original_source_work_item_id=_expect_text(
            row,
            8,
            "lineage_quarantines.original_source_work_item_id",
        ),
        original_source_activation_id=_expect_text(
            row,
            9,
            "lineage_quarantines.original_source_activation_id",
        ),
        emitting_recovery_activation_id=_expect_text(
            row,
            10,
            "lineage_quarantines.emitting_recovery_activation_id",
        ),
        emitting_recovery_run_id=_expect_text(
            row,
            11,
            "lineage_quarantines.emitting_recovery_run_id",
        ),
        action_id=_expect_text(row, 12, "lineage_quarantines.action_id"),
        attempt_count=_expect_positive_int(
            row,
            13,
            "lineage_quarantines.attempt_count",
        ),
        created_input_id=_expect_text(
            row,
            14,
            "lineage_quarantines.created_input_id",
        ),
        actor_kind=_expect_text(row, 15, "lineage_quarantines.actor_kind"),
        status=_expect_text(row, 16, "lineage_quarantines.status"),
        superseded_input_id=_expect_optional_text(
            row,
            17,
            "lineage_quarantines.superseded_input_id",
            allow_empty=False,
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            18,
            "lineage_quarantines.created_at_order",
        ),
    )


def encode_recovery_attempt_row(
    attempt: RecoveryAttemptRecord,
    *,
    updated_at_order: int,
) -> RecoveryAttemptRow:
    plan_ref = attempt.plan_ref
    return RecoveryAttemptRow(
        record_id=attempt.record_id,
        policy_id=str(attempt.policy_id),
        lineage_id=attempt.lineage_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        attempt_count=attempt.attempt_count,
        phase=attempt.phase,
        source_run_id=attempt.source_run_id,
        source_work_item_id=attempt.source_work_item_id,
        source_activation_id=attempt.source_activation_id,
        source_graph_node_id=attempt.source_graph_node_id,
        source_stage_kind_id=str(attempt.source_stage_kind_id),
        source_runner_binding_id=str(attempt.source_runner_binding_id),
        source_queue_family_id=str(attempt.source_queue_family_id),
        recovery_action_id=str(attempt.recovery_action_id),
        latest_recovery_activation_id=attempt.latest_recovery_activation_id,
        latest_recovery_run_id=attempt.latest_recovery_run_id,
        latest_return_action_id=(
            str(attempt.latest_return_action_id)
            if attempt.latest_return_action_id is not None
            else None
        ),
        created_by_input_id=attempt.created_by_input_id,
        updated_by_input_id=attempt.updated_by_input_id,
        updated_at_order=updated_at_order,
    )


def decode_recovery_attempt_row(row: tuple[object, ...]) -> RecoveryAttemptRow:
    return RecoveryAttemptRow(
        record_id=_expect_text(row, 0, "recovery_attempts.record_id"),
        policy_id=_expect_text(row, 1, "recovery_attempts.policy_id"),
        lineage_id=_expect_text(row, 2, "recovery_attempts.lineage_id"),
        plan_id=_expect_text(row, 3, "recovery_attempts.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            4,
            "recovery_attempts.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            5,
            "recovery_attempts.plan_format_version",
        ),
        attempt_count=_expect_positive_int(
            row,
            6,
            "recovery_attempts.attempt_count",
        ),
        phase=_expect_text(row, 7, "recovery_attempts.phase"),
        source_run_id=_expect_text(row, 8, "recovery_attempts.source_run_id"),
        source_work_item_id=_expect_text(
            row,
            9,
            "recovery_attempts.source_work_item_id",
        ),
        source_activation_id=_expect_text(
            row,
            10,
            "recovery_attempts.source_activation_id",
        ),
        source_graph_node_id=_expect_text(
            row,
            11,
            "recovery_attempts.source_graph_node_id",
        ),
        source_stage_kind_id=_expect_text(
            row,
            12,
            "recovery_attempts.source_stage_kind_id",
        ),
        source_runner_binding_id=_expect_text(
            row,
            13,
            "recovery_attempts.source_runner_binding_id",
        ),
        source_queue_family_id=_expect_text(
            row,
            14,
            "recovery_attempts.source_queue_family_id",
        ),
        recovery_action_id=_expect_text(
            row,
            15,
            "recovery_attempts.recovery_action_id",
        ),
        latest_recovery_activation_id=_expect_optional_text(
            row,
            16,
            "recovery_attempts.latest_recovery_activation_id",
            allow_empty=False,
        ),
        latest_recovery_run_id=_expect_optional_text(
            row,
            17,
            "recovery_attempts.latest_recovery_run_id",
            allow_empty=False,
        ),
        latest_return_action_id=_expect_optional_text(
            row,
            18,
            "recovery_attempts.latest_return_action_id",
            allow_empty=False,
        ),
        created_by_input_id=_expect_text(
            row,
            19,
            "recovery_attempts.created_by_input_id",
        ),
        updated_by_input_id=_expect_text(
            row,
            20,
            "recovery_attempts.updated_by_input_id",
        ),
        updated_at_order=_expect_nonnegative_int(
            row,
            21,
            "recovery_attempts.updated_at_order",
        ),
    )


def encode_operator_intervention_row(
    record: OperatorInterventionRecord,
    *,
    created_at_order: int,
) -> OperatorInterventionRow:
    plan_ref = record.selected_plan_ref
    return OperatorInterventionRow(
        record_id=record.record_id,
        created_by_input_id=record.created_by_input_id,
        input_payload_digest=record.input_payload_digest,
        option_id=record.option_id,
        kind=record.kind,
        result=record.result,
        policy_id=str(record.policy_id),
        lineage_id=record.lineage_id,
        quarantine_id=record.quarantine_id,
        recovery_attempt_record_id=record.recovery_attempt_record_id,
        recovery_attempt_count=record.recovery_attempt_count,
        attempt_effect=record.attempt_effect,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        actor_kind=record.actor_kind,
        actor_id=record.actor_id,
        reason=record.reason,
        target_work_item_id=record.target_work_item_id,
        target_activation_id=record.target_activation_id,
        closed_work_item_ids_json=_json_string_tuple(record.closed_work_item_ids),
        closed_activation_ids_json=_json_string_tuple(record.closed_activation_ids),
        closed_run_ids_json=_json_string_tuple(record.closed_run_ids),
        payload_digest=record.payload_digest,
        payload_reference=record.payload_reference,
        created_at_order=created_at_order,
    )


def decode_operator_intervention_row(
    row: tuple[object, ...],
) -> OperatorInterventionRow:
    return OperatorInterventionRow(
        record_id=_expect_text(row, 0, "operator_interventions.record_id"),
        created_by_input_id=_expect_text(
            row,
            1,
            "operator_interventions.created_by_input_id",
        ),
        input_payload_digest=_expect_text(
            row,
            2,
            "operator_interventions.input_payload_digest",
        ),
        option_id=_expect_text(row, 3, "operator_interventions.option_id"),
        kind=_expect_text(row, 4, "operator_interventions.kind"),
        result=_expect_text(row, 5, "operator_interventions.result"),
        policy_id=_expect_text(row, 6, "operator_interventions.policy_id"),
        lineage_id=_expect_text(row, 7, "operator_interventions.lineage_id"),
        quarantine_id=_expect_text(row, 8, "operator_interventions.quarantine_id"),
        recovery_attempt_record_id=_expect_text(
            row,
            9,
            "operator_interventions.recovery_attempt_record_id",
        ),
        recovery_attempt_count=_expect_positive_int(
            row,
            10,
            "operator_interventions.recovery_attempt_count",
        ),
        attempt_effect=_expect_text(row, 11, "operator_interventions.attempt_effect"),
        plan_id=_expect_text(row, 12, "operator_interventions.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            13,
            "operator_interventions.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            14,
            "operator_interventions.plan_format_version",
        ),
        actor_kind=_expect_text(row, 15, "operator_interventions.actor_kind"),
        actor_id=_expect_text(row, 16, "operator_interventions.actor_id"),
        reason=_expect_text(row, 17, "operator_interventions.reason"),
        target_work_item_id=_expect_optional_text(
            row,
            18,
            "operator_interventions.target_work_item_id",
            allow_empty=False,
        ),
        target_activation_id=_expect_optional_text(
            row,
            19,
            "operator_interventions.target_activation_id",
            allow_empty=False,
        ),
        closed_work_item_ids_json=_json_string_tuple(
            _expect_json_string_tuple(
                row,
                20,
                "operator_interventions.closed_work_item_ids_json",
            )
        ),
        closed_activation_ids_json=_json_string_tuple(
            _expect_json_string_tuple(
                row,
                21,
                "operator_interventions.closed_activation_ids_json",
            )
        ),
        closed_run_ids_json=_json_string_tuple(
            _expect_json_string_tuple(
                row,
                22,
                "operator_interventions.closed_run_ids_json",
            )
        ),
        payload_digest=_expect_text(row, 23, "operator_interventions.payload_digest"),
        payload_reference=_expect_optional_text(
            row,
            24,
            "operator_interventions.payload_reference",
            allow_empty=False,
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            25,
            "operator_interventions.created_at_order",
        ),
    )


def encode_operator_wait_row(
    record: OperatorWaitRecord,
    *,
    created_at_order: int,
) -> OperatorWaitRow:
    plan_ref = record.selected_plan_ref
    return OperatorWaitRow(
        wait_id=record.wait_id,
        operator_wait_id=str(record.operator_wait_id),
        source_action_id=str(record.source_action_id),
        lineage_id=record.lineage_id,
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        source_work_item_id=record.source_work_item_id,
        source_activation_id=record.source_activation_id,
        source_run_id=record.source_run_id,
        source_stage_kind_id=str(record.source_stage_kind_id),
        source_graph_node_id=record.source_graph_node_id,
        source_queue_family_id=str(record.source_queue_family_id),
        source_runner_binding_id=str(record.source_runner_binding_id),
        source_artifact_id=record.source_artifact_id,
        status=record.status,
        created_input_id=record.created_input_id,
        created_input_payload_digest=record.created_input_payload_digest,
        resolved_input_id=record.resolved_input_id,
        resolved_input_payload_digest=record.resolved_input_payload_digest,
        actor_id=record.actor_id,
        actor_kind=record.actor_kind,
        resolution_kind=record.resolution_kind,
        target_work_item_id=record.target_work_item_id,
        target_activation_id=record.target_activation_id,
        closed_work_item_ids_json=(
            _json_string_tuple(record.closed_work_item_ids)
            if record.status != "active"
            else None
        ),
        payload_digest=record.payload_digest,
        payload_reference=record.payload_reference,
        created_at_order=created_at_order,
    )


def decode_operator_wait_row(row: tuple[object, ...]) -> OperatorWaitRow:
    return OperatorWaitRow(
        wait_id=_expect_text(row, 0, "operator_waits.wait_id"),
        operator_wait_id=_expect_text(row, 1, "operator_waits.operator_wait_id"),
        source_action_id=_expect_text(row, 2, "operator_waits.source_action_id"),
        lineage_id=_expect_text(row, 3, "operator_waits.lineage_id"),
        plan_id=_expect_text(row, 4, "operator_waits.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            5,
            "operator_waits.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            6,
            "operator_waits.plan_format_version",
        ),
        source_work_item_id=_expect_text(row, 7, "operator_waits.source_work_item_id"),
        source_activation_id=_expect_text(
            row,
            8,
            "operator_waits.source_activation_id",
        ),
        source_run_id=_expect_text(row, 9, "operator_waits.source_run_id"),
        source_stage_kind_id=_expect_text(
            row,
            10,
            "operator_waits.source_stage_kind_id",
        ),
        source_graph_node_id=_expect_text(
            row,
            11,
            "operator_waits.source_graph_node_id",
        ),
        source_queue_family_id=_expect_text(
            row,
            12,
            "operator_waits.source_queue_family_id",
        ),
        source_runner_binding_id=_expect_text(
            row,
            13,
            "operator_waits.source_runner_binding_id",
        ),
        source_artifact_id=_expect_optional_text(
            row,
            14,
            "operator_waits.source_artifact_id",
            allow_empty=False,
        ),
        status=_expect_text(row, 15, "operator_waits.status"),
        created_input_id=_expect_text(row, 16, "operator_waits.created_input_id"),
        created_input_payload_digest=_expect_text(
            row,
            17,
            "operator_waits.created_input_payload_digest",
        ),
        resolved_input_id=_expect_optional_text(
            row,
            18,
            "operator_waits.resolved_input_id",
            allow_empty=False,
        ),
        resolved_input_payload_digest=_expect_optional_text(
            row,
            19,
            "operator_waits.resolved_input_payload_digest",
            allow_empty=False,
        ),
        actor_id=_expect_optional_text(
            row,
            20,
            "operator_waits.actor_id",
            allow_empty=False,
        ),
        actor_kind=_expect_optional_text(
            row,
            21,
            "operator_waits.actor_kind",
            allow_empty=False,
        ),
        resolution_kind=_expect_optional_text(
            row,
            22,
            "operator_waits.resolution_kind",
            allow_empty=False,
        ),
        target_work_item_id=_expect_optional_text(
            row,
            23,
            "operator_waits.target_work_item_id",
            allow_empty=False,
        ),
        target_activation_id=_expect_optional_text(
            row,
            24,
            "operator_waits.target_activation_id",
            allow_empty=False,
        ),
        closed_work_item_ids_json=_expect_optional_text(
            row,
            25,
            "operator_waits.closed_work_item_ids_json",
            allow_empty=False,
        ),
        payload_digest=_expect_optional_text(
            row,
            26,
            "operator_waits.payload_digest",
            allow_empty=False,
        ),
        payload_reference=_expect_optional_text(
            row,
            27,
            "operator_waits.payload_reference",
            allow_empty=False,
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            28,
            "operator_waits.created_at_order",
        ),
    )


def encode_cooldown_wait_row(
    wait: CooldownWaitRecord,
    *,
    updated_at_order: int,
) -> CooldownWaitRow:
    plan_ref = wait.plan_ref
    return CooldownWaitRow(
        wait_id=wait.wait_id,
        policy_id=str(wait.policy_id),
        lineage_id=wait.lineage_id,
        recovery_attempt_record_id=wait.recovery_attempt_record_id,
        attempt_count=wait.attempt_count,
        source_run_id=wait.source_run_id,
        source_work_item_id=wait.source_work_item_id,
        source_activation_id=wait.source_activation_id,
        recovery_action_id=str(wait.recovery_action_id),
        target_stage_kind_id=str(wait.target_stage_kind_id),
        target_graph_node_id=wait.target_graph_node_id,
        target_runner_binding_id=str(wait.target_runner_binding_id),
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        created_input_id=wait.created_input_id,
        created_at=wait.created_at,
        due_at=wait.due_at,
        consumed_input_id=wait.consumed_input_id,
        consumed_at=wait.consumed_at,
        resulting_recovery_activation_id=wait.resulting_recovery_activation_id,
        updated_at_order=updated_at_order,
    )


def decode_cooldown_wait_row(row: tuple[object, ...]) -> CooldownWaitRow:
    return CooldownWaitRow(
        wait_id=_expect_text(row, 0, "cooldown_waits.wait_id"),
        policy_id=_expect_text(row, 1, "cooldown_waits.policy_id"),
        lineage_id=_expect_text(row, 2, "cooldown_waits.lineage_id"),
        recovery_attempt_record_id=_expect_text(
            row,
            3,
            "cooldown_waits.recovery_attempt_record_id",
        ),
        attempt_count=_expect_positive_int(
            row,
            4,
            "cooldown_waits.attempt_count",
        ),
        source_run_id=_expect_text(row, 5, "cooldown_waits.source_run_id"),
        source_work_item_id=_expect_text(
            row,
            6,
            "cooldown_waits.source_work_item_id",
        ),
        source_activation_id=_expect_text(
            row,
            7,
            "cooldown_waits.source_activation_id",
        ),
        recovery_action_id=_expect_text(
            row,
            8,
            "cooldown_waits.recovery_action_id",
        ),
        target_stage_kind_id=_expect_text(
            row,
            9,
            "cooldown_waits.target_stage_kind_id",
        ),
        target_graph_node_id=_expect_text(
            row,
            10,
            "cooldown_waits.target_graph_node_id",
        ),
        target_runner_binding_id=_expect_text(
            row,
            11,
            "cooldown_waits.target_runner_binding_id",
        ),
        plan_id=_expect_text(row, 12, "cooldown_waits.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            13,
            "cooldown_waits.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            14,
            "cooldown_waits.plan_format_version",
        ),
        created_input_id=_expect_text(row, 15, "cooldown_waits.created_input_id"),
        created_at=_expect_nonnegative_int(row, 16, "cooldown_waits.created_at"),
        due_at=_expect_nonnegative_int(row, 17, "cooldown_waits.due_at"),
        consumed_input_id=_expect_optional_text(
            row,
            18,
            "cooldown_waits.consumed_input_id",
            allow_empty=False,
        ),
        consumed_at=_expect_optional_nonnegative_int(
            row,
            19,
            "cooldown_waits.consumed_at",
        ),
        resulting_recovery_activation_id=_expect_optional_text(
            row,
            20,
            "cooldown_waits.resulting_recovery_activation_id",
            allow_empty=False,
        ),
        updated_at_order=_expect_nonnegative_int(
            row,
            21,
            "cooldown_waits.updated_at_order",
        ),
    )


def encode_counter_row(
    record: CounterRecord,
    *,
    updated_at_order: int,
) -> CounterRow:
    plan_ref = record.selected_plan_ref
    return CounterRow(
        record_id=record.record_id,
        counter_id=str(record.counter_id),
        plan_id=plan_ref.plan_id,
        plan_authority_fingerprint=plan_ref.authority_fingerprint,
        plan_format_version=plan_ref.plan_format_version,
        lineage_id=record.lineage_id,
        value=record.value,
        updated_by_input_id=record.updated_by_input_id,
        updated_at_order=updated_at_order,
    )


def decode_counter_row(row: tuple[object, ...]) -> CounterRow:
    return CounterRow(
        record_id=_expect_text(row, 0, "counters.record_id"),
        counter_id=_expect_text(row, 1, "counters.counter_id"),
        plan_id=_expect_text(row, 2, "counters.plan_id"),
        plan_authority_fingerprint=_expect_text(
            row,
            3,
            "counters.plan_authority_fingerprint",
        ),
        plan_format_version=_expect_plan_format_version(
            row,
            4,
            "counters.plan_format_version",
        ),
        lineage_id=_expect_text(row, 5, "counters.lineage_id"),
        value=_expect_positive_int(row, 6, "counters.value"),
        updated_by_input_id=_expect_text(row, 7, "counters.updated_by_input_id"),
        updated_at_order=_expect_nonnegative_int(
            row,
            8,
            "counters.updated_at_order",
        ),
    )


def encode_transition_row(
    record: TransitionRecord,
    *,
    transition_order: int,
) -> TransitionRow:
    # TransitionRecord carries ordering but no timestamp; keep this deterministic
    # instead of inventing wall-clock authority during persistence.
    return TransitionRow(
        transition_order=transition_order,
        record_id=record.record_id,
        input_id=record.input_id,
        input_kind=record.input_kind,
        input_family=record.input_family,
        accepted=int(record.accepted),
        created_at=f"transition-order:{transition_order}",
    )


def decode_transition_row(row: tuple[object, ...]) -> TransitionRow:
    return TransitionRow(
        transition_order=_expect_nonnegative_int(
            row,
            0,
            "transitions.transition_order",
        ),
        record_id=_expect_text(row, 1, "transitions.record_id"),
        input_id=_expect_text(row, 2, "transitions.input_id"),
        input_kind=_expect_text(row, 3, "transitions.input_kind"),
        input_family=_expect_text(row, 4, "transitions.input_family"),
        accepted=_expect_bool_int(row, 5, "transitions.accepted"),
        created_at=_expect_text(row, 6, "transitions.created_at"),
    )


def encode_governance_event_row(
    event: GovernanceEventRecord,
    *,
    transition_order: int,
    created_at_order: int,
) -> GovernanceEventRow:
    return GovernanceEventRow(
        record_id=event.record_id,
        transition_order=transition_order,
        input_id=event.input_id,
        input_kind=event.input_kind,
        input_family=event.input_family,
        disposition=event.disposition,
        plan_fingerprint=event.plan_fingerprint,
        work_item_id=event.work_item_id,
        run_id=event.run_id,
        action_id=str(event.action_id) if event.action_id is not None else None,
        authority_source=event.authority_source,
        refusal_reason=event.refusal_reason,
        created_at_order=created_at_order,
    )


def decode_governance_event_row(row: tuple[object, ...]) -> GovernanceEventRow:
    return GovernanceEventRow(
        record_id=_expect_text(row, 0, "governance_events.record_id"),
        transition_order=_expect_nonnegative_int(
            row,
            1,
            "governance_events.transition_order",
        ),
        input_id=_expect_text(row, 2, "governance_events.input_id"),
        input_kind=_expect_text(row, 3, "governance_events.input_kind"),
        input_family=_expect_text(row, 4, "governance_events.input_family"),
        disposition=_expect_text(row, 5, "governance_events.disposition"),
        plan_fingerprint=_expect_optional_text(
            row,
            6,
            "governance_events.plan_fingerprint",
            allow_empty=False,
        ),
        work_item_id=_expect_optional_text(
            row,
            7,
            "governance_events.work_item_id",
            allow_empty=False,
        ),
        run_id=_expect_optional_text(
            row,
            8,
            "governance_events.run_id",
            allow_empty=False,
        ),
        action_id=_expect_optional_text(
            row,
            9,
            "governance_events.action_id",
            allow_empty=False,
        ),
        authority_source=_expect_optional_text(
            row,
            10,
            "governance_events.authority_source",
        ),
        refusal_reason=_expect_optional_text(
            row,
            11,
            "governance_events.refusal_reason",
        ),
        created_at_order=_expect_nonnegative_int(
            row,
            12,
            "governance_events.created_at_order",
        ),
    )


def encode_trace_row(
    trace: TraceRecord,
    *,
    transition_order: int,
    created_at_order: int,
) -> TraceRow:
    return TraceRow(
        record_id=trace.record_id,
        transition_order=transition_order,
        input_id=trace.input_id,
        input_kind=trace.input_kind,
        input_family=trace.input_family,
        disposition=trace.disposition,
        plan_fingerprint=trace.plan_fingerprint,
        work_item_id=trace.work_item_id,
        run_id=trace.run_id,
        action_id=str(trace.action_id) if trace.action_id is not None else None,
        authority_source=trace.authority_source,
        refusal_reason=trace.refusal_reason,
        created_at_order=created_at_order,
    )


def decode_trace_row(row: tuple[object, ...]) -> TraceRow:
    return TraceRow(
        record_id=_expect_text(row, 0, "traces.record_id"),
        transition_order=_expect_nonnegative_int(
            row,
            1,
            "traces.transition_order",
        ),
        input_id=_expect_text(row, 2, "traces.input_id"),
        input_kind=_expect_text(row, 3, "traces.input_kind"),
        input_family=_expect_text(row, 4, "traces.input_family"),
        disposition=_expect_text(row, 5, "traces.disposition"),
        plan_fingerprint=_expect_optional_text(
            row,
            6,
            "traces.plan_fingerprint",
            allow_empty=False,
        ),
        work_item_id=_expect_optional_text(
            row,
            7,
            "traces.work_item_id",
            allow_empty=False,
        ),
        run_id=_expect_optional_text(row, 8, "traces.run_id", allow_empty=False),
        action_id=_expect_optional_text(
            row,
            9,
            "traces.action_id",
            allow_empty=False,
        ),
        authority_source=_expect_optional_text(row, 10, "traces.authority_source"),
        refusal_reason=_expect_optional_text(row, 11, "traces.refusal_reason"),
        created_at_order=_expect_nonnegative_int(
            row,
            12,
            "traces.created_at_order",
        ),
    )


def encode_refusal_row(
    refusal: TransitionRefusal,
    *,
    transition_order: int,
    created_at_order: int,
) -> RefusalRow:
    return RefusalRow(
        record_id=refusal.record_id,
        transition_order=transition_order,
        input_id=refusal.input_id,
        input_kind=refusal.input_kind,
        input_family=refusal.input_family,
        reason=refusal.reason,
        detail=refusal.detail,
        created_at_order=created_at_order,
    )


def decode_refusal_row(row: tuple[object, ...]) -> RefusalRow:
    return RefusalRow(
        record_id=_expect_text(row, 0, "refusals.record_id"),
        transition_order=_expect_nonnegative_int(
            row,
            1,
            "refusals.transition_order",
        ),
        input_id=_expect_text(row, 2, "refusals.input_id"),
        input_kind=_expect_text(row, 3, "refusals.input_kind"),
        input_family=_expect_text(row, 4, "refusals.input_family"),
        reason=_expect_text(row, 5, "refusals.reason"),
        detail=_expect_optional_text(row, 6, "refusals.detail"),
        created_at_order=_expect_nonnegative_int(
            row,
            7,
            "refusals.created_at_order",
        ),
    )


def plan_ref_from_work_item_row(row: WorkItemRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_activation_row(row: ActivationRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_run_row(row: RunRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_effect_proposal_row(row: EffectProposalRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_effect_reconciliation_row(
    row: EffectReconciliationRow,
) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_recovery_attempt_row(row: RecoveryAttemptRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_lineage_quarantine_row(row: LineageQuarantineRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_cooldown_wait_row(row: CooldownWaitRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_counter_row(row: CounterRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_operator_intervention_row(row: OperatorInterventionRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_operator_wait_row(row: OperatorWaitRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_fanout_row(row: FanoutRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_work_dependency_row(row: WorkDependencyRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_closure_target_row(row: ClosureTargetRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_closure_evaluation_row(
    row: ClosureEvaluationRow,
) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_closure_terminal_row(row: ClosureTerminalRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_remediation_work_row(row: RemediationWorkRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def plan_ref_from_closure_blocked_row(row: ClosureBlockedRow) -> PlanRef:
    return PlanRef(
        plan_id=row.plan_id,
        authority_fingerprint=row.plan_authority_fingerprint,
        plan_format_version=row.plan_format_version,
    )


def activation_route_from_row(row: ActivationRouteRow) -> ActivationRouteRecord:
    return ActivationRouteRecord(
        record_id=row.record_id,
        action_id=ActionId(row.action_id),
        source_run_id=row.source_run_id,
        source_work_item_id=row.source_work_item_id,
        target_work_item_id=row.target_work_item_id,
        target_activation_id=row.target_activation_id,
        created_by_input_id=row.created_by_input_id,
    )


def effect_proposal_from_row(row: EffectProposalRow) -> EffectProposalRecord:
    return EffectProposalRecord(
        effect_id=row.effect_id,
        dedupe_key=row.dedupe_key,
        effect_declaration_id=EffectDeclarationId(row.effect_declaration_id),
        selected_plan_ref=plan_ref_from_effect_proposal_row(row),
        selected_plan_fingerprint=row.selected_plan_fingerprint,
        terminal_action_id=ActionId(row.terminal_action_id),
        artifact_id=row.artifact_id,
        artifact_schema_id=ArtifactSchemaId(row.artifact_schema_id),
        artifact_payload_digest=row.artifact_payload_digest,
        source_run_id=row.source_run_id,
        source_action_id=ActionId(row.source_action_id),
        source_input_id=row.source_input_id,
        source_work_item_id=row.source_work_item_id,
        source_activation_id=row.source_activation_id,
        source_graph_node_id=row.source_graph_node_id,
        source_stage_kind_id=StageKindId(row.source_stage_kind_id),
        source_runner_binding_id=RunnerBindingId(row.source_runner_binding_id),
        source_queue_family_id=QueueFamilyId(row.source_queue_family_id),
        lineage_id=row.lineage_id,
        provider_ref=row.provider_ref,
        capability_policy_ref=row.capability_policy_ref,
        target_ref_kind=row.target_ref_kind,
        target_ref_schema=row.target_ref_schema,
        target_skill_id=row.target_skill_id,
        target_path_ref=row.target_path_ref,
        status=row.status,
        created_input_id=row.created_input_id,
        created_transition_id=row.created_transition_id,
    )


def effect_reconciliation_from_row(
    row: EffectReconciliationRow,
) -> EffectReconciliationRecord:
    return EffectReconciliationRecord(
        reconciliation_id=row.reconciliation_id,
        effect_id=row.effect_id,
        selected_plan_ref=plan_ref_from_effect_reconciliation_row(row),
        selected_plan_fingerprint=row.selected_plan_fingerprint,
        provider_ref=row.provider_ref,
        status=row.status,
        fake_local_result_digest=row.fake_local_result_digest,
        created_input_id=row.created_input_id,
        created_transition_id=row.created_transition_id,
    )


def fanout_from_row(row: FanoutRow) -> FanoutRecord:
    return FanoutRecord(
        record_id=row.record_id,
        fanout_id=FanoutId(row.fanout_id),
        source_artifact_id=row.source_artifact_id,
        source_artifact_digest=row.source_artifact_digest,
        source_work_item_id=row.source_work_item_id,
        source_run_id=row.source_run_id,
        source_action_id=ActionId(row.source_action_id),
        target_work_item_id=row.target_work_item_id,
        target_activation_id=row.target_activation_id,
        target_queue_family_id=QueueFamilyId(row.target_queue_family_id),
        target_stage_kind_id=StageKindId(row.target_stage_kind_id),
        target_graph_node_id=row.target_graph_node_id,
        item_key=row.item_key,
        lineage_id=row.lineage_id,
        selected_plan_ref=plan_ref_from_fanout_row(row),
        created_by_input_id=row.created_by_input_id,
    )


def work_dependency_from_row(row: WorkDependencyRow) -> WorkDependencyRecord:
    return WorkDependencyRecord(
        dependency_id=row.dependency_id,
        dependent_work_item_id=row.dependent_work_item_id,
        dependency_work_item_id=row.dependency_work_item_id,
        selected_plan_ref=plan_ref_from_work_dependency_row(row),
        lineage_id=row.lineage_id,
        fanout_record_id=row.fanout_record_id,
        created_by_input_id=row.created_by_input_id,
    )


def closure_target_from_row(
    row: ClosureTargetRow,
    *,
    evidence_window: Mapping[str, AuthorityValue],
) -> ClosureTargetRecord:
    return ClosureTargetRecord(
        closure_target_id=row.closure_target_id,
        selected_plan_ref=plan_ref_from_closure_target_row(row),
        completion_behavior_id=CompletionBehaviorId(row.completion_behavior_id),
        lineage_id=row.lineage_id,
        root_source_kind=row.root_source_kind,
        root_source_id=row.root_source_id,
        closure_root_work_item_id=row.closure_root_work_item_id,
        request_kind=row.request_kind,
        target_graph_node_id=row.target_graph_node_id,
        evidence_window=evidence_window,
        status=row.status,
        opened_by_input_id=row.opened_by_input_id,
        closed_by_record_id=row.closed_by_record_id,
    )


def closure_evaluation_from_row(
    row: ClosureEvaluationRow,
) -> ClosureEvaluationRecord:
    return ClosureEvaluationRecord(
        record_id=row.record_id,
        closure_target_id=row.closure_target_id,
        completion_behavior_id=CompletionBehaviorId(row.completion_behavior_id),
        request_kind=row.request_kind,
        target_work_item_id=row.target_work_item_id,
        target_activation_id=row.target_activation_id,
        selected_plan_ref=plan_ref_from_closure_evaluation_row(row),
        lineage_id=row.lineage_id,
        created_by_input_id=row.created_by_input_id,
    )


def closure_terminal_from_row(row: ClosureTerminalRow) -> ClosureTerminalRecord:
    return ClosureTerminalRecord(
        record_id=row.record_id,
        closure_target_id=row.closure_target_id,
        completion_behavior_id=CompletionBehaviorId(row.completion_behavior_id),
        terminal_kind=row.terminal_kind,
        source_run_id=row.source_run_id,
        source_action_id=ActionId(row.source_action_id),
        source_artifact_id=row.source_artifact_id,
        selected_plan_ref=plan_ref_from_closure_terminal_row(row),
        lineage_id=row.lineage_id,
        created_by_input_id=row.created_by_input_id,
    )


def remediation_work_from_row(
    row: RemediationWorkRow,
) -> RemediationWorkRecord:
    return RemediationWorkRecord(
        record_id=row.record_id,
        remediation_policy_id=RemediationPolicyId(row.remediation_policy_id),
        closure_target_id=row.closure_target_id,
        source_run_id=row.source_run_id,
        source_action_id=ActionId(row.source_action_id),
        source_artifact_id=row.source_artifact_id,
        target_work_item_id=row.target_work_item_id,
        target_activation_id=row.target_activation_id,
        selected_plan_ref=plan_ref_from_remediation_work_row(row),
        lineage_id=row.lineage_id,
        dedupe_key=row.dedupe_key,
        created_by_input_id=row.created_by_input_id,
    )


def closure_blocked_from_row(row: ClosureBlockedRow) -> ClosureBlockedRecord:
    return ClosureBlockedRecord(
        record_id=row.record_id,
        closure_target_id=row.closure_target_id,
        completion_behavior_id=CompletionBehaviorId(row.completion_behavior_id),
        source_run_id=row.source_run_id,
        source_action_id=ActionId(row.source_action_id),
        selected_plan_ref=plan_ref_from_closure_blocked_row(row),
        lineage_id=row.lineage_id,
        operator_required=bool(row.operator_required),
        created_by_input_id=row.created_by_input_id,
    )


def closed_work_item_from_row(row: ClosedWorkItemRow) -> ClosedWorkItemRecord:
    return ClosedWorkItemRecord(
        record_id=row.record_id,
        work_item_id=row.work_item_id,
        source_run_id=row.source_run_id,
        action_id=ActionId(row.action_id) if row.action_id is not None else None,
        created_by_input_id=row.created_by_input_id,
        operator_intervention_record_id=row.operator_intervention_record_id,
        close_kind=row.close_kind,
    )


def pause_from_row(row: PauseStateRow) -> PauseRecord:
    return PauseRecord(
        record_id=row.record_id,
        source_run_id=row.source_run_id,
        work_item_id=row.work_item_id,
        action_id=ActionId(row.action_id),
        created_by_input_id=row.created_by_input_id,
    )


def quarantine_from_row(row: QuarantineRow) -> QuarantineRecord:
    return QuarantineRecord(
        record_id=row.record_id,
        work_item_id=row.work_item_id,
        source_run_id=row.source_run_id,
        action_id=ActionId(row.action_id),
        created_by_input_id=row.created_by_input_id,
    )


def lineage_quarantine_from_row(
    row: LineageQuarantineRow,
) -> LineageQuarantineRecord:
    return LineageQuarantineRecord(
        quarantine_id=row.quarantine_id,
        policy_id=RecoveryPolicyId(row.policy_id),
        lineage_id=row.lineage_id,
        selected_plan_ref=plan_ref_from_lineage_quarantine_row(row),
        selected_plan_fingerprint=row.plan_authority_fingerprint,
        recovery_attempt_record_id=row.recovery_attempt_record_id,
        original_source_run_id=row.original_source_run_id,
        original_source_work_item_id=row.original_source_work_item_id,
        original_source_activation_id=row.original_source_activation_id,
        emitting_recovery_activation_id=row.emitting_recovery_activation_id,
        emitting_recovery_run_id=row.emitting_recovery_run_id,
        action_id=ActionId(row.action_id),
        attempt_count=row.attempt_count,
        created_input_id=row.created_input_id,
        actor_kind=row.actor_kind,
        status=row.status,
        superseded_input_id=row.superseded_input_id,
    )


def recovery_attempt_from_row(row: RecoveryAttemptRow) -> RecoveryAttemptRecord:
    return RecoveryAttemptRecord(
        record_id=row.record_id,
        policy_id=RecoveryPolicyId(row.policy_id),
        lineage_id=row.lineage_id,
        plan_ref=plan_ref_from_recovery_attempt_row(row),
        attempt_count=row.attempt_count,
        phase=row.phase,
        source_run_id=row.source_run_id,
        source_work_item_id=row.source_work_item_id,
        source_activation_id=row.source_activation_id,
        source_graph_node_id=row.source_graph_node_id,
        source_stage_kind_id=StageKindId(row.source_stage_kind_id),
        source_runner_binding_id=RunnerBindingId(row.source_runner_binding_id),
        source_queue_family_id=QueueFamilyId(row.source_queue_family_id),
        recovery_action_id=ActionId(row.recovery_action_id),
        latest_recovery_activation_id=row.latest_recovery_activation_id,
        latest_recovery_run_id=row.latest_recovery_run_id,
        latest_return_action_id=(
            ActionId(row.latest_return_action_id)
            if row.latest_return_action_id is not None
            else None
        ),
        created_by_input_id=row.created_by_input_id,
        updated_by_input_id=row.updated_by_input_id,
    )


def cooldown_wait_from_row(row: CooldownWaitRow) -> CooldownWaitRecord:
    return CooldownWaitRecord(
        wait_id=row.wait_id,
        policy_id=RecoveryPolicyId(row.policy_id),
        lineage_id=row.lineage_id,
        recovery_attempt_record_id=row.recovery_attempt_record_id,
        attempt_count=row.attempt_count,
        source_run_id=row.source_run_id,
        source_work_item_id=row.source_work_item_id,
        source_activation_id=row.source_activation_id,
        recovery_action_id=ActionId(row.recovery_action_id),
        target_stage_kind_id=StageKindId(row.target_stage_kind_id),
        target_graph_node_id=row.target_graph_node_id,
        target_runner_binding_id=RunnerBindingId(row.target_runner_binding_id),
        plan_ref=plan_ref_from_cooldown_wait_row(row),
        created_input_id=row.created_input_id,
        created_at=row.created_at,
        due_at=row.due_at,
        consumed_input_id=row.consumed_input_id,
        consumed_at=row.consumed_at,
        resulting_recovery_activation_id=row.resulting_recovery_activation_id,
    )


def counter_from_row(row: CounterRow) -> CounterRecord:
    return CounterRecord(
        record_id=row.record_id,
        counter_id=CounterId(row.counter_id),
        selected_plan_ref=plan_ref_from_counter_row(row),
        lineage_id=row.lineage_id,
        value=row.value,
        updated_by_input_id=row.updated_by_input_id,
    )


def operator_intervention_from_row(
    row: OperatorInterventionRow,
) -> OperatorInterventionRecord:
    return OperatorInterventionRecord(
        record_id=row.record_id,
        created_by_input_id=row.created_by_input_id,
        input_payload_digest=row.input_payload_digest,
        option_id=row.option_id,
        kind=row.kind,
        result=row.result,
        policy_id=RecoveryPolicyId(row.policy_id),
        lineage_id=row.lineage_id,
        quarantine_id=row.quarantine_id,
        recovery_attempt_record_id=row.recovery_attempt_record_id,
        recovery_attempt_count=row.recovery_attempt_count,
        attempt_effect=row.attempt_effect,
        selected_plan_ref=plan_ref_from_operator_intervention_row(row),
        selected_plan_fingerprint=row.plan_authority_fingerprint,
        actor_kind=row.actor_kind,
        actor_id=row.actor_id,
        reason=row.reason,
        target_work_item_id=row.target_work_item_id,
        target_activation_id=row.target_activation_id,
        closed_work_item_ids=_json_string_tuple_to_tuple(
            row.closed_work_item_ids_json
        ),
        closed_activation_ids=_json_string_tuple_to_tuple(
            row.closed_activation_ids_json
        ),
        closed_run_ids=_json_string_tuple_to_tuple(row.closed_run_ids_json),
        payload_digest=row.payload_digest,
        payload_reference=row.payload_reference,
    )


def operator_wait_from_row(row: OperatorWaitRow) -> OperatorWaitRecord:
    return OperatorWaitRecord(
        wait_id=row.wait_id,
        operator_wait_id=OperatorWaitId(row.operator_wait_id),
        source_action_id=ActionId(row.source_action_id),
        lineage_id=row.lineage_id,
        selected_plan_ref=plan_ref_from_operator_wait_row(row),
        selected_plan_fingerprint=row.plan_authority_fingerprint,
        source_work_item_id=row.source_work_item_id,
        source_activation_id=row.source_activation_id,
        source_run_id=row.source_run_id,
        source_stage_kind_id=StageKindId(row.source_stage_kind_id),
        source_graph_node_id=row.source_graph_node_id,
        source_queue_family_id=QueueFamilyId(row.source_queue_family_id),
        source_runner_binding_id=RunnerBindingId(row.source_runner_binding_id),
        source_artifact_id=row.source_artifact_id,
        status=row.status,
        created_input_id=row.created_input_id,
        created_input_payload_digest=row.created_input_payload_digest,
        resolved_input_id=row.resolved_input_id,
        resolved_input_payload_digest=row.resolved_input_payload_digest,
        actor_id=row.actor_id,
        actor_kind=row.actor_kind,
        resolution_kind=row.resolution_kind,
        target_work_item_id=row.target_work_item_id,
        target_activation_id=row.target_activation_id,
        closed_work_item_ids=(
            _json_string_tuple_to_tuple(row.closed_work_item_ids_json)
            if row.closed_work_item_ids_json is not None
            else ()
        ),
        payload_digest=row.payload_digest,
        payload_reference=row.payload_reference,
    )


def _json_string_tuple_to_tuple(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    return tuple(str(item) for item in parsed)


def artifact_schema_id_from_row(row: ArtifactRow) -> ArtifactSchemaId:
    return ArtifactSchemaId(row.artifact_schema_id)


def artifact_source_action_id_from_row(row: ArtifactRow) -> ActionId:
    return ActionId(row.source_action_id)


def artifact_source_stage_kind_id_from_row(row: ArtifactRow) -> StageKindId:
    return StageKindId(row.source_stage_kind_id)


def transition_from_row(row: TransitionRow) -> TransitionRecord:
    return TransitionRecord(
        record_id=row.record_id,
        input_id=row.input_id,
        input_kind=row.input_kind,
        input_family=row.input_family,
        accepted=bool(row.accepted),
    )


def governance_event_from_row(row: GovernanceEventRow) -> GovernanceEventRecord:
    return GovernanceEventRecord(
        record_id=row.record_id,
        input_id=row.input_id,
        input_kind=row.input_kind,
        input_family=row.input_family,
        disposition=row.disposition,
        plan_fingerprint=row.plan_fingerprint,
        work_item_id=row.work_item_id,
        run_id=row.run_id,
        action_id=ActionId(row.action_id) if row.action_id is not None else None,
        authority_source=row.authority_source,
        refusal_reason=row.refusal_reason,
    )


def trace_from_row(row: TraceRow) -> TraceRecord:
    return TraceRecord(
        record_id=row.record_id,
        input_id=row.input_id,
        input_kind=row.input_kind,
        input_family=row.input_family,
        disposition=row.disposition,
        plan_fingerprint=row.plan_fingerprint,
        work_item_id=row.work_item_id,
        run_id=row.run_id,
        action_id=ActionId(row.action_id) if row.action_id is not None else None,
        authority_source=row.authority_source,
        refusal_reason=row.refusal_reason,
    )


def refusal_from_row(row: RefusalRow) -> TransitionRefusal:
    return TransitionRefusal(
        record_id=row.record_id,
        input_id=row.input_id,
        input_kind=row.input_kind,
        input_family=row.input_family,
        reason=row.reason,
        detail=row.detail,
    )
