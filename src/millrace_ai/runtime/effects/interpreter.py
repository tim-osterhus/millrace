"""Runtime effect operation step interpreter.

Walks compiled operation steps, resolves bindings, dispatches to the
primitive executor registry, and returns a RuntimeEffectResult.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import JsonValue

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.workspace.paths import WorkspacePaths

from .journal import (
    completed_hashes_by_step,
    compute_idempotency_hash,
    has_started_record,
    write_completed_record,
    write_failed_record,
    write_started_record,
)
from .models import (
    RuntimeEffectDecision,
    RuntimeEffectMutationPhase,
    RuntimeEffectResult,
)
from .primitives import (
    PrimitiveExecutorRegistry,
    default_primitive_executor_registry,
)

_MUTATING_PRIMITIVES = frozenset({"persist_record", "enqueue_work_items"})


@dataclass(slots=True)
class InterpreterContext:
    """Execution context for operation step interpretation."""

    workspace_paths: WorkspacePaths
    stage_result: StageResultEnvelope
    run_dir: Path
    compiled_plan: Any
    registry: PrimitiveExecutorRegistry = field(
        default_factory=default_primitive_executor_registry
    )
    step_context: dict[str, JsonValue] = field(default_factory=dict)
    source_runner_id: str = ""
    source_work_item_family: str | None = None
    source_work_item_id: str | None = None
    request_id: str | None = None

    @property
    def paths(self) -> WorkspacePaths:
        return self.workspace_paths

    @property
    def root(self) -> Path:
        return self.workspace_paths.root


INTERPRETED_RUNNER_ID = "interpreted_runtime_effect"


def interpret_operation(
    workspace_paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: Any,
    *,
    operation_id: str,
    runner_id: str,
    registry: PrimitiveExecutorRegistry | None = None,
) -> RuntimeEffectResult:
    """Interpret and execute an operation's declared steps.

    This is the entry point called by the effect_execution dispatch layer
    when a runner is detected as an interpreted runner (runner_id ==
    INTERPRETED_RUNNER_ID).
    """
    effective_registry = registry or default_primitive_executor_registry()
    ctx = InterpreterContext(
        workspace_paths=workspace_paths,
        stage_result=stage_result,
        run_dir=run_dir,
        compiled_plan=compiled_plan,
        registry=effective_registry,
        source_runner_id=runner_id,
        source_work_item_family=stage_result.work_item_family_id,
        source_work_item_id=stage_result.work_item_id,
    )
    return _interpret(ctx, operation_id=operation_id, runner_id=runner_id)


def _interpret(
    ctx: InterpreterContext,
    *,
    operation_id: str,
    runner_id: str,
) -> RuntimeEffectResult:
    """Walk operation steps and dispatch to the executor registry."""
    operation_def = _resolve_operation(ctx, operation_id)
    if operation_def is None:
        return RuntimeEffectResult(
            operation_id=operation_id,
            runner_id=runner_id,
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PRE_MUTATION,
            failure_class="interpreted_operation_missing",
            message=f"operation definition not found for {operation_id} in compiled plan",
        )

    steps = _operation_steps(operation_def)
    if not steps:
        return RuntimeEffectResult(
            operation_id=operation_id,
            runner_id=runner_id,
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PRE_MUTATION,
            failure_class="interpreted_operation_no_steps",
            message=f"operation {operation_id} has no steps",
        )

    created_paths: list[str] = []
    mutation_journal: list[dict[str, JsonValue]] = []
    mutation_phase = RuntimeEffectMutationPhase.PRE_MUTATION

    failure_mappings = _failure_mappings(operation_def)
    mutation_journal_config = _mutation_journal_config(operation_def)

    # --- idempotent resume: load completed hashes from the durable journal ---
    journal_dir = ctx.workspace_paths.runtime_effect_journal_dir
    completed_hashes = completed_hashes_by_step(journal_dir, operation_id)

    # Source context extracted from ctx for journal record calls
    source_work_item_family = ctx.source_work_item_family
    source_work_item_id = ctx.source_work_item_id
    request_id = ctx.request_id
    run_id_val = ctx.stage_result.run_id

    for step in steps:
        step_id = str(getattr(step, "step_id", ""))
        primitive_id = str(getattr(step, "primitive_id", ""))
        step_mutation_phase_raw = str(getattr(step, "mutation_phase", "pre_mutation") or "pre_mutation")

        # Resolve params from input_bindings
        params = _resolve_step_params(ctx, step)

        reads_artifact_ids = tuple(
            str(aid) for aid in (getattr(step, "reads_artifact_ids", ()) or ())
        )

        executor = ctx.registry.executor_for(primitive_id)
        if executor is None:
            return RuntimeEffectResult(
                operation_id=operation_id,
                runner_id=runner_id,
                decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
                mutation_phase=RuntimeEffectMutationPhase.PRE_MUTATION,
                failure_class="interpreted_primitive_unknown",
                message=f"unknown primitive {primitive_id} in step {step_id} of {operation_id}",
            )

        # --- idempotent resume guard for mutating primitives ---
        if primitive_id in _MUTATING_PRIMITIVES:
            idem_hash = compute_idempotency_hash(
                operation_id, step_id, params, reads_artifact_ids
            )
            step_completed_hashes = completed_hashes.get(step_id, set())

            if idem_hash in step_completed_hashes:
                # Already completed successfully — skip.
                continue

            if step_completed_hashes:
                # Completed records exist for this step, but the hash differs.
                # This is a non-equivalent duplicate.
                mapped = _map_failure(
                    failure_mappings,
                    failure_class="interpreted_idempotency_conflict",
                    step_mutation_phase=step_mutation_phase_raw,
                )

                # Write a failed record if an uncompleted started record
                # exists for this step (from a prior interrupted run).
                if has_started_record(journal_dir, operation_id, step_id):
                    write_failed_record(
                        journal_dir,
                        operation_id,
                        step_id,
                        params,
                        reads_artifact_ids,
                        runner_id=runner_id,
                        failure_class=mapped or "interpreted_idempotency_conflict",
                        failure_message=(
                            f"non-equivalent duplicate mutation in step {step_id} "
                            f"of {operation_id}: existing completed record uses "
                            f"a different idempotency hash"
                        ),
                        source_work_item_family=source_work_item_family,
                        source_work_item_id=source_work_item_id,
                        run_id=run_id_val,
                        request_id=request_id,
                        primitive_id=primitive_id,
                    )

                return RuntimeEffectResult(
                    operation_id=operation_id,
                    runner_id=runner_id,
                    decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
                    mutation_phase=_mutation_phase_value(
                        step_mutation_phase_raw, len(created_paths)
                    ),
                    failure_class=mapped or "interpreted_idempotency_conflict",
                    message=(
                        f"non-equivalent duplicate mutation in step {step_id} "
                        f"of {operation_id}: existing completed record uses "
                        f"a different idempotency hash"
                    ),
                    created_paths=tuple(created_paths),
                )

            # Write started record before the mutation.
            write_started_record(
                journal_dir,
                operation_id,
                step_id,
                params,
                reads_artifact_ids,
                runner_id=runner_id,
                source_work_item_family=source_work_item_family,
                source_work_item_id=source_work_item_id,
                run_id=run_id_val,
                request_id=request_id,
                primitive_id=primitive_id,
            )

        try:
            result = executor(ctx, step_id, params, reads_artifact_ids)
        except Exception as exc:
            # Write a failed journal record before returning the error.
            if primitive_id in _MUTATING_PRIMITIVES:
                write_failed_record(
                    journal_dir,
                    operation_id,
                    step_id,
                    params,
                    reads_artifact_ids,
                    runner_id=runner_id,
                    failure_class="interpreted_primitive_error",
                    failure_message=str(exc),
                    source_work_item_family=source_work_item_family,
                    source_work_item_id=source_work_item_id,
                    run_id=run_id_val,
                    request_id=request_id,
                    primitive_id=primitive_id,
                )

            return RuntimeEffectResult(
                operation_id=operation_id,
                runner_id=runner_id,
                decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
                mutation_phase=_mutation_phase_value(
                    step_mutation_phase_raw, len(created_paths)
                ),
                failure_class="interpreted_primitive_error",
                message=f"primitive {primitive_id} failed in step {step_id}: {exc}",
                created_paths=tuple(created_paths),
            )

        decision = str(result.get("decision", "continue") or "continue")

        if decision.startswith("pre_mutation_failure"):
            failure_class = str(
                result.get("failure_class", "interpreted_step_failure")
            )
            message = str(result.get("message", f"step {step_id} failed"))

            # Map through operation failure_mappings
            mapped = _map_failure(
                failure_mappings,
                failure_class=failure_class,
                step_mutation_phase=step_mutation_phase_raw,
            )

            # Write a failed journal record before returning the error.
            if primitive_id in _MUTATING_PRIMITIVES:
                write_failed_record(
                    journal_dir,
                    operation_id,
                    step_id,
                    params,
                    reads_artifact_ids,
                    runner_id=runner_id,
                    failure_class=mapped or failure_class,
                    failure_message=message,
                    source_work_item_family=source_work_item_family,
                    source_work_item_id=source_work_item_id,
                    run_id=run_id_val,
                    request_id=request_id,
                    primitive_id=primitive_id,
                )

            return RuntimeEffectResult(
                operation_id=operation_id,
                runner_id=runner_id,
                decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
                mutation_phase=_mutation_phase_value(
                    step_mutation_phase_raw, len(created_paths)
                ),
                failure_class=mapped or failure_class,
                message=message,
                created_paths=tuple(created_paths),
            )

        # Collect created paths from the step result
        step_paths = result.get("created_paths", ()) or ()
        if isinstance(step_paths, (list, tuple)):
            created_paths.extend(str(p) for p in step_paths)

        # Track mutation phase
        if step_mutation_phase_raw == "partial_mutation" and created_paths:
            mutation_phase = RuntimeEffectMutationPhase.PARTIAL_MUTATION

        # Write completed journal record for mutating primitives.
        if primitive_id in _MUTATING_PRIMITIVES:
            write_completed_record(
                journal_dir,
                operation_id,
                step_id,
                params,
                reads_artifact_ids,
                runner_id=runner_id,
                source_work_item_family=source_work_item_family,
                source_work_item_id=source_work_item_id,
                run_id=run_id_val,
                request_id=request_id,
                primitive_id=primitive_id,
            )
            # Track the hash in-memory so a step cannot be re-executed
            # twice within the same interpretation pass.
            completed_hashes.setdefault(step_id, set()).add(
                compute_idempotency_hash(
                    operation_id, step_id, params, reads_artifact_ids
                )
            )

        # Write journal entry if configured
        if _should_journal(mutation_journal_config, step_id):
            mutation_journal.append(
                {
                    "operation_id": operation_id,
                    "run_id": ctx.stage_result.run_id,
                    "step_id": step_id,
                    "mutation_phase": step_mutation_phase_raw,
                    "decision": decision,
                }
            )

    return RuntimeEffectResult(
        operation_id=operation_id,
        runner_id=runner_id,
        decision=RuntimeEffectDecision.CONTINUE_ROUTE,
        created_paths=tuple(created_paths),
        mutation_phase=mutation_phase,
        mutation_journal=tuple(mutation_journal),
    )


def _resolve_operation(ctx: InterpreterContext, operation_id: str) -> Any | None:
    compiled_plan = ctx.compiled_plan
    ops_by_id = getattr(compiled_plan, "runtime_effect_operations_by_id", {}) or {}
    return ops_by_id.get(operation_id)


def _operation_steps(operation_def: Any) -> tuple[Any, ...]:
    return tuple(getattr(operation_def, "steps", ()) or ())


def _failure_mappings(operation_def: Any) -> tuple[Any, ...]:
    return tuple(getattr(operation_def, "failure_mappings", ()) or ())


def _mutation_journal_config(operation_def: Any) -> Any:
    return getattr(operation_def, "mutation_journal", None)


def _should_journal(config: Any, step_id: str) -> bool:
    if config is None:
        return False
    record_steps = getattr(config, "record_step_ids", ()) or ()
    return step_id in record_steps


def _resolve_step_params(
    ctx: InterpreterContext,
    step: Any,
) -> dict[str, JsonValue]:
    """Resolve step parameters from input_bindings and params."""
    params: dict[str, JsonValue] = dict(
        getattr(step, "params", {}) or {}
    )
    input_bindings = getattr(step, "input_bindings", {}) or {}
    store_id = getattr(step, "store_id", None)

    # Add store_id if present
    if store_id is not None:
        params["store_id"] = str(store_id)

    # Resolve each input binding
    for binding_key, binding_value in input_bindings.items():
        resolved = _resolve_binding(ctx, str(binding_value))
        if resolved is not None:
            params[binding_key] = resolved

    return params


def _resolve_binding(
    ctx: InterpreterContext,
    binding_value: str,
) -> JsonValue | None:
    """Resolve a binding reference like $artifact.some_id or $context.some_key."""
    if not binding_value.startswith("$"):
        return binding_value

    if binding_value.startswith("$artifact."):
        artifact_id = binding_value[len("$artifact.") :]
        return artifact_id  # Pass through as the ID for artifact references

    if binding_value.startswith("$context."):
        key = binding_value[len("$context.") :]
        return ctx.step_context.get(key)

    if binding_value.startswith("$store."):
        store_id = binding_value[len("$store.") :]
        return store_id  # Pass through as store reference

    return binding_value


def _map_failure(
    failure_mappings: tuple[Any, ...],
    *,
    failure_class: str,
    step_mutation_phase: str,
) -> str | None:
    """Map a primitive failure_class through the operation's failure_mappings."""
    for mapping in failure_mappings:
        mapping_class = str(getattr(mapping, "failure_class", "") or "")
        mapping_phase = str(getattr(mapping, "mutation_phase", "") or "")
        if mapping_class == failure_class and mapping_phase == step_mutation_phase:
            return failure_class
    # If no mapping found, return the original class
    return None


def _mutation_phase_value(
    raw: str,
    created_count: int,
) -> RuntimeEffectMutationPhase:
    if raw == "partial_mutation" or created_count > 0:
        return RuntimeEffectMutationPhase.PARTIAL_MUTATION
    if raw == "unknown":
        return RuntimeEffectMutationPhase.UNKNOWN
    return RuntimeEffectMutationPhase.PRE_MUTATION


__all__ = [
    "INTERPRETED_RUNNER_ID",
    "InterpreterContext",
    "interpret_operation",
]
