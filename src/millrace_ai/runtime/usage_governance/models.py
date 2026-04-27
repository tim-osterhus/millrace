"""Usage-governance state and ledger models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from millrace_ai.contracts import Plane, TokenUsage, WorkItemKind


class UsageGovernanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsageGovernanceBlocker(UsageGovernanceModel):
    source: Literal["runtime_token", "subscription_quota"]
    rule_id: str
    window: str
    observed: float
    threshold: float
    metric: str | None = None
    auto_resume_possible: bool = True
    next_auto_resume_at: datetime | None = None
    detail: str = ""


class SubscriptionQuotaWindowReading(UsageGovernanceModel):
    window: str
    percent_used: float
    resets_at: datetime | None = None
    read_at: datetime


class SubscriptionQuotaStatus(UsageGovernanceModel):
    enabled: bool = False
    provider: str = "codex_chatgpt_oauth"
    state: Literal["disabled", "healthy", "degraded"] = "disabled"
    degraded_policy: str | None = None
    detail: str | None = None
    last_refreshed_at: datetime | None = None
    windows: dict[str, SubscriptionQuotaWindowReading] = Field(default_factory=dict)


class UsageGovernanceState(UsageGovernanceModel):
    version: Literal["1.0"] = "1.0"
    enabled: bool = False
    auto_resume: bool = True
    auto_resume_possible: bool = True
    evaluation_boundary: str = "between_stages"
    calendar_timezone: str = "UTC"
    daemon_session_id: str | None = None
    last_evaluated_at: datetime
    active_blockers: tuple[UsageGovernanceBlocker, ...] = ()
    paused_by_governance: bool = False
    next_auto_resume_at: datetime | None = None
    subscription_quota_status: SubscriptionQuotaStatus = Field(
        default_factory=SubscriptionQuotaStatus
    )


class UsageGovernanceLedgerEntry(UsageGovernanceModel):
    dedupe_key: str
    counted_at: datetime
    stage_completed_at: datetime
    plane: Plane
    run_id: str
    stage_id: str
    work_item_kind: WorkItemKind
    work_item_id: str
    token_usage: TokenUsage
    stage_result_path: str
    daemon_session_id: str | None = None


class SubscriptionQuotaProvider(Protocol):
    def read(self, *, now: datetime) -> SubscriptionQuotaStatus: ...


__all__ = [
    "SubscriptionQuotaProvider",
    "SubscriptionQuotaStatus",
    "SubscriptionQuotaWindowReading",
    "UsageGovernanceBlocker",
    "UsageGovernanceLedgerEntry",
    "UsageGovernanceModel",
    "UsageGovernanceState",
]
