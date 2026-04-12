"""OpenClaw reference adapter for Sentinel notifications."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import SentinelNotifyConfig
from .sentinel_models import SentinelNotificationAttemptRecord, SentinelNotificationPayload


def deliver_openclaw_notification(
    *,
    payload: SentinelNotificationPayload,
    notify_config: SentinelNotifyConfig,
    workspace_root: Path,
    base_attempt: SentinelNotificationAttemptRecord,
) -> SentinelNotificationAttemptRecord:
    """Deliver one notification attempt through the configured OpenClaw command hook."""

    command = tuple(notify_config.openclaw_command)
    if not command:
        return base_attempt.model_copy(
            update={
                "transport": "hook",
                "outcome": "failed",
                "status": "openclaw-command-not-configured",
                "detail": "set sentinel.notify.openclaw_command to enable OpenClaw delivery",
            }
        )
    try:
        completed = subprocess.run(
            command,
            input=payload.model_dump_json(indent=2) + "\n",
            text=True,
            capture_output=True,
            cwd=workspace_root,
            timeout=notify_config.openclaw_timeout_seconds,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - adapter failure must not break Sentinel correctness
        return base_attempt.model_copy(
            update={
                "transport": "hook",
                "outcome": "failed",
                "status": exc.__class__.__name__,
                "detail": str(exc).strip() or exc.__class__.__name__,
            }
        )
    detail = json.dumps(
        {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        },
        sort_keys=True,
    )
    if completed.returncode == 0:
        return base_attempt.model_copy(
            update={
                "transport": "hook",
                "outcome": "delivered",
                "status": "openclaw-delivered",
                "detail": detail,
            }
        )
    return base_attempt.model_copy(
        update={
            "transport": "hook",
            "outcome": "failed",
            "status": f"openclaw-exit-{completed.returncode}",
            "detail": detail,
        }
    )


__all__ = ["deliver_openclaw_notification"]
