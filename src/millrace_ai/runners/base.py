"""Runner adapter interface contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from millrace_ai.contracts import (
    CapabilityEnforcementMode,
    CapabilitySupportDecision,
    CapabilitySupportState,
    ExecutionCapabilityGrant,
)
from millrace_ai.runners.requests import RunnerRawResult, StageRunRequest


class StageRunnerAdapter(Protocol):
    """Interface implemented by concrete stage runner adapters."""

    @property
    def name(self) -> str:
        """Stable adapter identifier used in mode/config resolution."""

    def run(self, request: StageRunRequest) -> RunnerRawResult:
        """Execute one stage request and return raw runner output."""

    def evaluate_capability_grant(
        self,
        grant: ExecutionCapabilityGrant,
        invocation_context: Mapping[str, object],
    ) -> CapabilitySupportDecision:
        """Evaluate whether this adapter can support one grant in context."""


def default_capability_support_decision(
    grant: ExecutionCapabilityGrant,
    invocation_context: Mapping[str, object] | None = None,
) -> CapabilitySupportDecision:
    """Return a conservative support decision for callers without adapter context."""

    del invocation_context
    if grant.decision_state.value != "granted":
        return CapabilitySupportDecision(
            runner_id="runtime",
            invocation_context_ref="default",
            grant_id=grant.grant_id,
            support_state=CapabilitySupportState.UNSUPPORTED,
            enforcement_mode=CapabilityEnforcementMode.NOT_APPLICABLE,
            reason=f"grant decision is {grant.decision_state.value}",
        )
    return CapabilitySupportDecision(
        runner_id="runtime",
        invocation_context_ref="default",
        grant_id=grant.grant_id,
        support_state=CapabilitySupportState.SUPPORTED,
        enforcement_mode=grant.enforcement_mode,
        evidence_available=("runtime_event",),
        reason="runtime default support decision",
    )


__all__ = ["StageRunnerAdapter", "default_capability_support_decision"]
