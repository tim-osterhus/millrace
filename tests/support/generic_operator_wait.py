"""Neutral selected operator-wait authority fixture."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    OperatorCloseWait,
    OperatorResumeWait,
    OperatorReviseWait,
)
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping
from support import kernel_ping as kernel_ping_support

REVISE_WAIT_ID = "kernel_ping.taskmaster_detail_wait"
CLOSE_WAIT_ID = "kernel_ping.taskmaster_close_wait"
REVISE_ACTION_ID = "kernel_ping.taskmaster.needs_operator_detail"
CLOSE_ACTION_ID = "kernel_ping.taskmaster.operator_close"

_AUDIT_FIELDS = (
    "input_id",
    "input_digest",
    "selected_plan_fingerprint",
    "actor_id",
    "actor_kind",
    "wait_id",
    "operator_wait_id",
    "lineage_id",
    "target_activation_id",
    "empty_payload",
    "closed_work_item_ids",
    "target_work_item_id",
    "payload_digest",
    "payload_reference",
)
_CLOSE_AUDIT_FIELDS = (
    "input_id",
    "input_digest",
    "selected_plan_fingerprint",
    "actor_id",
    "actor_kind",
    "wait_id",
    "operator_wait_id",
    "lineage_id",
    "closed_work_item_ids",
    "empty_payload",
)


def source() -> dict[str, object]:
    workflow_source = deepcopy(kernel_ping.WORKFLOW_SOURCE)
    taskmaster = next(
        record
        for record in cast(list[dict[str, object]], workflow_source["stage_kinds"])
        if record["id"] == "kernel_ping.taskmaster"
    )
    taskmaster["declared_outcome_ids"] = (
        *cast(tuple[str, ...], taskmaster["declared_outcome_ids"]),
        "kernel_ping.taskmaster.needs_operator_detail",
        "kernel_ping.taskmaster.operator_close",
    )
    outcomes = cast(list[dict[str, object]], workflow_source["terminal_outcomes"])
    outcomes.extend(
        (
            {
                "id": "kernel_ping.taskmaster.needs_operator_detail",
                "stage_kind_id": "kernel_ping.taskmaster",
                "marker": "NEEDS_OPERATOR_DETAIL",
            },
            {
                "id": "kernel_ping.taskmaster.operator_close",
                "stage_kind_id": "kernel_ping.taskmaster",
                "marker": "OPERATOR_CLOSE",
            },
        )
    )
    actions = cast(list[dict[str, object]], workflow_source["terminal_actions"])
    actions.extend(
        (
            {
                "id": REVISE_ACTION_ID,
                "stage_kind_id": "kernel_ping.taskmaster",
                "outcome_id": "kernel_ping.taskmaster.needs_operator_detail",
                "kind": "operator_wait",
                "artifact_schema_id": "kernel_ping.task_incident",
            },
            {
                "id": CLOSE_ACTION_ID,
                "stage_kind_id": "kernel_ping.taskmaster",
                "outcome_id": "kernel_ping.taskmaster.operator_close",
                "kind": "operator_wait",
                "artifact_schema_id": "kernel_ping.task_incident",
            },
        )
    )
    workflow_source["operator_waits"] = [
        {
            "id": REVISE_WAIT_ID,
            "source_action_ids": (REVISE_ACTION_ID,),
            "wait_scope": "lineage",
            "source_work_item_behavior": "leave_open",
            "unrelated_lineages_continue": True,
            "allowed_resolution_kinds": (
                "resume_recorded_source",
                "close_recorded_source",
                "revise_recorded_source",
            ),
            "payload_schema_id": "kernel_ping.task_artifact",
            "target_queue_family_id": "prompt",
            "target_stage_kind_id": "kernel_ping.taskmaster",
            "target_graph_node_id": "kernel_ping.taskmaster.start",
            "target_runner_binding_id": "kernel_ping.taskmaster_runner",
            "actor_kind": "local_operator",
            "audit_metadata_requirements": _AUDIT_FIELDS,
            "correlation_key": "wait_id",
            "idempotency": "input_receipt_and_active_wait_status",
            "timeout_policy": "none",
            "expiry_policy": "none",
            "cancellation_policy": "selected_resolution_only",
            "status_effect": "operator_wait_active",
        },
        {
            "id": CLOSE_WAIT_ID,
            "source_action_ids": (CLOSE_ACTION_ID,),
            "wait_scope": "lineage",
            "source_work_item_behavior": "close_on_create",
            "unrelated_lineages_continue": True,
            "allowed_resolution_kinds": ("close_recorded_source",),
            "actor_kind": "local_operator",
            "audit_metadata_requirements": _CLOSE_AUDIT_FIELDS,
            "correlation_key": "wait_id",
            "idempotency": "input_receipt_and_active_wait_status",
            "timeout_policy": "none",
            "expiry_policy": "none",
            "cancellation_policy": "selected_resolution_only",
            "status_effect": "operator_wait_active",
        },
    ]
    return workflow_source


def active_revise_wait_state() -> tuple[
    RuntimeState,
    SelectedCompiledPlan,
    str,
    str,
]:
    workflow_source = source()
    revise_wait = next(
        wait
        for wait in cast(list[dict[str, object]], workflow_source["operator_waits"])
        if wait["id"] == REVISE_WAIT_ID
    )
    revise_wait["allowed_resolution_kinds"] = (
        "resume_recorded_source",
        "revise_recorded_source",
    )
    revise_wait["audit_metadata_requirements"] = tuple(
        field for field in _AUDIT_FIELDS if field != "closed_work_item_ids"
    )
    plan, fingerprint = kernel_ping_support.compile_kernel_ping(workflow_source)
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = kernel_ping_support.apply_accepted_input(
        state,
        kernel_ping_support.runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id=REVISE_ACTION_ID,
            input_id="observe-taskmaster-needs-detail",
            artifact_payload=kernel_ping_support.task_incident_payload(),
        ),
        kernel_ping_support.kernel_ping_context("observe-taskmaster-needs-detail"),
    )
    wait = next(iter(state.operator_waits.values()))
    return state, plan, fingerprint, wait.wait_id


def wait_state(resolution_kind: str):
    plan, fingerprint = kernel_ping_support.compile_kernel_ping(source())
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = kernel_ping_support.apply_accepted_input(
        state,
        kernel_ping_support.runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id=REVISE_ACTION_ID,
            input_id="observe-taskmaster-needs-detail",
            artifact_payload=kernel_ping_support.task_incident_payload(),
        ),
        kernel_ping_support.kernel_ping_context("observe-taskmaster-needs-detail"),
    )
    wait = next(iter(state.operator_waits.values()))
    transition_input = None
    if resolution_kind == "resume":
        transition_input = OperatorResumeWait(
            "operator-resume-generic-restart",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local_operator",
            actor_kind="local_operator",
            payload={},
        )
    elif resolution_kind == "close":
        transition_input = OperatorCloseWait(
            "operator-close-generic-restart",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local_operator",
            actor_kind="local_operator",
            payload={},
        )
    elif resolution_kind == "revise":
        transition_input = OperatorReviseWait(
            "operator-revise-generic-restart",
            selected_plan_ref=wait.selected_plan_ref,
            wait_id=wait.wait_id,
            lineage_id=wait.lineage_id,
            actor_id="local_operator",
            actor_kind="local_operator",
            payload=kernel_ping_support.task_artifact_payload(),
        )
    elif resolution_kind != "active":
        raise ValueError(f"unsupported resolution_kind: {resolution_kind}")
    if transition_input is not None:
        state = kernel_ping_support.apply_accepted_input(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"transition-{transition_input.input_id}",
                work_item_id="work-operator-revised-restart",
                activation_id="activation-operator-revised-restart",
            ),
        )
    return state, wait
