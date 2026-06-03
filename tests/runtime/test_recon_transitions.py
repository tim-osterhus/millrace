from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from millrace_ai.contracts import (
    PlanningTerminalResult,
    ResultClass,
    StageResultEnvelope,
    TerminalOutcome,
)
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runtime.recon_transitions import apply_recon_router_decision, is_recon_stage_result

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _recon_result(terminal_result: PlanningTerminalResult) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-recon",
        plane="planning",
        stage="recon",
        node_id="recon",
        stage_kind_id="recon",
        work_item_kind="probe",
        work_item_id="probe-001",
        terminal_result=terminal_result,
        result_class=(
            ResultClass.BLOCKED
            if terminal_result is PlanningTerminalResult.RECON_BLOCKED
            else ResultClass.SUCCESS
        ),
        summary_status_marker=f"### {terminal_result.value}",
        success=terminal_result is not PlanningTerminalResult.RECON_BLOCKED,
        started_at=NOW,
        completed_at=NOW,
    )


def test_is_recon_stage_result_requires_recon_probe_identity() -> None:
    result = _recon_result(PlanningTerminalResult.RECON_TO_EXECUTION)

    assert is_recon_stage_result(result) is True
    assert is_recon_stage_result(result.model_copy(update={"stage_kind_id": "planner"})) is False
    assert is_recon_stage_result(result.model_copy(update={"work_item_kind": "spec"})) is False


def test_successful_recon_terminals_require_idle_router_decision() -> None:
    result = _recon_result(PlanningTerminalResult.RECON_TO_EXECUTION)

    with pytest.raises(ValueError, match="successful recon terminal results require an idle"):
        apply_recon_router_decision(
            SimpleNamespace(),
            RouterDecision(
                action=RouterAction.RUN_STAGE,
                next_plane=None,
                next_stage=None,
                reason="unexpected",
                runtime_operation_id="recon.enqueue_task",
            ),
            result,
        )


def test_blocked_recon_terminals_require_blocked_router_decision() -> None:
    result = _recon_result(PlanningTerminalResult.RECON_BLOCKED)

    with pytest.raises(ValueError, match="blocked recon terminal results require a blocked"):
        apply_recon_router_decision(
            SimpleNamespace(),
            RouterDecision(
                action=RouterAction.IDLE,
                next_plane=None,
                next_stage=None,
                reason="idle",
                runtime_operation_id="recon.block_work_item",
            ),
            result,
        )


def test_recon_route_validation_uses_runtime_operation_metadata_for_custom_outcome() -> None:
    result = _recon_result(PlanningTerminalResult.RECON_NOOP).model_copy(
        update={
            "terminal_result": TerminalOutcome("CUSTOM_RECON_NOOP"),
            "summary_status_marker": "### CUSTOM_RECON_NOOP",
        }
    )

    with pytest.raises(ValueError, match="successful recon terminal results require an idle"):
        apply_recon_router_decision(
            SimpleNamespace(),
            RouterDecision(
                action=RouterAction.RUN_STAGE,
                next_plane=None,
                next_stage=None,
                reason="unexpected",
                runtime_operation_id="recon.noop",
                terminal_action_router_consequence="idle",
            ),
            result,
        )


def test_recon_route_validation_does_not_use_terminal_state_fallback() -> None:
    result = _recon_result(PlanningTerminalResult.RECON_NOOP)

    with pytest.raises(
        ValueError,
        match="recompile or update the compiled plan",
    ):
        apply_recon_router_decision(
            SimpleNamespace(),
            RouterDecision(
                action=RouterAction.IDLE,
                next_plane=None,
                next_stage=None,
                reason="recon_noop",
                terminal_state_id="recon_noop",
                terminal_action_id="no_op_complete_work_item",
                terminal_action_router_consequence="idle",
            ),
            result,
        )


def test_recon_route_validation_does_not_use_raw_outcome_fallback() -> None:
    result = _recon_result(PlanningTerminalResult.RECON_NOOP)

    with pytest.raises(
        ValueError,
        match="runtime_operation_id",
    ):
        apply_recon_router_decision(
            SimpleNamespace(),
            RouterDecision(
                action=RouterAction.IDLE,
                next_plane=None,
                next_stage=None,
                reason="recon_noop",
            ),
            result,
        )
