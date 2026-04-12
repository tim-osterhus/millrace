"""Deterministic Sentinel evidence reads and meaningful-progress heuristics."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .control_models import SupervisorReport
from .control_reports import read_event_log
from .events import EventRecord, EventType
from .paths import RuntimePaths
from .research.governance_models import ProgressWatchdogReport, ProgressWatchdogState
from .sentinel_models import (
    SENTINEL_ARTIFACT_SCHEMA_VERSION,
    SentinelArtifactEvidence,
    SentinelEvidenceSnapshot,
    SentinelEventEvidence,
    SentinelHistoryEvidence,
    SentinelIncidentQueueEvidence,
    SentinelProgressAssessment,
    SentinelProgressComponent,
    SentinelProgressWatchdogEvidence,
    SentinelStatusMarkerEvidence,
    SentinelSupervisorEvidence,
)

DEFAULT_RECENT_EVENT_LIMIT = 12
DEFAULT_RECENT_HISTORY_LIMIT = 12
DEFAULT_RECENT_ARTIFACT_LIMIT = 5
_STATUS_MARKER_RE = re.compile(r"^###\s+([A-Z0-9_]+)\s*$")
_HISTORY_LINE_RE = re.compile(
    r"^- (?P<timestamp>\S+) \[(?P<event_type>[^\]]+)\] task=(?P<task_id>\S+) detail=(?P<detail_path>\S+)\s*$"
)
_IGNORED_PROGRESS_KEYS = frozenset(
    {
        "attempt",
        "attempt_count",
        "check_id",
        "heartbeat",
        "heartbeat_at",
        "notification_attempt_id",
        "notification_id",
        "notified_at",
        "sentinel_check_id",
        "updated_at",
    }
)
_MEANINGFUL_EVENT_TYPES: dict[EventType, str] = {
    EventType.TASK_PROMOTED: "queue",
    EventType.TASK_ARCHIVED: "queue",
    EventType.TASK_QUARANTINED: "queue",
    EventType.STAGE_STARTED: "stage",
    EventType.STAGE_COMPLETED: "stage",
    EventType.STAGE_FAILED: "stage",
    EventType.QUICKFIX_ATTEMPT: "stage",
    EventType.QUICKFIX_EXHAUSTED: "stage",
    EventType.NEEDS_RESEARCH: "research",
    EventType.BACKLOG_REPOPULATED: "queue",
    EventType.RESEARCH_RECEIVED: "research",
    EventType.RESEARCH_DEFERRED: "research",
    EventType.RESEARCH_SCAN_COMPLETED: "research",
    EventType.RESEARCH_MODE_SELECTED: "research",
    EventType.RESEARCH_DISPATCH_COMPILED: "research",
    EventType.RESEARCH_CHECKPOINT_RESUMED: "research",
    EventType.RESEARCH_BLOCKED: "research",
    EventType.IDEA_SUBMITTED: "research",
}


def _normalize_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        text = value.strip()
        if not text:
            return None
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _utc_iso(moment: datetime | None) -> str:
    if moment is None:
        return ""
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_modified_at(path: Path) -> datetime | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _hash_payload(payload: object) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(body.encode("utf-8")).hexdigest()


def _relative_path(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _read_status_marker(path: Path, *, plane: str, root: Path) -> SentinelStatusMarkerEvidence:
    marker = ""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _STATUS_MARKER_RE.match(line.strip())
            if match:
                marker = match.group(1)
                break
    return SentinelStatusMarkerEvidence(
        plane=plane,
        marker=marker,
        observed_at=_file_modified_at(path),
        source_path=_relative_path(path, root=root),
    )


def supervisor_evidence_from_report(report: SupervisorReport) -> SentinelSupervisorEvidence:
    return SentinelSupervisorEvidence(
        generated_at=report.generated_at,
        process_running=report.process_running,
        paused=report.paused,
        execution_status=report.execution_status.value,
        research_status=report.research_status.value,
        active_task_id="" if report.active_task is None else report.active_task.task_id,
        next_task_id="" if report.next_task is None else report.next_task.task_id,
        backlog_depth=report.backlog_depth,
        deferred_queue_size=report.deferred_queue_size,
        current_run_id=report.current_run_id or "",
        current_stage=report.current_stage or "",
        attention_reason=report.attention_reason.value,
        attention_summary=report.attention_summary,
    )


def _normalize_progress_payload(value: Any) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in sorted(value.items()):
            key = str(raw_key).strip()
            if not key:
                continue
            if key in _IGNORED_PROGRESS_KEYS or key.startswith("sentinel_"):
                continue
            normalized_value = _normalize_progress_payload(raw_value)
            if normalized_value in (None, "", (), [], {}):
                continue
            normalized[key] = normalized_value
        return normalized
    if isinstance(value, list):
        return [_normalize_progress_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_progress_payload(item) for item in value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, datetime):
        return _utc_iso(value)
    return value


def _event_signature(event: EventRecord, *, progress_class: str) -> str:
    payload = _normalize_progress_payload(event.payload)
    return _hash_payload(
        {
            "type": event.type.value,
            "source": event.source.value,
            "progress_class": progress_class,
            "payload": payload,
        }
    )


def _event_summary(event: EventRecord, *, progress_class: str) -> str:
    payload = _normalize_progress_payload(event.payload)
    stage = payload.get("stage") if isinstance(payload, dict) else None
    task_id = None
    if isinstance(payload, dict):
        task_id = payload.get("task_id") or payload.get("active_task_id")
    parts = [progress_class, event.type.value]
    if stage:
        parts.append(f"stage={stage}")
    if task_id:
        parts.append(f"task={task_id}")
    return " ".join(str(part) for part in parts if part)


def read_recent_meaningful_events(
    paths: RuntimePaths,
    *,
    limit: int = DEFAULT_RECENT_EVENT_LIMIT,
) -> tuple[SentinelEventEvidence, ...]:
    if limit <= 0 or not paths.engine_events_log.exists():
        return ()
    evidence: list[SentinelEventEvidence] = []
    for event in reversed(read_event_log(paths.engine_events_log)):
        progress_class = _MEANINGFUL_EVENT_TYPES.get(event.type)
        if progress_class is None:
            continue
        evidence.append(
            SentinelEventEvidence(
                timestamp=event.timestamp,
                event_type=event.type.value,
                source=event.source.value,
                progress_class=progress_class,
                signature=_event_signature(event, progress_class=progress_class),
                summary=_event_summary(event, progress_class=progress_class),
            )
        )
        if len(evidence) >= limit:
            break
    evidence.reverse()
    return tuple(evidence)


def read_recent_history_evidence(
    paths: RuntimePaths,
    *,
    limit: int = DEFAULT_RECENT_HISTORY_LIMIT,
) -> tuple[SentinelHistoryEvidence, ...]:
    if limit <= 0 or not paths.historylog_file.exists():
        return ()
    lines = [line.strip() for line in paths.historylog_file.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]
    selected = lines[-limit:]
    evidence: list[SentinelHistoryEvidence] = []
    for line in selected:
        match = _HISTORY_LINE_RE.match(line)
        if match is None:
            evidence.append(SentinelHistoryEvidence(line=line))
            continue
        detail_path = paths.agents_dir / match.group("detail_path")
        evidence.append(
            SentinelHistoryEvidence(
                timestamp=_normalize_datetime(match.group("timestamp")),
                event_type=match.group("event_type"),
                task_id=match.group("task_id"),
                detail_path=match.group("detail_path"),
                detail_exists=detail_path.exists(),
                line=line,
            )
        )
    return tuple(evidence)


def _artifact_entry_count(path: Path) -> int:
    if path.is_dir():
        return sum(1 for _ in path.iterdir())
    return 0


def _artifact_signature(path: Path) -> str:
    stat = path.stat()
    return _hash_payload(
        {
            "path": path.name,
            "is_dir": path.is_dir(),
            "size_bytes": stat.st_size,
            "modified_at": _utc_iso(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)),
            "entry_count": _artifact_entry_count(path),
        }
    )


def _recent_artifacts(
    root: Path,
    *,
    category: str,
    workspace_root: Path,
    limit: int,
) -> tuple[SentinelArtifactEvidence, ...]:
    if limit <= 0 or not root.exists():
        return ()
    entries = sorted(
        (entry for entry in root.iterdir() if entry.name != "sentinel"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    return tuple(
        SentinelArtifactEvidence(
            category=category,
            relative_path=_relative_path(entry, root=workspace_root),
            modified_at=datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc),
            size_bytes=entry.stat().st_size,
            entry_count=_artifact_entry_count(entry),
            signature=_artifact_signature(entry),
        )
        for entry in entries
    )


def read_incident_queue_evidence(paths: RuntimePaths) -> SentinelIncidentQueueEvidence:
    incident_root = paths.ideas_dir / "incidents"
    stage_dirs = {
        "incoming": incident_root / "incoming",
        "working": incident_root / "working",
        "resolved": incident_root / "resolved",
        "archived": incident_root / "archived",
    }
    latest_change: datetime | None = None
    signatures: dict[str, tuple[str, ...]] = {}
    counts: dict[str, int] = {}
    for stage, directory in stage_dirs.items():
        files = sorted(path for path in directory.iterdir()) if directory.exists() else []
        counts[stage] = len(files)
        signatures[stage] = tuple(path.name for path in files)
        for file_path in files:
            modified_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
            if latest_change is None or modified_at > latest_change:
                latest_change = modified_at
    signature = _hash_payload({"counts": counts, "files": signatures})
    return SentinelIncidentQueueEvidence(
        incoming_count=counts.get("incoming", 0),
        working_count=counts.get("working", 0),
        resolved_count=counts.get("resolved", 0),
        archived_count=counts.get("archived", 0),
        latest_change_at=latest_change,
        signature=signature,
    )


def read_progress_watchdog_evidence(paths: RuntimePaths) -> SentinelProgressWatchdogEvidence | None:
    if not paths.progress_watchdog_report_file.exists() and not paths.progress_watchdog_state_file.exists():
        return None
    report = (
        ProgressWatchdogReport.model_validate_json(paths.progress_watchdog_report_file.read_text(encoding="utf-8"))
        if paths.progress_watchdog_report_file.exists()
        else None
    )
    state = (
        ProgressWatchdogState.model_validate_json(paths.progress_watchdog_state_file.read_text(encoding="utf-8"))
        if paths.progress_watchdog_state_file.exists()
        else None
    )
    status = report.status if report is not None else state.status
    reason = report.reason if report is not None else state.reason
    batch_id = report.batch_id if report is not None else state.batch_id
    remediation_spec_id = (
        report.remediation_spec_id if report is not None else state.remediation_spec_id
    )
    visible_recovery_task_count = (
        report.visible_recovery_task_count if report is not None else state.visible_recovery_task_count
    )
    escalation_action = report.escalation_action if report is not None else state.escalation_action
    updated_at = report.updated_at if report is not None else state.updated_at
    signature = _hash_payload(
        {
            "status": status,
            "reason": reason,
            "batch_id": batch_id,
            "remediation_spec_id": remediation_spec_id,
            "visible_recovery_task_count": visible_recovery_task_count,
            "escalation_action": escalation_action,
        }
    )
    return SentinelProgressWatchdogEvidence(
        updated_at=updated_at,
        status=status,
        reason=reason,
        batch_id=batch_id,
        remediation_spec_id=remediation_spec_id,
        visible_recovery_task_count=visible_recovery_task_count,
        escalation_action=escalation_action,
        signature=signature,
    )


def _supervisor_signature(supervisor: SentinelSupervisorEvidence | None) -> str:
    if supervisor is None:
        return _hash_payload({})
    return _hash_payload(
        {
            "process_running": supervisor.process_running,
            "paused": supervisor.paused,
            "execution_status": supervisor.execution_status,
            "research_status": supervisor.research_status,
            "active_task_id": supervisor.active_task_id,
            "next_task_id": supervisor.next_task_id,
            "backlog_depth": supervisor.backlog_depth,
            "deferred_queue_size": supervisor.deferred_queue_size,
            "current_run_id": supervisor.current_run_id,
            "current_stage": supervisor.current_stage,
            "attention_reason": supervisor.attention_reason,
        }
    )


def _component_summary(name: str, signature: str, *, snapshot: SentinelEvidenceSnapshot | None = None) -> str:
    if snapshot is None:
        return f"{name} signature={signature[:12]}"
    if name == "status_markers":
        return (
            f"execution={snapshot.execution_status.marker or 'unknown'} "
            f"research={snapshot.research_status.marker or 'unknown'}"
        )
    if name == "supervisor_state" and snapshot.supervisor is not None:
        return (
            f"run={snapshot.supervisor.current_run_id or 'none'} "
            f"stage={snapshot.supervisor.current_stage or 'none'} "
            f"backlog={snapshot.supervisor.backlog_depth}"
        )
    if name == "recent_events":
        return (
            "recent meaningful events="
            f"{len(snapshot.recent_events)} latest={snapshot.recent_events[-1].event_type if snapshot.recent_events else 'none'}"
        )
    if name == "incident_queues":
        return (
            f"incidents incoming={snapshot.incidents.incoming_count} "
            f"working={snapshot.incidents.working_count} resolved={snapshot.incidents.resolved_count}"
        )
    if name == "progress_watchdog":
        watchdog = snapshot.progress_watchdog
        if watchdog is None:
            return "progress watchdog absent"
        return f"progress_watchdog={watchdog.status} count={watchdog.visible_recovery_task_count}"
    if name == "diagnostics":
        return f"diagnostics selected={len(snapshot.diagnostics)}"
    if name == "runs":
        return f"runs selected={len(snapshot.runs)}"
    return f"{name} signature={signature[:12]}"


def _build_progress_components(
    *,
    snapshot: SentinelEvidenceSnapshot,
) -> tuple[SentinelProgressComponent, ...]:
    components = [
        SentinelProgressComponent(
            component="status_markers",
            signature=_hash_payload(
                {
                    "execution": snapshot.execution_status.marker,
                    "research": snapshot.research_status.marker,
                }
            ),
            observed_at=max(
                (moment for moment in (snapshot.execution_status.observed_at, snapshot.research_status.observed_at) if moment is not None),
                default=None,
            ),
        ),
        SentinelProgressComponent(
            component="supervisor_state",
            signature=_supervisor_signature(snapshot.supervisor),
            observed_at=None if snapshot.supervisor is None else snapshot.supervisor.generated_at,
        ),
        SentinelProgressComponent(
            component="recent_events",
            signature=_hash_payload([event.signature for event in snapshot.recent_events]),
            observed_at=(snapshot.recent_events[-1].timestamp if snapshot.recent_events else None),
        ),
        SentinelProgressComponent(
            component="incident_queues",
            signature=snapshot.incidents.signature,
            observed_at=snapshot.incidents.latest_change_at,
        ),
        SentinelProgressComponent(
            component="progress_watchdog",
            signature=(
                _hash_payload({})
                if snapshot.progress_watchdog is None
                else snapshot.progress_watchdog.signature
            ),
            observed_at=None if snapshot.progress_watchdog is None else snapshot.progress_watchdog.updated_at,
        ),
        SentinelProgressComponent(
            component="diagnostics",
            signature=_hash_payload([entry.signature for entry in snapshot.diagnostics]),
            observed_at=max((entry.modified_at for entry in snapshot.diagnostics if entry.modified_at is not None), default=None),
        ),
        SentinelProgressComponent(
            component="runs",
            signature=_hash_payload([entry.signature for entry in snapshot.runs]),
            observed_at=max((entry.modified_at for entry in snapshot.runs if entry.modified_at is not None), default=None),
        ),
    ]
    return tuple(
        component.model_copy(
            update={"summary": _component_summary(component.component, component.signature, snapshot=snapshot)}
        )
        for component in components
    )


def collect_sentinel_evidence(
    *,
    paths: RuntimePaths,
    supervisor_report: SupervisorReport | None = None,
    recent_event_limit: int = DEFAULT_RECENT_EVENT_LIMIT,
    recent_history_limit: int = DEFAULT_RECENT_HISTORY_LIMIT,
    recent_artifact_limit: int = DEFAULT_RECENT_ARTIFACT_LIMIT,
    now: datetime | None = None,
) -> SentinelEvidenceSnapshot:
    collected_at = _normalize_datetime(now) or datetime.now(timezone.utc)
    execution_status = _read_status_marker(paths.status_file, plane="execution", root=paths.root)
    research_status = _read_status_marker(paths.research_status_file, plane="research", root=paths.root)
    supervisor = None if supervisor_report is None else supervisor_evidence_from_report(supervisor_report)
    incidents = read_incident_queue_evidence(paths)
    progress_watchdog = read_progress_watchdog_evidence(paths)
    recent_events = read_recent_meaningful_events(paths, limit=recent_event_limit)
    recent_history = read_recent_history_evidence(paths, limit=recent_history_limit)
    diagnostics = _recent_artifacts(
        paths.diagnostics_dir,
        category="diagnostic",
        workspace_root=paths.root,
        limit=recent_artifact_limit,
    )
    runs = _recent_artifacts(
        paths.runs_dir,
        category="run",
        workspace_root=paths.root,
        limit=recent_artifact_limit,
    )
    snapshot = SentinelEvidenceSnapshot(
        schema_version=SENTINEL_ARTIFACT_SCHEMA_VERSION,
        collected_at=collected_at,
        execution_status=execution_status,
        research_status=research_status,
        supervisor=supervisor,
        incidents=incidents,
        progress_watchdog=progress_watchdog,
        recent_events=recent_events,
        recent_history=recent_history,
        diagnostics=diagnostics,
        runs=runs,
        progress_signature="pending",
        latest_progress_at=max(
            (
                moment
                for moment in (
                    execution_status.observed_at,
                    research_status.observed_at,
                    incidents.latest_change_at,
                    None if progress_watchdog is None else progress_watchdog.updated_at,
                    None if not recent_events else recent_events[-1].timestamp,
                    max((entry.modified_at for entry in diagnostics if entry.modified_at is not None), default=None),
                    max((entry.modified_at for entry in runs if entry.modified_at is not None), default=None),
                )
                if moment is not None
            ),
            default=None,
        ),
    )
    components = _build_progress_components(snapshot=snapshot)
    progress_signature = _hash_payload(
        {
            "components": [
                {"component": component.component, "signature": component.signature}
                for component in components
            ]
        }
    )
    return snapshot.model_copy(
        update={
            "progress_components": components,
            "progress_signature": progress_signature,
        }
    )


def assess_meaningful_progress(
    current: SentinelEvidenceSnapshot,
    *,
    previous: SentinelEvidenceSnapshot | None = None,
    now: datetime | None = None,
) -> SentinelProgressAssessment:
    checked_at = _normalize_datetime(now) or datetime.now(timezone.utc)
    if previous is None:
        return SentinelProgressAssessment(
            checked_at=checked_at,
            state="unknown",
            reason="baseline-evidence-captured",
            progress_signature=current.progress_signature,
            latest_progress_at=current.latest_progress_at,
            evidence_summaries=tuple(component.summary for component in current.progress_components if component.summary),
        )

    previous_components = {component.component: component for component in previous.progress_components}
    changed = [
        component
        for component in current.progress_components
        if previous_components.get(component.component) is None
        or previous_components[component.component].signature != component.signature
    ]
    if changed:
        latest_progress_at = max(
            (component.observed_at for component in changed if component.observed_at is not None),
            default=current.latest_progress_at,
        )
        return SentinelProgressAssessment(
            checked_at=checked_at,
            state="progressing",
            reason="meaningful-progress-detected",
            progress_signature=current.progress_signature,
            latest_progress_at=latest_progress_at,
            changed_sources=tuple(component.component for component in changed),
            evidence_summaries=tuple(component.summary for component in changed if component.summary),
        )
    return SentinelProgressAssessment(
        checked_at=checked_at,
        state="stale",
        reason="no-meaningful-progress-signature-change",
        progress_signature=current.progress_signature,
        latest_progress_at=previous.latest_progress_at or current.latest_progress_at,
        evidence_summaries=(
            "status markers unchanged",
            "recent meaningful events unchanged",
            "incident queues unchanged",
            "progress watchdog unchanged",
        ),
    )


__all__ = [
    "DEFAULT_RECENT_ARTIFACT_LIMIT",
    "DEFAULT_RECENT_EVENT_LIMIT",
    "DEFAULT_RECENT_HISTORY_LIMIT",
    "assess_meaningful_progress",
    "collect_sentinel_evidence",
    "read_incident_queue_evidence",
    "read_progress_watchdog_evidence",
    "read_recent_history_evidence",
    "read_recent_meaningful_events",
    "supervisor_evidence_from_report",
]
