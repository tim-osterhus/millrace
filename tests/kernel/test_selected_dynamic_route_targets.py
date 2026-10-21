"""Selected dynamic route targets are typed like activation fields.

Synthetic/offline; never native evidence. Reuses the dynamic-only variant from
tests/operator/test_dispatch_dynamic_route_targets.py, where the selector target
is reachable only through ``dynamic_target_selector``.
"""

from __future__ import annotations

from millrace.contracts.ids import QueueFamilyId, RunnerBindingId, StageKindId
from millrace.contracts.runtime_failure_history import selected_activation_targets
from millrace.operator import dispatch
from tests.operator.test_dispatch_dynamic_route_targets import (
    DYNAMIC_ACTIVATION_ID,
    DYNAMIC_TARGET_TUPLE,
    _dynamic_state,
    _variant,
)


def _typed(target: tuple[str, str, str, str]) -> tuple[object, ...]:
    queue, stage, node, binding = target
    return (QueueFamilyId(queue), StageKindId(stage), node, RunnerBindingId(binding))


def test_dynamic_selector_target_is_a_typed_selected_activation_target() -> None:
    plan, _fingerprint = _variant()
    assert _typed(DYNAMIC_TARGET_TUPLE) in selected_activation_targets(plan)


def test_kernel_routed_dynamic_activation_is_selected_everywhere() -> None:
    state, plan, _fingerprint = _dynamic_state()
    activation = state.activations[DYNAMIC_ACTIVATION_ID]
    work = state.work_items[activation.work_item_id]
    observed = (
        work.queue_family_id,
        activation.stage_kind_id,
        activation.graph_node_id,
        activation.runner_binding_id,
    )
    assert observed in selected_activation_targets(plan)
    assert observed in dispatch._selected_route_targets(plan)
