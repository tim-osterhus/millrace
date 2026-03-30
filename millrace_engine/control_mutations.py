"""Control-plane config and queue mutation helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from shutil import copy2
from typing import Any

import tomlkit
from pydantic import ValidationError
from tomlkit.exceptions import ParseError as TomlParseError

from .config import ConfigApplyBoundary, EngineConfig, LoadedConfig, diff_config_fields
from .contracts import TaskCard
from .markdown import TaskStoreDocument, parse_task_store, render_task_store, write_text_atomic
from .paths import RuntimePaths
from .control_common import ControlError, load_control_config, single_line_message, validation_error_message


def _resolve_mapping_key(mapping: dict[Any, Any], token: str) -> Any:
    for key in mapping:
        if isinstance(key, Enum) and key.value == token:
            return key
        if str(key) == token:
            return key
    raise ControlError(f"unknown config key segment: {token}")


def _resolve_attr(target: Any, token: str) -> Any:
    if isinstance(target, dict):
        return target[_resolve_mapping_key(target, token)]
    if not hasattr(target, token):
        raise ControlError(f"unknown config key segment: {token}")
    return getattr(target, token)


def _resolve_value(target: Any, key: str) -> Any:
    current = target
    for token in key.split("."):
        current = _resolve_attr(current, token)
    return current


def _assign_config_value(config: EngineConfig, key: str, raw_value: str) -> EngineConfig:
    tokens = key.split(".")
    current: Any = config
    for token in tokens[:-1]:
        current = _resolve_attr(current, token)
    final_token = tokens[-1]
    if isinstance(current, dict):
        current[_resolve_mapping_key(current, final_token)] = raw_value
    else:
        if not hasattr(current, final_token):
            raise ControlError(f"unknown config key: {key}")
        setattr(current, final_token, raw_value)
    return config


def _toml_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, tuple):
        return [_toml_ready(item) for item in value]
    if isinstance(value, list):
        return [_toml_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _toml_ready(item) for key, item in value.items()}
    return value


def _set_toml_value(document: Any, tokens: list[str], value: Any) -> None:
    node = document
    for token in tokens[:-1]:
        if token not in node or not hasattr(node[token], "items"):
            node[token] = tomlkit.table()
        node = node[token]
    node[tokens[-1]] = _toml_ready(value)


def _assert_reload_safe(current: LoadedConfig, reloaded: LoadedConfig) -> None:
    for field_name in diff_config_fields(current.config, reloaded.config):
        if current.config.boundaries.classify_field(field_name) is ConfigApplyBoundary.STARTUP_ONLY:
            raise ControlError(f"reload would change startup-only field: {field_name}")


def apply_native_config_value(
    config_path: Path,
    loaded: LoadedConfig,
    key: str,
    raw_value: str,
    *,
    reject_startup_only: bool,
) -> LoadedConfig:
    """Apply one dotted-key config mutation to native TOML."""

    if loaded.source.kind != "native_toml":
        raise ControlError("config set only supports native TOML")

    if reject_startup_only and key in set(loaded.config.boundaries.startup_only_fields):
        raise ControlError(f"cannot change startup-only field at runtime: {key}")

    updated_config = loaded.config.model_copy(deep=True)
    try:
        _assign_config_value(updated_config, key, raw_value)
    except ValidationError as exc:
        raise ControlError(f"config value for {key} is invalid: {validation_error_message(exc)}") from exc

    if config_path.exists():
        try:
            document = tomlkit.parse(config_path.read_text(encoding="utf-8"))
        except TomlParseError as exc:
            raise ControlError(f"config TOML is invalid: {single_line_message(exc)}") from exc
    else:
        document = tomlkit.document()
    _set_toml_value(document, key.split("."), _resolve_value(updated_config, key))
    write_text_atomic(config_path, tomlkit.dumps(document))
    reloaded = load_control_config(config_path)
    if reject_startup_only:
        _assert_reload_safe(loaded, reloaded)
    return reloaded


def append_task_to_backlog(
    paths: RuntimePaths,
    *,
    title: str,
    body: str | None = None,
    spec_id: str | None = None,
) -> TaskCard:
    """Append one new task card to the backlog."""

    task_date = date.today().isoformat()
    body_lines: list[str] = []
    if spec_id:
        body_lines.append(f"- **Spec-ID:** {spec_id}")
    if body:
        body_lines.append(body.strip())
    card = TaskCard.model_validate(
        {
            "heading": f"## {task_date} - {title.strip()}",
            "body": "\n\n".join(line for line in body_lines if line).rstrip("\n"),
        }
    )
    existing = parse_task_store(paths.backlog_file.read_text(encoding="utf-8"))
    updated = TaskStoreDocument(preamble=existing.preamble, cards=[*existing.cards, card])
    write_text_atomic(paths.backlog_file, render_task_store(updated))
    return card


def copy_idea_into_raw_queue(paths: RuntimePaths, source_file: Path) -> Path:
    """Copy one idea file into `agents/ideas/raw/` with a stable timestamp prefix."""

    if not source_file.exists():
        raise FileNotFoundError(source_file)
    raw_dir = paths.agents_dir / "ideas" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = raw_dir / f"{timestamp}__{source_file.name}"
    copy2(source_file, destination)
    return destination
