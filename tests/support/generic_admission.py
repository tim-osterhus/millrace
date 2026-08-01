"""Neutral selected-plan authority fixture for kernel admission tests."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import cast

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import SelectedCompiledPlan
from support import generic_fanout

RUNNER_ID = "admission.runner"
PARENT_STAGE_ID = "admission.parent"
CHILD_STAGE_ID = "admission.child"
RECOVERY_STAGE_ID = "admission.recovery"
PARENT_NODE_ID = "admission.parent.start"
CHILD_NODE_ID = "admission.child.start"
RECOVERY_NODE_ID = "admission.recovery.start"
OTHER_SCHEMA_ID = "admission.other"
REVISE_WAIT_ID = "admission.detail_wait"
CLOSE_WAIT_ID = "admission.close_wait"
REVISE_ACTION_ID = "admission.wait_for_detail"
CLOSE_WAIT_ACTION_ID = "admission.wait_for_close"
RECOVERY_POLICY_ID = "admission.recovery_policy"
WAIT_STATE_ID = "admission.recovery_cooldown"
COUNTER_ID = "admission.counter"
RECOVERY_COUNTER_ID = "admission.recovery_counter"
COUNTER_INCREMENT_ACTION_ID = "admission.retry"
COUNTER_THRESHOLD_ACTION_ID = "admission.retry_exhausted"
RECOVERY_SOURCE_ACTION_ID = "admission.recover"
ALTERNATE_RECOVERY_SOURCE_ACTION_ID = "admission.recover_alternate"
RECOVERY_THRESHOLD_ACTION_ID = "admission.recovery_exhausted"
RECOVERY_RETURN_ACTION_ID = "admission.return"
RECOVERY_QUARANTINE_ACTION_ID = "admission.quarantine"
DYNAMIC_ROUTE_ACTION_ID = "admission.dynamic_route"
ESCALATION_ACTION_ID = "admission.escalate"

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)

_AUDIT_FIELDS = (
    "input_id",
    "input_digest",
    "selected_plan_fingerprint",
    "actor_id",
    "actor_kind",
    "reason",
    "option_id",
    "policy_id",
    "lineage_id",
    "quarantine_id",
    "recovery_attempt_record_id",
    "target_activation_id",
    "empty_payload",
)
_CLOSE_AUDIT_FIELDS = (
    "input_id",
    "input_digest",
    "selected_plan_fingerprint",
    "actor_id",
    "actor_kind",
    "reason",
    "option_id",
    "policy_id",
    "lineage_id",
    "quarantine_id",
    "recovery_attempt_record_id",
    "closed_work_item_ids",
    "closed_activation_ids",
    "closed_run_ids",
    "empty_payload",
)
_REVISE_AUDIT_FIELDS = (
    "input_id",
    "input_digest",
    "selected_plan_fingerprint",
    "actor_id",
    "actor_kind",
    "reason",
    "option_id",
    "policy_id",
    "lineage_id",
    "quarantine_id",
    "recovery_attempt_record_id",
    "recovery_attempt_count",
    "target_work_item_id",
    "target_activation_id",
    "payload_digest",
    "payload_reference",
)
_WAIT_AUDIT_FIELDS = (
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
_CLOSE_WAIT_AUDIT_FIELDS = (
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
    workflow_source = deepcopy(generic_fanout.source())
    workflow = cast(dict[str, object], workflow_source["workflow"])
    workflow.update({"id": "admission.workflow", "name": "Admission Probe"})

    graph = cast(list[dict[str, object]], workflow_source["graphs"])[0]
    graph["id"] = "admission.graph"
    graph["node_ids"] = (PARENT_NODE_ID, CHILD_NODE_ID, RECOVERY_NODE_ID)

    routes = cast(list[dict[str, object]], workflow_source["external_enqueue_routes"])
    routes[0].update(
        {
            "id": "admission.parent_route",
            "graph_node_id": PARENT_NODE_ID,
            "stage_kind_id": PARENT_STAGE_ID,
            "runner_binding_id": RUNNER_ID,
            "payload_schema_id": generic_fanout.PACKET_SCHEMA_ID,
        }
    )
    routes[1].update(
        {
            "id": "admission.child_route",
            "graph_node_id": CHILD_NODE_ID,
            "stage_kind_id": CHILD_STAGE_ID,
            "runner_binding_id": RUNNER_ID,
        }
    )

    stages = cast(list[dict[str, object]], workflow_source["stage_kinds"])
    parent = stages[0]
    parent.update(
        {
            "id": PARENT_STAGE_ID,
            "runner_binding_id": RUNNER_ID,
            "output_queue_family_ids": ("parent", "child"),
            "declared_outcome_ids": (
                "admission.parent_done",
                "admission.needs_detail",
                "admission.needs_close",
                "admission.needs_recovery",
                "admission.needs_alternate_recovery",
                "admission.recovery_exhausted",
                "admission.retry_ready",
                "admission.retry_exhausted",
                "admission.dynamic_ready",
                "admission.escalation_ready",
            ),
        }
    )
    child = stages[1]
    child.update(
        {
            "id": CHILD_STAGE_ID,
            "runner_binding_id": RUNNER_ID,
            "artifact_schema_ids": (
                generic_fanout.PACKET_SCHEMA_ID,
                generic_fanout.CHILD_SCHEMA_ID,
                OTHER_SCHEMA_ID,
            ),
            "declared_outcome_ids": ("admission.child.done",),
        }
    )
    stages.append(
        {
            "id": RECOVERY_STAGE_ID,
            "partition_id": "primary",
            "runner_binding_id": RUNNER_ID,
            "input_queue_family_ids": (),
            "output_queue_family_ids": (),
            "artifact_schema_ids": (generic_fanout.PACKET_SCHEMA_ID,),
            "asset_ids": ("admission.recovery_prompt",),
            "declared_outcome_ids": (
                "admission.recovered",
                "admission.unrecoverable",
            ),
            "presentation": {"display_name": "Recovery"},
        }
    )
    cast(list[dict[str, object]], workflow_source["assets"]).append(
        {
            "id": "admission.recovery_prompt",
            "kind": "prompt",
            "body": "Recover the recorded source.",
            "presentation": {"display_name": "Recovery prompt"},
        }
    )
    cast(list[dict[str, object]], workflow_source["artifact_schemas"]).append(
        {
            "id": OTHER_SCHEMA_ID,
            "schema": {
                "type": "object",
                "required": ("value",),
                "properties": {"value": {"type": "string", "min_length": 1}},
            },
            "presentation": {"display_name": "Other payload"},
        }
    )

    outcomes = cast(list[dict[str, object]], workflow_source["terminal_outcomes"])
    outcomes[0].update(
        {
            "id": "admission.parent_done",
            "stage_kind_id": PARENT_STAGE_ID,
        }
    )
    outcomes.extend(
        _outcome(outcome_id, PARENT_STAGE_ID)
        for outcome_id in (
            "admission.needs_detail",
            "admission.needs_close",
            "admission.needs_recovery",
            "admission.needs_alternate_recovery",
            "admission.recovery_exhausted",
            "admission.retry_ready",
            "admission.retry_exhausted",
            "admission.dynamic_ready",
            "admission.escalation_ready",
        )
    )
    outcomes.extend(
        (
            _outcome("admission.child.done", CHILD_STAGE_ID),
            _outcome("admission.recovered", RECOVERY_STAGE_ID),
            _outcome("admission.unrecoverable", RECOVERY_STAGE_ID),
        )
    )

    actions = cast(list[dict[str, object]], workflow_source["terminal_actions"])
    actions[0].update(
        {
            "id": "admission.complete",
            "stage_kind_id": PARENT_STAGE_ID,
            "outcome_id": "admission.parent_done",
        }
    )
    actions.extend(
        (
            {
                "id": "admission.child.complete",
                "stage_kind_id": CHILD_STAGE_ID,
                "outcome_id": "admission.child.done",
                "kind": "complete_work_item",
                "artifact_schema_id": generic_fanout.CHILD_SCHEMA_ID,
            },
            _inert_action(REVISE_ACTION_ID, "admission.needs_detail", "operator_wait"),
            _inert_action(
                CLOSE_WAIT_ACTION_ID, "admission.needs_close", "operator_wait"
            ),
            {
                "id": RECOVERY_SOURCE_ACTION_ID,
                "stage_kind_id": PARENT_STAGE_ID,
                "outcome_id": "admission.needs_recovery",
                "kind": "recovery_route",
                "target_stage_kind_id": RECOVERY_STAGE_ID,
                "target_graph_node_id": RECOVERY_NODE_ID,
                "runner_binding_id": RUNNER_ID,
                "asset_ids": ("admission.recovery_prompt",),
            },
            {
                "id": ALTERNATE_RECOVERY_SOURCE_ACTION_ID,
                "stage_kind_id": PARENT_STAGE_ID,
                "outcome_id": "admission.needs_alternate_recovery",
                "kind": "recovery_route",
                "target_stage_kind_id": RECOVERY_STAGE_ID,
                "target_graph_node_id": RECOVERY_NODE_ID,
                "runner_binding_id": RUNNER_ID,
                "asset_ids": ("admission.recovery_prompt",),
            },
            _inert_action(
                COUNTER_INCREMENT_ACTION_ID, "admission.retry_ready", "close"
            ),
            _inert_action(
                COUNTER_THRESHOLD_ACTION_ID,
                "admission.retry_exhausted",
                "close",
            ),
            {
                "id": RECOVERY_THRESHOLD_ACTION_ID,
                "stage_kind_id": PARENT_STAGE_ID,
                "outcome_id": "admission.recovery_exhausted",
                "kind": "recovery_route",
                "target_stage_kind_id": RECOVERY_STAGE_ID,
                "target_graph_node_id": RECOVERY_NODE_ID,
                "runner_binding_id": RUNNER_ID,
                "asset_ids": ("admission.recovery_prompt",),
            },
            {
                "id": DYNAMIC_ROUTE_ACTION_ID,
                "stage_kind_id": PARENT_STAGE_ID,
                "outcome_id": "admission.dynamic_ready",
                "kind": "route",
                "target_stage_kind_id": CHILD_STAGE_ID,
                "target_graph_node_id": CHILD_NODE_ID,
                "emitted_queue_family_id": "child",
                "artifact_schema_id": generic_fanout.PACKET_SCHEMA_ID,
                "runner_binding_id": RUNNER_ID,
                "payload_projection": {"kind": "source", "path": ("artifact_payload",)},
                "dynamic_target_selector": {
                    "kind": "observation_payload_route_target",
                    "field_names": ("target",),
                    "disallowed_targets": ("recovery",),
                    "targets": {
                        "child": {
                            "target_stage_kind_id": CHILD_STAGE_ID,
                            "target_graph_node_id": CHILD_NODE_ID,
                            "emitted_queue_family_id": "child",
                            "runner_binding_id": RUNNER_ID,
                        }
                    },
                },
            },
            _inert_action(
                ESCALATION_ACTION_ID,
                "admission.escalation_ready",
                "close_with_escalation",
            ),
            _recovery_action(
                RECOVERY_RETURN_ACTION_ID,
                "admission.recovered",
                "return_to_recorded_source",
            ),
            _recovery_action(
                RECOVERY_QUARANTINE_ACTION_ID,
                "admission.unrecoverable",
                "quarantine_lineage",
            ),
        )
    )

    workflow_source["fanout_declarations"] = ()
    workflow_source["recovery_policies"] = [
        {
            "id": RECOVERY_POLICY_ID,
            "source_recovery_action_ids": (RECOVERY_SOURCE_ACTION_ID,),
            "return_action_ids": (RECOVERY_RETURN_ACTION_ID,),
            "quarantine_action_ids": (RECOVERY_QUARANTINE_ACTION_ID,),
            "recovery_stage_kind_id": RECOVERY_STAGE_ID,
            "recorded_source_selector": "latest_recovery_attempt_for_lineage",
            "attempt_scope": "lineage",
            "immediate_recovery_limit": 1,
            "cooldown_starts_at_attempt": 2,
            "quarantine_threshold_attempt": 3,
            "threshold_behavior": "runtime_quarantine_at_threshold",
            "return_allowed_phases": ("active_recovery", "quarantine_eligible"),
            "reset_trigger_action_ids": ("admission.complete",),
            "default_cooldown_seconds": 900,
            "cooldown_wait_state_id": WAIT_STATE_ID,
        }
    ]
    workflow_source["wait_states"] = [
        {
            "id": WAIT_STATE_ID,
            "kind": "timer",
            "policy_id": RECOVERY_POLICY_ID,
            "starts_at_attempt": 2,
            "duration_seconds": 900,
        }
    ]
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
            "payload_schema_id": generic_fanout.PACKET_SCHEMA_ID,
            "target_queue_family_id": "parent",
            "target_stage_kind_id": PARENT_STAGE_ID,
            "target_graph_node_id": PARENT_NODE_ID,
            "target_runner_binding_id": RUNNER_ID,
            "actor_kind": "local_operator",
            "audit_metadata_requirements": _WAIT_AUDIT_FIELDS,
            "correlation_key": "wait_id",
            "idempotency": "input_receipt_and_active_wait_status",
            "timeout_policy": "none",
            "expiry_policy": "none",
            "cancellation_policy": "selected_resolution_only",
            "status_effect": "operator_wait_active",
        },
        {
            "id": CLOSE_WAIT_ID,
            "source_action_ids": (CLOSE_WAIT_ACTION_ID,),
            "wait_scope": "lineage",
            "source_work_item_behavior": "close_on_create",
            "unrelated_lineages_continue": True,
            "allowed_resolution_kinds": ("close_recorded_source",),
            "actor_kind": "local_operator",
            "audit_metadata_requirements": _CLOSE_WAIT_AUDIT_FIELDS,
            "correlation_key": "wait_id",
            "idempotency": "input_receipt_and_active_wait_status",
            "timeout_policy": "none",
            "expiry_policy": "none",
            "cancellation_policy": "selected_resolution_only",
            "status_effect": "operator_wait_active",
        },
    ]
    workflow_source["counters"] = [
        {
            "id": COUNTER_ID,
            "kind": "lineage_terminal_action_counter",
            "scope": "lineage",
            "stage_kind_id": PARENT_STAGE_ID,
            "increment_action_id": COUNTER_INCREMENT_ACTION_ID,
            "threshold_action_id": COUNTER_THRESHOLD_ACTION_ID,
            "threshold_count": 2,
        },
        {
            "id": RECOVERY_COUNTER_ID,
            "kind": "lineage_terminal_action_counter",
            "scope": "lineage",
            "stage_kind_id": PARENT_STAGE_ID,
            "increment_action_id": RECOVERY_SOURCE_ACTION_ID,
            "threshold_action_id": RECOVERY_THRESHOLD_ACTION_ID,
            "threshold_count": 2,
        },
    ]
    workflow_source["intervention_options"] = [
        _intervention("resume", "resume_lineage"),
        _intervention("close", "close_lineage"),
        _intervention("revise", "revise_lineage"),
    ]

    binding = cast(list[dict[str, object]], workflow_source["runner_bindings"])[0]
    binding.update(
        {
            "id": RUNNER_ID,
            "stage_kind_ids": (PARENT_STAGE_ID, CHILD_STAGE_ID, RECOVERY_STAGE_ID),
        }
    )
    return workflow_source


def alternate_source() -> dict[str, object]:
    workflow_source = source()
    workflow = cast(dict[str, object], workflow_source["workflow"])
    workflow["version"] = "0.2"
    return workflow_source


def compile_plan(
    workflow_source: Mapping[str, object] | None = None,
) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(
        workflow_source or source(), selected_runner_policy=_CODEX_POLICY
    )
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _outcome(outcome_id: str, stage_kind_id: str) -> dict[str, object]:
    return {
        "id": outcome_id,
        "stage_kind_id": stage_kind_id,
        "marker": outcome_id.upper().replace(".", "_"),
    }


def _inert_action(action_id: str, outcome_id: str, kind: str) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": PARENT_STAGE_ID,
        "outcome_id": outcome_id,
        "kind": kind,
        "artifact_schema_id": generic_fanout.PACKET_SCHEMA_ID,
    }


def _recovery_action(action_id: str, outcome_id: str, kind: str) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": RECOVERY_STAGE_ID,
        "outcome_id": outcome_id,
        "kind": kind,
        "artifact_schema_id": generic_fanout.PACKET_SCHEMA_ID,
    }


def _intervention(option_id: str, kind: str) -> dict[str, object]:
    record: dict[str, object] = {
        "id": f"admission.{option_id}",
        "policy_id": RECOVERY_POLICY_ID,
        "kind": kind,
        "legal_source_state": "active_lineage_quarantine",
        "target_selector": "selected_quarantine_or_active_quarantine_by_lineage",
        "resume_target_selector": None,
        "close_behavior": None,
        "supersede_behavior": "supersede_quarantine",
        "attempt_effect": "resolve_attempt",
        "actor_kind": "local_operator",
        "audit_metadata_requirements": _AUDIT_FIELDS,
    }
    if kind == "resume_lineage":
        record["resume_target_selector"] = "recorded_source"
    elif kind == "close_lineage":
        record["close_behavior"] = "close_ready_or_active_work_in_lineage"
        record["audit_metadata_requirements"] = _CLOSE_AUDIT_FIELDS
    else:
        record["audit_metadata_requirements"] = _REVISE_AUDIT_FIELDS
        record.update(
            {
                "payload_schema_id": generic_fanout.PACKET_SCHEMA_ID,
                "target_queue_family_id": "child",
                "target_stage_kind_id": CHILD_STAGE_ID,
                "target_graph_node_id": CHILD_NODE_ID,
                "target_runner_binding_id": RUNNER_ID,
            }
        )
    return record
