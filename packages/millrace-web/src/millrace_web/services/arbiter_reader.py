"""Read-only Arbiter/closure-target reader."""

from __future__ import annotations

from millrace_ai.paths import workspace_paths
from millrace_ai.workspace.arbiter_state import list_open_closure_target_states

from millrace_web.models import ArbiterSummary, WorkspaceRef


def read_arbiter_summary(workspace: WorkspaceRef, *, latest_result: str | None = None) -> ArbiterSummary:
    paths = workspace_paths(workspace.path)
    try:
        open_targets = list_open_closure_target_states(paths)
    except Exception:
        return ArbiterSummary(status="unknown", latest_result=latest_result)
    if not open_targets:
        return ArbiterSummary(status="idle", latest_result=latest_result)
    actionable = tuple(target for target in open_targets if not target.closure_blocked_by_lineage_work)
    if len(actionable) > 1:
        return ArbiterSummary(closure_target_open=True, latest_result=latest_result, status="invalid")
    target = actionable[0] if actionable else open_targets[0]
    status = "blocked" if target.closure_blocked_by_lineage_work else "watching"
    return ArbiterSummary(
        closure_target_open=True,
        latest_result=latest_result,
        next_stage=None,
        status=status,
    )
