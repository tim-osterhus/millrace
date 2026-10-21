from __future__ import annotations

from typing import cast

from tests.compiler.test_runtime_failure_mappings import _compile
from tests.kernel.test_recovery_recorded_source_return import (
    _RECURRENCE_ACTION_ID,
    _recorded_source_return_source,
)
from tests.substrate.test_persistence_integrity_refusals import (
    _apply_generic_observation,
    _generic_parent_claimed_state,
)
from tests.support import generic_lifecycle

from millrace.compiler.canonical import authority_fingerprint
from millrace.testing import fake_runner_dispatch_envelope_for_run
from support import generic_admission, generic_fanout

_ORDINARY_ENTRY_OUTCOME = "admission.enter_recovery_ordinary"
_ORDINARY_ENTRY_ACTION = "admission.enter_recovery_ordinary_route"
_ORDINARY_BLOCKED_OUTCOME = "admission.recovery.ordinary_blocked"
_ORDINARY_BLOCKED_ACTION = "admission.recovery.ordinary_blocked_route"
_ORDINARY_COMPLETE_OUTCOME = "admission.recovery.ordinary_complete"
_ORDINARY_COMPLETE_ACTION = "admission.recovery.ordinary_complete"


def _ordinary_recovery_ingress_source() -> dict[str, object]:
    source = _recorded_source_return_source()
    stages = cast(list[dict[str, object]], source["stage_kinds"])
    outcomes = cast(list[dict[str, object]], source["terminal_outcomes"])
    actions = cast(list[dict[str, object]], source["terminal_actions"])

    parent = next(
        stage
        for stage in stages
        if stage["id"] == generic_admission.PARENT_STAGE_ID
    )
    parent["declared_outcome_ids"] = (
        *cast(tuple[str, ...], parent["declared_outcome_ids"]),
        _ORDINARY_ENTRY_OUTCOME,
    )
    outcomes.append(
        {
            "id": _ORDINARY_ENTRY_OUTCOME,
            "stage_kind_id": generic_admission.PARENT_STAGE_ID,
            "marker": "ENTER_RECOVERY_ORDINARY",
        }
    )
    actions.append(
        {
            "id": _ORDINARY_ENTRY_ACTION,
            "stage_kind_id": generic_admission.PARENT_STAGE_ID,
            "outcome_id": _ORDINARY_ENTRY_OUTCOME,
            "kind": "route",
            "target_stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
            "target_graph_node_id": generic_admission.RECOVERY_NODE_ID,
            "emitted_queue_family_id": "child",
            "artifact_schema_id": generic_fanout.PACKET_SCHEMA_ID,
            "runner_binding_id": generic_admission.RUNNER_ID,
            "payload_projection": {
                "kind": "source",
                "path": ("artifact_payload",),
            },
        }
    )

    recovery = next(
        stage
        for stage in stages
        if stage["id"] == generic_admission.RECOVERY_STAGE_ID
    )
    recovery["output_queue_family_ids"] = ("child",)
    recovery["declared_outcome_ids"] = (
        *cast(tuple[str, ...], recovery["declared_outcome_ids"]),
        _ORDINARY_BLOCKED_OUTCOME,
        _ORDINARY_COMPLETE_OUTCOME,
    )
    outcomes.extend(
        (
            {
                "id": _ORDINARY_BLOCKED_OUTCOME,
                "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
                "marker": "BLOCKED",
            },
            {
                "id": _ORDINARY_COMPLETE_OUTCOME,
                "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
                "marker": "COMPLETE",
            },
        )
    )
    actions.extend(
        (
            {
                "id": _ORDINARY_BLOCKED_ACTION,
                "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
                "outcome_id": _ORDINARY_BLOCKED_OUTCOME,
                "kind": "route",
                "target_stage_kind_id": generic_admission.CHILD_STAGE_ID,
                "target_graph_node_id": generic_admission.CHILD_NODE_ID,
                "emitted_queue_family_id": "child",
                "artifact_schema_id": generic_fanout.PACKET_SCHEMA_ID,
                "runner_binding_id": generic_admission.RUNNER_ID,
                "payload_projection": {
                    "kind": "source",
                    "path": ("artifact_payload",),
                },
            },
            {
                "id": _ORDINARY_COMPLETE_ACTION,
                "stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
                "outcome_id": _ORDINARY_COMPLETE_OUTCOME,
                "kind": "complete_work_item",
                "artifact_schema_id": generic_fanout.PACKET_SCHEMA_ID,
            },
        )
    )
    return source


def test_ordinary_recovery_stage_dispatch_hides_attempt_owned_options(
    monkeypatch,
) -> None:
    compiled = _compile(_ordinary_recovery_ingress_source())
    assert compiled.plan is not None, compiled.diagnostics
    plan = compiled.plan
    fingerprint = authority_fingerprint(plan)
    monkeypatch.setattr(
        generic_admission,
        "compile_plan",
        lambda: (plan, fingerprint),
    )
    state, _, _ = _generic_parent_claimed_state()
    state = _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-generic-parent",
        action_id=_ORDINARY_ENTRY_ACTION,
        input_id="ordinary-entry-to-recovery",
        context=generic_lifecycle.context(
            "ordinary-entry-to-recovery",
            work_item_id="ordinary-recovery-work",
            activation_id="ordinary-recovery-activation",
        ),
    )
    state = generic_lifecycle.claim_activation(
        state,
        activation_id="ordinary-recovery-activation",
        suffix="ordinary-recovery",
    )

    dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-ordinary-recovery",
    )

    action_ids = {str(option["action_id"]) for option in dispatch.terminal_options}
    assert _ORDINARY_BLOCKED_ACTION in action_ids
    assert _ORDINARY_COMPLETE_ACTION in action_ids
    assert _RECURRENCE_ACTION_ID in action_ids
    assert generic_admission.RECOVERY_RETURN_ACTION_ID not in action_ids
    assert generic_admission.RECOVERY_QUARANTINE_ACTION_ID not in action_ids

    state = _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-ordinary-recovery",
        action_id=_ORDINARY_BLOCKED_ACTION,
        input_id="ordinary-blocked-route",
        context=generic_lifecycle.context(
            "ordinary-blocked-route",
            work_item_id="ordinary-child-work",
            activation_id="ordinary-child-activation",
        ),
    )
    assert "ordinary-child-work" in state.work_items
    state = generic_lifecycle.claim_activation(
        state,
        activation_id="ordinary-child-activation",
        suffix="ordinary-child",
    )
    child_dispatch = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-ordinary-child",
    )
    assert str(child_dispatch.stage_kind_id) == generic_admission.CHILD_STAGE_ID
