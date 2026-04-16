"""Public runtime control abstraction over routing and direct mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from pydantic import JsonValue

from millrace_ai.contracts import (
    MailboxAddIdeaPayload,
    MailboxAddSpecPayload,
    MailboxAddTaskPayload,
    MailboxCommand,
    Plane,
    SpecDocument,
    TaskDocument,
)
from millrace_ai.paths import WorkspacePaths, bootstrap_workspace, workspace_paths
from millrace_ai.state_store import load_snapshot

from .control_mailbox import MailboxControlRouter
from .control_mutations import DirectControlMutations


@dataclass(frozen=True, slots=True)
class ControlActionResult:
    """Outcome for one control action request."""

    action: MailboxCommand
    mode: str
    applied: bool
    detail: str
    command_id: str | None = None
    mailbox_path: Path | None = None
    artifact_path: Path | None = None


class RuntimeControl:
    """Control API that switches between direct and mailbox-safe mutation paths."""

    def __init__(self, target: WorkspacePaths | Path | str) -> None:
        paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
        self.paths = bootstrap_workspace(paths)
        self._router = MailboxControlRouter(
            self.paths,
            result_factory=ControlActionResult,
            now=self._now,
        )
        self._mutations = DirectControlMutations(
            self.paths,
            result_factory=ControlActionResult,
            now=self._now,
        )

    def pause_runtime(self, *, issuer: str = "operator") -> ControlActionResult:
        return self._router.dispatch(
            command=MailboxCommand.PAUSE,
            issuer=issuer,
            direct_handler=self._mutations.pause,
        )

    def resume_runtime(self, *, issuer: str = "operator") -> ControlActionResult:
        return self._router.dispatch(
            command=MailboxCommand.RESUME,
            issuer=issuer,
            direct_handler=self._mutations.resume,
        )

    def stop_runtime(self, *, issuer: str = "operator") -> ControlActionResult:
        return self._router.dispatch(
            command=MailboxCommand.STOP,
            issuer=issuer,
            direct_handler=self._mutations.stop,
        )

    def retry_active(
        self,
        *,
        reason: str = "operator requested retry",
        issuer: str = "operator",
    ) -> ControlActionResult:
        payload = {"reason": reason.strip() or "operator requested retry"}
        return self._router.dispatch(
            command=MailboxCommand.RETRY_ACTIVE,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda snapshot: self._mutations.retry_active(
                snapshot,
                reason=payload["reason"],
                scope=None,
            ),
        )

    def retry_active_planning(
        self,
        *,
        reason: str = "operator requested planning retry",
        issuer: str = "operator",
    ) -> ControlActionResult:
        snapshot = load_snapshot(self.paths)
        if snapshot.active_plane is not Plane.PLANNING:
            active_plane = snapshot.active_plane.value if snapshot.active_plane is not None else "none"
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=False,
                detail=(
                    "planning retry requires active planning work; "
                    f"current active plane is {active_plane}"
                ),
            )

        payload: Mapping[str, JsonValue] = {
            "reason": reason.strip() or "operator requested planning retry",
            "scope": Plane.PLANNING.value,
        }
        return self._router.dispatch(
            command=MailboxCommand.RETRY_ACTIVE,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda current_snapshot: self._mutations.retry_active(
                current_snapshot,
                reason=str(payload["reason"]),
                scope=Plane.PLANNING,
            ),
        )

    def clear_stale_state(
        self,
        *,
        reason: str = "operator requested stale-state clear",
        issuer: str = "operator",
    ) -> ControlActionResult:
        payload = {"reason": reason.strip() or "operator requested stale-state clear"}
        return self._router.dispatch(
            command=MailboxCommand.CLEAR_STALE_STATE,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda snapshot: self._mutations.clear_stale(
                snapshot,
                reason=payload["reason"],
            ),
        )

    def reload_config(self, *, issuer: str = "operator") -> ControlActionResult:
        return self._router.dispatch(
            command=MailboxCommand.RELOAD_CONFIG,
            issuer=issuer,
            direct_handler=self._mutations.reload_config,
        )

    def add_task(self, document: TaskDocument, *, issuer: str = "operator") -> ControlActionResult:
        payload = MailboxAddTaskPayload(document=document).model_dump(mode="json")
        return self._router.dispatch(
            command=MailboxCommand.ADD_TASK,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda snapshot: self._mutations.add_task(snapshot, document=document),
        )

    def add_spec(self, document: SpecDocument, *, issuer: str = "operator") -> ControlActionResult:
        payload = MailboxAddSpecPayload(document=document).model_dump(mode="json")
        return self._router.dispatch(
            command=MailboxCommand.ADD_SPEC,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda snapshot: self._mutations.add_spec(snapshot, document=document),
        )

    def add_idea_markdown(
        self,
        *,
        source_name: str,
        markdown: str,
        issuer: str = "operator",
    ) -> ControlActionResult:
        payload_model = MailboxAddIdeaPayload(source_name=source_name, markdown=markdown)
        payload = payload_model.model_dump(mode="json")
        return self._router.dispatch(
            command=MailboxCommand.ADD_IDEA,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda snapshot: self._mutations.add_idea(snapshot, payload=payload_model),
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)


__all__ = ["ControlActionResult", "RuntimeControl"]
