"""Runtime milestone flush helpers for governed compounding artifacts."""

from __future__ import annotations

from ..contract_compounding import CompoundingFlushCheckpoint, CompoundingFlushMilestone, ReusableProcedureArtifact
from ..contract_context_facts import ContextFactArtifact
from ..contract_core import StageType
from ..context_facts import ensure_workspace_candidate_context_fact
from ..paths import RuntimePaths
from .lifecycle import ensure_workspace_candidate_procedure


_RECOVERY_SUCCESS_EDGE_IDS = frozenset(
    {
        "execution.doublecheck.success.update",
        "execution.troubleshoot.success.resume",
        "execution.consult.success.resume",
    }
)


def flush_milestone_for_transition(
    *,
    stage: StageType,
    selected_edge_id: str,
    candidate_created: bool,
) -> CompoundingFlushMilestone | None:
    """Classify one transition into the supported compounding flush cadence."""

    if selected_edge_id == "execution.update.success.archive":
        return CompoundingFlushMilestone.RUN_CLOSEOUT
    if not candidate_created:
        return None
    if selected_edge_id in _RECOVERY_SUCCESS_EDGE_IDS:
        return CompoundingFlushMilestone.RECOVERY_SUCCESS
    return CompoundingFlushMilestone.STAGE_SUCCESS


def flush_run_scoped_compounding_candidates(
    paths: RuntimePaths,
    *,
    run_id: str,
    trigger_stage: StageType,
    milestone: CompoundingFlushMilestone,
) -> CompoundingFlushCheckpoint:
    """Finalize run-scoped governed artifacts into workspace review checkpoints."""

    finalized_procedure_ids = tuple(
        _flush_run_scoped_procedures(paths, run_id=run_id, milestone=milestone, trigger_stage=trigger_stage)
    )
    finalized_context_fact_ids = tuple(_flush_run_scoped_context_facts(paths, run_id=run_id))
    return CompoundingFlushCheckpoint(
        run_id=run_id,
        trigger_stage=trigger_stage,
        milestone=milestone,
        finalized_procedure_ids=finalized_procedure_ids,
        finalized_context_fact_ids=finalized_context_fact_ids,
    )


def _flush_run_scoped_procedures(
    paths: RuntimePaths,
    *,
    run_id: str,
    milestone: CompoundingFlushMilestone,
    trigger_stage: StageType,
) -> list[str]:
    run_dir = paths.compounding_procedures_dir / run_id
    if not run_dir.exists():
        return []

    changed_by = f"runtime.compounding.{milestone.value}"
    reason = (
        f"Runtime {milestone.value.replace('_', ' ')} checkpoint finalized "
        f"{trigger_stage.value} run-scoped candidate for review."
    )
    finalized: list[str] = []
    for path in sorted(run_dir.glob("*.json")):
        artifact = ReusableProcedureArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        procedure_id = ensure_workspace_candidate_procedure(
            paths,
            artifact,
            changed_by=changed_by,
            reason=reason,
        )
        if procedure_id is not None:
            finalized.append(procedure_id)
    return finalized


def _flush_run_scoped_context_facts(paths: RuntimePaths, *, run_id: str) -> list[str]:
    run_dir = paths.compounding_context_facts_dir / run_id
    if not run_dir.exists():
        return []

    finalized: list[str] = []
    for path in sorted(run_dir.glob("*.json")):
        artifact = ContextFactArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        fact_id = ensure_workspace_candidate_context_fact(paths, artifact)
        if fact_id is not None:
            finalized.append(fact_id)
    return finalized


__all__ = [
    "CompoundingFlushCheckpoint",
    "CompoundingFlushMilestone",
    "flush_milestone_for_transition",
    "flush_run_scoped_compounding_candidates",
]
