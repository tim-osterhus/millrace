"""Configuration loading, merging, and fingerprint helpers."""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from .models import DEFAULT_CONFIG_PATH, RuntimeConfig


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


def render_bootstrap_runtime_config() -> str:
    return "\n".join(
        [
            "[runtime]",
            'default_mode = "standard_plain"',
            'run_style = "daemon"',
            "",
            "[runners.codex]",
            'permission_default = "maximum"',
            "",
        ]
    )


def fingerprint_runtime_config(config: RuntimeConfig) -> str:
    payload = config.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    return f"cfg-{digest}"


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


__all__ = [
    "fingerprint_runtime_config",
    "load_runtime_config",
    "render_bootstrap_runtime_config",
]
