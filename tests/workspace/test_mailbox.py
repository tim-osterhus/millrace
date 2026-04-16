from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import millrace_ai.workspace.mailbox as mailbox_module
from millrace_ai.mailbox import (
    archive_claimed_mailbox_command,
    claim_next_mailbox_command,
    drain_incoming_mailbox_commands,
    read_pending_mailbox_commands,
    write_mailbox_command,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _command_payload(command_id: str, command: str) -> dict[str, object]:
    return {
        "command_id": command_id,
        "command": command,
        "issued_at": NOW,
        "issuer": "operator",
        "payload": {"note": command_id},
    }


def _workspace_paths(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def test_mailbox_module_is_workspace_facade() -> None:
    mailbox_facade = importlib.import_module("millrace_ai.mailbox")
    mailbox_module = importlib.import_module("millrace_ai.workspace.mailbox")

    assert mailbox_facade.ClaimedMailboxCommand.__module__ == "millrace_ai.workspace.mailbox"
    assert mailbox_facade.MailboxDrainResult.__module__ == "millrace_ai.workspace.mailbox"
    assert mailbox_facade.write_mailbox_command is mailbox_module.write_mailbox_command
    assert mailbox_facade.claim_next_mailbox_command is mailbox_module.claim_next_mailbox_command


def test_write_and_read_pending_commands_round_trip(tmp_path: Path) -> None:
    paths = _workspace_paths(tmp_path)

    written_path = write_mailbox_command(paths, _command_payload("cmd-001", "pause"))

    assert written_path.parent == paths.mailbox_incoming_dir
    assert written_path.name == "cmd-001.json"

    pending = read_pending_mailbox_commands(paths)

    assert [entry.command_id for entry in pending] == ["cmd-001"]
    assert pending[0].command.value == "pause"


def test_claim_and_archive_flow_moves_commands_out_of_incoming(tmp_path: Path) -> None:
    paths = _workspace_paths(tmp_path)
    write_mailbox_command(paths, _command_payload("cmd-b", "pause"))
    write_mailbox_command(paths, _command_payload("cmd-a", "stop"))

    first_claim = claim_next_mailbox_command(paths)
    assert first_claim is not None
    assert first_claim.envelope.command_id == "cmd-a"
    assert first_claim.source_path.name == "cmd-a.json.claimed"
    assert first_claim.claim_lock_path is not None
    assert first_claim.claim_lock_path.exists()

    second_claim = claim_next_mailbox_command(paths)
    assert second_claim is not None
    assert second_claim.envelope.command_id == "cmd-b"
    assert second_claim.source_path.name == "cmd-b.json.claimed"
    assert second_claim.claim_lock_path is not None
    assert second_claim.claim_lock_path.exists()

    assert claim_next_mailbox_command(paths) is None

    processed_archive = archive_claimed_mailbox_command(
        paths,
        first_claim,
        success=True,
        result={"applied": True},
    )

    failed_archive = archive_claimed_mailbox_command(
        paths,
        second_claim,
        success=False,
        error="handler failed",
    )

    assert claim_next_mailbox_command(paths) is None

    processed_payload = json.loads(processed_archive.read_text(encoding="utf-8"))
    assert processed_payload["disposition"] == "processed"
    assert processed_payload["envelope"]["command_id"] == "cmd-a"
    assert processed_payload["result"]["applied"] is True

    failed_payload = json.loads(failed_archive.read_text(encoding="utf-8"))
    assert failed_payload["disposition"] == "failed"
    assert failed_payload["envelope"]["command_id"] == "cmd-b"
    assert failed_payload["error"] == "handler failed"


def test_archive_write_failure_preserves_claimed_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _workspace_paths(tmp_path)
    write_mailbox_command(paths, _command_payload("cmd-a", "pause"))
    claim = claim_next_mailbox_command(paths)
    assert claim is not None

    def _raise_write_failure(path: Path, payload: dict[str, object]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(mailbox_module, "_write_json_file", _raise_write_failure)

    with pytest.raises(OSError, match="disk full"):
        archive_claimed_mailbox_command(paths, claim, success=True)

    assert claim.source_path.exists()
    assert claim.claim_lock_path is not None
    assert claim.claim_lock_path.exists() is False
    assert (paths.mailbox_processed_dir / "cmd-a.json").exists() is False


def test_claim_recovers_preexisting_claimed_artifact_when_lock_released(
    tmp_path: Path,
) -> None:
    paths = _workspace_paths(tmp_path)
    write_mailbox_command(paths, _command_payload("cmd-a", "pause"))

    first_claim = claim_next_mailbox_command(paths)
    assert first_claim is not None
    assert first_claim.source_path.name == "cmd-a.json.claimed"
    assert first_claim.claim_lock_path is not None

    assert claim_next_mailbox_command(paths) is None
    stale_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()
    first_claim.claim_lock_path.touch()
    os.utime(first_claim.claim_lock_path, (stale_time, stale_time))

    recovered_claim = claim_next_mailbox_command(paths)
    assert recovered_claim is not None
    assert recovered_claim.source_path == first_claim.source_path
    assert recovered_claim.source_name == "cmd-a.json"
    assert recovered_claim.claim_lock_path is not None
    assert recovered_claim.claim_lock_path.exists()


def test_claim_does_not_overwrite_existing_claimed_file(tmp_path: Path) -> None:
    paths = _workspace_paths(tmp_path)
    stale_claimed_path = paths.mailbox_incoming_dir / "cmd-a.json.claimed"
    stale_claimed_path.write_text(
        json.dumps(_command_payload("cmd-a", "stop"), default=str, indent=2) + "\n",
        encoding="utf-8",
    )
    write_mailbox_command(paths, _command_payload("cmd-a", "pause"))

    claim = claim_next_mailbox_command(paths)
    assert claim is not None
    assert claim.source_path == stale_claimed_path
    assert claim.envelope.command.value == "stop"
    assert claim.claim_lock_path is not None
    assert claim.claim_lock_path.exists()
    assert (paths.mailbox_incoming_dir / "cmd-a.json").exists()


def test_write_validates_payload_with_mailbox_command_envelope(tmp_path: Path) -> None:
    paths = _workspace_paths(tmp_path)

    with pytest.raises(ValidationError):
        write_mailbox_command(paths, _command_payload("cmd-bad", "not_a_command"))


def test_drain_is_deterministic_and_archives_failed_and_processed(tmp_path: Path) -> None:
    paths = _workspace_paths(tmp_path)

    invalid_path = paths.mailbox_incoming_dir / "cmd-001-invalid.json"
    invalid_path.write_text(
        json.dumps(
            {
                "command_id": "cmd-001-invalid",
                "command": "not_a_command",
                "issued_at": NOW.isoformat(),
                "issuer": "operator",
                "payload": {},
            }
        ),
        encoding="utf-8",
    )
    write_mailbox_command(paths, _command_payload("cmd-002", "pause"))
    write_mailbox_command(paths, _command_payload("cmd-003", "resume"))

    def _handler(command) -> None:
        if command.command_id == "cmd-003":
            raise RuntimeError("boom")

    results = drain_incoming_mailbox_commands(paths, handler=_handler)

    assert [result.source_name for result in results] == [
        "cmd-001-invalid.json",
        "cmd-002.json",
        "cmd-003.json",
    ]
    assert [result.disposition for result in results] == [
        "failed",
        "processed",
        "failed",
    ]

    assert tuple(paths.mailbox_incoming_dir.glob("*.json")) == ()
    assert sorted(path.name for path in paths.mailbox_processed_dir.glob("*.json")) == [
        "cmd-002.json"
    ]
    assert sorted(path.name for path in paths.mailbox_failed_dir.glob("*.json")) == [
        "cmd-001-invalid.json",
        "cmd-003.json",
    ]
