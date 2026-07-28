"""SQLite runtime state write orchestration."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from millrace.contracts.state import RuntimeState
from millrace.substrate._sqlite_relations import (
    runner_session_cas_references,
    validate_audit_transition_rows,
    validate_completed_runner_evidence,
    validate_loaded_runtime_state,
    validate_receipt_transition_rows,
    validate_trace_governance_rows,
    validate_transition_rows,
)
from millrace.substrate._sqlite_rows import (
    ActivationRouteRow,
    ActivationRow,
    AdmittedPlanPinRow,
    ArtifactRow,
    ClosedWorkItemRow,
    ClosureBlockedRow,
    ClosureEvaluationRow,
    ClosureTargetRow,
    ClosureTerminalRow,
    CooldownWaitRow,
    CounterRow,
    DefaultPlanRow,
    EffectProposalRow,
    EffectReconciliationRow,
    FanoutRow,
    GovernanceEventRow,
    InputReceiptRow,
    LineageQuarantineRow,
    OperatorInterventionRow,
    OperatorWaitRow,
    PauseStateRow,
    QuarantineRow,
    RecoveryAttemptRow,
    RefusalRow,
    RemediationWorkRow,
    RunnerObservationRow,
    RunnerSessionCancellationAttemptRow,
    RunnerSessionCancellationRow,
    RunnerSessionCompletionRow,
    RunnerSessionRow,
    RunRow,
    TraceRow,
    TransitionRow,
    WorkDependencyRow,
    WorkItemRow,
    encode_activation_route_row,
    encode_activation_row,
    encode_admitted_plan_pin_row,
    encode_artifact_row,
    encode_closed_work_item_row,
    encode_closure_blocked_row,
    encode_closure_evaluation_row,
    encode_closure_target_row,
    encode_closure_terminal_row,
    encode_cooldown_wait_row,
    encode_counter_row,
    encode_default_plan_row,
    encode_effect_proposal_row,
    encode_effect_reconciliation_row,
    encode_fanout_row,
    encode_governance_event_row,
    encode_input_receipt_row,
    encode_lineage_quarantine_row,
    encode_operator_intervention_row,
    encode_operator_wait_row,
    encode_pause_state_row,
    encode_quarantine_row,
    encode_recovery_attempt_row,
    encode_refusal_row,
    encode_remediation_work_row,
    encode_run_row,
    encode_runner_observation_row,
    encode_runner_session_cancellation_attempt_row,
    encode_runner_session_cancellation_row,
    encode_runner_session_completion_row,
    encode_runner_session_row,
    encode_trace_row,
    encode_transition_row,
    encode_work_dependency_row,
    encode_work_item_row,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.codecs import (
    dumps_cas_object,
    encode_payload,
    encode_selected_compiled_plan,
)
from millrace.substrate.errors import StorageIntegrityError, SubstrateError
from millrace.substrate.records import ARTIFACT_PAYLOAD_OBJECT_KIND


def persist_runtime_state_rows(
    connection: sqlite3.Connection,
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
    *,
    _before_sqlite_commit: Callable[[], None] | None = None,
) -> None:
    _validate_runner_session_cas_references(state, cas_store)
    selected_plan_digests = _put_selected_plan_objects(state, cas_store)
    payload_digests = _put_work_item_payload_objects(state, cas_store)
    observation_payload_digests = _put_runner_observation_payload_objects(
        state,
        cas_store,
    )
    artifact_payload_digests = _put_artifact_payload_objects(state, cas_store)
    closure_evidence_window_digests = _put_closure_evidence_window_objects(
        state,
        cas_store,
    )
    admitted_plan_rows = tuple(
        encode_admitted_plan_pin_row(
            admitted_plan,
            selected_plan_digest=selected_plan_digests[
                admitted_plan.plan_ref.authority_fingerprint
            ],
            admitted_at_order=order,
        )
        for order, admitted_plan in enumerate(state.admitted_plans.values())
    )
    default_plan_row = encode_default_plan_row(
        state.default_plan_ref,
        selected_plan_digests,
    )
    receipt_rows = tuple(
        encode_input_receipt_row(receipt, received_at_order=order)
        for order, receipt in enumerate(state.receipts.values())
    )
    work_item_rows = tuple(
        encode_work_item_row(
            work_item,
            payload_digest=payload_digests[work_item.ref.work_item_id],
            created_at_order=order,
        )
        for order, work_item in enumerate(state.work_items.values())
    )
    activation_rows = tuple(
        encode_activation_row(activation, created_at_order=order)
        for order, activation in enumerate(state.activations.values())
    )
    run_rows = tuple(
        encode_run_row(run, started_at_order=order)
        for order, run in enumerate(state.runs.values())
    )
    runner_session_rows = tuple(
        encode_runner_session_row(session)
        for session in state.runner_sessions.values()
    )
    runner_session_cancellation_rows = tuple(
        encode_runner_session_cancellation_row(record)
        for record in state.runner_session_cancellation_requests.values()
    )
    runner_session_cancellation_attempt_rows = tuple(
        encode_runner_session_cancellation_attempt_row(record)
        for record in state.runner_session_cancellation_attempts.values()
    )
    runner_session_completion_rows = tuple(
        encode_runner_session_completion_row(record)
        for record in state.runner_session_completions.values()
    )
    observation_rows = tuple(
        encode_runner_observation_row(
            observation,
            payload_digest=observation_payload_digests[observation.observation_id],
            observed_at_order=order,
        )
        for order, observation in enumerate(state.runner_observations.values())
    )
    artifact_rows = tuple(
        encode_artifact_row(
            artifact,
            payload_digest=artifact_payload_digests[artifact.artifact_id],
            created_at_order=order,
        )
        for order, artifact in enumerate(state.artifacts.values())
    )
    effect_proposal_rows = tuple(
        encode_effect_proposal_row(record, created_at_order=order)
        for order, record in enumerate(state.effect_proposals.values())
    )
    effect_reconciliation_rows = tuple(
        encode_effect_reconciliation_row(record, created_at_order=order)
        for order, record in enumerate(state.effect_reconciliations.values())
    )
    activation_route_rows = tuple(
        encode_activation_route_row(route, created_at_order=order)
        for order, route in enumerate(state.activation_routes)
    )
    fanout_rows = tuple(
        encode_fanout_row(record, created_at_order=order)
        for order, record in enumerate(state.fanout_records.values())
    )
    work_dependency_rows = tuple(
        encode_work_dependency_row(record, created_at_order=order)
        for order, record in enumerate(state.work_dependencies.values())
    )
    closure_target_rows = tuple(
        encode_closure_target_row(
            record,
            evidence_window_digest=closure_evidence_window_digests[
                record.closure_target_id
            ],
            created_at_order=order,
        )
        for order, record in enumerate(state.closure_targets.values())
    )
    closure_evaluation_rows = tuple(
        encode_closure_evaluation_row(record, created_at_order=order)
        for order, record in enumerate(state.closure_evaluations.values())
    )
    closure_terminal_rows = tuple(
        encode_closure_terminal_row(record, created_at_order=order)
        for order, record in enumerate(state.closure_terminal_records.values())
    )
    remediation_work_rows = tuple(
        encode_remediation_work_row(record, created_at_order=order)
        for order, record in enumerate(state.remediation_work_records.values())
    )
    closure_blocked_rows = tuple(
        encode_closure_blocked_row(record, created_at_order=order)
        for order, record in enumerate(state.closure_blocked_records.values())
    )
    closed_work_item_rows = tuple(
        encode_closed_work_item_row(record, closed_at_order=order)
        for order, record in enumerate(state.closed_work_items.values())
    )
    pause_state_row = encode_pause_state_row(state.pause)
    quarantine_rows = tuple(
        encode_quarantine_row(record, created_at_order=order)
        for order, record in enumerate(state.quarantines.values())
    )
    lineage_quarantine_rows = tuple(
        encode_lineage_quarantine_row(record, created_at_order=order)
        for order, record in enumerate(state.lineage_quarantines.values())
    )
    recovery_attempt_rows = tuple(
        encode_recovery_attempt_row(attempt, updated_at_order=order)
        for order, attempt in enumerate(state.recovery_attempts.values())
    )
    operator_intervention_rows = tuple(
        encode_operator_intervention_row(record, created_at_order=order)
        for order, record in enumerate(state.operator_interventions.values())
    )
    operator_wait_rows = tuple(
        encode_operator_wait_row(record, created_at_order=order)
        for order, record in enumerate(state.operator_waits.values())
    )
    cooldown_wait_rows = tuple(
        encode_cooldown_wait_row(wait, updated_at_order=order)
        for order, wait in enumerate(state.cooldown_waits.values())
    )
    counter_rows = tuple(
        encode_counter_row(record, updated_at_order=order)
        for order, record in enumerate(state.counters.values())
    )
    transition_rows = tuple(
        encode_transition_row(record, transition_order=order)
        for order, record in enumerate(state.transitions)
    )
    transition_order_by_record_id = {
        row.record_id: row.transition_order for row in transition_rows
    }
    governance_event_rows = tuple(
        encode_governance_event_row(
            event,
            transition_order=_audit_record_transition_order(
                event.record_id,
                fallback_order=order,
                transition_order_by_record_id=transition_order_by_record_id,
            ),
            created_at_order=order,
        )
        for order, event in enumerate(state.governance_events)
    )
    trace_rows = tuple(
        encode_trace_row(
            trace,
            transition_order=_audit_record_transition_order(
                trace.record_id,
                fallback_order=order,
                transition_order_by_record_id=transition_order_by_record_id,
            ),
            created_at_order=order,
        )
        for order, trace in enumerate(state.traces)
    )
    refusal_rows = tuple(
        encode_refusal_row(
            refusal,
            transition_order=_audit_record_transition_order(
                refusal.record_id,
                fallback_order=order,
                transition_order_by_record_id=transition_order_by_record_id,
            ),
            created_at_order=order,
        )
        for order, refusal in enumerate(state.refusals)
    )

    if connection.in_transaction:
        _replace_runtime_rows(
            connection,
            state=state,
            cas_store=cas_store,
            admitted_plan_rows=admitted_plan_rows,
            default_plan_row=default_plan_row,
            receipt_rows=receipt_rows,
            work_item_rows=work_item_rows,
            activation_rows=activation_rows,
            run_rows=run_rows,
            runner_session_rows=runner_session_rows,
            runner_session_cancellation_rows=runner_session_cancellation_rows,
            runner_session_cancellation_attempt_rows=(
                runner_session_cancellation_attempt_rows
            ),
            runner_session_completion_rows=runner_session_completion_rows,
            observation_rows=observation_rows,
            artifact_rows=artifact_rows,
            effect_proposal_rows=effect_proposal_rows,
            effect_reconciliation_rows=effect_reconciliation_rows,
            activation_route_rows=activation_route_rows,
            fanout_rows=fanout_rows,
            work_dependency_rows=work_dependency_rows,
            closure_target_rows=closure_target_rows,
            closure_evaluation_rows=closure_evaluation_rows,
            closure_terminal_rows=closure_terminal_rows,
            remediation_work_rows=remediation_work_rows,
            closure_blocked_rows=closure_blocked_rows,
            closed_work_item_rows=closed_work_item_rows,
            pause_state_row=pause_state_row,
            quarantine_rows=quarantine_rows,
            lineage_quarantine_rows=lineage_quarantine_rows,
            recovery_attempt_rows=recovery_attempt_rows,
            operator_intervention_rows=operator_intervention_rows,
            operator_wait_rows=operator_wait_rows,
            cooldown_wait_rows=cooldown_wait_rows,
            counter_rows=counter_rows,
            transition_rows=transition_rows,
            governance_event_rows=governance_event_rows,
            trace_rows=trace_rows,
            refusal_rows=refusal_rows,
        )
        if _before_sqlite_commit is not None:
            _before_sqlite_commit()
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        _replace_runtime_rows(
            connection,
            state=state,
            cas_store=cas_store,
            admitted_plan_rows=admitted_plan_rows,
            default_plan_row=default_plan_row,
            receipt_rows=receipt_rows,
            work_item_rows=work_item_rows,
            activation_rows=activation_rows,
            run_rows=run_rows,
            runner_session_rows=runner_session_rows,
            runner_session_cancellation_rows=runner_session_cancellation_rows,
            runner_session_cancellation_attempt_rows=(
                runner_session_cancellation_attempt_rows
            ),
            runner_session_completion_rows=runner_session_completion_rows,
            observation_rows=observation_rows,
            artifact_rows=artifact_rows,
            effect_proposal_rows=effect_proposal_rows,
            effect_reconciliation_rows=effect_reconciliation_rows,
            activation_route_rows=activation_route_rows,
            fanout_rows=fanout_rows,
            work_dependency_rows=work_dependency_rows,
            closure_target_rows=closure_target_rows,
            closure_evaluation_rows=closure_evaluation_rows,
            closure_terminal_rows=closure_terminal_rows,
            remediation_work_rows=remediation_work_rows,
            closure_blocked_rows=closure_blocked_rows,
            closed_work_item_rows=closed_work_item_rows,
            pause_state_row=pause_state_row,
            quarantine_rows=quarantine_rows,
            lineage_quarantine_rows=lineage_quarantine_rows,
            recovery_attempt_rows=recovery_attempt_rows,
            operator_intervention_rows=operator_intervention_rows,
            operator_wait_rows=operator_wait_rows,
            cooldown_wait_rows=cooldown_wait_rows,
            counter_rows=counter_rows,
            transition_rows=transition_rows,
            governance_event_rows=governance_event_rows,
            trace_rows=trace_rows,
            refusal_rows=refusal_rows,
        )
        if _before_sqlite_commit is not None:
            _before_sqlite_commit()
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def _put_selected_plan_objects(
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
) -> dict[str, str]:
    return {
        admitted_plan.plan_ref.authority_fingerprint: cas_store.put_bytes(
            dumps_cas_object(
                encode_selected_compiled_plan(admitted_plan.selected_plan),
            )
        )
        for admitted_plan in state.admitted_plans.values()
    }


def _validate_runner_session_cas_references(
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
) -> None:
    for reference_name, digest in runner_session_cas_references(state):
        try:
            cas_store.get_bytes(digest)
        except SubstrateError as exc:
            raise StorageIntegrityError(
                f"runner session {reference_name} CAS reference is invalid: {digest}"
            ) from exc


def _validate_runner_session_evidence_authority(
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
) -> None:
    for completion in state.runner_session_completions.values():
        evidence_digest = completion.runner_result_evidence_digest
        if evidence_digest is not None:
            validate_completed_runner_evidence(
                state,
                session_id=completion.session_id,
                payload=cas_store.get_bytes(evidence_digest),
            )


def _put_work_item_payload_objects(
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
) -> dict[str, str]:
    return {
        work_item.ref.work_item_id: cas_store.put_bytes(
            dumps_cas_object(encode_payload(work_item.payload))
        )
        for work_item in state.work_items.values()
    }


def _put_runner_observation_payload_objects(
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
) -> dict[str, str]:
    return {
        observation.observation_id: cas_store.put_bytes(
            dumps_cas_object(encode_payload(observation.payload))
        )
        for observation in state.runner_observations.values()
    }


def _put_artifact_payload_objects(
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
) -> dict[str, str]:
    return {
        artifact.artifact_id: cas_store.put_bytes(
            dumps_cas_object(
                encode_payload(
                    artifact.payload,
                    object_kind=ARTIFACT_PAYLOAD_OBJECT_KIND,
                )
            )
        )
        for artifact in state.artifacts.values()
    }


def _put_closure_evidence_window_objects(
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
) -> dict[str, str]:
    return {
        target.closure_target_id: cas_store.put_bytes(
            dumps_cas_object(encode_payload(target.evidence_window))
        )
        for target in state.closure_targets.values()
    }


def _audit_record_transition_order(
    record_id: str,
    *,
    fallback_order: int,
    transition_order_by_record_id: dict[str, int],
) -> int:
    transition_record_id, separator, _suffix = record_id.rpartition(":")
    if not separator:
        return fallback_order
    return transition_order_by_record_id.get(transition_record_id, fallback_order)


def _refuse_stale_or_divergent_transition_history(
    connection: sqlite3.Connection,
    candidate_rows: tuple[TransitionRow, ...],
) -> None:
    durable_signature = _durable_transition_signature(connection)
    candidate_signature = tuple(
        _transition_row_signature(row) for row in candidate_rows
    )
    if not durable_signature:
        return
    if len(candidate_signature) < len(durable_signature):
        raise StorageIntegrityError(
            "stale runtime state would rewind durable transition history"
        )
    if candidate_signature[: len(durable_signature)] != durable_signature:
        raise StorageIntegrityError(
            "stale runtime state diverges from durable transition history"
        )


def _durable_transition_signature(
    connection: sqlite3.Connection,
) -> tuple[tuple[object, ...], ...]:
    rows = connection.execute(
        """
        SELECT
            transition_order,
            record_id,
            input_id,
            input_kind,
            input_family,
            accepted,
            created_at
        FROM transitions
        ORDER BY transition_order
        """
    ).fetchall()
    return tuple(tuple(row) for row in rows)


def _transition_row_signature(row: TransitionRow) -> tuple[object, ...]:
    return (
        row.transition_order,
        row.record_id,
        row.input_id,
        row.input_kind,
        row.input_family,
        row.accepted,
        row.created_at,
    )


_RUNTIME_TABLE_SIGNATURE_COLUMNS = (
    (
        "admitted_plan_pins",
        (
            "authority_fingerprint",
            "plan_id",
            "plan_format_version",
            "selected_plan_digest",
            "admitted_at_order",
        ),
        "admitted_at_order",
    ),
    (
        "default_plan",
        (
            "plan_id",
            "authority_fingerprint",
            "plan_format_version",
            "selected_plan_digest",
            "set_at_order",
        ),
        "id",
    ),
    (
        "input_receipts",
        (
            "input_id",
            "input_payload_digest",
            "transition_id",
            "accepted",
            "refusal_reason",
            "received_at_order",
        ),
        "received_at_order",
    ),
    (
        "work_items",
        (
            "work_item_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "generation",
            "payload_digest",
            "queue_family_id",
            "lineage_id",
            "created_by_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "activations",
        (
            "activation_id",
            "work_item_id",
            "lineage_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "queue_family_id",
            "graph_node_id",
            "stage_kind_id",
            "runner_binding_id",
            "generation",
            "created_by_input_id",
            "claimed_by_run_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "runs",
        (
            "run_id",
            "activation_id",
            "work_item_id",
            "claim_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "generation",
            "fencing_token",
            "stage_kind_id",
            "runner_binding_id",
            "created_by_input_id",
            "current_session_id",
            "last_dispatch_generation",
            "started_at_order",
        ),
        "started_at_order",
    ),
    (
        "runner_sessions",
        (
            "schema_version",
            "session_id",
            "run_id",
            "dispatch_generation",
            "session_fencing_token",
            "state",
            "created_at",
            "start_intent_at",
            "started_at",
            "ended_at",
            "durable_locator_digest",
            "cleanup_disposition",
        ),
        "session_id",
    ),
    (
        "runner_session_cancellation_requests",
        (
            "schema_version",
            "request_id",
            "session_id",
            "dispatch_generation",
            "reason",
            "source_kind",
            "actor_id",
            "requested_at",
            "request_order",
            "primary_request",
        ),
        "request_id",
    ),
    (
        "runner_session_cancellation_attempts",
        (
            "schema_version",
            "attempt_id",
            "session_id",
            "request_id",
            "sequence",
            "operation",
            "result",
            "started_at",
            "completed_at",
            "bounded_diagnostic_digest",
        ),
        "attempt_id",
    ),
    (
        "runner_session_completions",
        (
            "schema_version",
            "completion_id",
            "session_id",
            "run_id",
            "dispatch_generation",
            "session_fencing_token",
            "terminal_state",
            "exit_kind",
            "adapter_outcome_kind",
            "adapter_error_kind",
            "runner_result_evidence_digest",
            "primary_cancellation_request_id",
            "cleanup_disposition",
            "started_at",
            "cancel_requested_at",
            "completed_at",
            "bounds_summary",
            "truncation_metadata",
            "redaction_policy_id",
            "diagnostic_digest",
            "application_input_id",
        ),
        "completion_id",
    ),
    (
        "runner_observations",
        (
            "observation_id",
            "run_id",
            "payload_digest",
            "created_by_input_id",
            "observed_at",
            "observed_at_order",
        ),
        "observed_at_order",
    ),
    (
        "artifacts",
        (
            "artifact_id",
            "work_item_id",
            "artifact_schema_id",
            "payload_digest",
            "created_by_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "effect_proposals",
        (
            "effect_id",
            "dedupe_key",
            "effect_declaration_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "selected_plan_fingerprint",
            "terminal_action_id",
            "artifact_id",
            "artifact_schema_id",
            "artifact_payload_digest",
            "source_run_id",
            "source_action_id",
            "source_input_id",
            "source_work_item_id",
            "source_activation_id",
            "source_graph_node_id",
            "source_stage_kind_id",
            "source_runner_binding_id",
            "source_queue_family_id",
            "lineage_id",
            "provider_ref",
            "capability_policy_ref",
            "target_ref_kind",
            "target_ref_schema",
            "target_skill_id",
            "target_path_ref",
            "status",
            "created_input_id",
            "created_transition_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "effect_reconciliations",
        (
            "reconciliation_id",
            "effect_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "selected_plan_fingerprint",
            "provider_ref",
            "status",
            "fake_local_result_digest",
            "created_input_id",
            "created_transition_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "activation_routes",
        (
            "record_id",
            "action_id",
            "source_run_id",
            "source_work_item_id",
            "target_work_item_id",
            "target_activation_id",
            "created_by_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "fanout_records",
        (
            "record_id",
            "fanout_id",
            "source_artifact_id",
            "source_artifact_digest",
            "source_work_item_id",
            "source_run_id",
            "source_action_id",
            "target_work_item_id",
            "target_activation_id",
            "target_queue_family_id",
            "target_stage_kind_id",
            "target_graph_node_id",
            "item_key",
            "lineage_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "created_by_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "work_dependencies",
        (
            "dependency_id",
            "dependent_work_item_id",
            "dependency_work_item_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "lineage_id",
            "fanout_record_id",
            "created_by_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "closure_targets",
        (
            "closure_target_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "completion_behavior_id",
            "lineage_id",
            "root_source_kind",
            "root_source_id",
            "closure_root_work_item_id",
            "request_kind",
            "target_graph_node_id",
            "evidence_window_digest",
            "status",
            "opened_by_input_id",
            "closed_by_record_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "closure_evaluations",
        (
            "record_id",
            "closure_target_id",
            "completion_behavior_id",
            "request_kind",
            "target_work_item_id",
            "target_activation_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "lineage_id",
            "created_by_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "closure_terminal_records",
        (
            "record_id",
            "closure_target_id",
            "completion_behavior_id",
            "terminal_kind",
            "source_run_id",
            "source_action_id",
            "source_artifact_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "lineage_id",
            "created_by_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "remediation_work_records",
        (
            "record_id",
            "remediation_policy_id",
            "closure_target_id",
            "source_run_id",
            "source_action_id",
            "source_artifact_id",
            "target_work_item_id",
            "target_activation_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "lineage_id",
            "dedupe_key",
            "created_by_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "closure_blocked_records",
        (
            "record_id",
            "closure_target_id",
            "completion_behavior_id",
            "source_run_id",
            "source_action_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "lineage_id",
            "operator_required",
            "created_by_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "closed_work_items",
        (
            "record_id",
            "work_item_id",
            "source_run_id",
            "action_id",
            "operator_intervention_record_id",
            "close_kind",
            "created_by_input_id",
            "closed_at_order",
        ),
        "closed_at_order",
    ),
    (
        "pause_state",
        (
            "record_id",
            "source_run_id",
            "work_item_id",
            "action_id",
            "created_by_input_id",
            "paused_at_order",
        ),
        "id",
    ),
    (
        "quarantine_records",
        (
            "record_id",
            "work_item_id",
            "source_run_id",
            "action_id",
            "created_by_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "lineage_quarantines",
        (
            "quarantine_id",
            "policy_id",
            "lineage_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "recovery_attempt_record_id",
            "original_source_run_id",
            "original_source_work_item_id",
            "original_source_activation_id",
            "emitting_recovery_activation_id",
            "emitting_recovery_run_id",
            "action_id",
            "attempt_count",
            "created_input_id",
            "actor_kind",
            "status",
            "superseded_input_id",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "recovery_attempts",
        (
            "record_id",
            "policy_id",
            "lineage_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "attempt_count",
            "phase",
            "source_run_id",
            "source_work_item_id",
            "source_activation_id",
            "source_graph_node_id",
            "source_stage_kind_id",
            "source_runner_binding_id",
            "source_queue_family_id",
            "recovery_action_id",
            "latest_recovery_activation_id",
            "latest_recovery_run_id",
            "latest_return_action_id",
            "created_by_input_id",
            "updated_by_input_id",
            "updated_at_order",
        ),
        "updated_at_order",
    ),
    (
        "operator_interventions",
        (
            "record_id",
            "created_by_input_id",
            "input_payload_digest",
            "option_id",
            "kind",
            "result",
            "policy_id",
            "lineage_id",
            "quarantine_id",
            "recovery_attempt_record_id",
            "recovery_attempt_count",
            "attempt_effect",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "actor_kind",
            "actor_id",
            "target_work_item_id",
            "target_activation_id",
            "closed_work_item_ids_json",
            "closed_activation_ids_json",
            "closed_run_ids_json",
            "payload_digest",
            "payload_reference",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "operator_waits",
        (
            "wait_id",
            "operator_wait_id",
            "source_action_id",
            "lineage_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "source_work_item_id",
            "source_activation_id",
            "source_run_id",
            "source_stage_kind_id",
            "source_graph_node_id",
            "source_queue_family_id",
            "source_runner_binding_id",
            "source_artifact_id",
            "status",
            "created_input_id",
            "created_input_payload_digest",
            "resolved_input_id",
            "resolved_input_payload_digest",
            "actor_id",
            "actor_kind",
            "resolution_kind",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "cooldown_waits",
        (
            "wait_id",
            "policy_id",
            "lineage_id",
            "recovery_attempt_record_id",
            "attempt_count",
            "source_run_id",
            "source_work_item_id",
            "source_activation_id",
            "recovery_action_id",
            "target_stage_kind_id",
            "target_graph_node_id",
            "target_runner_binding_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "created_input_id",
            "created_at",
            "due_at",
            "consumed_input_id",
            "consumed_at",
            "resulting_recovery_activation_id",
            "updated_at_order",
        ),
        "updated_at_order",
    ),
    (
        "counters",
        (
            "record_id",
            "counter_id",
            "plan_id",
            "plan_authority_fingerprint",
            "plan_format_version",
            "lineage_id",
            "value",
            "updated_by_input_id",
            "updated_at_order",
        ),
        "updated_at_order",
    ),
    (
        "transitions",
        (
            "transition_order",
            "record_id",
            "input_id",
            "input_kind",
            "input_family",
            "accepted",
            "created_at",
        ),
        "transition_order",
    ),
    (
        "governance_events",
        (
            "record_id",
            "transition_order",
            "input_id",
            "input_kind",
            "input_family",
            "disposition",
            "plan_fingerprint",
            "work_item_id",
            "run_id",
            "action_id",
            "authority_source",
            "refusal_reason",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "traces",
        (
            "record_id",
            "transition_order",
            "input_id",
            "input_kind",
            "input_family",
            "disposition",
            "plan_fingerprint",
            "work_item_id",
            "run_id",
            "action_id",
            "authority_source",
            "refusal_reason",
            "created_at_order",
        ),
        "created_at_order",
    ),
    (
        "refusals",
        (
            "record_id",
            "transition_order",
            "input_id",
            "input_kind",
            "input_family",
            "reason",
            "detail",
            "created_at_order",
        ),
        "created_at_order",
    ),
)


def _refuse_same_history_runtime_rewrite(
    connection: sqlite3.Connection,
    *,
    admitted_plan_rows: tuple[AdmittedPlanPinRow, ...],
    default_plan_row: DefaultPlanRow | None,
    receipt_rows: tuple[InputReceiptRow, ...],
    work_item_rows: tuple[WorkItemRow, ...],
    activation_rows: tuple[ActivationRow, ...],
    run_rows: tuple[RunRow, ...],
    runner_session_rows: tuple[RunnerSessionRow, ...],
    runner_session_cancellation_rows: tuple[RunnerSessionCancellationRow, ...],
    runner_session_cancellation_attempt_rows: tuple[
        RunnerSessionCancellationAttemptRow, ...
    ],
    runner_session_completion_rows: tuple[RunnerSessionCompletionRow, ...],
    observation_rows: tuple[RunnerObservationRow, ...],
    artifact_rows: tuple[ArtifactRow, ...],
    effect_proposal_rows: tuple[EffectProposalRow, ...],
    effect_reconciliation_rows: tuple[EffectReconciliationRow, ...],
    activation_route_rows: tuple[ActivationRouteRow, ...],
    fanout_rows: tuple[FanoutRow, ...],
    work_dependency_rows: tuple[WorkDependencyRow, ...],
    closure_target_rows: tuple[ClosureTargetRow, ...],
    closure_evaluation_rows: tuple[ClosureEvaluationRow, ...],
    closure_terminal_rows: tuple[ClosureTerminalRow, ...],
    remediation_work_rows: tuple[RemediationWorkRow, ...],
    closure_blocked_rows: tuple[ClosureBlockedRow, ...],
    closed_work_item_rows: tuple[ClosedWorkItemRow, ...],
    pause_state_row: PauseStateRow | None,
    quarantine_rows: tuple[QuarantineRow, ...],
    lineage_quarantine_rows: tuple[LineageQuarantineRow, ...],
    recovery_attempt_rows: tuple[RecoveryAttemptRow, ...],
    operator_intervention_rows: tuple[OperatorInterventionRow, ...],
    operator_wait_rows: tuple[OperatorWaitRow, ...],
    cooldown_wait_rows: tuple[CooldownWaitRow, ...],
    counter_rows: tuple[CounterRow, ...],
    transition_rows: tuple[TransitionRow, ...],
    governance_event_rows: tuple[GovernanceEventRow, ...],
    trace_rows: tuple[TraceRow, ...],
    refusal_rows: tuple[RefusalRow, ...],
) -> None:
    durable_signature = _durable_transition_signature(connection)
    candidate_signature = tuple(
        _transition_row_signature(row) for row in transition_rows
    )
    if not durable_signature or len(candidate_signature) != len(durable_signature):
        return
    if _durable_runtime_signature(connection) == _candidate_runtime_signature(
        admitted_plan_rows=admitted_plan_rows,
        default_plan_row=default_plan_row,
        receipt_rows=receipt_rows,
        work_item_rows=work_item_rows,
        activation_rows=activation_rows,
        run_rows=run_rows,
        runner_session_rows=runner_session_rows,
        runner_session_cancellation_rows=runner_session_cancellation_rows,
        runner_session_cancellation_attempt_rows=(
            runner_session_cancellation_attempt_rows
        ),
        runner_session_completion_rows=runner_session_completion_rows,
        observation_rows=observation_rows,
        artifact_rows=artifact_rows,
        effect_proposal_rows=effect_proposal_rows,
        effect_reconciliation_rows=effect_reconciliation_rows,
        activation_route_rows=activation_route_rows,
        fanout_rows=fanout_rows,
        work_dependency_rows=work_dependency_rows,
        closure_target_rows=closure_target_rows,
        closure_evaluation_rows=closure_evaluation_rows,
        closure_terminal_rows=closure_terminal_rows,
        remediation_work_rows=remediation_work_rows,
        closure_blocked_rows=closure_blocked_rows,
        closed_work_item_rows=closed_work_item_rows,
        pause_state_row=pause_state_row,
        quarantine_rows=quarantine_rows,
        lineage_quarantine_rows=lineage_quarantine_rows,
        recovery_attempt_rows=recovery_attempt_rows,
        operator_intervention_rows=operator_intervention_rows,
        operator_wait_rows=operator_wait_rows,
        cooldown_wait_rows=cooldown_wait_rows,
        counter_rows=counter_rows,
        transition_rows=transition_rows,
        governance_event_rows=governance_event_rows,
        trace_rows=trace_rows,
        refusal_rows=refusal_rows,
    ):
        return
    raise StorageIntegrityError(
        "stale runtime state would rewrite durable state without new transition"
    )


def _durable_runtime_signature(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    signature: list[tuple[str, tuple[tuple[object, ...], ...]]] = []
    for table_name, columns, order_column in _RUNTIME_TABLE_SIGNATURE_COLUMNS:
        rows = connection.execute(
            f"""
            SELECT {", ".join(columns)}
            FROM {table_name}
            ORDER BY {order_column}
            """
        ).fetchall()
        signature.append((table_name, tuple(tuple(row) for row in rows)))
    return tuple(signature)


def _candidate_runtime_signature(
    *,
    admitted_plan_rows: tuple[AdmittedPlanPinRow, ...],
    default_plan_row: DefaultPlanRow | None,
    receipt_rows: tuple[InputReceiptRow, ...],
    work_item_rows: tuple[WorkItemRow, ...],
    activation_rows: tuple[ActivationRow, ...],
    run_rows: tuple[RunRow, ...],
    runner_session_rows: tuple[RunnerSessionRow, ...],
    runner_session_cancellation_rows: tuple[RunnerSessionCancellationRow, ...],
    runner_session_cancellation_attempt_rows: tuple[
        RunnerSessionCancellationAttemptRow, ...
    ],
    runner_session_completion_rows: tuple[RunnerSessionCompletionRow, ...],
    observation_rows: tuple[RunnerObservationRow, ...],
    artifact_rows: tuple[ArtifactRow, ...],
    effect_proposal_rows: tuple[EffectProposalRow, ...],
    effect_reconciliation_rows: tuple[EffectReconciliationRow, ...],
    activation_route_rows: tuple[ActivationRouteRow, ...],
    fanout_rows: tuple[FanoutRow, ...],
    work_dependency_rows: tuple[WorkDependencyRow, ...],
    closure_target_rows: tuple[ClosureTargetRow, ...],
    closure_evaluation_rows: tuple[ClosureEvaluationRow, ...],
    closure_terminal_rows: tuple[ClosureTerminalRow, ...],
    remediation_work_rows: tuple[RemediationWorkRow, ...],
    closure_blocked_rows: tuple[ClosureBlockedRow, ...],
    closed_work_item_rows: tuple[ClosedWorkItemRow, ...],
    pause_state_row: PauseStateRow | None,
    quarantine_rows: tuple[QuarantineRow, ...],
    lineage_quarantine_rows: tuple[LineageQuarantineRow, ...],
    recovery_attempt_rows: tuple[RecoveryAttemptRow, ...],
    operator_intervention_rows: tuple[OperatorInterventionRow, ...],
    operator_wait_rows: tuple[OperatorWaitRow, ...],
    cooldown_wait_rows: tuple[CooldownWaitRow, ...],
    counter_rows: tuple[CounterRow, ...],
    transition_rows: tuple[TransitionRow, ...],
    governance_event_rows: tuple[GovernanceEventRow, ...],
    trace_rows: tuple[TraceRow, ...],
    refusal_rows: tuple[RefusalRow, ...],
) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    row_groups: dict[str, tuple[object, ...]] = {
        "admitted_plan_pins": admitted_plan_rows,
        "default_plan": () if default_plan_row is None else (default_plan_row,),
        "input_receipts": receipt_rows,
        "work_items": work_item_rows,
        "activations": activation_rows,
        "runs": run_rows,
        "runner_sessions": runner_session_rows,
        "runner_session_cancellation_requests": runner_session_cancellation_rows,
        "runner_session_cancellation_attempts": (
            runner_session_cancellation_attempt_rows
        ),
        "runner_session_completions": runner_session_completion_rows,
        "runner_observations": observation_rows,
        "artifacts": artifact_rows,
        "effect_proposals": effect_proposal_rows,
        "effect_reconciliations": effect_reconciliation_rows,
        "activation_routes": activation_route_rows,
        "fanout_records": fanout_rows,
        "work_dependencies": work_dependency_rows,
        "closure_targets": closure_target_rows,
        "closure_evaluations": closure_evaluation_rows,
        "closure_terminal_records": closure_terminal_rows,
        "remediation_work_records": remediation_work_rows,
        "closure_blocked_records": closure_blocked_rows,
        "closed_work_items": closed_work_item_rows,
        "pause_state": () if pause_state_row is None else (pause_state_row,),
        "quarantine_records": quarantine_rows,
        "lineage_quarantines": lineage_quarantine_rows,
        "recovery_attempts": recovery_attempt_rows,
        "operator_interventions": operator_intervention_rows,
        "operator_waits": operator_wait_rows,
        "cooldown_waits": cooldown_wait_rows,
        "counters": counter_rows,
        "transitions": transition_rows,
        "governance_events": governance_event_rows,
        "traces": trace_rows,
        "refusals": refusal_rows,
    }
    return tuple(
        (
            table_name,
            tuple(
                tuple(getattr(row, column) for column in columns)
                for row in row_groups[table_name]
            ),
        )
        for table_name, columns, _order_column in _RUNTIME_TABLE_SIGNATURE_COLUMNS
    )


def _validate_candidate_runtime_rows(
    *,
    receipt_rows: tuple[InputReceiptRow, ...],
    transition_rows: tuple[TransitionRow, ...],
    governance_event_rows: tuple[GovernanceEventRow, ...],
    trace_rows: tuple[TraceRow, ...],
    refusal_rows: tuple[RefusalRow, ...],
) -> None:
    transition_rows_by_record_id = {
        row.record_id: row for row in transition_rows
    }
    transition_rows_by_order = {
        row.transition_order: row for row in transition_rows
    }
    governance_rows_by_order = {
        row.transition_order: row for row in governance_event_rows
    }
    validate_transition_rows(transition_rows)
    validate_receipt_transition_rows(receipt_rows, transition_rows_by_record_id)
    validate_audit_transition_rows(
        "governance_events",
        governance_event_rows,
        transition_rows_by_order,
    )
    validate_audit_transition_rows("traces", trace_rows, transition_rows_by_order)
    validate_trace_governance_rows(trace_rows, governance_rows_by_order)
    validate_audit_transition_rows("refusals", refusal_rows, transition_rows_by_order)


def _replace_runtime_rows(
    connection: sqlite3.Connection,
    *,
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
    admitted_plan_rows: tuple[AdmittedPlanPinRow, ...],
    default_plan_row: DefaultPlanRow | None,
    receipt_rows: tuple[InputReceiptRow, ...],
    work_item_rows: tuple[WorkItemRow, ...],
    activation_rows: tuple[ActivationRow, ...],
    run_rows: tuple[RunRow, ...],
    runner_session_rows: tuple[RunnerSessionRow, ...],
    runner_session_cancellation_rows: tuple[RunnerSessionCancellationRow, ...],
    runner_session_cancellation_attempt_rows: tuple[
        RunnerSessionCancellationAttemptRow, ...
    ],
    runner_session_completion_rows: tuple[RunnerSessionCompletionRow, ...],
    observation_rows: tuple[RunnerObservationRow, ...],
    artifact_rows: tuple[ArtifactRow, ...],
    effect_proposal_rows: tuple[EffectProposalRow, ...],
    effect_reconciliation_rows: tuple[EffectReconciliationRow, ...],
    activation_route_rows: tuple[ActivationRouteRow, ...],
    fanout_rows: tuple[FanoutRow, ...],
    work_dependency_rows: tuple[WorkDependencyRow, ...],
    closure_target_rows: tuple[ClosureTargetRow, ...],
    closure_evaluation_rows: tuple[ClosureEvaluationRow, ...],
    closure_terminal_rows: tuple[ClosureTerminalRow, ...],
    remediation_work_rows: tuple[RemediationWorkRow, ...],
    closure_blocked_rows: tuple[ClosureBlockedRow, ...],
    closed_work_item_rows: tuple[ClosedWorkItemRow, ...],
    pause_state_row: PauseStateRow | None,
    quarantine_rows: tuple[QuarantineRow, ...],
    lineage_quarantine_rows: tuple[LineageQuarantineRow, ...],
    recovery_attempt_rows: tuple[RecoveryAttemptRow, ...],
    operator_intervention_rows: tuple[OperatorInterventionRow, ...],
    operator_wait_rows: tuple[OperatorWaitRow, ...],
    cooldown_wait_rows: tuple[CooldownWaitRow, ...],
    counter_rows: tuple[CounterRow, ...],
    transition_rows: tuple[TransitionRow, ...],
    governance_event_rows: tuple[GovernanceEventRow, ...],
    trace_rows: tuple[TraceRow, ...],
    refusal_rows: tuple[RefusalRow, ...],
) -> None:
    _refuse_stale_or_divergent_transition_history(connection, transition_rows)
    _validate_candidate_runtime_rows(
        receipt_rows=receipt_rows,
        transition_rows=transition_rows,
        governance_event_rows=governance_event_rows,
        trace_rows=trace_rows,
        refusal_rows=refusal_rows,
    )
    _refuse_same_history_runtime_rewrite(
        connection,
        admitted_plan_rows=admitted_plan_rows,
        default_plan_row=default_plan_row,
        receipt_rows=receipt_rows,
        work_item_rows=work_item_rows,
        activation_rows=activation_rows,
        run_rows=run_rows,
        runner_session_rows=runner_session_rows,
        runner_session_cancellation_rows=runner_session_cancellation_rows,
        runner_session_cancellation_attempt_rows=(
            runner_session_cancellation_attempt_rows
        ),
        runner_session_completion_rows=runner_session_completion_rows,
        observation_rows=observation_rows,
        artifact_rows=artifact_rows,
        effect_proposal_rows=effect_proposal_rows,
        effect_reconciliation_rows=effect_reconciliation_rows,
        activation_route_rows=activation_route_rows,
        fanout_rows=fanout_rows,
        work_dependency_rows=work_dependency_rows,
        closure_target_rows=closure_target_rows,
        closure_evaluation_rows=closure_evaluation_rows,
        closure_terminal_rows=closure_terminal_rows,
        remediation_work_rows=remediation_work_rows,
        closure_blocked_rows=closure_blocked_rows,
        closed_work_item_rows=closed_work_item_rows,
        pause_state_row=pause_state_row,
        quarantine_rows=quarantine_rows,
        lineage_quarantine_rows=lineage_quarantine_rows,
        recovery_attempt_rows=recovery_attempt_rows,
        operator_intervention_rows=operator_intervention_rows,
        operator_wait_rows=operator_wait_rows,
        cooldown_wait_rows=cooldown_wait_rows,
        counter_rows=counter_rows,
        transition_rows=transition_rows,
        governance_event_rows=governance_event_rows,
        trace_rows=trace_rows,
        refusal_rows=refusal_rows,
    )
    validate_loaded_runtime_state(state)
    _validate_runner_session_evidence_authority(state, cas_store)
    for table_name in (
        "refusals",
        "traces",
        "governance_events",
        "transitions",
        "counters",
        "cooldown_waits",
        "operator_waits",
        "operator_interventions",
        "recovery_attempts",
        "lineage_quarantines",
        "quarantine_records",
        "pause_state",
        "closed_work_items",
        "closure_blocked_records",
        "remediation_work_records",
        "closure_terminal_records",
        "closure_evaluations",
        "closure_targets",
        "work_dependencies",
        "fanout_records",
        "activation_routes",
        "effect_reconciliations",
        "effect_proposals",
        "artifacts",
        "runner_observations",
        "runner_session_completions",
        "runner_session_cancellation_attempts",
        "runner_session_cancellation_requests",
        "runner_sessions",
        "runs",
        "activations",
        "work_items",
        "input_receipts",
        "default_plan",
        "admitted_plan_pins",
    ):
        connection.execute(f"DELETE FROM {table_name}")

    connection.executemany(
        """
        INSERT INTO admitted_plan_pins (
            authority_fingerprint,
            plan_id,
            plan_format_version,
            selected_plan_digest,
            admitted_at_order
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.authority_fingerprint,
                row.plan_id,
                row.plan_format_version,
                row.selected_plan_digest,
                row.admitted_at_order,
            )
            for row in admitted_plan_rows
        ),
    )
    if default_plan_row is not None:
        connection.execute(
            """
            INSERT INTO default_plan (
                id,
                plan_id,
                authority_fingerprint,
                plan_format_version,
                selected_plan_digest,
                set_at_order
            )
            VALUES (1, ?, ?, ?, ?, ?)
            """,
            (
                default_plan_row.plan_id,
                default_plan_row.authority_fingerprint,
                default_plan_row.plan_format_version,
                default_plan_row.selected_plan_digest,
                default_plan_row.set_at_order,
            ),
        )
    connection.executemany(
        """
        INSERT INTO input_receipts (
            input_id,
            input_payload_digest,
            transition_id,
            accepted,
            refusal_reason,
            received_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.input_id,
                row.input_payload_digest,
                row.transition_id,
                row.accepted,
                row.refusal_reason,
                row.received_at_order,
            )
            for row in receipt_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO work_items (
            work_item_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            generation,
            payload_digest,
            queue_family_id,
            lineage_id,
            created_by_input_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.work_item_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.generation,
                row.payload_digest,
                row.queue_family_id,
                row.lineage_id,
                row.created_by_input_id,
                row.created_at_order,
            )
            for row in work_item_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO activations (
            activation_id,
            work_item_id,
            lineage_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            queue_family_id,
            graph_node_id,
            stage_kind_id,
            runner_binding_id,
            generation,
            created_by_input_id,
            claimed_by_run_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.activation_id,
                row.work_item_id,
                row.lineage_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.queue_family_id,
                row.graph_node_id,
                row.stage_kind_id,
                row.runner_binding_id,
                row.generation,
                row.created_by_input_id,
                row.claimed_by_run_id,
                row.created_at_order,
            )
            for row in activation_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO runs (
            run_id,
            activation_id,
            work_item_id,
            claim_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            generation,
            fencing_token,
            stage_kind_id,
            runner_binding_id,
            created_by_input_id,
            current_session_id,
            last_dispatch_generation,
            started_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.run_id,
                row.activation_id,
                row.work_item_id,
                row.claim_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.generation,
                row.fencing_token,
                row.stage_kind_id,
                row.runner_binding_id,
                row.created_by_input_id,
                row.current_session_id,
                row.last_dispatch_generation,
                row.started_at_order,
            )
            for row in run_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO runner_sessions (
            schema_version,
            session_id,
            run_id,
            dispatch_generation,
            session_fencing_token,
            state,
            created_at,
            start_intent_at,
            started_at,
            ended_at,
            durable_locator_digest,
            cleanup_disposition
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.schema_version,
                row.session_id,
                row.run_id,
                row.dispatch_generation,
                row.session_fencing_token,
                row.state,
                row.created_at,
                row.start_intent_at,
                row.started_at,
                row.ended_at,
                row.durable_locator_digest,
                row.cleanup_disposition,
            )
            for row in runner_session_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO runner_session_cancellation_requests (
            schema_version,
            request_id,
            session_id,
            dispatch_generation,
            reason,
            source_kind,
            actor_id,
            requested_at,
            request_order,
            primary_request
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.schema_version,
                row.request_id,
                row.session_id,
                row.dispatch_generation,
                row.reason,
                row.source_kind,
                row.actor_id,
                row.requested_at,
                row.request_order,
                row.primary_request,
            )
            for row in runner_session_cancellation_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO runner_session_cancellation_attempts (
            schema_version,
            attempt_id,
            session_id,
            request_id,
            sequence,
            operation,
            result,
            started_at,
            completed_at,
            bounded_diagnostic_digest
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.schema_version,
                row.attempt_id,
                row.session_id,
                row.request_id,
                row.sequence,
                row.operation,
                row.result,
                row.started_at,
                row.completed_at,
                row.bounded_diagnostic_digest,
            )
            for row in runner_session_cancellation_attempt_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO runner_session_completions (
            schema_version,
            completion_id,
            session_id,
            run_id,
            dispatch_generation,
            session_fencing_token,
            terminal_state,
            exit_kind,
            adapter_outcome_kind,
            adapter_error_kind,
            runner_result_evidence_digest,
            primary_cancellation_request_id,
            cleanup_disposition,
            started_at,
            cancel_requested_at,
            completed_at,
            bounds_summary,
            truncation_metadata,
            redaction_policy_id,
            diagnostic_digest,
            application_input_id
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        tuple(
            (
                row.schema_version,
                row.completion_id,
                row.session_id,
                row.run_id,
                row.dispatch_generation,
                row.session_fencing_token,
                row.terminal_state,
                row.exit_kind,
                row.adapter_outcome_kind,
                row.adapter_error_kind,
                row.runner_result_evidence_digest,
                row.primary_cancellation_request_id,
                row.cleanup_disposition,
                row.started_at,
                row.cancel_requested_at,
                row.completed_at,
                row.bounds_summary,
                row.truncation_metadata,
                row.redaction_policy_id,
                row.diagnostic_digest,
                row.application_input_id,
            )
            for row in runner_session_completion_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO runner_observations (
            observation_id,
            run_id,
            payload_digest,
            created_by_input_id,
            observed_at,
            observed_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.observation_id,
                row.run_id,
                row.payload_digest,
                row.created_by_input_id,
                row.observed_at,
                row.observed_at_order,
            )
            for row in observation_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO artifacts (
            artifact_id,
            work_item_id,
            artifact_schema_id,
            payload_digest,
            created_by_input_id,
            source_run_id,
            source_action_id,
            source_stage_kind_id,
            source_graph_node_id,
            artifact_payload_digest,
            transition_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.artifact_id,
                row.work_item_id,
                row.artifact_schema_id,
                row.payload_digest,
                row.created_by_input_id,
                row.source_run_id,
                row.source_action_id,
                row.source_stage_kind_id,
                row.source_graph_node_id,
                row.artifact_payload_digest,
                row.transition_id,
                row.created_at_order,
            )
            for row in artifact_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO effect_proposals (
            effect_id,
            dedupe_key,
            effect_declaration_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            selected_plan_fingerprint,
            terminal_action_id,
            artifact_id,
            artifact_schema_id,
            artifact_payload_digest,
            source_run_id,
            source_action_id,
            source_input_id,
            source_work_item_id,
            source_activation_id,
            source_graph_node_id,
            source_stage_kind_id,
            source_runner_binding_id,
            source_queue_family_id,
            lineage_id,
            provider_ref,
            capability_policy_ref,
            target_ref_kind,
            target_ref_schema,
            target_skill_id,
            target_path_ref,
            status,
            created_input_id,
            created_transition_id,
            created_at_order
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        tuple(
            (
                row.effect_id,
                row.dedupe_key,
                row.effect_declaration_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.selected_plan_fingerprint,
                row.terminal_action_id,
                row.artifact_id,
                row.artifact_schema_id,
                row.artifact_payload_digest,
                row.source_run_id,
                row.source_action_id,
                row.source_input_id,
                row.source_work_item_id,
                row.source_activation_id,
                row.source_graph_node_id,
                row.source_stage_kind_id,
                row.source_runner_binding_id,
                row.source_queue_family_id,
                row.lineage_id,
                row.provider_ref,
                row.capability_policy_ref,
                row.target_ref_kind,
                row.target_ref_schema,
                row.target_skill_id,
                row.target_path_ref,
                row.status,
                row.created_input_id,
                row.created_transition_id,
                row.created_at_order,
            )
            for row in effect_proposal_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO effect_reconciliations (
            reconciliation_id,
            effect_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            selected_plan_fingerprint,
            provider_ref,
            status,
            fake_local_result_digest,
            created_input_id,
            created_transition_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.reconciliation_id,
                row.effect_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.selected_plan_fingerprint,
                row.provider_ref,
                row.status,
                row.fake_local_result_digest,
                row.created_input_id,
                row.created_transition_id,
                row.created_at_order,
            )
            for row in effect_reconciliation_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO activation_routes (
            record_id,
            action_id,
            source_run_id,
            source_work_item_id,
            target_work_item_id,
            target_activation_id,
            created_by_input_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.action_id,
                row.source_run_id,
                row.source_work_item_id,
                row.target_work_item_id,
                row.target_activation_id,
                row.created_by_input_id,
                row.created_at_order,
            )
            for row in activation_route_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO fanout_records (
            record_id,
            fanout_id,
            source_artifact_id,
            source_artifact_digest,
            source_work_item_id,
            source_run_id,
            source_action_id,
            target_work_item_id,
            target_activation_id,
            target_queue_family_id,
            target_stage_kind_id,
            target_graph_node_id,
            item_key,
            lineage_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            created_by_input_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.fanout_id,
                row.source_artifact_id,
                row.source_artifact_digest,
                row.source_work_item_id,
                row.source_run_id,
                row.source_action_id,
                row.target_work_item_id,
                row.target_activation_id,
                row.target_queue_family_id,
                row.target_stage_kind_id,
                row.target_graph_node_id,
                row.item_key,
                row.lineage_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.created_by_input_id,
                row.created_at_order,
            )
            for row in fanout_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO work_dependencies (
            dependency_id,
            dependent_work_item_id,
            dependency_work_item_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            lineage_id,
            fanout_record_id,
            created_by_input_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.dependency_id,
                row.dependent_work_item_id,
                row.dependency_work_item_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.lineage_id,
                row.fanout_record_id,
                row.created_by_input_id,
                row.created_at_order,
            )
            for row in work_dependency_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO closure_targets (
            closure_target_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            completion_behavior_id,
            lineage_id,
            root_source_kind,
            root_source_id,
            closure_root_work_item_id,
            request_kind,
            target_graph_node_id,
            evidence_window_digest,
            status,
            opened_by_input_id,
            closed_by_record_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.closure_target_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.completion_behavior_id,
                row.lineage_id,
                row.root_source_kind,
                row.root_source_id,
                row.closure_root_work_item_id,
                row.request_kind,
                row.target_graph_node_id,
                row.evidence_window_digest,
                row.status,
                row.opened_by_input_id,
                row.closed_by_record_id,
                row.created_at_order,
            )
            for row in closure_target_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO closure_evaluations (
            record_id,
            closure_target_id,
            completion_behavior_id,
            request_kind,
            target_work_item_id,
            target_activation_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            lineage_id,
            created_by_input_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.closure_target_id,
                row.completion_behavior_id,
                row.request_kind,
                row.target_work_item_id,
                row.target_activation_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.lineage_id,
                row.created_by_input_id,
                row.created_at_order,
            )
            for row in closure_evaluation_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO closure_terminal_records (
            record_id,
            closure_target_id,
            completion_behavior_id,
            terminal_kind,
            source_run_id,
            source_action_id,
            source_artifact_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            lineage_id,
            created_by_input_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.closure_target_id,
                row.completion_behavior_id,
                row.terminal_kind,
                row.source_run_id,
                row.source_action_id,
                row.source_artifact_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.lineage_id,
                row.created_by_input_id,
                row.created_at_order,
            )
            for row in closure_terminal_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO remediation_work_records (
            record_id,
            remediation_policy_id,
            closure_target_id,
            source_run_id,
            source_action_id,
            source_artifact_id,
            target_work_item_id,
            target_activation_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            lineage_id,
            dedupe_key,
            created_by_input_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.remediation_policy_id,
                row.closure_target_id,
                row.source_run_id,
                row.source_action_id,
                row.source_artifact_id,
                row.target_work_item_id,
                row.target_activation_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.lineage_id,
                row.dedupe_key,
                row.created_by_input_id,
                row.created_at_order,
            )
            for row in remediation_work_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO closure_blocked_records (
            record_id,
            closure_target_id,
            completion_behavior_id,
            source_run_id,
            source_action_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            lineage_id,
            operator_required,
            created_by_input_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.closure_target_id,
                row.completion_behavior_id,
                row.source_run_id,
                row.source_action_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.lineage_id,
                row.operator_required,
                row.created_by_input_id,
                row.created_at_order,
            )
            for row in closure_blocked_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO closed_work_items (
            record_id,
            work_item_id,
            source_run_id,
            action_id,
            operator_intervention_record_id,
            close_kind,
            created_by_input_id,
            closed_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.work_item_id,
                row.source_run_id,
                row.action_id,
                row.operator_intervention_record_id,
                row.close_kind,
                row.created_by_input_id,
                row.closed_at_order,
            )
            for row in closed_work_item_rows
        ),
    )
    if pause_state_row is not None:
        connection.execute(
            """
            INSERT INTO pause_state (
                id,
                record_id,
                source_run_id,
                work_item_id,
                action_id,
                created_by_input_id,
                paused_at_order
            )
            VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            (
                pause_state_row.record_id,
                pause_state_row.source_run_id,
                pause_state_row.work_item_id,
                pause_state_row.action_id,
                pause_state_row.created_by_input_id,
                pause_state_row.paused_at_order,
            ),
        )
    connection.executemany(
        """
        INSERT INTO quarantine_records (
            record_id,
            work_item_id,
            source_run_id,
            action_id,
            created_by_input_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.work_item_id,
                row.source_run_id,
                row.action_id,
                row.created_by_input_id,
                row.created_at_order,
            )
            for row in quarantine_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO lineage_quarantines (
            quarantine_id,
            policy_id,
            lineage_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            recovery_attempt_record_id,
            original_source_run_id,
            original_source_work_item_id,
            original_source_activation_id,
            emitting_recovery_activation_id,
            emitting_recovery_run_id,
            action_id,
            attempt_count,
            created_input_id,
            actor_kind,
            status,
            superseded_input_id,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.quarantine_id,
                row.policy_id,
                row.lineage_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.recovery_attempt_record_id,
                row.original_source_run_id,
                row.original_source_work_item_id,
                row.original_source_activation_id,
                row.emitting_recovery_activation_id,
                row.emitting_recovery_run_id,
                row.action_id,
                row.attempt_count,
                row.created_input_id,
                row.actor_kind,
                row.status,
                row.superseded_input_id,
                row.created_at_order,
            )
            for row in lineage_quarantine_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO recovery_attempts (
            record_id,
            policy_id,
            lineage_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            attempt_count,
            phase,
            source_run_id,
            source_work_item_id,
            source_activation_id,
            source_graph_node_id,
            source_stage_kind_id,
            source_runner_binding_id,
            source_queue_family_id,
            recovery_action_id,
            latest_recovery_activation_id,
            latest_recovery_run_id,
            latest_return_action_id,
            created_by_input_id,
            updated_by_input_id,
            updated_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.policy_id,
                row.lineage_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.attempt_count,
                row.phase,
                row.source_run_id,
                row.source_work_item_id,
                row.source_activation_id,
                row.source_graph_node_id,
                row.source_stage_kind_id,
                row.source_runner_binding_id,
                row.source_queue_family_id,
                row.recovery_action_id,
                row.latest_recovery_activation_id,
                row.latest_recovery_run_id,
                row.latest_return_action_id,
                row.created_by_input_id,
                row.updated_by_input_id,
                row.updated_at_order,
            )
            for row in recovery_attempt_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO operator_interventions (
            record_id,
            created_by_input_id,
            input_payload_digest,
            option_id,
            kind,
            result,
            policy_id,
            lineage_id,
            quarantine_id,
            recovery_attempt_record_id,
            recovery_attempt_count,
            attempt_effect,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            actor_kind,
            actor_id,
            reason,
            target_work_item_id,
            target_activation_id,
            closed_work_item_ids_json,
            closed_activation_ids_json,
            closed_run_ids_json,
            payload_digest,
            payload_reference,
            created_at_order
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        tuple(
            (
                row.record_id,
                row.created_by_input_id,
                row.input_payload_digest,
                row.option_id,
                row.kind,
                row.result,
                row.policy_id,
                row.lineage_id,
                row.quarantine_id,
                row.recovery_attempt_record_id,
                row.recovery_attempt_count,
                row.attempt_effect,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.actor_kind,
                row.actor_id,
                row.reason,
                row.target_work_item_id,
                row.target_activation_id,
                row.closed_work_item_ids_json,
                row.closed_activation_ids_json,
                row.closed_run_ids_json,
                row.payload_digest,
                row.payload_reference,
                row.created_at_order,
            )
            for row in operator_intervention_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO operator_waits (
            wait_id,
            operator_wait_id,
            source_action_id,
            lineage_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            source_work_item_id,
            source_activation_id,
            source_run_id,
            source_stage_kind_id,
            source_graph_node_id,
            source_queue_family_id,
            source_runner_binding_id,
            source_artifact_id,
            status,
            created_input_id,
            created_input_payload_digest,
            resolved_input_id,
            resolved_input_payload_digest,
            actor_id,
            actor_kind,
            resolution_kind,
            target_work_item_id,
            target_activation_id,
            closed_work_item_ids_json,
            payload_digest,
            payload_reference,
            created_at_order
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?
        )
        """,
        tuple(
            (
                row.wait_id,
                row.operator_wait_id,
                row.source_action_id,
                row.lineage_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.source_work_item_id,
                row.source_activation_id,
                row.source_run_id,
                row.source_stage_kind_id,
                row.source_graph_node_id,
                row.source_queue_family_id,
                row.source_runner_binding_id,
                row.source_artifact_id,
                row.status,
                row.created_input_id,
                row.created_input_payload_digest,
                row.resolved_input_id,
                row.resolved_input_payload_digest,
                row.actor_id,
                row.actor_kind,
                row.resolution_kind,
                row.target_work_item_id,
                row.target_activation_id,
                row.closed_work_item_ids_json,
                row.payload_digest,
                row.payload_reference,
                row.created_at_order,
            )
            for row in operator_wait_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO cooldown_waits (
            wait_id,
            policy_id,
            lineage_id,
            recovery_attempt_record_id,
            attempt_count,
            source_run_id,
            source_work_item_id,
            source_activation_id,
            recovery_action_id,
            target_stage_kind_id,
            target_graph_node_id,
            target_runner_binding_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            created_input_id,
            created_at,
            due_at,
            consumed_input_id,
            consumed_at,
            resulting_recovery_activation_id,
            updated_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.wait_id,
                row.policy_id,
                row.lineage_id,
                row.recovery_attempt_record_id,
                row.attempt_count,
                row.source_run_id,
                row.source_work_item_id,
                row.source_activation_id,
                row.recovery_action_id,
                row.target_stage_kind_id,
                row.target_graph_node_id,
                row.target_runner_binding_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.created_input_id,
                row.created_at,
                row.due_at,
                row.consumed_input_id,
                row.consumed_at,
                row.resulting_recovery_activation_id,
                row.updated_at_order,
            )
            for row in cooldown_wait_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO counters (
            record_id,
            counter_id,
            plan_id,
            plan_authority_fingerprint,
            plan_format_version,
            lineage_id,
            value,
            updated_by_input_id,
            updated_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.counter_id,
                row.plan_id,
                row.plan_authority_fingerprint,
                row.plan_format_version,
                row.lineage_id,
                row.value,
                row.updated_by_input_id,
                row.updated_at_order,
            )
            for row in counter_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO transitions (
            transition_order,
            record_id,
            input_id,
            input_kind,
            input_family,
            accepted,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.transition_order,
                row.record_id,
                row.input_id,
                row.input_kind,
                row.input_family,
                row.accepted,
                row.created_at,
            )
            for row in transition_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO governance_events (
            record_id,
            transition_order,
            input_id,
            input_kind,
            input_family,
            disposition,
            plan_fingerprint,
            work_item_id,
            run_id,
            action_id,
            authority_source,
            refusal_reason,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.transition_order,
                row.input_id,
                row.input_kind,
                row.input_family,
                row.disposition,
                row.plan_fingerprint,
                row.work_item_id,
                row.run_id,
                row.action_id,
                row.authority_source,
                row.refusal_reason,
                row.created_at_order,
            )
            for row in governance_event_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO traces (
            record_id,
            transition_order,
            input_id,
            input_kind,
            input_family,
            disposition,
            plan_fingerprint,
            work_item_id,
            run_id,
            action_id,
            authority_source,
            refusal_reason,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.transition_order,
                row.input_id,
                row.input_kind,
                row.input_family,
                row.disposition,
                row.plan_fingerprint,
                row.work_item_id,
                row.run_id,
                row.action_id,
                row.authority_source,
                row.refusal_reason,
                row.created_at_order,
            )
            for row in trace_rows
        ),
    )
    connection.executemany(
        """
        INSERT INTO refusals (
            record_id,
            transition_order,
            input_id,
            input_kind,
            input_family,
            reason,
            detail,
            created_at_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.record_id,
                row.transition_order,
                row.input_id,
                row.input_kind,
                row.input_family,
                row.reason,
                row.detail,
                row.created_at_order,
            )
            for row in refusal_rows
        ),
    )
