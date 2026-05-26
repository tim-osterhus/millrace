"""Run-trace graph persistence and fallback inspection helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.contracts.run_trace import (
    RunTraceArtifactRef,
    RunTraceEdge,
    RunTraceGraph,
    RunTraceNode,
    RunTraceSpawnedWorkKind,
    RunTraceSpawnedWorkRef,
    RunTraceStatus,
)
from millrace_ai.events import write_runtime_event
from millrace_ai.paths import WorkspacePaths
from millrace_ai.router import RouterAction, RouterDecision


def trace_path_for_run_dir(run_dir: Path) -> Path:
    return run_dir / "run_trace.json"


def inspect_run_trace(run_dir: Path | str) -> RunTraceGraph:
    """Read a run trace, deriving a fallback from stage results when needed."""

    resolved_run_dir = Path(run_dir).expanduser().resolve()
    trace_path = trace_path_for_run_dir(resolved_run_dir)
    if trace_path.is_file():
        try:
            return RunTraceGraph.model_validate_json(trace_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError, ValueError) as exc:
            fallback = derive_run_trace_from_stage_results(
                resolved_run_dir,
                status="malformed",
                notes=(f"run_trace.json malformed: {exc}",),
            )
            return fallback
    return derive_run_trace_from_stage_results(
        resolved_run_dir,
        status="incomplete",
        notes=("derived from stage result artifacts",),
    )


def inspect_run_trace_id(
    paths: WorkspacePaths,
    run_id: str,
) -> RunTraceGraph | None:
    if "/" in run_id or "\\" in run_id or run_id in {"", ".", ".."}:
        return None
    run_dir = paths.runs_dir / run_id
    if not run_dir.is_dir():
        return None
    return inspect_run_trace(run_dir)


def derive_run_trace_from_stage_results(
    run_dir: Path,
    *,
    status: RunTraceStatus,
    notes: tuple[str, ...],
) -> RunTraceGraph:
    stage_results_dir = run_dir / "stage_results"
    stage_result_paths = (
        sorted(path for path in stage_results_dir.iterdir() if path.suffix == ".json")
        if stage_results_dir.is_dir()
        else []
    )
    nodes: list[RunTraceNode] = []
    collected_notes = list(notes)
    for index, stage_result_path in enumerate(stage_result_paths, start=1):
        try:
            stage_result = StageResultEnvelope.model_validate_json(
                stage_result_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            collected_notes.append(f"{stage_result_path.name}: invalid stage result: {exc}")
            continue
        nodes.append(
            _node_from_stage_result(
                run_dir=run_dir,
                stage_result=stage_result,
                stage_result_path=stage_result_path,
                fallback_index=index,
            )
        )

    first = nodes[0] if nodes else None
    latest = nodes[-1] if nodes else None
    return RunTraceGraph(
        run_id=run_dir.name,
        run_dir=str(run_dir),
        compiled_plan_id=latest.compiled_plan_id if latest else None,
        mode_id=latest.mode_id if latest else None,
        request_kind=latest.request_kind if latest else None,
        work_item_family_id=latest.work_item_family_id if latest else None,
        work_item_kind=latest.work_item_kind if latest else None,
        work_item_id=latest.work_item_id if latest else None,
        closure_target_root_spec_id=latest.closure_target_root_spec_id if latest else None,
        closure_target_root_source_kind=(
            latest.closure_target_root_source_kind if latest else None
        ),
        closure_target_root_source_id=latest.closure_target_root_source_id if latest else None,
        closure_target_root_source_path=(
            latest.closure_target_root_source_path if latest else None
        ),
        status=status,
        started_at=first.started_at if first else None,
        completed_at=latest.completed_at if latest else None,
        duration_seconds=(
            (latest.completed_at - first.started_at).total_seconds()
            if first is not None and latest is not None
            else None
        ),
        nodes=tuple(nodes),
        edges=(),
        notes=tuple(collected_notes),
        generated_at=datetime.now(timezone.utc),
    )


def upsert_stage_result_trace_node(
    paths: WorkspacePaths,
    *,
    run_dir: Path,
    stage_result: StageResultEnvelope,
    stage_result_path: Path,
) -> None:
    """Best-effort trace-node update after stage result persistence."""

    try:
        trace = _load_or_derive_for_update(run_dir)
        node = _node_from_stage_result(
            run_dir=run_dir,
            stage_result=stage_result,
            stage_result_path=stage_result_path,
        )
        nodes = [existing for existing in trace.nodes if existing.trace_node_id != node.trace_node_id]
        nodes.append(node)
        nodes.sort(key=lambda item: (item.started_at, item.completed_at, item.trace_node_id))
        edges = tuple(_link_edge_target(edge, node) for edge in trace.edges)
        updated = _trace_with(
            trace,
            stage_result=stage_result,
            nodes=tuple(nodes),
            edges=edges,
            status="active",
            notes=_without_derived_note(trace.notes),
        )
        _write_trace(trace_path_for_run_dir(run_dir), updated)
    except Exception as exc:  # pragma: no cover - defensive path
        write_runtime_event(
            paths,
            event_type="run_trace_write_failed",
            data={"run_id": stage_result.run_id, "phase": "node", "error": str(exc)},
        )


def record_router_decision_trace(
    paths: WorkspacePaths,
    *,
    run_dir: Path,
    stage_result: StageResultEnvelope,
    decision: RouterDecision,
    spawned_work: Iterable[RunTraceSpawnedWorkRef] = (),
) -> None:
    """Best-effort trace-edge update after authoritative routing."""

    try:
        trace = _load_or_derive_for_update(run_dir)
        source_node_id = _trace_node_id(stage_result, None)
        edge = _edge_from_decision(
            stage_result=stage_result,
            decision=decision,
            source_trace_node_id=source_node_id,
            spawned_work=tuple(spawned_work),
        )
        edges = [existing for existing in trace.edges if existing.trace_edge_id != edge.trace_edge_id]
        edges.append(edge)
        status = _status_from_decision(decision)
        updated = _trace_with(
            trace,
            stage_result=stage_result,
            edges=tuple(edges),
            status=status,
            notes=_without_derived_note(trace.notes),
        )
        _write_trace(trace_path_for_run_dir(run_dir), updated)
    except Exception as exc:  # pragma: no cover - defensive path
        write_runtime_event(
            paths,
            event_type="run_trace_write_failed",
            data={"run_id": stage_result.run_id, "phase": "edge", "error": str(exc)},
        )


def spawned_work_ref_from_path(
    path: Path,
    *,
    source_stage_result: StageResultEnvelope,
    reason: str,
    compiled_plan: CompiledRunPlan | None = None,
) -> RunTraceSpawnedWorkRef:
    item_id = path.stem
    family_id = _spawned_kind_from_effect_rule(
        path,
        source_stage_result,
        compiled_plan=compiled_plan,
    ) or _spawned_kind_from_path(path, compiled_plan=compiled_plan)
    return RunTraceSpawnedWorkRef(
        family_id=family_id,
        kind=family_id,
        item_id=item_id,
        path=path.as_posix(),
        reason=reason,
        source_stage_node_id=source_stage_result.node_id,
        source_terminal_result=source_stage_result.terminal_result.value,
    )


def _load_or_derive_for_update(run_dir: Path) -> RunTraceGraph:
    trace_path = trace_path_for_run_dir(run_dir)
    if trace_path.is_file():
        try:
            return RunTraceGraph.model_validate_json(trace_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError, json.JSONDecodeError):
            return derive_run_trace_from_stage_results(
                run_dir,
                status="malformed",
                notes=("run_trace.json malformed; regenerated from stage result artifacts",),
            )
    return derive_run_trace_from_stage_results(
        run_dir,
        status="incomplete",
        notes=("derived from stage result artifacts",),
    )


def _node_from_stage_result(
    *,
    run_dir: Path,
    stage_result: StageResultEnvelope,
    stage_result_path: Path,
    fallback_index: int | None = None,
) -> RunTraceNode:
    request_id = _trace_node_id(stage_result, fallback_index)
    return RunTraceNode(
        trace_node_id=request_id,
        run_id=stage_result.run_id,
        request_id=request_id,
        plane=stage_result.plane,
        stage=stage_result.stage.value,
        node_id=stage_result.node_id,
        stage_kind_id=stage_result.stage_kind_id,
        compiled_plan_id=_string_metadata(stage_result, "compiled_plan_id"),
        mode_id=_string_metadata(stage_result, "mode_id"),
        request_kind=_string_metadata(stage_result, "request_kind"),
        work_item_family_id=stage_result.work_item_family_id,
        work_item_kind=(
            stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
        ),
        work_item_id=stage_result.work_item_id,
        closure_target_root_spec_id=_string_metadata(
            stage_result,
            "closure_target_root_spec_id",
        ),
        closure_target_root_source_kind=_string_metadata(
            stage_result,
            "closure_target_root_source_kind",
        ),
        closure_target_root_source_id=_string_metadata(
            stage_result,
            "closure_target_root_source_id",
        ),
        closure_target_root_source_path=_string_metadata(
            stage_result,
            "closure_target_root_source_path",
        ),
        terminal_result=stage_result.terminal_result.value,
        result_class=stage_result.result_class,
        failure_class=_string_metadata(stage_result, "failure_class"),
        runner_name=stage_result.runner_name,
        model_name=stage_result.model_name,
        thinking_level=stage_result.thinking_level,
        model_reasoning_effort=stage_result.model_reasoning_effort,
        model_assignment_alias_id=stage_result.model_assignment_alias_id,
        model_assignment_source=stage_result.model_assignment_source,
        started_at=stage_result.started_at,
        completed_at=stage_result.completed_at,
        duration_seconds=stage_result.duration_seconds,
        token_usage=stage_result.token_usage,
        artifacts=_artifact_refs(run_dir, stage_result, stage_result_path),
    )


def _artifact_refs(
    run_dir: Path,
    stage_result: StageResultEnvelope,
    stage_result_path: Path,
) -> tuple[RunTraceArtifactRef, ...]:
    paths: list[tuple[str, str]] = [(_normalize_run_relative_path(run_dir, stage_result_path), "stage_result")]
    optional_paths = (
        (stage_result.prompt_artifact, "prompt"),
        (stage_result.stdout_path, "stdout"),
        (stage_result.stderr_path, "stderr"),
        (stage_result.report_artifact, "report"),
    )
    for path_value, kind in optional_paths:
        if path_value:
            paths.append((_normalize_run_relative_path(run_dir, Path(path_value)), kind))
    for path_value in stage_result.artifact_paths:
        paths.append((_normalize_run_relative_path(run_dir, Path(path_value)), _artifact_kind(path_value)))

    seen: set[str] = set()
    refs: list[RunTraceArtifactRef] = []
    for relative_path, kind in paths:
        if relative_path in seen:
            continue
        seen.add(relative_path)
        absolute = run_dir / relative_path
        refs.append(
            RunTraceArtifactRef(
                path=relative_path,
                kind=kind,
                size_bytes=absolute.stat().st_size if absolute.is_file() else None,
            )
        )
    return tuple(refs)


def _edge_from_decision(
    *,
    stage_result: StageResultEnvelope,
    decision: RouterDecision,
    source_trace_node_id: str,
    spawned_work: tuple[RunTraceSpawnedWorkRef, ...],
) -> RunTraceEdge:
    target_node_id = decision.next_node_id if decision.action is RouterAction.RUN_STAGE else None
    terminal_state_id = None if target_node_id is not None else _terminal_state_id(stage_result, decision)
    target_or_terminal = target_node_id or f"terminal:{terminal_state_id}" or decision.action.value
    return RunTraceEdge(
        trace_edge_id=(
            f"{source_trace_node_id}--{stage_result.terminal_result.value}--{target_or_terminal}"
        ),
        source_trace_node_id=source_trace_node_id,
        outcome=stage_result.terminal_result.value,
        edge_kind=_edge_kind_from_decision(stage_result, decision),
        target_node_id=target_node_id,
        terminal_state_id=terminal_state_id,
        spawned_work=spawned_work,
        decision_reason=decision.reason,
        decided_at=stage_result.completed_at,
    )


def _edge_kind_from_decision(
    stage_result: StageResultEnvelope,
    decision: RouterDecision,
) -> str:
    if (
        decision.action is RouterAction.RUN_STAGE
        and (
            stage_result.metadata.get("runtime_effect_recovery_action") == "route_to_node"
            or decision.reason.startswith("runtime_effect_failure:")
        )
    ):
        return "runtime_effect_recovery"
    if decision.action is RouterAction.RUN_STAGE and decision.reason.startswith("runtime_exception:"):
        return "runtime_repair"
    return decision.action.value


def _trace_with(
    trace: RunTraceGraph,
    *,
    stage_result: StageResultEnvelope,
    nodes: tuple[RunTraceNode, ...] | None = None,
    edges: tuple[RunTraceEdge, ...] | None = None,
    status: RunTraceStatus,
    notes: tuple[str, ...] | None = None,
) -> RunTraceGraph:
    new_nodes = trace.nodes if nodes is None else nodes
    first = new_nodes[0] if new_nodes else None
    latest = new_nodes[-1] if new_nodes else None
    return trace.model_copy(
        update={
            "compiled_plan_id": _string_metadata(stage_result, "compiled_plan_id")
            or trace.compiled_plan_id,
            "mode_id": _string_metadata(stage_result, "mode_id") or trace.mode_id,
            "request_kind": _string_metadata(stage_result, "request_kind") or trace.request_kind,
            "work_item_family_id": stage_result.work_item_family_id,
            "work_item_kind": (
                stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
            ),
            "work_item_id": stage_result.work_item_id,
            "closure_target_root_spec_id": _string_metadata(
                stage_result,
                "closure_target_root_spec_id",
            )
            or trace.closure_target_root_spec_id,
            "closure_target_root_source_kind": _string_metadata(
                stage_result,
                "closure_target_root_source_kind",
            )
            or trace.closure_target_root_source_kind,
            "closure_target_root_source_id": _string_metadata(
                stage_result,
                "closure_target_root_source_id",
            )
            or trace.closure_target_root_source_id,
            "closure_target_root_source_path": _string_metadata(
                stage_result,
                "closure_target_root_source_path",
            )
            or trace.closure_target_root_source_path,
            "status": status,
            "started_at": first.started_at if first else trace.started_at,
            "completed_at": latest.completed_at if latest else trace.completed_at,
            "duration_seconds": (
                (latest.completed_at - first.started_at).total_seconds()
                if first is not None and latest is not None
                else trace.duration_seconds
            ),
            "nodes": new_nodes,
            "edges": trace.edges if edges is None else edges,
            "notes": trace.notes if notes is None else notes,
            "generated_at": datetime.now(timezone.utc),
        }
    )


def _write_trace(trace_path: Path, trace: RunTraceGraph) -> None:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = trace_path.with_name(f"{trace_path.name}.tmp")
    tmp_path.write_text(trace.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(trace_path)


def _trace_node_id(stage_result: StageResultEnvelope, fallback_index: int | None) -> str:
    request_id = _string_metadata(stage_result, "request_id")
    if request_id:
        return request_id
    if fallback_index is not None:
        return f"stage-result-{fallback_index:04d}"
    return f"{stage_result.stage.value}-{stage_result.terminal_result.value.lower()}"


def _terminal_state_id(stage_result: StageResultEnvelope, decision: RouterDecision) -> str | None:
    if decision.action is RouterAction.BLOCKED:
        return "blocked"
    return str(stage_result.terminal_result.value).lower()


def _status_from_decision(decision: RouterDecision) -> RunTraceStatus:
    if decision.action is RouterAction.RUN_STAGE:
        return "active"
    if decision.action is RouterAction.HANDOFF:
        return "handoff"
    if decision.action is RouterAction.BLOCKED:
        return "blocked"
    return "complete"


def _link_edge_target(edge: RunTraceEdge, node: RunTraceNode) -> RunTraceEdge:
    if edge.target_node_id == node.node_id and edge.target_trace_node_id is None:
        return edge.model_copy(update={"target_trace_node_id": node.trace_node_id})
    return edge


def _spawned_kind_from_path(
    path: Path,
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> RunTraceSpawnedWorkKind:
    if compiled_plan is not None:
        resolved_path = path.resolve()
        matches: list[tuple[int, str]] = []
        for family in compiled_plan.work_item_families_by_id.values():
            for relative_dir in (
                family.queue_dirs.queue,
                family.queue_dirs.active,
                family.queue_dirs.done,
                family.queue_dirs.blocked,
                family.queue_dirs.canceled,
                family.queue_dirs.superseded,
            ):
                if relative_dir is None:
                    continue
                marker = Path(relative_dir)
                if marker.parts and _path_has_relative_suffix(resolved_path, marker):
                    matches.append((len(marker.parts), family.family_id))
        if matches:
            return sorted(matches, reverse=True)[0][1]
    parts = set(path.parts)
    if "blueprints" in parts and "drafts" in parts:
        return "blueprint_draft"
    if "learning" in parts:
        return "learning_request"
    if "incidents" in parts:
        return "incident"
    if "specs" in parts:
        return "spec"
    return "task"


def _spawned_kind_from_effect_rule(
    path: Path,
    stage_result: StageResultEnvelope,
    *,
    compiled_plan: CompiledRunPlan | None,
) -> RunTraceSpawnedWorkKind | None:
    if compiled_plan is None:
        return None
    handler_id = stage_result.metadata.get("runtime_effect_handler_id")
    if not isinstance(handler_id, str):
        return None
    terminal_result = stage_result.terminal_result.value
    for rule in getattr(compiled_plan, "runtime_effect_rules", ()):
        if getattr(rule, "handler_id", None) != handler_id:
            continue
        if terminal_result not in getattr(rule, "on_outcomes", ()):
            continue
        destination_family_id = getattr(rule, "destination_family_id", None)
        if destination_family_id and _path_matches_family_queue(
            path,
            compiled_plan=compiled_plan,
            family_id=str(destination_family_id),
        ):
            return str(destination_family_id)
    return None


def _path_matches_family_queue(
    path: Path,
    *,
    compiled_plan: CompiledRunPlan,
    family_id: str,
) -> bool:
    family = compiled_plan.work_item_families_by_id.get(family_id)
    if family is None:
        return False
    return _path_has_relative_suffix(path.resolve(), Path(family.queue_dirs.queue))


def _path_has_relative_suffix(path: Path, relative: Path) -> bool:
    relative_parts = relative.parts
    if not relative_parts or len(path.parts) < len(relative_parts):
        return False
    for index in range(0, len(path.parts) - len(relative_parts) + 1):
        if path.parts[index : index + len(relative_parts)] == relative_parts:
            return True
    return False


def _artifact_kind(path: str) -> str:
    name = Path(path).name
    if name.endswith(".json"):
        return "json"
    if name.endswith(".md"):
        return "report"
    if "stdout" in name:
        return "stdout"
    if "stderr" in name:
        return "stderr"
    return "artifact"


def _normalize_run_relative_path(run_dir: Path, path_value: Path) -> str:
    candidate = path_value if path_value.is_absolute() else run_dir / path_value
    try:
        return candidate.resolve().relative_to(run_dir.resolve()).as_posix()
    except (OSError, ValueError):
        return path_value.as_posix()


def _without_derived_note(notes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(note for note in notes if note != "derived from stage result artifacts")


def _string_metadata(stage_result: StageResultEnvelope, key: str) -> str | None:
    value = stage_result.metadata.get(key)
    return value if isinstance(value, str) else None


__all__ = [
    "derive_run_trace_from_stage_results",
    "inspect_run_trace",
    "inspect_run_trace_id",
    "record_router_decision_trace",
    "spawned_work_ref_from_path",
    "trace_path_for_run_dir",
    "upsert_stage_result_trace_node",
]
