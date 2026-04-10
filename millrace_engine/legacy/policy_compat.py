"""Legacy workflow policy compatibility reporting."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from pydantic import BaseModel, ConfigDict, field_validator


class LegacyPolicyCompatStatus(str, Enum):
    """Compatibility status for one legacy policy knob."""

    MAPPED = "mapped"
    PARTIALLY_MAPPED = "partially_mapped"
    DEPRECATED = "deprecated"
    UNSUPPORTED = "unsupported"


class LegacyPolicyCompatCategory(str, Enum):
    """Audited legacy policy categories."""

    INTEGRATION = "integration"
    SIZING = "sizing"
    SEARCH = "search"
    USAGE = "usage"
    PACING = "pacing"
    NETWORK_GUARD = "network_guard"
    PREFLIGHT = "preflight"
    OUTAGE = "outage"


class LegacyCompatModel(BaseModel):
    """Closed-world base model for compatibility reporting."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LegacyPolicyCompatEntry(LegacyCompatModel):
    """Deterministic compatibility record for one legacy key."""

    key: str
    category: LegacyPolicyCompatCategory
    status: LegacyPolicyCompatStatus
    present_in_source: bool = False
    mapped_fields: tuple[str, ...] = ()
    replacement_native_fields: tuple[str, ...] = ()
    note: str | None = None

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("key may not be empty")
        return normalized

    @field_validator("mapped_fields", "replacement_native_fields", mode="before")
    @classmethod
    def normalize_fields(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            token = str(item).strip()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        return tuple(normalized)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(part.strip() for part in value.splitlines() if part.strip())
        return normalized or None


class LegacyPolicyCompatReport(LegacyCompatModel):
    """Audited compatibility surface for legacy workflow policy keys."""

    entries: tuple[LegacyPolicyCompatEntry, ...] = ()

    @field_validator("entries", mode="before")
    @classmethod
    def normalize_entries(
        cls,
        value: tuple[LegacyPolicyCompatEntry, ...] | list[LegacyPolicyCompatEntry | dict[str, object]] | None,
    ) -> tuple[LegacyPolicyCompatEntry, ...]:
        if not value:
            return ()
        entries = [
            item if isinstance(item, LegacyPolicyCompatEntry) else LegacyPolicyCompatEntry.model_validate(item)
            for item in value
        ]
        return tuple(sorted(entries, key=lambda item: item.key))

    def entries_for_status(
        self,
        status: LegacyPolicyCompatStatus,
        *,
        present_only: bool = False,
    ) -> tuple[LegacyPolicyCompatEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.status is status and (entry.present_in_source or not present_only)
        )

    def status_counts(self, *, present_only: bool = False) -> dict[str, int]:
        return {
            status.value: len(self.entries_for_status(status, present_only=present_only))
            for status in (
                LegacyPolicyCompatStatus.MAPPED,
                LegacyPolicyCompatStatus.PARTIALLY_MAPPED,
                LegacyPolicyCompatStatus.DEPRECATED,
                LegacyPolicyCompatStatus.UNSUPPORTED,
            )
        }

    def explicitly_reported_keys(self) -> tuple[str, ...]:
        return tuple(entry.key for entry in self.entries if entry.present_in_source)


@dataclass(frozen=True, slots=True)
class _PolicyRule:
    key: str
    category: LegacyPolicyCompatCategory
    status: LegacyPolicyCompatStatus
    mapped_fields: tuple[str, ...] = ()
    replacement_native_fields: tuple[str, ...] = ()
    note: str | None = None


_POLICY_RULES: tuple[_PolicyRule, ...] = (
    _PolicyRule(
        key="INTEGRATION_COUNT",
        category=LegacyPolicyCompatCategory.INTEGRATION,
        status=LegacyPolicyCompatStatus.UNSUPPORTED,
        note="Native config does not expose legacy integration-count gating.",
    ),
    _PolicyRule(
        key="INTEGRATION_MODE",
        category=LegacyPolicyCompatCategory.INTEGRATION,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("execution.integration_mode",),
    ),
    _PolicyRule(
        key="INTEGRATION_TARGET",
        category=LegacyPolicyCompatCategory.INTEGRATION,
        status=LegacyPolicyCompatStatus.UNSUPPORTED,
        note="Native config does not expose legacy integration-target gating.",
    ),
    _PolicyRule(
        key="RUN_UPDATE_ON_EMPTY",
        category=LegacyPolicyCompatCategory.INTEGRATION,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("execution.run_update_on_empty",),
    ),
    _PolicyRule(
        key="SIZE_METRIC_MODE",
        category=LegacyPolicyCompatCategory.SIZING,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("sizing.mode",),
    ),
    _PolicyRule(
        key="LARGE_FILES_THRESHOLD",
        category=LegacyPolicyCompatCategory.SIZING,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("sizing.repo.file_count_threshold",),
    ),
    _PolicyRule(
        key="LARGE_LOC_THRESHOLD",
        category=LegacyPolicyCompatCategory.SIZING,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("sizing.repo.nonempty_line_count_threshold",),
    ),
    _PolicyRule(
        key="TASK_LARGE_FILES_THRESHOLD",
        category=LegacyPolicyCompatCategory.SIZING,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("sizing.task.file_count_threshold",),
    ),
    _PolicyRule(
        key="TASK_LARGE_LOC_THRESHOLD",
        category=LegacyPolicyCompatCategory.SIZING,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("sizing.task.nonempty_line_count_threshold",),
    ),
    _PolicyRule(
        key="ENV_PREFLIGHT_MODE",
        category=LegacyPolicyCompatCategory.PREFLIGHT,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.preflight.enabled",),
    ),
    _PolicyRule(
        key="ENV_PREFLIGHT_TRANSPORT_CHECK",
        category=LegacyPolicyCompatCategory.PREFLIGHT,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.preflight.transport_check",),
    ),
    _PolicyRule(
        key="NETWORK_GUARD_MODE",
        category=LegacyPolicyCompatCategory.NETWORK_GUARD,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.network_guard.enabled",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_MAX_PROBES",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.max_probes",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_POLICY",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.policy",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_PROBE_CMD",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.probe_command",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_PROBE_HOST",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.probe_host",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_PROBE_PORT",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.probe_port",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_PROBE_TIMEOUT_SECS",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.probe_timeout_seconds",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_RESILIENCE_MODE",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.enabled",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_ROUTE_TO_BLOCKER",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.route_to_blocker",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_ROUTE_TO_INCIDENT",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.route_to_incident",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_WAIT_INITIAL_SECS",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.wait_initial_seconds",),
    ),
    _PolicyRule(
        key="NETWORK_OUTAGE_WAIT_MAX_SECS",
        category=LegacyPolicyCompatCategory.OUTAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.outage.wait_max_seconds",),
    ),
    _PolicyRule(
        key="ORCH_ALLOW_SEARCH",
        category=LegacyPolicyCompatCategory.SEARCH,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.search.execution_enabled",),
    ),
    _PolicyRule(
        key="ORCH_ALLOW_SEARCH_EXCEPTION",
        category=LegacyPolicyCompatCategory.SEARCH,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.search.execution_exception",),
    ),
    _PolicyRule(
        key="ORCH_INTER_TASK_DELAY_MODE",
        category=LegacyPolicyCompatCategory.PACING,
        status=LegacyPolicyCompatStatus.PARTIALLY_MAPPED,
        mapped_fields=("engine.inter_task_delay_seconds",),
        replacement_native_fields=("engine.inter_task_delay_seconds",),
        note="Native config only carries delay seconds; the explicit mode override is not preserved separately.",
    ),
    _PolicyRule(
        key="ORCH_INTER_TASK_DELAY_SECS",
        category=LegacyPolicyCompatCategory.PACING,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("engine.inter_task_delay_seconds",),
    ),
    _PolicyRule(
        key="ORCH_NETWORK_GUARD_POLICY",
        category=LegacyPolicyCompatCategory.NETWORK_GUARD,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.network_guard.execution_policy",),
    ),
    _PolicyRule(
        key="ORCH_NETWORK_POLICY_EXCEPTION",
        category=LegacyPolicyCompatCategory.NETWORK_GUARD,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.network_guard.execution_exception",),
    ),
    _PolicyRule(
        key="ORCH_WEEKLY_REFRESH_UTC",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.execution.refresh_utc",),
    ),
    _PolicyRule(
        key="ORCH_WEEKLY_USAGE_CONSUMED_THRESHOLD",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.execution.consumed_threshold",),
    ),
    _PolicyRule(
        key="ORCH_WEEKLY_USAGE_REMAINING_THRESHOLD",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.execution.remaining_threshold",),
    ),
    _PolicyRule(
        key="ORCH_WEEKLY_USAGE_THRESHOLD",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.DEPRECATED,
        mapped_fields=("policies.usage.execution.legacy_threshold",),
        replacement_native_fields=("policies.usage.execution.remaining_threshold",),
        note="Legacy threshold is retained only for compatibility until policy enforcement uses the native remaining-threshold contract.",
    ),
    _PolicyRule(
        key="RESEARCH_ALLOW_SEARCH",
        category=LegacyPolicyCompatCategory.SEARCH,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.search.research_enabled",),
    ),
    _PolicyRule(
        key="RESEARCH_ALLOW_SEARCH_EXCEPTION",
        category=LegacyPolicyCompatCategory.SEARCH,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.search.research_exception",),
    ),
    _PolicyRule(
        key="RESEARCH_NETWORK_GUARD_POLICY",
        category=LegacyPolicyCompatCategory.NETWORK_GUARD,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.network_guard.research_policy",),
    ),
    _PolicyRule(
        key="RESEARCH_NETWORK_POLICY_EXCEPTION",
        category=LegacyPolicyCompatCategory.NETWORK_GUARD,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.network_guard.research_exception",),
    ),
    _PolicyRule(
        key="RESEARCH_WEEKLY_REFRESH_UTC",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.research.refresh_utc",),
    ),
    _PolicyRule(
        key="RESEARCH_WEEKLY_USAGE_CONSUMED_THRESHOLD",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.research.consumed_threshold",),
    ),
    _PolicyRule(
        key="RESEARCH_WEEKLY_USAGE_REMAINING_THRESHOLD",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.research.remaining_threshold",),
    ),
    _PolicyRule(
        key="RESEARCH_WEEKLY_USAGE_THRESHOLD",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.DEPRECATED,
        mapped_fields=("policies.usage.research.legacy_threshold",),
        replacement_native_fields=("policies.usage.research.remaining_threshold",),
        note="Legacy threshold is retained only for compatibility until policy enforcement uses the native remaining-threshold contract.",
    ),
    _PolicyRule(
        key="USAGE_AUTOPAUSE_MODE",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.enabled",),
    ),
    _PolicyRule(
        key="USAGE_SAMPLER_CACHE_MAX_AGE_SECS",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.cache_max_age_secs",),
    ),
    _PolicyRule(
        key="USAGE_SAMPLER_ORCH_CMD",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.orch_command",),
    ),
    _PolicyRule(
        key="USAGE_SAMPLER_PROVIDER",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.provider",),
    ),
    _PolicyRule(
        key="USAGE_SAMPLER_RESEARCH_CMD",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.research_command",),
    ),
    _PolicyRule(
        key="CODEX_AUTH_SOURCE_DIR",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.codex_auth_source_dir",),
    ),
    _PolicyRule(
        key="CODEX_RUNTIME_HOME",
        category=LegacyPolicyCompatCategory.USAGE,
        status=LegacyPolicyCompatStatus.MAPPED,
        mapped_fields=("policies.usage.codex_runtime_home",),
    ),
)


def build_legacy_policy_compatibility_report(
    workflow_values: Mapping[str, str],
) -> LegacyPolicyCompatReport:
    """Return the audited workflow-policy compatibility surface."""

    return LegacyPolicyCompatReport(
        entries=tuple(
            LegacyPolicyCompatEntry(
                key=rule.key,
                category=rule.category,
                status=rule.status,
                present_in_source=rule.key in workflow_values,
                mapped_fields=rule.mapped_fields,
                replacement_native_fields=rule.replacement_native_fields,
                note=rule.note,
            )
            for rule in _POLICY_RULES
        )
    )
