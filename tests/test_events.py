from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.events import read_runtime_events, write_runtime_event
from millrace_ai.paths import bootstrap_workspace, workspace_paths

NOW = datetime(2026, 4, 15, tzinfo=timezone.utc)


def _bootstrap(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def test_events_module_is_workspace_facade() -> None:
    events_facade = importlib.import_module("millrace_ai.events")
    events_module = importlib.import_module("millrace_ai.workspace.events")

    assert events_facade.RuntimeEventRecord.__module__ == "millrace_ai.workspace.events"
    assert events_facade.write_runtime_event is events_module.write_runtime_event
    assert events_facade.read_runtime_events is events_module.read_runtime_events


def test_runtime_event_round_trip(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)

    write_runtime_event(
        paths,
        event_type="runtime_started",
        data={"mode_id": "standard_plain"},
        occurred_at=NOW,
    )
    records = read_runtime_events(paths)

    assert len(records) == 1
    assert records[0].event_type == "runtime_started"
    assert records[0].data["mode_id"] == "standard_plain"


def test_runtime_event_writer_rejects_blank_event_type(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)

    with pytest.raises(ValueError, match="event_type"):
        write_runtime_event(paths, event_type="   ")


def test_runtime_event_writer_rejects_timezone_naive_occurred_at(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    naive_time = datetime(2026, 4, 15, 12, 0, 0)

    with pytest.raises(ValueError, match="occurred_at"):
        write_runtime_event(paths, event_type="runtime_started", occurred_at=naive_time)


def test_runtime_event_reader_rejects_invalid_schema_version(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    log_path = paths.logs_dir / "runtime_events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_payload = {
        "schema_version": "2.0",
        "kind": "runtime_event",
        "event_type": "runtime_started",
        "occurred_at": NOW.isoformat(),
        "data": {},
    }
    log_path.write_text(json.dumps(invalid_payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported schema_version"):
        read_runtime_events(paths)


def test_runtime_event_reader_rejects_invalid_kind(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    log_path = paths.logs_dir / "runtime_events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_payload = {
        "schema_version": "1.0",
        "kind": "not_runtime_event",
        "event_type": "runtime_started",
        "occurred_at": NOW.isoformat(),
        "data": {},
    }
    log_path.write_text(json.dumps(invalid_payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid kind"):
        read_runtime_events(paths)


def test_runtime_event_reader_rejects_malformed_json_line(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    log_path = paths.logs_dir / "runtime_events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text('{"schema_version":"1.0"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="malformed JSON"):
        read_runtime_events(paths)


def test_runtime_event_reader_rejects_invalid_occurred_at(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    log_path = paths.logs_dir / "runtime_events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_payload = {
        "schema_version": "1.0",
        "kind": "runtime_event",
        "event_type": "runtime_started",
        "occurred_at": "not-a-valid-timestamp",
        "data": {},
    }
    log_path.write_text(json.dumps(invalid_payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid occurred_at"):
        read_runtime_events(paths)


def test_runtime_event_reader_rejects_timezone_naive_occurred_at(tmp_path: Path) -> None:
    paths = _bootstrap(tmp_path)
    log_path = paths.logs_dir / "runtime_events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    invalid_payload = {
        "schema_version": "1.0",
        "kind": "runtime_event",
        "event_type": "runtime_started",
        "occurred_at": "2026-04-15T12:00:00",
        "data": {},
    }
    log_path.write_text(json.dumps(invalid_payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid occurred_at"):
        read_runtime_events(paths)
