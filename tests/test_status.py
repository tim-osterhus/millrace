from __future__ import annotations

from pathlib import Path

import pytest

from millrace_engine.contracts import ExecutionStatus, ResearchStatus, StageType
from millrace_engine.status import (
    ControlPlane,
    CrossPlaneStatusError,
    IllegalStatusTransitionError,
    StatusChange,
    StatusStore,
    UnknownStatusMarkerError,
    validate_stage_terminal,
    validate_transition,
)


def test_execution_status_transitions_accept_legal_edges() -> None:
    validate_transition(ControlPlane.EXECUTION, ExecutionStatus.IDLE, ExecutionStatus.BUILDER_COMPLETE)
    validate_transition(
        ControlPlane.EXECUTION,
        ExecutionStatus.QUICKFIX_NEEDED,
        ExecutionStatus.BUILDER_COMPLETE,
    )
    validate_transition(
        ControlPlane.EXECUTION,
        ExecutionStatus.BLOCKED,
        ExecutionStatus.CONSULT_RUNNING,
    )


def test_research_status_transitions_accept_public_stub_edges() -> None:
    validate_transition(ControlPlane.RESEARCH, ResearchStatus.IDLE, ResearchStatus.GOALSPEC_RUNNING)
    validate_transition(ControlPlane.RESEARCH, ResearchStatus.GOALSPEC_RUNNING, ResearchStatus.IDLE)


def test_execution_status_transitions_reject_illegal_edges() -> None:
    with pytest.raises(IllegalStatusTransitionError):
        validate_transition(
            ControlPlane.EXECUTION,
            ExecutionStatus.BUILDER_COMPLETE,
            ExecutionStatus.NEEDS_RESEARCH,
        )


def test_status_store_rejects_wrong_plane_and_unknown_marker(tmp_path: Path) -> None:
    execution_status_path = tmp_path / "status.md"
    execution_status_path.write_text("### GOAL_INTAKE_RUNNING\n", encoding="utf-8")

    with pytest.raises(CrossPlaneStatusError):
        StatusStore(execution_status_path, ControlPlane.EXECUTION).read()

    execution_status_path.write_text("### SOMETHING_ELSE\n", encoding="utf-8")
    with pytest.raises(UnknownStatusMarkerError):
        StatusStore(execution_status_path, ControlPlane.EXECUTION).read()


def test_status_store_writes_one_authoritative_marker_line(tmp_path: Path) -> None:
    research_status_path = tmp_path / "research_status.md"
    store = StatusStore(research_status_path, ControlPlane.RESEARCH)

    store.write_raw(ResearchStatus.IDLE)

    assert research_status_path.read_text(encoding="utf-8") == "### IDLE\n"


def test_status_store_emits_authoritative_transition_events(tmp_path: Path) -> None:
    status_path = tmp_path / "status.md"
    status_path.write_text("### IDLE\n", encoding="utf-8")
    observed: list[StatusChange] = []
    store = StatusStore(status_path, ControlPlane.EXECUTION, on_change=observed.append)

    store.transition(ExecutionStatus.BUILDER_RUNNING)
    status_path.write_text("### BUILDER_COMPLETE\n", encoding="utf-8")
    store.confirm_transition(
        ExecutionStatus.BUILDER_COMPLETE,
        previous=ExecutionStatus.BUILDER_RUNNING,
    )

    assert observed == [
        StatusChange(
            plane=ControlPlane.EXECUTION,
            previous=ExecutionStatus.IDLE,
            current=ExecutionStatus.BUILDER_RUNNING,
            mode="transition",
        ),
        StatusChange(
            plane=ControlPlane.EXECUTION,
            previous=ExecutionStatus.BUILDER_RUNNING,
            current=ExecutionStatus.BUILDER_COMPLETE,
            mode="confirmed",
        ),
    ]


def test_stage_terminal_validation_rejects_needs_research_for_troubleshoot() -> None:
    validate_stage_terminal(StageType.CONSULT, ExecutionStatus.NEEDS_RESEARCH)
    with pytest.raises(IllegalStatusTransitionError):
        validate_stage_terminal(StageType.TROUBLESHOOT, ExecutionStatus.NEEDS_RESEARCH)
