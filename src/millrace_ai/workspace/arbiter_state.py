"""Closure-target persistence and canonical Arbiter contract-copy helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from millrace_ai.contracts import ClosureTargetState
from millrace_ai.errors import WorkspaceStateError

from .paths import WorkspacePaths, workspace_paths


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


__all__ = [
    "closure_target_state_path",
    "list_open_closure_target_states",
    "load_closure_target_state",
    "save_closure_target_state",
    "write_canonical_idea_contract",
    "write_canonical_root_spec_contract",
]
