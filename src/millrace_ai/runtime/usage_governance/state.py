"""Usage-governance state file persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from millrace_ai.paths import WorkspacePaths

from .io import atomic_write_text
from .models import SubscriptionQuotaStatus, UsageGovernanceState


def load_usage_governance_state(paths: WorkspacePaths) -> UsageGovernanceState:
    if not paths.usage_governance_state_file.is_file():
        return disabled_state(now=datetime.fromtimestamp(0, timezone.utc))
    payload = json.loads(paths.usage_governance_state_file.read_text(encoding="utf-8"))
    return UsageGovernanceState.model_validate(payload)


def save_usage_governance_state(paths: WorkspacePaths, state: UsageGovernanceState) -> None:
    atomic_write_text(paths.usage_governance_state_file, state.model_dump_json(indent=2) + "\n")


def disabled_state(*, now: datetime) -> UsageGovernanceState:
    return UsageGovernanceState(
        enabled=False,
        auto_resume=True,
        auto_resume_possible=True,
        last_evaluated_at=now,
        subscription_quota_status=SubscriptionQuotaStatus(),
    )


__all__ = [
    "disabled_state",
    "load_usage_governance_state",
    "save_usage_governance_state",
]
