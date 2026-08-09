"""Kernel-side projection evaluation for compiled terminal actions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    UnsupportedAuthorityValueError,
    freeze_authority_value,
)
from millrace.contracts.schema import (
    ProjectionEvaluationError,
    ProjectionEvaluationResult,
    validate_projection_declaration,
)
from millrace.contracts.state import RunRecord, WorkItem
from millrace.contracts.transition import artifact_payload_digest


@dataclass(frozen=True, slots=True)
class ProjectionContext:
    work_item_payload: Mapping[str, object]
    artifact_payload: Mapping[str, object]
    observation_payload: Mapping[str, object]
    run_metadata: Mapping[str, object]
    plan_metadata: Mapping[str, object]


def evaluate_projection(
    projection: object,
    context: ProjectionContext,
) -> ProjectionEvaluationResult:
    declaration = validate_projection_declaration(projection)
    if not declaration.accepted:
        issue = declaration.issues[0]
        return ProjectionEvaluationResult(
            accepted=False,
            error=ProjectionEvaluationError(issue.path, issue.reason, issue.detail),
        )
    try:
        value = _evaluate(projection, context, ())
    except _ProjectionRejected as exc:
        return ProjectionEvaluationResult(
            accepted=False,
            error=ProjectionEvaluationError(exc.path, exc.reason, exc.detail),
        )
    return ProjectionEvaluationResult(accepted=True, value=value)


def projection_context_for_run(
    *,
    work_item: WorkItem,
    run: RunRecord,
    observation_payload: Mapping[str, object],
    artifact_payload: Mapping[str, object],
) -> ProjectionContext:
    """Build the canonical runtime context for a compiled payload projection."""

    return ProjectionContext(
        work_item_payload=work_item.payload,
        artifact_payload=artifact_payload,
        observation_payload=observation_payload,
        run_metadata={
            "run_id": run.run_ref.run_id,
            "work_item_id": run.work_item_id,
            "activation_id": run.activation_id,
            "claim_id": run.run_ref.claim_id,
            "generation": run.run_ref.generation,
            "fencing_token": run.run_ref.fencing_token,
            "work_item_payload_digest": artifact_payload_digest(work_item.payload),
            "artifact_payload_digest": artifact_payload_digest(artifact_payload),
        },
        plan_metadata={
            "plan_id": run.run_ref.plan_ref.plan_id,
            "authority_fingerprint": run.run_ref.plan_ref.authority_fingerprint,
        },
    )


def _evaluate(
    projection: object,
    context: ProjectionContext,
    path: tuple[str, ...],
) -> AuthorityValue:
    if not isinstance(projection, Mapping):
        raise _ProjectionRejected(path, "unsupported_projection")

    kind = projection.get("kind")
    if kind == "literal":
        return _freeze_projection_value(projection.get("value"), path)
    if kind == "source":
        return _read_source(projection.get("path"), context, path)
    if kind == "object":
        return _evaluate_object(projection.get("fields"), context, path)
    if kind == "array":
        return _evaluate_array(projection.get("items"), context, path)
    if kind == "coalesce":
        return _evaluate_coalesce(
            projection.get("candidates"),
            projection.get("default"),
            context,
            path,
        )
    raise _ProjectionRejected(path, "unsupported_projection")


def _evaluate_coalesce(
    candidates: object,
    default: object,
    context: ProjectionContext,
    path: tuple[str, ...],
) -> AuthorityValue:
    if not _is_sequence(candidates) or not candidates:
        raise _ProjectionRejected((*path, "candidates"), "coalesce_candidates_empty")
    for index, candidate in enumerate(candidates):
        try:
            value = _evaluate(
                candidate,
                context,
                (*path, "candidates", str(index)),
            )
        except _ProjectionRejected as exc:
            if exc.reason == "missing_source":
                continue
            raise
        if value is not None:
            return value
    return _evaluate(default, context, (*path, "default"))


def _evaluate_object(
    fields: object,
    context: ProjectionContext,
    path: tuple[str, ...],
) -> AuthorityValue:
    if not isinstance(fields, Mapping):
        raise _ProjectionRejected(path, "unsupported_projection")
    values: dict[str, AuthorityValue] = {}
    for field_name, field_projection in fields.items():
        if not isinstance(field_name, str):
            raise _ProjectionRejected(path, "unsupported_projection")
        values[field_name] = _evaluate(field_projection, context, (*path, field_name))
    return freeze_authority_value(values)


def _evaluate_array(
    items: object,
    context: ProjectionContext,
    path: tuple[str, ...],
) -> AuthorityValue:
    if not _is_sequence(items):
        raise _ProjectionRejected(path, "unsupported_projection")
    values = tuple(
        _evaluate(item, context, (*path, str(index)))
        for index, item in enumerate(items)
    )
    return values


def _read_source(
    raw_path: object,
    context: ProjectionContext,
    path: tuple[str, ...],
) -> AuthorityValue:
    source_path = _path_parts(raw_path)
    if source_path is None:
        raise _ProjectionRejected(path, "unsupported_projection_path")
    root_name = source_path[0]
    roots: Mapping[str, Mapping[str, object]] = {
        "work_item_payload": context.work_item_payload,
        "artifact_payload": context.artifact_payload,
        "observation_payload": context.observation_payload,
        "run_metadata": context.run_metadata,
        "plan_metadata": context.plan_metadata,
    }
    current: object | None = roots.get(root_name)
    if current is None:
        raise _ProjectionRejected(path, "unknown_source_root", root_name)

    for field_name in source_path[1:]:
        if not isinstance(current, Mapping) or field_name not in current:
            raise _ProjectionRejected(path, "missing_source", ".".join(source_path))
        current = current[field_name]

    return _freeze_projection_value(current, path)


def _freeze_projection_value(value: object, path: tuple[str, ...]) -> AuthorityValue:
    try:
        return freeze_authority_value(value)
    except UnsupportedAuthorityValueError as exc:
        raise _ProjectionRejected(
            path,
            "unsupported_projection_value",
            type(value).__name__,
        ) from exc


def _path_parts(value: object) -> tuple[str, ...] | None:
    if not _is_sequence(value):
        return None
    source_path: list[str] = []
    for part in value:
        if not isinstance(part, str):
            return None
        source_path.append(part)
    if not source_path:
        return None
    return tuple(source_path)


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


@dataclass(frozen=True, slots=True)
class _ProjectionRejected(Exception):
    path: tuple[str, ...]
    reason: str
    detail: str | None = None


__all__ = ("ProjectionContext", "evaluate_projection", "projection_context_for_run")
