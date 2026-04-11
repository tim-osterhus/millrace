"""Lock, retry, and idle lifecycle helpers for the research supervisor."""

from __future__ import annotations

import fcntl
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..contracts import ResearchMode, ResearchStatus
from ..events import EventType
from ..research.incidents import IncidentExecutionError
from ..status import RESEARCH_RUNNING_STATUSES
from .audit import AuditExecutionError
from .dispatcher import ResearchDispatchError
from .state import (
    ResearchLockScope,
    ResearchLockState,
    ResearchQueueFamily,
    ResearchQueueSelectionAuthority,
    ResearchRuntimeMode,
    ResearchStageRetryState,
)
from .supervisor_payloads import (
    blocked_payload,
    idle_payload,
    lock_payload,
    retry_scheduled_payload,
)


def should_scan(self: Any, *, trigger: str, observed_at: datetime) -> bool:
    if self.state.checkpoint is not None:
        return should_continue_checkpoint(self, observed_at=observed_at)
    if trigger != "daemon-loop":
        return True
    if self.state.retry_state is not None and not self.state.retry_due(observed_at):
        return False
    if self.config.research.idle_mode == "watch":
        return False
    return self.state.poll_due(observed_at)


def should_continue_checkpoint(self: Any, *, observed_at: datetime) -> bool:
    """Gate checkpoint continuation separately from fresh daemon rescans."""

    if self.state.checkpoint is None:
        return False
    if self.state.retry_state is not None and not self.state.retry_due(observed_at):
        return False
    return True


def lock_path(self: Any) -> Path:
    return self.paths.agents_dir / ".locks" / "research_loop.lock"


def lock_expiry(self: Any, observed_at: datetime) -> datetime:
    timeout_seconds = max(
        self.config.research.idle_poll_seconds,
        self.config.research.stage_retry_backoff_seconds,
        1,
    )
    return observed_at + timedelta(seconds=timeout_seconds * 2)


def release_lock(self: Any) -> None:
    if self._lock_handle is None:
        return
    lock_state = self.state.lock_state
    try:
        fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
    finally:
        self._lock_handle.close()
        self._lock_handle = None
    if lock_state is not None:
        self._emit(EventType.RESEARCH_LOCK_RELEASED, lock_payload(lock_state))


def acquire_lock(self: Any, *, observed_at: datetime) -> None:
    acquired_new = False
    if self._lock_handle is None:
        lock_path = self._lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            message = f"research loop lock is already held: {lock_path.as_posix()}"
            self._record_lock_failure(message=message, failed_at=observed_at)
            raise self._lock_error_cls(message) from exc
        self._lock_handle = handle
        acquired_new = True

    lock_state = ResearchLockState(
        lock_key="research-loop",
        owner_id=self._owner_id,
        scope=ResearchLockScope.PLANE_RUN,
        lock_path=self._lock_path(),
        acquired_at=observed_at,
        heartbeat_at=observed_at,
        expires_at=self._lock_expiry(observed_at),
    )
    self.state = self.state.model_copy(
        update={
            "updated_at": observed_at,
            "lock_state": lock_state,
        }
    )
    if acquired_new:
        payload = lock_payload(lock_state)
        payload["expires_at"] = lock_state.expires_at
        self._emit(EventType.RESEARCH_LOCK_ACQUIRED, payload)


def record_lock_failure(self: Any, *, message: str, failed_at: datetime) -> None:
    self.status_store.write_raw(ResearchStatus.BLOCKED)
    configured_mode = self._configured_runtime_mode()
    previous_mode = self.state.current_mode
    transition_count = self.state.transition_count
    if previous_mode is not configured_mode:
        transition_count += 1
    self.state = self.state.model_copy(
        update={
            "updated_at": failed_at,
            "current_mode": configured_mode,
            "last_mode": previous_mode,
            "mode_reason": message,
            "cycle_count": self.state.cycle_count + 1,
            "transition_count": transition_count,
            "lock_state": None,
            "next_poll_at": self._next_poll_at(failed_at),
        }
    )
    self._last_dispatch = None
    self._persist_state()
    self._emit(
        EventType.RESEARCH_BLOCKED,
        {
            "configured_mode": self.config.research.mode.value,
            "current_mode": self.state.current_mode.value,
            "reason": message,
            "failure_kind": "lock_unavailable",
            "next_poll_at": self.state.next_poll_at,
        },
    )


def set_research_status(self: Any, target: ResearchStatus) -> None:
    current = self.status_store.read()
    if current is target:
        return
    if current in RESEARCH_RUNNING_STATUSES and target in RESEARCH_RUNNING_STATUSES:
        self.status_store.write_raw(ResearchStatus.IDLE)
    self.status_store.write_raw(target)


def release_execution_lock(self: Any, *, observed_at: datetime) -> None:
    if self.state.lock_state is None and self._lock_handle is None:
        return
    self._release_lock()
    self.state = self.state.model_copy(
        update={
            "updated_at": observed_at,
            "lock_state": None,
        }
    )
    self._persist_state()


def record_dispatch_failure(
    self: Any,
    error: ResearchDispatchError | IncidentExecutionError | AuditExecutionError,
    *,
    discovery: Any,
    failed_at: datetime,
) -> None:
    self.status_store.write_raw(ResearchStatus.BLOCKED)
    self._last_dispatch = None
    self._release_lock()
    configured_mode = self._configured_runtime_mode()
    previous_mode = self.state.current_mode
    transition_count = self.state.transition_count
    if previous_mode is not configured_mode:
        transition_count += 1
    retry_state = self._next_retry_state(error=error, failed_at=failed_at)
    checkpoint = self.state.checkpoint
    queue_snapshot = discovery.to_snapshot(last_scanned_at=failed_at)
    if checkpoint is not None:
        checkpoint_update: dict[str, object] = {
            "attempt": retry_state.attempt,
            "updated_at": failed_at,
        }
        try:
            from .goalspec import GoalSpecReviewBlockedError
        except Exception:  # pragma: no cover - defensive import fallback
            GoalSpecReviewBlockedError = None  # type: ignore[assignment]
        if (
            GoalSpecReviewBlockedError is not None
            and isinstance(error, GoalSpecReviewBlockedError)
            and checkpoint.node_id == "spec_review"
            and retry_state.attempt <= 2
        ):
            checkpoint_update.update(
                {
                    "node_id": "mechanic",
                    "stage_kind_id": "research.mechanic",
                    "status": ResearchStatus.GOALSPEC_RUNNING,
                }
            )
        checkpoint = checkpoint.model_copy(
            update=checkpoint_update
        )
        configured_mode = checkpoint.mode
        queue_snapshot = discovery.to_snapshot(
            ownerships=checkpoint.owned_queues,
            last_scanned_at=failed_at,
            selected_family=self._resume_selected_family(checkpoint),
            selected_family_authority=ResearchQueueSelectionAuthority.CHECKPOINT,
        )
    self.state = self.state.model_copy(
        update={
            "updated_at": failed_at,
            "current_mode": configured_mode,
            "last_mode": previous_mode,
            "mode_reason": str(error),
            "cycle_count": self.state.cycle_count + 1,
            "transition_count": transition_count,
            "queue_snapshot": queue_snapshot,
            "retry_state": retry_state,
            "lock_state": None,
            "checkpoint": checkpoint,
            "next_poll_at": self._next_poll_at(failed_at),
        }
    )
    self._persist_state()
    self._emit(
        EventType.RESEARCH_BLOCKED,
        blocked_payload(
            self,
            queue_snapshot=queue_snapshot,
            checkpoint=checkpoint,
            reason=str(error),
            failure_kind=type(error).__name__,
        ),
    )
    self._emit(EventType.RESEARCH_RETRY_SCHEDULED, retry_scheduled_payload(retry_state))


def record_no_dispatchable_work(
    self: Any,
    *,
    discovery: Any,
    observed_at: datetime,
    reason: str,
) -> None:
    self.shutdown(persist=False)
    self.status_store.write_raw(ResearchStatus.IDLE)
    self._last_dispatch = None
    configured_mode = self._configured_runtime_mode()
    previous_mode = self.state.current_mode
    transition_count = self.state.transition_count
    if previous_mode is not configured_mode:
        transition_count += 1
    self.state = self.state.model_copy(
        update={
            "updated_at": observed_at,
            "current_mode": configured_mode,
            "last_mode": previous_mode,
            "mode_reason": reason,
            "cycle_count": self.state.cycle_count + 1,
            "transition_count": transition_count,
            "queue_snapshot": discovery.to_snapshot(last_scanned_at=observed_at),
            "checkpoint": None,
            "retry_state": None,
            "lock_state": None,
            "next_poll_at": self._next_poll_at(observed_at),
        }
    )
    self._persist_state()
    self._emit(EventType.RESEARCH_IDLE, idle_payload(self, discovery, observed_at=observed_at, reason=reason))


def next_retry_state(
    self: Any,
    *,
    error: ResearchDispatchError | IncidentExecutionError | AuditExecutionError,
    failed_at: datetime,
) -> ResearchStageRetryState:
    previous = self.state.retry_state
    typed_signature = getattr(error, "failure_signature", None)
    normalized_typed_signature = str(typed_signature).strip() if typed_signature is not None else ""
    signature = (
        f"{type(error).__name__}:{normalized_typed_signature}"
        if normalized_typed_signature
        else f"{type(error).__name__}:{error}"
    )
    same_failure = previous is not None and previous.last_failure_signature == signature
    max_attempts = self.config.research.stage_retry_max + 1
    attempt = (previous.attempt + 1) if same_failure and previous is not None else 1
    attempt = min(attempt, max_attempts)
    backoff_seconds = float(self.config.research.stage_retry_backoff_seconds) if attempt < max_attempts else 0.0
    next_retry_at = failed_at + timedelta(seconds=backoff_seconds) if backoff_seconds > 0 else None
    return ResearchStageRetryState(
        attempt=attempt,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        next_retry_at=next_retry_at,
        last_failure_reason=str(error),
        last_failure_signature=signature,
    )


def next_poll_at(self: Any, observed_at: datetime) -> datetime | None:
    if self.config.research.idle_mode != "poll":
        return None
    return observed_at + timedelta(seconds=self.config.research.idle_poll_seconds)


def configured_runtime_mode(self: Any) -> ResearchRuntimeMode:
    if self.config.research.mode is ResearchMode.STUB:
        return ResearchRuntimeMode.STUB
    return ResearchRuntimeMode.from_value(self.config.research.mode)


def no_work_reason_for_selection(self: Any, runtime_mode: ResearchRuntimeMode) -> str:
    if runtime_mode is ResearchRuntimeMode.GOALSPEC:
        return "forced-by-config; no-goalspec-queue-ready"
    if runtime_mode is ResearchRuntimeMode.INCIDENT:
        return "forced-by-config; no-incident-or-blocker-queue-ready"
    if runtime_mode is ResearchRuntimeMode.AUDIT:
        return "forced-by-config; no-audit-queue-ready"
    family_name = ResearchQueueFamily(runtime_mode.value.lower()).value
    return f"forced-by-config; no-{family_name}-queue-ready"
