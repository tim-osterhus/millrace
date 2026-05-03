"""Read-only usage-governance reader."""

from __future__ import annotations

from millrace_ai.paths import workspace_paths
from millrace_ai.runtime.usage_governance import load_usage_governance_state

from millrace_web.models import UsageGovernanceSummary, WorkspaceRef


def read_usage_governance_summary(workspace: WorkspaceRef) -> UsageGovernanceSummary:
    paths = workspace_paths(workspace.path)
    state = load_usage_governance_state(paths)
    budget_status = state.subscription_quota_status.state
    if state.active_blockers:
        budget_status = "blocked"
    elif not state.enabled:
        budget_status = "disabled"
    return UsageGovernanceSummary(
        enabled=state.enabled,
        paused=state.paused_by_governance,
        blocker_count=len(state.active_blockers),
        auto_resume_possible=state.auto_resume_possible,
        budget_status=budget_status,
    )

