"""Execution-plane diagnostics, recovery, and quarantine helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from ..contracts import (
    BlockerEntry,
    ExecutionResearchHandoff,
    ExecutionStatus,
    RunnerResult,
    StageResult,
    StageType,
    TaskCard,
)
from ..diagnostics import create_diagnostics_bundle
from ..markdown import insert_after_preamble, write_text_atomic
from ..run_ids import stable_slug
from ..stages.base import StageExecutionError

if TYPE_CHECKING:
    from ..config import EngineConfig
    from ..paths import RuntimePaths
    from ..queue import TaskQueue
    from ..status import StatusStore


class ExecutionRecoveryPlane(Protocol):
    config: EngineConfig
    paths: RuntimePaths
    queue: TaskQueue
    status_store: StatusStore
    _last_research_handoff: ExecutionResearchHandoff | None

    def _build_research_handoff(
        self,
        *,
        run_id: str,
        task: TaskCard,
        stage_label: str,
        reason: str,
        diagnostics_dir: Path | None,
        run_dir: Path | None,
        latch: object,
    ) -> ExecutionResearchHandoff: ...

    def _run_stage(
        self,
        stage_type: StageType,
        task: TaskCard | None,
        run_id: str,
        *,
        node_id: str | None = None,
    ) -> StageResult: ...

    def _record_stage_transition(self, result: StageResult, **kwargs: object) -> None: ...

    def _run_full_task_path(
        self,
        task: TaskCard,
        run_id: str,
        stage_results: list[StageResult],
        *,
        recovery_rounds: int = 0,
    ) -> ExecutionOutcome: ...


INCIDENT_PATH_RE = re.compile(r"agents/ideas/incidents/[A-Za-z0-9._/\-]+\.md")


@dataclass(frozen=True, slots=True)
class _RecoveryResult:
    action: Literal["resume", "quarantine"]
    diagnostics_dir: Path
    quarantined_task: TaskCard | None = None


ExecutionOutcome = tuple[ExecutionStatus, TaskCard | None, TaskCard | None, Path | None, int]


def active_config_hashes(plane: ExecutionRecoveryPlane) -> dict[str, str]:
    """Capture the active engine-config hash for diagnostics bundles."""

    payload = json.dumps(plane.config.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    return {"engine_config_sha256": sha256(payload).hexdigest()}


def extract_incident_path(plane: ExecutionRecoveryPlane, runner_result: RunnerResult | None) -> Path | None:
    """Extract the first incident markdown path surfaced by a consult-like runner result."""

    del plane
    if runner_result is None:
        return None
    text_parts = [runner_result.stdout, runner_result.stderr]
    if runner_result.last_response_path is not None and runner_result.last_response_path.exists():
        text_parts.append(runner_result.last_response_path.read_text(encoding="utf-8", errors="replace"))

    for text in text_parts:
        match = INCIDENT_PATH_RE.search(text)
        if match:
            return Path(match.group(0))
    return None


def create_blocker_bundle(
    plane: ExecutionRecoveryPlane,
    run_id: str,
    stage_label: str,
    why: str,
    failing_result: StageResult | None,
) -> Path:
    """Create the diagnostics bundle used for fallback escalation and quarantine."""

    snapshot_paths = [
        plane.paths.tasks_file,
        plane.paths.status_file,
        plane.paths.backlog_file,
        plane.paths.blocker_file,
    ]
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    run_dir: Path | None = None

    if failing_result is not None and failing_result.runner_result is not None:
        run_dir = failing_result.runner_result.run_dir
        stdout_path = failing_result.runner_result.stdout_path
        stderr_path = failing_result.runner_result.stderr_path
        if run_dir is not None:
            snapshot_paths.append(run_dir)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    bundle_name = (
        f"diag-{stable_slug(run_id, fallback='run')}"
        f"-{stable_slug(stage_label, fallback='run')}-{timestamp}"
    )
    return create_diagnostics_bundle(
        plane.paths,
        stage=failing_result.stage if failing_result is not None else StageType.CONSULT,
        marker=failing_result.status if failing_result is not None else None,
        run_dir=run_dir,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        snapshot_paths=snapshot_paths,
        config_hashes=active_config_hashes(plane),
        note=why,
        bundle_name=bundle_name,
    )


def write_blocker_entry(
    plane: ExecutionRecoveryPlane,
    task: TaskCard | None,
    *,
    stage_label: str,
    reason: str,
    diagnostics_dir: Path,
    status: ExecutionStatus = ExecutionStatus.BLOCKED,
    run_dir: Path | None = None,
    prompt_artifact: Path | None = None,
    incident_path: Path | None = None,
    notes: str | None = None,
) -> None:
    """Persist one blocker-ledger entry without mutating queue ownership."""

    task_title = task.title if task is not None else "No active task"
    source_task = f"agents/tasks.md :: {task.heading}" if task is not None else "agents/tasks.md :: n/a"
    blocker_entry = BlockerEntry.model_validate(
        {
            "occurred_at": datetime.now(timezone.utc),
            "task_title": task_title,
            "status": status,
            "stage_blocked": stage_label,
            "source_task": source_task,
            "prompt_artifact": prompt_artifact,
            "run_dir": run_dir,
            "diagnostics_dir": diagnostics_dir,
            "root_cause_summary": reason,
            "next_action": (
                "Research handoff via incident intake and pending-task regeneration"
                if status is ExecutionStatus.NEEDS_RESEARCH
                else "Operator review of blocker state"
            ),
            "incident_path": incident_path,
            "notes": notes,
        }
    )
    blocker_text = plane.paths.blocker_file.read_text(encoding="utf-8")
    write_text_atomic(plane.paths.blocker_file, insert_after_preamble(blocker_text, blocker_entry.render_markdown()))


def quarantine_task(
    plane: ExecutionRecoveryPlane,
    task: TaskCard,
    *,
    run_id: str,
    stage_label: str,
    why: str,
    diagnostics_dir: Path,
    consult_result: StageResult | None,
) -> TaskCard:
    """Quarantine one task into needs-research storage and reset the plane to idle."""

    incident_path = extract_incident_path(plane, consult_result.runner_result if consult_result else None)

    run_dir = None
    prompt_artifact = None
    if consult_result is not None and consult_result.runner_result is not None:
        run_dir = consult_result.runner_result.run_dir
        prompt_artifact = consult_result.runner_result.last_response_path

    latch = plane.queue.quarantine(
        task,
        why,
        incident_path,
        stage=stage_label,
        status=ExecutionStatus.NEEDS_RESEARCH,
        run_dir=run_dir,
        diagnostics_dir=diagnostics_dir,
        prompt_artifact=prompt_artifact,
    )
    handoff = plane._build_research_handoff(
        run_id=run_id,
        task=task,
        stage_label=stage_label,
        reason=why,
        diagnostics_dir=diagnostics_dir,
        run_dir=run_dir,
        latch=latch,
    )
    latch = latch.model_copy(update={"handoff": handoff})
    write_text_atomic(
        plane.paths.research_recovery_latch_file,
        latch.model_dump_json(indent=2, exclude_none=True) + "\n",
    )
    plane._last_research_handoff = handoff
    plane.status_store.transition(ExecutionStatus.IDLE)
    return task


def recover_or_quarantine(
    plane: ExecutionRecoveryPlane,
    task: TaskCard,
    *,
    run_id: str,
    stage_label: str,
    why: str,
    stage_results: list[StageResult],
    failing_result: StageResult | None,
    routing_mode_fixed_v1_fallback: str,
) -> _RecoveryResult:
    """Run the fixed-v1 escalation sequence, resuming locally or quarantining the task."""

    diagnostics_dir = create_blocker_bundle(plane, run_id, stage_label, why, failing_result)
    consult_result: StageResult | None = None
    last_result: StageResult | None = failing_result

    for escalation_stage in plane.config.routing.escalation_sequence:
        stage_result = plane._run_stage(escalation_stage, task, run_id)
        stage_results.append(stage_result)
        last_result = stage_result
        stage_status = ExecutionStatus(stage_result.status)

        if escalation_stage is StageType.TROUBLESHOOT:
            plane._record_stage_transition(
                stage_result,
                task_before=task,
                task_after=task,
                routing_mode=routing_mode_fixed_v1_fallback,
                selected_edge_id=(
                    "execution.troubleshoot.success.resume"
                    if stage_status is ExecutionStatus.TROUBLESHOOT_COMPLETE
                    else "execution.troubleshoot.blocked.consult"
                ),
                selected_edge_reason=(
                    "troubleshoot restored a local execution path"
                    if stage_status is ExecutionStatus.TROUBLESHOOT_COMPLETE
                    else f"troubleshoot ended with {stage_status.value}, so consult remains eligible"
                ),
                condition_inputs={"status": stage_status.value},
                condition_result=stage_status is ExecutionStatus.TROUBLESHOOT_COMPLETE,
                attributes={"recovery_diagnostics_dir": diagnostics_dir.as_posix()},
            )
            if stage_status is ExecutionStatus.TROUBLESHOOT_COMPLETE:
                return _RecoveryResult(action="resume", diagnostics_dir=diagnostics_dir)
            continue

        if escalation_stage is StageType.CONSULT:
            consult_result = stage_result
            plane._record_stage_transition(
                stage_result,
                task_before=task,
                task_after=(task if stage_status is ExecutionStatus.CONSULT_COMPLETE else None),
                routing_mode=routing_mode_fixed_v1_fallback,
                selected_edge_id=(
                    "execution.consult.success.resume"
                    if stage_status is ExecutionStatus.CONSULT_COMPLETE
                    else "execution.consult.handoff.needs_research"
                ),
                selected_terminal_state_id=(
                    None if stage_status is ExecutionStatus.CONSULT_COMPLETE else "needs_research"
                ),
                selected_edge_reason=(
                    "consult found a local path back into the execution loop"
                    if stage_status is ExecutionStatus.CONSULT_COMPLETE
                    else f"consult ended with {stage_status.value}, so the task is handed off"
                ),
                condition_inputs={"status": stage_status.value},
                condition_result=stage_status is ExecutionStatus.CONSULT_COMPLETE,
                queue_mutations_applied=(
                    () if stage_status is ExecutionStatus.CONSULT_COMPLETE else ("quarantine_task",)
                ),
                attributes={"recovery_diagnostics_dir": diagnostics_dir.as_posix()},
            )
            if stage_status is ExecutionStatus.CONSULT_COMPLETE:
                return _RecoveryResult(action="resume", diagnostics_dir=diagnostics_dir)
            continue

        raise StageExecutionError(
            f"unsupported escalation stage in routing config: {escalation_stage.value}"
        )

    final_status = (
        ExecutionStatus(consult_result.status)
        if consult_result is not None
        else (ExecutionStatus(last_result.status) if last_result is not None else ExecutionStatus.BLOCKED)
    )
    quarantined = quarantine_task(
        plane,
        task,
        run_id=run_id,
        stage_label=stage_label,
        why=(
            why
            if final_status is ExecutionStatus.NEEDS_RESEARCH
            else f"{why} (post-escalation status: {final_status.value})"
        ),
        diagnostics_dir=diagnostics_dir,
        consult_result=consult_result,
    )
    return _RecoveryResult(action="quarantine", diagnostics_dir=diagnostics_dir, quarantined_task=quarantined)


def resume_after_recovery(
    plane: ExecutionRecoveryPlane,
    task: TaskCard,
    *,
    run_id: str,
    stage_results: list[StageResult],
    recovery_rounds: int,
    max_local_recovery_rounds: int,
    diagnostics_dir: Path | None = None,
) -> ExecutionOutcome:
    """Resume the fixed-v1 fallback flow after a successful local recovery stage."""

    if recovery_rounds > max_local_recovery_rounds:
        if diagnostics_dir is None:
            diagnostics_dir = create_blocker_bundle(
                plane,
                run_id,
                "Consult",
                "Local recovery exhausted after consult/troubleshoot",
                stage_results[-1] if stage_results else None,
            )
        quarantined = quarantine_task(
            plane,
            task,
            run_id=run_id,
            stage_label="Consult",
            why="Local recovery exhausted after consult/troubleshoot",
            diagnostics_dir=diagnostics_dir,
            consult_result=None,
        )
        return ExecutionStatus.IDLE, None, quarantined, diagnostics_dir, 0
    final_status, archived_task, quarantined_task, resumed_diagnostics_dir, quickfix_attempts = (
        plane._run_full_task_path(task, run_id, stage_results, recovery_rounds=recovery_rounds)
    )
    return (
        final_status,
        archived_task,
        quarantined_task,
        resumed_diagnostics_dir or diagnostics_dir,
        quickfix_attempts,
    )


__all__ = [
    "_RecoveryResult",
    "active_config_hashes",
    "create_blocker_bundle",
    "extract_incident_path",
    "quarantine_task",
    "recover_or_quarantine",
    "resume_after_recovery",
    "write_blocker_entry",
]
