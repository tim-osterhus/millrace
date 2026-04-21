"""Planning-handoff incident creation for routed stage results."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from millrace_ai.contracts import (
    IncidentDecision,
    IncidentDocument,
    IncidentSeverity,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterDecision

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def enqueue_handoff_incident(
    engine: RuntimeEngine,
    *,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
) -> Path:
    queue = QueueStore(engine.paths)
    root_spec_id = _metadata_string(stage_result, "closure_target_root_spec_id")
    root_idea_id = _metadata_string(stage_result, "closure_target_root_idea_id")
    is_closure_target = _is_closure_target_result(stage_result)
    incident_id = (
        f"arbiter-gap-{root_spec_id}-{uuid4().hex[:8]}"
        if is_closure_target and root_spec_id is not None
        else f"incident-{stage_result.work_item_id}-{uuid4().hex[:8]}"
    )
    source_task_id = None if is_closure_target else (
        stage_result.work_item_id if stage_result.work_item_kind is WorkItemKind.TASK else None
    )
    source_spec_id = (
        root_spec_id
        if is_closure_target
        else (stage_result.work_item_id if stage_result.work_item_kind is WorkItemKind.SPEC else None)
    )
    evidence_paths = list(stage_result.artifact_paths)
    for key in ("preferred_rubric_path", "preferred_verdict_path", "preferred_report_path"):
        value = _metadata_string(stage_result, key)
        if value is not None and value not in evidence_paths:
            evidence_paths.append(value)
    incident = IncidentDocument(
        incident_id=incident_id,
        title=(
            f"Arbiter remediation for {root_spec_id}"
            if is_closure_target and root_spec_id is not None
            else f"Planning handoff for {stage_result.work_item_kind.value} {stage_result.work_item_id}"
        ),
        summary=(
            (
                f"Arbiter found parity gaps for root spec {root_spec_id}; planning remediation required."
                if is_closure_target and root_spec_id is not None
                else (
                    f"Stage {stage_result.stage.value} returned {stage_result.terminal_result.value}; "
                    "planning remediation required."
                )
            )
        ),
        root_idea_id=root_idea_id,
        root_spec_id=root_spec_id,
        source_task_id=source_task_id,
        source_spec_id=source_spec_id,
        source_stage=stage_result.stage,
        source_plane=stage_result.plane,
        failure_class=decision.failure_class or (
            "arbiter_parity_gap" if is_closure_target else "consultant_needs_planning"
        ),
        severity=IncidentSeverity.HIGH,
        needs_planning=True,
        trigger_reason=decision.reason,
        observed_symptoms=stage_result.notes,
        failed_attempts=(),
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        evidence_paths=tuple(evidence_paths),
        related_run_ids=(stage_result.run_id,),
        related_stage_results=(
            engine.snapshot.last_stage_result_path,
        )
        if engine.snapshot is not None and engine.snapshot.last_stage_result_path is not None
        else (),
        references=(),
        opened_at=engine._now(),
        opened_by="runtime",
    )
    destination = queue.enqueue_incident(incident)
    write_runtime_event(
        engine.paths,
        event_type="runtime_handoff_incident_enqueued",
        data={
            "incident_id": incident_id,
            "source_work_item_kind": stage_result.work_item_kind.value,
            "source_work_item_id": stage_result.work_item_id,
            "destination": str(destination.relative_to(engine.paths.root)),
        },
    )
    return destination


def _is_closure_target_result(stage_result: StageResultEnvelope) -> bool:
    return stage_result.metadata.get("request_kind") == "closure_target"


def _metadata_string(stage_result: StageResultEnvelope, key: str) -> str | None:
    value = stage_result.metadata.get(key)
    return value if isinstance(value, str) and value else None


__all__ = ["enqueue_handoff_incident"]
