"""Usage-governance runtime monitor event emission."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.events import write_runtime_event

from .models import UsageGovernanceState

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def emit_governance_monitor_events(
    engine: RuntimeEngine,
    *,
    state: UsageGovernanceState,
    previous_state: UsageGovernanceState,
    had_governance_pause: bool,
    has_governance_pause: bool,
) -> None:
    if state.subscription_quota_status.state == "degraded":
        engine._emit_monitor_event(
            "usage_governance_degraded",
            source=state.subscription_quota_status.provider,
            policy=state.subscription_quota_status.degraded_policy,
            detail=state.subscription_quota_status.detail,
        )

    if not had_governance_pause and has_governance_pause and state.active_blockers:
        for blocker in state.active_blockers:
            write_runtime_event(
                engine.paths,
                event_type="usage_governance_blocked",
                data=blocker.model_dump(mode="json"),
            )
            engine._emit_monitor_event(
                "usage_governance_paused",
                source=blocker.source,
                rule_id=blocker.rule_id,
                window=blocker.window,
                observed=blocker.observed,
                threshold=blocker.threshold,
                next_auto_resume_at=(
                    blocker.next_auto_resume_at.isoformat()
                    if blocker.next_auto_resume_at is not None
                    else None
                ),
            )
        return

    if had_governance_pause and not has_governance_pause:
        engine._emit_monitor_event(
            "usage_governance_resumed",
            cleared_rules=",".join(blocker.rule_id for blocker in previous_state.active_blockers),
        )


__all__ = ["emit_governance_monitor_events"]
