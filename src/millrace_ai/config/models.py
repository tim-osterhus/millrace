"""Typed runtime configuration models and shared constants."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from millrace_ai.contracts import ExecutionStageName, PlanningStageName, RuntimeMode

DEFAULT_CONFIG_PATH = Path("millrace-agents") / "millrace.toml"

KNOWN_STAGE_NAMES = {
    *(stage.value for stage in ExecutionStageName),
    *(stage.value for stage in PlanningStageName),
}


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeSection(ConfigModel):
    default_mode: str = "default_codex"
    run_style: RuntimeMode = RuntimeMode.DAEMON
    idle_sleep_seconds: float = Field(default=1.0, gt=0)


class CodexPermissionLevel(str, Enum):
    BASIC = "basic"
    ELEVATED = "elevated"
    MAXIMUM = "maximum"


class CodexRunnerSection(ConfigModel):
    command: str = "codex"
    args: tuple[str, ...] = ("exec",)
    profile: str | None = None
    permission_default: CodexPermissionLevel = CodexPermissionLevel.MAXIMUM
    permission_by_stage: dict[str, CodexPermissionLevel] = Field(default_factory=dict)
    permission_by_model: dict[str, CodexPermissionLevel] = Field(default_factory=dict)
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
            raise ValueError(
                f"unknown stage names in runners.codex.permission_by_stage: {names}"
            )
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
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("args")
    @classmethod
    def validate_reserved_transport_flags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        conflicts = tuple(
            arg
            for arg in value
            if arg in _PI_RESERVED_ARGS
            or any(arg.startswith(f"{reserved}=") for reserved in _PI_RESERVED_ARGS)
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
    stale_state_recovery_enabled: bool = True


class WatchersSection(ConfigModel):
    enabled: bool = True
    debounce_ms: int = Field(default=250, gt=0)
    watch_ideas_inbox: bool = True
    watch_specs_queue: bool = True


class StageConfig(ConfigModel):
    runner: str | None = None
    model: str | None = None
    timeout_seconds: int = Field(default=3600, gt=0)


class RuntimeConfig(ConfigModel):
    runtime: RuntimeSection = Field(default_factory=RuntimeSection)
    runners: RunnersSection = Field(default_factory=RunnersSection)
    recovery: RecoverySection = Field(default_factory=RecoverySection)
    watchers: WatchersSection = Field(default_factory=WatchersSection)
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
    "CodexPermissionLevel",
    "CodexRunnerSection",
    "ConfigModel",
    "DEFAULT_CONFIG_PATH",
    "KNOWN_STAGE_NAMES",
    "RecoverySection",
    "PiRunnerSection",
    "RunnersSection",
    "RuntimeConfig",
    "RuntimeSection",
    "StageConfig",
    "WatchersSection",
]
