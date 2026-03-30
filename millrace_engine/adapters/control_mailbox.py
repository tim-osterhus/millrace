"""File-backed daemon control mailbox."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
import json

from pydantic import ConfigDict, Field, field_validator

from ..contracts import ContractModel
from ..markdown import write_text_atomic
from ..paths import RuntimePaths


class ControlCommand(str, Enum):
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    RELOAD_CONFIG = "reload_config"
    SET_CONFIG = "set_config"
    ADD_TASK = "add_task"
    ADD_IDEA = "add_idea"
    QUEUE_REORDER = "queue_reorder"


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


class ControlCommandEnvelope(ContractModel):
    """Normalized mailbox command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str
    issued_at: datetime
    command: ControlCommand
    payload: dict[str, Any] = Field(default_factory=dict)
    issuer: str = "cli"

    @field_validator("issued_at", mode="before")
    @classmethod
    def normalize_issued_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


class MailboxCommandResult(ContractModel):
    """Archived outcome of one mailbox command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str
    processed_at: datetime
    ok: bool
    applied: bool
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("processed_at", mode="before")
    @classmethod
    def normalize_processed_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)


def _command_id(command: ControlCommand) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}__{command.value}"


def write_command(
    paths: RuntimePaths,
    command: ControlCommand,
    *,
    payload: dict[str, Any] | None = None,
    issuer: str = "cli",
) -> ControlCommandEnvelope:
    """Write one command file into the incoming mailbox."""

    envelope = ControlCommandEnvelope.model_validate(
        {
            "command_id": _command_id(command),
            "issued_at": datetime.now(timezone.utc),
            "command": command,
            "payload": _json_safe(payload or {}),
            "issuer": issuer,
        }
    )
    paths.commands_incoming_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        paths.commands_incoming_dir / f"{envelope.command_id}.json",
        envelope.model_dump_json(indent=2) + "\n",
    )
    return envelope


def list_incoming_command_paths(paths: RuntimePaths) -> list[Path]:
    """Return incoming mailbox files in deterministic order."""

    if not paths.commands_incoming_dir.exists():
        return []
    return sorted(path for path in paths.commands_incoming_dir.iterdir() if path.suffix == ".json")


def read_command(path: Path) -> ControlCommandEnvelope:
    """Parse one mailbox command file."""

    return ControlCommandEnvelope.model_validate_json(path.read_text(encoding="utf-8"))


def archive_command(
    paths: RuntimePaths,
    command_path: Path,
    *,
    envelope: ControlCommandEnvelope | None,
    result: MailboxCommandResult,
    failed: bool,
) -> Path:
    """Archive one processed mailbox command into processed/ or failed/."""

    target_dir = paths.commands_failed_dir if failed else paths.commands_processed_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / command_path.name
    payload = {
        "envelope": envelope.model_dump(mode="json") if envelope is not None else None,
        "result": result.model_dump(mode="json"),
    }
    write_text_atomic(archive_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    command_path.unlink(missing_ok=True)
    return archive_path
