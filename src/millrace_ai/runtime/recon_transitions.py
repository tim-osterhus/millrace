"""Runtime-owned application of Recon probe routing results."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import (
    PlanningStageName,
    PlanningTerminalResult,
    ReconDecision,
    ReconPacketDocument,
    RootIntakeKind,
    RuntimeErrorCode,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.queue_store import QueueStore
from millrace_ai.recon_packets import read_recon_packet, render_recon_packet
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.work_documents import parse_work_document_as

from .error_recovery import record_post_stage_exception_context
from .work_item_transitions import apply_blocked_router_decision, apply_idle_router_decision

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def is_recon_stage_result(stage_result: StageResultEnvelope) -> bool:
    """Return whether a stage result belongs to Recon probe intake."""

    return stage_result.stage_kind_id == "recon" and stage_result.work_item_kind is WorkItemKind.PROBE


def apply_recon_router_decision(
    engine: RuntimeEngine,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
    *,
    stage_result_path: Path | None = None,
) -> tuple[Path, ...]:
    """Persist Recon artifacts, enqueue routed work, then finish the active probe."""

    terminal_result = PlanningTerminalResult(stage_result.terminal_result)
    if (
        terminal_result
        in {
            PlanningTerminalResult.RECON_TO_EXECUTION,
            PlanningTerminalResult.RECON_TO_PLANNING,
            PlanningTerminalResult.RECON_NOOP,
        }
        and decision.action is not RouterAction.IDLE
    ):
        raise ValueError("successful recon terminal results require an idle router decision")
    if (
        terminal_result
        in {
            PlanningTerminalResult.RECON_BLOCKED,
            PlanningTerminalResult.BLOCKED,
        }
        and decision.action is not RouterAction.BLOCKED
    ):
        raise ValueError("blocked recon terminal results require a blocked router decision")

    try:
        packet = _read_and_persist_packet(engine, stage_result)
        _validate_packet_for_stage_result(packet, stage_result, terminal_result)

        spawned: tuple[Path, ...] = ()
        if terminal_result is PlanningTerminalResult.RECON_TO_EXECUTION:
            spawned = (_enqueue_generated_task(engine, stage_result, packet),)
            apply_idle_router_decision(engine, stage_result)
            return spawned
        if terminal_result is PlanningTerminalResult.RECON_TO_PLANNING:
            spawned = (_enqueue_generated_spec(engine, stage_result, packet),)
            apply_idle_router_decision(engine, stage_result)
            return spawned
        if terminal_result is PlanningTerminalResult.RECON_NOOP:
            apply_idle_router_decision(engine, stage_result)
            return ()

        apply_blocked_router_decision(
            engine,
            decision,
            stage_result,
            stage_result_path=stage_result_path,
        )
        return ()
    except Exception as exc:
        return _block_invalid_recon_handoff(
            engine,
            stage_result=stage_result,
            router_decision=decision,
            stage_result_path=stage_result_path,
            error=exc,
        )


def _block_invalid_recon_handoff(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
    router_decision: RouterDecision,
    stage_result_path: Path | None,
    error: Exception,
) -> tuple[Path, ...]:
    record_post_stage_exception_context(
        engine,
        stage_result=stage_result,
        error=error,
        router_decision=router_decision,
        stage_result_path=stage_result_path,
        error_code=RuntimeErrorCode.RECON_HANDOFF_INVALID,
        repair_stage=PlanningStageName.RECON,
    )
    engine._set_plane_status_marker(
        plane=stage_result.plane,
        marker="### BLOCKED",
        run_id=stage_result.run_id,
        source="recon_handoff_invalid",
    )
    apply_blocked_router_decision(
        engine,
        RouterDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
            reason="recon_handoff_invalid",
            failure_class=RuntimeErrorCode.RECON_HANDOFF_INVALID.value,
        ),
        stage_result,
        stage_result_path=stage_result_path,
    )
    return ()


def _read_and_persist_packet(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
) -> ReconPacketDocument:
    run_dir = engine.paths.runs_dir / stage_result.run_id
    source = run_dir / "recon_packet.md"
    packet = read_recon_packet(source)
    destination = engine.paths.recon_packets_dir / f"{packet.recon_packet_id}.md"
    if destination.exists():
        raise ValueError(f"recon packet already exists: {destination}")
    destination.write_text(render_recon_packet(packet), encoding="utf-8")
    return packet


def _validate_packet_for_stage_result(
    packet: ReconPacketDocument,
    stage_result: StageResultEnvelope,
    terminal_result: PlanningTerminalResult,
) -> None:
    if packet.probe_id != stage_result.work_item_id:
        raise ValueError("recon packet probe_id must match active probe")
    expected_decision = {
        PlanningTerminalResult.RECON_TO_EXECUTION: ReconDecision.TO_EXECUTION,
        PlanningTerminalResult.RECON_TO_PLANNING: ReconDecision.TO_PLANNING,
        PlanningTerminalResult.RECON_NOOP: ReconDecision.NOOP,
        PlanningTerminalResult.RECON_BLOCKED: ReconDecision.BLOCKED,
        PlanningTerminalResult.BLOCKED: ReconDecision.BLOCKED,
    }[terminal_result]
    if packet.decision is not expected_decision:
        raise ValueError("recon packet decision must match terminal result")


def _enqueue_generated_task(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
    packet: ReconPacketDocument,
) -> Path:
    if packet.emitted_task_id is None:
        raise ValueError("recon execution route requires emitted_task_id")
    source = engine.paths.runs_dir / stage_result.run_id / "generated_task.md"
    task = _read_task_artifact(source)
    if task.task_id != packet.emitted_task_id:
        raise ValueError("generated task id must match recon packet emitted_task_id")
    task = _with_probe_task_lineage(task, packet)
    return QueueStore(engine.paths).enqueue_task(task)


def _enqueue_generated_spec(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
    packet: ReconPacketDocument,
) -> Path:
    if packet.emitted_spec_id is None:
        raise ValueError("recon planning route requires emitted_spec_id")
    source = engine.paths.runs_dir / stage_result.run_id / "generated_spec.md"
    spec = _read_spec_artifact(source)
    if spec.spec_id != packet.emitted_spec_id:
        raise ValueError("generated spec id must match recon packet emitted_spec_id")
    spec = _with_probe_spec_lineage(spec, packet)
    return QueueStore(engine.paths).enqueue_spec(spec)


def _read_task_artifact(path: Path) -> TaskDocument:
    raw = path.read_text(encoding="utf-8")
    if raw.lstrip().startswith("{"):
        return TaskDocument.model_validate_json(raw)
    return parse_work_document_as(raw, model=TaskDocument, path=path)


def _read_spec_artifact(path: Path) -> SpecDocument:
    raw = path.read_text(encoding="utf-8")
    if raw.lstrip().startswith("{"):
        return SpecDocument.model_validate_json(raw)
    return parse_work_document_as(raw, model=SpecDocument, path=path)


def _with_probe_task_lineage(
    task: TaskDocument,
    packet: ReconPacketDocument,
) -> TaskDocument:
    references = _append_required_references(
        task.references,
        probe_id=packet.probe_id,
        recon_packet_id=packet.recon_packet_id,
    )
    payload = task.model_dump(mode="python")
    payload.update(
        {
            "root_intake_kind": RootIntakeKind.PROBE,
            "root_intake_id": packet.probe_id,
            "references": references,
        }
    )
    return TaskDocument.model_validate(payload)


def _with_probe_spec_lineage(
    spec: SpecDocument,
    packet: ReconPacketDocument,
) -> SpecDocument:
    references = _append_required_references(
        spec.references,
        probe_id=packet.probe_id,
        recon_packet_id=packet.recon_packet_id,
    )
    payload = spec.model_dump(mode="python")
    payload.update(
        {
            "source_type": "probe",
            "source_id": packet.probe_id,
            "root_intake_kind": RootIntakeKind.PROBE,
            "root_intake_id": packet.probe_id,
            "root_spec_id": spec.root_spec_id or spec.spec_id,
            "references": references,
        }
    )
    return SpecDocument.model_validate(payload)


def _append_required_references(
    references: tuple[str, ...],
    *,
    probe_id: str,
    recon_packet_id: str,
) -> tuple[str, ...]:
    required = (
        f"millrace-agents/probes/active/{probe_id}.md",
        f"millrace-agents/recon/packets/{recon_packet_id}.md",
    )
    seen: set[str] = set()
    merged: list[str] = []
    for reference in (*references, *required):
        if reference in seen:
            continue
        seen.add(reference)
        merged.append(reference)
    return tuple(merged)


__all__ = ["apply_recon_router_decision", "is_recon_stage_result"]
