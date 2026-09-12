"""Bounded diagnostic evidence without private diagnostic payloads."""

from __future__ import annotations

from typing import Any

from millrace.adapters.cli.output import json_ready
from millrace.contracts.public_projections import digest


def diagnostic_record(
    code: str,
    source: str,
    status: str,
    evidence: object,
    *,
    severity: str = "error",
    evidence_digest: str | None = None,
    **scope: Any,
) -> dict[str, Any]:
    identity = {"code": code, "source": source, **scope}
    return {
        "kind": "diagnostic",
        "id": digest(identity),
        **identity,
        "severity": severity,
        "status": status,
        "evidence_digest": evidence_digest or digest(json_ready(evidence)),
        "redaction_policy_id": "core-public-read-omit-v1",
        "truncation": {"detail_omitted": True},
    }


def session_diagnostics(runtime: Any, state: Any, session: Any) -> dict[str, Any]:
    from millrace.adapters.cli.status import _load_rejected_evidence

    scope = {
        "run_id": session.run_id,
        "session_id": session.session_id,
        "dispatch_generation": session.dispatch_generation,
    }
    facts = {
        **scope,
        "state": session.state,
        "cleanup_disposition": session.cleanup_disposition,
    }
    records = []
    conditions = {
        "runner_session_lost": session.state == "lost",
        "runner_session_orphan_risk": session.cleanup_disposition == "orphan_risk",
        "runner_session_cleanup_pending": session.cleanup_disposition == "pending"
        and session.state in {"cancellation_requested", "terminating"},
    }
    completion = state.runner_session_completions.get(session.session_id)
    status, diagnostic_digest = "not_present", None
    if completion is not None:
        diagnostic_digest = completion.diagnostic_digest
        if diagnostic_digest is not None:
            status = _completion_status(runtime, state, session, diagnostic_digest)
        conditions["runner_session_reconciliation_unsupported"] = (
            completion.adapter_outcome_kind == "unsupported"
        )
        receipt = state.receipts.get(completion.application_input_id)
        conditions["runner_session_application_refused"] = (
            receipt is not None and not receipt.accepted
        )
        records.append(
            diagnostic_record(
                "runner_session_completion_diagnostic",
                "runner_session_completion",
                status,
                {"completion_id": completion.completion_id},
                evidence_digest=diagnostic_digest,
                severity="info" if status == "available" else "error",
                **scope,
            )
        )
        if completion.runner_result_evidence_digest is not None:
            _, result_status = _load_rejected_evidence(
                runtime,
                state,
                session.session_id,
                completion.runner_result_evidence_digest,
            )
            records.append(
                diagnostic_record(
                    "runner_session_result_evidence",
                    "runner_session_completion",
                    result_status,
                    {"completion_id": completion.completion_id},
                    evidence_digest=completion.runner_result_evidence_digest,
                    severity="info" if result_status == "available" else "error",
                    **scope,
                )
            )
    for code, present in conditions.items():
        if present:
            records.append(
                diagnostic_record(code, "runner_session", "available", facts, **scope)
            )
    return {
        "completion_diagnostic_digest": diagnostic_digest,
        "diagnostic_status": status,
        "diagnostics": records,
    }


def _completion_status(
    runtime: Any, state: Any, session: Any, evidence_digest: str
) -> str:
    import json

    from millrace.adapters.cli.status import _load_bounded_cas_bytes
    from millrace.contracts.runner import (
        runner_session_completion_diagnostic_bytes,
        runner_session_completion_diagnostic_from_payload,
    )

    raw, status = _load_bounded_cas_bytes(runtime, evidence_digest, max_bytes=16 * 1024)
    if raw is None:
        return status
    try:
        diagnostic = runner_session_completion_diagnostic_from_payload(json.loads(raw))
        run = state.runs[session.run_id]
        expected = {
            "run_id": session.run_id,
            "session_id": session.session_id,
            "dispatch_generation": session.dispatch_generation,
            "session_fencing_token": session.session_fencing_token,
            "plan_fingerprint": str(run.run_ref.plan_ref.authority_fingerprint),
            "claim_id": run.run_ref.claim_id,
            "generation": run.run_ref.generation,
            "fencing_token": run.run_ref.fencing_token,
            "stage_kind_id": str(run.stage_kind_id),
            "graph_node_id": state.activations[run.activation_id].graph_node_id,
            "runner_binding_id": str(run.runner_binding_id),
        }
        if runner_session_completion_diagnostic_bytes(diagnostic) != raw or any(
            getattr(diagnostic, key) != value for key, value in expected.items()
        ):
            return "corrupt"
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
        return "corrupt"
    return "available"


def doctor_diagnostics(
    runtime: Any, state: Any, fingerprint: str | None
) -> list[dict[str, Any]]:
    from millrace.operator.dispatch import list_ready_dispatch_candidates

    records = []
    for item in list_ready_dispatch_candidates(state).diagnostics:
        if fingerprint is None or item.plan_fingerprint == fingerprint:
            records.append(
                diagnostic_record(
                    item.reason_code,
                    "ready_dispatch",
                    "available",
                    json_ready(item),
                    severity=item.severity,
                    activation_id=item.activation_id,
                    work_item_id=item.work_item_id,
                    plan_fingerprint=item.plan_fingerprint,
                )
            )
    for session in state.runner_sessions.values():
        run = state.runs[session.run_id]
        if (
            fingerprint is None
            or str(run.run_ref.plan_ref.authority_fingerprint) == fingerprint
        ):
            records.extend(session_diagnostics(runtime, state, session)["diagnostics"])
    return records


def failure_status(exc: BaseException) -> str:
    from millrace.substrate.errors import (
        CasDigestMismatch,
        CasObjectNotFound,
        StorageIntegrityError,
    )

    status = "unavailable"
    current: BaseException | None = exc
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, CasObjectNotFound):
            return "missing"
        if isinstance(current, (CasDigestMismatch, StorageIntegrityError)):
            status = "corrupt"
        current = current.__cause__ or current.__context__
    return status
