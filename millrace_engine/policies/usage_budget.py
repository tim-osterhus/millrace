"""Execution weekly-usage budget policy helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path

from pydantic import Field, field_validator

from ..config import EngineConfig
from ..contracts import ContractModel
from ..paths import RuntimePaths
from ..telemetry import WeeklyUsageSample, sample_weekly_usage
from .hooks import (
    PolicyDecision,
    PolicyEvaluationRecord,
    PolicyEvidence,
    PolicyEvidenceKind,
    PolicyFactSnapshot,
    PolicyHook,
    PolicyHookError,
)


class UsageBudgetSemantics(str, Enum):
    """Supported weekly-usage threshold contracts."""

    REMAINING = "remaining"
    CONSUMED = "consumed"


class ExecutionUsageBudgetContract(ContractModel):
    """One resolved weekly-usage threshold contract."""

    semantics: UsageBudgetSemantics
    threshold: Decimal = Field(ge=0)
    source_field: str
    refresh_utc: str
    compatibility_fallback: bool = False

    @field_validator("source_field", "refresh_utc")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("text fields may not be empty")
        return normalized


class ExecutionUsageBudgetSnapshot(ContractModel):
    """Config-derived execution usage-budget snapshot."""

    enabled: bool
    provider: str
    cache_max_age_secs: int = Field(ge=0)
    command: str | None = None
    codex_auth_source_dir: Path | None = None
    codex_runtime_home: Path | None = None
    contract: ExecutionUsageBudgetContract | None = None

    @field_validator("provider", "command", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            if getattr(info, "field_name", "") == "provider":
                raise ValueError("provider may not be empty")
            return None
        return normalized

    @field_validator("codex_auth_source_dir", "codex_runtime_home", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value)

    @classmethod
    def from_config(cls, config: EngineConfig) -> "ExecutionUsageBudgetSnapshot":
        usage = config.policies.usage
        thresholds = usage.execution
        contract: ExecutionUsageBudgetContract | None = None
        if thresholds.remaining_threshold is not None:
            contract = ExecutionUsageBudgetContract(
                semantics=UsageBudgetSemantics.REMAINING,
                threshold=thresholds.remaining_threshold,
                source_field="policies.usage.execution.remaining_threshold",
                refresh_utc=thresholds.refresh_utc,
            )
        elif thresholds.consumed_threshold is not None:
            contract = ExecutionUsageBudgetContract(
                semantics=UsageBudgetSemantics.CONSUMED,
                threshold=thresholds.consumed_threshold,
                source_field="policies.usage.execution.consumed_threshold",
                refresh_utc=thresholds.refresh_utc,
            )
        elif thresholds.legacy_threshold is not None:
            contract = ExecutionUsageBudgetContract(
                semantics=UsageBudgetSemantics.REMAINING,
                threshold=thresholds.legacy_threshold,
                source_field="policies.usage.execution.legacy_threshold",
                refresh_utc=thresholds.refresh_utc,
                compatibility_fallback=True,
            )
        return cls(
            enabled=usage.enabled,
            provider=usage.provider,
            cache_max_age_secs=usage.cache_max_age_secs,
            command=usage.orch_command,
            codex_auth_source_dir=usage.codex_auth_source_dir,
            codex_runtime_home=usage.codex_runtime_home,
            contract=contract,
        )


class ExecutionUsageBudgetContext(ContractModel):
    """Typed execution usage-budget evaluation result."""

    decision: PolicyDecision
    pause_requested: bool
    reason: str
    sample: WeeklyUsageSample
    contract: ExecutionUsageBudgetContract | None = None
    next_refresh_at: datetime | None = None

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("reason may not be empty")
        return normalized

    @field_validator("next_refresh_at", mode="before")
    @classmethod
    def normalize_refresh(cls, value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            moment = value
        else:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)


class ExecutionUsageBudgetEvaluator:
    """Cycle-boundary execution usage budget evaluator."""

    evaluator_name = "execution_usage_budget"

    def __init__(
        self,
        policy: ExecutionUsageBudgetSnapshot,
        *,
        paths: RuntimePaths,
    ) -> None:
        self._policy = policy
        self._paths = paths

    def __call__(self, facts: PolicyFactSnapshot) -> PolicyEvaluationRecord:
        if facts.hook is not PolicyHook.CYCLE_BOUNDARY:
            raise PolicyHookError("execution usage budget evaluator only supports cycle_boundary hooks")
        context = self._context()
        return PolicyEvaluationRecord(
            evaluator=self.evaluator_name,
            hook=facts.hook,
            decision=context.decision,
            facts=facts,
            evidence=self._evidence(context),
            notes=(context.reason,),
        )

    def _context(self) -> ExecutionUsageBudgetContext:
        if not self._policy.enabled:
            sample = WeeklyUsageSample(
                ok=True,
                loop="orchestrate",
                provider=self._policy.provider,  # type: ignore[arg-type]
                source="policy:disabled",
                current=None,
            )
            return ExecutionUsageBudgetContext(
                decision=PolicyDecision.PASS,
                pause_requested=False,
                reason="Execution weekly usage auto-pause is disabled.",
                sample=sample,
                contract=self._policy.contract,
            )
        if self._policy.contract is None:
            sample = WeeklyUsageSample(
                ok=True,
                loop="orchestrate",
                provider=self._policy.provider,  # type: ignore[arg-type]
                source="policy:no-threshold",
                current=None,
            )
            return ExecutionUsageBudgetContext(
                decision=PolicyDecision.PASS,
                pause_requested=False,
                reason="Execution weekly usage auto-pause is enabled but no execution threshold is configured.",
                sample=sample,
                contract=None,
            )

        sample = sample_weekly_usage(
            runtime_dir=self._paths.runtime_dir,
            loop="orchestrate",
            provider=self._policy.provider,  # type: ignore[arg-type]
            cache_max_age_secs=self._policy.cache_max_age_secs,
            command=self._policy.command,
            auth_source_dir=self._policy.codex_auth_source_dir,
            runtime_home=self._policy.codex_runtime_home,
        )
        if not sample.ok or sample.current is None:
            return ExecutionUsageBudgetContext(
                decision=PolicyDecision.PASS,
                pause_requested=False,
                reason="Execution weekly usage current is unavailable; continuing without auto-pause.",
                sample=sample,
                contract=self._policy.contract,
                next_refresh_at=_next_refresh_at(self._policy.contract.refresh_utc),
            )

        contract = self._policy.contract
        threshold_hit = (
            sample.current <= contract.threshold
            if contract.semantics is UsageBudgetSemantics.REMAINING
            else sample.current >= contract.threshold
        )
        if threshold_hit:
            return ExecutionUsageBudgetContext(
                decision=PolicyDecision.POLICY_BLOCKED,
                pause_requested=True,
                reason=_pause_reason(contract, current=sample.current),
                sample=sample,
                contract=contract,
                next_refresh_at=_next_refresh_at(contract.refresh_utc),
            )
        return ExecutionUsageBudgetContext(
            decision=PolicyDecision.PASS,
            pause_requested=False,
            reason=_continue_reason(contract, current=sample.current),
            sample=sample,
            contract=contract,
            next_refresh_at=_next_refresh_at(contract.refresh_utc),
        )

    def _evidence(self, context: ExecutionUsageBudgetContext) -> tuple[PolicyEvidence, ...]:
        contract = context.contract
        return (
            PolicyEvidence(
                kind=PolicyEvidenceKind.USAGE_BUDGET,
                summary="Execution weekly usage budget evaluated the configured threshold contract.",
                details={
                    "enabled": self._policy.enabled,
                    "provider": self._policy.provider,
                    "cache_max_age_secs": self._policy.cache_max_age_secs,
                    "command": self._policy.command,
                    "pause_requested": context.pause_requested,
                    "threshold_semantics": contract.semantics.value if contract is not None else None,
                    "threshold": str(contract.threshold) if contract is not None else None,
                    "threshold_source": contract.source_field if contract is not None else None,
                    "refresh_utc": contract.refresh_utc if contract is not None else None,
                    "compatibility_fallback": (
                        contract.compatibility_fallback if contract is not None else False
                    ),
                    "next_refresh_at": (
                        context.next_refresh_at.isoformat().replace("+00:00", "Z")
                        if context.next_refresh_at is not None
                        else None
                    ),
                },
            ),
            PolicyEvidence(
                kind=PolicyEvidenceKind.USAGE_SAMPLE,
                summary=(
                    f"Execution weekly usage sampled current={context.sample.current} source={context.sample.source}"
                    if context.sample.current is not None
                    else f"Execution weekly usage sample unavailable source={context.sample.source}"
                ),
                details={
                    "ok": context.sample.ok,
                    "provider": context.sample.provider,
                    "source": context.sample.source,
                    "current": str(context.sample.current) if context.sample.current is not None else None,
                    "sampled_at": (
                        context.sample.sampled_at.isoformat().replace("+00:00", "Z")
                        if context.sample.sampled_at is not None
                        else None
                    ),
                    "warnings": list(context.sample.warnings),
                    "reason": context.sample.reason,
                },
            ),
        )


def execution_usage_budget_context(record: PolicyEvaluationRecord | None) -> ExecutionUsageBudgetContext | None:
    """Parse the persisted execution usage-budget context from one record."""

    if record is None or record.evaluator != ExecutionUsageBudgetEvaluator.evaluator_name:
        return None
    budget_details = next(
        (item.details for item in record.evidence if item.kind is PolicyEvidenceKind.USAGE_BUDGET),
        None,
    )
    sample_details = next(
        (item.details for item in record.evidence if item.kind is PolicyEvidenceKind.USAGE_SAMPLE),
        None,
    )
    if budget_details is None or sample_details is None:
        return None
    contract = None
    if budget_details.get("threshold_semantics") and budget_details.get("threshold") is not None:
        contract = ExecutionUsageBudgetContract(
            semantics=UsageBudgetSemantics(str(budget_details["threshold_semantics"])),
            threshold=str(budget_details["threshold"]),
            source_field=str(budget_details.get("threshold_source") or "unknown"),
            refresh_utc=str(budget_details.get("refresh_utc") or "MON 00:00"),
            compatibility_fallback=bool(budget_details.get("compatibility_fallback")),
        )
    return ExecutionUsageBudgetContext(
        decision=record.decision,
        pause_requested=bool(budget_details.get("pause_requested")),
        reason=record.notes[0] if record.notes else "Execution weekly usage budget evaluated.",
        sample=WeeklyUsageSample(
            ok=bool(sample_details.get("ok")),
            loop="orchestrate",
            provider=str(sample_details.get("provider") or "codex"),  # type: ignore[arg-type]
            source=str(sample_details.get("source") or "unknown"),
            current=sample_details.get("current"),
            sampled_at=sample_details.get("sampled_at"),
            warnings=tuple(sample_details.get("warnings") or ()),
            reason=sample_details.get("reason"),
        ),
        contract=contract,
        next_refresh_at=budget_details.get("next_refresh_at"),
    )


def execution_usage_budget_context_from_records(
    records: tuple[PolicyEvaluationRecord, ...] | list[PolicyEvaluationRecord],
) -> ExecutionUsageBudgetContext | None:
    """Return the last execution usage-budget context from one record batch."""

    for record in reversed(tuple(records)):
        context = execution_usage_budget_context(record)
        if context is not None:
            return context
    return None


def _pause_reason(contract: ExecutionUsageBudgetContract, *, current: Decimal) -> str:
    comparator = "<=" if contract.semantics is UsageBudgetSemantics.REMAINING else ">="
    usage_label = "remaining" if contract.semantics is UsageBudgetSemantics.REMAINING else "consumed"
    prefix = "Execution weekly usage auto-pause triggered"
    if contract.compatibility_fallback:
        prefix += " via legacy remaining-threshold compatibility"
    return (
        f"{prefix}: {usage_label}={current} {comparator} threshold={contract.threshold} "
        f"source={contract.source_field}."
    )


def _continue_reason(contract: ExecutionUsageBudgetContract, *, current: Decimal) -> str:
    comparator = ">" if contract.semantics is UsageBudgetSemantics.REMAINING else "<"
    usage_label = "remaining" if contract.semantics is UsageBudgetSemantics.REMAINING else "consumed"
    return (
        f"Execution weekly usage continues: {usage_label}={current} {comparator} "
        f"threshold={contract.threshold} source={contract.source_field}."
    )


def _next_refresh_at(refresh_utc: str) -> datetime | None:
    parts = refresh_utc.strip().upper().split()
    if len(parts) != 2:
        return None
    day_token, time_token = parts
    weekday_map = {
        "MON": 0,
        "TUE": 1,
        "WED": 2,
        "THU": 3,
        "FRI": 4,
        "SAT": 5,
        "SUN": 6,
    }
    if day_token not in weekday_map:
        return None
    try:
        hour_text, minute_text = time_token.split(":", maxsplit=1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    now = datetime.now(timezone.utc)
    days_ahead = (weekday_map[day_token] - now.weekday()) % 7
    candidate = now + timedelta(days=days_ahead)
    candidate = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


__all__ = [
    "ExecutionUsageBudgetContext",
    "ExecutionUsageBudgetContract",
    "ExecutionUsageBudgetEvaluator",
    "ExecutionUsageBudgetSnapshot",
    "UsageBudgetSemantics",
    "execution_usage_budget_context",
    "execution_usage_budget_context_from_records",
]
