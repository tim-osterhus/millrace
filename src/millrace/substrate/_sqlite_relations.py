"""SQLite runtime-state relationship validation for loaded and candidate states."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from hashlib import sha256
from typing import Protocol

import millrace.kernel._closure_lifecycle as _closure
from millrace.contracts import ActionId, ArtifactSchemaId, PartitionId, StageKindId
from millrace.contracts.compiled_plan import (
    ArtifactSchemaDeclaration,
    CompletionBehaviorDeclaration,
    EffectDeclaration,
    ExternalEnqueueRouteDeclaration,
    FanoutDeclaration,
    GeneratedWorkRouteDeclaration,
    OperatorWaitDeclaration,
    RecoveryPolicyDeclaration,
    RemediationPolicyDeclaration,
    SelectedCompiledPlan,
    StageContextBindingDeclaration,
    TerminalActionDeclaration,
)
from millrace.contracts.context_checkout import (
    context_checkout_manifest_digest,
    decode_context_checkout_manifest,
)
from millrace.contracts.operator_waits import _operator_wait_record_id
from millrace.contracts.runner import (
    RunnerResultEvidence,
    RunnerSessionCompletionDiagnostic,
    runner_result_evidence_digest,
    runner_result_evidence_from_payload,
)
from millrace.contracts.schema import validate_schema
from millrace.contracts.selected_plan_lookups import (
    terminal_action_for,
    terminal_outcome_for,
)
from millrace.contracts.state import (
    Activation,
    AdmittedPlan,
    ArtifactRecord,
    ClosedWorkItemRecord,
    ClosureBlockedRecord,
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    ClosureTerminalRecord,
    CooldownWaitRecord,
    EffectProposalRecord,
    EffectReconciliationRecord,
    FanoutRecord,
    InputReceipt,
    LineageQuarantineRecord,
    OperatorWaitRecord,
    PlanRef,
    RecoveryAttemptRecord,
    RemediationWorkRecord,
    RunnerSessionCancellationAttemptRecord,
    RunnerSessionCancellationRecord,
    RunnerSessionRecord,
    RunRecord,
    RuntimeState,
    TransitionRecord,
    WorkDependencyRecord,
    WorkItem,
)
from millrace.contracts.transition import (
    AttachRunnerSessionContext,
    CancelQueuedLineage,
    CancelQueuedWork,
    ClaimWork,
    EvaluateCompletionBehavior,
    OpenClosureTarget,
    ReconcileEffect,
    ResumeDispatch,
    RunnerResultObserved,
    SuspendDispatch,
    artifact_payload_digest,
    input_payload_digest,
    operator_payload_digest,
)
from millrace.kernel.fanout_policy import (
    PolicyAssessment,
    artifact_relevant_to_fanout,
    assess_fanout,
    fanout_items,
    fanout_target_payload,
    sorted_artifacts,
    source_context_for_artifact,
)
from millrace.kernel.join_policy import (
    assess_join_group,
    join_groups_for_declaration,
    join_target_route,
)
from millrace.kernel.observation_policy import (
    AuthenticatedArtifactProvenance,
    AuthenticatedRunnerObservation,
    authenticate_artifact_provenance,
    authenticate_runner_observation,
)
from millrace.substrate._sqlite_rows import (
    GovernanceEventRow,
    InputReceiptRow,
    RefusalRow,
    TraceRow,
    TransitionRow,
)
from millrace.substrate.errors import StorageIntegrityError, SubstrateError


class _CasByteReader(Protocol):
    def get_bytes(self, digest: str) -> bytes: ...

_TERMINAL_CLOSING_ACTION_KINDS = frozenset(
    (
        "close",
        "complete_work_item",
        "close_with_escalation",
        "block_work_item",
        "closure_gap",
    )
)

_SUPPORTED_SELECTED_TERMINAL_ACTION_KINDS = frozenset(
    (
        "route",
        "create_incident_route",
        "close",
        "complete_work_item",
        "close_with_escalation",
        "block_work_item",
        "pause_quarantine",
        "recovery_route",
        "return_to_recorded_source",
        "quarantine_lineage",
        "operator_wait",
        "closure_gap",
    )
)


def _validate_runner_session_relations(state: RuntimeState) -> None:
    terminal_states = {"completed", "interrupted", "failed", "lost"}
    sessions_by_run: dict[str, list[RunnerSessionRecord]] = {}
    seen_run_generations: set[tuple[str, int]] = set()
    seen_run_fencing_tokens: set[tuple[str, str]] = set()
    for session_id, session in state.runner_sessions.items():
        if session_id != session.session_id:
            raise StorageIntegrityError(
                "runner_sessions mapping key must match session_id"
            )
        run = state.runs.get(session.run_id)
        if run is None:
            raise StorageIntegrityError(
                "runner_sessions.run_id must reference runs"
            )
        run_generation = (session.run_id, session.dispatch_generation)
        if run_generation in seen_run_generations:
            raise StorageIntegrityError(
                "runner_sessions run dispatch generation must be unique"
            )
        seen_run_generations.add(run_generation)
        run_fencing_token = (
            session.run_id,
            session.session_fencing_token,
        )
        if run_fencing_token in seen_run_fencing_tokens:
            raise StorageIntegrityError(
                "runner_sessions same-run fencing token must be unique"
            )
        seen_run_fencing_tokens.add(run_fencing_token)
        sessions_by_run.setdefault(session.run_id, []).append(session)

    for run_id, run in state.runs.items():
        sessions = sessions_by_run.get(run_id, [])
        if not sessions:
            if (
                run.current_session_id is not None
                or run.last_dispatch_generation != 0
            ):
                raise StorageIntegrityError(
                    "runs runner session pointer requires durable session"
                )
            continue
        current_session = state.runner_sessions.get(run.current_session_id or "")
        if current_session is None or current_session.run_id != run_id:
            raise StorageIntegrityError(
                "runs.current_session_id must reference session for same run"
            )
        generations = sorted(
            session.dispatch_generation for session in sessions
        )
        if generations != list(range(1, len(sessions) + 1)):
            raise StorageIntegrityError(
                "runner session dispatch generation must be monotonic"
            )
        highest_generation = generations[-1]
        if (
            current_session.dispatch_generation != highest_generation
            or run.last_dispatch_generation != highest_generation
        ):
            raise StorageIntegrityError(
                "runs session pointer and dispatch generation must be current"
            )
        active = [
            session
            for session in sessions
            if session.state not in terminal_states
        ]
        if len(active) > 1:
            raise StorageIntegrityError(
                "run may have at most one nonterminal runner session"
            )
        if active and active[0].session_id != run.current_session_id:
            raise StorageIntegrityError(
                "nonterminal runner session must be current"
            )

    requests_by_session: dict[str, list[RunnerSessionCancellationRecord]] = {}
    for request_id, request in state.runner_session_cancellation_requests.items():
        if request_id != request.request_id:
            raise StorageIntegrityError(
                "runner session cancellation mapping key must match request_id"
            )
        request_session = state.runner_sessions.get(request.session_id)
        if request_session is None:
            raise StorageIntegrityError(
                "runner session cancellation request session is missing"
            )
        if request.dispatch_generation != request_session.dispatch_generation:
            raise StorageIntegrityError(
                "runner session cancellation dispatch generation mismatch"
            )
        latest_phase_at = max(
            timestamp
            for timestamp in (
                request_session.created_at,
                request_session.start_intent_at,
                request_session.started_at,
            )
            if timestamp is not None
        )
        if request.requested_at < latest_phase_at:
            raise StorageIntegrityError(
                "runner session cancellation request predates session phase"
            )
        if (
            request_session.ended_at is not None
            and request.requested_at > request_session.ended_at
        ):
            raise StorageIntegrityError(
                "runner session cancellation request exceeds session ended_at"
            )
        requests_by_session.setdefault(request.session_id, []).append(request)
    for requests in requests_by_session.values():
        ordered = sorted(requests, key=lambda record: record.request_order)
        if [record.request_order for record in ordered] != list(
            range(1, len(ordered) + 1)
        ):
            raise StorageIntegrityError(
                "runner session cancellation request order must be monotonic"
            )
        if not ordered[0].primary or any(
            record.primary for record in ordered[1:]
        ):
            raise StorageIntegrityError(
                "runner session cancellation primary request is contradictory"
            )
        if [record.requested_at for record in ordered] != sorted(
            record.requested_at for record in ordered
        ):
            raise StorageIntegrityError(
                "runner session cancellation timestamps must be monotonic"
            )
    cancellation_history_states = {
        "cancellation_requested",
        "terminating",
        *terminal_states,
    }
    for session in state.runner_sessions.values():
        requests = requests_by_session.get(session.session_id, [])
        if requests and session.state not in cancellation_history_states:
            raise StorageIntegrityError(
                "runner session cancellation history contradicts session state"
            )
        if (
            session.state in {"cancellation_requested", "terminating"}
            and not requests
        ):
            raise StorageIntegrityError(
                "active runner session cancellation requires primary request"
            )

    attempts_by_session: dict[str, list[RunnerSessionCancellationAttemptRecord]] = {}
    for attempt_id, attempt in state.runner_session_cancellation_attempts.items():
        if attempt_id != attempt.attempt_id:
            raise StorageIntegrityError(
                "runner session cancellation attempt key must match attempt_id"
            )
        attempt_session = state.runner_sessions.get(attempt.session_id)
        attempt_request = state.runner_session_cancellation_requests.get(
            attempt.request_id
        )
        if attempt_session is None:
            raise StorageIntegrityError(
                "runner session cancellation attempt session is missing"
            )
        if (
            attempt_request is None
            or attempt_request.session_id != attempt.session_id
            or not attempt_request.primary
        ):
            raise StorageIntegrityError(
                "runner session cancellation attempt requires primary request"
            )
        if attempt.started_at < attempt_request.requested_at:
            raise StorageIntegrityError(
                "runner session cancellation attempt predates request"
            )
        if (
            attempt_session.ended_at is not None
            and attempt.completed_at > attempt_session.ended_at
        ):
            raise StorageIntegrityError(
                "runner session cancellation attempt exceeds session ended_at"
            )
        attempts_by_session.setdefault(attempt.session_id, []).append(attempt)
    for attempts in attempts_by_session.values():
        ordered_attempts = sorted(
            attempts,
            key=lambda attempt_record: attempt_record.sequence,
        )
        if [record.sequence for record in ordered_attempts] != list(
            range(1, len(ordered_attempts) + 1)
        ):
            raise StorageIntegrityError(
                "runner session cancellation attempt order must be monotonic"
            )

    application_input_ids: set[str] = set()
    for session_id, completion in state.runner_session_completions.items():
        if session_id != completion.session_id:
            raise StorageIntegrityError(
                "runner session completion key must match session_id"
            )
        completion_session = state.runner_sessions.get(session_id)
        if completion_session is None:
            raise StorageIntegrityError(
                "runner session completion session is missing"
            )
        if (
            completion.run_id != completion_session.run_id
            or completion.dispatch_generation
            != completion_session.dispatch_generation
            or completion.session_fencing_token
            != completion_session.session_fencing_token
            or completion.terminal_state != completion_session.state
            or completion.started_at != completion_session.started_at
            or completion.completed_at != completion_session.ended_at
            or completion.cleanup_disposition
            != completion_session.cleanup_disposition
        ):
            raise StorageIntegrityError(
                "runner session completion started or terminal facts contradict session"
            )
        if completion.application_input_id in application_input_ids:
            raise StorageIntegrityError(
                "runner session completion application_input_id must be unique"
            )
        application_input_ids.add(completion.application_input_id)
        completion_request_id = completion.primary_cancellation_request_id
        completion_requests = requests_by_session.get(session_id, [])
        if completion.terminal_state == "interrupted" and completion_requests:
            primary_request = min(
                completion_requests,
                key=lambda request: request.request_order,
            )
            if (
                completion_request_id != primary_request.request_id
                or completion.cancel_requested_at
                != primary_request.requested_at
            ):
                raise StorageIntegrityError(
                    "interrupted runner session completion must link "
                    "primary cancellation request"
                )
        if completion_request_id is not None:
            completion_request = state.runner_session_cancellation_requests.get(
                completion_request_id
            )
            if (
                completion_request is None
                or completion_request.session_id != session_id
                or not completion_request.primary
                or completion.cancel_requested_at
                != completion_request.requested_at
            ):
                raise StorageIntegrityError(
                    "runner session completion cancellation link is invalid"
                )

    for session in state.runner_sessions.values():
        has_completion = session.session_id in state.runner_session_completions
        if (session.state in terminal_states) != has_completion:
            raise StorageIntegrityError(
                "runner session terminal state and completion must be paired"
            )


def runner_session_cas_references(
    state: RuntimeState,
    *,
    excluded_completion_session_id: str | None = None,
) -> tuple[tuple[str, str], ...]:
    references: list[tuple[str, str]] = []
    for session in state.runner_sessions.values():
        if session.durable_locator_digest is not None:
            references.append(("locator", session.durable_locator_digest))
    references.extend(
        (
            "attempt_diagnostic",
            attempt.bounded_diagnostic_digest,
        )
        for attempt in state.runner_session_cancellation_attempts.values()
    )
    for completion in state.runner_session_completions.values():
        if completion.session_id == excluded_completion_session_id:
            continue
        references.append(
            ("completion_diagnostic", completion.diagnostic_digest)
        )
        if completion.runner_result_evidence_digest is not None:
            references.append(
                (
                    "completed_evidence",
                    completion.runner_result_evidence_digest,
                )
            )
    return tuple(references)


def validate_runner_session_context_cas(
    state: RuntimeState,
    cas_store: _CasByteReader,
) -> None:
    for session in state.runner_sessions.values():
        run = state.runs.get(session.run_id)
        if run is None:
            continue
        admitted = state.admitted_plans.get(
            run.run_ref.plan_ref.authority_fingerprint
        )
        if admitted is None or admitted.plan_ref != run.run_ref.plan_ref:
            continue
        bindings = tuple(
            binding
            for binding in admitted.selected_plan.context_bindings
            if binding.stage_kind_id == run.stage_kind_id
        )
        if len(bindings) > 1:
            raise StorageIntegrityError(
                "runner session context binding is not unique"
            )
        context_digest = session.context_manifest_digest
        if not bindings:
            if context_digest is not None:
                raise StorageIntegrityError(
                    "unbound runner session cannot reference context manifest"
                )
            continue
        if context_digest is None:
            if (
                session.start_intent_at is not None
                or session.started_at is not None
            ):
                raise StorageIntegrityError(
                    "bound runner session must attach context before starting"
                )
            continue

        try:
            manifest_bytes = cas_store.get_bytes(context_digest)
            manifest = decode_context_checkout_manifest(manifest_bytes)
            if context_checkout_manifest_digest(manifest_bytes) != context_digest:
                raise ValueError("manifest digest mismatch")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise StorageIntegrityError(
                "runner session context manifest CAS reference is invalid: "
                f"{context_digest}"
            ) from exc
        except SubstrateError as exc:
            raise StorageIntegrityError(
                "runner session context manifest CAS reference is unavailable: "
                f"{context_digest}"
            ) from exc

        binding = bindings[0]
        if (
            manifest.session_id != session.session_id
            or manifest.dispatch_generation != session.dispatch_generation
            or manifest.plan_fingerprint != run.run_ref.plan_ref.authority_fingerprint
            or manifest.binding_id != str(binding.id)
            or manifest.router_asset_id != str(binding.router_asset_id)
        ):
            raise StorageIntegrityError(
                "runner session context manifest authority does not match"
            )
        for context_file in manifest.files:
            try:
                file_bytes = cas_store.get_bytes(context_file.content_digest)
            except SubstrateError as exc:
                raise StorageIntegrityError(
                    "runner session context file CAS reference is unavailable: "
                    f"{context_file.content_digest}"
                ) from exc
            if len(file_bytes) != context_file.byte_length:
                raise StorageIntegrityError(
                    "runner session context file byte length does not match: "
                    f"{context_file.checkout_path}"
                )
        _validate_runner_session_context_attach_anchor(
            state,
            run=run,
            session=session,
            binding=binding,
        )


def _validate_runner_session_context_attach_anchor(
    state: RuntimeState,
    *,
    run: RunRecord,
    session: RunnerSessionRecord,
    binding: StageContextBindingDeclaration,
) -> None:
    context_digest = session.context_manifest_digest
    assert context_digest is not None
    expected_plan_fingerprint = run.run_ref.plan_ref.authority_fingerprint
    for receipt in state.receipts.values():
        if not receipt.accepted:
            continue
        input_id = receipt.receipt_ref.input_id
        transition = next(
            (
                candidate
                for candidate in state.transitions
                if candidate.record_id == receipt.transition_id
            ),
            None,
        )
        if (
            transition is None
            or not transition.accepted
            or transition.input_id != input_id
            or transition.input_kind != AttachRunnerSessionContext.input_kind
            or transition.input_family != "workflow_kernel_command"
        ):
            continue
        events = tuple(
            event
            for event in state.governance_events
            if event.input_id == input_id
            and event.input_kind == AttachRunnerSessionContext.input_kind
            and event.input_family == "workflow_kernel_command"
            and event.disposition == "accepted"
            and event.plan_fingerprint == expected_plan_fingerprint
            and event.run_id == run.run_ref.run_id
            and event.authority_source == "run"
        )
        traces = tuple(
            trace
            for trace in state.traces
            if trace.input_id == input_id
            and trace.input_kind == AttachRunnerSessionContext.input_kind
            and trace.input_family == "workflow_kernel_command"
            and trace.disposition == "accepted"
            and trace.plan_fingerprint == expected_plan_fingerprint
            and trace.run_id == run.run_ref.run_id
            and trace.authority_source == "run"
        )
        if len(events) != 1 or len(traces) != 1:
            continue
        event = events[0]
        trace = traces[0]
        if (
            event.record_id != f"{transition.record_id}:governance"
            or trace.record_id != f"{transition.record_id}:trace"
        ):
            continue
        expected_input = AttachRunnerSessionContext(
            input_id,
            run_ref=run.run_ref,
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            session_fencing_token=session.session_fencing_token,
            context_manifest_digest=context_digest,
            selected_binding_id=str(binding.id),
        )
        if receipt.receipt_ref.input_payload_digest == input_payload_digest(
            expected_input
        ):
            return
    raise StorageIntegrityError(
        "runner session context attach audit anchor is missing or mismatched"
    )


def validate_completed_runner_evidence(
    state: RuntimeState,
    *,
    session_id: str,
    payload: bytes,
) -> None:
    try:
        raw = json.loads(payload)
        evidence = runner_result_evidence_from_payload(raw)
    except (
        RecursionError,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise StorageIntegrityError(
            "runner session completed evidence CAS object is malformed"
        ) from exc
    completion = state.runner_session_completions.get(session_id)
    session = state.runner_sessions.get(session_id)
    run = None if session is None else state.runs.get(session.run_id)
    activation = None if run is None else state.activations.get(run.activation_id)
    if (
        completion is None
        or session is None
        or run is None
        or activation is None
        or run.current_session_id != session_id
        or completion.runner_result_evidence_digest is None
        or runner_result_evidence_digest(evidence)
        != completion.runner_result_evidence_digest
        or evidence.run_id != run.run_ref.run_id
        or evidence.session_id != session.session_id
        or evidence.dispatch_generation != session.dispatch_generation
        or evidence.session_fencing_token != session.session_fencing_token
        or evidence.session_id != completion.session_id
        or evidence.dispatch_generation != completion.dispatch_generation
        or evidence.session_fencing_token != completion.session_fencing_token
        or evidence.plan_fingerprint
        != run.run_ref.plan_ref.authority_fingerprint
        or evidence.claim_id != run.run_ref.claim_id
        or evidence.generation != run.run_ref.generation
        or evidence.fencing_token != run.run_ref.fencing_token
        or evidence.stage_kind_id != str(run.stage_kind_id)
        or evidence.graph_node_id != activation.graph_node_id
        or evidence.runner_binding_id != str(run.runner_binding_id)
    ):
        raise StorageIntegrityError(
            "runner session completed evidence authority mismatch"
        )


def completion_diagnostic_matches_current_authority(
    state: RuntimeState,
    *,
    session_id: str,
    diagnostic: RunnerSessionCompletionDiagnostic,
) -> bool:
    session = state.runner_sessions.get(session_id)
    completion = state.runner_session_completions.get(session_id)
    run = None if session is None else state.runs.get(session.run_id)
    activation = None if run is None else state.activations.get(run.activation_id)
    if (
        session is None
        or completion is None
        or run is None
        or activation is None
        or run.current_session_id != session_id
        or completion.session_id != session_id
        or completion.run_id != run.run_ref.run_id
        or completion.dispatch_generation != session.dispatch_generation
        or completion.session_fencing_token != session.session_fencing_token
    ):
        return False
    return (
        diagnostic.run_id == run.run_ref.run_id
        and diagnostic.session_id == session.session_id
        and diagnostic.dispatch_generation == session.dispatch_generation
        and diagnostic.session_fencing_token == session.session_fencing_token
        and diagnostic.plan_fingerprint
        == run.run_ref.plan_ref.authority_fingerprint
        and diagnostic.claim_id == run.run_ref.claim_id
        and diagnostic.generation == run.run_ref.generation
        and diagnostic.fencing_token == run.run_ref.fencing_token
        and diagnostic.stage_kind_id == str(run.stage_kind_id)
        and diagnostic.graph_node_id == activation.graph_node_id
        and diagnostic.runner_binding_id == str(run.runner_binding_id)
    )


def runner_result_refusal_chain(
    state: RuntimeState,
    *,
    run_id: str,
    session_id: str,
    application_input_id: str,
    evidence: RunnerResultEvidence | None,
) -> tuple[bool, str | None]:
    session = state.runner_sessions.get(session_id)
    completion = state.runner_session_completions.get(session_id)
    run = None if session is None else state.runs.get(run_id)
    if (
        session is None
        or completion is None
        or run is None
        or run.current_session_id != session_id
        or completion.application_input_id != application_input_id
        or completion.run_id != run_id
    ):
        return False, None
    receipt = state.receipts.get(application_input_id)
    if (
        receipt is None
        or receipt.receipt_ref.input_id != application_input_id
        or receipt.accepted
        or receipt.refusal_reason is None
    ):
        return False, None
    transitions = tuple(
        transition
        for transition in state.transitions
        if transition.input_id == application_input_id
    )
    if len(transitions) != 1:
        return False, None
    transition = transitions[0]
    if (
        receipt.transition_id != transition.record_id
        or transition.input_kind != RunnerResultObserved.input_kind
        or transition.input_family != "workflow_observation"
        or transition.accepted
    ):
        return False, None

    if evidence is not None:
        if (
            evidence.run_id != run_id
            or evidence.session_id != session_id
            or evidence.dispatch_generation != session.dispatch_generation
            or evidence.session_fencing_token != session.session_fencing_token
        ):
            return False, None
        try:
            expected_digest = input_payload_digest(
                RunnerResultObserved(
                    application_input_id,
                    run_id=run_id,
                    payload=evidence.payload(),
                    observed_at=None,
                )
            )
        except (RecursionError, TypeError, ValueError):
            return False, None
        if receipt.receipt_ref.input_payload_digest != expected_digest:
            return False, None

    refusals = tuple(
        refusal
        for refusal in state.refusals
        if refusal.input_id == application_input_id
    )
    traces = tuple(
        trace for trace in state.traces if trace.input_id == application_input_id
    )
    events = tuple(
        event
        for event in state.governance_events
        if event.input_id == application_input_id
    )
    if len(refusals) != 1 or len(traces) != 1 or len(events) != 1:
        return False, None
    refusal = refusals[0]
    trace = traces[0]
    event = events[0]
    if (
        refusal.record_id != f"{transition.record_id}:refusal"
        or refusal.input_kind != transition.input_kind
        or refusal.input_family != transition.input_family
        or refusal.reason != receipt.refusal_reason
        or trace.record_id != f"{transition.record_id}:trace"
        or event.record_id != f"{transition.record_id}:governance"
    ):
        return False, None

    expected_plan_fingerprint = run.run_ref.plan_ref.authority_fingerprint
    expected_action_id = None
    expected_authority_source = None
    if evidence is not None:
        admitted = state.admitted_plans.get(expected_plan_fingerprint)
        if admitted is None:
            return False, None
        outcome = terminal_outcome_for(
            admitted.selected_plan,
            str(run.stage_kind_id),
            evidence.marker,
        )
        action = (
            None
            if outcome is None
            else terminal_action_for(
                admitted.selected_plan,
                str(run.stage_kind_id),
                str(outcome.id),
            )
        )
        if action is not None:
            expected_action_id = action.id
            expected_authority_source = "terminal_action"

    common_audit_fields = (
        application_input_id,
        transition.input_kind,
        transition.input_family,
        "refused",
        expected_plan_fingerprint,
        run.work_item_id,
        run_id,
        receipt.refusal_reason,
    )
    trace_common = (
        trace.input_id,
        trace.input_kind,
        trace.input_family,
        trace.disposition,
        trace.plan_fingerprint,
        trace.work_item_id,
        trace.run_id,
        trace.refusal_reason,
    )
    event_common = (
        event.input_id,
        event.input_kind,
        event.input_family,
        event.disposition,
        event.plan_fingerprint,
        event.work_item_id,
        event.run_id,
        event.refusal_reason,
    )
    if trace_common != common_audit_fields or event_common != common_audit_fields:
        return False, None
    if (
        trace.action_id != event.action_id
        or trace.authority_source != event.authority_source
    ):
        return False, None
    if evidence is not None and (
        trace.action_id != expected_action_id
        or trace.authority_source != expected_authority_source
    ):
        return False, None
    return True, receipt.refusal_reason


def _recovery_action_matches_policy_source(
    admitted_plan: AdmittedPlan,
    policy: RecoveryPolicyDeclaration,
    action_id: ActionId,
) -> bool:
    selected_plan = admitted_plan.selected_plan
    action = next(
        (
            action
            for action in selected_plan.terminal_actions
            if action.id == action_id
        ),
        None,
    )
    if (
        action is None
        or action.action_kind != "recovery_route"
        or action.target_stage_kind_id != policy.recovery_stage_kind_id
    ):
        return False
    if action_id in policy.source_recovery_action_ids:
        return True
    return any(
        counter.threshold_action_id == action_id
        and counter.increment_action_id in policy.source_recovery_action_ids
        for counter in selected_plan.counters
    )


def _validate_dispatch_suspension(state: RuntimeState) -> None:
    record = state.dispatch_suspension
    if record is None:
        return
    _validate_plan_ref(
        "dispatch_suspension",
        "dispatch_suspension.plan_authority_fingerprint",
        record.selected_plan_ref,
        state.admitted_plans,
    )
    expected_id = (
        "dispatch-suspension:"
        + sha256(record.suspended_by_input_id.encode("utf-8")).hexdigest()
    )
    if record.suspension_id != expected_id:
        raise StorageIntegrityError(
            "dispatch_suspension.suspension_id must match suspended input"
        )
    suspend_receipt, _suspend_transition = _validate_dispatch_control_input(
        state,
        input_id=record.suspended_by_input_id,
        expected_kind=SuspendDispatch.input_kind,
        field_name="dispatch_suspension.suspended_by_input_id",
    )
    expected_suspend_digest = input_payload_digest(
        SuspendDispatch(
            record.suspended_by_input_id,
            plan_fingerprint=record.selected_plan_ref.authority_fingerprint,
            actor_id=record.actor_id,
            reason=record.reason,
        )
    )
    if suspend_receipt.receipt_ref.input_payload_digest != expected_suspend_digest:
        raise StorageIntegrityError(
            "dispatch_suspension suspend input payload digest disagrees with record"
        )
    if record.status == "active" and record.dispatch_generation != len(state.runs):
        raise StorageIntegrityError(
            "active dispatch_suspension dispatch generation is stale"
        )

    dispatch_transitions = tuple(
        (order, transition)
        for order, transition in enumerate(state.transitions)
        if transition.accepted
        and transition.input_kind
        in {SuspendDispatch.input_kind, ResumeDispatch.input_kind}
    )
    active_suspension_transition: tuple[int, TransitionRecord] | None = None
    completed_lifecycle_count = 0
    for order, transition in dispatch_transitions:
        if transition.input_kind == SuspendDispatch.input_kind:
            if active_suspension_transition is not None:
                raise StorageIntegrityError(
                    "dispatch_suspension accepted lifecycle has consecutive suspends"
                )
            active_suspension_transition = (order, transition)
            continue
        if active_suspension_transition is None:
            raise StorageIntegrityError(
                "dispatch_suspension accepted lifecycle resumes without suspension"
            )
        completed_lifecycle_count += 1
        active_suspension_transition = None

    expected_generation = completed_lifecycle_count + (
        1 if active_suspension_transition is not None else 0
    )
    if record.generation != expected_generation:
        raise StorageIntegrityError(
            "dispatch_suspension.generation disagrees with accepted lifecycle"
        )
    if active_suspension_transition is None:
        if record.status != "resumed":
            raise StorageIntegrityError(
                "dispatch_suspension status disagrees with accepted lifecycle"
            )
    elif (
        record.status != "active"
        or record.suspended_by_input_id != active_suspension_transition[1].input_id
    ):
        raise StorageIntegrityError(
            "dispatch_suspension active record disagrees with accepted lifecycle"
        )

    suspend_transition_order = next(
        order
        for order, transition in dispatch_transitions
        if transition.input_id == record.suspended_by_input_id
    )
    expected_dispatch_generation = sum(
        transition.accepted and transition.input_kind == ClaimWork.input_kind
        for transition in state.transitions[:suspend_transition_order]
    )
    if record.dispatch_generation != expected_dispatch_generation:
        raise StorageIntegrityError(
            "dispatch_suspension.dispatch_generation disagrees with accepted claims"
        )
    if record.status == "resumed":
        assert record.resumed_by_input_id is not None
        assert record.resume_actor_id is not None
        assert record.resume_reason is not None
        resume_receipt, _resume_transition = _validate_dispatch_control_input(
            state,
            input_id=record.resumed_by_input_id,
            expected_kind=ResumeDispatch.input_kind,
            field_name="dispatch_suspension.resumed_by_input_id",
        )
        expected_resume_digest = input_payload_digest(
            ResumeDispatch(
                record.resumed_by_input_id,
                plan_fingerprint=record.selected_plan_ref.authority_fingerprint,
                suspension_id=record.suspension_id,
                actor_id=record.resume_actor_id,
                reason=record.resume_reason,
            )
        )
        if resume_receipt.receipt_ref.input_payload_digest != expected_resume_digest:
            raise StorageIntegrityError(
                "dispatch_suspension resume input payload digest disagrees with record"
            )
        if not dispatch_transitions or (
            dispatch_transitions[-1][1].input_id != record.resumed_by_input_id
        ):
            raise StorageIntegrityError(
                "dispatch_suspension resumed record disagrees with accepted lifecycle"
            )


def _validate_queue_closures(state: RuntimeState) -> None:
    accepted_inputs = {
        transition.input_id
        for transition in state.transitions
        if transition.accepted
        and transition.input_kind
        in {CancelQueuedWork.input_kind, CancelQueuedLineage.input_kind}
    }
    if accepted_inputs != {
        record.created_by_input_id for record in state.queue_closures.values()
    }:
        raise StorageIntegrityError(
            "queue_closures must match accepted queue closure transitions"
        )

    for record in state.queue_closures.values():
        _validate_plan_ref(
            "queue_closures",
            "queue_closures.plan_authority_fingerprint",
            record.selected_plan_ref,
            state.admitted_plans,
        )
        expected_id = (
            "queue-closure:"
            + sha256(record.created_by_input_id.encode("utf-8")).hexdigest()
        )
        if record.closure_id != expected_id:
            raise StorageIntegrityError(
                "queue_closures.closure_id must match created input"
            )
        transition_type = (
            CancelQueuedWork
            if record.target_kind == "work_item"
            else CancelQueuedLineage
        )
        receipt, _transition = _validate_dispatch_control_input(
            state,
            input_id=record.created_by_input_id,
            expected_kind=transition_type.input_kind,
            field_name="queue_closures.created_by_input_id",
        )
        if record.target_kind == "work_item":
            expected_input: CancelQueuedWork | CancelQueuedLineage = CancelQueuedWork(
                record.created_by_input_id,
                work_item_id=record.target_id,
                plan_fingerprint=record.selected_plan_ref.authority_fingerprint,
                actor_id=record.actor_id,
                reason=record.reason,
            )
            target_work_item = state.work_items.get(record.target_id)
            members: tuple[WorkItem, ...] = (
                () if target_work_item is None else (target_work_item,)
            )
        else:
            expected_input = CancelQueuedLineage(
                record.created_by_input_id,
                lineage_id=record.target_id,
                plan_fingerprint=record.selected_plan_ref.authority_fingerprint,
                actor_id=record.actor_id,
                reason=record.reason,
            )
            members = tuple(
                work_item
                for work_item in state.work_items.values()
                if work_item.lineage_id == record.target_id
            )
        if receipt.receipt_ref.input_payload_digest != input_payload_digest(
            expected_input
        ):
            raise StorageIntegrityError(
                "queue_closures input payload digest disagrees with record"
            )
        if not members:
            raise StorageIntegrityError("queue_closures target work is missing")
        work_items = members
        expected_work_ids = tuple(
            sorted(work_item.ref.work_item_id for work_item in work_items)
        )
        if (
            not expected_work_ids
            or expected_work_ids != record.closed_work_item_ids
            or any(
                work_item.ref.plan_ref != record.selected_plan_ref
                for work_item in work_items
            )
        ):
            raise StorageIntegrityError(
                "queue_closures closed work membership or plan pin is invalid"
            )
        expected_activation_ids = tuple(
            sorted(
                activation.activation_id
                for activation in state.activations.values()
                if activation.work_item_id in expected_work_ids
            )
        )
        expected_run_ids = tuple(
            sorted(
                run.run_ref.run_id
                for run in state.runs.values()
                if run.work_item_id in expected_work_ids
            )
        )
        if (
            record.closed_activation_ids != expected_activation_ids
            or record.closed_run_ids != expected_run_ids
        ):
            raise StorageIntegrityError(
                "queue_closures activation or run membership is invalid"
            )
        for work_item_id in expected_work_ids:
            closed = state.closed_work_items.get(work_item_id)
            if (
                closed is None
                or closed.close_kind != "queue_cancellation"
                or closed.created_by_input_id != record.created_by_input_id
            ):
                raise StorageIntegrityError(
                    "queue_closures must own every closed work record"
                )
        sessions = tuple(
            session
            for session in state.runner_sessions.values()
            if session.run_id in expected_run_ids
        )
        if any(
            session.state == "lost"
            or session.cleanup_disposition in {"pending", "orphan_risk"}
            or session.state
            in {
                "created",
                "starting",
                "running",
                "cancellation_requested",
                "terminating",
            }
            for session in sessions
        ):
            raise StorageIntegrityError(
                "queue_closures cannot retain live or unresolved runner aftermath"
            )
        sessions_by_id = {session.session_id for session in sessions}
        for run_id in expected_run_ids:
            run = state.runs[run_id]
            if (
                run.current_session_id is None
                and not any(
                    observation.run_id == run_id
                    for observation in state.runner_observations.values()
                )
            ) or (
                run.current_session_id is not None
                and run.current_session_id not in sessions_by_id
            ):
                raise StorageIntegrityError(
                    "queue_closures cannot retain an unresolved accepted claim"
                )


def _validate_dispatch_control_input(
    state: RuntimeState,
    *,
    input_id: str,
    expected_kind: str,
    field_name: str,
) -> tuple[InputReceipt, TransitionRecord]:
    receipt = state.receipts.get(input_id)
    if receipt is None or not receipt.accepted:
        raise StorageIntegrityError(
            f"{field_name} must reference an accepted input receipt"
        )
    transition = next(
        (
            candidate
            for candidate in state.transitions
            if candidate.record_id == receipt.transition_id
        ),
        None,
    )
    if (
        transition is None
        or not transition.accepted
        or transition.input_id != input_id
        or transition.input_kind != expected_kind
        or transition.input_family != "workflow_operator_command"
    ):
        raise StorageIntegrityError(
            f"{field_name} must reference an accepted dispatch transition"
        )
    return receipt, transition


def validate_loaded_runtime_state(state: RuntimeState) -> None:
    _validate_selected_terminal_action_authority(state)
    _validate_selected_enqueue_route_authority(state)
    _validate_selected_join_declaration_authority(state)
    _validate_selected_concurrency_policy_authority(state)
    _validate_selected_operator_wait_authority(state)

    work_item_ids = frozenset(state.work_items)
    activation_ids = frozenset(state.activations)
    run_ids = frozenset(state.runs)
    transition_by_id = {record.record_id: record for record in state.transitions}
    transition_ids = frozenset(transition_by_id)

    if state.default_plan_ref is not None:
        _validate_plan_ref(
            "default_plan",
            "default_plan.authority_fingerprint",
            state.default_plan_ref,
            state.admitted_plans,
        )

    for work_item in state.work_items.values():
        _validate_plan_ref(
            "work_items",
            "work_items.plan_authority_fingerprint",
            work_item.ref.plan_ref,
            state.admitted_plans,
        )

    _validate_operator_wait_resume_target_selected_authority(state)

    for activation in state.activations.values():
        _validate_reference(
            "activations.work_item_id",
            activation.work_item_id,
            work_item_ids,
            "work_items",
        )
        _validate_plan_ref(
            "activations",
            "activations.plan_authority_fingerprint",
            activation.plan_ref,
            state.admitted_plans,
        )
        work_item = state.work_items[activation.work_item_id]
        if activation.lineage_id != work_item.lineage_id:
            raise StorageIntegrityError(
                "activations.lineage_id must match work_items.lineage_id"
            )
        if activation.plan_ref != work_item.ref.plan_ref:
            raise StorageIntegrityError(
                "activations PlanRef must match work_items PlanRef"
            )
        if activation.queue_family_id != work_item.queue_family_id:
            raise StorageIntegrityError(
                "activations.queue_family_id must match work_items.queue_family_id"
            )
        if activation.claimed_by_run_id is not None:
            _validate_reference(
                "activations.claimed_by_run_id",
                activation.claimed_by_run_id,
                run_ids,
                "runs",
            )

    for run in state.runs.values():
        _validate_reference(
            "runs.activation_id",
            run.activation_id,
            activation_ids,
            "activations",
        )
        _validate_reference(
            "runs.work_item_id",
            run.work_item_id,
            work_item_ids,
            "work_items",
        )
        _validate_plan_ref(
            "runs",
            "runs.plan_authority_fingerprint",
            run.run_ref.plan_ref,
            state.admitted_plans,
        )
        activation = state.activations[run.activation_id]
        if run.run_ref.work_item_id != run.work_item_id:
            raise StorageIntegrityError(
                "runs.run_ref.work_item_id must match runs.work_item_id"
            )
        if run.work_item_id != activation.work_item_id:
            raise StorageIntegrityError(
                "runs.work_item_id must match activation work_item_id"
            )
        if run.run_ref.plan_ref != activation.plan_ref:
            raise StorageIntegrityError(
                "runs PlanRef must match activations PlanRef"
            )
        if run.stage_kind_id != activation.stage_kind_id:
            raise StorageIntegrityError(
                "runs.stage_kind_id must match activations.stage_kind_id"
            )
        if run.runner_binding_id != activation.runner_binding_id:
            raise StorageIntegrityError(
                "runs.runner_binding_id must match activations.runner_binding_id"
            )

    _validate_runner_session_relations(state)
    _validate_dispatch_suspension(state)
    _validate_queue_closures(state)

    _validate_unique(
        "runs.claim_id",
        (run.run_ref.claim_id for run in state.runs.values()),
    )
    _validate_unique(
        "runs.activation_id",
        (run.activation_id for run in state.runs.values()),
    )

    for activation in state.activations.values():
        if activation.claimed_by_run_id is None:
            continue
        claimed_run = state.runs[activation.claimed_by_run_id]
        if claimed_run.activation_id != activation.activation_id:
            raise StorageIntegrityError(
                "activations.claimed_by_run_id must reference run for activation"
            )

    _validate_concurrency_policy_state(state)

    for observation in state.runner_observations.values():
        _validate_reference(
            "runner_observations.run_id",
            observation.run_id,
            run_ids,
            "runs",
        )
        authenticated = authenticate_runner_observation(state, observation)
        if not isinstance(authenticated, AuthenticatedRunnerObservation):
            detail = (
                f":{authenticated.detail}"
                if authenticated.detail is not None
                else ""
            )
            raise StorageIntegrityError(
                "runner_observations accepted-input authority invalid: "
                f"{authenticated.reason_code}{detail}"
            )
    _validate_unique(
        "runner_observations.run_id",
        (observation.run_id for observation in state.runner_observations.values()),
    )

    for artifact in state.artifacts.values():
        _validate_reference(
            "artifacts.work_item_id",
            artifact.work_item_id,
            work_item_ids,
            "work_items",
        )
        _validate_artifact_source_context(
            state,
            artifact=artifact,
        )

    _validate_effect_records(
        state,
        work_item_ids=work_item_ids,
        activation_ids=activation_ids,
        run_ids=run_ids,
        transition_by_id=transition_by_id,
    )

    for route in state.activation_routes:
        _validate_reference(
            "activation_routes.source_run_id",
            route.source_run_id,
            run_ids,
            "runs",
        )
        _validate_reference(
            "activation_routes.source_work_item_id",
            route.source_work_item_id,
            work_item_ids,
            "work_items",
        )
        _validate_reference(
            "activation_routes.target_work_item_id",
            route.target_work_item_id,
            work_item_ids,
            "work_items",
        )
        _validate_reference(
            "activation_routes.target_activation_id",
            route.target_activation_id,
            activation_ids,
            "activations",
        )
        source_run = state.runs[route.source_run_id]
        if route.source_work_item_id != source_run.work_item_id:
            raise StorageIntegrityError(
                "activation_routes.source_work_item_id must match source_run_id"
            )
        target_activation = state.activations[route.target_activation_id]
        if route.target_work_item_id != target_activation.work_item_id:
            raise StorageIntegrityError(
                "activation_routes.target_work_item_id must match "
                "target_activation_id"
            )
    _validate_fanout_records(
        state,
        work_item_ids=work_item_ids,
        activation_ids=activation_ids,
        run_ids=run_ids,
    )
    _validate_work_dependencies(state, work_item_ids=work_item_ids)
    _validate_selected_lifecycle_aftermath(state)
    _validate_closure_records(
        state,
        work_item_ids=work_item_ids,
        activation_ids=activation_ids,
        run_ids=run_ids,
    )

    for closed in state.closed_work_items.values():
        if closed.close_kind == "terminal_action":
            if closed.source_run_id is None or closed.action_id is None:
                raise StorageIntegrityError(
                    "closed_work_items terminal_action close requires source run"
                )
            _validate_run_and_work_item_record(
                "closed_work_items",
                closed.source_run_id,
                closed.work_item_id,
                state.runs,
                state.work_items,
            )
            _validate_terminal_action_closed_work_item(state, closed)
        elif closed.close_kind == "operator_intervention":
            _validate_reference(
                "closed_work_items.work_item_id",
                closed.work_item_id,
                work_item_ids,
                "work_items",
            )
            if closed.operator_intervention_record_id is None:
                raise StorageIntegrityError(
                    "closed_work_items.operator_intervention_record_id "
                    "must reference operator_interventions"
                )
            _validate_reference(
                "closed_work_items.operator_intervention_record_id",
                closed.operator_intervention_record_id,
                frozenset(state.operator_interventions),
                "operator_interventions",
            )
        elif closed.close_kind == "queue_cancellation":
            _validate_reference(
                "closed_work_items.work_item_id",
                closed.work_item_id,
                work_item_ids,
                "work_items",
            )
            if (
                closed.source_run_id is not None
                or closed.action_id is not None
                or closed.operator_intervention_record_id is not None
            ):
                raise StorageIntegrityError(
                    "queue-cancelled work cannot have runner, action, "
                    "or intervention provenance"
                )
        else:
            raise StorageIntegrityError("closed_work_items.close_kind is unsupported")

    if state.pause is not None:
        _validate_run_and_work_item_record(
            "pause_state",
            state.pause.source_run_id,
            state.pause.work_item_id,
            state.runs,
            state.work_items,
        )

    for quarantine in state.quarantines.values():
        _validate_run_and_work_item_record(
            "quarantine_records",
            quarantine.source_run_id,
            quarantine.work_item_id,
            state.runs,
            state.work_items,
        )

    _validate_recovery_attempts(
        state,
        work_item_ids=work_item_ids,
        activation_ids=activation_ids,
        run_ids=run_ids,
    )
    _validate_lineage_quarantines(
        state,
        work_item_ids=work_item_ids,
        activation_ids=activation_ids,
        run_ids=run_ids,
    )
    _validate_cooldown_waits(
        state,
        work_item_ids=work_item_ids,
        activation_ids=activation_ids,
        run_ids=run_ids,
    )
    _validate_operator_waits(
        state,
        work_item_ids=work_item_ids,
        activation_ids=activation_ids,
        run_ids=run_ids,
    )
    _validate_counters(state)
    _validate_operator_interventions(
        state,
        work_item_ids=work_item_ids,
        activation_ids=activation_ids,
        run_ids=run_ids,
    )

    for receipt in state.receipts.values():
        _validate_reference(
            "input_receipts.transition_id",
            receipt.transition_id,
            transition_ids,
            "transitions",
        )


def _validate_selected_terminal_action_authority(state: RuntimeState) -> None:
    for admitted in state.admitted_plans.values():
        selected_plan = admitted.selected_plan
        for action in admitted.selected_plan.terminal_actions:
            _validate_selected_terminal_action_kind(action)
            _validate_close_with_escalation_action_authority(action)
            _validate_terminal_action_artifact_authority(selected_plan, action)
            _validate_static_route_action_authority(selected_plan, action)


def _validate_selected_terminal_action_kind(
    action: TerminalActionDeclaration,
) -> None:
    if action.action_kind in _SUPPORTED_SELECTED_TERMINAL_ACTION_KINDS:
        return
    raise StorageIntegrityError("selected terminal action kind is unsupported")


def _validate_close_with_escalation_action_authority(
    action: TerminalActionDeclaration,
) -> None:
    if action.action_kind != "close_with_escalation":
        return
    for value in (
        action.target_stage_kind_id,
        action.target_graph_node_id,
        action.emitted_queue_family_id,
        action.runner_binding_id,
        action.payload_projection,
        action.dynamic_target_selector,
    ):
        if value is not None:
            raise StorageIntegrityError(
                "close_with_escalation selected action cannot carry route authority"
            )


def _validate_terminal_action_artifact_authority(
    selected_plan: SelectedCompiledPlan,
    action: TerminalActionDeclaration,
) -> None:
    if (
        action.action_kind in {"route", "create_incident_route"}
        or action.artifact_schema_id is None
    ):
        return
    stage_by_id = {stage.id: stage for stage in selected_plan.stage_kinds}
    source_stage = stage_by_id.get(action.stage_kind_id)
    if (
        source_stage is None
        or action.artifact_schema_id not in source_stage.artifact_schema_ids
    ):
        raise StorageIntegrityError(
            "selected terminal action artifact_schema_id must be declared by "
            "source stage"
        )


def _known_graph_node_stage_owner(
    selected_plan: SelectedCompiledPlan,
) -> Mapping[str, StageKindId]:
    owners: dict[str, StageKindId] = {}
    for route in selected_plan.external_enqueue_routes:
        owners.setdefault(route.graph_node_id, route.stage_kind_id)
    for generated_route in selected_plan.generated_work_routes:
        owners.setdefault(
            generated_route.graph_node_id,
            generated_route.stage_kind_id,
        )
    for behavior in selected_plan.completion_behaviors:
        owners.setdefault(behavior.target_graph_node_id, behavior.target_stage_kind_id)
    for policy in selected_plan.remediation_policies:
        owners.setdefault(policy.target_graph_node_id, policy.target_stage_kind_id)
    for action in selected_plan.terminal_actions:
        if action.action_kind == "recovery_route":
            continue
        if (
            action.target_graph_node_id is not None
            and action.target_stage_kind_id is not None
        ):
            owners.setdefault(action.target_graph_node_id, action.target_stage_kind_id)
    return owners


def _validate_static_route_action_authority(
    selected_plan: SelectedCompiledPlan,
    action: TerminalActionDeclaration,
) -> None:
    if action.action_kind not in {"route", "create_incident_route"}:
        return
    graph_node_ids = {
        node_id for graph in selected_plan.graphs for node_id in graph.node_ids
    }
    graph_node_stage_owner = _known_graph_node_stage_owner(selected_plan)
    stage_by_id = {stage.id: stage for stage in selected_plan.stage_kinds}
    runner_by_id = {runner.id: runner for runner in selected_plan.runner_bindings}
    source_stage = stage_by_id.get(action.stage_kind_id)
    target_stage = (
        None
        if action.target_stage_kind_id is None
        else stage_by_id.get(action.target_stage_kind_id)
    )
    runner = (
        None
        if action.runner_binding_id is None
        else runner_by_id.get(action.runner_binding_id)
    )
    if source_stage is None:
        raise StorageIntegrityError(
            "selected terminal route source stage_kind_id must reference stage_kinds"
        )
    if target_stage is None:
        raise StorageIntegrityError(
            "selected terminal route target_stage_kind_id must reference stage_kinds"
        )
    if (
        action.target_graph_node_id is None
        or action.target_graph_node_id not in graph_node_ids
    ):
        raise StorageIntegrityError(
            "selected terminal route target_graph_node_id must reference graphs"
        )
    known_stage_for_node = graph_node_stage_owner.get(action.target_graph_node_id)
    if (
        known_stage_for_node is not None
        and known_stage_for_node != action.target_stage_kind_id
    ):
        raise StorageIntegrityError(
            "selected terminal route target_graph_node_id must belong to target stage"
        )
    if action.emitted_queue_family_id not in source_stage.output_queue_family_ids:
        raise StorageIntegrityError(
            "selected terminal route emitted_queue_family_id must be a source "
            "stage output queue"
        )
    if action.emitted_queue_family_id not in target_stage.input_queue_family_ids:
        raise StorageIntegrityError(
            "selected terminal route emitted_queue_family_id must be a target "
            "stage input queue"
        )
    if runner is None:
        raise StorageIntegrityError(
            "selected terminal route runner_binding_id must reference "
            "runner_bindings"
        )
    if target_stage.runner_binding_id != action.runner_binding_id:
        raise StorageIntegrityError(
            "selected terminal route runner_binding_id must match target stage"
        )
    if action.target_stage_kind_id not in runner.stage_kind_ids:
        raise StorageIntegrityError(
            "selected terminal route runner_binding_id must list target stage"
        )
    if (
        action.artifact_schema_id is None
        or action.artifact_schema_id not in source_stage.artifact_schema_ids
        or action.artifact_schema_id not in target_stage.artifact_schema_ids
    ):
        raise StorageIntegrityError(
            "selected terminal route artifact_schema_id must be declared by "
            "source and target stages"
        )


def _validate_selected_enqueue_route_authority(state: RuntimeState) -> None:
    for admitted in state.admitted_plans.values():
        selected_plan = admitted.selected_plan
        queue_family_ids = {family.id for family in selected_plan.queue_families}
        graph_node_ids = {
            node_id for graph in selected_plan.graphs for node_id in graph.node_ids
        }
        stage_by_id = {stage.id: stage for stage in selected_plan.stage_kinds}
        runner_by_id = {runner.id: runner for runner in selected_plan.runner_bindings}
        schema_ids = {schema.id for schema in selected_plan.artifact_schemas}
        seen_route_ids: set[str] = set()
        routes: tuple[
            ExternalEnqueueRouteDeclaration | GeneratedWorkRouteDeclaration,
            ...,
        ] = (
            *selected_plan.external_enqueue_routes,
            *selected_plan.generated_work_routes,
        )
        for route in routes:
            if route.id in seen_route_ids:
                raise StorageIntegrityError(
                    "selected enqueue route id must be unique"
                )
            seen_route_ids.add(route.id)
            if route.queue_family_id not in queue_family_ids:
                raise StorageIntegrityError(
                    "selected enqueue route queue_family_id must reference "
                    "queue_families"
                )
            if route.graph_node_id not in graph_node_ids:
                raise StorageIntegrityError(
                    "selected enqueue route graph_node_id must reference graphs"
                )
            stage = stage_by_id.get(route.stage_kind_id)
            if stage is None:
                raise StorageIntegrityError(
                    "selected enqueue route stage_kind_id must reference stage_kinds"
                )
            runner = runner_by_id.get(route.runner_binding_id)
            if runner is None:
                raise StorageIntegrityError(
                    "selected enqueue route runner_binding_id must reference "
                    "runner_bindings"
                )
            if route.queue_family_id not in stage.input_queue_family_ids:
                raise StorageIntegrityError(
                    "selected enqueue route queue_family_id must be a target stage "
                    "input queue"
                )
            if stage.runner_binding_id != route.runner_binding_id:
                raise StorageIntegrityError(
                    "selected enqueue route runner_binding_id must match target "
                    "stage runner_binding_id"
                )
            if route.stage_kind_id not in runner.stage_kind_ids:
                raise StorageIntegrityError(
                    "selected enqueue route runner_binding_id must list target "
                    "stage_kind_id"
                )
            if (
                route.payload_schema_id is not None
                and route.payload_schema_id not in schema_ids
            ):
                raise StorageIntegrityError(
                    "selected enqueue route payload_schema_id must reference "
                    "artifact_schemas"
                )


def _validate_selected_concurrency_policy_authority(state: RuntimeState) -> None:
    for admitted in state.admitted_plans.values():
        selected_plan = admitted.selected_plan
        partition_ids = {partition.id for partition in selected_plan.partitions}
        policies_by_partition: dict[PartitionId, str] = {}
        for policy in selected_plan.concurrency_policies:
            if policy.partition_id not in partition_ids:
                raise StorageIntegrityError(
                    "selected concurrency policy partition_id must reference "
                    "partitions"
                )
            if policy.partition_id in policies_by_partition:
                raise StorageIntegrityError(
                    "selected concurrency policy partition_id must be unique"
                )
            policies_by_partition[policy.partition_id] = policy.id
            if policy.max_active_runs <= 0:
                raise StorageIntegrityError(
                    "selected concurrency policy max_active_runs must be positive"
                )
            if len(policy.coexist_partition_ids) != len(
                set(policy.coexist_partition_ids)
            ):
                raise StorageIntegrityError(
                    "selected concurrency policy coexist_partition_ids must be "
                    "unique"
                )
            if policy.partition_id in policy.coexist_partition_ids:
                raise StorageIntegrityError(
                    "selected concurrency policy self coexist is unsupported"
                )
            for peer_partition_id in policy.coexist_partition_ids:
                if peer_partition_id not in partition_ids:
                    raise StorageIntegrityError(
                        "selected concurrency policy coexist_partition_ids must "
                        "reference partitions"
                    )

        policy_by_partition = {
            policy.partition_id: policy
            for policy in selected_plan.concurrency_policies
        }
        for policy in selected_plan.concurrency_policies:
            for peer_partition_id in policy.coexist_partition_ids:
                peer_policy = policy_by_partition.get(peer_partition_id)
                if (
                    peer_policy is not None
                    and policy.partition_id not in peer_policy.coexist_partition_ids
                ):
                    raise StorageIntegrityError(
                        "selected concurrency policy coexist_partition_ids must be "
                        "symmetric"
                    )


def _validate_selected_join_declaration_authority(state: RuntimeState) -> None:
    for admitted in state.admitted_plans.values():
        selected_plan = admitted.selected_plan
        stage_by_id = {stage.id: stage for stage in selected_plan.stage_kinds}
        schema_ids = {schema.id for schema in selected_plan.artifact_schemas}
        terminal_action_ids = {
            str(action.id) for action in selected_plan.terminal_actions
        }
        schema_property_ids = _selected_artifact_schema_property_ids(
            selected_plan.artifact_schemas
        )
        seen_join_ids: set[str] = set()
        for join in selected_plan.join_declarations:
            if not join.id or join.id in seen_join_ids:
                raise StorageIntegrityError("selected join declaration is invalid")
            seen_join_ids.add(join.id)
            if str(join.id) in terminal_action_ids:
                raise StorageIntegrityError("selected join declaration is invalid")
            target_stage = stage_by_id.get(join.target_stage_kind_id)
            if target_stage is None:
                raise StorageIntegrityError("selected join declaration is invalid")
            if join_target_route(selected_plan, join) is None:
                raise StorageIntegrityError("selected join declaration is invalid")
            if not join.correlation_key or join.missing_policy != "wait":
                raise StorageIntegrityError("selected join declaration is invalid")
            if not join.required_artifact_schema_ids or len(
                join.required_artifact_schema_ids
            ) != len(set(join.required_artifact_schema_ids)):
                raise StorageIntegrityError("selected join declaration is invalid")
            for schema_id in join.required_artifact_schema_ids:
                if (
                    schema_id not in schema_ids
                    or schema_id not in target_stage.artifact_schema_ids
                    or join.correlation_key
                    not in schema_property_ids.get(schema_id, frozenset())
                ):
                    raise StorageIntegrityError(
                        "selected join declaration is invalid"
        )


def _validate_selected_lifecycle_aftermath(state: RuntimeState) -> None:
    artifacts = sorted_artifacts(state)
    for plan_fingerprint, admitted in sorted(state.admitted_plans.items()):
        selected_plan = admitted.selected_plan
        for fanout in selected_plan.fanout_declarations:
            for artifact in artifacts:
                if not artifact_relevant_to_fanout(artifact, fanout):
                    continue
                source_context = source_context_for_artifact(state, artifact)
                if isinstance(source_context, PolicyAssessment):
                    raise StorageIntegrityError(
                        "selected fanout source evidence is invalid"
                    )
                if (
                    source_context.run.run_ref.plan_ref.authority_fingerprint
                    != plan_fingerprint
                ):
                    continue
                assessment = assess_fanout(state, source_context, fanout)
                if assessment.status == "partial_or_corrupt":
                    raise StorageIntegrityError(
                        "selected fanout aftermath is partial or corrupt"
                    )
        for join in selected_plan.join_declarations:
            groups = join_groups_for_declaration(
                state,
                selected_plan=selected_plan,
                plan_fingerprint=plan_fingerprint,
                join=join,
            )
            if isinstance(groups, PolicyAssessment):
                if groups.detail == "route_action":
                    raise StorageIntegrityError(
                        "join-created activation route action must match selected join"
                    )
                if groups.detail == "route_creator":
                    raise StorageIntegrityError(
                        "join-created activation route must reference join transition"
                    )
                raise StorageIntegrityError(
                    "selected join transition or evidence is partial or corrupt"
                )
            for group in groups:
                assessment = assess_join_group(
                    state,
                    selected_plan=selected_plan,
                    plan_fingerprint=plan_fingerprint,
                    join=join,
                    group=group,
                )
                if assessment.status == "partial_or_corrupt":
                    if assessment.detail == "duplicate_completion":
                        raise StorageIntegrityError(
                            "duplicate logical join aftermath"
                        )
                    if assessment.detail == "target_route":
                        raise StorageIntegrityError(
                            "join-created activation route target must match "
                            "selected join route"
                        )
                    raise StorageIntegrityError(
                        "selected join aftermath is partial or corrupt"
                    )


def _selected_artifact_schema_property_ids(
    schemas: Iterable[ArtifactSchemaDeclaration],
) -> Mapping[ArtifactSchemaId, frozenset[str]]:
    property_ids: dict[ArtifactSchemaId, frozenset[str]] = {}
    for schema in schemas:
        raw_properties = schema.schema.get("properties", {})
        if not isinstance(raw_properties, Mapping):
            property_ids[schema.id] = frozenset()
            continue
        property_ids[schema.id] = frozenset(
            key for key in raw_properties if isinstance(key, str)
        )
    return property_ids


def _validate_selected_operator_wait_authority(state: RuntimeState) -> None:
    for admitted in state.admitted_plans.values():
        selected_plan = admitted.selected_plan
        for selected_wait in selected_plan.operator_waits:
            _validate_selected_operator_wait_projection(
                selected_plan,
                selected_wait,
            )
            if "revise_recorded_source" not in set(
                selected_wait.allowed_resolution_kinds
            ):
                continue
            _validate_selected_operator_wait_revise_route_schema(
                selected_plan,
                selected_wait,
            )


def _validate_selected_operator_wait_projection(
    selected_plan: SelectedCompiledPlan,
    selected_wait: OperatorWaitDeclaration,
) -> None:
    if type(selected_wait.project_source_artifact) is not bool:
        raise StorageIntegrityError(
            "selected operator_wait projection authority is invalid"
        )
    if selected_wait.project_source_artifact is False:
        return
    if "revise_recorded_source" not in set(selected_wait.allowed_resolution_kinds):
        raise StorageIntegrityError(
            "selected operator_wait projection authority is invalid"
        )
    target_stage = next(
        (
            stage
            for stage in selected_plan.stage_kinds
            if stage.id == selected_wait.target_stage_kind_id
        ),
        None,
    )
    actions_by_id = {action.id: action for action in selected_plan.terminal_actions}
    if target_stage is None or any(
        (action := actions_by_id.get(action_id)) is None
        or action.action_kind != "operator_wait"
        or action.artifact_schema_id is None
        or action.artifact_schema_id not in target_stage.artifact_schema_ids
        for action_id in selected_wait.source_action_ids
    ):
        raise StorageIntegrityError(
            "selected operator_wait projection authority is invalid"
        )


def _validate_terminal_action_closed_work_item(
    state: RuntimeState,
    closed: ClosedWorkItemRecord,
) -> None:
    if closed.source_run_id is None or closed.action_id is None:
        raise StorageIntegrityError(
            "closed_work_items terminal_action close requires source run"
        )
    source_run = state.runs[closed.source_run_id]
    admitted = state.admitted_plans.get(
        source_run.run_ref.plan_ref.authority_fingerprint
    )
    if admitted is None:
        raise StorageIntegrityError(
            "closed_work_items.source_run_id must reference an admitted plan"
        )
    action = next(
        (
            candidate
            for candidate in admitted.selected_plan.terminal_actions
            if candidate.id == closed.action_id
        ),
        None,
    )
    if action is None:
        raise StorageIntegrityError(
            "closed_work_items.action_id must reference selected terminal action"
        )
    if action.stage_kind_id != source_run.stage_kind_id:
        raise StorageIntegrityError(
            "closed_work_items.action_id must match source stage"
        )
    if _needs_operator_wait_source_close_validation(state, closed, action.action_kind):
        return
    if action.action_kind in _TERMINAL_CLOSING_ACTION_KINDS:
        _validate_closed_work_item_runner_observation(state, closed)
        return
    raise StorageIntegrityError(
        "closed_work_items.action_id must reference selected close action"
    )


def _validate_effect_records(
    state: RuntimeState,
    *,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
    transition_by_id: Mapping[str, TransitionRecord],
) -> None:
    _validate_unique(
        "effect_proposals.dedupe_key",
        (proposal.dedupe_key for proposal in state.effect_proposals.values()),
    )
    _validate_unique(
        "effect_reconciliations.effect_id",
        (
            reconciliation.effect_id
            for reconciliation in state.effect_reconciliations.values()
        ),
    )
    for proposal in state.effect_proposals.values():
        _validate_effect_proposal(
            state,
            proposal,
            work_item_ids=work_item_ids,
            activation_ids=activation_ids,
            run_ids=run_ids,
            transition_by_id=transition_by_id,
        )
    for reconciliation in state.effect_reconciliations.values():
        _validate_effect_reconciliation(state, reconciliation, transition_by_id)


def _validate_effect_proposal(
    state: RuntimeState,
    proposal: EffectProposalRecord,
    *,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
    transition_by_id: Mapping[str, TransitionRecord],
) -> None:
    if proposal.status != "pending":
        raise StorageIntegrityError("effect_proposals.status must be pending")
    _validate_plan_ref(
        "effect_proposals",
        "effect_proposals.plan_authority_fingerprint",
        proposal.selected_plan_ref,
        state.admitted_plans,
    )
    if (
        proposal.selected_plan_fingerprint
        != proposal.selected_plan_ref.authority_fingerprint
    ):
        raise StorageIntegrityError(
            "effect_proposals.selected_plan_fingerprint must match PlanRef"
        )
    _validate_reference(
        "effect_proposals.artifact_id",
        proposal.artifact_id,
        frozenset(state.artifacts),
        "artifacts",
    )
    _validate_reference(
        "effect_proposals.source_run_id",
        proposal.source_run_id,
        run_ids,
        "runs",
    )
    _validate_reference(
        "effect_proposals.source_work_item_id",
        proposal.source_work_item_id,
        work_item_ids,
        "work_items",
    )
    _validate_reference(
        "effect_proposals.source_activation_id",
        proposal.source_activation_id,
        activation_ids,
        "activations",
    )
    transition = transition_by_id.get(proposal.created_transition_id)
    if transition is None:
        raise StorageIntegrityError(
            "effect_proposals.created_transition_id must reference transitions"
        )
    if transition.input_id != proposal.created_input_id:
        raise StorageIntegrityError(
            "effect_proposals.created_input_id must match transition"
        )
    if (
        not transition.accepted
        or transition.input_kind != RunnerResultObserved.input_kind
        or transition.input_family != "workflow_observation"
    ):
        raise StorageIntegrityError(
            "effect_proposals.created_transition_id must reference accepted "
            "runner observation transition"
        )
    artifact = state.artifacts[proposal.artifact_id]
    run = state.runs[proposal.source_run_id]
    activation = state.activations[proposal.source_activation_id]
    work_item = state.work_items[proposal.source_work_item_id]
    if proposal.selected_plan_ref != run.run_ref.plan_ref:
        raise StorageIntegrityError("effect_proposals PlanRef must match source run")
    if artifact.artifact_id != proposal.artifact_id:
        raise StorageIntegrityError("effect_proposals.artifact_id mismatch")
    if artifact.schema_id != proposal.artifact_schema_id:
        raise StorageIntegrityError(
            "effect_proposals.artifact_schema_id must match artifact"
        )
    if artifact.payload_digest != proposal.artifact_payload_digest:
        raise StorageIntegrityError(
            "effect_proposals.artifact_payload_digest must match artifact"
        )
    if artifact.created_by_input_id != proposal.created_input_id:
        raise StorageIntegrityError(
            "effect_proposals.created_input_id must match artifact"
        )
    if proposal.source_input_id != proposal.created_input_id:
        raise StorageIntegrityError(
            "effect_proposals.source_input_id must match created_input_id"
        )
    if artifact.source_run_id != proposal.source_run_id:
        raise StorageIntegrityError(
            "effect_proposals.source_run_id must match artifact"
        )
    if artifact.source_action_id != proposal.source_action_id:
        raise StorageIntegrityError(
            "effect_proposals.source_action_id must match artifact"
        )
    if proposal.source_action_id != proposal.terminal_action_id:
        raise StorageIntegrityError(
            "effect_proposals.source_action_id must match terminal_action_id"
        )
    if artifact.source_graph_node_id != proposal.source_graph_node_id:
        raise StorageIntegrityError(
            "effect_proposals.source_graph_node_id must match artifact"
        )
    if artifact.source_stage_kind_id != proposal.source_stage_kind_id:
        raise StorageIntegrityError(
            "effect_proposals.source_stage_kind_id must match artifact"
        )
    if artifact.transition_id != proposal.created_transition_id:
        raise StorageIntegrityError(
            "effect_proposals.created_transition_id must match artifact"
        )
    if run.work_item_id != proposal.source_work_item_id:
        raise StorageIntegrityError(
            "effect_proposals.source_work_item_id must match source run"
        )
    if run.activation_id != proposal.source_activation_id:
        raise StorageIntegrityError(
            "effect_proposals.source_activation_id must match source run"
        )
    if run.stage_kind_id != proposal.source_stage_kind_id:
        raise StorageIntegrityError(
            "effect_proposals.source_stage_kind_id must match source run"
        )
    if run.runner_binding_id != proposal.source_runner_binding_id:
        raise StorageIntegrityError(
            "effect_proposals.source_runner_binding_id must match source run"
        )
    if activation.work_item_id != proposal.source_work_item_id:
        raise StorageIntegrityError(
            "effect_proposals.source_activation_id must reference source work item"
        )
    if activation.graph_node_id != proposal.source_graph_node_id:
        raise StorageIntegrityError(
            "effect_proposals.source_graph_node_id must match source activation"
        )
    if activation.queue_family_id != proposal.source_queue_family_id:
        raise StorageIntegrityError(
            "effect_proposals.source_queue_family_id must match source activation"
        )
    if work_item.lineage_id != proposal.lineage_id:
        raise StorageIntegrityError(
            "effect_proposals.lineage_id must match source work item"
        )
    _validate_effect_target_refs_match_artifact(proposal, artifact)
    admitted = state.admitted_plans[proposal.selected_plan_ref.authority_fingerprint]
    declaration = _selected_effect_declaration_for_proposal(
        admitted.selected_plan,
        proposal,
    )
    if declaration is None:
        raise StorageIntegrityError(
            "effect_proposals.effect_declaration_id must reference selected effect"
        )
    expected_dedupe_key = f"{proposal.effect_declaration_id}:{proposal.artifact_id}"
    if proposal.dedupe_key != expected_dedupe_key:
        raise StorageIntegrityError(
            "effect_proposals.dedupe_key must match selected declaration and artifact"
        )


def _validate_effect_target_refs_match_artifact(
    proposal: EffectProposalRecord,
    artifact: ArtifactRecord,
) -> None:
    target_skill_id = _optional_artifact_payload_text(
        artifact.payload,
        "target_skill_id",
    )
    target_path_ref = _optional_artifact_payload_text(
        artifact.payload,
        "installed_path",
    )
    if proposal.target_skill_id != target_skill_id:
        raise StorageIntegrityError(
            "effect_proposals.target_skill_id must match artifact payload"
        )
    if proposal.target_path_ref != target_path_ref:
        raise StorageIntegrityError(
            "effect_proposals.target_path_ref must match artifact payload"
        )


def _optional_artifact_payload_text(
    payload: Mapping[str, object],
    key: str,
) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _selected_effect_declaration_for_proposal(
    selected_plan: SelectedCompiledPlan,
    proposal: EffectProposalRecord,
) -> EffectDeclaration | None:
    declaration = next(
        (
            candidate
            for candidate in selected_plan.effect_declarations
            if candidate.effect_declaration_id == proposal.effect_declaration_id
        ),
        None,
    )
    if declaration is None:
        return None
    action = next(
        (
            candidate
            for candidate in selected_plan.terminal_actions
            if candidate.id == proposal.terminal_action_id
        ),
        None,
    )
    if (
        action is None
        or declaration.terminal_action_id != proposal.terminal_action_id
        or declaration.artifact_schema_id != proposal.artifact_schema_id
        or action.artifact_schema_id != proposal.artifact_schema_id
        or proposal.provider_ref != declaration.provider_ref
        or proposal.capability_policy_ref != declaration.capability_policy_ref
        or proposal.target_ref_kind != declaration.target_ref_kind
        or proposal.target_ref_schema != declaration.target_ref_schema
        or declaration.real_side_effects_allowed
        or declaration.allowed_reconciliation_statuses
        != ("applied", "no_op", "refused")
    ):
        return None
    return declaration


def _validate_effect_reconciliation(
    state: RuntimeState,
    reconciliation: EffectReconciliationRecord,
    transition_by_id: Mapping[str, TransitionRecord],
) -> None:
    _validate_reference(
        "effect_reconciliations.effect_id",
        reconciliation.effect_id,
        frozenset(state.effect_proposals),
        "effect_proposals",
    )
    transition = transition_by_id.get(reconciliation.created_transition_id)
    if transition is None:
        raise StorageIntegrityError(
            "effect_reconciliations.created_transition_id must reference transitions"
        )
    if transition.input_id != reconciliation.created_input_id:
        raise StorageIntegrityError(
            "effect_reconciliations.created_input_id must match transition"
        )
    if (
        not transition.accepted
        or transition.input_kind != ReconcileEffect.input_kind
        or transition.input_family != "workflow_kernel_command"
    ):
        raise StorageIntegrityError(
            "effect_reconciliations.created_transition_id must reference "
            "accepted effect reconciliation transition"
        )
    if reconciliation.status not in {"applied", "no_op", "refused"}:
        raise StorageIntegrityError(
            "effect_reconciliations.status must be selected reconciliation status"
        )
    _validate_sha256_digest(
        "effect_reconciliations.fake_local_result_digest",
        reconciliation.fake_local_result_digest,
    )
    proposal = state.effect_proposals[reconciliation.effect_id]
    if reconciliation.selected_plan_ref != proposal.selected_plan_ref:
        raise StorageIntegrityError(
            "effect_reconciliations PlanRef must match proposal"
        )
    if reconciliation.selected_plan_fingerprint != proposal.selected_plan_fingerprint:
        raise StorageIntegrityError(
            "effect_reconciliations.selected_plan_fingerprint must match proposal"
        )
    if reconciliation.provider_ref != proposal.provider_ref:
        raise StorageIntegrityError(
            "effect_reconciliations.provider_ref must match proposal"
        )
    _validate_plan_ref(
        "effect_reconciliations",
        "effect_reconciliations.plan_authority_fingerprint",
        reconciliation.selected_plan_ref,
        state.admitted_plans,
    )


def _validate_fanout_records(
    state: RuntimeState,
    *,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    artifact_ids = frozenset(state.artifacts)
    selected_keys: set[tuple[PlanRef, str, str, str]] = set()
    for record in state.fanout_records.values():
        _validate_plan_ref(
            "fanout_records",
            "fanout_records.plan_authority_fingerprint",
            record.selected_plan_ref,
            state.admitted_plans,
        )
        _validate_reference(
            "fanout_records.source_artifact_id",
            record.source_artifact_id,
            artifact_ids,
            "artifacts",
        )
        _validate_reference(
            "fanout_records.source_work_item_id",
            record.source_work_item_id,
            work_item_ids,
            "work_items",
        )
        _validate_reference(
            "fanout_records.source_run_id",
            record.source_run_id,
            run_ids,
            "runs",
        )
        if record.target_work_item_id not in work_item_ids:
            raise StorageIntegrityError(
                "fanout target work item must reference work_items"
            )
        if record.target_activation_id not in activation_ids:
            raise StorageIntegrityError(
                "fanout target activation must reference activations"
            )
        artifact = state.artifacts[record.source_artifact_id]
        source_work_item = state.work_items[record.source_work_item_id]
        source_run = state.runs[record.source_run_id]
        target_work_item = state.work_items[record.target_work_item_id]
        target_activation = state.activations[record.target_activation_id]
        _validate_fanout_source(
            record,
            artifact=artifact,
            source_work_item=source_work_item,
            source_run=source_run,
        )
        _validate_fanout_source_state_policy(state, record=record)
        _validate_fanout_target(
            record,
            target_work_item=target_work_item,
            target_activation=target_activation,
        )
        _validate_fanout_authority(
            state,
            record=record,
            artifact=artifact,
            target_work_item=target_work_item,
            target_activation=target_activation,
        )
        _validate_fanout_dependency_policy(state, record=record)
        key = (
            record.selected_plan_ref,
            str(record.fanout_id),
            record.source_artifact_id,
            record.item_key,
        )
        if key in selected_keys:
            raise StorageIntegrityError(
                "fanout_records selected fanout item key must be unique"
            )
        selected_keys.add(key)


def _validate_concurrency_policy_state(state: RuntimeState) -> None:
    plan_refs = {run.run_ref.plan_ref for run in state.runs.values()}
    for plan_ref in plan_refs:
        admitted = state.admitted_plans.get(plan_ref.authority_fingerprint)
        if admitted is None or not admitted.selected_plan.concurrency_policies:
            continue
        _validate_plan_concurrency_policy_state(
            state,
            plan_ref=plan_ref,
            selected_plan=admitted.selected_plan,
        )


def _validate_plan_concurrency_policy_state(
    state: RuntimeState,
    *,
    plan_ref: PlanRef,
    selected_plan: SelectedCompiledPlan,
) -> None:
    policies_by_partition = {
        policy.partition_id: policy
        for policy in selected_plan.concurrency_policies
    }
    active_by_partition: dict[PartitionId, list[RunRecord]] = {}
    for run in state.runs.values():
        if run.run_ref.plan_ref != plan_ref:
            continue
        if run.work_item_id in state.closed_work_items:
            continue
        if any(
            observation.run_id == run.run_ref.run_id
            for observation in state.runner_observations.values()
        ):
            continue
        stage = next(
            (
                candidate
                for candidate in selected_plan.stage_kinds
                if candidate.id == run.stage_kind_id
            ),
            None,
        )
        if stage is None or stage.partition_id is None:
            continue
        if stage.partition_id not in policies_by_partition:
            continue
        active_by_partition.setdefault(stage.partition_id, []).append(run)

    for partition_id, active_runs in active_by_partition.items():
        policy = policies_by_partition[partition_id]
        if len(active_runs) > policy.max_active_runs:
            raise StorageIntegrityError(
                "concurrency_policy max_active_runs violated by active runs"
            )

    active_partitions = tuple(active_by_partition)
    for index, partition_id in enumerate(active_partitions):
        policy = policies_by_partition[partition_id]
        for peer_partition_id in active_partitions[index + 1 :]:
            peer_policy = policies_by_partition[peer_partition_id]
            if (
                peer_partition_id not in policy.coexist_partition_ids
                or partition_id not in peer_policy.coexist_partition_ids
            ):
                raise StorageIntegrityError(
                    "concurrency_policy coexist_partition_ids violated by active runs"
                )


def _validate_fanout_source(
    record: FanoutRecord,
    *,
    artifact: ArtifactRecord,
    source_work_item: WorkItem,
    source_run: RunRecord,
) -> None:
    if artifact.payload_digest != record.source_artifact_digest:
        raise StorageIntegrityError(
            "fanout_records source artifact digest must match artifact"
        )
    if (
        artifact.work_item_id != record.source_work_item_id
        or artifact.source_run_id != record.source_run_id
        or artifact.source_action_id != record.source_action_id
    ):
        raise StorageIntegrityError(
            "fanout_records source fields must match artifact"
        )
    if source_run.work_item_id != record.source_work_item_id:
        raise StorageIntegrityError(
            "fanout_records source run must match source work item"
        )
    if (
        source_run.run_ref.plan_ref != record.selected_plan_ref
        or source_work_item.ref.plan_ref != record.selected_plan_ref
    ):
        raise StorageIntegrityError(
            "fanout_records source PlanRef must match selected PlanRef"
        )
    if source_work_item.lineage_id != record.lineage_id:
        raise StorageIntegrityError(
            "fanout_records source lineage must match recorded lineage"
        )


def _validate_fanout_source_closed(
    state: RuntimeState,
    *,
    record: FanoutRecord,
) -> None:
    closed = state.closed_work_items.get(record.source_work_item_id)
    if closed is None:
        raise StorageIntegrityError("fanout source work item must be closed")
    if (
        closed.source_run_id != record.source_run_id
        or closed.action_id != record.source_action_id
    ):
        raise StorageIntegrityError("fanout source close must match fanout record")


def _validate_fanout_source_state_policy(
    state: RuntimeState,
    *,
    record: FanoutRecord,
) -> None:
    selected_fanout = _selected_fanout_for_record(state, record)
    if selected_fanout is None:
        return
    if selected_fanout.source_state_policy == "source_closed":
        _validate_fanout_source_closed(state, record=record)
        return
    if selected_fanout.source_state_policy == "accepted_terminal_observation":
        return
    raise StorageIntegrityError("fanout source state policy is unsupported")


def _validate_fanout_target(
    record: FanoutRecord,
    *,
    target_work_item: WorkItem,
    target_activation: Activation,
) -> None:
    if target_activation.work_item_id != record.target_work_item_id:
        raise StorageIntegrityError(
            "fanout target activation must reference target work item"
        )
    if (
        target_work_item.ref.plan_ref != record.selected_plan_ref
        or target_activation.plan_ref != record.selected_plan_ref
    ):
        raise StorageIntegrityError(
            "fanout target PlanRef must match selected PlanRef"
        )
    if (
        target_work_item.queue_family_id != record.target_queue_family_id
        or target_activation.queue_family_id != record.target_queue_family_id
    ):
        raise StorageIntegrityError(
            "fanout target queue family must match fanout record"
        )
    if (
        target_work_item.lineage_id != record.lineage_id
        or target_activation.lineage_id != record.lineage_id
    ):
        raise StorageIntegrityError("fanout target lineage must match fanout record")
    if (
        target_activation.stage_kind_id != record.target_stage_kind_id
        or target_activation.graph_node_id != record.target_graph_node_id
    ):
        raise StorageIntegrityError(
            "fanout target activation context must match fanout record"
        )


def _validate_fanout_authority(
    state: RuntimeState,
    *,
    record: FanoutRecord,
    artifact: ArtifactRecord,
    target_work_item: WorkItem,
    target_activation: Activation,
) -> None:
    selected_plan = state.admitted_plans[
        record.selected_plan_ref.authority_fingerprint
    ].selected_plan
    selected_fanout = _selected_fanout_for_record(state, record)
    if selected_fanout is None:
        raise StorageIntegrityError(
            "fanout_records.fanout_id must reference selected fanout_declarations"
        )
    source_action = next(
        (
            action
            for action in selected_plan.terminal_actions
            if action.id == selected_fanout.source_action_id
        ),
        None,
    )
    supported_action_kinds = (
        {"close", "complete_work_item"}
        if selected_fanout.source_state_policy == "source_closed"
        else {
            "route",
            "create_incident_route",
            "close",
            "complete_work_item",
            "close_with_escalation",
            "block_work_item",
        }
    )
    if source_action is None or source_action.action_kind not in supported_action_kinds:
        if selected_fanout.source_state_policy == "source_closed":
            raise StorageIntegrityError(
                "fanout source action must reference selected close action"
            )
        raise StorageIntegrityError(
            "fanout source action must reference selected terminal action"
        )
    if (
        selected_fanout.source_action_id != record.source_action_id
        or selected_fanout.source_artifact_schema_id != artifact.schema_id
        or selected_fanout.target_queue_family_id != record.target_queue_family_id
        or selected_fanout.target_stage_kind_id != record.target_stage_kind_id
        or selected_fanout.target_graph_node_id != record.target_graph_node_id
    ):
        raise StorageIntegrityError(
            "fanout_records context must match selected fanout_declaration"
        )
    if selected_fanout.target_runner_binding_id != target_activation.runner_binding_id:
        raise StorageIntegrityError(
            "fanout target runner binding must match selected fanout_declaration"
        )
    selected_items = fanout_items(artifact, selected_fanout)
    if selected_items is None:
        raise StorageIntegrityError(
            "fanout_records source collection must match selected fanout_declaration"
        )
    raw_item = dict(selected_items).get(record.item_key)
    if raw_item is None:
        raise StorageIntegrityError(
            "fanout_records item_key must reference selected source item"
        )
    expected_payload = fanout_target_payload(
        selected_fanout.target_payload_mapping,
        raw_item,
        artifact.payload,
    )
    if expected_payload is None or expected_payload != target_work_item.payload:
        raise StorageIntegrityError(
            "fanout target work item payload must match selected source item mapping"
        )
    target_schema = _selected_artifact_schema(
        selected_plan.artifact_schemas,
        str(selected_fanout.target_payload_schema_id),
    )
    if target_schema is None:
        raise StorageIntegrityError(
            "fanout target payload schema must reference selected artifact_schemas"
        )
    if not validate_schema(target_schema.schema, target_work_item.payload).accepted:
        raise StorageIntegrityError(
            "fanout target work item payload must match selected fanout target "
            "payload schema"
        )


def _validate_fanout_dependency_policy(
    state: RuntimeState,
    *,
    record: FanoutRecord,
) -> None:
    selected_fanout = _selected_fanout_for_record(state, record)
    if selected_fanout is None:
        return
    dependencies = tuple(
        dependency
        for dependency in state.work_dependencies.values()
        if dependency.fanout_record_id == record.record_id
    )
    if selected_fanout.dependency_policy == "depends_on_source_work_item":
        if len(dependencies) != 1:
            raise StorageIntegrityError(
                "fanout_record_id dependency policy requires exactly one dependency"
            )
        return
    if selected_fanout.dependency_policy == "none":
        if dependencies:
            raise StorageIntegrityError(
                "fanout dependency policy forbids dependencies"
            )
        return
    raise StorageIntegrityError("fanout dependency policy is unsupported")


def _selected_fanout_for_record(
    state: RuntimeState,
    record: FanoutRecord,
) -> FanoutDeclaration | None:
    selected_plan = state.admitted_plans[
        record.selected_plan_ref.authority_fingerprint
    ].selected_plan
    return next(
        (
            candidate
            for candidate in selected_plan.fanout_declarations
            if candidate.id == record.fanout_id
        ),
        None,
    )


def _selected_artifact_schema(
    schemas: Iterable[ArtifactSchemaDeclaration],
    schema_id: str,
) -> ArtifactSchemaDeclaration | None:
    return next((schema for schema in schemas if str(schema.id) == schema_id), None)


def _validate_work_dependencies(
    state: RuntimeState,
    *,
    work_item_ids: frozenset[str],
) -> None:
    for dependency in state.work_dependencies.values():
        _validate_plan_ref(
            "work_dependencies",
            "work_dependencies.plan_authority_fingerprint",
            dependency.selected_plan_ref,
            state.admitted_plans,
        )
        _validate_reference(
            "work_dependencies.dependent_work_item_id",
            dependency.dependent_work_item_id,
            work_item_ids,
            "work_items",
        )
        _validate_reference(
            "work_dependencies.dependency_work_item_id",
            dependency.dependency_work_item_id,
            work_item_ids,
            "work_items",
        )
        fanout = state.fanout_records.get(dependency.fanout_record_id)
        if fanout is None:
            raise StorageIntegrityError(
                "work_dependencies.fanout_record_id must reference fanout_records"
            )
        _validate_work_dependency_matches_fanout(
            dependency,
            fanout=fanout,
            dependent=state.work_items[dependency.dependent_work_item_id],
            source=state.work_items[dependency.dependency_work_item_id],
        )


def _validate_work_dependency_matches_fanout(
    dependency: WorkDependencyRecord,
    *,
    fanout: FanoutRecord,
    dependent: WorkItem,
    source: WorkItem,
) -> None:
    if dependency.selected_plan_ref != fanout.selected_plan_ref:
        raise StorageIntegrityError("dependency PlanRef must match fanout record")
    if (
        dependency.dependent_work_item_id != fanout.target_work_item_id
        or dependency.dependency_work_item_id != fanout.source_work_item_id
    ):
        raise StorageIntegrityError("dependency work items must match fanout record")
    if (
        dependent.ref.plan_ref != dependency.selected_plan_ref
        or source.ref.plan_ref != dependency.selected_plan_ref
    ):
        raise StorageIntegrityError("dependency PlanRef must match work items")
    if (
        dependency.lineage_id != fanout.lineage_id
        or dependent.lineage_id != dependency.lineage_id
        or source.lineage_id != dependency.lineage_id
    ):
        raise StorageIntegrityError("dependency lineage must match work items")


def _validate_closure_records(
    state: RuntimeState,
    *,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    if any(
        key != value.closure_target_id for key, value in state.closure_targets.items()
    ) or any(
        key != value.record_id
        for mapping in (
            state.closure_evaluations,
            state.closure_terminal_records,
            state.remediation_work_records,
            state.closure_blocked_records,
        )
        for key, value in mapping.items()
    ):
        raise StorageIntegrityError("closure record mapping key must match record ID")
    closure_target_ids = frozenset(state.closure_targets)
    terminal_record_ids = frozenset(state.closure_terminal_records)
    _validate_unique(
        "closure_targets.logical_key",
        (
            _closure.closure_target_key_for(target)
            for target in state.closure_targets.values()
        ),
    )
    for target in state.closure_targets.values():
        _validate_closure_target_authority(state, target)
        if target.status == "open":
            if target.closed_by_record_id is not None:
                raise StorageIntegrityError(
                    "closure_targets open status must not carry closed_by_record_id"
                )
        elif target.status == "closed":
            if target.closed_by_record_id is None:
                raise StorageIntegrityError(
                    "closure_targets closed status requires closed_by_record_id"
                )
            _validate_reference(
                "closure_targets.closed_by_record_id",
                target.closed_by_record_id,
                terminal_record_ids,
                "closure_terminal_records",
            )
        else:
            raise StorageIntegrityError("closure_targets.status is unsupported")

    evaluation_anchors: set[tuple[object, ...]] = set()
    for activation in state.closure_evaluations.values():
        target, behavior = _validate_closure_child_target(
            state,
            "closure_evaluations",
            closure_target_ids,
            activation,
        )
        _validate_reference(
            "closure_evaluations.target_work_item_id",
            activation.target_work_item_id,
            work_item_ids,
            "work_items",
        )
        _validate_reference(
            "closure_evaluations.target_activation_id",
            activation.target_activation_id,
            activation_ids,
            "activations",
        )
        work_item = state.work_items[activation.target_work_item_id]
        if _closure.closure_creator_refusal(
            state,
            EvaluateCompletionBehavior(
                activation.created_by_input_id,
                activation.selected_plan_ref,
                str(activation.completion_behavior_id),
                activation.closure_target_id,
            ),
        ) is not None:
            raise StorageIntegrityError("closure creator payload digest is invalid")
        if (
            _closure._evaluation_parts(state, activation, target, behavior)[2]
            is not None
        ):
            raise StorageIntegrityError(
                "closure_evaluations target context must match selected "
                "completion behavior"
            )
        anchor = _closure.closure_evidence_anchor(
            work_item.payload.get("closure_evidence_snapshot"), target=target
        )
        if anchor is None:
            raise StorageIntegrityError(
                "closure_evaluations evidence anchor is invalid"
            )
        anchor_key = (target.closure_target_id, *tuple(sorted(anchor.items())))
        if anchor_key in evaluation_anchors:
            raise StorageIntegrityError(
                "closure_evaluations evidence anchor must be unique"
            )
        evaluation_anchors.add(anchor_key)

    for terminal in state.closure_terminal_records.values():
        target, behavior = _validate_closure_child_target(
            state,
            "closure_terminal_records",
            closure_target_ids,
            terminal,
        )
        if (
            target.status != "closed"
            or target.closed_by_record_id != terminal.record_id
        ):
            raise StorageIntegrityError(
                "closure_terminal_records must close recorded closure target"
            )
        _validate_closure_source_action(
            state,
            table_name="closure_terminal_records",
            behavior=behavior,
            target=target,
            selected_plan_ref=terminal.selected_plan_ref,
            source_run_id=terminal.source_run_id,
            source_action_id=terminal.source_action_id,
            expected_action_id=behavior.pass_action_id,
            source_artifact_id=terminal.source_artifact_id,
            run_ids=run_ids,
        )

    for remediation_record in state.remediation_work_records.values():
        _validate_closure_remediation_work(
            state,
            remediation_record=remediation_record,
            closure_target_ids=closure_target_ids,
            work_item_ids=work_item_ids,
            activation_ids=activation_ids,
            run_ids=run_ids,
        )

    for blocked in state.closure_blocked_records.values():
        target, behavior = _validate_closure_child_target(
            state,
            "closure_blocked_records",
            closure_target_ids,
            blocked,
        )
        if _closure.closure_block_overlay_error(
            state, target=target, behavior=behavior
        ):
            raise StorageIntegrityError(
                "closure block overlay operator_required or authority is invalid"
            )


def _validate_closure_target_authority(
    state: RuntimeState,
    target: ClosureTargetRecord,
) -> CompletionBehaviorDeclaration:
    _validate_plan_ref(
        "closure_targets",
        "closure_targets.plan_authority_fingerprint",
        target.selected_plan_ref,
        state.admitted_plans,
    )
    behavior = _selected_completion_behavior(
        state,
        target.selected_plan_ref,
        target.completion_behavior_id,
        "closure_targets.completion_behavior_id",
    )
    if target.target_graph_node_id != behavior.target_graph_node_id:
        raise StorageIntegrityError(
            "closure_targets.target_graph_node_id must match selected "
            "completion behavior"
        )
    if target.request_kind != behavior.request_kind:
        raise StorageIntegrityError(
            "closure_targets.request_kind must match selected completion behavior"
        )
    if target.root_source_kind not in behavior.accepted_root_source_kinds:
        raise StorageIntegrityError(
            "closure_targets.root_source_kind must be accepted by selected "
            "completion behavior"
        )
    expected_window = {"kind": "lineage", "lineage_id": target.lineage_id}
    if target.evidence_window != expected_window:
        raise StorageIntegrityError("closure_targets.evidence_window is noncanonical")
    if behavior.root_source_resolution == "runtime_inventory":
        selected_plan = state.admitted_plans[
            target.selected_plan_ref.authority_fingerprint
        ].selected_plan
        _validate_closure_root_inventory(state, target, selected_plan)
    target_key = _closure.closure_target_key_for(target)
    if target.closure_target_id != _closure.closure_target_id(target_key):
        raise StorageIntegrityError("closure_targets.closure_target_id is noncanonical")
    if _closure.closure_creator_refusal(
        state,
        OpenClosureTarget(
            target.opened_by_input_id,
            target.selected_plan_ref,
            str(target.completion_behavior_id),
            target.closure_target_id,
            target.lineage_id,
            target.root_source_kind,
            target.root_source_id,
            target.closure_root_work_item_id,
            target.request_kind,
            target.target_graph_node_id,
            target.evidence_window,
        ),
    ) is not None:
        raise StorageIntegrityError("closure creator payload digest is invalid")
    return behavior


def _validate_closure_root_inventory(
    state: RuntimeState,
    target: ClosureTargetRecord,
    selected_plan: SelectedCompiledPlan,
) -> None:
    if target.closure_root_work_item_id is None:
        raise StorageIntegrityError(
            "closure_targets.closure_root_work_item_id is required for runtime "
            "inventory root sources"
        )
    root_inventory_queue_ids = {
        route.queue_family_id for route in selected_plan.external_enqueue_routes
    }
    matches = tuple(
        work_item
        for work_item in state.work_items.values()
        if work_item.queue_family_id in root_inventory_queue_ids
        and _closure.closure_root_source_matches(
            work_item,
            root_source_kind=target.root_source_kind,
            root_source_id=target.root_source_id,
        )
        and work_item.ref.plan_ref == target.selected_plan_ref
    )
    if any(
        not isinstance(item.lineage_id, str) or not item.lineage_id.strip()
        for item in matches
    ):
        raise StorageIntegrityError("invalid_closure_root_lineage")
    matches = tuple(
        item for item in matches if item.lineage_id == item.ref.work_item_id
    )
    if not matches:
        raise StorageIntegrityError(
            "closure_targets root source must exist in runtime inventory"
        )
    if len(matches) > 1:
        raise StorageIntegrityError(
            "closure_targets root source must resolve unambiguously"
        )
    if matches[0].ref.work_item_id != target.closure_root_work_item_id:
        raise StorageIntegrityError(
            "closure_targets.closure_root_work_item_id must match runtime inventory"
        )
    if matches[0].lineage_id != target.lineage_id:
        raise StorageIntegrityError(
            "closure_targets.lineage_id must match runtime inventory root lineage"
        )
    if _closure.closure_enqueue_creator_refusal(state, matches[0]) is not None:
        raise StorageIntegrityError("closure root creator authority is invalid")


def _validate_closure_child_target(
    state: RuntimeState,
    table_name: str,
    closure_target_ids: frozenset[str],
    record: (
        ClosureEvaluationRecord
        | ClosureTerminalRecord
        | ClosureBlockedRecord
        | RemediationWorkRecord
    ),
) -> tuple[ClosureTargetRecord, CompletionBehaviorDeclaration]:
    _validate_reference(
        f"{table_name}.closure_target_id",
        record.closure_target_id,
        closure_target_ids,
        "closure_targets",
    )
    target = state.closure_targets[record.closure_target_id]
    behavior = _validate_closure_target_authority(state, target)
    if record.selected_plan_ref != target.selected_plan_ref:
        raise StorageIntegrityError(f"{table_name} PlanRef must match closure target")
    if record.lineage_id != target.lineage_id:
        raise StorageIntegrityError(f"{table_name}.lineage_id must match target")
    completion_behavior_id = (
        None
        if isinstance(record, RemediationWorkRecord)
        else record.completion_behavior_id
    )
    if (
        completion_behavior_id is not None
        and completion_behavior_id != target.completion_behavior_id
    ):
        raise StorageIntegrityError(
            f"{table_name}.completion_behavior_id must match closure target"
        )
    return target, behavior


def _validate_closure_source_action(
    state: RuntimeState,
    *,
    table_name: str,
    behavior: CompletionBehaviorDeclaration,
    target: ClosureTargetRecord,
    selected_plan_ref: PlanRef,
    source_run_id: str,
    source_action_id: ActionId,
    expected_action_id: ActionId,
    source_artifact_id: str | None,
    run_ids: frozenset[str],
) -> TerminalActionDeclaration:
    if source_action_id != expected_action_id:
        raise StorageIntegrityError(
            f"{table_name}.source_action_id must match selected completion behavior"
        )
    _validate_reference(
        f"{table_name}.source_run_id",
        source_run_id,
        run_ids,
        "runs",
    )
    run = state.runs[source_run_id]
    if run.run_ref.plan_ref != selected_plan_ref:
        raise StorageIntegrityError(
            f"{table_name}.source_run_id PlanRef must match selected PlanRef"
        )
    if (
        run.stage_kind_id != behavior.target_stage_kind_id
        or run.runner_binding_id != behavior.runner_binding_id
    ):
        raise StorageIntegrityError(
            f"{table_name} source run must match selected completion behavior"
        )
    _validate_closure_source_evaluator_link(
        state,
        table_name=table_name,
        target=target,
        behavior=behavior,
        selected_plan_ref=selected_plan_ref,
        run=run,
    )
    selected_plan = state.admitted_plans[
        selected_plan_ref.authority_fingerprint
    ].selected_plan
    action = _selected_terminal_action(
        selected_plan.terminal_actions,
        source_action_id,
        f"{table_name}.source_action_id",
    )
    if action.stage_kind_id != run.stage_kind_id:
        raise StorageIntegrityError(
            f"{table_name}.source_action_id must match source run stage"
        )
    if source_artifact_id is None:
        return action
    _validate_reference(
        f"{table_name}.source_artifact_id",
        source_artifact_id,
        frozenset(state.artifacts),
        "artifacts",
    )
    artifact = state.artifacts[source_artifact_id]
    if (
        artifact.source_run_id != source_run_id
        or artifact.source_action_id != source_action_id
        or artifact.work_item_id != run.work_item_id
    ):
        raise StorageIntegrityError(
            f"{table_name}.source_artifact_id must match source action"
        )
    if action.artifact_schema_id != artifact.schema_id:
        raise StorageIntegrityError(
            f"{table_name}.source_artifact_id schema must match source action"
        )
    return action


def _validate_closure_source_evaluator_link(
    state: RuntimeState,
    *,
    table_name: str,
    target: ClosureTargetRecord,
    behavior: CompletionBehaviorDeclaration,
    selected_plan_ref: PlanRef,
    run: RunRecord,
) -> None:
    if not any(
        record.closure_target_id == target.closure_target_id
        and record.completion_behavior_id == behavior.id
        and record.request_kind == behavior.request_kind
        and record.target_work_item_id == run.work_item_id
        and record.target_activation_id == run.activation_id
        and record.selected_plan_ref == selected_plan_ref
        and record.lineage_id == target.lineage_id
        for record in state.closure_evaluations.values()
    ):
        raise StorageIntegrityError(
            f"{table_name}.source_run_id must match closure evaluator activation"
        )


def _validate_closure_remediation_work(
    state: RuntimeState,
    *,
    remediation_record: RemediationWorkRecord,
    closure_target_ids: frozenset[str],
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    target, behavior = _validate_closure_child_target(
        state,
        "remediation_work_records",
        closure_target_ids,
        remediation_record,
    )
    if remediation_record.remediation_policy_id != behavior.remediation_policy_id:
        raise StorageIntegrityError(
            "remediation_work_records.remediation_policy_id must match selected "
            "completion behavior"
        )
    _validate_closure_source_action(
        state,
        table_name="remediation_work_records",
        behavior=behavior,
        target=target,
        selected_plan_ref=remediation_record.selected_plan_ref,
        source_run_id=remediation_record.source_run_id,
        source_action_id=remediation_record.source_action_id,
        expected_action_id=behavior.gap_action_id,
        source_artifact_id=remediation_record.source_artifact_id,
        run_ids=run_ids,
    )
    selected_plan = state.admitted_plans[
        remediation_record.selected_plan_ref.authority_fingerprint
    ].selected_plan
    policy = _selected_remediation_policy(
        selected_plan.remediation_policies,
        remediation_record.remediation_policy_id,
        "remediation_work_records.remediation_policy_id",
    )
    if policy.source_action_id != remediation_record.source_action_id:
        raise StorageIntegrityError(
            "remediation_work_records.source_action_id must match remediation policy"
        )
    if (
        policy.guidance_source == "source_artifact"
        and remediation_record.source_artifact_id is None
    ):
        raise StorageIntegrityError(
            "remediation_work_records.source_artifact_id is required by selected "
            "remediation policy"
        )
    if (
        policy.dedupe_key == "closure_target_and_source_artifact"
        and remediation_record.dedupe_key
        != f"{remediation_record.closure_target_id}:"
        f"{remediation_record.source_artifact_id}"
    ):
        raise StorageIntegrityError(
            "remediation_work_records.dedupe_key must match selected remediation policy"
        )
    duplicate_keys: set[tuple[PlanRef, str]] = set()
    for candidate in state.remediation_work_records.values():
        key = (candidate.selected_plan_ref, candidate.dedupe_key)
        if key in duplicate_keys:
            raise StorageIntegrityError(
                "remediation_work_records.dedupe_key must be unique per selected plan"
            )
        duplicate_keys.add(key)
    _validate_reference(
        "remediation_work_records.target_work_item_id",
        remediation_record.target_work_item_id,
        work_item_ids,
        "work_items",
    )
    _validate_reference(
        "remediation_work_records.target_activation_id",
        remediation_record.target_activation_id,
        activation_ids,
        "activations",
    )
    work_item = state.work_items[remediation_record.target_work_item_id]
    activation = state.activations[remediation_record.target_activation_id]
    if (
        activation.work_item_id != remediation_record.target_work_item_id
        or work_item.ref.plan_ref != remediation_record.selected_plan_ref
        or activation.plan_ref != remediation_record.selected_plan_ref
        or work_item.lineage_id != remediation_record.lineage_id
        or activation.lineage_id != remediation_record.lineage_id
        or work_item.queue_family_id != policy.target_queue_family_id
        or activation.queue_family_id != policy.target_queue_family_id
        or activation.stage_kind_id != policy.target_stage_kind_id
        or activation.graph_node_id != policy.target_graph_node_id
        or activation.runner_binding_id != policy.target_runner_binding_id
    ):
        raise StorageIntegrityError(
            "remediation_work_records target context must match selected remediation "
            "policy"
        )


def _selected_completion_behavior(
    state: RuntimeState,
    plan_ref: PlanRef,
    completion_behavior_id: object,
    column: str,
) -> CompletionBehaviorDeclaration:
    selected_plan = state.admitted_plans[
        plan_ref.authority_fingerprint
    ].selected_plan
    behavior = next(
        (
            candidate
            for candidate in selected_plan.completion_behaviors
            if candidate.id == completion_behavior_id
        ),
        None,
    )
    if behavior is None:
        raise StorageIntegrityError(
            f"{column} must reference selected completion_behaviors"
        )
    return behavior


def _selected_terminal_action(
    terminal_actions: Iterable[TerminalActionDeclaration],
    action_id: ActionId,
    column: str,
) -> TerminalActionDeclaration:
    action = next(
        (candidate for candidate in terminal_actions if candidate.id == action_id),
        None,
    )
    if action is None:
        raise StorageIntegrityError(
            f"{column} must reference selected terminal_actions"
        )
    return action


def _selected_remediation_policy(
    policies: Iterable[RemediationPolicyDeclaration],
    policy_id: object,
    column: str,
) -> RemediationPolicyDeclaration:
    policy = next(
        (candidate for candidate in policies if candidate.id == policy_id),
        None,
    )
    if policy is None:
        raise StorageIntegrityError(
            f"{column} must reference selected remediation_policies"
        )
    return policy


def _validate_closed_work_item_runner_observation(
    state: RuntimeState,
    closed: ClosedWorkItemRecord,
) -> None:
    observation = next(
        (
            candidate
            for candidate in state.runner_observations.values()
            if candidate.run_id == closed.source_run_id
        ),
        None,
    )
    if observation is None:
        raise StorageIntegrityError(
            "closed_work_items.source_run_id must reference a runner observation"
        )
    if observation.created_by_input_id != closed.created_by_input_id:
        raise StorageIntegrityError(
            "closed_work_items.created_by_input_id must match source runner "
            "observation"
        )
    event = next(
        (
            candidate
            for candidate in state.governance_events
            if candidate.input_id == closed.created_by_input_id
            and candidate.input_kind == RunnerResultObserved.input_kind
            and candidate.input_family == "workflow_observation"
            and candidate.disposition == "accepted"
            and candidate.run_id == closed.source_run_id
            and candidate.work_item_id == closed.work_item_id
        ),
        None,
    )
    if event is None:
        raise StorageIntegrityError(
            "closed_work_items.created_by_input_id must reference accepted "
            "runner observation governance event"
        )
    if event.action_id != closed.action_id:
        raise StorageIntegrityError(
            "closed_work_items.action_id must match source runner observation"
        )


def _needs_operator_wait_source_close_validation(
    state: RuntimeState,
    closed: ClosedWorkItemRecord,
    action_kind: str,
) -> bool:
    for wait in state.operator_waits.values():
        if wait.source_work_item_id != closed.work_item_id:
            continue
        if (
            action_kind == "operator_wait"
            and wait.source_run_id == closed.source_run_id
            and wait.source_action_id == closed.action_id
        ):
            if closed.created_by_input_id == wait.created_input_id:
                _validate_closed_work_item_runner_observation(state, closed)
                return True
            if closed.created_by_input_id == wait.resolved_input_id:
                return True
        if wait.status not in {"resolved", "superseded"}:
            continue
        if wait.resolution_kind != "resume_recorded_source":
            continue
        if closed.created_by_input_id in {
            wait.created_input_id,
            wait.resolved_input_id,
        }:
            return True
        if closed.action_id == wait.source_action_id:
            return True
        if action_kind not in _TERMINAL_CLOSING_ACTION_KINDS:
            return True
    return False


def _validate_recovery_attempts(
    state: RuntimeState,
    *,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    active_keys: set[tuple[str, str, str]] = set()
    for attempt in state.recovery_attempts.values():
        _validate_plan_ref(
            "recovery_attempts",
            "recovery_attempts.plan_authority_fingerprint",
            attempt.plan_ref,
            state.admitted_plans,
        )
        key = (
            attempt.plan_ref.authority_fingerprint,
            str(attempt.policy_id),
            attempt.lineage_id,
        )
        if attempt.phase != "resolved" and key in active_keys:
            raise StorageIntegrityError(
                "recovery_attempts active plan/policy/lineage key must be unique"
            )
        if attempt.phase != "resolved":
            active_keys.add(key)
        admitted_plan = state.admitted_plans[attempt.plan_ref.authority_fingerprint]
        policy = next(
            (
                policy
                for policy in admitted_plan.selected_plan.recovery_policies
                if policy.id == attempt.policy_id
            ),
            None,
        )
        if policy is None:
            raise StorageIntegrityError(
                "recovery_attempts.policy_id must reference selected recovery_policies"
            )
        if not _recovery_action_matches_policy_source(
            admitted_plan,
            policy,
            attempt.recovery_action_id,
        ):
            raise StorageIntegrityError(
                "recovery_attempts.recovery_action_id must reference "
                "policy source_recovery_action_ids"
            )
        if (
            attempt.latest_return_action_id is not None
            and attempt.latest_return_action_id not in policy.return_action_ids
        ):
            raise StorageIntegrityError(
                "recovery_attempts.latest_return_action_id must reference "
                "policy return_action_ids"
            )
        _validate_recovery_attempt_source_links(
            state,
            attempt=attempt,
            work_item_ids=work_item_ids,
            activation_ids=activation_ids,
            run_ids=run_ids,
        )
        expected_record_id = (
            "recovery-attempt:"
            f"{attempt.plan_ref.authority_fingerprint}:"
            f"{attempt.policy_id}:"
            f"{attempt.lineage_id}:"
            f"{attempt.created_by_input_id}"
        )
        if attempt.record_id != expected_record_id:
            raise StorageIntegrityError(
                "recovery_attempts.record_id must match plan/policy/lineage/input key"
            )
        _validate_recovery_attempt_latest_links(
            state,
            attempt=attempt,
            policy_recovery_stage_kind_id=policy.recovery_stage_kind_id,
            activation_ids=activation_ids,
            run_ids=run_ids,
        )


def _validate_recovery_attempt_source_links(
    state: RuntimeState,
    *,
    attempt: RecoveryAttemptRecord,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    _validate_reference(
        "recovery_attempts.source_run_id",
        attempt.source_run_id,
        run_ids,
        "runs",
    )
    _validate_reference(
        "recovery_attempts.source_work_item_id",
        attempt.source_work_item_id,
        work_item_ids,
        "work_items",
    )
    _validate_reference(
        "recovery_attempts.source_activation_id",
        attempt.source_activation_id,
        activation_ids,
        "activations",
    )
    source_run = state.runs[attempt.source_run_id]
    source_work_item = state.work_items[attempt.source_work_item_id]
    source_activation = state.activations[attempt.source_activation_id]
    if attempt.lineage_id != source_work_item.lineage_id:
        raise StorageIntegrityError(
            "recovery_attempts.lineage_id must match source work item lineage"
        )
    if source_run.work_item_id != attempt.source_work_item_id:
        raise StorageIntegrityError(
            "recovery_attempts.source_run_id must reference source work item"
        )
    if source_run.activation_id != attempt.source_activation_id:
        raise StorageIntegrityError(
            "recovery_attempts.source_run_id must reference source activation"
        )
    if source_activation.work_item_id != attempt.source_work_item_id:
        raise StorageIntegrityError(
            "recovery_attempts.source_activation_id must reference source work item"
        )
    if (
        source_work_item.ref.plan_ref != attempt.plan_ref
        or source_run.run_ref.plan_ref != attempt.plan_ref
        or source_activation.plan_ref != attempt.plan_ref
    ):
        raise StorageIntegrityError(
            "recovery_attempts PlanRef must match recorded source context"
        )
    if (
        source_activation.graph_node_id != attempt.source_graph_node_id
        or source_run.stage_kind_id != attempt.source_stage_kind_id
        or source_run.runner_binding_id != attempt.source_runner_binding_id
        or source_work_item.queue_family_id != attempt.source_queue_family_id
    ):
        raise StorageIntegrityError(
            "recovery_attempts source context fields must match recorded source"
        )


def _validate_recovery_attempt_latest_links(
    state: RuntimeState,
    *,
    attempt: RecoveryAttemptRecord,
    policy_recovery_stage_kind_id: StageKindId,
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    if attempt.latest_recovery_activation_id is not None:
        _validate_reference(
            "recovery_attempts.latest_recovery_activation_id",
            attempt.latest_recovery_activation_id,
            activation_ids,
            "activations",
        )
        activation = state.activations[attempt.latest_recovery_activation_id]
        if (
            activation.plan_ref != attempt.plan_ref
            or activation.lineage_id != attempt.lineage_id
        ):
            raise StorageIntegrityError(
                "recovery_attempts latest recovery activation must match attempt"
            )
        if activation.stage_kind_id != policy_recovery_stage_kind_id:
            raise StorageIntegrityError(
                "recovery_attempts latest recovery activation must match policy "
                "recovery stage"
            )
    if attempt.latest_recovery_run_id is not None:
        _validate_reference(
            "recovery_attempts.latest_recovery_run_id",
            attempt.latest_recovery_run_id,
            run_ids,
            "runs",
        )
        run = state.runs[attempt.latest_recovery_run_id]
        if (
            attempt.latest_recovery_activation_id is not None
            and run.activation_id != attempt.latest_recovery_activation_id
        ):
            raise StorageIntegrityError(
                "recovery_attempts latest recovery run must match activation"
            )
        if run.run_ref.plan_ref != attempt.plan_ref:
            raise StorageIntegrityError(
                "recovery_attempts latest recovery run must match attempt PlanRef"
            )
        if run.stage_kind_id != policy_recovery_stage_kind_id:
            raise StorageIntegrityError(
                "recovery_attempts latest recovery run must match policy "
                "recovery stage"
            )


def _validate_cooldown_waits(
    state: RuntimeState,
    *,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    for wait in state.cooldown_waits.values():
        _validate_plan_ref(
            "cooldown_waits",
            "cooldown_waits.plan_authority_fingerprint",
            wait.plan_ref,
            state.admitted_plans,
        )
        _validate_reference(
            "cooldown_waits.recovery_attempt_record_id",
            wait.recovery_attempt_record_id,
            frozenset(state.recovery_attempts),
            "recovery_attempts",
        )
        _validate_reference(
            "cooldown_waits.source_run_id",
            wait.source_run_id,
            run_ids,
            "runs",
        )
        _validate_reference(
            "cooldown_waits.source_work_item_id",
            wait.source_work_item_id,
            work_item_ids,
            "work_items",
        )
        _validate_reference(
            "cooldown_waits.source_activation_id",
            wait.source_activation_id,
            activation_ids,
            "activations",
        )
        admitted_plan = state.admitted_plans[wait.plan_ref.authority_fingerprint]
        policy = next(
            (
                policy
                for policy in admitted_plan.selected_plan.recovery_policies
                if policy.id == wait.policy_id
            ),
            None,
        )
        if policy is None:
            raise StorageIntegrityError(
                "cooldown_waits.policy_id must reference selected recovery_policies"
            )
        if not _recovery_action_matches_policy_source(
            admitted_plan,
            policy,
            wait.recovery_action_id,
        ):
            raise StorageIntegrityError(
                "cooldown_waits.recovery_action_id must reference "
                "policy source_recovery_action_ids"
            )
        action = next(
            (
                action
                for action in admitted_plan.selected_plan.terminal_actions
                if action.id == wait.recovery_action_id
            ),
            None,
        )
        if action is None:
            raise StorageIntegrityError(
                "cooldown_waits.recovery_action_id must reference terminal_actions"
            )
        _validate_cooldown_wait_matches_attempt(
            state,
            wait=wait,
            policy_recovery_stage_kind_id=policy.recovery_stage_kind_id,
        )
        if (
            action.target_stage_kind_id != wait.target_stage_kind_id
            or action.target_graph_node_id != wait.target_graph_node_id
            or action.runner_binding_id != wait.target_runner_binding_id
            or action.target_stage_kind_id != policy.recovery_stage_kind_id
        ):
            raise StorageIntegrityError(
                "cooldown_waits target context fields must match selected "
                "recovery action"
            )


def _validate_cooldown_wait_matches_attempt(
    state: RuntimeState,
    *,
    wait: CooldownWaitRecord,
    policy_recovery_stage_kind_id: StageKindId,
) -> None:
    attempt = state.recovery_attempts[wait.recovery_attempt_record_id]
    if wait.lineage_id != attempt.lineage_id:
        raise StorageIntegrityError(
            "cooldown_waits.lineage_id must match recovery attempt lineage"
        )
    if wait.plan_ref != attempt.plan_ref:
        raise StorageIntegrityError(
            "cooldown_waits PlanRef must match recovery attempt PlanRef"
        )
    if wait.policy_id != attempt.policy_id:
        raise StorageIntegrityError(
            "cooldown_waits.policy_id must match recovery attempt policy_id"
        )
    if wait.due_at < wait.created_at:
        raise StorageIntegrityError(
            "cooldown_waits.due_at must be at or after created_at"
        )
    _validate_cooldown_wait_source_context(state, wait=wait)
    if wait.consumed_input_id is None:
        if (
            wait.attempt_count != attempt.attempt_count
            or wait.source_run_id != attempt.source_run_id
            or wait.source_work_item_id != attempt.source_work_item_id
            or wait.source_activation_id != attempt.source_activation_id
            or wait.recovery_action_id != attempt.recovery_action_id
        ):
            raise StorageIntegrityError(
                "cooldown_waits source fields must match recovery attempt"
            )
        if (
            wait.consumed_at is not None
            or wait.resulting_recovery_activation_id is not None
        ):
            raise StorageIntegrityError(
                "cooldown_waits pending wait must not carry consumed fields"
            )
        if attempt.phase != "pending_cooldown":
            raise StorageIntegrityError(
                "cooldown_waits pending wait must match pending_cooldown attempt"
            )
        return
    # Consumed waits are historical. Later legal source failures can advance
    # the same lineage recovery attempt beyond the attempt snapshot in the wait.
    if wait.consumed_at is None or wait.resulting_recovery_activation_id is None:
        raise StorageIntegrityError(
            "cooldown_waits consumed wait must carry consumed fields"
        )
    if wait.consumed_at < wait.due_at:
        raise StorageIntegrityError("cooldown_waits.consumed_at must be due")
    _validate_reference(
        "cooldown_waits.resulting_recovery_activation_id",
        wait.resulting_recovery_activation_id,
        frozenset(state.activations),
        "activations",
    )
    activation = state.activations[wait.resulting_recovery_activation_id]
    if (
        activation.plan_ref != wait.plan_ref
        or activation.lineage_id != wait.lineage_id
        or activation.work_item_id != wait.source_work_item_id
        or activation.stage_kind_id != policy_recovery_stage_kind_id
        or activation.stage_kind_id != wait.target_stage_kind_id
        or activation.graph_node_id != wait.target_graph_node_id
        or activation.runner_binding_id != wait.target_runner_binding_id
    ):
        raise StorageIntegrityError(
            "cooldown_waits resulting activation must match wait target"
        )
    if activation.created_by_input_id != wait.consumed_input_id:
        raise StorageIntegrityError(
            "cooldown_waits resulting activation created_by_input_id must match "
            "consumed_input_id"
        )


def _validate_cooldown_wait_source_context(
    state: RuntimeState,
    *,
    wait: CooldownWaitRecord,
) -> None:
    run = state.runs[wait.source_run_id]
    work_item = state.work_items[wait.source_work_item_id]
    activation = state.activations[wait.source_activation_id]
    if run.work_item_id != wait.source_work_item_id:
        raise StorageIntegrityError(
            "cooldown_waits source run must match source work item"
        )
    if activation.work_item_id != wait.source_work_item_id:
        raise StorageIntegrityError(
            "cooldown_waits source activation must match source work item"
        )
    if run.activation_id != wait.source_activation_id:
        raise StorageIntegrityError(
            "cooldown_waits source run must match source activation"
        )
    if activation.claimed_by_run_id != wait.source_run_id:
        raise StorageIntegrityError(
            "cooldown_waits source activation must be claimed by source run"
        )
    if work_item.lineage_id != wait.lineage_id:
        raise StorageIntegrityError(
            "cooldown_waits source work item lineage must match wait lineage"
        )
    if (
        run.run_ref.plan_ref != wait.plan_ref
        or work_item.ref.plan_ref != wait.plan_ref
        or activation.plan_ref != wait.plan_ref
    ):
        raise StorageIntegrityError(
            "cooldown_waits source context PlanRef must match wait PlanRef"
        )


def _validate_operator_waits(
    state: RuntimeState,
    *,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    active_keys: set[tuple[str, str]] = set()
    resolved_target_owners: set[tuple[object, str, str]] = set()
    artifact_ids = frozenset(state.artifacts)
    closed_work_item_ids = frozenset(state.closed_work_items)
    for wait in state.operator_waits.values():
        _validate_plan_ref(
            "operator_waits",
            "operator_waits.plan_authority_fingerprint",
            wait.selected_plan_ref,
            state.admitted_plans,
        )
        if (
            wait.selected_plan_fingerprint
            != wait.selected_plan_ref.authority_fingerprint
        ):
            raise StorageIntegrityError(
                "operator_waits selected fingerprint must match PlanRef"
            )
        selected_plan = state.admitted_plans[
            wait.selected_plan_ref.authority_fingerprint
        ].selected_plan
        selected_wait = next(
            (
                declaration
                for declaration in selected_plan.operator_waits
                if declaration.id == wait.operator_wait_id
            ),
            None,
        )
        if selected_wait is None:
            raise StorageIntegrityError(
                "operator_waits.operator_wait_id must reference selected operator_waits"
            )
        if wait.source_action_id not in selected_wait.source_action_ids:
            raise StorageIntegrityError(
                "operator_waits.source_action_id must reference wait source actions"
            )
        source_action = next(
            (
                action
                for action in selected_plan.terminal_actions
                if action.id == wait.source_action_id
            ),
            None,
        )
        if source_action is None or source_action.action_kind != "operator_wait":
            raise StorageIntegrityError(
                "operator_waits.source_action_id must reference operator_wait action"
            )
        expected_wait_id = _operator_wait_record_id(
            authority_fingerprint=wait.selected_plan_ref.authority_fingerprint,
            operator_wait_id=str(wait.operator_wait_id),
            lineage_id=wait.lineage_id,
            created_by_input_id=wait.created_input_id,
        )
        if wait.wait_id != expected_wait_id:
            raise StorageIntegrityError("operator_waits.wait_id must match authority")
        _validate_sha256_digest(
            "operator_waits.created_input_payload_digest",
            wait.created_input_payload_digest,
        )
        if wait.resolved_input_payload_digest is not None:
            _validate_sha256_digest(
                "operator_waits.resolved_input_payload_digest",
                wait.resolved_input_payload_digest,
            )
        if wait.payload_digest is not None:
            _validate_sha256_digest(
                "operator_waits.payload_digest",
                wait.payload_digest,
            )
        if wait.actor_kind is not None and wait.actor_kind != selected_wait.actor_kind:
            raise StorageIntegrityError(
                "operator_waits.actor_kind must match selected operator_wait"
            )
        if (
            wait.status in {"resolved", "superseded"}
            and wait.target_work_item_id is not None
            and wait.target_activation_id is not None
        ):
            owner_key = (
                wait.selected_plan_ref,
                wait.target_work_item_id,
                wait.target_activation_id,
            )
            if owner_key in resolved_target_owners:
                raise StorageIntegrityError(
                    "operator_waits resolved target ownership must be unique"
                )
            resolved_target_owners.add(owner_key)
        if "revise_recorded_source" in set(selected_wait.allowed_resolution_kinds):
            _validate_selected_operator_wait_revise_route_schema(
                selected_plan,
                selected_wait,
            )
        if wait.status == "active":
            key = (
                wait.selected_plan_ref.authority_fingerprint,
                wait.lineage_id,
            )
            if key in active_keys:
                raise StorageIntegrityError(
                    "operator_waits active plan/lineage key must be unique"
                )
            active_keys.add(key)
        _validate_operator_wait_source_context(
            state,
            wait=wait,
            work_item_ids=work_item_ids,
            activation_ids=activation_ids,
            run_ids=run_ids,
        )
        _validate_operator_wait_source_state(
            wait=wait,
            selected_wait=selected_wait,
            closed_work_item_ids=closed_work_item_ids,
        )
        if (
            source_action.artifact_schema_id is not None
            and wait.source_artifact_id is None
        ):
            raise StorageIntegrityError(
                "operator_waits source artifact is required by source action"
            )
        if wait.source_artifact_id is not None:
            _validate_reference(
                "operator_waits.source_artifact_id",
                wait.source_artifact_id,
                artifact_ids,
                "artifacts",
            )
            artifact = state.artifacts[wait.source_artifact_id]
            artifact_work_item = state.work_items[artifact.work_item_id]
            artifact_run = state.runs[artifact.source_run_id]
            if (
                artifact.work_item_id != wait.source_work_item_id
                or artifact.source_run_id != wait.source_run_id
                or artifact.source_action_id != wait.source_action_id
                or artifact.source_stage_kind_id != wait.source_stage_kind_id
                or artifact.source_graph_node_id != wait.source_graph_node_id
                or artifact.created_by_input_id != wait.created_input_id
                or artifact.payload_digest != artifact_payload_digest(artifact.payload)
                or artifact_work_item.ref.plan_ref != wait.selected_plan_ref
                or artifact_work_item.lineage_id != wait.lineage_id
                or artifact_run.run_ref.plan_ref != wait.selected_plan_ref
            ):
                raise StorageIntegrityError(
                    "operator_waits source artifact provenance must match wait source"
                )
            if artifact.schema_id != source_action.artifact_schema_id:
                raise StorageIntegrityError(
                    "operator_waits source artifact schema must match source action"
                )
        _validate_operator_wait_resolution_audit(wait)
        if wait.target_work_item_id is not None:
            _validate_reference(
                "operator_waits.target_work_item_id",
                wait.target_work_item_id,
                work_item_ids,
                "work_items",
            )
        if wait.target_activation_id is not None:
            _validate_reference(
                "operator_waits.target_activation_id",
                wait.target_activation_id,
                activation_ids,
                "activations",
            )
            target_activation = state.activations[wait.target_activation_id]
            target_work_item_id = (
                wait.target_work_item_id
                if wait.target_work_item_id is not None
                else wait.source_work_item_id
            )
            if (
                target_activation.work_item_id != target_work_item_id
                or target_activation.lineage_id != wait.lineage_id
                or target_activation.plan_ref != wait.selected_plan_ref
                or target_activation.created_by_input_id != wait.resolved_input_id
            ):
                raise StorageIntegrityError(
                    "operator_waits target activation must match resolution"
                )
        if wait.resolution_kind == "revise_recorded_source":
            if wait.target_work_item_id is None:
                raise StorageIntegrityError(
                    "operator_waits revise target work item is required"
                )
            target_work_item = state.work_items[wait.target_work_item_id]
            if (
                target_work_item.lineage_id != wait.lineage_id
                or target_work_item.ref.plan_ref != wait.selected_plan_ref
                or target_work_item.created_by_input_id != wait.resolved_input_id
                or wait.payload_digest
                != operator_payload_digest(target_work_item.payload)
            ):
                raise StorageIntegrityError(
                    "operator_waits revise target must match created work"
                )
            _validate_operator_wait_revise_target_authority(
                state,
                wait=wait,
                selected_wait=selected_wait,
            )
        for closed_work_item_id in wait.closed_work_item_ids:
            _validate_reference(
                "operator_waits.closed_work_item_ids",
                closed_work_item_id,
                closed_work_item_ids,
                "closed_work_items",
            )
        if wait.status == "active":
            if (
                wait.resolved_input_id is not None
                or wait.resolved_input_payload_digest is not None
                or wait.actor_id is not None
                or wait.actor_kind is not None
                or wait.resolution_kind is not None
                or wait.target_work_item_id is not None
                or wait.target_activation_id is not None
                or wait.closed_work_item_ids
                or wait.payload_digest is not None
                or wait.payload_reference is not None
            ):
                raise StorageIntegrityError(
                    "operator_waits active wait must not carry resolution fields"
                )
        elif wait.status in {"resolved", "superseded"}:
            if (
                wait.resolved_input_id is None
                or wait.resolved_input_payload_digest is None
                or wait.actor_id is None
                or wait.actor_kind is None
                or wait.resolution_kind is None
                or wait.payload_digest is None
            ):
                raise StorageIntegrityError(
                    "operator_waits resolved wait must carry resolution fields"
                )
        else:
            raise StorageIntegrityError("operator_waits.status is unsupported")
        if (
            wait.resolution_kind is not None
            and wait.resolution_kind not in set(selected_wait.allowed_resolution_kinds)
        ):
            raise StorageIntegrityError(
                "operator_waits.resolution_kind must be selected"
            )
        _validate_operator_wait_resolution_source_state(
            state=state,
            wait=wait,
            closed_work_item_ids=closed_work_item_ids,
        )


def _validate_operator_wait_source_state(
    *,
    wait: OperatorWaitRecord,
    selected_wait: OperatorWaitDeclaration,
    closed_work_item_ids: frozenset[str],
) -> None:
    if wait.status != "active":
        return
    source_is_closed = wait.source_work_item_id in closed_work_item_ids
    if selected_wait.source_work_item_behavior == "leave_open" and source_is_closed:
        raise StorageIntegrityError(
            "operator_waits leave_open source must remain open"
        )
    if (
        selected_wait.source_work_item_behavior == "close_on_create"
        and not source_is_closed
    ):
        raise StorageIntegrityError(
            "operator_waits close_on_create source must be closed"
        )


def _validate_operator_wait_resolution_source_state(
    *,
    state: RuntimeState,
    wait: OperatorWaitRecord,
    closed_work_item_ids: frozenset[str],
) -> None:
    if wait.status == "active":
        return
    source_is_closed = wait.source_work_item_id in closed_work_item_ids
    if wait.resolution_kind == "resume_recorded_source":
        if source_is_closed:
            _validate_operator_wait_resume_source_close_provenance(
                state,
                wait=wait,
                closed=state.closed_work_items[wait.source_work_item_id],
            )
        return
    if wait.resolution_kind in {
        "close_recorded_source",
        "revise_recorded_source",
    } and not source_is_closed:
        raise StorageIntegrityError(
            "operator_waits resolved source must be closed by resolution"
        )


def _validate_operator_wait_resume_source_close_provenance(
    state: RuntimeState,
    *,
    wait: OperatorWaitRecord,
    closed: ClosedWorkItemRecord,
) -> None:
    if closed.close_kind == "terminal_action":
        close_run = (
            state.runs.get(closed.source_run_id)
            if closed.source_run_id is not None
            else None
        )
        if (
            closed.source_run_id == wait.source_run_id
            or close_run is None
            or close_run.activation_id != wait.target_activation_id
        ):
            raise StorageIntegrityError(
                "operator_waits resume source close must originate "
                "from resumed activation"
            )
        if closed.action_id == wait.source_action_id:
            raise StorageIntegrityError(
                "operator_waits resume source close action must not be "
                "wait source action"
            )
        selected_plan = state.admitted_plans[
            wait.selected_plan_ref.authority_fingerprint
        ].selected_plan
        close_action = next(
            (
                action
                for action in selected_plan.terminal_actions
                if action.id == closed.action_id
            ),
            None,
        )
        if (
            close_action is None
            or close_action.action_kind != "close"
            or close_action.stage_kind_id != close_run.stage_kind_id
        ):
            raise StorageIntegrityError(
                "operator_waits resume source close action must reference "
                "selected close action"
            )
    if closed.created_by_input_id in {wait.created_input_id, wait.resolved_input_id}:
        raise StorageIntegrityError(
            "operator_waits resume source close input must be later than "
            "wait creation and resolution"
        )


def _validate_operator_wait_resume_target_selected_authority(
    state: RuntimeState,
) -> None:
    for wait in state.operator_waits.values():
        if (
            wait.resolution_kind != "resume_recorded_source"
            or wait.target_activation_id is None
        ):
            continue
        target_activation = state.activations.get(wait.target_activation_id)
        if target_activation is None:
            continue
        if target_activation.work_item_id != wait.source_work_item_id:
            raise StorageIntegrityError(
                "operator_waits resume target work item must match recorded source"
            )
        if target_activation.queue_family_id != wait.source_queue_family_id:
            raise StorageIntegrityError(
                "operator_waits resume target queue family must match recorded source"
            )
        if target_activation.graph_node_id != wait.source_graph_node_id:
            raise StorageIntegrityError(
                "operator_waits resume target graph node must match recorded source"
            )
        if target_activation.stage_kind_id != wait.source_stage_kind_id:
            raise StorageIntegrityError(
                "operator_waits resume target stage kind must match recorded source"
            )
        if target_activation.runner_binding_id != wait.source_runner_binding_id:
            raise StorageIntegrityError(
                "operator_waits resume target runner binding must match recorded source"
            )


def _validate_operator_wait_revise_target_authority(
    state: RuntimeState,
    *,
    wait: OperatorWaitRecord,
    selected_wait: OperatorWaitDeclaration,
) -> None:
    if wait.target_work_item_id is None or wait.target_activation_id is None:
        return
    target_work_item = state.work_items[wait.target_work_item_id]
    target_activation = state.activations[wait.target_activation_id]
    if target_work_item.queue_family_id != selected_wait.target_queue_family_id:
        raise StorageIntegrityError(
            "operator_waits revise target queue family must match "
            "selected operator_wait"
        )
    if target_activation.graph_node_id != selected_wait.target_graph_node_id:
        raise StorageIntegrityError(
            "operator_waits revise target graph node must match selected operator_wait"
        )
    if target_activation.stage_kind_id != selected_wait.target_stage_kind_id:
        raise StorageIntegrityError(
            "operator_waits revise target stage kind must match selected operator_wait"
        )
    if target_activation.runner_binding_id != selected_wait.target_runner_binding_id:
        raise StorageIntegrityError(
            "operator_waits revise target runner binding must match "
            "selected operator_wait"
        )


def _validate_selected_operator_wait_revise_route_schema(
    selected_plan: SelectedCompiledPlan,
    selected_wait: OperatorWaitDeclaration,
) -> None:
    payload_schema_id = selected_wait.payload_schema_id
    queue_family_id = selected_wait.target_queue_family_id
    stage_kind_id = selected_wait.target_stage_kind_id
    graph_node_id = selected_wait.target_graph_node_id
    runner_binding_id = selected_wait.target_runner_binding_id
    if (
        payload_schema_id is None
        or queue_family_id is None
        or stage_kind_id is None
        or graph_node_id is None
        or runner_binding_id is None
    ):
        raise StorageIntegrityError(
            "operator_waits selected revise target must be complete"
        )
    queue_family_ids = {family.id for family in selected_plan.queue_families}
    graph_node_ids = {
        node_id for graph in selected_plan.graphs for node_id in graph.node_ids
    }
    stage_by_id = {stage.id: stage for stage in selected_plan.stage_kinds}
    runner_by_id = {runner.id: runner for runner in selected_plan.runner_bindings}
    if queue_family_id not in queue_family_ids:
        raise StorageIntegrityError(
            "operator_waits selected revise target queue family must reference "
            "queue_families"
        )
    if graph_node_id not in graph_node_ids:
        raise StorageIntegrityError(
            "operator_waits selected revise target graph node must reference graphs"
        )
    target_stage = stage_by_id.get(stage_kind_id)
    if target_stage is None:
        raise StorageIntegrityError(
            "operator_waits selected revise target stage kind must reference "
            "stage_kinds"
        )
    target_runner = runner_by_id.get(runner_binding_id)
    if target_runner is None:
        raise StorageIntegrityError(
            "operator_waits selected revise target runner binding must reference "
            "runner_bindings"
        )
    if queue_family_id not in target_stage.input_queue_family_ids:
        raise StorageIntegrityError(
            "operator_waits selected revise target queue family must be target "
            "stage input"
        )
    if target_stage.runner_binding_id != runner_binding_id:
        raise StorageIntegrityError(
            "operator_waits selected revise target runner binding must match "
            "target stage"
        )
    if stage_kind_id not in target_runner.stage_kind_ids:
        raise StorageIntegrityError(
            "operator_waits selected revise target runner binding must list "
            "target stage"
        )
    if (
        queue_family_id,
        stage_kind_id,
        graph_node_id,
        runner_binding_id,
    ) not in _selected_route_targets(selected_plan):
        raise StorageIntegrityError(
            "operator_waits selected revise target must match selected route"
        )
    target_schema_ids = _selected_route_payload_schema_ids(
        selected_plan,
        queue_family_id=queue_family_id,
        stage_kind_id=stage_kind_id,
        graph_node_id=graph_node_id,
        runner_binding_id=runner_binding_id,
    )
    if target_schema_ids and payload_schema_id not in target_schema_ids:
        raise StorageIntegrityError(
            "operator_waits selected revise payload schema must match target route"
        )


def _selected_route_targets(
    selected_plan: SelectedCompiledPlan,
) -> frozenset[tuple[object, object, str, object]]:
    route_targets: set[tuple[object, object, str, object]] = set()
    selected_routes: tuple[
        ExternalEnqueueRouteDeclaration | GeneratedWorkRouteDeclaration,
        ...,
    ] = (
        *selected_plan.external_enqueue_routes,
        *selected_plan.generated_work_routes,
    )
    for route in selected_routes:
        route_targets.add(
            (
                route.queue_family_id,
                route.stage_kind_id,
                route.graph_node_id,
                route.runner_binding_id,
            )
        )
    route_targets.update(
        (
            behavior.request_queue_family_id,
            behavior.target_stage_kind_id,
            behavior.target_graph_node_id,
            behavior.runner_binding_id,
        )
        for behavior in selected_plan.completion_behaviors
    )
    route_targets.update(
        (
            policy.target_queue_family_id,
            policy.target_stage_kind_id,
            policy.target_graph_node_id,
            policy.target_runner_binding_id,
        )
        for policy in selected_plan.remediation_policies
    )
    for action in selected_plan.terminal_actions:
        if (
            action.emitted_queue_family_id is not None
            and action.target_stage_kind_id is not None
            and action.target_graph_node_id is not None
            and action.runner_binding_id is not None
        ):
            route_targets.add(
                (
                    action.emitted_queue_family_id,
                    action.target_stage_kind_id,
                    action.target_graph_node_id,
                    action.runner_binding_id,
                )
            )
    return frozenset(route_targets)


def _selected_route_payload_schema_ids(
    selected_plan: SelectedCompiledPlan,
    *,
    queue_family_id: object,
    stage_kind_id: object,
    graph_node_id: object,
    runner_binding_id: object,
) -> frozenset[ArtifactSchemaId]:
    schema_ids: set[ArtifactSchemaId] = set()
    selected_routes: tuple[
        ExternalEnqueueRouteDeclaration | GeneratedWorkRouteDeclaration,
        ...,
    ] = (
        *selected_plan.external_enqueue_routes,
        *selected_plan.generated_work_routes,
    )
    for route in selected_routes:
        if (
            route.queue_family_id == queue_family_id
            and route.stage_kind_id == stage_kind_id
            and route.graph_node_id == graph_node_id
            and route.runner_binding_id == runner_binding_id
            and route.payload_schema_id is not None
        ):
            schema_ids.add(route.payload_schema_id)
    for action in selected_plan.terminal_actions:
        if (
            action.emitted_queue_family_id == queue_family_id
            and action.target_stage_kind_id == stage_kind_id
            and action.target_graph_node_id == graph_node_id
            and action.runner_binding_id == runner_binding_id
            and action.artifact_schema_id is not None
        ):
            schema_ids.add(action.artifact_schema_id)
    return frozenset(schema_ids)


def _validate_operator_wait_resolution_audit(wait: OperatorWaitRecord) -> None:
    if wait.status == "active":
        return
    if wait.resolution_kind == "resume_recorded_source":
        if (
            wait.target_activation_id is None
            or wait.target_work_item_id is not None
            or wait.closed_work_item_ids
            or wait.payload_digest != operator_payload_digest({})
            or wait.payload_reference is not None
        ):
            raise StorageIntegrityError(
                "operator_waits resume audit fields are incoherent"
            )
    elif wait.resolution_kind == "close_recorded_source":
        if (
            wait.target_activation_id is not None
            or wait.target_work_item_id is not None
            or wait.source_work_item_id not in set(wait.closed_work_item_ids)
            or wait.payload_digest != operator_payload_digest({})
            or wait.payload_reference is not None
        ):
            raise StorageIntegrityError(
                "operator_waits close audit fields are incoherent"
            )
    elif wait.resolution_kind == "revise_recorded_source":
        if (
            wait.target_activation_id is None
            or wait.target_work_item_id is None
            or wait.source_work_item_id not in set(wait.closed_work_item_ids)
            or wait.payload_digest in {None, operator_payload_digest({})}
            or wait.payload_reference != f"work_item:{wait.target_work_item_id}:payload"
        ):
            raise StorageIntegrityError(
                "operator_waits revise audit fields are incoherent"
            )


def _validate_operator_wait_source_context(
    state: RuntimeState,
    *,
    wait: OperatorWaitRecord,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    _validate_reference(
        "operator_waits.source_work_item_id",
        wait.source_work_item_id,
        work_item_ids,
        "work_items",
    )
    _validate_reference(
        "operator_waits.source_activation_id",
        wait.source_activation_id,
        activation_ids,
        "activations",
    )
    _validate_reference(
        "operator_waits.source_run_id",
        wait.source_run_id,
        run_ids,
        "runs",
    )
    work_item = state.work_items[wait.source_work_item_id]
    activation = state.activations[wait.source_activation_id]
    run = state.runs[wait.source_run_id]
    if work_item.lineage_id != wait.lineage_id:
        raise StorageIntegrityError(
            "operator_waits.lineage_id must match source work item lineage"
        )
    if activation.work_item_id != wait.source_work_item_id:
        raise StorageIntegrityError(
            "operator_waits source activation must reference source work item"
        )
    if run.work_item_id != wait.source_work_item_id:
        raise StorageIntegrityError(
            "operator_waits source run must reference source work item"
        )
    if run.activation_id != wait.source_activation_id:
        raise StorageIntegrityError(
            "operator_waits source run must reference source activation"
        )
    if (
        work_item.ref.plan_ref != wait.selected_plan_ref
        or activation.plan_ref != wait.selected_plan_ref
        or run.run_ref.plan_ref != wait.selected_plan_ref
    ):
        raise StorageIntegrityError(
            "operator_waits source context PlanRef must match wait PlanRef"
        )
    if (
        work_item.queue_family_id != wait.source_queue_family_id
        or activation.graph_node_id != wait.source_graph_node_id
        or run.stage_kind_id != wait.source_stage_kind_id
        or run.runner_binding_id != wait.source_runner_binding_id
    ):
        raise StorageIntegrityError(
            "operator_waits source context fields must match recorded source"
        )
    selected_plan = state.admitted_plans[
        wait.selected_plan_ref.authority_fingerprint
    ].selected_plan
    action_ids = frozenset(
        action.id
        for action in selected_plan.terminal_actions
        if action.action_kind == "operator_wait"
    )
    if wait.source_action_id not in action_ids:
        raise StorageIntegrityError(
            "operator_waits.source_action_id must reference operator_wait action"
        )


def _validate_sha256_digest(column: str, value: str) -> None:
    hex_value = value.removeprefix("sha256:")
    if (
        not value.startswith("sha256:")
        or len(hex_value) != 64
        or any(character not in "0123456789abcdef" for character in hex_value)
    ):
        raise StorageIntegrityError(f"{column} must be sha256 digest")


def _validate_counters(state: RuntimeState) -> None:
    for counter in state.counters.values():
        _validate_plan_ref(
            "counters",
            "counters.plan_authority_fingerprint",
            counter.selected_plan_ref,
            state.admitted_plans,
        )
        selected_plan = state.admitted_plans[
            counter.selected_plan_ref.authority_fingerprint
        ].selected_plan
        if selected_plan.lineage_policy == "none":
            raise StorageIntegrityError(
                "counters require a selected plan with lineage authority"
            )
        selected_counter = next(
            (
                declaration
                for declaration in selected_plan.counters
                if declaration.id == counter.counter_id
            ),
            None,
        )
        if selected_counter is None:
            raise StorageIntegrityError(
                "counters.counter_id must reference selected counters"
            )
        expected_record_id = (
            "counter:"
            f"{counter.selected_plan_ref.authority_fingerprint}:"
            f"{counter.counter_id}:"
            f"{counter.lineage_id}"
        )
        if counter.record_id != expected_record_id:
            raise StorageIntegrityError(
                "counters.record_id must match plan/counter/lineage key"
            )
        if counter.value <= 0:
            raise StorageIntegrityError("counters.value must be positive")


def _validate_lineage_quarantines(
    state: RuntimeState,
    *,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    active_keys: set[tuple[str, str, str]] = set()
    for quarantine in state.lineage_quarantines.values():
        _validate_plan_ref(
            "lineage_quarantines",
            "lineage_quarantines.plan_authority_fingerprint",
            quarantine.selected_plan_ref,
            state.admitted_plans,
        )
        if (
            quarantine.selected_plan_fingerprint
            != quarantine.selected_plan_ref.authority_fingerprint
        ):
            raise StorageIntegrityError(
                "lineage_quarantines selected fingerprint must match PlanRef"
            )
        key = (
            quarantine.selected_plan_fingerprint,
            str(quarantine.policy_id),
            quarantine.lineage_id,
        )
        if quarantine.status == "active" and key in active_keys:
            raise StorageIntegrityError(
                "lineage_quarantines active plan/policy/lineage key must be unique"
            )
        if quarantine.status == "active":
            active_keys.add(key)
        expected_quarantine_id = (
            "lineage-quarantine:"
            f"{quarantine.selected_plan_fingerprint}:"
            f"{quarantine.policy_id}:"
            f"{quarantine.lineage_id}:"
            f"{quarantine.recovery_attempt_record_id}"
        )
        if quarantine.quarantine_id != expected_quarantine_id:
            raise StorageIntegrityError(
                "lineage_quarantines.quarantine_id must match plan/policy/lineage"
            )
        if quarantine.actor_kind != "runtime":
            raise StorageIntegrityError(
                "lineage_quarantines.actor_kind must be runtime"
            )
        if quarantine.status == "active":
            if quarantine.superseded_input_id is not None:
                raise StorageIntegrityError(
                    "lineage_quarantines active status must not be superseded"
                )
        elif quarantine.status == "superseded":
            if quarantine.superseded_input_id is None:
                raise StorageIntegrityError(
                    "lineage_quarantines superseded status requires input"
                )
        else:
            raise StorageIntegrityError("lineage_quarantines.status is unsupported")

        _validate_reference(
            "lineage_quarantines.recovery_attempt_record_id",
            quarantine.recovery_attempt_record_id,
            frozenset(state.recovery_attempts),
            "recovery_attempts",
        )
        _validate_reference(
            "lineage_quarantines.original_source_run_id",
            quarantine.original_source_run_id,
            run_ids,
            "runs",
        )
        _validate_reference(
            "lineage_quarantines.original_source_work_item_id",
            quarantine.original_source_work_item_id,
            work_item_ids,
            "work_items",
        )
        _validate_reference(
            "lineage_quarantines.original_source_activation_id",
            quarantine.original_source_activation_id,
            activation_ids,
            "activations",
        )
        _validate_reference(
            "lineage_quarantines.emitting_recovery_activation_id",
            quarantine.emitting_recovery_activation_id,
            activation_ids,
            "activations",
        )
        _validate_reference(
            "lineage_quarantines.emitting_recovery_run_id",
            quarantine.emitting_recovery_run_id,
            run_ids,
            "runs",
        )

        admitted_plan = state.admitted_plans[quarantine.selected_plan_fingerprint]
        policy = next(
            (
                policy
                for policy in admitted_plan.selected_plan.recovery_policies
                if policy.id == quarantine.policy_id
            ),
            None,
        )
        if policy is None:
            raise StorageIntegrityError(
                "lineage_quarantines.policy_id must reference selected "
                "recovery_policies"
            )
        if quarantine.action_id not in policy.quarantine_action_ids:
            raise StorageIntegrityError(
                "lineage_quarantines.action_id must reference policy "
                "quarantine_action_ids"
            )
        _validate_lineage_quarantine_attempt_context(
            state,
            quarantine=quarantine,
            policy_recovery_stage_kind_id=policy.recovery_stage_kind_id,
        )


def _validate_lineage_quarantine_attempt_context(
    state: RuntimeState,
    *,
    quarantine: LineageQuarantineRecord,
    policy_recovery_stage_kind_id: StageKindId,
) -> None:
    attempt = state.recovery_attempts[quarantine.recovery_attempt_record_id]
    if (
        attempt.plan_ref != quarantine.selected_plan_ref
        or attempt.policy_id != quarantine.policy_id
        or attempt.lineage_id != quarantine.lineage_id
    ):
        raise StorageIntegrityError(
            "lineage_quarantines must match recovery attempt key"
        )
    if quarantine.status == "active" and attempt.phase != "quarantine_eligible":
        raise StorageIntegrityError(
            "lineage_quarantines active record must match quarantine_eligible attempt"
        )
    if quarantine.attempt_count != attempt.attempt_count:
        raise StorageIntegrityError(
            "lineage_quarantines.attempt_count must match recovery attempt"
        )
    if (
        quarantine.original_source_run_id != attempt.source_run_id
        or quarantine.original_source_work_item_id != attempt.source_work_item_id
        or quarantine.original_source_activation_id != attempt.source_activation_id
    ):
        raise StorageIntegrityError(
            "lineage_quarantines original source must match recovery attempt"
        )
    emits_from_latest_recovery = (
        attempt.latest_recovery_activation_id is not None
        and attempt.latest_recovery_run_id is not None
    )
    if emits_from_latest_recovery:
        if (
            quarantine.emitting_recovery_activation_id
            != attempt.latest_recovery_activation_id
            or quarantine.emitting_recovery_run_id != attempt.latest_recovery_run_id
        ):
            raise StorageIntegrityError(
                "lineage_quarantines emitting recovery context must match "
                "latest attempt"
            )
    elif (
        quarantine.emitting_recovery_activation_id != attempt.source_activation_id
        or quarantine.emitting_recovery_run_id != attempt.source_run_id
    ):
        raise StorageIntegrityError(
            "lineage_quarantines runtime threshold emitter must match source"
        )
    original_run = state.runs[quarantine.original_source_run_id]
    original_work_item = state.work_items[quarantine.original_source_work_item_id]
    original_activation = state.activations[quarantine.original_source_activation_id]
    emitting_run = state.runs[quarantine.emitting_recovery_run_id]
    emitting_activation = state.activations[
        quarantine.emitting_recovery_activation_id
    ]
    if (
        original_run.work_item_id != original_work_item.ref.work_item_id
        or original_run.activation_id != original_activation.activation_id
        or original_activation.work_item_id != original_work_item.ref.work_item_id
        or original_work_item.lineage_id != quarantine.lineage_id
    ):
        raise StorageIntegrityError(
            "lineage_quarantines original source references must be coherent"
        )
    if (
        emitting_run.activation_id != emitting_activation.activation_id
        or emitting_run.work_item_id != original_work_item.ref.work_item_id
        or emitting_activation.work_item_id != original_work_item.ref.work_item_id
        or emitting_activation.lineage_id != quarantine.lineage_id
        or emitting_run.run_ref.plan_ref != quarantine.selected_plan_ref
        or emitting_activation.plan_ref != quarantine.selected_plan_ref
    ):
        raise StorageIntegrityError(
            "lineage_quarantines emitting recovery references must be coherent"
        )
    if emits_from_latest_recovery and (
        emitting_run.stage_kind_id != policy_recovery_stage_kind_id
        or emitting_activation.stage_kind_id != policy_recovery_stage_kind_id
    ):
        raise StorageIntegrityError(
            "lineage_quarantines emitting recovery references must be coherent"
        )


def _validate_operator_interventions(
    state: RuntimeState,
    *,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    for intervention in state.operator_interventions.values():
        _validate_plan_ref(
            "operator_interventions",
            "operator_interventions.plan_authority_fingerprint",
            intervention.selected_plan_ref,
            state.admitted_plans,
        )
        if (
            intervention.selected_plan_fingerprint
            != intervention.selected_plan_ref.authority_fingerprint
        ):
            raise StorageIntegrityError(
                "operator_interventions selected fingerprint must match PlanRef"
            )
        if intervention.actor_kind != "local_operator":
            raise StorageIntegrityError("operator_interventions.actor_kind")
        _validate_reference(
            "operator_interventions.quarantine_id",
            intervention.quarantine_id,
            frozenset(
                quarantine.quarantine_id
                for quarantine in state.lineage_quarantines.values()
            ),
            "lineage_quarantines",
        )
        _validate_reference(
            "operator_interventions.recovery_attempt_record_id",
            intervention.recovery_attempt_record_id,
            frozenset(state.recovery_attempts),
            "recovery_attempts",
        )
        quarantine = next(
            quarantine
            for quarantine in state.lineage_quarantines.values()
            if quarantine.quarantine_id == intervention.quarantine_id
        )
        attempt = state.recovery_attempts[intervention.recovery_attempt_record_id]
        if (
            intervention.selected_plan_ref != quarantine.selected_plan_ref
            or intervention.selected_plan_ref != attempt.plan_ref
            or intervention.lineage_id != quarantine.lineage_id
            or intervention.lineage_id != attempt.lineage_id
            or intervention.recovery_attempt_count != attempt.attempt_count
        ):
            raise StorageIntegrityError(
                "operator_interventions must match quarantine and recovery attempt"
            )
        if (
            intervention.policy_id != quarantine.policy_id
            or intervention.policy_id != attempt.policy_id
        ):
            raise StorageIntegrityError(
                "operator_interventions.policy_id must match quarantine and attempt"
            )
        if attempt.phase != "resolved":
            raise StorageIntegrityError(
                "operator_interventions recovery attempt must be resolved"
            )
        admitted_plan = state.admitted_plans[
            intervention.selected_plan_ref.authority_fingerprint
        ]
        option = next(
            (
                option
                for option in admitted_plan.selected_plan.intervention_options
                if str(option.id) == intervention.option_id
            ),
            None,
        )
        if option is None:
            raise StorageIntegrityError(
                "operator_interventions.option_id must reference selected "
                "intervention_options"
            )
        if str(option.policy_id) != str(intervention.policy_id):
            raise StorageIntegrityError(
                "operator_interventions.policy_id must match selected option"
            )
        if option.option_kind != intervention.kind:
            raise StorageIntegrityError(
                "operator_interventions.kind must match selected option"
            )
        if (
            option.attempt_effect != intervention.attempt_effect
            or intervention.attempt_effect != "resolve_attempt"
        ):
            raise StorageIntegrityError(
                "operator_interventions.attempt_effect must match selected option"
            )
        if quarantine.superseded_input_id != intervention.created_by_input_id:
            raise StorageIntegrityError(
                "lineage_quarantines superseded input must match intervention"
            )
        if intervention.kind == "resume_lineage":
            _validate_operator_resume_intervention(
                state,
                intervention_record_id=intervention.record_id,
                target_work_item_id=intervention.target_work_item_id,
                target_activation_id=intervention.target_activation_id,
                attempt=attempt,
                activation_ids=activation_ids,
            )
        elif intervention.kind == "close_lineage":
            _validate_operator_close_intervention(
                state,
                intervention_record_id=intervention.record_id,
                closed_work_item_ids=intervention.closed_work_item_ids,
                closed_activation_ids=intervention.closed_activation_ids,
                closed_run_ids=intervention.closed_run_ids,
                lineage_id=intervention.lineage_id,
                work_item_ids=work_item_ids,
                activation_ids=activation_ids,
                run_ids=run_ids,
            )
        elif intervention.kind == "revise_lineage":
            _validate_operator_revise_intervention(
                state,
                intervention_record_id=intervention.record_id,
                target_work_item_id=intervention.target_work_item_id,
                target_activation_id=intervention.target_activation_id,
                lineage_id=intervention.lineage_id,
                work_item_ids=work_item_ids,
                activation_ids=activation_ids,
            )
        else:
            raise StorageIntegrityError("operator_interventions.kind is unsupported")


def _validate_operator_resume_intervention(
    state: RuntimeState,
    *,
    intervention_record_id: str,
    target_work_item_id: str | None,
    target_activation_id: str | None,
    attempt: RecoveryAttemptRecord,
    activation_ids: frozenset[str],
) -> None:
    if target_work_item_id is not None:
        raise StorageIntegrityError(
            "operator_interventions.target_work_item_id is forbidden for resume"
        )
    if target_activation_id is None:
        raise StorageIntegrityError(
            "operator_interventions.target_activation_id is required for resume"
        )
    _validate_reference(
        "operator_interventions.target_activation_id",
        target_activation_id,
        activation_ids,
        "activations",
    )
    activation = state.activations[target_activation_id]
    if (
        activation.work_item_id != attempt.source_work_item_id
        or activation.lineage_id != attempt.lineage_id
        or activation.plan_ref != attempt.plan_ref
        or activation.queue_family_id != attempt.source_queue_family_id
        or activation.graph_node_id != attempt.source_graph_node_id
        or activation.stage_kind_id != attempt.source_stage_kind_id
        or activation.runner_binding_id != attempt.source_runner_binding_id
    ):
        raise StorageIntegrityError(
            "operator_interventions resume target must match recorded source"
        )
    if activation.created_by_input_id != state.operator_interventions[
        intervention_record_id
    ].created_by_input_id:
        raise StorageIntegrityError(
            "operator_interventions resume target must be created by intervention"
        )


def _validate_operator_close_intervention(
    state: RuntimeState,
    *,
    intervention_record_id: str,
    closed_work_item_ids: tuple[str, ...],
    closed_activation_ids: tuple[str, ...],
    closed_run_ids: tuple[str, ...],
    lineage_id: str,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
    run_ids: frozenset[str],
) -> None:
    intervention = state.operator_interventions[intervention_record_id]
    if intervention.target_work_item_id is not None:
        raise StorageIntegrityError(
            "operator_interventions.target_work_item_id is forbidden for close"
        )
    if not closed_work_item_ids:
        raise StorageIntegrityError(
            "operator_interventions.closed_work_item_ids must not be empty"
        )
    for work_item_id in closed_work_item_ids:
        _validate_reference(
            "operator_interventions.closed_work_item_ids",
            work_item_id,
            work_item_ids,
            "work_items",
        )
        work_item = state.work_items[work_item_id]
        if work_item.lineage_id != lineage_id:
            raise StorageIntegrityError(
                "operator_interventions closed work must match lineage"
            )
        closed = state.closed_work_items.get(work_item_id)
        if (
            closed is None
            or closed.close_kind != "operator_intervention"
            or closed.operator_intervention_record_id != intervention_record_id
        ):
            raise StorageIntegrityError(
                "closed_work_items.operator_intervention_record_id "
                "must reference operator_interventions"
            )
    for activation_id in closed_activation_ids:
        _validate_reference(
            "operator_interventions.closed_activation_ids",
            activation_id,
            activation_ids,
            "activations",
        )
        activation = state.activations[activation_id]
        if activation.work_item_id not in closed_work_item_ids:
            raise StorageIntegrityError(
                "operator_interventions closed activations must match closed work"
            )
    for run_id in closed_run_ids:
        _validate_reference(
            "operator_interventions.closed_run_ids",
            run_id,
            run_ids,
            "runs",
        )
        run = state.runs[run_id]
        if run.work_item_id not in closed_work_item_ids:
            raise StorageIntegrityError(
                "operator_interventions closed runs must match closed work"
            )


def _validate_operator_revise_intervention(
    state: RuntimeState,
    *,
    intervention_record_id: str,
    target_work_item_id: str | None,
    target_activation_id: str | None,
    lineage_id: str,
    work_item_ids: frozenset[str],
    activation_ids: frozenset[str],
) -> None:
    if target_work_item_id is None:
        raise StorageIntegrityError(
            "operator_interventions.target_work_item_id is required for revise"
        )
    if target_activation_id is None:
        raise StorageIntegrityError(
            "operator_interventions.target_activation_id is required for revise"
        )
    _validate_reference(
        "operator_interventions.target_work_item_id",
        target_work_item_id,
        work_item_ids,
        "work_items",
    )
    _validate_reference(
        "operator_interventions.target_activation_id",
        target_activation_id,
        activation_ids,
        "activations",
    )
    intervention = state.operator_interventions[intervention_record_id]
    work_item = state.work_items[target_work_item_id]
    activation = state.activations[target_activation_id]
    if (
        work_item.lineage_id != lineage_id
        or activation.lineage_id != lineage_id
        or activation.work_item_id != target_work_item_id
        or work_item.ref.plan_ref != intervention.selected_plan_ref
        or activation.plan_ref != intervention.selected_plan_ref
        or work_item.created_by_input_id != intervention.created_by_input_id
        or activation.created_by_input_id != intervention.created_by_input_id
    ):
        raise StorageIntegrityError(
            "operator_interventions revise target must match created work"
        )
    if intervention.payload_reference != f"work_item:{target_work_item_id}:payload":
        raise StorageIntegrityError(
            "operator_interventions payload_reference must match revise target"
        )
    if intervention.payload_digest != operator_payload_digest(work_item.payload):
        raise StorageIntegrityError(
            "operator_interventions payload_digest must match revise target"
        )


def _validate_plan_ref(
    table_name: str,
    column: str,
    plan_ref: PlanRef,
    admitted_plans: Mapping[str, AdmittedPlan],
) -> None:
    admitted_plan = admitted_plans.get(plan_ref.authority_fingerprint)
    if admitted_plan is None:
        raise StorageIntegrityError(f"{column} must reference admitted_plan_pins")
    if plan_ref != admitted_plan.plan_ref:
        raise StorageIntegrityError(
            f"{table_name} PlanRef must match admitted plan pin"
        )


def _validate_run_and_work_item_record(
    table_name: str,
    run_id: str,
    work_item_id: str,
    runs: Mapping[str, RunRecord],
    work_items: Mapping[str, WorkItem],
) -> None:
    _validate_reference(
        f"{table_name}.source_run_id",
        run_id,
        frozenset(runs),
        "runs",
    )
    _validate_reference(
        f"{table_name}.work_item_id",
        work_item_id,
        frozenset(work_items),
        "work_items",
    )
    if runs[run_id].work_item_id != work_item_id:
        raise StorageIntegrityError(
            f"{table_name}.source_run_id must reference run for work_item_id"
        )


def _validate_unique(column: str, values: Iterable[object]) -> None:
    seen: set[object] = set()
    for value in values:
        if value in seen:
            raise StorageIntegrityError(f"{column} must be unique")
        seen.add(value)


def validate_transition_rows(rows: Iterable[TransitionRow]) -> None:
    expected_order = 0
    seen_record_ids: set[str] = set()
    for row in rows:
        if row.transition_order != expected_order:
            raise StorageIntegrityError(
                "transitions.transition_order must be contiguous from zero"
            )
        if row.record_id in seen_record_ids:
            raise StorageIntegrityError("transitions.record_id must be unique")
        expected_created_at = f"transition-order:{row.transition_order}"
        if row.created_at != expected_created_at:
            raise StorageIntegrityError(
                "transitions.created_at must match transition_order"
            )
        seen_record_ids.add(row.record_id)
        expected_order += 1


def validate_receipt_transition_rows(
    receipt_rows: Iterable[InputReceiptRow],
    transition_rows_by_record_id: Mapping[str, TransitionRow],
) -> None:
    for row in receipt_rows:
        transition = transition_rows_by_record_id.get(row.transition_id)
        if transition is None:
            raise StorageIntegrityError(
                "input_receipts.transition_id must reference transitions"
            )
        if row.input_id != transition.input_id or row.accepted != transition.accepted:
            raise StorageIntegrityError(
                "input_receipts.transition_id must match receipt input "
                "and accepted flag"
            )


def validate_audit_transition_rows(
    table_name: str,
    rows: Iterable[GovernanceEventRow | TraceRow | RefusalRow],
    transition_rows_by_order: Mapping[int, TransitionRow],
) -> None:
    seen_orders: set[int] = set()
    for row in rows:
        if row.transition_order in seen_orders:
            raise StorageIntegrityError(
                f"{table_name}.transition_order must be unique"
            )
        seen_orders.add(row.transition_order)
        transition = transition_rows_by_order.get(row.transition_order)
        if transition is None:
            raise StorageIntegrityError(
                f"{table_name}.transition_order must reference transitions"
            )
        if (
            row.input_id,
            row.input_kind,
            row.input_family,
        ) != (
            transition.input_id,
            transition.input_kind,
            transition.input_family,
        ):
            raise StorageIntegrityError(
                f"{table_name} transition fields must match transitions"
            )
        _validate_audit_disposition(table_name, row, transition)


def validate_trace_governance_rows(
    trace_rows: Iterable[TraceRow],
    governance_rows_by_order: Mapping[int, GovernanceEventRow],
) -> None:
    for row in trace_rows:
        event = governance_rows_by_order.get(row.transition_order)
        if event is None:
            raise StorageIntegrityError(
                "traces.transition_order must reference governance_events"
            )
        if (
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
        ) != (
            event.input_id,
            event.input_kind,
            event.input_family,
            event.disposition,
            event.plan_fingerprint,
            event.work_item_id,
            event.run_id,
            event.action_id,
            event.authority_source,
            event.refusal_reason,
        ):
            raise StorageIntegrityError(
                "traces must match governance_events for transition_order"
            )


def _validate_audit_disposition(
    table_name: str,
    row: GovernanceEventRow | TraceRow | RefusalRow,
    transition: TransitionRow,
) -> None:
    if isinstance(row, RefusalRow):
        if transition.accepted:
            raise StorageIntegrityError(
                "refusals.transition_order must reference refused transition"
            )
        return
    expected_disposition = "accepted" if transition.accepted else "refused"
    if row.disposition != expected_disposition:
        raise StorageIntegrityError(
            f"{table_name}.disposition must match transition accepted flag"
            )


def _validate_artifact_source_context(
    state: RuntimeState,
    *,
    artifact: ArtifactRecord,
) -> None:
    authenticated = authenticate_artifact_provenance(state, artifact)
    if not isinstance(authenticated, AuthenticatedArtifactProvenance):
        detail = (
            f":{authenticated.detail}"
            if authenticated.detail is not None
            else ""
        )
        raise StorageIntegrityError(
            "artifacts runner-observation provenance invalid: "
            f"{authenticated.reason_code}{detail}"
        )


def _validate_reference(
    column: str,
    value: str,
    valid_values: frozenset[str],
    referenced_table: str,
) -> None:
    if value not in valid_values:
        raise StorageIntegrityError(
            f"{column} must reference {referenced_table}"
        )


__all__ = (
    "completion_diagnostic_matches_current_authority",
    "runner_session_cas_references",
    "runner_result_refusal_chain",
    "validate_completed_runner_evidence",
    "validate_runner_session_context_cas",
    "validate_audit_transition_rows",
    "validate_loaded_runtime_state",
    "validate_receipt_transition_rows",
    "validate_trace_governance_rows",
    "validate_transition_rows",
)
