"""Runtime-stage and config-boundary helpers re-exported by config.py."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contract_core import HeadlessPermissionProfile, ReasoningEffort, RunnerKind, StageType


class MillraceRuntimeConfigModel(BaseModel):
    """Closed-world config base for runtime-stage helper models."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _to_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    text = value.strip()
    if not text:
        return None
    return Path(text).expanduser()


class StageConfig(MillraceRuntimeConfigModel):
    runner: RunnerKind = RunnerKind.CODEX
    model: str = "gpt-5.3-codex"
    effort: ReasoningEffort | None = None
    permission_profile: HeadlessPermissionProfile = HeadlessPermissionProfile.NORMAL
    timeout_seconds: int = Field(default=3600, ge=1)
    prompt_file: Path | None = None
    allow_search: bool = False

    @field_validator("prompt_file", mode="before")
    @classmethod
    def normalize_prompt_file(cls, value: str | Path | None) -> Path | None:
        return _to_path(value)


def _coerce_stage_sequence(
    value: tuple[StageType, ...] | list[StageType | str] | None,
) -> tuple[StageType, ...]:
    if value is None:
        return ()
    items = tuple(value)
    normalized: list[StageType] = []
    for item in items:
        if isinstance(item, StageType):
            normalized.append(item)
            continue
        normalized.append(StageType(str(item)))
    return tuple(normalized)


def _validate_stage_sequence(
    value: tuple[StageType, ...],
    *,
    field_label: str,
    allowed: frozenset[StageType],
    allow_empty: bool,
) -> tuple[StageType, ...]:
    if not value and not allow_empty:
        raise ValueError(f"{field_label} may not be empty")
    invalid = [stage.value for stage in value if stage not in allowed]
    if invalid:
        raise ValueError(f"{field_label} contains unsupported stages: {', '.join(invalid)}")
    return value


class RoutingConfig(MillraceRuntimeConfigModel):
    builder_success_sequence: tuple[StageType, ...] = (StageType.QA,)
    builder_success_sequence_with_integration: tuple[StageType, ...] = (
        StageType.INTEGRATION,
        StageType.QA,
    )
    qa_success_sequence: tuple[StageType, ...] = (StageType.UPDATE,)
    backlog_empty_sequence: tuple[StageType, ...] = (StageType.UPDATE,)
    quickfix_stage: StageType = StageType.HOTFIX
    quickfix_verification_stage: StageType = StageType.DOUBLECHECK
    escalation_sequence: tuple[StageType, ...] = (
        StageType.TROUBLESHOOT,
        StageType.CONSULT,
    )

    @field_validator(
        "builder_success_sequence",
        "builder_success_sequence_with_integration",
        "qa_success_sequence",
        "backlog_empty_sequence",
        "escalation_sequence",
        mode="before",
    )
    @classmethod
    def normalize_sequences(
        cls,
        value: tuple[StageType, ...] | list[StageType | str] | None,
    ) -> tuple[StageType, ...]:
        return _coerce_stage_sequence(value)

    @field_validator("builder_success_sequence", "builder_success_sequence_with_integration")
    @classmethod
    def validate_builder_sequences(cls, value: tuple[StageType, ...]) -> tuple[StageType, ...]:
        value = _validate_stage_sequence(
            value,
            field_label="builder success sequence",
            allowed=frozenset({StageType.INTEGRATION, StageType.QA}),
            allow_empty=False,
        )
        if value[-1] is not StageType.QA:
            raise ValueError("builder success sequence must end with qa")
        if value.count(StageType.QA) != 1:
            raise ValueError("builder success sequence must include qa exactly once")
        if value.count(StageType.INTEGRATION) > 1:
            raise ValueError("builder success sequence may include integration at most once")
        if StageType.INTEGRATION in value and value.index(StageType.INTEGRATION) > value.index(
            StageType.QA
        ):
            raise ValueError("integration must come before qa in builder success sequence")
        return value

    @field_validator("qa_success_sequence", "backlog_empty_sequence")
    @classmethod
    def validate_update_sequences(cls, value: tuple[StageType, ...]) -> tuple[StageType, ...]:
        return _validate_stage_sequence(
            value,
            field_label="update sequence",
            allowed=frozenset({StageType.UPDATE}),
            allow_empty=True,
        )

    @field_validator("escalation_sequence")
    @classmethod
    def validate_escalation_sequence(cls, value: tuple[StageType, ...]) -> tuple[StageType, ...]:
        return _validate_stage_sequence(
            value,
            field_label="escalation sequence",
            allowed=frozenset({StageType.TROUBLESHOOT, StageType.CONSULT}),
            allow_empty=False,
        )

    @field_validator("quickfix_stage")
    @classmethod
    def validate_quickfix_stage(cls, value: StageType) -> StageType:
        if value is not StageType.HOTFIX:
            raise ValueError("quickfix_stage must be hotfix in v1")
        return value

    @field_validator("quickfix_verification_stage")
    @classmethod
    def validate_quickfix_verification_stage(cls, value: StageType) -> StageType:
        if value not in {StageType.DOUBLECHECK, StageType.QA}:
            raise ValueError("quickfix_verification_stage must be doublecheck or qa")
        return value


class ConfigApplyBoundary(str, Enum):
    """Runtime application boundary for a config change."""

    LIVE_IMMEDIATE = "live_immediate"
    STAGE_BOUNDARY = "stage_boundary"
    CYCLE_BOUNDARY = "cycle_boundary"
    STARTUP_ONLY = "startup_only"


def _field_matches(prefix: str, field_name: str) -> bool:
    return field_name == prefix or field_name.startswith(f"{prefix}.")


def _boundary_rank(boundary: ConfigApplyBoundary) -> int:
    ordering = {
        ConfigApplyBoundary.LIVE_IMMEDIATE: 0,
        ConfigApplyBoundary.STAGE_BOUNDARY: 1,
        ConfigApplyBoundary.CYCLE_BOUNDARY: 2,
        ConfigApplyBoundary.STARTUP_ONLY: 3,
    }
    return ordering[boundary]


class ConfigBoundaries(MillraceRuntimeConfigModel):
    startup_only_fields: tuple[str, ...] = (
        "paths.workspace",
        "paths.agents_dir",
    )
    live_immediate_fields: tuple[str, ...] = (
        "engine.poll_interval_seconds",
        "engine.inter_task_delay_seconds",
    )
    stage_boundary_fields: tuple[str, ...] = (
        "engine.mode",
        "execution.integration_mode",
        "execution.quickfix_max_attempts",
        "execution.run_update_on_empty",
        "routing",
        "policies.search",
        "policies.compounding",
        "policies.complexity",
        "policies.usage",
        "policies.network_guard",
        "policies.preflight",
        "policies.outage",
        "stages",
    )
    cycle_boundary_fields: tuple[str, ...] = (
        "engine.idle_mode",
        "sizing",
        "research.mode",
        "research.idle_mode",
        "research.idle_poll_seconds",
        "research.stage_retry_max",
        "research.stage_retry_backoff_seconds",
        "research.interview_policy",
        "watchers",
    )

    def classify_field(self, field_name: str) -> ConfigApplyBoundary:
        """Return the narrowest supported runtime boundary for one dotted field name."""

        for prefix in self.startup_only_fields:
            if _field_matches(prefix, field_name):
                return ConfigApplyBoundary.STARTUP_ONLY
        for prefix in self.live_immediate_fields:
            if _field_matches(prefix, field_name):
                return ConfigApplyBoundary.LIVE_IMMEDIATE
        for prefix in self.stage_boundary_fields:
            if _field_matches(prefix, field_name):
                return ConfigApplyBoundary.STAGE_BOUNDARY
        for prefix in self.cycle_boundary_fields:
            if _field_matches(prefix, field_name):
                return ConfigApplyBoundary.CYCLE_BOUNDARY
        return ConfigApplyBoundary.CYCLE_BOUNDARY

    def classify_fields(self, field_names: tuple[str, ...]) -> ConfigApplyBoundary | None:
        """Return the strictest runtime boundary for a set of changed dotted fields."""

        if not field_names:
            return None
        boundary = ConfigApplyBoundary.LIVE_IMMEDIATE
        for field_name in field_names:
            candidate = self.classify_field(field_name)
            if _boundary_rank(candidate) > _boundary_rank(boundary):
                boundary = candidate
        return boundary


def default_stage_configs() -> dict[StageType, StageConfig]:
    return {
        StageType.BUILDER: StageConfig(prompt_file=Path("agents/_start.md")),
        StageType.INTEGRATION: StageConfig(prompt_file=Path("agents/_integrate.md")),
        StageType.QA: StageConfig(prompt_file=Path("agents/_check.md")),
        StageType.HOTFIX: StageConfig(prompt_file=Path("agents/_hotfix.md")),
        StageType.DOUBLECHECK: StageConfig(prompt_file=Path("agents/_doublecheck.md")),
        StageType.TROUBLESHOOT: StageConfig(prompt_file=Path("agents/_troubleshoot.md")),
        StageType.CONSULT: StageConfig(prompt_file=Path("agents/_consult.md")),
        StageType.UPDATE: StageConfig(prompt_file=Path("agents/_update.md")),
        StageType.LARGE_PLAN: StageConfig(prompt_file=Path("agents/_start_large_plan.md")),
        StageType.LARGE_EXECUTE: StageConfig(prompt_file=Path("agents/_start_large_execute.md")),
        StageType.REASSESS: StageConfig(prompt_file=Path("agents/prompts/reassess.md")),
        StageType.REFACTOR: StageConfig(prompt_file=Path("agents/_refactor.md")),
        StageType.GOAL_INTAKE: StageConfig(
            model="gpt-5.3-codex",
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_goal_intake.md"),
        ),
        StageType.OBJECTIVE_PROFILE_SYNC: StageConfig(
            model="gpt-5.3-codex",
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_objective_profile_sync.md"),
        ),
        StageType.SPEC_SYNTHESIS: StageConfig(
            model="gpt-5.2",
            effort=ReasoningEffort.XHIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_spec_synthesis.md"),
        ),
        StageType.SPEC_INTERVIEW: StageConfig(
            model="gpt-5.3-codex",
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_spec_interview.md"),
        ),
        StageType.SPEC_REVIEW: StageConfig(
            model="gpt-5.3-codex",
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_spec_review.md"),
        ),
        StageType.TASKMASTER: StageConfig(
            model="gpt-5.3-codex",
            effort=ReasoningEffort.XHIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_taskmaster.md"),
        ),
        StageType.TASKAUDIT: StageConfig(
            model="gpt-5.3-codex",
            effort=ReasoningEffort.MEDIUM,
            timeout_seconds=5400,
            prompt_file=Path("agents/_taskaudit.md"),
        ),
        StageType.CLARIFY: StageConfig(
            model="gpt-5.2",
            effort=ReasoningEffort.XHIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_clarify.md"),
        ),
        StageType.CRITIC: StageConfig(
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_critic.md"),
        ),
        StageType.DESIGNER: StageConfig(
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_designer.md"),
        ),
        StageType.PHASESPLIT: StageConfig(
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_designer.md"),
        ),
        StageType.INCIDENT_INTAKE: StageConfig(
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_incident_intake.md"),
        ),
        StageType.INCIDENT_RESOLVE: StageConfig(
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_incident_resolve.md"),
        ),
        StageType.INCIDENT_ARCHIVE: StageConfig(
            effort=ReasoningEffort.MEDIUM,
            timeout_seconds=5400,
            prompt_file=Path("agents/_incident_archive.md"),
        ),
        StageType.AUDIT_INTAKE: StageConfig(
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_audit_intake.md"),
        ),
        StageType.AUDIT_VALIDATE: StageConfig(
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_audit_validate.md"),
        ),
        StageType.AUDIT_GATEKEEPER: StageConfig(
            effort=ReasoningEffort.HIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_audit_gatekeeper.md"),
        ),
        StageType.MECHANIC: StageConfig(
            effort=ReasoningEffort.XHIGH,
            timeout_seconds=5400,
            prompt_file=Path("agents/_mechanic.md"),
        ),
    }


__all__ = [
    "ConfigApplyBoundary",
    "ConfigBoundaries",
    "RoutingConfig",
    "StageConfig",
    "default_stage_configs",
]
