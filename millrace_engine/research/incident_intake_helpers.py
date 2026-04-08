"""Incident source resolution, materialization, and queue movement helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..markdown import write_text_atomic
from ..paths import RuntimePaths

if TYPE_CHECKING:
    from .incidents import IncidentDocument, IncidentLifecycleStatus
    from .state import ResearchCheckpoint, ResearchQueueOwnership


def target_incident_path(
    paths: RuntimePaths,
    lifecycle_status: "IncidentLifecycleStatus",
    source_path: Path,
) -> Path:
    """Resolve the next queue location for one incident lifecycle transition."""

    from .incidents import IncidentLifecycleStatus

    if lifecycle_status is IncidentLifecycleStatus.INCOMING:
        return paths.agents_dir / "ideas" / "incidents" / "working" / source_path.name
    if lifecycle_status is IncidentLifecycleStatus.WORKING:
        return paths.agents_dir / "ideas" / "incidents" / "resolved" / source_path.name
    if lifecycle_status is IncidentLifecycleStatus.RESOLVED:
        return paths.agents_dir / "ideas" / "incidents" / "archived" / source_path.name
    return paths.agents_dir / "ideas" / "incidents" / "archived" / source_path.name


def move_incident(path: Path, target_path: Path) -> Path:
    """Move one incident file while guarding against divergent target contents."""

    from .incidents import IncidentExecutionError

    if path == target_path:
        return target_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        if path.exists() and target_path.read_text(encoding="utf-8") != path.read_text(encoding="utf-8"):
            raise IncidentExecutionError(
                f"incident target already exists with different contents: {target_path.as_posix()}"
            )
        if path.exists():
            path.unlink()
        return target_path
    path.rename(target_path)
    return target_path


def incident_paths_from_checkpoint(paths: RuntimePaths, checkpoint: "ResearchCheckpoint") -> list[Path]:
    """Collect candidate incident paths carried by checkpoint ownership and handoff state."""

    from .incidents import _resolve_path_token

    candidates: list[Path] = []
    for ownership in checkpoint.owned_queues:
        if ownership.item_path is not None:
            candidates.append(_resolve_path_token(ownership.item_path, relative_to=paths.root))

    active_request = checkpoint.active_request
    if active_request is None:
        return candidates

    if active_request.incident_document is not None:
        candidates.append(_resolve_path_token(active_request.incident_document.source_path, relative_to=paths.root))
    if active_request.blocker_record is not None and active_request.blocker_record.incident_path is not None:
        candidates.append(_resolve_path_token(active_request.blocker_record.incident_path, relative_to=paths.root))
    if active_request.handoff is not None and active_request.handoff.incident_path is not None:
        candidates.append(_resolve_path_token(active_request.handoff.incident_path, relative_to=paths.root))
    payload_path = active_request.payload.get("path")
    if payload_path:
        candidates.append(_resolve_path_token(str(payload_path), relative_to=paths.root))
    return candidates


def materializable_incident_path(paths: RuntimePaths, checkpoint: "ResearchCheckpoint") -> Path | None:
    """Return the authoritative incident path for an intake checkpoint that can be materialized."""

    if checkpoint.node_id != "incident_intake":
        return None
    candidates = incident_paths_from_checkpoint(paths, checkpoint)
    if not candidates:
        return None
    return candidates[0]


def render_materialized_incident_document(
    checkpoint: "ResearchCheckpoint",
    *,
    incident_path: Path,
    emitted_at: datetime,
) -> str:
    """Render a restart-safe incident document from blocker or handoff context."""

    active_request = checkpoint.active_request
    blocker_record = None if active_request is None else active_request.blocker_record
    handoff = checkpoint.parent_handoff or (None if active_request is None else active_request.handoff)
    incident_id = incident_path.stem
    title = (
        (None if handoff is None else handoff.task_title)
        or (None if blocker_record is None else blocker_record.task_title)
        or incident_id
    )
    source_task = (
        (None if handoff is None else handoff.task_id)
        or (None if blocker_record is None else blocker_record.source_task)
    )
    failure_signature = None if handoff is None else handoff.failure_signature
    timestamp = emitted_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    summary_lines = [
        f"- Consult returned `NEEDS_RESEARCH` during `{handoff.stage}`." if handoff is not None else None,
        (
            f"- {handoff.reason}"
            if handoff is not None and handoff.reason
            else (
                None
                if blocker_record is None or not blocker_record.root_cause_summary
                else f"- {blocker_record.root_cause_summary}"
            )
        ),
    ]
    detail_lines = [
        f"- **Incident-ID:** `{incident_id}`",
        f"- **Source task:** `{source_task}`" if source_task else None,
        f"- **Parent handoff:** `{handoff.handoff_id}`" if handoff is not None else None,
        (
            f"- **Parent run:** `{handoff.parent_run.run_id}`"
            if handoff is not None and handoff.parent_run is not None
            else None
        ),
        f"- **Failure signature:** `{failure_signature}`" if failure_signature else None,
        (
            f"- **Diagnostics directory:** `{handoff.diagnostics_dir.as_posix()}`"
            if handoff is not None and handoff.diagnostics_dir is not None
            else None
        ),
    ]
    lines = [
        "---",
        f"incident_id: {incident_id}",
        "status: incoming",
        f"opened_at: {timestamp}",
        f"updated_at: {timestamp}",
    ]
    if source_task:
        lines.append(f"source_task: {source_task}")
    if failure_signature:
        lines.append(f"failure_signature: {failure_signature}")
    lines.extend(
        [
            "---",
            "",
            f"# {title}",
            "",
            *[line for line in detail_lines if line],
            "",
            "## Summary",
            *[line for line in summary_lines if line],
            "",
        ]
    )
    return "\n".join(lines)


def materialize_incident_source(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    emitted_at: datetime | None = None,
) -> Path | None:
    """Create the authoritative incoming incident file for a materializable intake checkpoint."""

    incident_path = materializable_incident_path(paths, checkpoint)
    if incident_path is None or incident_path.exists():
        return incident_path
    observed_at = emitted_at or datetime.now(timezone.utc)
    incident_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        incident_path,
        render_materialized_incident_document(
            checkpoint,
            incident_path=incident_path,
            emitted_at=observed_at,
        ),
    )
    return incident_path


def resolve_incident_source(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
) -> tuple[Path, "IncidentDocument"]:
    """Resolve the current incident artifact from checkpoint state."""

    from .incidents import IncidentExecutionError, load_incident_document

    checked: list[Path] = []
    for candidate in incident_paths_from_checkpoint(paths, checkpoint):
        if candidate in checked:
            continue
        checked.append(candidate)
        if candidate.exists():
            return candidate, load_incident_document(candidate)
    materialized = materialize_incident_source(paths, checkpoint)
    if materialized is not None and materialized.exists():
        return materialized, load_incident_document(materialized)
    raise IncidentExecutionError("incident checkpoint has no existing source artifact to execute")


def incident_source_exists(paths: RuntimePaths, checkpoint: "ResearchCheckpoint") -> bool:
    """Return True when a checkpoint can resolve an incident artifact on disk."""

    checked: list[Path] = []
    for candidate in incident_paths_from_checkpoint(paths, checkpoint):
        if candidate in checked:
            continue
        checked.append(candidate)
        if candidate.exists():
            return True
    return materializable_incident_path(paths, checkpoint) is not None


def incident_source_on_disk(paths: RuntimePaths, checkpoint: "ResearchCheckpoint") -> bool:
    """Return True only when one checkpoint candidate already exists on disk."""

    checked: list[Path] = []
    for candidate in incident_paths_from_checkpoint(paths, checkpoint):
        if candidate in checked:
            continue
        checked.append(candidate)
        if candidate.exists():
            return True
    return False


def queue_ownership_for_incident_path(
    *,
    incident_path: Path,
    run_id: str,
    emitted_at: datetime,
) -> "ResearchQueueOwnership":
    """Build deterministic queue ownership for the active incident artifact."""

    from .state import ResearchQueueFamily, ResearchQueueOwnership

    return ResearchQueueOwnership(
        family=ResearchQueueFamily.INCIDENT,
        queue_path=incident_path.parent,
        item_path=incident_path,
        owner_token=run_id,
        acquired_at=emitted_at,
    )
