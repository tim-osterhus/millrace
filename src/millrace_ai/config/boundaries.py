"""Configuration change classification and apply-boundary helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .models import (
    KNOWN_STAGE_NAMES,
    ConfigModel,
    RecoverySection,
    RunnersSection,
    RuntimeConfig,
    RuntimeSection,
    StageConfig,
    UsageGovernanceSection,
    WatchersSection,
)


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

_FIELD_BOUNDARIES: dict[str, ApplyBoundary] = {
    "runtime.default_mode": ApplyBoundary.RECOMPILE,
    "runtime.run_style": ApplyBoundary.NEXT_TICK,
    "runtime.idle_sleep_seconds": ApplyBoundary.NEXT_TICK,
    "runners.default_runner": ApplyBoundary.NEXT_TICK,
    "runners.codex": ApplyBoundary.NEXT_TICK,
    "runners.pi": ApplyBoundary.NEXT_TICK,
    "recovery.max_fix_cycles": ApplyBoundary.NEXT_TICK,
    "recovery.max_troubleshoot_attempts_before_consult": ApplyBoundary.NEXT_TICK,
    "recovery.max_mechanic_attempts": ApplyBoundary.NEXT_TICK,
    "recovery.stale_state_recovery_enabled": ApplyBoundary.NEXT_TICK,
    "watchers.enabled": ApplyBoundary.NEXT_TICK,
    "watchers.debounce_ms": ApplyBoundary.NEXT_TICK,
    "watchers.watch_ideas_inbox": ApplyBoundary.NEXT_TICK,
    "watchers.watch_specs_queue": ApplyBoundary.NEXT_TICK,
    "usage_governance.enabled": ApplyBoundary.NEXT_TICK,
    "usage_governance.auto_resume": ApplyBoundary.NEXT_TICK,
    "usage_governance.evaluation_boundary": ApplyBoundary.NEXT_TICK,
    "usage_governance.calendar_timezone": ApplyBoundary.NEXT_TICK,
    "usage_governance.runtime_token_rules": ApplyBoundary.NEXT_TICK,
    "usage_governance.subscription_quota_rules": ApplyBoundary.NEXT_TICK,
}

_STAGE_FIELD_BOUNDARIES: dict[str, ApplyBoundary] = {
    "runner": ApplyBoundary.RECOMPILE,
    "model": ApplyBoundary.RECOMPILE,
    "model_reasoning_effort": ApplyBoundary.RECOMPILE,
    "timeout_seconds": ApplyBoundary.RECOMPILE,
}

_SECTIONS: tuple[tuple[str, type[ConfigModel]], ...] = (
    ("runtime", RuntimeSection),
    ("runners", RunnersSection),
    ("recovery", RecoverySection),
    ("watchers", WatchersSection),
    ("usage_governance", UsageGovernanceSection),
)

_MISSING = object()


@dataclass(frozen=True)
class ConfigChangeSummary:
    changed_keys: tuple[str, ...]
    boundary_by_key: dict[str, ApplyBoundary]
    highest_boundary: ApplyBoundary | None
    requires_recompile: bool
    recompile_keys: tuple[str, ...]


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
    "ConfigChangeSummary",
    "apply_boundary_for_field",
    "iter_config_field_paths",
    "recompile_boundary_changes",
    "summarize_config_changes",
]
