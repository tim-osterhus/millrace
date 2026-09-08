"""Construction of durable runner-session completion records."""

from __future__ import annotations

import time
from uuid import uuid4

from millrace.contracts.state import (
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
)


def completion_record(
    *,
    session: RunnerSessionRecord,
    terminal_state: str,
    exit_kind: str,
    adapter_outcome_kind: str,
    adapter_error_kind: str | None,
    evidence_digest: str | None,
    diagnostic_digest: str,
    cleanup_disposition: str,
    redaction_policy_id: str,
    primary: RunnerSessionCancellationRecord | None = None,
) -> RunnerSessionCompletionRecord:
    completion_id = f"completion-{uuid4().hex}"
    return RunnerSessionCompletionRecord(
        completion_id=completion_id,
        session_id=session.session_id,
        run_id=session.run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        terminal_state=terminal_state,
        exit_kind=exit_kind,
        adapter_outcome_kind=adapter_outcome_kind,
        adapter_error_kind=adapter_error_kind,
        runner_result_evidence_digest=evidence_digest,
        primary_cancellation_request_id=(
            None if primary is None else primary.request_id
        ),
        cleanup_disposition=cleanup_disposition,
        started_at=session.started_at,
        cancel_requested_at=(None if primary is None else primary.requested_at),
        completed_at=max(time.time_ns(), session.started_at or session.created_at),
        bounds_summary="bounded",
        truncation_metadata="none",
        redaction_policy_id=redaction_policy_id,
        diagnostic_digest=diagnostic_digest,
        application_input_id=f"cli:run.session-completion:{completion_id}",
    )


def context_mutation_completion_record(
    *,
    session: RunnerSessionRecord,
    diagnostic_digest: str,
    cleanup_disposition: str,
    redaction_policy_id: str,
    primary: RunnerSessionCancellationRecord | None = None,
) -> RunnerSessionCompletionRecord:
    return completion_record(
        session=session,
        terminal_state=("lost" if cleanup_disposition == "orphan_risk" else "failed"),
        exit_kind="error",
        adapter_outcome_kind="error",
        adapter_error_kind="context_mutation_refused",
        evidence_digest=None,
        diagnostic_digest=diagnostic_digest,
        cleanup_disposition=cleanup_disposition,
        redaction_policy_id=redaction_policy_id,
        primary=primary,
    )


__all__ = ("completion_record", "context_mutation_completion_record")
