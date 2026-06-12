"""Shared helpers for Blueprint runtime-effect operation runners."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import JsonValue

from millrace_ai.contracts import StageResultEnvelope, WorkItemKind
from millrace_ai.errors import QueueStateError
from millrace_ai.runtime.effects.operation_runners.artifacts import read_json_model
from millrace_ai.runtime.effects.operation_runners.idempotency import (
    normalized_markdown_content,
    normalized_model_payload,
)
from millrace_ai.runtime.effects.operation_runners.results import (
    append_lifecycle_journal,
    runtime_mutation_journal,
)
from millrace_ai.runtime.effects.operation_runners.stores import copy_unique_file, effect_path
from millrace_ai.runtime.effects.operation_runners.types import ModelT
from millrace_ai.runtime.effects.operation_runners.work_items import stage_result_work_item_kind
from millrace_ai.workspace.paths import WorkspacePaths


def _append_lifecycle_journal(
    mutation_journal: Sequence[dict[str, JsonValue]],
    lifecycle_entry: dict[str, JsonValue] | None,
) -> tuple[dict[str, JsonValue], ...]:
    return append_lifecycle_journal(mutation_journal, lifecycle_entry)


def _runtime_mutation_journal(
    entries: Sequence[dict[str, JsonValue]],
) -> tuple[dict[str, JsonValue], ...]:
    return runtime_mutation_journal(entries)


def _stage_result_work_item_kind(stage_result: StageResultEnvelope) -> WorkItemKind:
    return stage_result_work_item_kind(stage_result, context="Blueprint runtime effect")


def _effect_path(paths: WorkspacePaths, path: Path) -> str:
    return effect_path(paths, path)


def _normalized_blueprint_model_payload(document: ModelT) -> str:
    return normalized_model_payload(document)


def _normalized_markdown_content(content: str) -> str:
    return normalized_markdown_content(content)


def _copy_unique_file(source: Path, destination: Path) -> None:
    try:
        copy_unique_file(
            source,
            destination,
            exists_message=f"Blueprint artifact already exists: {destination}",
        )
    except FileExistsError as exc:
        raise QueueStateError(str(exc)) from exc


def _read_json_model(path: Path, model: type[ModelT]) -> ModelT:
    return read_json_model(
        path,
        model,
        missing_message=f"required Blueprint artifact is missing: {path.name}",
    )

__all__ = [
    "_append_lifecycle_journal",
    "_copy_unique_file",
    "_effect_path",
    "_normalized_blueprint_model_payload",
    "_normalized_markdown_content",
    "_read_json_model",
    "_runtime_mutation_journal",
    "_stage_result_work_item_kind",
]
