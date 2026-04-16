"""File-backed mailbox command intake and archive helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from pydantic import JsonValue, ValidationError

from millrace_ai.contracts import MailboxCommandEnvelope

from .paths import WorkspacePaths

ArchiveDisposition = str
_CLAIMED_SUFFIX = ".claimed"
_CLAIM_LOCK_SUFFIX = ".lock"
_LOCK_STALE_AFTER_SECONDS = 300


@dataclass(frozen=True, slots=True)
class ClaimedMailboxCommand:
    """A validated mailbox command claimed from incoming storage."""

    envelope: MailboxCommandEnvelope
    source_name: str
    source_path: Path
    claim_lock_path: Path | None = None


@dataclass(frozen=True, slots=True)
class MailboxDrainResult:
    """Result for one incoming mailbox entry drained from incoming storage."""

    source_name: str
    disposition: ArchiveDisposition
    archive_path: Path


def write_mailbox_command(
    paths: WorkspacePaths,
    envelope: MailboxCommandEnvelope | Mapping[str, object],
) -> Path:
    """Validate and write one command envelope into incoming mailbox storage."""

    validated = _validate_envelope(envelope)
    target = paths.mailbox_incoming_dir / _command_filename(validated.command_id)
    if target.exists():
        raise FileExistsError(f"Mailbox command already exists: {target.name}")

    _write_json_file(target, validated.model_dump(mode="json"))
    return target


def read_pending_mailbox_commands(paths: WorkspacePaths) -> tuple[MailboxCommandEnvelope, ...]:
    """Return validated pending mailbox commands in deterministic order."""

    commands: list[MailboxCommandEnvelope] = []
    for source_path in _incoming_command_paths(paths):
        commands.append(_read_envelope(source_path))
    return tuple(commands)


def claim_next_mailbox_command(paths: WorkspacePaths) -> ClaimedMailboxCommand | None:
    """Claim the next pending validated mailbox command by deterministic filename order."""

    for claimed_source_path in _claimed_command_paths(paths):
        claim_lock_path = _claimed_lock_path(claimed_source_path)
        if not _try_acquire_lock(claim_lock_path):
            continue
        try:
            envelope = _read_envelope(claimed_source_path)
        except Exception:
            _release_lock(claim_lock_path)
            raise
        return ClaimedMailboxCommand(
            envelope=envelope,
            source_name=_unclaim_source_name(claimed_source_path.name),
            source_path=claimed_source_path,
            claim_lock_path=claim_lock_path,
        )

    for incoming_source_path in _incoming_command_paths(paths):
        claimed_source_path = _claimed_source_path(incoming_source_path)
        claim_lock_path = _claimed_lock_path(claimed_source_path)
        if not _try_acquire_lock(claim_lock_path):
            continue

        if claimed_source_path.exists():
            _release_lock(claim_lock_path)
            continue
        try:
            os.link(incoming_source_path, claimed_source_path)
            incoming_source_path.unlink()
        except (FileExistsError, FileNotFoundError):
            # Another concurrent claimer moved this file between list and claim.
            _release_lock(claim_lock_path)
            continue

        try:
            envelope = _read_envelope(claimed_source_path)
        except (ValidationError, ValueError, json.JSONDecodeError):
            # Preserve pre-existing claim behavior by returning malformed payloads to incoming.
            claimed_source_path.replace(incoming_source_path)
            _release_lock(claim_lock_path)
            raise

        return ClaimedMailboxCommand(
            envelope=envelope,
            source_name=incoming_source_path.name,
            source_path=claimed_source_path,
            claim_lock_path=claim_lock_path,
        )

    return None


def archive_claimed_mailbox_command(
    paths: WorkspacePaths,
    claim: ClaimedMailboxCommand,
    *,
    success: bool,
    result: Mapping[str, JsonValue] | None = None,
    error: str | None = None,
) -> Path:
    """Archive a claimed command into processed or failed mailbox storage."""

    disposition = "processed" if success else "failed"
    if success and error is not None:
        raise ValueError("error must be omitted when success is True")

    archive_dir = paths.mailbox_processed_dir if success else paths.mailbox_failed_dir
    archive_path = archive_dir / claim.source_name

    archive_payload: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "mailbox_archive",
        "disposition": disposition,
        "archived_at": _utc_now_iso(),
        "envelope": claim.envelope.model_dump(mode="json"),
        "result": dict(result or {}),
    }
    if not success:
        archive_payload["error"] = error or "command handler failed"

    try:
        _write_json_file(archive_path, archive_payload)
        _remove_if_exists(claim.source_path)
        return archive_path
    finally:
        _release_lock(claim.claim_lock_path)


def drain_incoming_mailbox_commands(
    paths: WorkspacePaths,
    *,
    handler: Callable[[MailboxCommandEnvelope], None],
) -> tuple[MailboxDrainResult, ...]:
    """Drain incoming commands deterministically and archive each as processed or failed."""

    drained: list[MailboxDrainResult] = []

    for source_path in _incoming_command_paths(paths):
        try:
            envelope = _read_envelope(source_path)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            archive_path = _archive_unreadable_payload(paths, source_path, str(exc))
            drained.append(
                MailboxDrainResult(
                    source_name=source_path.name,
                    disposition="failed",
                    archive_path=archive_path,
                )
            )
            continue

        claim = ClaimedMailboxCommand(
            envelope=envelope,
            source_name=source_path.name,
            source_path=source_path,
        )

        try:
            handler(envelope)
        except Exception as exc:  # pragma: no cover - exercised via tests
            archive_path = archive_claimed_mailbox_command(
                paths,
                claim,
                success=False,
                error=str(exc),
            )
            drained.append(
                MailboxDrainResult(
                    source_name=claim.source_name,
                    disposition="failed",
                    archive_path=archive_path,
                )
            )
        else:
            archive_path = archive_claimed_mailbox_command(paths, claim, success=True)
            drained.append(
                MailboxDrainResult(
                    source_name=claim.source_name,
                    disposition="processed",
                    archive_path=archive_path,
                )
            )

    return tuple(drained)


def _incoming_command_paths(paths: WorkspacePaths) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (entry for entry in paths.mailbox_incoming_dir.glob("*.json") if entry.is_file()),
            key=lambda path: path.name,
        )
    )


def _claimed_command_paths(paths: WorkspacePaths) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                entry
                for entry in paths.mailbox_incoming_dir.glob(f"*{_CLAIMED_SUFFIX}")
                if entry.is_file()
            ),
            key=lambda path: path.name,
        )
    )


def _read_envelope(path: Path) -> MailboxCommandEnvelope:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("mailbox command payload must be a JSON object")
    return MailboxCommandEnvelope.model_validate(payload)


def _validate_envelope(
    envelope: MailboxCommandEnvelope | Mapping[str, object],
) -> MailboxCommandEnvelope:
    if isinstance(envelope, MailboxCommandEnvelope):
        return envelope
    return MailboxCommandEnvelope.model_validate(envelope)


def _command_filename(command_id: str) -> str:
    safe_command_id = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in command_id
    ).strip(".")
    if not safe_command_id:
        raise ValueError("command_id must include at least one filename-safe character")
    return f"{safe_command_id}.json"


def _archive_unreadable_payload(paths: WorkspacePaths, source_path: Path, error: str) -> Path:
    archive_path = paths.mailbox_failed_dir / source_path.name
    raw_payload: str | None
    try:
        raw_payload = source_path.read_text(encoding="utf-8")
    except OSError:
        raw_payload = None

    archive_payload: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "mailbox_archive",
        "disposition": "failed",
        "archived_at": _utc_now_iso(),
        "error": error,
    }
    if raw_payload is not None:
        archive_payload["raw_payload"] = raw_payload

    _write_json_file(archive_path, archive_payload)
    _remove_if_exists(source_path)
    return archive_path


def _claimed_source_path(incoming_source_path: Path) -> Path:
    return incoming_source_path.with_name(f"{incoming_source_path.name}{_CLAIMED_SUFFIX}")


def _claimed_lock_path(claimed_source_path: Path) -> Path:
    return claimed_source_path.with_name(f"{claimed_source_path.name}{_CLAIM_LOCK_SUFFIX}")


def _try_acquire_lock(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                handle.write(f"pid={os.getpid()}\n")
        except FileExistsError:
            if _is_lock_stale(lock_path):
                _remove_if_exists(lock_path)
                continue
            return False
        else:
            return True
    return False


def _release_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    _remove_if_exists(lock_path)


def _is_lock_stale(lock_path: Path) -> bool:
    try:
        lock_mtime = lock_path.stat().st_mtime
    except FileNotFoundError:
        return False
    now = datetime.now(timezone.utc).timestamp()
    return (now - lock_mtime) > _LOCK_STALE_AFTER_SECONDS


def _unclaim_source_name(claimed_name: str) -> str:
    if not claimed_name.endswith(_CLAIMED_SUFFIX):
        raise ValueError(f"Claimed mailbox filename must end with {_CLAIMED_SUFFIX}")
    return claimed_name[: -len(_CLAIMED_SUFFIX)]


def _write_json_file(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def _remove_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ClaimedMailboxCommand",
    "MailboxDrainResult",
    "archive_claimed_mailbox_command",
    "claim_next_mailbox_command",
    "drain_incoming_mailbox_commands",
    "read_pending_mailbox_commands",
    "write_mailbox_command",
]
