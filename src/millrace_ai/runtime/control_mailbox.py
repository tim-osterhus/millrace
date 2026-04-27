"""Mailbox-safe control routing for daemon-owned workspaces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Generic, Protocol, TypeVar
from uuid import uuid4

from pydantic import JsonValue, ValidationError

from millrace_ai.contracts import MailboxCommand, MailboxCommandEnvelope, RuntimeSnapshot
from millrace_ai.errors import ControlRoutingError
from millrace_ai.mailbox import write_mailbox_command
from millrace_ai.paths import WorkspacePaths
from millrace_ai.runtime_lock import inspect_runtime_ownership_lock
from millrace_ai.state_store import load_snapshot

ResultT = TypeVar("ResultT")
ResultT_co = TypeVar("ResultT_co", covariant=True)


class ControlActionResultFactory(Protocol[ResultT_co]):
    """Build one public control result object."""

    def __call__(
        self,
        *,
        action: MailboxCommand,
        mode: str,
        applied: bool,
        detail: str,
        command_id: str | None = None,
        mailbox_path: Path | None = None,
        artifact_path: Path | None = None,
    ) -> ResultT_co: ...


class DirectControlHandler(Protocol[ResultT_co]):
    """Apply one direct control mutation against the current runtime snapshot."""

    def __call__(self, snapshot: RuntimeSnapshot) -> ResultT_co: ...


class MailboxControlRouter(Generic[ResultT]):
    """Route control commands directly or through mailbox storage as needed."""

    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        result_factory: ControlActionResultFactory[ResultT],
        now: Callable[[], datetime],
    ) -> None:
        self.paths = paths
        self._result_factory = result_factory
        self._now = now

    def dispatch(
        self,
        *,
        command: MailboxCommand,
        issuer: str,
        direct_handler: DirectControlHandler[ResultT],
        payload: Mapping[str, JsonValue] | None = None,
    ) -> ResultT:
        snapshot = load_snapshot(self.paths)
        if command is MailboxCommand.CLEAR_STALE_STATE:
            lock_status = inspect_runtime_ownership_lock(self.paths)
            if lock_status.state in {"absent", "stale", "invalid"}:
                return direct_handler(snapshot)
        if self.daemon_owns_workspace():
            return self.enqueue_mailbox_command(command=command, issuer=issuer, payload=payload)
        return direct_handler(snapshot)

    def enqueue_mailbox_command(
        self,
        *,
        command: MailboxCommand,
        issuer: str,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> ResultT:
        command_id = self._command_id(command)
        envelope = MailboxCommandEnvelope(
            command_id=command_id,
            command=command,
            issued_at=self._now(),
            issuer=issuer,
            payload=dict(payload or {}),
        )
        try:
            mailbox_path = write_mailbox_command(self.paths, envelope)
        except (OSError, ValidationError, ValueError) as exc:
            raise ControlRoutingError(
                f"unable to enqueue {command.value} control command: {exc}"
            ) from exc
        return self._result_factory(
            action=command,
            mode="mailbox",
            applied=False,
            detail=_mailbox_detail(command),
            command_id=command_id,
            mailbox_path=mailbox_path,
        )

    def daemon_owns_workspace(self) -> bool:
        status = inspect_runtime_ownership_lock(self.paths)
        return status.state == "active"

    def _command_id(self, command: MailboxCommand) -> str:
        timestamp_ms = int(self._now().timestamp() * 1000)
        return f"{command.value}-{timestamp_ms}-{uuid4().hex[:8]}"


def _mailbox_detail(command: MailboxCommand) -> str:
    if command is MailboxCommand.RELOAD_CONFIG:
        return "queued for daemon processing on the next runtime tick"
    return "queued for daemon processing"


__all__ = ["ControlActionResultFactory", "DirectControlHandler", "MailboxControlRouter"]
