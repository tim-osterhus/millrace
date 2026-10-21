"""Materialize bounded authenticated source failure evidence for recovery."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace

from millrace.adapters.cli.context import OpenRuntimeContext
from millrace.contracts.runner import (
    RunnerDispatchEnvelope,
    runner_session_completion_diagnostic_from_payload,
)
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_cleanup_digests,
    runtime_failure_diagnostic_matches,
)
from millrace.contracts.state import RuntimeState
from millrace.operator.dispatch import (
    DispatchProjectionError,
    build_dispatch_envelope_for_run,
)
from millrace.substrate.errors import SubstrateError


def materialize_recovery_failure_context(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    dispatch: RunnerDispatchEnvelope,
) -> RunnerDispatchEnvelope:
    """Frozen completion diagnostics are evidence, never terminal-result authority."""
    expected_dispatch = build_dispatch_envelope_for_run(
        state=state, run_id=dispatch.run_id
    )
    if dispatch != expected_dispatch:
        raise DispatchProjectionError(
            reason="runtime_failure_source_invalid",
            message="Recovery failure projection differs from current ownership.",
        )
    source_context = dispatch.governance_context.get("runtime_failure_source")
    if source_context is None:
        return dispatch
    if not isinstance(source_context, Mapping):
        raise DispatchProjectionError(
            reason="runtime_failure_source_invalid",
            message="Invalid recovery failure context.",
        )
    run = state.runs.get(str(source_context.get("source_run_id")))
    completion = state.runner_session_completions.get(
        "" if run is None else run.current_session_id or ""
    )
    if (
        run is None
        or completion is None
        or not runtime_failure_applied(state, run.run_ref.run_id)
        or run.run_ref.plan_ref.authority_fingerprint != dispatch.plan_fingerprint
        or completion.session_id != source_context.get("source_session_id")
        or completion.diagnostic_digest != source_context.get("diagnostic_digest")
        or completion.runner_result_evidence_digest
        != source_context.get("runner_result_evidence_digest")
    ):
        raise DispatchProjectionError(
            reason="runtime_failure_source_invalid",
            message="Invalid recovery failure context.",
        )
    try:
        payload = runtime.cas_store.get_bytes(completion.diagnostic_digest)
        evidence_payload = (
            runtime.cas_store.get_bytes(completion.runner_result_evidence_digest)
            if completion.runner_result_evidence_digest is not None
            else None
        )
        if not runtime_failure_diagnostic_matches(
            state,
            completion,
            payload,
            evidence_payload=evidence_payload,
            cleanup_payloads={
                digest: runtime.cas_store.get_bytes(digest)
                for digest in runtime_failure_cleanup_digests(state, completion)
            },
        ):
            raise ValueError("failure diagnostic authority mismatch")
        diagnostic = runner_session_completion_diagnostic_from_payload(
            json.loads(payload)
        )
    except (OSError, SubstrateError, TypeError, ValueError, RecursionError) as exc:
        raise DispatchProjectionError(
            reason="runtime_failure_source_invalid",
            message="Invalid recovery failure diagnostic.",
        ) from exc
    return replace(
        dispatch,
        governance_context={
            **dispatch.governance_context,
            "runtime_failure_source": {
                **source_context,
                "diagnostic": diagnostic.diagnostic,
            },
        },
    )
