"""Typed runtime configuration models and shared constants."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from millrace_ai.contracts import ExecutionStageName, LearningStageName, PlanningStageName, RuntimeMode
from millrace_ai.contracts.capabilities import CapabilityPolicyDecision, normalize_capability_id

DEFAULT_CONFIG_PATH = Path("millrace-agents") / "millrace.toml"
DEFAULT_IDLE_EVENT_HEARTBEAT_SECONDS = 21600.0

KNOWN_STAGE_NAMES = {
    *(stage.value for stage in ExecutionStageName),
    *(stage.value for stage in LearningStageName),
    *(stage.value for stage in PlanningStageName),
}


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeSection(ConfigModel):
    default_mode: str = "lad_codex"
    run_style: RuntimeMode = RuntimeMode.DAEMON
    idle_sleep_seconds: float = Field(default=1.0, gt=0)
    idle_event_heartbeat_seconds: float = Field(default=DEFAULT_IDLE_EVENT_HEARTBEAT_SECONDS, gt=0)

    @field_validator("run_style", mode="before")
    @classmethod
    def reject_legacy_run_once_style(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() == "once":
            raise ValueError(
                "runtime.run_style = 'once' was removed; use run_style = 'daemon' "
                "with millrace run daemon --max-ticks N for bounded execution"
            )
        return value


class CodexPermissionLevel(str, Enum):
    BASIC = "basic"
    ELEVATED = "elevated"
    MAXIMUM = "maximum"


class CodexReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class ModelAssignmentInvalidPolicy(str, Enum):
    WARN_FALLBACK = "warn_fallback"


class PiEventLogPolicy(str, Enum):
    FAILURE_FULL = "failure_full"
    FULL = "full"


class UsageGovernanceEvaluationBoundary(str, Enum):
    BETWEEN_STAGES = "between_stages"


class UsageGovernanceRuntimeTokenWindow(str, Enum):
    ROLLING_5H = "rolling_5h"
    CALENDAR_WEEK = "calendar_week"
    DAEMON_SESSION = "daemon_session"
    PER_RUN = "per_run"


class UsageGovernanceRuntimeTokenMetric(str, Enum):
    TOTAL_TOKENS = "total_tokens"


class UsageGovernanceSubscriptionWindow(str, Enum):
    FIVE_HOUR = "five_hour"
    WEEKLY = "weekly"


class UsageGovernanceSubscriptionProvider(str, Enum):
    CODEX_CHATGPT_OAUTH = "codex_chatgpt_oauth"


class UsageGovernanceDegradedPolicy(str, Enum):
    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


def _default_runtime_token_rules() -> tuple["RuntimeTokenRule", ...]:
    return (
        RuntimeTokenRule(
            rule_id="rolling-5h-default",
            window=UsageGovernanceRuntimeTokenWindow.ROLLING_5H,
            threshold=750_000,
        ),
        RuntimeTokenRule(
            rule_id="calendar-week-default",
            window=UsageGovernanceRuntimeTokenWindow.CALENDAR_WEEK,
            threshold=5_000_000,
        ),
    )


def _default_subscription_quota_rules() -> tuple["SubscriptionQuotaRule", ...]:
    return (
        SubscriptionQuotaRule(
            rule_id="codex-five-hour-default",
            window=UsageGovernanceSubscriptionWindow.FIVE_HOUR,
            pause_at_percent_used=95,
        ),
        SubscriptionQuotaRule(
            rule_id="codex-weekly-default",
            window=UsageGovernanceSubscriptionWindow.WEEKLY,
            pause_at_percent_used=95,
        ),
    )


class RuntimeTokenRule(ConfigModel):
    rule_id: str
    window: UsageGovernanceRuntimeTokenWindow
    metric: UsageGovernanceRuntimeTokenMetric = UsageGovernanceRuntimeTokenMetric.TOTAL_TOKENS
    threshold: int = Field(gt=0)


class RuntimeTokenRulesSection(ConfigModel):
    enabled: bool = True
    rules: tuple[RuntimeTokenRule, ...] = Field(default_factory=_default_runtime_token_rules)


class SubscriptionQuotaRule(ConfigModel):
    rule_id: str
    window: UsageGovernanceSubscriptionWindow
    pause_at_percent_used: float = Field(gt=0, le=100)


class SubscriptionQuotaRulesSection(ConfigModel):
    enabled: bool = False
    provider: UsageGovernanceSubscriptionProvider = UsageGovernanceSubscriptionProvider.CODEX_CHATGPT_OAUTH
    degraded_policy: UsageGovernanceDegradedPolicy = UsageGovernanceDegradedPolicy.FAIL_OPEN
    refresh_interval_seconds: int = Field(default=60, gt=0)
    rules: tuple[SubscriptionQuotaRule, ...] = Field(default_factory=_default_subscription_quota_rules)


class UsageGovernanceSection(ConfigModel):
    enabled: bool = False
    auto_resume: bool = True
    evaluation_boundary: UsageGovernanceEvaluationBoundary = UsageGovernanceEvaluationBoundary.BETWEEN_STAGES
    calendar_timezone: str = "UTC"
    runtime_token_rules: RuntimeTokenRulesSection = Field(default_factory=RuntimeTokenRulesSection)
    subscription_quota_rules: SubscriptionQuotaRulesSection = Field(default_factory=SubscriptionQuotaRulesSection)


def _default_execution_capability_decisions() -> dict[str, CapabilityPolicyDecision]:
    return {
        "network.access": CapabilityPolicyDecision.DENY,
        "package.install": CapabilityPolicyDecision.APPROVAL_REQUIRED,
        "git.mutate": CapabilityPolicyDecision.APPROVAL_REQUIRED,
        "shell.run": CapabilityPolicyDecision.ALLOW,
        "workspace.write": CapabilityPolicyDecision.ALLOW,
    }


class ExecutionCapabilitiesSection(ConfigModel):
    enabled: bool = True
    default_unknown_capability: CapabilityPolicyDecision = CapabilityPolicyDecision.DENY
    allow_advisory_grants: bool = True
    fail_required_advisory: bool = False
    defaults: dict[str, CapabilityPolicyDecision] = Field(
        default_factory=_default_execution_capability_decisions
    )

    @field_validator("defaults", mode="before")
    @classmethod
    def normalize_default_keys(
        cls,
        value: dict[str, str | CapabilityPolicyDecision] | None,
    ) -> dict[str, str | CapabilityPolicyDecision]:
        if not value:
            return {}
        return {normalize_capability_id(str(key)): decision for key, decision in value.items()}


class CodexRunnerSection(ConfigModel):
    command: str = "codex"
    args: tuple[str, ...] = ("exec",)
    profile: str | None = None
    permission_default: CodexPermissionLevel = CodexPermissionLevel.MAXIMUM
    permission_by_stage: dict[str, CodexPermissionLevel] = Field(default_factory=dict)
    permission_by_model: dict[str, CodexPermissionLevel] = Field(default_factory=dict)
    model_reasoning_effort: CodexReasoningEffort | None = None
    skip_git_repo_check: bool = True
    extra_config: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("permission_by_stage")
    @classmethod
    def validate_permission_stage_names(
        cls,
        value: dict[str, CodexPermissionLevel],
    ) -> dict[str, CodexPermissionLevel]:
        unknown = sorted(set(value) - KNOWN_STAGE_NAMES)
        if unknown:
            names = ", ".join(unknown)
            raise ValueError(f"unknown stage names in runners.codex.permission_by_stage: {names}")
        return value


_PI_RESERVED_ARGS = frozenset(
    {
        "--mode",
        "--no-session",
        "--provider",
        "--model",
        "--thinking",
        "--no-context-files",
        "--no-skills",
    }
)


class PiRunnerSection(ConfigModel):
    command: str = "pi"
    args: tuple[str, ...] = ()
    provider: str | None = None
    thinking: str | None = None
    disable_context_files: bool = True
    disable_skills: bool = True
    event_log_policy: PiEventLogPolicy = PiEventLogPolicy.FAILURE_FULL
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("args")
    @classmethod
    def validate_reserved_transport_flags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        conflicts = tuple(
            arg
            for arg in value
            if arg in _PI_RESERVED_ARGS or any(arg.startswith(f"{reserved}=") for reserved in _PI_RESERVED_ARGS)
        )
        if conflicts:
            joined = ", ".join(conflicts)
            raise ValueError(f"reserved pi runner flags are not allowed in runners.pi.args: {joined}")
        return value


class RunnersSection(ConfigModel):
    default_runner: str = "codex_cli"
    codex: CodexRunnerSection = Field(default_factory=CodexRunnerSection)
    pi: PiRunnerSection = Field(default_factory=PiRunnerSection)


class RecoverySection(ConfigModel):
    max_fix_cycles: int = Field(default=2, gt=0)
    max_troubleshoot_attempts_before_consult: int = Field(default=2, gt=0)
    max_mechanic_attempts: int = Field(default=2, gt=0)
    max_repair_attempts: int = Field(default=2, gt=0)
    stale_state_recovery_enabled: bool = True


class AutoRecoverySection(ConfigModel):
    enabled: bool = True
    blocked_dependency_retry_enabled: bool = True
    max_auto_requeues_per_work_item: int = Field(default=3, ge=0)
    cooldown_seconds: tuple[int, ...] = (300, 900, 3600)

    @field_validator("cooldown_seconds")
    @classmethod
    def validate_cooldown_seconds(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("auto recovery cooldown_seconds must not be empty")
        if any(seconds < 0 for seconds in value):
            raise ValueError("auto recovery cooldown_seconds values must be >= 0")
        return value


class WatchersSection(ConfigModel):
    enabled: bool = True
    debounce_ms: int = Field(default=250, gt=0)
    watch_ideas_inbox: bool = True
    watch_specs_queue: bool = True


class ModelAliasDefinition(ConfigModel):
    model: str | None = None
    thinking_level: str | None = None


def _default_model_aliases() -> dict[str, ModelAliasDefinition]:
    return {
        "fast": ModelAliasDefinition(model="gpt-5.4-mini", thinking_level="high"),
        "standard": ModelAliasDefinition(model="gpt-5.5", thinking_level="medium"),
        "deep": ModelAliasDefinition(model="gpt-5.5", thinking_level="xhigh"),
    }


class ModelAssignmentSection(ConfigModel):
    enabled: bool = True
    default_alias: str = "standard"
    invalid_assignment_policy: ModelAssignmentInvalidPolicy = ModelAssignmentInvalidPolicy.WARN_FALLBACK
    by_loop: dict[str, str] = Field(default_factory=dict)
    by_stage: dict[str, str] = Field(default_factory=dict)


class StageConfig(ConfigModel):
    runner: str | None = None
    model: str | None = None
    thinking_level: str | None = None
    model_reasoning_effort: CodexReasoningEffort | None = None
    timeout_seconds: int = Field(default=3600, gt=0)

    @field_validator("thinking_level")
    @classmethod
    def validate_thinking_level(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("thinking_level must not be empty")
        return normalized

    @model_validator(mode="after")
    def normalize_thinking_aliases(self) -> "StageConfig":
        legacy_value = self.model_reasoning_effort.value if self.model_reasoning_effort is not None else None
        if self.thinking_level is not None and legacy_value is not None:
            if self.thinking_level != legacy_value:
                raise ValueError("thinking_level and model_reasoning_effort must match when both are set")
            return self
        if self.thinking_level is None and legacy_value is not None:
            self.thinking_level = legacy_value
        return self


class RuntimeConfig(ConfigModel):
    runtime: RuntimeSection = Field(default_factory=RuntimeSection)
    runners: RunnersSection = Field(default_factory=RunnersSection)
    recovery: RecoverySection = Field(default_factory=RecoverySection)
    watchers: WatchersSection = Field(default_factory=WatchersSection)
    auto_recovery: AutoRecoverySection = Field(default_factory=AutoRecoverySection)
    usage_governance: UsageGovernanceSection = Field(default_factory=UsageGovernanceSection)
    execution_capabilities: ExecutionCapabilitiesSection = Field(default_factory=ExecutionCapabilitiesSection)
    model_aliases: dict[str, ModelAliasDefinition] = Field(default_factory=_default_model_aliases)
    model_assignment: ModelAssignmentSection = Field(default_factory=ModelAssignmentSection)
    stages: dict[str, StageConfig] = Field(default_factory=dict)

    @field_validator("stages")
    @classmethod
    def validate_stage_names(cls, value: dict[str, StageConfig]) -> dict[str, StageConfig]:
        unknown = sorted(set(value) - KNOWN_STAGE_NAMES)
        if unknown:
            names = ", ".join(unknown)
            raise ValueError(f"unknown stage names in config: {names}")
        return value


__all__ = [
    "AutoRecoverySection",
    "CodexPermissionLevel",
    "CodexReasoningEffort",
    "CodexRunnerSection",
    "ConfigModel",
    "DEFAULT_CONFIG_PATH",
    "ExecutionCapabilitiesSection",
    "KNOWN_STAGE_NAMES",
    "ModelAliasDefinition",
    "ModelAssignmentInvalidPolicy",
    "ModelAssignmentSection",
    "RecoverySection",
    "PiEventLogPolicy",
    "PiRunnerSection",
    "RunnersSection",
    "RuntimeConfig",
    "RuntimeSection",
    "RuntimeTokenRule",
    "RuntimeTokenRulesSection",
    "StageConfig",
    "SubscriptionQuotaRule",
    "SubscriptionQuotaRulesSection",
    "UsageGovernanceDegradedPolicy",
    "UsageGovernanceEvaluationBoundary",
    "UsageGovernanceRuntimeTokenMetric",
    "UsageGovernanceRuntimeTokenWindow",
    "UsageGovernanceSection",
    "UsageGovernanceSubscriptionProvider",
    "UsageGovernanceSubscriptionWindow",
    "WatchersSection",
]
