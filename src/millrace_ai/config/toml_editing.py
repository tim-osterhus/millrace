"""Comment-preserving TOML mutations for runtime config files."""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import tomlkit


def set_model_alias(
    path: Path,
    *,
    alias_id: str,
    model: str,
    thinking_level: str,
) -> None:
    document = _read_document(path)
    aliases = _table(document, "model_aliases")
    alias = _table(aliases, alias_id)
    alias["model"] = model
    alias["thinking_level"] = thinking_level
    _write_document(path, document)


def remove_model_alias(path: Path, *, alias_id: str) -> None:
    document = _read_document(path)
    aliases = document.get("model_aliases")
    if isinstance(aliases, MutableMapping):
        aliases.pop(alias_id, None)
    _write_document(path, document)


def set_model_assignment_default(path: Path, *, alias_id: str) -> None:
    document = _read_document(path)
    assignment = _table(document, "model_assignment")
    assignment["enabled"] = True
    assignment["default_alias"] = alias_id
    assignment["invalid_assignment_policy"] = "warn_fallback"
    _write_document(path, document)


def clear_model_assignment_default(path: Path) -> None:
    set_model_assignment_default(path, alias_id="standard")


def set_model_assignment_loop(path: Path, *, loop_id: str, alias_id: str) -> None:
    document = _read_document(path)
    by_loop = _table(_table(document, "model_assignment"), "by_loop")
    by_loop[loop_id] = alias_id
    _write_document(path, document)


def clear_model_assignment_loop(path: Path, *, loop_id: str) -> None:
    document = _read_document(path)
    assignment = document.get("model_assignment")
    if isinstance(assignment, MutableMapping):
        by_loop = assignment.get("by_loop")
        if isinstance(by_loop, MutableMapping):
            by_loop.pop(loop_id, None)
    _write_document(path, document)


def set_model_assignment_stage(path: Path, *, stage: str, alias_id: str) -> None:
    document = _read_document(path)
    by_stage = _table(_table(document, "model_assignment"), "by_stage")
    by_stage[stage] = alias_id
    _write_document(path, document)


def clear_model_assignment_stage(path: Path, *, stage: str) -> None:
    document = _read_document(path)
    assignment = document.get("model_assignment")
    if isinstance(assignment, MutableMapping):
        by_stage = assignment.get("by_stage")
        if isinstance(by_stage, MutableMapping):
            by_stage.pop(stage, None)
    _write_document(path, document)


def _read_document(path: Path) -> MutableMapping[str, Any]:
    if path.is_file():
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    return tomlkit.document()


def _table(parent: MutableMapping[str, Any], key: str) -> MutableMapping[str, Any]:
    value = parent.get(key)
    if isinstance(value, MutableMapping):
        return value
    table = tomlkit.table()
    parent[key] = table
    return table


def _write_document(path: Path, document: MutableMapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(tomlkit.dumps(document), encoding="utf-8")
    temporary_path.replace(path)


__all__ = [
    "clear_model_assignment_default",
    "clear_model_assignment_loop",
    "clear_model_assignment_stage",
    "remove_model_alias",
    "set_model_alias",
    "set_model_assignment_default",
    "set_model_assignment_loop",
    "set_model_assignment_stage",
]
