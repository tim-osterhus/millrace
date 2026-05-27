"""Artifact loading helpers for runtime-effect operation runners."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from millrace_ai.errors import QueueStateError
from millrace_ai.runtime.artifact_contracts import (
    parse_resolved_run_artifact_as,
    resolve_run_artifact,
)

from .types import ModelT, OperationErrorFactory

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan


def read_json_payload(
    path: Path,
    *,
    missing_class: str,
    parse_class: str,
    error_factory: OperationErrorFactory,
    missing_message: str | None = None,
    read_error_message_prefix: str | None = None,
) -> object:
    if not path.exists():
        raise error_factory(
            missing_class,
            missing_message or f"required artifact is missing: {path.name}",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        prefix = read_error_message_prefix or "required artifact could not be read"
        raise error_factory(
            missing_class,
            f"{prefix}: {path.name}: {exc}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise error_factory(
            parse_class,
            f"{path.name} is not valid JSON: {exc}",
        ) from exc


def read_json_model_payload(
    path: Path,
    model: type[ModelT],
    *,
    missing_class: str,
    parse_class: str,
    schema_class: str,
    error_factory: OperationErrorFactory,
    missing_message: str | None = None,
    read_error_message_prefix: str | None = None,
) -> ModelT:
    payload = read_json_payload(
        path,
        missing_class=missing_class,
        parse_class=parse_class,
        error_factory=error_factory,
        missing_message=missing_message,
        read_error_message_prefix=read_error_message_prefix,
    )
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise error_factory(
            schema_class,
            f"{path.name} failed schema validation: {exc}",
        ) from exc


def read_json_model_list_payload(
    path: Path,
    model: type[ModelT],
    *,
    missing_class: str,
    parse_class: str,
    schema_class: str,
    error_factory: OperationErrorFactory,
    missing_message: str | None = None,
    read_error_message_prefix: str | None = None,
) -> tuple[ModelT, ...]:
    payload = read_json_payload(
        path,
        missing_class=missing_class,
        parse_class=parse_class,
        error_factory=error_factory,
        missing_message=missing_message,
        read_error_message_prefix=read_error_message_prefix,
    )
    if not isinstance(payload, list):
        raise error_factory(schema_class, f"{path.name} must be a JSON list")
    try:
        return tuple(model.model_validate(item) for item in payload)
    except ValidationError as exc:
        raise error_factory(
            schema_class,
            f"{path.name} failed schema validation: {exc}",
        ) from exc


def read_json_model(
    path: Path,
    model: type[ModelT],
    *,
    missing_message: str | None = None,
) -> ModelT:
    if not path.exists():
        raise QueueStateError(
            missing_message or f"required artifact is missing: {path.name}"
        )
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def parse_required_run_artifact_as(
    compiled_plan: CompiledRunPlan | None,
    artifact_id: str,
    run_dir: Path,
    model: type[ModelT],
) -> ModelT:
    return parse_resolved_run_artifact_as(
        resolve_run_artifact(compiled_plan, artifact_id, run_dir),
        model,
    )


def read_required_run_artifact_text(
    compiled_plan: CompiledRunPlan | None,
    artifact_id: str,
    run_dir: Path,
) -> str:
    resolved = resolve_run_artifact(compiled_plan, artifact_id, run_dir)
    return resolved.path.read_text(encoding="utf-8")


__all__ = [
    "parse_required_run_artifact_as",
    "read_json_model",
    "read_json_model_list_payload",
    "read_json_model_payload",
    "read_json_payload",
    "read_required_run_artifact_text",
]
