"""SQLite runtime state load reconstruction."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping
from typing import NoReturn, TypeVar, cast

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    SelectedCompiledPlan,
    verify_authority_fingerprint,
)
from millrace.contracts.ids import (
    QueueFamilyId,
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
    ExternalEnqueueRoute,
    FanoutRecord,
    GovernanceEventRecord,
    InputReceipt,
    InputReceiptRef,
    LineageQuarantineRecord,
    OperatorInterventionRecord,
    OperatorWaitRecord,
    PauseRecord,
    PlanRef,
    QuarantineRecord,
    RecoveryAttemptRecord,
    RemediationWorkRecord,
    RunnerObservationRecord,
    RunnerSessionCancellationAttemptRecord,
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
    RunRecord,
    RunRef,
    RuntimeState,
    TraceRecord,
    TransitionRecord,
    TransitionRefusal,
    WorkDependencyRecord,
    WorkItem,
    WorkItemRef,
)
from millrace.substrate._sqlite_relations import (
    runner_session_cas_references,
    validate_audit_transition_rows,
    validate_loaded_runtime_state,
    validate_receipt_transition_rows,
    validate_trace_governance_rows,
    validate_transition_rows,
)
from millrace.substrate._sqlite_rows import (
    GovernanceEventRow,
    TransitionRow,
    activation_route_from_row,
    artifact_schema_id_from_row,
    artifact_source_action_id_from_row,
    artifact_source_stage_kind_id_from_row,
    closed_work_item_from_row,
    closure_blocked_from_row,
    closure_evaluation_from_row,
    closure_target_from_row,
    closure_terminal_from_row,
    cooldown_wait_from_row,
    counter_from_row,
    decode_activation_route_row,
    decode_activation_row,
    decode_admitted_plan_pin_row,
    decode_artifact_row,
    decode_closed_work_item_row,
    decode_closure_blocked_row,
    decode_closure_evaluation_row,
    decode_closure_target_row,
    decode_closure_terminal_row,
    decode_cooldown_wait_row,
    decode_counter_row,
    decode_default_plan_row,
    decode_effect_proposal_row,
    decode_effect_reconciliation_row,
    decode_fanout_row,
    decode_governance_event_row,
    decode_input_receipt_row,
    decode_lineage_quarantine_row,
    decode_operator_intervention_row,
    decode_operator_wait_row,
    decode_pause_state_row,
    decode_quarantine_row,
    decode_recovery_attempt_row,
    decode_refusal_row,
    decode_remediation_work_row,
    decode_run_row,
    decode_runner_observation_row,
    decode_runner_session_cancellation_attempt_row,
    decode_runner_session_cancellation_row,
    decode_runner_session_completion_row,
    decode_runner_session_row,
    decode_trace_row,
    decode_transition_row,
    decode_work_dependency_row,
    decode_work_item_row,
    effect_proposal_from_row,
    effect_reconciliation_from_row,
    fanout_from_row,
    governance_event_from_row,
    lineage_quarantine_from_row,
    operator_intervention_from_row,
    operator_wait_from_row,
    pause_from_row,
    plan_ref_from_activation_row,
    plan_ref_from_run_row,
    plan_ref_from_work_item_row,
    quarantine_from_row,
    recovery_attempt_from_row,
    refusal_from_row,
    remediation_work_from_row,
    trace_from_row,
    transition_from_row,
    work_dependency_from_row,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.codecs import (
    decode_payload,
    decode_selected_compiled_plan,
    loads_cas_object,
)
from millrace.substrate.errors import (
    CasDigestMismatch,
    CasObjectKindMismatch,
    CasObjectNotFound,
    InvalidCasDigest,
    StorageIntegrityError,
    SubstrateError,
)
from millrace.substrate.records import (
    ARTIFACT_PAYLOAD_OBJECT_KIND,
    PAYLOAD_OBJECT_KIND,
    SELECTED_COMPILED_PLAN_OBJECT_KIND,
    CasObjectEnvelope,
)

TWorkStateRecord = TypeVar(
    "TWorkStateRecord",
    ClosedWorkItemRecord,
    QuarantineRecord,
)


def load_runtime_state_rows(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
    *,
    _after_admitted_plans: Callable[[], None] | None = None,
) -> RuntimeState:
    if connection.in_transaction:
        return _load_runtime_state_rows_in_transaction(
            connection,
            cas_store,
            _after_admitted_plans=_after_admitted_plans,
        )

    connection.execute("BEGIN")
    try:
        state = _load_runtime_state_rows_in_transaction(
            connection,
            cas_store,
            _after_admitted_plans=_after_admitted_plans,
        )
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return state


def _load_runtime_state_rows_in_transaction(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
    *,
    _after_admitted_plans: Callable[[], None] | None = None,
) -> RuntimeState:
    admitted_plans, selected_plan_digests = _load_admitted_plans(
        connection,
        cas_store,
    )
    if _after_admitted_plans is not None:
        _after_admitted_plans()
    state = RuntimeState(
        admitted_plans=admitted_plans,
        default_plan_ref=_load_default_plan_ref(
            connection,
            admitted_plans,
            selected_plan_digests,
        ),
        receipts=_load_input_receipts(connection),
        work_items=_load_work_items(connection, cas_store),
        activations=_load_activations(connection),
        runs=_load_runs(connection),
        runner_sessions=_load_runner_sessions(connection),
        runner_session_cancellation_requests=(
            _load_runner_session_cancellation_requests(connection)
        ),
        runner_session_cancellation_attempts=(
            _load_runner_session_cancellation_attempts(connection)
        ),
        runner_session_completions=_load_runner_session_completions(connection),
        runner_observations=_load_runner_observations(connection, cas_store),
        artifacts=_load_artifacts(connection, cas_store),
        effect_proposals=_load_effect_proposals(connection),
        effect_reconciliations=_load_effect_reconciliations(connection),
        activation_routes=_load_activation_routes(connection),
        fanout_records=_load_fanout_records(connection),
        work_dependencies=_load_work_dependencies(connection),
        closure_targets=_load_closure_targets(connection, cas_store),
        closure_evaluations=_load_closure_evaluations(connection),
        closure_terminal_records=_load_closure_terminal_records(connection),
        remediation_work_records=_load_remediation_work_records(connection),
        closure_blocked_records=_load_closure_blocked_records(connection),
        closed_work_items=_load_closed_work_items(connection),
        pause=_load_pause(connection),
        quarantines=_load_quarantines(connection),
        lineage_quarantines=_load_lineage_quarantines(connection),
        recovery_attempts=_load_recovery_attempts(connection),
        operator_interventions=_load_operator_interventions(connection),
        operator_waits=_load_operator_waits(connection),
        cooldown_waits=_load_cooldown_waits(connection),
        counters=_load_counters(connection),
        governance_events=_load_governance_events(connection),
        traces=_load_traces(connection),
        transitions=_load_transitions(connection),
        refusals=_load_refusals(connection),
    )
    _validate_runner_session_cas_references(state, cas_store)
    validate_loaded_runtime_state(state)
    return state


def _validate_runner_session_cas_references(
    state: RuntimeState,
    cas_store: ContentAddressedByteStore,
) -> None:
    for reference_name, digest in runner_session_cas_references(state):
        try:
            cas_store.get_bytes(digest)
        except SubstrateError as exc:
            _raise_cas_reference_integrity_error(
                f"runner session {reference_name}",
                digest,
                exc,
            )


def _load_admitted_plans(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
) -> tuple[dict[str, AdmittedPlan], dict[str, str]]:
    rows = tuple(
        decode_admitted_plan_pin_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
                authority_fingerprint,
                plan_id,
                plan_format_version,
                selected_plan_digest,
                admitted_at_order
            FROM admitted_plan_pins
            ORDER BY admitted_at_order
            """
        ).fetchall()
    )
    admitted_plans: dict[str, AdmittedPlan] = {}
    selected_plan_digests: dict[str, str] = {}
    for row in rows:
        selected_plan = _load_selected_plan(
            cas_store,
            row.selected_plan_digest,
            reference_name="admitted plan selected_plan_digest",
        )
        if not verify_authority_fingerprint(selected_plan, row.authority_fingerprint):
            raise StorageIntegrityError(
                "selected plan authority fingerprint mismatch: "
                "admitted_plan_pins.authority_fingerprint does not match "
                "selected_plan_digest"
            )
        plan_ref = PlanRef(
            plan_id=row.plan_id,
            authority_fingerprint=row.authority_fingerprint,
            plan_format_version=row.plan_format_version,
        )
        if plan_ref != _plan_ref_for_selected_plan(
            selected_plan,
            row.authority_fingerprint,
        ):
            raise StorageIntegrityError(
                "admitted_plan_pins PlanRef must match selected plan"
            )
        admitted_plans[row.authority_fingerprint] = AdmittedPlan(
            plan_ref=plan_ref,
            selected_plan=selected_plan,
            external_enqueue_routes=_external_enqueue_routes(selected_plan),
        )
        selected_plan_digests[row.authority_fingerprint] = row.selected_plan_digest
    return admitted_plans, selected_plan_digests


def _load_default_plan_ref(
    connection: sqlite3.Connection,
    admitted_plans: Mapping[str, AdmittedPlan],
    selected_plan_digests: Mapping[str, str],
) -> PlanRef | None:
    row = connection.execute(
        """
        SELECT
            plan_id,
            authority_fingerprint,
            plan_format_version,
            selected_plan_digest,
            set_at_order
        FROM default_plan
        WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return None
    default_plan_row = decode_default_plan_row(cast(tuple[object, ...], row))
    admitted_plan = admitted_plans.get(default_plan_row.authority_fingerprint)
    if admitted_plan is None:
        raise StorageIntegrityError(
            "default_plan.authority_fingerprint must reference an admitted plan pin"
        )
    default_plan_ref = PlanRef(
        plan_id=default_plan_row.plan_id,
        authority_fingerprint=default_plan_row.authority_fingerprint,
        plan_format_version=default_plan_row.plan_format_version,
    )
    if default_plan_ref != admitted_plan.plan_ref:
        raise StorageIntegrityError("default_plan PlanRef must match admitted plan pin")
    admitted_digest = selected_plan_digests[default_plan_row.authority_fingerprint]
    if default_plan_row.selected_plan_digest != admitted_digest:
        raise StorageIntegrityError(
            "default_plan.selected_plan_digest must match admitted plan pin"
        )
    return default_plan_ref


def _load_input_receipts(connection: sqlite3.Connection) -> dict[str, InputReceipt]:
    rows = tuple(
        decode_input_receipt_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
                input_id,
                input_payload_digest,
                transition_id,
                accepted,
                refusal_reason,
                received_at_order
            FROM input_receipts
            ORDER BY received_at_order
            """
        ).fetchall()
    )
    validate_receipt_transition_rows(rows, _transition_rows_by_record_id(connection))
    return {
        row.input_id: InputReceipt(
            receipt_ref=InputReceiptRef(
                input_id=row.input_id,
                input_payload_digest=row.input_payload_digest,
            ),
            transition_id=row.transition_id,
            accepted=bool(row.accepted),
            refusal_reason=row.refusal_reason,
        )
        for row in rows
    }


def _load_work_items(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
) -> dict[str, WorkItem]:
    rows = tuple(
        decode_work_item_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM work_items
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    work_items: dict[str, WorkItem] = {}
    for row in rows:
        plan_ref = plan_ref_from_work_item_row(row)
        work_items[row.work_item_id] = WorkItem(
            ref=WorkItemRef(
                work_item_id=row.work_item_id,
                plan_ref=plan_ref,
                generation=row.generation,
            ),
            queue_family_id=QueueFamilyId(row.queue_family_id),
            payload=_load_payload(
                cas_store,
                row.payload_digest,
                reference_name="work item payload_digest",
            ),
            lineage_id=row.lineage_id,
            created_by_input_id=row.created_by_input_id,
        )
    return work_items


def _load_activations(connection: sqlite3.Connection) -> dict[str, Activation]:
    rows = tuple(
        decode_activation_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM activations
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.activation_id: Activation(
            activation_id=row.activation_id,
            work_item_id=row.work_item_id,
            lineage_id=row.lineage_id,
            plan_ref=plan_ref_from_activation_row(row),
            queue_family_id=QueueFamilyId(row.queue_family_id),
            graph_node_id=row.graph_node_id,
            stage_kind_id=StageKindId(row.stage_kind_id),
            runner_binding_id=RunnerBindingId(row.runner_binding_id),
            generation=row.generation,
            created_by_input_id=row.created_by_input_id,
            claimed_by_run_id=row.claimed_by_run_id,
        )
        for row in rows
    }


def _load_runs(connection: sqlite3.Connection) -> dict[str, RunRecord]:
    rows = tuple(
        decode_run_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM runs
            ORDER BY started_at_order
            """
        ).fetchall()
    )
    return {
        row.run_id: RunRecord(
            run_ref=RunRef(
                run_id=row.run_id,
                work_item_id=row.work_item_id,
                claim_id=row.claim_id,
                plan_ref=plan_ref_from_run_row(row),
                generation=row.generation,
                fencing_token=row.fencing_token,
            ),
            work_item_id=row.work_item_id,
            activation_id=row.activation_id,
            stage_kind_id=StageKindId(row.stage_kind_id),
            runner_binding_id=RunnerBindingId(row.runner_binding_id),
            created_by_input_id=row.created_by_input_id,
            current_session_id=row.current_session_id,
            last_dispatch_generation=row.last_dispatch_generation,
        )
        for row in rows
    }


def _load_runner_sessions(
    connection: sqlite3.Connection,
) -> dict[str, RunnerSessionRecord]:
    rows = tuple(
        decode_runner_session_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM runner_sessions
            ORDER BY run_id, dispatch_generation
            """
        ).fetchall()
    )
    records: dict[str, RunnerSessionRecord] = {}
    for row in rows:
        try:
            record = RunnerSessionRecord(
                session_id=row.session_id,
                run_id=row.run_id,
                dispatch_generation=row.dispatch_generation,
                session_fencing_token=row.session_fencing_token,
                state=row.state,
                created_at=row.created_at,
                start_intent_at=row.start_intent_at,
                started_at=row.started_at,
                ended_at=row.ended_at,
                durable_locator_digest=row.durable_locator_digest,
                cleanup_disposition=row.cleanup_disposition,
            )
        except ValueError as exc:
            raise StorageIntegrityError(
                f"invalid runner_sessions row: {exc}"
            ) from exc
        records[record.session_id] = record
    return records


def _load_runner_session_cancellation_requests(
    connection: sqlite3.Connection,
) -> dict[str, RunnerSessionCancellationRecord]:
    rows = tuple(
        decode_runner_session_cancellation_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM runner_session_cancellation_requests
            ORDER BY session_id, request_order
            """
        ).fetchall()
    )
    records: dict[str, RunnerSessionCancellationRecord] = {}
    for row in rows:
        try:
            record = RunnerSessionCancellationRecord(
                request_id=row.request_id,
                session_id=row.session_id,
                dispatch_generation=row.dispatch_generation,
                reason=row.reason,
                source_kind=row.source_kind,
                actor_id=row.actor_id,
                requested_at=row.requested_at,
                request_order=row.request_order,
                primary=bool(row.primary_request),
            )
        except ValueError as exc:
            raise StorageIntegrityError(
                f"invalid runner session cancellation row: {exc}"
            ) from exc
        records[record.request_id] = record
    return records


def _load_runner_session_cancellation_attempts(
    connection: sqlite3.Connection,
) -> dict[str, RunnerSessionCancellationAttemptRecord]:
    rows = tuple(
        decode_runner_session_cancellation_attempt_row(
            cast(tuple[object, ...], row)
        )
        for row in connection.execute(
            """
            SELECT
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
            FROM runner_session_cancellation_attempts
            ORDER BY session_id, sequence
            """
        ).fetchall()
    )
    records: dict[str, RunnerSessionCancellationAttemptRecord] = {}
    for row in rows:
        try:
            record = RunnerSessionCancellationAttemptRecord(
                attempt_id=row.attempt_id,
                session_id=row.session_id,
                request_id=row.request_id,
                sequence=row.sequence,
                operation=row.operation,
                result=row.result,
                started_at=row.started_at,
                completed_at=row.completed_at,
                bounded_diagnostic_digest=row.bounded_diagnostic_digest,
            )
        except ValueError as exc:
            raise StorageIntegrityError(
                f"invalid runner session cancellation attempt row: {exc}"
            ) from exc
        records[record.attempt_id] = record
    return records


def _load_runner_session_completions(
    connection: sqlite3.Connection,
) -> dict[str, RunnerSessionCompletionRecord]:
    rows = tuple(
        decode_runner_session_completion_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM runner_session_completions
            ORDER BY run_id, dispatch_generation
            """
        ).fetchall()
    )
    records: dict[str, RunnerSessionCompletionRecord] = {}
    for row in rows:
        try:
            record = RunnerSessionCompletionRecord(
                completion_id=row.completion_id,
                session_id=row.session_id,
                run_id=row.run_id,
                dispatch_generation=row.dispatch_generation,
                session_fencing_token=row.session_fencing_token,
                terminal_state=row.terminal_state,
                exit_kind=row.exit_kind,
                adapter_outcome_kind=row.adapter_outcome_kind,
                adapter_error_kind=row.adapter_error_kind,
                runner_result_evidence_digest=row.runner_result_evidence_digest,
                primary_cancellation_request_id=(
                    row.primary_cancellation_request_id
                ),
                cleanup_disposition=row.cleanup_disposition,
                started_at=row.started_at,
                cancel_requested_at=row.cancel_requested_at,
                completed_at=row.completed_at,
                bounds_summary=row.bounds_summary,
                truncation_metadata=row.truncation_metadata,
                redaction_policy_id=row.redaction_policy_id,
                diagnostic_digest=row.diagnostic_digest,
                application_input_id=row.application_input_id,
            )
        except ValueError as exc:
            raise StorageIntegrityError(
                f"invalid runner session completion row: {exc}"
            ) from exc
        records[record.session_id] = record
    return records


def _load_runner_observations(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
) -> dict[str, RunnerObservationRecord]:
    rows = tuple(
        decode_runner_observation_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
                observation_id,
                run_id,
                payload_digest,
                created_by_input_id,
                observed_at,
                observed_at_order
            FROM runner_observations
            ORDER BY observed_at_order
            """
        ).fetchall()
    )
    return {
        row.observation_id: RunnerObservationRecord(
            observation_id=row.observation_id,
            run_id=row.run_id,
            payload=_load_payload(
                cas_store,
                row.payload_digest,
                reference_name="runner observation payload_digest",
            ),
            created_by_input_id=row.created_by_input_id,
            observed_at=row.observed_at,
        )
        for row in rows
    }


def _load_artifacts(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
) -> dict[str, ArtifactRecord]:
    rows = tuple(
        decode_artifact_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM artifacts
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.artifact_id: ArtifactRecord(
            artifact_id=row.artifact_id,
            work_item_id=row.work_item_id,
            schema_id=artifact_schema_id_from_row(row),
            payload=_load_artifact_payload(
                cas_store,
                row.payload_digest,
                reference_name="artifact payload_digest",
            ),
            created_by_input_id=row.created_by_input_id,
            source_run_id=row.source_run_id,
            source_action_id=artifact_source_action_id_from_row(row),
            source_stage_kind_id=artifact_source_stage_kind_id_from_row(row),
            source_graph_node_id=row.source_graph_node_id,
            payload_digest=row.artifact_payload_digest,
            transition_id=row.transition_id,
        )
        for row in rows
    }


def _load_effect_proposals(
    connection: sqlite3.Connection,
) -> dict[str, EffectProposalRecord]:
    rows = tuple(
        decode_effect_proposal_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM effect_proposals
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.effect_id: effect_proposal_from_row(row)
        for row in rows
    }


def _load_effect_reconciliations(
    connection: sqlite3.Connection,
) -> dict[str, EffectReconciliationRecord]:
    rows = tuple(
        decode_effect_reconciliation_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM effect_reconciliations
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.reconciliation_id: effect_reconciliation_from_row(row)
        for row in rows
    }


def _load_activation_routes(
    connection: sqlite3.Connection,
) -> tuple[ActivationRouteRecord, ...]:
    rows = tuple(
        decode_activation_route_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
                record_id,
                action_id,
                source_run_id,
                source_work_item_id,
                target_work_item_id,
                target_activation_id,
                created_by_input_id,
                created_at_order
            FROM activation_routes
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return tuple(activation_route_from_row(row) for row in rows)


def _load_fanout_records(
    connection: sqlite3.Connection,
) -> dict[str, FanoutRecord]:
    rows = tuple(
        decode_fanout_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM fanout_records
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.record_id: fanout_from_row(row)
        for row in rows
    }


def _load_work_dependencies(
    connection: sqlite3.Connection,
) -> dict[str, WorkDependencyRecord]:
    rows = tuple(
        decode_work_dependency_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM work_dependencies
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.dependency_id: work_dependency_from_row(row)
        for row in rows
    }


def _load_closure_targets(
    connection: sqlite3.Connection,
    cas_store: ContentAddressedByteStore,
) -> dict[str, ClosureTargetRecord]:
    rows = tuple(
        decode_closure_target_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM closure_targets
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.closure_target_id: closure_target_from_row(
            row,
            evidence_window=_load_payload(
                cas_store,
                row.evidence_window_digest,
                reference_name="closure target evidence_window_digest",
            ),
        )
        for row in rows
    }


def _load_closure_evaluations(
    connection: sqlite3.Connection,
) -> dict[str, ClosureEvaluationRecord]:
    rows = tuple(
        decode_closure_evaluation_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM closure_evaluations
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.record_id: closure_evaluation_from_row(row)
        for row in rows
    }


def _load_closure_terminal_records(
    connection: sqlite3.Connection,
) -> dict[str, ClosureTerminalRecord]:
    rows = tuple(
        decode_closure_terminal_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM closure_terminal_records
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.record_id: closure_terminal_from_row(row)
        for row in rows
    }


def _load_remediation_work_records(
    connection: sqlite3.Connection,
) -> dict[str, RemediationWorkRecord]:
    rows = tuple(
        decode_remediation_work_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM remediation_work_records
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.record_id: remediation_work_from_row(row)
        for row in rows
    }


def _load_closure_blocked_records(
    connection: sqlite3.Connection,
) -> dict[str, ClosureBlockedRecord]:
    rows = tuple(
        decode_closure_blocked_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM closure_blocked_records
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.record_id: closure_blocked_from_row(row)
        for row in rows
    }


def _load_closed_work_items(
    connection: sqlite3.Connection,
) -> dict[str, ClosedWorkItemRecord]:
    rows = tuple(
        decode_closed_work_item_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
                record_id,
                work_item_id,
                source_run_id,
                action_id,
                operator_intervention_record_id,
                close_kind,
                created_by_input_id,
                closed_at_order
            FROM closed_work_items
            ORDER BY closed_at_order
            """
        ).fetchall()
    )
    records = tuple(closed_work_item_from_row(row) for row in rows)
    return _records_by_work_item_id(records, "closed_work_items")


def _load_pause(connection: sqlite3.Connection) -> PauseRecord | None:
    row = connection.execute(
        """
        SELECT
            record_id,
            source_run_id,
            work_item_id,
            action_id,
            created_by_input_id,
            paused_at_order
        FROM pause_state
        WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return None
    return pause_from_row(decode_pause_state_row(cast(tuple[object, ...], row)))


def _load_quarantines(connection: sqlite3.Connection) -> dict[str, QuarantineRecord]:
    rows = tuple(
        decode_quarantine_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
                record_id,
                work_item_id,
                source_run_id,
                action_id,
                created_by_input_id,
                created_at_order
            FROM quarantine_records
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    records = tuple(quarantine_from_row(row) for row in rows)
    return _records_by_work_item_id(records, "quarantine_records")


def _load_lineage_quarantines(
    connection: sqlite3.Connection,
) -> dict[str, LineageQuarantineRecord]:
    rows = tuple(
        decode_lineage_quarantine_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM lineage_quarantines
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    records_by_quarantine_id: dict[str, LineageQuarantineRecord] = {}
    for row in rows:
        record = lineage_quarantine_from_row(row)
        if record.quarantine_id in records_by_quarantine_id:
            raise StorageIntegrityError(
                "lineage_quarantines.quarantine_id must be unique"
            )
        records_by_quarantine_id[record.quarantine_id] = record
    return records_by_quarantine_id


def _load_recovery_attempts(
    connection: sqlite3.Connection,
) -> dict[str, RecoveryAttemptRecord]:
    rows = tuple(
        decode_recovery_attempt_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM recovery_attempts
            ORDER BY updated_at_order
            """
        ).fetchall()
    )
    return {
        row.record_id: recovery_attempt_from_row(row)
        for row in rows
    }


def _load_operator_interventions(
    connection: sqlite3.Connection,
) -> dict[str, OperatorInterventionRecord]:
    rows = tuple(
        decode_operator_intervention_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM operator_interventions
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.record_id: operator_intervention_from_row(row)
        for row in rows
    }


def _load_operator_waits(
    connection: sqlite3.Connection,
) -> dict[str, OperatorWaitRecord]:
    rows = tuple(
        decode_operator_wait_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM operator_waits
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {
        row.wait_id: operator_wait_from_row(row)
        for row in rows
    }


def _load_cooldown_waits(
    connection: sqlite3.Connection,
) -> dict[str, CooldownWaitRecord]:
    rows = tuple(
        decode_cooldown_wait_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM cooldown_waits
            ORDER BY updated_at_order
            """
        ).fetchall()
    )
    return {
        row.wait_id: cooldown_wait_from_row(row)
        for row in rows
    }


def _load_counters(
    connection: sqlite3.Connection,
) -> dict[str, CounterRecord]:
    rows = tuple(
        decode_counter_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
                record_id,
                counter_id,
                plan_id,
                plan_authority_fingerprint,
                plan_format_version,
                lineage_id,
                value,
                updated_by_input_id,
                updated_at_order
            FROM counters
            ORDER BY updated_at_order
            """
        ).fetchall()
    )
    return {
        row.record_id: counter_from_row(row)
        for row in rows
    }


def _records_by_work_item_id(
    records: Iterable[TWorkStateRecord],
    table_name: str,
) -> dict[str, TWorkStateRecord]:
    records_by_work_item_id: dict[str, TWorkStateRecord] = {}
    for record in records:
        if record.work_item_id in records_by_work_item_id:
            raise StorageIntegrityError(
                f"{table_name}.work_item_id must be unique"
            )
        records_by_work_item_id[record.work_item_id] = record
    return records_by_work_item_id


def _load_transitions(connection: sqlite3.Connection) -> tuple[TransitionRecord, ...]:
    rows = _transition_rows(connection)
    validate_transition_rows(rows)
    return tuple(transition_from_row(row) for row in rows)


def _transition_rows(connection: sqlite3.Connection) -> tuple[TransitionRow, ...]:
    return tuple(
        decode_transition_row(cast(tuple[object, ...], row))
        for row in connection.execute(
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
    )


def _load_governance_events(
    connection: sqlite3.Connection,
) -> tuple[GovernanceEventRecord, ...]:
    rows = tuple(
        decode_governance_event_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM governance_events
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    validate_audit_transition_rows(
        "governance_events",
        rows,
        _transition_rows_by_order(connection),
    )
    return tuple(governance_event_from_row(row) for row in rows)


def _load_traces(connection: sqlite3.Connection) -> tuple[TraceRecord, ...]:
    rows = tuple(
        decode_trace_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM traces
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    validate_audit_transition_rows(
        "traces",
        rows,
        _transition_rows_by_order(connection),
    )
    validate_trace_governance_rows(rows, _governance_rows_by_order(connection))
    return tuple(trace_from_row(row) for row in rows)


def _load_refusals(connection: sqlite3.Connection) -> tuple[TransitionRefusal, ...]:
    rows = tuple(
        decode_refusal_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
                record_id,
                transition_order,
                input_id,
                input_kind,
                input_family,
                reason,
                detail,
                created_at_order
            FROM refusals
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    validate_audit_transition_rows(
        "refusals",
        rows,
        _transition_rows_by_order(connection),
    )
    return tuple(refusal_from_row(row) for row in rows)


def _transition_rows_by_record_id(
    connection: sqlite3.Connection,
) -> dict[str, TransitionRow]:
    return {row.record_id: row for row in _transition_rows(connection)}


def _transition_rows_by_order(
    connection: sqlite3.Connection,
) -> dict[int, TransitionRow]:
    return {row.transition_order: row for row in _transition_rows(connection)}


def _governance_rows_by_order(
    connection: sqlite3.Connection,
) -> dict[int, GovernanceEventRow]:
    rows = tuple(
        decode_governance_event_row(cast(tuple[object, ...], row))
        for row in connection.execute(
            """
            SELECT
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
            FROM governance_events
            ORDER BY created_at_order
            """
        ).fetchall()
    )
    return {row.transition_order: row for row in rows}


def _load_selected_plan(
    cas_store: ContentAddressedByteStore,
    selected_plan_digest: str,
    *,
    reference_name: str,
) -> SelectedCompiledPlan:
    envelope = _load_cas_envelope(
        cas_store,
        selected_plan_digest,
        expected_object_kind=SELECTED_COMPILED_PLAN_OBJECT_KIND,
        reference_name=reference_name,
    )
    try:
        return decode_selected_compiled_plan(envelope)
    except SubstrateError as exc:
        _raise_malformed_reference(reference_name, selected_plan_digest, exc)


def _load_payload(
    cas_store: ContentAddressedByteStore,
    payload_digest: str,
    *,
    reference_name: str,
) -> Mapping[str, AuthorityValue]:
    envelope = _load_cas_envelope(
        cas_store,
        payload_digest,
        expected_object_kind=PAYLOAD_OBJECT_KIND,
        reference_name=reference_name,
    )
    try:
        return decode_payload(envelope)
    except SubstrateError as exc:
        _raise_malformed_reference(reference_name, payload_digest, exc)


def _load_artifact_payload(
    cas_store: ContentAddressedByteStore,
    payload_digest: str,
    *,
    reference_name: str,
) -> Mapping[str, AuthorityValue]:
    envelope = _load_cas_envelope(
        cas_store,
        payload_digest,
        expected_object_kind=ARTIFACT_PAYLOAD_OBJECT_KIND,
        reference_name=reference_name,
    )
    try:
        return decode_payload(
            envelope,
            expected_object_kind=ARTIFACT_PAYLOAD_OBJECT_KIND,
        )
    except SubstrateError as exc:
        _raise_malformed_reference(reference_name, payload_digest, exc)


def _load_cas_envelope(
    cas_store: ContentAddressedByteStore,
    digest: str,
    *,
    expected_object_kind: str,
    reference_name: str,
) -> CasObjectEnvelope:
    try:
        return loads_cas_object(
            cas_store.get_bytes(digest),
            expected_object_kind=expected_object_kind,
        )
    except SubstrateError as exc:
        _raise_cas_reference_integrity_error(reference_name, digest, exc)


def _raise_cas_reference_integrity_error(
    reference_name: str,
    digest: str,
    exc: SubstrateError,
) -> NoReturn:
    if isinstance(exc, CasObjectNotFound):
        detail = "references missing CAS object"
    elif isinstance(exc, CasDigestMismatch):
        detail = "references corrupt CAS object"
    elif isinstance(exc, InvalidCasDigest):
        raise StorageIntegrityError(
            f"{reference_name} is not a supported CAS digest: {digest}"
        ) from exc
    elif isinstance(exc, CasObjectKindMismatch):
        detail = "references wrong CAS object kind"
    else:
        detail = "references malformed CAS object"
    raise StorageIntegrityError(f"{reference_name} {detail}: {digest}") from exc


def _raise_malformed_reference(
    reference_name: str,
    digest: str,
    exc: SubstrateError,
) -> NoReturn:
    raise StorageIntegrityError(
        f"{reference_name} references malformed CAS object: {digest}"
    ) from exc


def _plan_ref_for_selected_plan(
    selected_plan: SelectedCompiledPlan,
    authority_fingerprint: str,
) -> PlanRef:
    return PlanRef(
        plan_id=(
            f"{selected_plan.workflow.workflow_id.value}:"
            f"{selected_plan.workflow.workflow_version.value}"
        ),
        authority_fingerprint=authority_fingerprint,
        plan_format_version=selected_plan.schema_version,
    )


def _external_enqueue_routes(
    selected_plan: SelectedCompiledPlan,
) -> Mapping[QueueFamilyId, ExternalEnqueueRoute]:
    return {
        route.queue_family_id: ExternalEnqueueRoute(
            queue_family_id=route.queue_family_id,
            graph_node_id=route.graph_node_id,
            stage_kind_id=route.stage_kind_id,
            runner_binding_id=route.runner_binding_id,
            payload_schema_id=route.payload_schema_id,
        )
        for route in selected_plan.external_enqueue_routes
    }
