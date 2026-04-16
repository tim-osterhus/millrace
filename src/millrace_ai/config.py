"""Runtime config model, source precedence, and apply-boundary helpers."""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from millrace_ai.contracts import ExecutionStageName, PlanningStageName, RuntimeMode

DEFAULT_CONFIG_PATH = Path("millrace-agents") / "millrace.toml"

KNOWN_STAGE_NAMES = {
    *(stage.value for stage in ExecutionStageName),
    *(stage.value for stage in PlanningStageName),
}


class ApplyBoundary(str, Enum):
    IMMEDIATE = "immediate"
    NEXT_TICK = "next_tick"
    RECOMPILE = "recompile"
    RESTART = "restart"


_BOUNDARY_PRIORITY: dict[ApplyBoundary, int] = {
    ApplyBoundary.IMMEDIATE: 0,
    ApplyBoundary.NEXT_TICK: 1,
    ApplyBoundary.RECOMPILE: 2,
    ApplyBoundary.RESTART: 3,
}


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeSection(ConfigModel):
    default_mode: str = "standard_plain"
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
    permission_default: CodexPermissionLevel = CodexPermissionLevel.BASIC
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


class RunnersSection(ConfigModel):
    default_runner: str = "codex_cli"
    codex: CodexRunnerSection = Field(default_factory=CodexRunnerSection)


class RecoverySection(ConfigModel):
    max_fix_cycles: int = Field(default=2, gt=0)
    max_troubleshoot_attempts_before_consult: int = Field(default=2, gt=0)
    max_mechanic_attempts: int = Field(default=2, gt=0)
    stale_state_recovery_enabled: bool = True


class WatchersSection(ConfigModel):
    enabled: bool = False
    debounce_ms: int = Field(default=250, gt=0)
    watch_ideas_inbox: bool = True
    watch_specs_queue: bool = True


class StageConfig(ConfigModel):
    runner: str | None = None
    model: str | None = None
    timeout_seconds: int = Field(default=300, gt=0)


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


_FIELD_BOUNDARIES: dict[str, ApplyBoundary] = {
    "runtime.default_mode": ApplyBoundary.RECOMPILE,
    "runtime.run_style": ApplyBoundary.NEXT_TICK,
    "runtime.idle_sleep_seconds": ApplyBoundary.NEXT_TICK,
    "runners.default_runner": ApplyBoundary.NEXT_TICK,
    "runners.codex": ApplyBoundary.NEXT_TICK,
    "recovery.max_fix_cycles": ApplyBoundary.NEXT_TICK,
    "recovery.max_troubleshoot_attempts_before_consult": ApplyBoundary.NEXT_TICK,
    "recovery.max_mechanic_attempts": ApplyBoundary.NEXT_TICK,
    "recovery.stale_state_recovery_enabled": ApplyBoundary.NEXT_TICK,
    "watchers.enabled": ApplyBoundary.NEXT_TICK,
    "watchers.debounce_ms": ApplyBoundary.NEXT_TICK,
    "watchers.watch_ideas_inbox": ApplyBoundary.NEXT_TICK,
    "watchers.watch_specs_queue": ApplyBoundary.NEXT_TICK,
}

_STAGE_FIELD_BOUNDARIES: dict[str, ApplyBoundary] = {
    "runner": ApplyBoundary.RECOMPILE,
    "model": ApplyBoundary.RECOMPILE,
    "timeout_seconds": ApplyBoundary.RECOMPILE,
}

_SECTIONS: tuple[tuple[str, type[ConfigModel]], ...] = (
    ("runtime", RuntimeSection),
    ("runners", RunnersSection),
    ("recovery", RecoverySection),
    ("watchers", WatchersSection),
)

_MISSING = object()


@dataclass(frozen=True)
class ConfigChangeSummary:
    changed_keys: tuple[str, ...]
    boundary_by_key: dict[str, ApplyBoundary]
    highest_boundary: ApplyBoundary | None
    requires_recompile: bool
    recompile_keys: tuple[str, ...]


def load_runtime_config(
    config_path: Path | str | None = None,
    *,
    mailbox_overrides: Mapping[str, Any] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> RuntimeConfig:
    path = DEFAULT_CONFIG_PATH if config_path is None else Path(config_path)
    payload = RuntimeConfig().model_dump(mode="python")

    if path.is_file():
        payload = _deep_merge_dicts(payload, _read_toml_config(path))

    if mailbox_overrides:
        _apply_overrides(payload, mailbox_overrides)

    if cli_overrides:
        _apply_overrides(payload, cli_overrides)

    return RuntimeConfig.model_validate(payload)


def _read_toml_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        parsed = tomllib.load(fh)
    if not isinstance(parsed, dict):
        raise ValueError(f"Config file must parse into a table: {path}")
    return parsed


def _deep_merge_dicts(base: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in incoming.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge_dicts(existing, value)
        else:
            merged[key] = value
    return merged


def _apply_overrides(target: MutableMapping[str, Any], overrides: Mapping[str, Any]) -> None:
    for key, value in overrides.items():
        if "." in key:
            _set_dotted_value(target, key, value)
            continue

        existing = target.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            target[key] = _deep_merge_dicts(existing, value)
            continue

        target[key] = value


def _set_dotted_value(target: MutableMapping[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    if any(part == "" for part in parts):
        raise ValueError(f"Invalid dotted config key: {dotted_key}")

    cursor: MutableMapping[str, Any] = target
    for part in parts[:-1]:
        next_value = cursor.get(part)
        if not isinstance(next_value, MutableMapping):
            next_value = {}
            cursor[part] = next_value
        cursor = next_value
    cursor[parts[-1]] = value


def iter_config_field_paths(config: RuntimeConfig) -> tuple[str, ...]:
    keys: list[str] = []
    for section_name, model_cls in _SECTIONS:
        keys.extend(f"{section_name}.{field_name}" for field_name in model_cls.model_fields)

    for stage_name in sorted(config.stages):
        keys.extend(f"stages.{stage_name}.{field_name}" for field_name in StageConfig.model_fields)

    return tuple(keys)


def apply_boundary_for_field(field_path: str) -> ApplyBoundary:
    direct_match = _FIELD_BOUNDARIES.get(field_path)
    if direct_match is not None:
        return direct_match

    if field_path.startswith("stages."):
        parts = field_path.split(".")
        if len(parts) == 3:
            if parts[1] not in KNOWN_STAGE_NAMES:
                raise KeyError(f"Unknown stage name in config field: {parts[1]}")
            stage_field = _STAGE_FIELD_BOUNDARIES.get(parts[2])
            if stage_field is not None:
                return stage_field

    raise KeyError(f"No apply boundary declared for config field: {field_path}")


def summarize_config_changes(
    current: RuntimeConfig,
    candidate: RuntimeConfig,
) -> ConfigChangeSummary:
    current_flat = _flatten_config(current)
    candidate_flat = _flatten_config(candidate)

    changed_keys = tuple(
        key
        for key in sorted(set(current_flat) | set(candidate_flat))
        if current_flat.get(key, _MISSING) != candidate_flat.get(key, _MISSING)
    )

    boundary_by_key: dict[str, ApplyBoundary] = {}
    highest_boundary: ApplyBoundary | None = None
    recompile_keys: list[str] = []

    for key in changed_keys:
        boundary = apply_boundary_for_field(key)
        boundary_by_key[key] = boundary
        if boundary is ApplyBoundary.RECOMPILE:
            recompile_keys.append(key)
        if highest_boundary is None:
            highest_boundary = boundary
        elif _BOUNDARY_PRIORITY[boundary] > _BOUNDARY_PRIORITY[highest_boundary]:
            highest_boundary = boundary

    return ConfigChangeSummary(
        changed_keys=changed_keys,
        boundary_by_key=boundary_by_key,
        highest_boundary=highest_boundary,
        requires_recompile=bool(recompile_keys),
        recompile_keys=tuple(recompile_keys),
    )


def recompile_boundary_changes(current: RuntimeConfig, candidate: RuntimeConfig) -> tuple[str, ...]:
    return summarize_config_changes(current, candidate).recompile_keys


def render_bootstrap_runtime_config() -> str:
    return "\n".join(
        [
            "[runtime]",
            'default_mode = "standard_plain"',
            'run_style = "daemon"',
            "",
        ]
    )


def fingerprint_runtime_config(config: RuntimeConfig) -> str:
    payload = config.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    return f"cfg-{digest}"


def _flatten_config(config: RuntimeConfig) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    for section_name, model_cls in _SECTIONS:
        section = getattr(config, section_name)
        for field_name in model_cls.model_fields:
            flattened[f"{section_name}.{field_name}"] = getattr(section, field_name)

    for stage_name in sorted(config.stages):
        stage_config = config.stages[stage_name]
        for field_name in StageConfig.model_fields:
            flattened[f"stages.{stage_name}.{field_name}"] = getattr(stage_config, field_name)

    return flattened


__all__ = [
    "ApplyBoundary",
    "CodexRunnerSection",
    "ConfigChangeSummary",
    "DEFAULT_CONFIG_PATH",
    "KNOWN_STAGE_NAMES",
    "RecoverySection",
    "RunnersSection",
    "RuntimeConfig",
    "RuntimeSection",
    "StageConfig",
    "WatchersSection",
    "apply_boundary_for_field",
    "fingerprint_runtime_config",
    "iter_config_field_paths",
    "load_runtime_config",
    "render_bootstrap_runtime_config",
    "recompile_boundary_changes",
    "summarize_config_changes",
]
