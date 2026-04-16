"""Filesystem-backed queue ownership and lifecycle helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from millrace_ai.contracts import IncidentDocument, SpecDocument, TaskDocument, WorkItemKind
from millrace_ai.errors import QueueStateError
from millrace_ai.paths import WorkspacePaths, bootstrap_workspace, workspace_paths
from millrace_ai.work_documents import parse_work_document_as, render_work_document

_DocT = TypeVar("_DocT", TaskDocument, SpecDocument, IncidentDocument)


@dataclass(frozen=True, slots=True)
class QueueClaim:
    """Represents ownership of a newly-claimed work item."""

    work_item_kind: WorkItemKind
    work_item_id: str
    path: Path


@dataclass(frozen=True, slots=True)
class StaleActiveState:
    """Result payload from stale active-state checks."""

    is_stale: bool
    reasons: tuple[str, ...]


class QueueStore:
    """Queue operations for tasks, specs, and incidents."""

    def __init__(self, target: WorkspacePaths | Path | str) -> None:
        paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
        self.paths = bootstrap_workspace(paths)

    def enqueue_task(self, doc: TaskDocument) -> Path:
        self._ensure_unique_task_id(doc.task_id)
        destination = self.paths.tasks_queue_dir / f"{doc.task_id}.md"
        self._write_model(destination, doc)
        return destination

    def enqueue_spec(self, doc: SpecDocument) -> Path:
        self._ensure_unique_spec_id(doc.spec_id)
        destination = self.paths.specs_queue_dir / f"{doc.spec_id}.md"
        self._write_model(destination, doc)
        return destination

    def enqueue_incident(self, doc: IncidentDocument) -> Path:
        self._ensure_unique_incident_id(doc.incident_id)
        destination = self.paths.incidents_incoming_dir / f"{doc.incident_id}.md"
        self._write_model(destination, doc)
        return destination

    def claim_next_execution_task(self) -> QueueClaim | None:
        active = self._list_markdown_files(self.paths.tasks_active_dir)
        if len(active) > 1:
            raise QueueStateError("Multiple active execution tasks found")
        if active:
            return None

        while True:
            candidate = self._select_oldest_task(self.paths.tasks_queue_dir)
            if candidate is None:
                return None

            task_id, source = candidate
            destination = self.paths.tasks_active_dir / source.name
            try:
                source.replace(destination)
            except FileNotFoundError:
                # Another claimer moved this artifact first; retry selection.
                continue
            return QueueClaim(work_item_kind=WorkItemKind.TASK, work_item_id=task_id, path=destination)

    def claim_next_planning_item(self) -> QueueClaim | None:
        active_specs = self._list_markdown_files(self.paths.specs_active_dir)
        active_incidents = self._list_markdown_files(self.paths.incidents_active_dir)
        if len(active_specs) + len(active_incidents) > 1:
            raise QueueStateError("Multiple active planning items found")
        if active_specs or active_incidents:
            return None

        while True:
            incident_candidate = self._select_oldest_incident(self.paths.incidents_incoming_dir)
            if incident_candidate is not None:
                incident_id, source = incident_candidate
                destination = self.paths.incidents_active_dir / source.name
                try:
                    source.replace(destination)
                except FileNotFoundError:
                    continue
                return QueueClaim(
                    work_item_kind=WorkItemKind.INCIDENT,
                    work_item_id=incident_id,
                    path=destination,
                )

            spec_candidate = self._select_oldest_spec(self.paths.specs_queue_dir)
            if spec_candidate is None:
                return None

            spec_id, source = spec_candidate
            destination = self.paths.specs_active_dir / source.name
            try:
                source.replace(destination)
            except FileNotFoundError:
                continue
            return QueueClaim(work_item_kind=WorkItemKind.SPEC, work_item_id=spec_id, path=destination)

    def mark_task_done(self, task_id: str) -> Path:
        return self._move_active_task(task_id, self.paths.tasks_done_dir)

    def mark_task_blocked(self, task_id: str) -> Path:
        return self._move_active_task(task_id, self.paths.tasks_blocked_dir)

    def mark_spec_done(self, spec_id: str) -> Path:
        return self._move_active_spec(spec_id, self.paths.specs_done_dir)

    def mark_spec_blocked(self, spec_id: str) -> Path:
        return self._move_active_spec(spec_id, self.paths.specs_blocked_dir)

    def mark_incident_resolved(self, incident_id: str) -> Path:
        return self._move_active_incident(incident_id, self.paths.incidents_resolved_dir)

    def mark_incident_blocked(self, incident_id: str) -> Path:
        return self._move_active_incident(incident_id, self.paths.incidents_blocked_dir)

    def requeue_task(self, task_id: str, *, reason: str) -> Path:
        destination = self._move_active_task(task_id, self.paths.tasks_queue_dir)
        self._append_requeue_reason(self.paths.tasks_queue_dir, task_id, WorkItemKind.TASK, reason)
        return destination

    def requeue_spec(self, spec_id: str, *, reason: str) -> Path:
        destination = self._move_active_spec(spec_id, self.paths.specs_queue_dir)
        self._append_requeue_reason(self.paths.specs_queue_dir, spec_id, WorkItemKind.SPEC, reason)
        return destination

    def requeue_incident(self, incident_id: str, *, reason: str) -> Path:
        destination = self._move_active_incident(incident_id, self.paths.incidents_incoming_dir)
        self._append_requeue_reason(
            self.paths.incidents_incoming_dir,
            incident_id,
            WorkItemKind.INCIDENT,
            reason,
        )
        return destination

    def detect_execution_stale_state(self, *, snapshot_active_task_id: str | None) -> StaleActiveState:
        active_ids = self._ids_in_directory(self.paths.tasks_active_dir)
        queued_ids = self._ids_in_directory(self.paths.tasks_queue_dir)
        reasons: list[str] = []

        if len(active_ids) > 1:
            reasons.append("multiple_active_items")

        if active_ids and snapshot_active_task_id is None:
            reasons.append("active_without_snapshot")

        if snapshot_active_task_id is not None:
            if snapshot_active_task_id in queued_ids:
                reasons.append("snapshot_points_to_queued_item")
            if not active_ids:
                reasons.append("snapshot_without_active_artifact")
            elif len(active_ids) == 1 and active_ids[0] != snapshot_active_task_id:
                reasons.append("snapshot_active_id_mismatch")

        return self._stale_state(reasons)

    def detect_planning_stale_state(
        self,
        *,
        snapshot_active_kind: WorkItemKind | None,
        snapshot_active_item_id: str | None,
    ) -> StaleActiveState:
        has_kind = snapshot_active_kind is not None
        has_item = snapshot_active_item_id is not None
        if has_kind != has_item:
            raise QueueStateError("snapshot_active_kind and snapshot_active_item_id must be set together")

        if snapshot_active_kind not in {None, WorkItemKind.SPEC, WorkItemKind.INCIDENT}:
            raise QueueStateError("Planning stale-state checks only support spec and incident kinds")

        active_specs = [(WorkItemKind.SPEC, item_id) for item_id in self._ids_in_directory(self.paths.specs_active_dir)]
        active_incidents = [
            (WorkItemKind.INCIDENT, item_id)
            for item_id in self._ids_in_directory(self.paths.incidents_active_dir)
        ]
        active_items = active_specs + active_incidents
        reasons: list[str] = []

        if len(active_items) > 1:
            reasons.append("multiple_active_items")

        if active_items and snapshot_active_item_id is None:
            reasons.append("active_without_snapshot")

        if snapshot_active_kind is not None and snapshot_active_item_id is not None:
            queued_ids = (
                self._ids_in_directory(self.paths.specs_queue_dir)
                if snapshot_active_kind is WorkItemKind.SPEC
                else self._ids_in_directory(self.paths.incidents_incoming_dir)
            )
            if snapshot_active_item_id in queued_ids:
                reasons.append("snapshot_points_to_queued_item")

            if not active_items:
                reasons.append("snapshot_without_active_artifact")
            elif len(active_items) == 1:
                active_kind, active_id = active_items[0]
                if active_kind is not snapshot_active_kind or active_id != snapshot_active_item_id:
                    reasons.append("snapshot_active_id_mismatch")

        return self._stale_state(reasons)

    def _move_active_task(self, task_id: str, destination_dir: Path) -> Path:
        return self._move_item(
            source_dir=self.paths.tasks_active_dir,
            destination_dir=destination_dir,
            item_id=task_id,
            kind=WorkItemKind.TASK,
        )

    def _move_active_spec(self, spec_id: str, destination_dir: Path) -> Path:
        return self._move_item(
            source_dir=self.paths.specs_active_dir,
            destination_dir=destination_dir,
            item_id=spec_id,
            kind=WorkItemKind.SPEC,
        )

    def _move_active_incident(self, incident_id: str, destination_dir: Path) -> Path:
        return self._move_item(
            source_dir=self.paths.incidents_active_dir,
            destination_dir=destination_dir,
            item_id=incident_id,
            kind=WorkItemKind.INCIDENT,
        )

    def _move_item(
        self,
        *,
        source_dir: Path,
        destination_dir: Path,
        item_id: str,
        kind: WorkItemKind,
    ) -> Path:
        source = source_dir / f"{item_id}.md"
        if not source.exists():
            raise QueueStateError(f"{kind.value} {item_id} is not active")

        destination = destination_dir / source.name
        if destination.exists():
            raise QueueStateError(f"{kind.value} {item_id} already exists at destination")

        source.replace(destination)
        return destination

    def _append_requeue_reason(
        self,
        destination_dir: Path,
        item_id: str,
        kind: WorkItemKind,
        reason: str,
    ) -> None:
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise QueueStateError("requeue reason is required")

        log_path = destination_dir / f"{item_id}.requeue.jsonl"
        payload = {
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": kind.value,
            "reason": cleaned_reason,
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")

    def _ensure_unique_task_id(self, task_id: str) -> None:
        self._ensure_unique_id(
            work_item_id=task_id,
            directories=(
                self.paths.tasks_queue_dir,
                self.paths.tasks_active_dir,
                self.paths.tasks_done_dir,
                self.paths.tasks_blocked_dir,
            ),
            kind=WorkItemKind.TASK,
        )

    def _ensure_unique_spec_id(self, spec_id: str) -> None:
        self._ensure_unique_id(
            work_item_id=spec_id,
            directories=(
                self.paths.specs_queue_dir,
                self.paths.specs_active_dir,
                self.paths.specs_done_dir,
                self.paths.specs_blocked_dir,
            ),
            kind=WorkItemKind.SPEC,
        )

    def _ensure_unique_incident_id(self, incident_id: str) -> None:
        self._ensure_unique_id(
            work_item_id=incident_id,
            directories=(
                self.paths.incidents_incoming_dir,
                self.paths.incidents_active_dir,
                self.paths.incidents_resolved_dir,
                self.paths.incidents_blocked_dir,
            ),
            kind=WorkItemKind.INCIDENT,
        )

    def _ensure_unique_id(
        self,
        *,
        work_item_id: str,
        directories: tuple[Path, ...],
        kind: WorkItemKind,
    ) -> None:
        filename = f"{work_item_id}.md"
        for directory in directories:
            if (directory / filename).exists():
                raise QueueStateError(f"{kind.value} {work_item_id} already exists")

    def _select_oldest_task(self, directory: Path) -> tuple[str, Path] | None:
        return self._select_oldest_document(
            directory=directory,
            model=TaskDocument,
            id_attr="task_id",
            timestamp_attr="created_at",
        )

    def _select_oldest_spec(self, directory: Path) -> tuple[str, Path] | None:
        return self._select_oldest_document(
            directory=directory,
            model=SpecDocument,
            id_attr="spec_id",
            timestamp_attr="created_at",
        )

    def _select_oldest_incident(self, directory: Path) -> tuple[str, Path] | None:
        return self._select_oldest_document(
            directory=directory,
            model=IncidentDocument,
            id_attr="incident_id",
            timestamp_attr="opened_at",
        )

    def _select_oldest_document(
        self,
        *,
        directory: Path,
        model: type[_DocT],
        id_attr: str,
        timestamp_attr: str,
    ) -> tuple[str, Path] | None:
        candidates: list[tuple[datetime, str, Path]] = []
        for path in self._list_markdown_files(directory):
            try:
                document = parse_work_document_as(
                    path.read_text(encoding="utf-8"),
                    model=model,
                    path=path,
                )
            except FileNotFoundError:
                # Another process claimed or quarantined this path before we read it.
                continue
            except (ValidationError, ValueError) as exc:
                # Malformed queue artifacts are quarantined so valid work can still be claimed.
                self._quarantine_invalid_artifact(directory, path, str(exc))
                continue
            item_id = str(getattr(document, id_attr))
            if path.stem != item_id:
                self._quarantine_invalid_artifact(
                    directory,
                    path,
                    f"filename stem does not match {id_attr}: expected {item_id}, found {path.stem}",
                )
                continue
            timestamp = getattr(document, timestamp_attr)
            candidates.append((timestamp, item_id, path))

        if not candidates:
            return None

        candidates.sort(key=lambda item: (item[0], item[1]))
        _timestamp, item_id, path = candidates[0]
        return item_id, path

    def _write_model(self, destination: Path, document: _DocT) -> None:
        destination.write_text(render_work_document(document), encoding="utf-8")

    def _ids_in_directory(self, directory: Path) -> list[str]:
        return sorted(path.stem for path in self._list_markdown_files(directory))

    def _list_markdown_files(self, directory: Path) -> list[Path]:
        return sorted(path for path in directory.glob("*.md") if path.is_file())

    def _quarantine_invalid_artifact(self, directory: Path, source_path: Path, error: str) -> None:
        destination = source_path.with_suffix(f"{source_path.suffix}.invalid")
        suffix_index = 1
        while destination.exists():
            destination = source_path.with_suffix(f"{source_path.suffix}.invalid.{suffix_index}")
            suffix_index += 1

        try:
            source_path.replace(destination)
        except FileNotFoundError:
            # Another process moved the artifact before quarantine.
            return
        log_path = directory / "invalid-artifacts.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "at": datetime.now(timezone.utc).isoformat(),
                        "source_name": source_path.name,
                        "quarantine_name": destination.name,
                        "error": error,
                    },
                    sort_keys=True,
                )
                + "\n"
            )

    def _stale_state(self, reasons: list[str]) -> StaleActiveState:
        deduped = tuple(sorted(set(reasons)))
        return StaleActiveState(is_stale=bool(deduped), reasons=deduped)


__all__ = ["QueueClaim", "QueueStore", "StaleActiveState"]
