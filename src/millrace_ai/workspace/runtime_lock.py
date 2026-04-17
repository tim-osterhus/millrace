"""Workspace-scoped runtime ownership lock helpers."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .paths import WorkspacePaths, workspace_paths

RuntimeOwnershipLockState = Literal["absent", "active", "stale", "invalid"]


@dataclass(frozen=True, slots=True)
class RuntimeOwnershipRecord:
    """Structured ownership metadata persisted in the lock file."""

    workspace_root: str
    owner_pid: int
    owner_hostname: str
    owner_session_id: str
    acquired_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeOwnershipLockStatus:
    """Current lock state with optional ownership context."""

    state: RuntimeOwnershipLockState
    lock_path: Path
    record: RuntimeOwnershipRecord | None
    detail: str


@dataclass(frozen=True, slots=True)
class ClearRuntimeOwnershipLockResult:
    """Result from clearing stale or invalid ownership lock files."""

    cleared: bool
    reason: str
    status: RuntimeOwnershipLockStatus


class RuntimeOwnershipLockError(RuntimeError):
    """Raised when daemon ownership cannot be acquired."""

    def __init__(self, message: str, *, status: RuntimeOwnershipLockStatus) -> None:
        super().__init__(message)
        self.status = status


def acquire_runtime_ownership_lock(
    target: WorkspacePaths | Path | str,
    *,
    owner_pid: int | None = None,
    owner_hostname: str | None = None,
    owner_session_id: str | None = None,
    acquired_at: datetime | None = None,
) -> RuntimeOwnershipRecord:
    """Acquire exclusive runtime ownership for one workspace."""

    paths = _resolve_paths(target)
    lock_path = paths.runtime_lock_file
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = acquired_at or datetime.now(timezone.utc)
    record = RuntimeOwnershipRecord(
        workspace_root=str(paths.root),
        owner_pid=owner_pid or os.getpid(),
        owner_hostname=(owner_hostname or socket.gethostname() or "unknown-host"),
        owner_session_id=owner_session_id or uuid4().hex,
        acquired_at=timestamp,
    )

    payload = _serialize_record(record)
    try:
        with lock_path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        status = inspect_runtime_ownership_lock(paths)
        raise RuntimeOwnershipLockError(_ownership_error_message(status), status=status) from exc

    return record


def release_runtime_ownership_lock(
    target: WorkspacePaths | Path | str,
    *,
    owner_session_id: str | None = None,
    force: bool = False,
) -> bool:
    """Release workspace runtime ownership if caller owns it or force is enabled."""

    paths = _resolve_paths(target)
    lock_path = paths.runtime_lock_file
    if not lock_path.exists():
        return False

    if force:
        try:
            lock_path.unlink()
            return True
        except FileNotFoundError:
            return False

    status = inspect_runtime_ownership_lock(paths)
    if status.record is None:
        return False
    if owner_session_id is not None and status.record.owner_session_id != owner_session_id:
        return False

    try:
        lock_path.unlink()
        return True
    except FileNotFoundError:
        return False


def clear_stale_runtime_ownership_lock(
    target: WorkspacePaths | Path | str,
) -> ClearRuntimeOwnershipLockResult:
    """Remove stale/invalid lock files, preserving active daemon ownership."""

    paths = _resolve_paths(target)
    status = inspect_runtime_ownership_lock(paths)

    if status.state == "absent":
        return ClearRuntimeOwnershipLockResult(cleared=False, reason="missing", status=status)

    if status.state == "active":
        return ClearRuntimeOwnershipLockResult(cleared=False, reason="active_owner", status=status)

    cleared = release_runtime_ownership_lock(paths, force=True)
    reason = "cleared_stale" if status.state == "stale" else "cleared_invalid"
    return ClearRuntimeOwnershipLockResult(cleared=cleared, reason=reason, status=status)


def inspect_runtime_ownership_lock(target: WorkspacePaths | Path | str) -> RuntimeOwnershipLockStatus:
    """Inspect lock metadata and classify active/stale/invalid states."""

    paths = _resolve_paths(target)
    lock_path = paths.runtime_lock_file
    if not lock_path.exists():
        return RuntimeOwnershipLockStatus(
            state="absent",
            lock_path=lock_path,
            record=None,
            detail="runtime ownership lock is absent",
        )

    try:
        record = _parse_lock_payload(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return RuntimeOwnershipLockStatus(
            state="invalid",
            lock_path=lock_path,
            record=None,
            detail=f"invalid runtime ownership lock payload: {exc}",
        )

    if record.workspace_root != str(paths.root):
        return RuntimeOwnershipLockStatus(
            state="invalid",
            lock_path=lock_path,
            record=record,
            detail=(
                "runtime ownership lock references a different workspace root "
                f"({record.workspace_root})"
            ),
        )

    if _pid_is_running(record.owner_pid):
        return RuntimeOwnershipLockStatus(
            state="active",
            lock_path=lock_path,
            record=record,
            detail=(
                "workspace runtime ownership lock is active: "
                f"pid={record.owner_pid} host={record.owner_hostname} "
                f"session={record.owner_session_id}"
            ),
        )

    return RuntimeOwnershipLockStatus(
        state="stale",
        lock_path=lock_path,
        record=record,
        detail=(
            "workspace runtime ownership lock is stale: "
            f"pid={record.owner_pid} is not running "
            f"(session={record.owner_session_id})"
        ),
    )


def _resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


def _serialize_record(record: RuntimeOwnershipRecord) -> str:
    payload = {
        "workspace_root": record.workspace_root,
        "owner_pid": record.owner_pid,
        "owner_hostname": record.owner_hostname,
        "owner_session_id": record.owner_session_id,
        "acquired_at": record.acquired_at.isoformat(),
    }
    return json.dumps(payload, indent=2) + "\n"


def _parse_lock_payload(payload: str) -> RuntimeOwnershipRecord:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("expected top-level object")

    workspace_root = parsed.get("workspace_root")
    if not isinstance(workspace_root, str) or not workspace_root.strip():
        raise ValueError("workspace_root must be a non-empty string")

    owner_pid = parsed.get("owner_pid")
    if not isinstance(owner_pid, int) or owner_pid <= 0:
        raise ValueError("owner_pid must be a positive integer")

    owner_hostname = parsed.get("owner_hostname")
    if not isinstance(owner_hostname, str) or not owner_hostname.strip():
        raise ValueError("owner_hostname must be a non-empty string")

    owner_session_id = parsed.get("owner_session_id")
    if not isinstance(owner_session_id, str) or not owner_session_id.strip():
        raise ValueError("owner_session_id must be a non-empty string")

    acquired_raw = parsed.get("acquired_at")
    if not isinstance(acquired_raw, str) or not acquired_raw.strip():
        raise ValueError("acquired_at must be an ISO datetime string")
    try:
        acquired_at = datetime.fromisoformat(acquired_raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError("acquired_at must be an ISO datetime string") from exc
    if acquired_at.tzinfo is None:
        acquired_at = acquired_at.replace(tzinfo=timezone.utc)

    return RuntimeOwnershipRecord(
        workspace_root=workspace_root,
        owner_pid=owner_pid,
        owner_hostname=owner_hostname,
        owner_session_id=owner_session_id,
        acquired_at=acquired_at,
    )


def _ownership_error_message(status: RuntimeOwnershipLockStatus) -> str:
    if status.state == "stale":
        return (
            f"{status.detail}; run clear-stale-state to remove stale ownership "
            "before starting runtime again"
        )
    if status.state == "invalid":
        return (
            f"{status.detail}; run clear-stale-state to repair ownership lock "
            "before starting runtime again"
        )
    if status.state == "active" and status.record is not None:
        return (
            "workspace runtime ownership lock is already held by "
            f"pid={status.record.owner_pid} host={status.record.owner_hostname} "
            f"session={status.record.owner_session_id}"
        )
    return "workspace runtime ownership lock is already held"


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


__all__ = [
    "ClearRuntimeOwnershipLockResult",
    "RuntimeOwnershipLockError",
    "RuntimeOwnershipLockState",
    "RuntimeOwnershipLockStatus",
    "RuntimeOwnershipRecord",
    "acquire_runtime_ownership_lock",
    "clear_stale_runtime_ownership_lock",
    "inspect_runtime_ownership_lock",
    "release_runtime_ownership_lock",
]
