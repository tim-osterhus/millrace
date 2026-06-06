"""Runtime-owned application of Recon probe routing results."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import (
    ReconDecision,
    ReconPacketDocument,
    RootIntakeKind,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.queue_store import QueueStore
from millrace_ai.recon_packets import render_recon_packet
from millrace_ai.router import RouterAction, RouterDecision

from .artifact_contracts import (
    parse_resolved_run_artifact_as,
    resolve_run_artifact,
)
from .work_item_transitions import apply_blocked_router_decision, apply_idle_router_decision

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan
    from millrace_ai.runtime.engine import RuntimeEngine


class ReconHandoffInvalidError(RuntimeError):
    """Raised when Recon produced artifacts that do not satisfy its handoff contract."""


def is_recon_stage_result(stage_result: StageResultEnvelope) -> bool:
    """Return whether a stage result belongs to Recon probe intake."""

    return stage_result.stage_kind_id == "recon" and stage_result.work_item_kind is WorkItemKind.PROBE


def apply_recon_router_decision(
    engine: RuntimeEngine,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
    *,
    stage_result_path: Path | None = None,
    compiled_plan: CompiledRunPlan | None = None,
) -> tuple[Path, ...]:
    """Persist Recon artifacts, enqueue routed work, then finish the active probe."""

    recon_route = _recon_route_from_decision(decision, stage_result, compiled_plan=compiled_plan)
    if recon_route in {"to_execution", "to_planning", "noop"} and decision.action is not RouterAction.IDLE:
        raise ValueError("successful recon terminal results require an idle router decision")
    if recon_route == "blocked" and decision.action is not RouterAction.BLOCKED:
        raise ValueError("blocked recon terminal results require a blocked router decision")

    try:
        packet = _read_and_persist_packet(engine, stage_result, compiled_plan=compiled_plan)
        _validate_packet_for_stage_result(packet, stage_result, recon_route)

        spawned: tuple[Path, ...] = ()
        if recon_route == "to_execution":
            spawned = (
                _enqueue_generated_task(
                    engine,
                    stage_result,
                    packet,
                    compiled_plan=compiled_plan,
                ),
            )
            apply_idle_router_decision(engine, stage_result, decision=decision)
            return spawned
        if recon_route == "to_planning":
            spawned = (
                _enqueue_generated_spec(
                    engine,
                    stage_result,
                    packet,
                    compiled_plan=compiled_plan,
                ),
            )
            apply_idle_router_decision(engine, stage_result, decision=decision)
            return spawned
        if recon_route == "noop":
            apply_idle_router_decision(engine, stage_result, decision=decision)
            return ()

        apply_blocked_router_decision(
            engine,
            decision,
            stage_result,
            stage_result_path=stage_result_path,
        )
        return ()
    except Exception as exc:
        raise ReconHandoffInvalidError("Recon handoff artifacts failed validation") from exc


def _read_and_persist_packet(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
    *,
    compiled_plan: CompiledRunPlan | None,
) -> ReconPacketDocument:
    run_dir = engine.paths.runs_dir / stage_result.run_id
    packet = parse_resolved_run_artifact_as(
        resolve_run_artifact(compiled_plan, "recon_packet", run_dir),
        ReconPacketDocument,
    )
    destination = engine.paths.recon_packets_dir / f"{packet.recon_packet_id}.md"
    if destination.exists():
        raise ValueError(f"recon packet already exists: {destination}")
    destination.write_text(render_recon_packet(packet), encoding="utf-8")
    return packet


def _validate_packet_for_stage_result(
    packet: ReconPacketDocument,
    stage_result: StageResultEnvelope,
    recon_route: str,
) -> None:
    if packet.probe_id != stage_result.work_item_id:
        raise ValueError("recon packet probe_id must match active probe")
    expected_decision = {
        "to_execution": ReconDecision.TO_EXECUTION,
        "to_planning": ReconDecision.TO_PLANNING,
        "noop": ReconDecision.NOOP,
        "blocked": ReconDecision.BLOCKED,
    }[recon_route]
    if packet.decision is not expected_decision:
        raise ValueError("recon packet decision must match terminal result")


def _recon_route_from_decision(
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> str:
    runtime_operation_id = decision.runtime_operation_id
    if runtime_operation_id is not None and compiled_plan is not None:
        operation = compiled_plan.runtime_operations_by_id.get(runtime_operation_id)
        if operation is None:
            raise ValueError(
                "recon terminal action declares unknown runtime_operation_id "
                f"{runtime_operation_id}; recompile or update terminal action assets"
            )
        if "terminal_action" not in operation.allowed_contexts:
            raise ValueError(
                f"recon terminal action runtime operation {runtime_operation_id} "
                f"does not allow terminal_action context"
            )
    route = _RECON_ROUTE_BY_RUNTIME_OPERATION.get(runtime_operation_id or "")
    if route is not None:
        return route
    terminal_state = decision.terminal_state_id or "unknown"
    terminal_action = decision.terminal_action_id or "unknown"
    outcome = stage_result.terminal_result.value
    if runtime_operation_id is None:
        raise ValueError(
            "recon terminal result requires terminal action runtime_operation_id; "
            "recompile or update the compiled plan so terminal action "
            f"{terminal_action} selected by terminal state {terminal_state} "
            f"declares a Recon runtime operation for outcome {outcome}"
        )
    raise ValueError(
        "recon terminal action declares unsupported runtime_operation_id "
        f"{runtime_operation_id}; recompile or update terminal action assets"
    )


def _enqueue_generated_task(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
    packet: ReconPacketDocument,
    *,
    compiled_plan: CompiledRunPlan | None,
) -> Path:
    if packet.emitted_task_id is None:
        raise ValueError("recon execution route requires emitted_task_id")
    run_dir = engine.paths.runs_dir / stage_result.run_id
    task = parse_resolved_run_artifact_as(
        resolve_run_artifact(compiled_plan, "generated_task", run_dir),
        TaskDocument,
    )
    if task.task_id != packet.emitted_task_id:
        raise ValueError("generated task id must match recon packet emitted_task_id")
    task = _with_probe_task_lineage(task, packet)
    return QueueStore(engine.paths).enqueue_task(task)


def _enqueue_generated_spec(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
    packet: ReconPacketDocument,
    *,
    compiled_plan: CompiledRunPlan | None,
) -> Path:
    if packet.emitted_spec_id is None:
        raise ValueError("recon planning route requires emitted_spec_id")
    run_dir = engine.paths.runs_dir / stage_result.run_id
    spec = parse_resolved_run_artifact_as(
        resolve_run_artifact(compiled_plan, "generated_spec", run_dir),
        SpecDocument,
    )
    if spec.spec_id != packet.emitted_spec_id:
        raise ValueError("generated spec id must match recon packet emitted_spec_id")
    spec = _with_probe_spec_lineage(spec, packet)
    return QueueStore(engine.paths).enqueue_spec(spec)


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


__all__ = ["ReconHandoffInvalidError", "apply_recon_router_decision", "is_recon_stage_result"]


_RECON_ROUTE_BY_RUNTIME_OPERATION = {
    "recon.enqueue_task": "to_execution",
    "recon.enqueue_spec": "to_planning",
    "recon.noop": "noop",
    "recon.block_work_item": "blocked",
}
