"""Result construction helpers for runtime-effect operation runners."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from millrace_ai.contracts import StageResultEnvelope

from ..models import (
    RuntimeEffectDecision,
    RuntimeEffectMutationPhase,
    RuntimeEffectResult,
    SourceLifecycleIntent,
)


def runtime_mutation_journal(
    entries: Sequence[dict[str, JsonValue]],
) -> tuple[dict[str, JsonValue], ...]:
    return tuple(dict(entry) for entry in entries)


def append_lifecycle_journal(
    mutation_journal: Sequence[dict[str, JsonValue]],
    lifecycle_entry: dict[str, JsonValue] | None,
) -> tuple[dict[str, JsonValue], ...]:
    entries = list(mutation_journal)
    if lifecycle_entry is not None:
        entries.append(lifecycle_entry)
    return runtime_mutation_journal(entries)


def mutation_phase_for_created_paths(
    created_paths: Sequence[str],
) -> RuntimeEffectMutationPhase:
    if created_paths:
        return RuntimeEffectMutationPhase.PARTIAL_MUTATION
    return RuntimeEffectMutationPhase.PRE_MUTATION


def block_source_failure_result(
    operation_id: str,
    _stage_result: StageResultEnvelope,
    *,
    failure_class: str,
    message: str,
    created_paths: Sequence[str],
    mutation_journal: Sequence[dict[str, JsonValue]] = (),
    _context: str = "runtime effect",
) -> RuntimeEffectResult:
    return RuntimeEffectResult(
        handler_id=operation_id,
        decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
        created_paths=tuple(created_paths),
        source_lifecycle_intent=None,
        failure_class=failure_class,
        message=message,
        mutation_phase=mutation_phase_for_created_paths(created_paths),
        mutation_journal=runtime_mutation_journal(mutation_journal),
    )


def complete_source_success_result(
    operation_id: str,
    *,
    created_paths: Sequence[str],
    source_lifecycle_intent: SourceLifecycleIntent | None,
    message: str,
    mutation_journal: Sequence[dict[str, JsonValue]] = (),
) -> RuntimeEffectResult:
    return RuntimeEffectResult(
        handler_id=operation_id,
        decision=RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE,
        created_paths=tuple(created_paths),
        source_lifecycle_intent=source_lifecycle_intent,
        message=message,
        mutation_journal=runtime_mutation_journal(mutation_journal),
    )


__all__ = [
    "append_lifecycle_journal",
    "block_source_failure_result",
    "complete_source_success_result",
    "mutation_phase_for_created_paths",
    "runtime_mutation_journal",
]
