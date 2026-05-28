"""Compiler-owned model alias assignment resolution."""

from __future__ import annotations

import re
from typing import NamedTuple

from millrace_ai.config import ModelAliasDefinition, RuntimeConfig
from millrace_ai.contracts import ModeDefinition, ModeModelAliasDefinition, StageMapKey

MODEL_ALIAS_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._:/+-]+$")
MODEL_ALIAS_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

_BUILTIN_STANDARD_ALIAS = ModelAliasDefinition(model="gpt-5.5", thinking_level="medium")


class ModelAliasDecision(NamedTuple):
    model_name: str | None
    thinking_level: str | None
    alias_id: str | None
    source: str | None
    warnings: tuple[str, ...]


def resolve_model_alias_assignment(
    *,
    config: RuntimeConfig,
    mode: ModeDefinition,
    stage_key: StageMapKey,
    loop_id: str,
    existing_model_name: str | None,
    existing_thinking_level: str | None,
) -> ModelAliasDecision:
    """Overlay model/thinking assignment aliases after existing node materialization."""

    if not config.model_assignment.enabled:
        return ModelAliasDecision(
            model_name=existing_model_name,
            thinking_level=existing_thinking_level,
            alias_id=None,
            source=None,
            warnings=(),
        )

    warnings: list[str] = []
    candidates = _assignment_candidates(config, mode, stage_key=stage_key, loop_id=loop_id)
    for alias_id, source in candidates:
        alias = _alias_definition(config, mode, alias_id, source=source)
        if alias is None:
            fallback_alias = _fallback_alias_definition(alias_id)
            if fallback_alias is None:
                warnings.append(
                    f"model assignment {source} references unknown alias {alias_id!r}; falling back"
                )
                continue
            alias = fallback_alias
        validation_error = _alias_validation_error(alias_id, alias)
        if validation_error is not None:
            fallback_alias = _fallback_alias_definition(alias_id)
            if fallback_alias is None:
                warnings.append(
                    "model assignment "
                    f"{source} selected invalid alias {alias_id!r}: {validation_error}; falling back"
                )
                continue
            warnings.append(
                "model assignment "
                f"{source} selected invalid alias {alias_id!r}: {validation_error}; "
                "using built-in standard alias"
            )
            alias = fallback_alias
        model = alias.model.strip() if alias.model is not None else None
        thinking_level = alias.thinking_level.strip() if alias.thinking_level is not None else None
        return ModelAliasDecision(
            model_name=model,
            thinking_level=thinking_level,
            alias_id=alias_id,
            source=source,
            warnings=tuple(warnings),
        )

    return ModelAliasDecision(
        model_name=existing_model_name,
        thinking_level=existing_thinking_level,
        alias_id=None,
        source=None,
        warnings=tuple(warnings),
    )


def _assignment_candidates(
    config: RuntimeConfig,
    mode: ModeDefinition,
    *,
    stage_key: StageMapKey,
    loop_id: str,
) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[str, str]] = []
    stage_lookup_key = _stage_key_value(stage_key)
    stage_alias = config.model_assignment.by_stage.get(stage_lookup_key)
    if stage_alias is not None:
        candidates.append((stage_alias, f"stage:{stage_lookup_key}"))
    loop_alias = config.model_assignment.by_loop.get(loop_id)
    if loop_alias is not None:
        candidates.append((loop_alias, f"loop:{loop_id}"))
    mode_stage_alias = mode.model_assignment.by_stage.get(stage_key)
    if mode_stage_alias is not None:
        candidates.append((mode_stage_alias, f"mode:stage:{stage_lookup_key}"))
    mode_loop_alias = mode.model_assignment.by_loop.get(loop_id)
    if mode_loop_alias is not None:
        candidates.append((mode_loop_alias, f"mode:loop:{loop_id}"))
    if mode.model_assignment.default_alias is not None:
        candidates.append((mode.model_assignment.default_alias, "mode:default"))
    candidates.append((config.model_assignment.default_alias, "default"))
    candidates.append(("standard", "builtin:standard"))
    return tuple(candidates)


def _alias_definition(
    config: RuntimeConfig,
    mode: ModeDefinition,
    alias_id: str,
    *,
    source: str,
) -> ModelAliasDefinition | ModeModelAliasDefinition | None:
    if source.startswith("mode:"):
        return mode.model_aliases.get(alias_id) or config.model_aliases.get(alias_id)
    return config.model_aliases.get(alias_id) or mode.model_aliases.get(alias_id)


def _fallback_alias_definition(alias_id: str) -> ModelAliasDefinition | None:
    if alias_id == "standard":
        return _BUILTIN_STANDARD_ALIAS
    return None


def _alias_validation_error(
    alias_id: str,
    alias: ModelAliasDefinition | ModeModelAliasDefinition,
) -> str | None:
    if not MODEL_ALIAS_ID_PATTERN.fullmatch(alias_id):
        return "alias id is not a safe identifier"
    model = alias.model.strip() if alias.model is not None else ""
    thinking_level = alias.thinking_level.strip() if alias.thinking_level is not None else ""
    if not model or not MODEL_ALIAS_VALUE_PATTERN.fullmatch(model):
        return "model is empty or contains unsupported characters"
    if not thinking_level or not MODEL_ALIAS_VALUE_PATTERN.fullmatch(thinking_level):
        return "thinking_level is empty or contains unsupported characters"
    return None


def _stage_key_value(stage_key: StageMapKey) -> str:
    value = getattr(stage_key, "value", stage_key)
    return str(value)


__all__ = [
    "MODEL_ALIAS_VALUE_PATTERN",
    "ModelAliasDecision",
    "resolve_model_alias_assignment",
]
