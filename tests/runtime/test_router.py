from __future__ import annotations

from millrace_ai.contracts import Plane, WorkItemKind
from millrace_ai.contracts.router import (
    RouterAction,
    RouterDecision,
    counter_key_for_failure_class,
)
from millrace_ai.router import (
    RouterAction as LegacyRouterAction,
)
from millrace_ai.router import (
    RouterDecision as LegacyRouterDecision,
)
from millrace_ai.router import (
    counter_key_for_failure_class as legacy_counter_key_for_failure_class,
)


def test_router_contracts_live_in_neutral_contract_module() -> None:
    decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="no_active_work",
    )

    assert decision.action is RouterAction.IDLE
    assert decision.next_plane is None
    assert decision.next_stage is None


def test_legacy_router_facade_reexports_contracts_only() -> None:
    assert LegacyRouterAction is RouterAction
    assert LegacyRouterDecision is RouterDecision
    assert legacy_counter_key_for_failure_class is counter_key_for_failure_class


def test_counter_key_for_failure_class_uses_neutral_contract_location() -> None:
    key = counter_key_for_failure_class(
        work_item_family_id="task",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        failure_class="Fix Cycle Exhausted",
    )

    assert key == "task:task-001:fix_cycle_exhausted"


def test_router_decision_accepts_graph_authority_metadata() -> None:
    decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=Plane.EXECUTION,
        next_stage=None,
        next_node_id="checker",
        next_stage_kind_id="checker",
        terminal_state_id="builder_complete",
        terminal_action_id="run_checker",
        reason="builder:BUILDER_COMPLETE",
        counter_mutation_name="fix_cycle_count",
        counter_key="task:task-001:fix_needed",
    )

    assert decision.action is RouterAction.RUN_STAGE
    assert decision.next_node_id == "checker"
    assert decision.counter_key == "task:task-001:fix_needed"
