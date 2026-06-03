"""Runtime effect handler for Planner source disposition."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import PlannerDispositionDocument, StageResultEnvelope
from millrace_ai.workspace.paths import WorkspacePaths

from .artifact_contracts import (
    RuntimeArtifactError,
    parse_resolved_run_artifact_as,
    resolve_run_artifact,
)
from .effects import (
    RuntimeEffectDecision,
    RuntimeEffectMutationPhase,
    RuntimeEffectResult,
)

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan

PLANNER_DISPOSITION_HANDLER_ID = "planner_disposition"


def planner_disposition(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Interpret Planner's explicit source disposition before downstream routing."""

    try:
        disposition = parse_resolved_run_artifact_as(
            resolve_run_artifact(compiled_plan, "planner_disposition", run_dir),
            PlannerDispositionDocument,
        )
    except RuntimeArtifactError as exc:
        if exc.failure_class == "artifact_missing":
            return _failure_result(
                stage_result,
                failure_class="planner_disposition_missing",
                message="required Planner artifact is missing: planner_disposition.json",
            )
        return _failure_result(
            stage_result,
            failure_class="planner_disposition_invalid",
            message=str(exc),
        )
    except Exception as exc:
        return _failure_result(
            stage_result,
            failure_class="planner_disposition_invalid",
            message=str(exc),
        )

    mismatch = _source_mismatch(stage_result, disposition)
    if mismatch is not None:
        return _failure_result(
            stage_result,
            failure_class="planner_disposition_source_mismatch",
            message=mismatch,
        )

    terminal_result = stage_result.terminal_result.value
    if disposition.disposition == "active_source_ready_for_manager":
        if terminal_result != "PLANNER_COMPLETE":
            return _failure_result(
                stage_result,
                failure_class="planner_disposition_terminal_mismatch",
                message="active_source_ready_for_manager requires PLANNER_COMPLETE",
            )
        return RuntimeEffectResult(
            handler_id=PLANNER_DISPOSITION_HANDLER_ID,
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
            message="Planner disposition keeps active source on the Manager route",
        )

    if disposition.disposition == "blocked":
        if terminal_result == "BLOCKED":
            return RuntimeEffectResult(
                handler_id=PLANNER_DISPOSITION_HANDLER_ID,
                decision=RuntimeEffectDecision.CONTINUE_ROUTE,
                message="Planner disposition preserves graph-declared blocked recovery",
            )
        return _failure_result(
            stage_result,
            failure_class="planner_disposition_blocked",
            message="blocked disposition requires the BLOCKED terminal result",
        )

    if terminal_result != "PLANNER_COMPLETE":
        return _failure_result(
            stage_result,
            failure_class="planner_disposition_terminal_mismatch",
            message="emitted_child_specs requires PLANNER_COMPLETE",
        )

    missing_spec_ids = _missing_emitted_spec_ids(paths, disposition.emitted_spec_ids)
    if missing_spec_ids:
        return _failure_result(
            stage_result,
            failure_class="planner_disposition_child_spec_missing",
            message="Planner emitted child spec ids that are not queued: "
            + ", ".join(missing_spec_ids),
        )

    queued_paths = tuple(
        _effect_path(paths, paths.specs_queue_dir / f"{spec_id}.md")
        for spec_id in disposition.emitted_spec_ids
    )
    return RuntimeEffectResult(
        handler_id=PLANNER_DISPOSITION_HANDLER_ID,
        decision=RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE,
        created_paths=queued_paths,
        message=(
            "Planner disposition resolved active source after emitting child specs: "
            + ", ".join(disposition.emitted_spec_ids)
        ),
    )


def _source_mismatch(
    stage_result: StageResultEnvelope,
    disposition: PlannerDispositionDocument,
) -> str | None:
    family_id = stage_result.work_item_family_id
    if family_id is None and stage_result.work_item_kind is not None:
        family_id = stage_result.work_item_kind.value
    if family_id != disposition.source_work_item_family_id:
        return (
            "planner disposition source family mismatch: "
            f"{disposition.source_work_item_family_id} != {family_id}"
        )
    if stage_result.work_item_id != disposition.source_work_item_id:
        return (
            "planner disposition source id mismatch: "
            f"{disposition.source_work_item_id} != {stage_result.work_item_id}"
        )
    return None


def _missing_emitted_spec_ids(
    paths: WorkspacePaths,
    emitted_spec_ids: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        spec_id
        for spec_id in emitted_spec_ids
        if not (paths.specs_queue_dir / f"{spec_id}.md").is_file()
    )


def _failure_result(
    stage_result: StageResultEnvelope,
    *,
    failure_class: str,
    message: str,
) -> RuntimeEffectResult:
    return RuntimeEffectResult(
        handler_id=PLANNER_DISPOSITION_HANDLER_ID,
        decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
        failure_class=failure_class,
        message=message,
        mutation_phase=RuntimeEffectMutationPhase.PRE_MUTATION,
    )


def _effect_path(paths: WorkspacePaths, path: Path) -> str:
    return path.relative_to(paths.root).as_posix()


__all__ = ["PLANNER_DISPOSITION_HANDLER_ID", "planner_disposition"]
