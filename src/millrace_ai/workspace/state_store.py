"""Runtime state persistence and status mutation helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from millrace_ai.contracts import (
    RecoveryCounterEntry,
    RecoveryCounters,
    RuntimeSnapshot,
    WorkItemKind,
)
from millrace_ai.contracts.work_refs import coerce_family_and_kind
from millrace_ai.errors import WorkspaceStateError

from .paths import WorkspacePaths, workspace_paths

_RECONCILIATION_EXPORTS = {
    "ReconciliationSignal",
    "collect_reconciliation_signals",
    "running_status_marker_for_stage",
}


def _state_reconciliation_module() -> Any:
    import importlib

    return importlib.import_module("millrace_ai.workspace.state_reconciliation")


def __getattr__(name: str) -> Any:
    if name not in _RECONCILIATION_EXPORTS:
        raise AttributeError(name)
    value = getattr(_state_reconciliation_module(), name)
    globals()[name] = value
    return value


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


def _save_model(path: Path, model: RuntimeSnapshot | RecoveryCounters) -> None:
    _atomic_write_text(path, model.model_dump_json(indent=2) + "\n")


def load_snapshot(target: WorkspacePaths | Path | str) -> RuntimeSnapshot:
    paths = _resolve_paths(target)
    return RuntimeSnapshot.model_validate(_load_json(paths.runtime_snapshot_file))


def save_snapshot(target: WorkspacePaths | Path | str, snapshot: RuntimeSnapshot) -> None:
    paths = _resolve_paths(target)
    validated = RuntimeSnapshot.model_validate(snapshot.model_dump(mode="python"))
    _save_model(paths.runtime_snapshot_file, validated)


def load_recovery_counters(target: WorkspacePaths | Path | str) -> RecoveryCounters:
    paths = _resolve_paths(target)
    return RecoveryCounters.model_validate(_load_json(paths.recovery_counters_file))


def save_recovery_counters(
    target: WorkspacePaths | Path | str,
    counters: RecoveryCounters,
) -> None:
    paths = _resolve_paths(target)
    validated = RecoveryCounters.model_validate(counters.model_dump(mode="python"))
    _save_model(paths.recovery_counters_file, validated)


def load_execution_status(target: WorkspacePaths | Path | str) -> str:
    paths = _resolve_paths(target)
    marker = paths.execution_status_file.read_text(encoding="utf-8")
    return _state_reconciliation_module().normalize_execution_status_marker(marker)


def load_planning_status(target: WorkspacePaths | Path | str) -> str:
    paths = _resolve_paths(target)
    marker = paths.planning_status_file.read_text(encoding="utf-8")
    return _state_reconciliation_module().normalize_planning_status_marker(marker)


def load_learning_status(target: WorkspacePaths | Path | str) -> str:
    paths = _resolve_paths(target)
    marker = paths.learning_status_file.read_text(encoding="utf-8")
    return _state_reconciliation_module().normalize_learning_status_marker(marker)


def set_execution_status(target: WorkspacePaths | Path | str, marker: str) -> str:
    paths = _resolve_paths(target)
    normalized = _state_reconciliation_module().normalize_execution_status_marker(marker)
    _atomic_write_text(paths.execution_status_file, normalized + "\n")
    return normalized


def set_planning_status(target: WorkspacePaths | Path | str, marker: str) -> str:
    paths = _resolve_paths(target)
    normalized = _state_reconciliation_module().normalize_planning_status_marker(marker)
    _atomic_write_text(paths.planning_status_file, normalized + "\n")
    return normalized


def set_learning_status(target: WorkspacePaths | Path | str, marker: str) -> str:
    paths = _resolve_paths(target)
    normalized = _state_reconciliation_module().normalize_learning_status_marker(marker)
    _atomic_write_text(paths.learning_status_file, normalized + "\n")
    return normalized


def _update_counter_entries(
    entries: tuple[RecoveryCounterEntry, ...],
    *,
    failure_class: str,
    work_item_family_id: str,
    work_item_kind: WorkItemKind | None,
    work_item_id: str,
    now: datetime,
) -> tuple[tuple[RecoveryCounterEntry, ...], RecoveryCounterEntry]:
    updated_entry: RecoveryCounterEntry | None = None
    mutable_entries = list(entries)

    for index, entry in enumerate(mutable_entries):
        if (
            entry.failure_class == failure_class
            and entry.work_item_family_id == work_item_family_id
            and entry.work_item_id == work_item_id
        ):
            updated_counters = dict(entry.counters)
            updated_counters["troubleshoot_attempt_count"] = (
                updated_counters.get("troubleshoot_attempt_count", 0) + 1
            )
            updated_entry = entry.model_copy(
                update={
                    "counters": updated_counters,
                    "last_updated_at": now,
                }
            )
            mutable_entries[index] = updated_entry
            break

    if updated_entry is None:
        updated_entry = RecoveryCounterEntry(
            failure_class=failure_class,
            work_item_family_id=work_item_family_id,
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            counters={"troubleshoot_attempt_count": 1},
            last_updated_at=now,
        )
        mutable_entries.append(updated_entry)

    return tuple(mutable_entries), updated_entry


def increment_troubleshoot_attempt(
    target: WorkspacePaths | Path | str,
    *,
    failure_class: str,
    work_item_family_id: str | None = None,
    work_item_kind: WorkItemKind | str | None = None,
    work_item_id: str,
    now: datetime | None = None,
) -> RecoveryCounterEntry:
    paths = _resolve_paths(target)
    counters = load_recovery_counters(paths)
    timestamp = now or datetime.now(timezone.utc)
    family_id, kind = coerce_family_and_kind(
        family_id=work_item_family_id,
        work_item_kind=work_item_kind,
    )
    if family_id is None:
        raise ValueError("work_item_family_id or work_item_kind is required")

    updated_entries, updated_entry = _update_counter_entries(
        counters.entries,
        failure_class=failure_class,
        work_item_family_id=family_id,
        work_item_kind=kind,
        work_item_id=work_item_id,
        now=timestamp,
    )
    save_recovery_counters(paths, RecoveryCounters(entries=updated_entries))
    return updated_entry


def reset_forward_progress_counters(
    target: WorkspacePaths | Path | str,
    *,
    work_item_family_id: str | None = None,
    work_item_kind: WorkItemKind | str | None = None,
    work_item_id: str,
) -> None:
    paths = _resolve_paths(target)
    counters = load_recovery_counters(paths)
    family_id, _kind = coerce_family_and_kind(
        family_id=work_item_family_id,
        work_item_kind=work_item_kind,
    )
    if family_id is None:
        raise ValueError("work_item_family_id or work_item_kind is required")

    remaining = tuple(
        entry
        for entry in counters.entries
        if not (entry.work_item_family_id == family_id and entry.work_item_id == work_item_id)
    )
    save_recovery_counters(paths, RecoveryCounters(entries=remaining))


__all__ = [
    "increment_troubleshoot_attempt",
    "load_execution_status",
    "load_learning_status",
    "load_planning_status",
    "load_recovery_counters",
    "load_snapshot",
    "reset_forward_progress_counters",
    "save_recovery_counters",
    "save_snapshot",
    "set_execution_status",
    "set_learning_status",
    "set_planning_status",
]
