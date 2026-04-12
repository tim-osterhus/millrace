from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.sentinel_notify import build_sentinel_notification_payload, deliver_sentinel_notification

from .support import runtime_workspace


def _payload_for(paths):
    return build_sentinel_notification_payload(
        paths=paths,
        status="escalated",
        reason="sentinel-hard-cap-triggered",
        route_target="notify",
        summary="Sentinel reached the configured hard cap.",
        latest_check_id="sentinel-20260411T200000Z",
        linked_incident_id="INC-001",
        linked_incident_path="agents/ideas/incidents/incoming/incident.md",
        linked_recovery_request_id="recovery-001",
    )


def test_deliver_sentinel_notification_persists_openclaw_success_attempt(tmp_path: Path) -> None:
    _, config_path = runtime_workspace(tmp_path)
    capture_path = tmp_path / "openclaw_payload.json"
    script_path = tmp_path / "openclaw_success.py"
    script_path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import sys",
                f"Path({str(capture_path)!r}).write_text(sys.stdin.read(), encoding='utf-8')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_engine_config(config_path)
    config = loaded.config.model_copy(
        update={
            "sentinel": loaded.config.sentinel.model_copy(
                update={
                    "notify": loaded.config.sentinel.notify.model_copy(
                        update={
                            "enabled": True,
                            "adapter": "openclaw",
                            "openclaw_command": ("python3", str(script_path)),
                        }
                    )
                }
            )
        }
    )
    paths = build_runtime_paths(config)

    attempt = deliver_sentinel_notification(
        config=config,
        paths=paths,
        payload=_payload_for(paths),
        attempted_at=datetime(2026, 4, 11, 20, 0, tzinfo=timezone.utc),
    )

    persisted = json.loads((paths.root / attempt.artifact_path).read_text(encoding="utf-8"))
    delivered_payload = json.loads(capture_path.read_text(encoding="utf-8"))

    assert attempt.adapter_id == "openclaw"
    assert attempt.outcome == "delivered"
    assert attempt.status == "openclaw-delivered"
    assert persisted["artifact_path"] == attempt.artifact_path
    assert delivered_payload["linked_incident_id"] == "INC-001"
    assert delivered_payload["latest_check_id"] == "sentinel-20260411T200000Z"


def test_deliver_sentinel_notification_persists_openclaw_configuration_failure(tmp_path: Path) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    config = loaded.config.model_copy(
        update={
            "sentinel": loaded.config.sentinel.model_copy(
                update={
                    "notify": loaded.config.sentinel.notify.model_copy(
                        update={"enabled": True, "adapter": "openclaw"}
                    )
                }
            )
        }
    )
    paths = build_runtime_paths(config)

    attempt = deliver_sentinel_notification(
        config=config,
        paths=paths,
        payload=_payload_for(paths),
        attempted_at=datetime(2026, 4, 11, 20, 5, tzinfo=timezone.utc),
    )

    persisted = json.loads((paths.root / attempt.artifact_path).read_text(encoding="utf-8"))

    assert attempt.outcome == "failed"
    assert attempt.status == "openclaw-command-not-configured"
    assert persisted["status"] == "openclaw-command-not-configured"
    assert persisted["payload"]["linked_recovery_request_id"] == "recovery-001"
