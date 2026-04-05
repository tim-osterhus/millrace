"""Status contract helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Literal

from .contracts import ExecutionStatus, ResearchStatus, StageType
from .markdown import write_text_atomic


class ControlPlane(str, Enum):
    EXECUTION = "execution"
    RESEARCH = "research"


PlaneStatus = ExecutionStatus | ResearchStatus


class StatusError(ValueError):
    """Base status-contract failure."""


class StatusFormatError(StatusError):
    """Raised when a status file does not contain one marker line."""


class UnknownStatusMarkerError(StatusError):
    """Raised when a marker is unknown to the requested plane."""


class CrossPlaneStatusError(StatusError):
    """Raised when a marker belongs to the wrong control plane."""


class IllegalStatusTransitionError(StatusError):
    """Raised when a status transition is not legal."""


@dataclass(frozen=True, slots=True)
class StatusChange:
    """One authoritative status change observed by the runtime."""

    plane: ControlPlane
    previous: PlaneStatus | None
    current: PlaneStatus
    mode: Literal["transition", "confirmed", "raw"]


EXECUTION_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.IDLE: frozenset(
        {
            ExecutionStatus.BUILDER_RUNNING,
            ExecutionStatus.BUILDER_COMPLETE,
            ExecutionStatus.UPDATE_RUNNING,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
            ExecutionStatus.LARGE_PLAN_COMPLETE,
        }
    ),
    ExecutionStatus.BUILDER_RUNNING: frozenset(
        {
            ExecutionStatus.BUILDER_COMPLETE,
            ExecutionStatus.LARGE_PLAN_COMPLETE,
            ExecutionStatus.LARGE_EXECUTE_COMPLETE,
            ExecutionStatus.LARGE_REASSESS_COMPLETE,
            ExecutionStatus.LARGE_REFACTOR_COMPLETE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.BUILDER_COMPLETE: frozenset(
        {
            ExecutionStatus.INTEGRATION_RUNNING,
            ExecutionStatus.INTEGRATION_COMPLETE,
            ExecutionStatus.QA_RUNNING,
            ExecutionStatus.DOUBLECHECK_RUNNING,
            ExecutionStatus.QA_COMPLETE,
            ExecutionStatus.UPDATE_RUNNING,
            ExecutionStatus.UPDATE_COMPLETE,
            ExecutionStatus.IDLE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.HOTFIX_COMPLETE: frozenset(
        {
            ExecutionStatus.DOUBLECHECK_RUNNING,
            ExecutionStatus.QA_RUNNING,
            ExecutionStatus.QA_COMPLETE,
            ExecutionStatus.QUICKFIX_NEEDED,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.INTEGRATION_RUNNING: frozenset(
        {ExecutionStatus.INTEGRATION_COMPLETE, ExecutionStatus.BLOCKED, ExecutionStatus.NET_WAIT}
    ),
    ExecutionStatus.INTEGRATION_COMPLETE: frozenset(
        {
            ExecutionStatus.QA_RUNNING,
            ExecutionStatus.QA_COMPLETE,
            ExecutionStatus.QUICKFIX_NEEDED,
            ExecutionStatus.UPDATE_RUNNING,
            ExecutionStatus.UPDATE_COMPLETE,
            ExecutionStatus.IDLE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.QA_RUNNING: frozenset(
        {
            ExecutionStatus.QA_COMPLETE,
            ExecutionStatus.QUICKFIX_NEEDED,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.QA_COMPLETE: frozenset(
        {
            ExecutionStatus.UPDATE_RUNNING,
            ExecutionStatus.UPDATE_COMPLETE,
            ExecutionStatus.IDLE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.QUICKFIX_NEEDED: frozenset(
        {
            ExecutionStatus.HOTFIX_RUNNING,
            ExecutionStatus.BUILDER_RUNNING,
            ExecutionStatus.BUILDER_COMPLETE,
            ExecutionStatus.HOTFIX_COMPLETE,
            ExecutionStatus.TROUBLESHOOT_RUNNING,
            ExecutionStatus.CONSULT_RUNNING,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.HOTFIX_RUNNING: frozenset(
        {
            ExecutionStatus.BUILDER_COMPLETE,
            ExecutionStatus.HOTFIX_COMPLETE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.DOUBLECHECK_RUNNING: frozenset(
        {
            ExecutionStatus.QA_COMPLETE,
            ExecutionStatus.QUICKFIX_NEEDED,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.TROUBLESHOOT_RUNNING: frozenset(
        {
            ExecutionStatus.TROUBLESHOOT_COMPLETE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.TROUBLESHOOT_COMPLETE: frozenset(
        {
            ExecutionStatus.BUILDER_RUNNING,
            ExecutionStatus.BUILDER_COMPLETE,
            ExecutionStatus.HOTFIX_RUNNING,
            ExecutionStatus.HOTFIX_COMPLETE,
            ExecutionStatus.INTEGRATION_RUNNING,
            ExecutionStatus.INTEGRATION_COMPLETE,
            ExecutionStatus.QA_RUNNING,
            ExecutionStatus.QA_COMPLETE,
            ExecutionStatus.CONSULT_RUNNING,
            ExecutionStatus.CONSULT_COMPLETE,
            ExecutionStatus.IDLE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.CONSULT_RUNNING: frozenset(
        {
            ExecutionStatus.CONSULT_COMPLETE,
            ExecutionStatus.NEEDS_RESEARCH,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.CONSULT_COMPLETE: frozenset(
        {
            ExecutionStatus.BUILDER_RUNNING,
            ExecutionStatus.BUILDER_COMPLETE,
            ExecutionStatus.HOTFIX_RUNNING,
            ExecutionStatus.HOTFIX_COMPLETE,
            ExecutionStatus.INTEGRATION_RUNNING,
            ExecutionStatus.INTEGRATION_COMPLETE,
            ExecutionStatus.QA_RUNNING,
            ExecutionStatus.QA_COMPLETE,
            ExecutionStatus.IDLE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.NEEDS_RESEARCH: frozenset({ExecutionStatus.IDLE}),
    ExecutionStatus.BLOCKED: frozenset(
        {
            ExecutionStatus.BUILDER_RUNNING,
            ExecutionStatus.INTEGRATION_RUNNING,
            ExecutionStatus.QA_RUNNING,
            ExecutionStatus.HOTFIX_RUNNING,
            ExecutionStatus.DOUBLECHECK_RUNNING,
            ExecutionStatus.TROUBLESHOOT_RUNNING,
            ExecutionStatus.CONSULT_RUNNING,
            ExecutionStatus.UPDATE_RUNNING,
            ExecutionStatus.IDLE,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.UPDATE_RUNNING: frozenset(
        {ExecutionStatus.UPDATE_COMPLETE, ExecutionStatus.BLOCKED, ExecutionStatus.NET_WAIT}
    ),
    ExecutionStatus.UPDATE_COMPLETE: frozenset(
        {ExecutionStatus.IDLE, ExecutionStatus.BLOCKED, ExecutionStatus.NET_WAIT}
    ),
    ExecutionStatus.NET_WAIT: frozenset(
        {
            ExecutionStatus.IDLE,
            ExecutionStatus.BUILDER_RUNNING,
            ExecutionStatus.INTEGRATION_RUNNING,
            ExecutionStatus.QA_RUNNING,
            ExecutionStatus.HOTFIX_RUNNING,
            ExecutionStatus.DOUBLECHECK_RUNNING,
            ExecutionStatus.TROUBLESHOOT_RUNNING,
            ExecutionStatus.CONSULT_RUNNING,
            ExecutionStatus.UPDATE_RUNNING,
            ExecutionStatus.BLOCKED,
        }
    ),
    ExecutionStatus.LARGE_PLAN_COMPLETE: frozenset(
        {
            ExecutionStatus.BUILDER_RUNNING,
            ExecutionStatus.LARGE_EXECUTE_COMPLETE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.LARGE_EXECUTE_COMPLETE: frozenset(
        {
            ExecutionStatus.BUILDER_RUNNING,
            ExecutionStatus.LARGE_REASSESS_COMPLETE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.LARGE_REASSESS_COMPLETE: frozenset(
        {
            ExecutionStatus.BUILDER_RUNNING,
            ExecutionStatus.LARGE_REFACTOR_COMPLETE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
    ExecutionStatus.LARGE_REFACTOR_COMPLETE: frozenset(
        {
            ExecutionStatus.QA_RUNNING,
            ExecutionStatus.QA_COMPLETE,
            ExecutionStatus.QUICKFIX_NEEDED,
            ExecutionStatus.UPDATE_RUNNING,
            ExecutionStatus.UPDATE_COMPLETE,
            ExecutionStatus.IDLE,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.NET_WAIT,
        }
    ),
}


RESEARCH_RUNNING_STATUSES = frozenset(
    {
        ResearchStatus.GOALSPEC_RUNNING,
        ResearchStatus.INCIDENT_RUNNING,
        ResearchStatus.GOAL_INTAKE_RUNNING,
        ResearchStatus.COMPLETION_MANIFEST_RUNNING,
        ResearchStatus.OBJECTIVE_PROFILE_SYNC_RUNNING,
        ResearchStatus.SPEC_SYNTHESIS_RUNNING,
        ResearchStatus.SPEC_INTERVIEW_RUNNING,
        ResearchStatus.SPEC_REVIEW_RUNNING,
        ResearchStatus.CLARIFY_RUNNING,
        ResearchStatus.TASKMASTER_RUNNING,
        ResearchStatus.TASKAUDIT_RUNNING,
        ResearchStatus.CRITIC_RUNNING,
        ResearchStatus.DESIGNER_RUNNING,
        ResearchStatus.INCIDENT_INTAKE_RUNNING,
        ResearchStatus.INCIDENT_RESOLVE_RUNNING,
        ResearchStatus.INCIDENT_ARCHIVE_RUNNING,
        ResearchStatus.AUDIT_INTAKE_RUNNING,
        ResearchStatus.AUDIT_VALIDATE_RUNNING,
    }
)

RESEARCH_TRANSITIONS: dict[ResearchStatus, frozenset[ResearchStatus]] = {
    ResearchStatus.IDLE: frozenset(
        set(RESEARCH_RUNNING_STATUSES)
        | {ResearchStatus.AUDIT_RUNNING, ResearchStatus.BLOCKED, ResearchStatus.NET_WAIT}
    ),
    ResearchStatus.BLOCKED: frozenset(
        set(RESEARCH_RUNNING_STATUSES)
        | {ResearchStatus.IDLE, ResearchStatus.AUDIT_RUNNING, ResearchStatus.NET_WAIT}
    ),
    ResearchStatus.NET_WAIT: frozenset(
        set(RESEARCH_RUNNING_STATUSES)
        | {ResearchStatus.IDLE, ResearchStatus.BLOCKED, ResearchStatus.AUDIT_RUNNING}
    ),
    ResearchStatus.AUDIT_RUNNING: frozenset(
        {
            ResearchStatus.AUDIT_PASS,
            ResearchStatus.AUDIT_FAIL,
            ResearchStatus.BLOCKED,
            ResearchStatus.NET_WAIT,
        }
    ),
    ResearchStatus.AUDIT_PASS: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    ResearchStatus.AUDIT_FAIL: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
}
for running_status in RESEARCH_RUNNING_STATUSES:
    RESEARCH_TRANSITIONS[running_status] = frozenset(
        {ResearchStatus.IDLE, ResearchStatus.BLOCKED, ResearchStatus.NET_WAIT}
    )


EXECUTION_TERMINAL_MARKERS: dict[StageType, frozenset[ExecutionStatus]] = {
    StageType.BUILDER: frozenset({ExecutionStatus.BUILDER_COMPLETE, ExecutionStatus.BLOCKED}),
    StageType.HOTFIX: frozenset(
        {
            ExecutionStatus.BUILDER_COMPLETE,
            ExecutionStatus.HOTFIX_COMPLETE,
            ExecutionStatus.BLOCKED,
        }
    ),
    StageType.INTEGRATION: frozenset({ExecutionStatus.INTEGRATION_COMPLETE, ExecutionStatus.BLOCKED}),
    StageType.QA: frozenset(
        {ExecutionStatus.QA_COMPLETE, ExecutionStatus.QUICKFIX_NEEDED, ExecutionStatus.BLOCKED}
    ),
    StageType.DOUBLECHECK: frozenset(
        {ExecutionStatus.QA_COMPLETE, ExecutionStatus.QUICKFIX_NEEDED, ExecutionStatus.BLOCKED}
    ),
    StageType.TROUBLESHOOT: frozenset(
        {ExecutionStatus.TROUBLESHOOT_COMPLETE, ExecutionStatus.BLOCKED}
    ),
    StageType.CONSULT: frozenset(
        {
            ExecutionStatus.CONSULT_COMPLETE,
            ExecutionStatus.NEEDS_RESEARCH,
            ExecutionStatus.BLOCKED,
        }
    ),
    StageType.UPDATE: frozenset({ExecutionStatus.UPDATE_COMPLETE, ExecutionStatus.BLOCKED}),
    StageType.LARGE_PLAN: frozenset({ExecutionStatus.LARGE_PLAN_COMPLETE, ExecutionStatus.BLOCKED}),
    StageType.LARGE_EXECUTE: frozenset({ExecutionStatus.LARGE_EXECUTE_COMPLETE, ExecutionStatus.BLOCKED}),
    StageType.REASSESS: frozenset({ExecutionStatus.LARGE_REASSESS_COMPLETE, ExecutionStatus.BLOCKED}),
    StageType.REFACTOR: frozenset({ExecutionStatus.LARGE_REFACTOR_COMPLETE, ExecutionStatus.BLOCKED}),
}


RESEARCH_TERMINAL_MARKERS: dict[StageType, frozenset[ResearchStatus]] = {
    StageType.GOAL_INTAKE: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.OBJECTIVE_PROFILE_SYNC: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.SPEC_SYNTHESIS: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.SPEC_INTERVIEW: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.SPEC_REVIEW: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.TASKMASTER: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.TASKAUDIT: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.CLARIFY: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.CRITIC: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.DESIGNER: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.INCIDENT_INTAKE: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.INCIDENT_RESOLVE: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.INCIDENT_ARCHIVE: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.AUDIT_INTAKE: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
    StageType.AUDIT_VALIDATE: frozenset({ResearchStatus.IDLE, ResearchStatus.BLOCKED}),
}


def format_status_marker(status: PlaneStatus) -> str:
    """Format a status enum as the canonical status-file payload."""

    return status.marker + "\n"


def parse_status_marker(text: str, plane: ControlPlane) -> PlaneStatus:
    """Parse and validate a status marker for a specific control plane."""

    lines = [line.strip() for line in text.replace("\r\n", "\n").splitlines() if line.strip()]
    if len(lines) != 1 or not lines[0].startswith("### "):
        raise StatusFormatError("status files must contain exactly one authoritative marker line")

    token = lines[0].removeprefix("### ").strip()
    status_type = ExecutionStatus if plane is ControlPlane.EXECUTION else ResearchStatus
    other_type = ResearchStatus if plane is ControlPlane.EXECUTION else ExecutionStatus

    try:
        return status_type(token)
    except ValueError:
        pass

    try:
        other_type(token)
    except ValueError as exc:
        raise UnknownStatusMarkerError(f"unknown {plane.value} status marker: {token}") from exc

    raise CrossPlaneStatusError(f"{token} belongs to the other control plane")


def validate_transition(plane: ControlPlane, current: PlaneStatus, new: PlaneStatus) -> None:
    """Validate a status transition for the requested control plane."""

    if plane is ControlPlane.EXECUTION:
        if not isinstance(current, ExecutionStatus) or not isinstance(new, ExecutionStatus):
            raise CrossPlaneStatusError("execution status transitions require execution markers")
        allowed = EXECUTION_TRANSITIONS[current]
    else:
        if not isinstance(current, ResearchStatus) or not isinstance(new, ResearchStatus):
            raise CrossPlaneStatusError("research status transitions require research markers")
        allowed = RESEARCH_TRANSITIONS[current]

    if new not in allowed:
        raise IllegalStatusTransitionError(f"illegal {plane.value} transition: {current.value} -> {new.value}")


def validate_stage_terminal(stage: StageType, marker: PlaneStatus) -> None:
    """Validate that a terminal marker is legal for a stage."""

    if stage in EXECUTION_TERMINAL_MARKERS:
        if not isinstance(marker, ExecutionStatus):
            raise CrossPlaneStatusError(f"{stage.value} is an execution stage")
        if marker not in EXECUTION_TERMINAL_MARKERS[stage]:
            raise IllegalStatusTransitionError(f"{marker.value} is not a legal terminal for {stage.value}")
        if stage is StageType.TROUBLESHOOT and marker is ExecutionStatus.NEEDS_RESEARCH:
            raise IllegalStatusTransitionError("troubleshoot may not emit NEEDS_RESEARCH")
        return

    if stage in RESEARCH_TERMINAL_MARKERS:
        if not isinstance(marker, ResearchStatus):
            raise CrossPlaneStatusError(f"{stage.value} is a research stage")
        if marker not in RESEARCH_TERMINAL_MARKERS[stage]:
            raise IllegalStatusTransitionError(f"{marker.value} is not a legal terminal for {stage.value}")
        return

    raise IllegalStatusTransitionError(f"no terminal marker contract registered for stage {stage.value}")


class StatusStore:
    """Validated overwrite-only status-file helper."""

    def __init__(
        self,
        path: Path,
        plane: ControlPlane,
        *,
        on_change: Callable[[StatusChange], None] | None = None,
    ) -> None:
        self.path = path
        self.plane = plane
        self.on_change = on_change

    def _emit_change(
        self,
        *,
        previous: PlaneStatus | None,
        current: PlaneStatus,
        mode: Literal["transition", "confirmed", "raw"],
    ) -> None:
        if self.on_change is None:
            return
        self.on_change(
            StatusChange(
                plane=self.plane,
                previous=previous,
                current=current,
                mode=mode,
            )
        )

    def _validated_marker(self, status: PlaneStatus) -> str:
        marker = format_status_marker(status)
        parse_status_marker(marker, self.plane)
        return marker

    def _existing_status(self) -> PlaneStatus | None:
        if not self.path.exists():
            return None
        try:
            return self.read()
        except StatusError:
            return None

    def read_raw(self) -> str:
        """Return the raw file contents."""

        return self.path.read_text(encoding="utf-8")

    def read(self) -> PlaneStatus:
        """Read and validate the current plane marker."""

        return parse_status_marker(self.read_raw(), self.plane)

    def write_raw(self, status: PlaneStatus) -> PlaneStatus:
        """Override the file with one validated marker without transition checks."""

        marker = self._validated_marker(status)
        previous = self._existing_status()
        write_text_atomic(self.path, marker)
        self._emit_change(previous=previous, current=status, mode="raw")
        return status

    def write(self, status: PlaneStatus) -> PlaneStatus:
        """Compatibility alias for the explicit raw override path."""

        return self.write_raw(status)

    def transition(self, status: PlaneStatus) -> PlaneStatus:
        """Validate and apply a legal state transition."""

        current = self.read()
        validate_transition(self.plane, current, status)
        write_text_atomic(self.path, self._validated_marker(status))
        self._emit_change(previous=current, current=status, mode="transition")
        return status

    def confirm_transition(self, status: PlaneStatus, *, previous: PlaneStatus) -> PlaneStatus:
        """Confirm or apply a transition when the target marker may already be present."""

        validate_transition(self.plane, previous, status)
        current = self.read()
        if current == status:
            self._emit_change(previous=previous, current=status, mode="confirmed")
            return status
        if current != previous:
            raise IllegalStatusTransitionError(
                f"expected {previous.value} or {status.value} before confirmation, found {current.value}"
            )
        write_text_atomic(self.path, self._validated_marker(status))
        self._emit_change(previous=previous, current=status, mode="confirmed")
        return status
