"""CLI-owned selected lifecycle reconciliation tick."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping

from millrace.adapters.cli.context import (
    OpenRuntimeContext,
    contextual_input_id,
    transition_context,
)
from millrace.adapters.cli.run import BoundedExecutionUnitResult
from millrace.contracts.state import CooldownWaitRecord
from millrace.contracts.transition import TimerDue
from millrace.kernel import apply, decide
from millrace.kernel.lifecycle import project_next_lifecycle_transition


def run_lifecycle_transition_once(
    runtime: OpenRuntimeContext,
    *,
    observed_at: int | None = None,
) -> BoundedExecutionUnitResult:
    if not isinstance(runtime, OpenRuntimeContext):
        raise TypeError("runtime must be OpenRuntimeContext")
    state = runtime.store.load_runtime_state(runtime.cas_store)
    observed = int(time.time()) if observed_at is None else observed_at
    due_wait = _next_due_cooldown_wait(state.cooldown_waits.values(), observed)
    if due_wait is not None:
        transition_input = TimerDue(
            f"lifecycle-timer-due:{due_wait.wait_id}:{due_wait.due_at}",
            wait_id=due_wait.wait_id,
            observed_at=observed,
        )
        decision = decide(
            state,
            transition_input,
            transition_context(
                command="run.daemon.lifecycle",
                input_id_value=contextual_input_id(transition_input),
            ),
        )
        next_state = apply(state, decision)
        runtime.store.persist_runtime_state(next_state, runtime.cas_store)
        diagnostics = (
            {
                "kind": "timer",
                "plan_fingerprint": due_wait.plan_ref.authority_fingerprint,
                "declaration_id": str(due_wait.policy_id),
                "source_artifact_id": due_wait.wait_id,
            },
        )
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
                diagnostics=diagnostics,
            )
        return BoundedExecutionUnitResult(
            code="lifecycle_transition_applied",
            accepted=True,
            transition_disposition=decision.disposition,
            diagnostics=diagnostics,
        )
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


def _next_due_cooldown_wait(
    waits: Iterable[CooldownWaitRecord],
    observed_at: int,
) -> CooldownWaitRecord | None:
    if type(observed_at) is not int:
        raise TypeError("observed_at must be an integer")
    if observed_at < 0:
        raise ValueError("observed_at must be non-negative")
    candidates = [
        wait
        for wait in waits
        if wait.consumed_input_id is None
        and wait.due_at <= observed_at
    ]
    return min(candidates, key=lambda wait: (wait.due_at, wait.wait_id), default=None)


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
