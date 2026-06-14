"""Closure-target persistence and canonical Arbiter contract-copy helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from millrace_ai.contracts import (
    ClosureEvidenceWindow,
    ClosureTargetState,
    LineageRunEvidence,
    Plane,
    TaskDocument,
)
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.events import RuntimeEventRecord, iter_runtime_events

from .paths import WorkspacePaths, workspace_paths
from .work_documents import read_work_document_as


def _resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise WorkspaceStateError(f"Expected object payload in {path}")
    return payload


def closure_target_state_path(
    target: WorkspacePaths | Path | str,
    *,
    root_spec_id: str,
) -> Path:
    paths = _resolve_paths(target)
    return paths.arbiter_targets_dir / f"{root_spec_id}.json"


def load_closure_target_state(
    target: WorkspacePaths | Path | str,
    *,
    root_spec_id: str,
) -> ClosureTargetState:
    path = closure_target_state_path(target, root_spec_id=root_spec_id)
    return ClosureTargetState.model_validate(_load_json(path))


def save_closure_target_state(
    target: WorkspacePaths | Path | str,
    state: ClosureTargetState,
) -> Path:
    paths = _resolve_paths(target)
    validated = ClosureTargetState.model_validate(state.model_dump(mode="python"))
    path = paths.arbiter_targets_dir / f"{validated.root_spec_id}.json"
    _atomic_write_text(path, validated.model_dump_json(indent=2) + "\n")
    return path


def write_closure_evidence_window(
    target: WorkspacePaths | Path | str,
    *,
    run_dir: Path,
    target_state: ClosureTargetState,
    request_id: str,
    run_id: str,
) -> Path:
    """Persist the request-specific closure evidence freshness window."""

    paths = _resolve_paths(target)
    window = build_closure_evidence_window(
        paths,
        target_state=target_state,
        request_id=request_id,
        run_id=run_id,
    )
    path = run_dir / "closure_evidence_window.json"
    _atomic_write_text(path, window.model_dump_json(indent=2) + "\n")
    return path


def build_closure_evidence_window(
    target: WorkspacePaths | Path | str,
    *,
    target_state: ClosureTargetState,
    request_id: str,
    run_id: str,
) -> ClosureEvidenceWindow:
    """Build a compact freshness window using streaming runtime events."""

    paths = _resolve_paths(target)
    previous_event = None
    completed_lineage: list[LineageRunEvidence] = []
    seen_previous_arbiter = target_state.last_arbiter_run_id is None

    for event in iter_runtime_events(paths):
        if event.event_type != "stage_completed":
            continue
        event_run_id = _string_data(event.data.get("run_id"))
        if (
            target_state.last_arbiter_run_id is not None
            and event_run_id == target_state.last_arbiter_run_id
        ):
            previous_event = event
            seen_previous_arbiter = True
            continue
        if not seen_previous_arbiter or target_state.last_arbiter_run_id is None:
            continue
        if event.data.get("plane") != Plane.EXECUTION.value:
            continue
        work_item_id = _string_data(event.data.get("work_item_id"))
        if not _is_completed_same_lineage_task(paths, work_item_id, target_state.root_spec_id):
            continue
        evidence = _lineage_run_evidence(event)
        if evidence is not None:
            completed_lineage.append(evidence)

    previous_request_id = (
        _string_data(previous_event.data.get("request_id")) if previous_event is not None else None
    )
    return ClosureEvidenceWindow(
        root_spec_id=target_state.root_spec_id,
        current_arbiter_run_id=run_id,
        current_arbiter_request_id=request_id,
        previous_arbiter={
            "run_id": target_state.last_arbiter_run_id,
            "request_id": previous_request_id,
            "verdict_path": target_state.latest_verdict_path,
            "report_path": target_state.latest_report_path,
            "completed_at": previous_event.occurred_at if previous_event is not None else None,
        },
        freshness_watermark_at=previous_event.occurred_at if previous_event is not None else None,
        completed_lineage_evidence=tuple(completed_lineage),
    )


def list_open_closure_target_states(
    target: WorkspacePaths | Path | str,
) -> tuple[ClosureTargetState, ...]:
    paths = _resolve_paths(target)
    states: list[ClosureTargetState] = []
    for path in sorted(paths.arbiter_targets_dir.glob("*.json")):
        state = ClosureTargetState.model_validate(_load_json(path))
        if state.closure_open:
            states.append(state)
    return tuple(states)


def write_canonical_idea_contract(
    target: WorkspacePaths | Path | str,
    *,
    root_idea_id: str,
    markdown: str,
) -> Path:
    paths = _resolve_paths(target)
    path = paths.arbiter_idea_contracts_dir / f"{root_idea_id}.md"
    _atomic_write_text(path, markdown)
    return path


def write_canonical_root_source_contract(
    target: WorkspacePaths | Path | str,
    *,
    root_source_kind: str,
    root_source_id: str,
    markdown: str,
) -> Path:
    paths = _resolve_paths(target)
    path = paths.arbiter_root_source_contracts_dir / root_source_kind / f"{root_source_id}.md"
    _atomic_write_text(path, markdown)
    return path


def write_canonical_root_spec_contract(
    target: WorkspacePaths | Path | str,
    *,
    root_spec_id: str,
    markdown: str,
) -> Path:
    paths = _resolve_paths(target)
    path = paths.arbiter_root_spec_contracts_dir / f"{root_spec_id}.md"
    _atomic_write_text(path, markdown)
    return path


def _lineage_run_evidence(event: RuntimeEventRecord) -> LineageRunEvidence | None:
    data = event.data
    run_id = _string_data(data.get("run_id"))
    if run_id is None:
        return None
    request_id = _string_data(data.get("request_id"))
    stage_result_path = (
        f"millrace-agents/runs/{run_id}/stage_results/{request_id}.json"
        if request_id is not None
        else None
    )
    return LineageRunEvidence(
        run_id=run_id,
        request_id=request_id,
        plane=data.get("plane"),
        stage=data.get("stage"),
        work_item_family_id=_string_data(data.get("work_item_family_id")),
        work_item_id=_string_data(data.get("work_item_id")),
        terminal_result=_string_data(data.get("terminal_result")),
        completed_at=event.occurred_at,
        stage_result_path=stage_result_path,
    )


def _is_completed_same_lineage_task(
    paths: WorkspacePaths,
    work_item_id: str | None,
    root_spec_id: str,
) -> bool:
    if work_item_id is None:
        return False
    task_path = paths.tasks_done_dir / f"{work_item_id}.md"
    if not task_path.is_file():
        return False
    task = read_work_document_as(task_path, model=TaskDocument)
    return task.root_spec_id == root_spec_id


def _string_data(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "build_closure_evidence_window",
    "closure_target_state_path",
    "list_open_closure_target_states",
    "load_closure_target_state",
    "save_closure_target_state",
    "write_closure_evidence_window",
    "write_canonical_idea_contract",
    "write_canonical_root_source_contract",
    "write_canonical_root_spec_contract",
]
