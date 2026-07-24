"""CLI-owned selected lifecycle reconciliation tick."""

from __future__ import annotations

from collections.abc import Mapping

from millrace.adapters.cli.context import OpenRuntimeContext
from millrace.adapters.cli.run import BoundedExecutionUnitResult
from millrace.kernel import apply, decide
from millrace.kernel.lifecycle import project_next_lifecycle_transition


def run_lifecycle_transition_once(
    runtime: OpenRuntimeContext,
) -> BoundedExecutionUnitResult:
    if not isinstance(runtime, OpenRuntimeContext):
        raise TypeError("runtime must be OpenRuntimeContext")
    state = runtime.store.load_runtime_state(runtime.cas_store)
    projection = project_next_lifecycle_transition(state)
    if projection.diagnostics:
        return BoundedExecutionUnitResult(
            code="lifecycle_state_corrupt",
            diagnostics=tuple(
                _diagnostic_payload(item) for item in projection.diagnostics
            ),
        )
    candidate = projection.candidate
    if candidate is None:
        return BoundedExecutionUnitResult(code="no_ready_work")

    decision = decide(state, candidate.transition_input, candidate.transition_context)
    next_state = apply(state, decision)
    runtime.store.persist_runtime_state(next_state, runtime.cas_store)
    if not decision.accepted:
        reason = (
            "transition_refused"
            if decision.refusal is None
            else decision.refusal.reason
        )
        return BoundedExecutionUnitResult(
            code="lifecycle_transition_refused",
            observation_refusal_reason=reason,
            transition_disposition=decision.disposition,
            diagnostics=(
                {
                    "kind": candidate.kind,
                    "plan_fingerprint": candidate.plan_fingerprint,
                    "declaration_id": candidate.declaration_id,
                    "source_artifact_id": candidate.source_artifact_id,
                },
            ),
        )
    return BoundedExecutionUnitResult(
        code="lifecycle_transition_applied",
        accepted=True,
        transition_disposition=decision.disposition,
        diagnostics=(
            {
                "kind": candidate.kind,
                "plan_fingerprint": candidate.plan_fingerprint,
                "declaration_id": candidate.declaration_id,
                "source_artifact_id": candidate.source_artifact_id,
            },
        ),
    )


def _diagnostic_payload(diagnostic: object) -> Mapping[str, object]:
    payload: dict[str, object] = {
        "reason_code": getattr(diagnostic, "reason_code", "lifecycle_state_corrupt")
    }
    kind = getattr(diagnostic, "kind", None)
    if kind is not None:
        payload["kind"] = kind
    plan_fingerprint = getattr(diagnostic, "plan_fingerprint", None)
    if plan_fingerprint is not None:
        payload["plan_fingerprint"] = plan_fingerprint
    declaration_id = getattr(diagnostic, "declaration_id", None)
    if declaration_id is not None:
        payload["declaration_id"] = declaration_id
    source_artifact_id = getattr(diagnostic, "source_artifact_id", None)
    if source_artifact_id is not None:
        payload["source_artifact_id"] = source_artifact_id
    detail = getattr(diagnostic, "detail", None)
    if detail is not None:
        payload["detail"] = detail
    return payload


__all__ = ("run_lifecycle_transition_once",)
