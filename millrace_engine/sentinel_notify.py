"""Generic Sentinel notification payload and adapter dispatch helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .config import EngineConfig
from .markdown import write_text_atomic
from .paths import RuntimePaths
from .sentinel_models import (
    SentinelNotificationAttemptRecord,
    SentinelNotificationPayload,
    SentinelRouteTarget,
)
from .sentinel_notify_openclaw import deliver_openclaw_notification


def _relative_path(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _attempt_id_for(moment: datetime) -> str:
    return f"sentinel-notify-{moment.strftime('%Y%m%dT%H%M%S%fZ')}"


def build_sentinel_notification_payload(
    *,
    paths: RuntimePaths,
    status: str,
    reason: str,
    route_target: SentinelRouteTarget,
    summary: str,
    latest_check_id: str,
    linked_incident_id: str,
    linked_incident_path: str,
    linked_recovery_request_id: str,
) -> SentinelNotificationPayload:
    evidence_paths = [
        _relative_path(paths.sentinel_state_file, root=paths.root),
        _relative_path(paths.sentinel_latest_report_file, root=paths.root),
    ]
    if linked_incident_path:
        evidence_paths.append(linked_incident_path)
    severity = "critical" if status == "escalated" else "warning"
    return SentinelNotificationPayload(
        severity=severity,
        reason=reason,
        summary=summary,
        workspace_root=paths.root.as_posix(),
        route_target=route_target,
        evidence_paths=tuple(evidence_paths),
        linked_incident_id=linked_incident_id,
        linked_incident_path=linked_incident_path,
        linked_recovery_request_id=linked_recovery_request_id,
        latest_check_id=latest_check_id,
        sentinel_state_path=_relative_path(paths.sentinel_state_file, root=paths.root),
        sentinel_report_path=_relative_path(paths.sentinel_latest_report_file, root=paths.root),
    )


def deliver_sentinel_notification(
    *,
    config: EngineConfig,
    paths: RuntimePaths,
    payload: SentinelNotificationPayload,
    attempted_at: datetime | None = None,
) -> SentinelNotificationAttemptRecord:
    """Persist one durable notification attempt and dispatch via the selected adapter."""

    moment = attempted_at or datetime.now(timezone.utc)
    attempt_id = _attempt_id_for(moment)
    base_attempt = SentinelNotificationAttemptRecord(
        attempt_id=attempt_id,
        attempted_at=moment,
        adapter_id=(config.sentinel.notify.adapter or "none"),
        adapter_kind=(config.sentinel.notify.adapter or "none"),
        transport="none",
        outcome="skipped",
        status="notify-disabled",
        payload=payload,
    )
    if not config.sentinel.notify.enabled:
        attempt = base_attempt
    else:
        adapter = (config.sentinel.notify.adapter or "").strip().lower()
        if not adapter:
            attempt = base_attempt.model_copy(update={"status": "no-adapter-configured"})
        elif adapter == "openclaw":
            attempt = deliver_openclaw_notification(
                payload=payload,
                notify_config=config.sentinel.notify,
                workspace_root=paths.root,
                base_attempt=base_attempt.model_copy(update={"adapter_id": "openclaw", "adapter_kind": "openclaw"}),
            )
        else:
            attempt = base_attempt.model_copy(
                update={
                    "adapter_id": adapter,
                    "adapter_kind": adapter,
                    "outcome": "failed",
                    "status": "unsupported-adapter",
                    "detail": f"unsupported sentinel notify adapter: {adapter}",
                }
            )
    artifact_path = paths.sentinel_notification_attempts_dir / f"{attempt.attempt_id}.json"
    paths.sentinel_notification_attempts_dir.mkdir(parents=True, exist_ok=True)
    persisted = attempt.model_copy(update={"artifact_path": _relative_path(artifact_path, root=paths.root)})
    write_text_atomic(artifact_path, persisted.model_dump_json(indent=2) + "\n")
    return persisted


__all__ = ["build_sentinel_notification_payload", "deliver_sentinel_notification"]
