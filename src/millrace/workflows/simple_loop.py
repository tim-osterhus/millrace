"""Hosted simple_loop workflow source used by conformance tests."""

from __future__ import annotations

from copy import deepcopy

_RUNNER_ID = "simple_loop.default_agent_runner"
_TROUBLESHOOTER_STAGE_ID = "simple_loop.troubleshooter"
_TROUBLESHOOTER_NODE_ID = "simple_loop.troubleshooter.start"
_TROUBLESHOOTER_PROMPT_ID = "simple_loop.troubleshooter_prompt"


def _required_string_schema() -> dict[str, object]:
    return {"type": "string", "min_length": 1}


def _required_string_array_schema() -> dict[str, object]:
    return {
        "type": "array",
        "min_items": 1,
        "items": _required_string_schema(),
    }


def _source_projection(*path: str) -> dict[str, object]:
    return {"kind": "source", "path": path}


def _object_projection(fields: dict[str, object]) -> dict[str, object]:
    return {"kind": "object", "fields": fields}


def _route_action(
    *,
    action_id: str,
    stage_kind_id: str,
    outcome_id: str,
    target_stage_kind_id: str,
    target_graph_node_id: str,
    emitted_queue_family_id: str,
    artifact_schema_id: str,
    payload_projection: dict[str, object],
) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": stage_kind_id,
        "outcome_id": outcome_id,
        "kind": "route",
        "target_stage_kind_id": target_stage_kind_id,
        "target_graph_node_id": target_graph_node_id,
        "emitted_queue_family_id": emitted_queue_family_id,
        "artifact_schema_id": artifact_schema_id,
        "runner_binding_id": _RUNNER_ID,
        "payload_projection": payload_projection,
        "presentation": {"display_name": action_id},
    }


def _recovery_action(
    *,
    action_id: str,
    stage_kind_id: str,
    outcome_id: str,
) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": stage_kind_id,
        "outcome_id": outcome_id,
        "kind": "recovery_route",
        "target_stage_kind_id": _TROUBLESHOOTER_STAGE_ID,
        "target_graph_node_id": _TROUBLESHOOTER_NODE_ID,
        "runner_binding_id": _RUNNER_ID,
        "asset_ids": (_TROUBLESHOOTER_PROMPT_ID,),
        "presentation": {"display_name": action_id},
    }


def _inert_action(
    *,
    action_id: str,
    stage_kind_id: str,
    outcome_id: str,
    kind: str,
    artifact_schema_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": stage_kind_id,
        "outcome_id": outcome_id,
        "kind": kind,
        "artifact_schema_id": artifact_schema_id,
        "presentation": {"display_name": action_id},
    }


WORKFLOW_SOURCE: dict[str, object] = {
    "workflow": {
        "id": "simple_loop",
        "version": "0.1",
        "name": "Simple Loop",
        "compatibility_profile": None,
        "required_extensions": (),
    },
    "lineage_policy": "root_from_external_enqueue",
    "graphs": [
        {
            "id": "simple_loop.graph",
            "node_ids": (
                "simple_loop.manager.start",
                "simple_loop.worker.start",
                "simple_loop.reviewer.start",
                "simple_loop.manager.detail_request",
                "simple_loop.worker.gaps",
                "simple_loop.manager.incident",
                _TROUBLESHOOTER_NODE_ID,
            ),
            "presentation": {"display_name": "Simple Loop Graph"},
        }
    ],
    "partitions": [
        {
            "id": "management",
            "kind": "plane",
            "presentation": {"display_name": "Management"},
        },
        {
            "id": "implementation",
            "kind": "plane",
            "presentation": {"display_name": "Implementation"},
        },
        {
            "id": "review",
            "kind": "plane",
            "presentation": {"display_name": "Review"},
        },
    ],
    "queue_families": [
        {
            "id": "work_prompt",
            "external_enqueue": True,
            "presentation": {"display_name": "Work prompt"},
        },
        {
            "id": "work_packet",
            "external_enqueue": False,
            "presentation": {"display_name": "Work packet"},
        },
        {
            "id": "gap_packet",
            "external_enqueue": False,
            "presentation": {"display_name": "Gap packet"},
        },
        {
            "id": "incident_report",
            "external_enqueue": False,
            "presentation": {"display_name": "Incident report"},
        },
    ],
    "external_enqueue_routes": [
        {
            "id": "simple_loop.external_work_prompt",
            "queue_family_id": "work_prompt",
            "graph_node_id": "simple_loop.manager.start",
            "stage_kind_id": "simple_loop.manager",
            "runner_binding_id": _RUNNER_ID,
            "payload_schema_id": "simple_loop.work_prompt",
        }
    ],
    "artifact_schemas": [
        {
            "id": "simple_loop.work_prompt",
            "schema": {
                "type": "object",
                "required": ("prompt_id", "body"),
                "properties": {
                    "prompt_id": _required_string_schema(),
                    "body": _required_string_schema(),
                },
            },
            "presentation": {"display_name": "Work prompt schema"},
        },
        {
            "id": "simple_loop.work_packet",
            "schema": {
                "type": "object",
                "required": (
                    "artifact_kind",
                    "source_prompt_id",
                    "title",
                    "objective",
                    "completion_definition",
                ),
                "properties": {
                    "artifact_kind": {"const": "simple_loop.work_packet"},
                    "source_prompt_id": _required_string_schema(),
                    "title": _required_string_schema(),
                    "objective": _required_string_schema(),
                    "completion_definition": _required_string_schema(),
                },
            },
            "presentation": {"display_name": "Work packet schema"},
        },
        {
            "id": "simple_loop.detail_request",
            "schema": {
                "type": "object",
                "required": ("artifact_kind", "missing_details"),
                "properties": {
                    "artifact_kind": {"const": "simple_loop.detail_request"},
                    "missing_details": _required_string_array_schema(),
                },
            },
            "presentation": {"display_name": "Detail request schema"},
        },
        {
            "id": "simple_loop.work_result",
            "schema": {
                "type": "object",
                "required": ("artifact_kind", "summary"),
                "properties": {
                    "artifact_kind": {"const": "simple_loop.work_result"},
                    "summary": _required_string_schema(),
                },
            },
            "presentation": {"display_name": "Work result schema"},
        },
        {
            "id": "simple_loop.gap_packet",
            "schema": {
                "type": "object",
                "required": ("artifact_kind", "gaps"),
                "properties": {
                    "artifact_kind": {"const": "simple_loop.gap_packet"},
                    "gaps": _required_string_array_schema(),
                },
            },
            "presentation": {"display_name": "Gap packet schema"},
        },
        {
            "id": "simple_loop.incident_report",
            "schema": {
                "type": "object",
                "required": ("artifact_kind", "reason"),
                "properties": {
                    "artifact_kind": {"const": "simple_loop.incident_report"},
                    "reason": _required_string_schema(),
                },
            },
            "presentation": {"display_name": "Incident report schema"},
        },
        {
            "id": "simple_loop.troubleshooting_report",
            "schema": {
                "type": "object",
                "required": (
                    "artifact_kind",
                    "result",
                    "blocker_cause",
                    "attempted_repair",
                    "next_route",
                ),
                "properties": {
                    "artifact_kind": {
                        "const": "simple_loop.troubleshooting_report"
                    },
                    "result": _required_string_schema(),
                    "blocker_cause": _required_string_schema(),
                    "attempted_repair": _required_string_schema(),
                    "next_route": {
                        "type": "string",
                        "enum": (
                            "retry_recorded_source",
                            "operator_intervention",
                            "unresolved_return",
                        ),
                    },
                },
            },
            "presentation": {"display_name": "Troubleshooting report schema"},
        },
    ],
    "assets": [
        {
            "id": "simple_loop.manager_prompt",
            "kind": "prompt",
            "body": "Turn incoming prompts into work packets with completion criteria.",
            "presentation": {"display_name": "Manager prompt"},
        },
        {
            "id": "simple_loop.worker_prompt",
            "kind": "prompt",
            "body": "Complete work packets and report declared outcomes.",
            "presentation": {"display_name": "Worker prompt"},
        },
        {
            "id": "simple_loop.reviewer_prompt",
            "kind": "prompt",
            "body": "Review work results against completion criteria.",
            "presentation": {"display_name": "Reviewer prompt"},
        },
        {
            "id": _TROUBLESHOOTER_PROMPT_ID,
            "kind": "prompt",
            "body": "Investigate blockers and report a declared recovery outcome.",
            "presentation": {"display_name": "Troubleshooter prompt"},
        },
    ],
    "terminal_outcomes": [
        {
            "id": "simple_loop.manager.packet_ready",
            "stage_kind_id": "simple_loop.manager",
            "marker": "PACKET_READY",
            "presentation": {"display_name": "Packet ready"},
        },
        {
            "id": "simple_loop.manager.needs_operator_detail",
            "stage_kind_id": "simple_loop.manager",
            "marker": "NEEDS_OPERATOR_DETAIL",
            "presentation": {"display_name": "Needs operator detail"},
        },
        {
            "id": "simple_loop.manager.incident_triaged",
            "stage_kind_id": "simple_loop.manager",
            "marker": "INCIDENT_TRIAGED",
            "presentation": {"display_name": "Incident triaged"},
        },
        {
            "id": "simple_loop.manager.blocked",
            "stage_kind_id": "simple_loop.manager",
            "marker": "BLOCKED",
            "presentation": {"display_name": "Manager blocked"},
        },
        {
            "id": "simple_loop.manager.invalid_prompt",
            "stage_kind_id": "simple_loop.manager",
            "marker": "INVALID_PROMPT",
            "presentation": {"display_name": "Invalid prompt"},
        },
        {
            "id": "simple_loop.worker.work_done",
            "stage_kind_id": "simple_loop.worker",
            "marker": "WORK_DONE",
            "presentation": {"display_name": "Work done"},
        },
        {
            "id": "simple_loop.worker.insufficient_spec",
            "stage_kind_id": "simple_loop.worker",
            "marker": "INSUFFICIENT_SPEC",
            "presentation": {"display_name": "Insufficient spec"},
        },
        {
            "id": "simple_loop.worker.blocked",
            "stage_kind_id": "simple_loop.worker",
            "marker": "BLOCKED",
            "presentation": {"display_name": "Worker blocked"},
        },
        {
            "id": "simple_loop.worker.failed",
            "stage_kind_id": "simple_loop.worker",
            "marker": "FAILED",
            "presentation": {"display_name": "Worker failed"},
        },
        {
            "id": "simple_loop.reviewer.accepted",
            "stage_kind_id": "simple_loop.reviewer",
            "marker": "ACCEPTED",
            "presentation": {"display_name": "Accepted"},
        },
        {
            "id": "simple_loop.reviewer.gaps_found",
            "stage_kind_id": "simple_loop.reviewer",
            "marker": "GAPS_FOUND",
            "presentation": {"display_name": "Gaps found"},
        },
        {
            "id": "simple_loop.reviewer.incident_required",
            "stage_kind_id": "simple_loop.reviewer",
            "marker": "INCIDENT_REQUIRED",
            "presentation": {"display_name": "Incident required"},
        },
        {
            "id": "simple_loop.reviewer.blocked",
            "stage_kind_id": "simple_loop.reviewer",
            "marker": "BLOCKED",
            "presentation": {"display_name": "Reviewer blocked"},
        },
        {
            "id": "simple_loop.troubleshooter.resolved",
            "stage_kind_id": _TROUBLESHOOTER_STAGE_ID,
            "marker": "RESOLVED",
            "presentation": {"display_name": "Resolved"},
        },
        {
            "id": "simple_loop.troubleshooter.unresolved",
            "stage_kind_id": _TROUBLESHOOTER_STAGE_ID,
            "marker": "UNRESOLVED",
            "presentation": {"display_name": "Unresolved"},
        },
        {
            "id": "simple_loop.troubleshooter.operator_needed",
            "stage_kind_id": _TROUBLESHOOTER_STAGE_ID,
            "marker": "OPERATOR_NEEDED",
            "presentation": {"display_name": "Operator needed"},
        },
    ],
    "stage_kinds": [
        {
            "id": "simple_loop.manager",
            "partition_id": "management",
            "runner_binding_id": _RUNNER_ID,
            "input_queue_family_ids": ("work_prompt", "work_packet", "incident_report"),
            "output_queue_family_ids": ("work_packet",),
            "artifact_schema_ids": (
                "simple_loop.work_packet",
                "simple_loop.detail_request",
                "simple_loop.incident_report",
            ),
            "asset_ids": ("simple_loop.manager_prompt",),
            "declared_outcome_ids": (
                "simple_loop.manager.packet_ready",
                "simple_loop.manager.needs_operator_detail",
                "simple_loop.manager.incident_triaged",
                "simple_loop.manager.blocked",
                "simple_loop.manager.invalid_prompt",
            ),
            "presentation": {"display_name": "Manager"},
        },
        {
            "id": "simple_loop.worker",
            "partition_id": "implementation",
            "runner_binding_id": _RUNNER_ID,
            "input_queue_family_ids": ("work_packet", "gap_packet"),
            "output_queue_family_ids": ("work_packet",),
            "artifact_schema_ids": (
                "simple_loop.work_packet",
                "simple_loop.detail_request",
                "simple_loop.work_result",
                "simple_loop.gap_packet",
            ),
            "asset_ids": ("simple_loop.worker_prompt",),
            "declared_outcome_ids": (
                "simple_loop.worker.work_done",
                "simple_loop.worker.insufficient_spec",
                "simple_loop.worker.blocked",
                "simple_loop.worker.failed",
            ),
            "presentation": {"display_name": "Worker"},
        },
        {
            "id": "simple_loop.reviewer",
            "partition_id": "review",
            "runner_binding_id": _RUNNER_ID,
            "input_queue_family_ids": ("work_packet",),
            "output_queue_family_ids": (
                "work_packet",
                "gap_packet",
                "incident_report",
            ),
            "artifact_schema_ids": (
                "simple_loop.work_packet",
                "simple_loop.work_result",
                "simple_loop.gap_packet",
                "simple_loop.incident_report",
            ),
            "asset_ids": ("simple_loop.reviewer_prompt",),
            "declared_outcome_ids": (
                "simple_loop.reviewer.accepted",
                "simple_loop.reviewer.gaps_found",
                "simple_loop.reviewer.incident_required",
                "simple_loop.reviewer.blocked",
            ),
            "presentation": {"display_name": "Reviewer"},
        },
        {
            "id": _TROUBLESHOOTER_STAGE_ID,
            "partition_id": None,
            "runner_binding_id": _RUNNER_ID,
            "input_queue_family_ids": (),
            "output_queue_family_ids": (),
            "artifact_schema_ids": ("simple_loop.troubleshooting_report",),
            "asset_ids": (_TROUBLESHOOTER_PROMPT_ID,),
            "declared_outcome_ids": (
                "simple_loop.troubleshooter.resolved",
                "simple_loop.troubleshooter.unresolved",
                "simple_loop.troubleshooter.operator_needed",
            ),
            "presentation": {"display_name": "Troubleshooter"},
        },
    ],
    "terminal_actions": [
        _route_action(
            action_id="simple_loop.manager.packet_ready",
            stage_kind_id="simple_loop.manager",
            outcome_id="simple_loop.manager.packet_ready",
            target_stage_kind_id="simple_loop.worker",
            target_graph_node_id="simple_loop.worker.start",
            emitted_queue_family_id="work_packet",
            artifact_schema_id="simple_loop.work_packet",
            payload_projection=_object_projection(
                {
                    "prompt_id": _source_projection(
                        "work_item_payload",
                        "prompt_id",
                    ),
                    "body": _source_projection("work_item_payload", "body"),
                    "work_packet": _source_projection("artifact_payload"),
                }
            ),
        ),
        _inert_action(
            action_id="simple_loop.manager.needs_operator_detail",
            stage_kind_id="simple_loop.manager",
            outcome_id="simple_loop.manager.needs_operator_detail",
            kind="operator_wait",
            artifact_schema_id="simple_loop.detail_request",
        ),
        _inert_action(
            action_id="simple_loop.manager.incident_triaged",
            stage_kind_id="simple_loop.manager",
            outcome_id="simple_loop.manager.incident_triaged",
            kind="operator_wait",
            artifact_schema_id="simple_loop.incident_report",
        ),
        _recovery_action(
            action_id="simple_loop.manager.blocked",
            stage_kind_id="simple_loop.manager",
            outcome_id="simple_loop.manager.blocked",
        ),
        _inert_action(
            action_id="simple_loop.manager.invalid_prompt",
            stage_kind_id="simple_loop.manager",
            outcome_id="simple_loop.manager.invalid_prompt",
            kind="close",
        ),
        _route_action(
            action_id="simple_loop.worker.work_done",
            stage_kind_id="simple_loop.worker",
            outcome_id="simple_loop.worker.work_done",
            target_stage_kind_id="simple_loop.reviewer",
            target_graph_node_id="simple_loop.reviewer.start",
            emitted_queue_family_id="work_packet",
            artifact_schema_id="simple_loop.work_result",
            payload_projection=_object_projection(
                {
                    "prompt_id": _source_projection(
                        "work_item_payload",
                        "prompt_id",
                    ),
                    "body": _source_projection("work_item_payload", "body"),
                    "work_packet": _source_projection(
                        "work_item_payload",
                        "work_packet",
                    ),
                    "work_result": _source_projection("artifact_payload"),
                }
            ),
        ),
        _route_action(
            action_id="simple_loop.worker.insufficient_spec",
            stage_kind_id="simple_loop.worker",
            outcome_id="simple_loop.worker.insufficient_spec",
            target_stage_kind_id="simple_loop.manager",
            target_graph_node_id="simple_loop.manager.detail_request",
            emitted_queue_family_id="work_packet",
            artifact_schema_id="simple_loop.detail_request",
            payload_projection=_object_projection(
                {
                    "prompt_id": _source_projection(
                        "work_item_payload",
                        "prompt_id",
                    ),
                    "body": _source_projection("work_item_payload", "body"),
                    "work_packet": _source_projection(
                        "work_item_payload",
                        "work_packet",
                    ),
                    "detail_request": _source_projection("artifact_payload"),
                }
            ),
        ),
        _recovery_action(
            action_id="simple_loop.worker.blocked",
            stage_kind_id="simple_loop.worker",
            outcome_id="simple_loop.worker.blocked",
        ),
        _recovery_action(
            action_id="simple_loop.worker.failed",
            stage_kind_id="simple_loop.worker",
            outcome_id="simple_loop.worker.failed",
        ),
        _inert_action(
            action_id="simple_loop.reviewer.accepted",
            stage_kind_id="simple_loop.reviewer",
            outcome_id="simple_loop.reviewer.accepted",
            kind="close",
        ),
        _route_action(
            action_id="simple_loop.reviewer.gaps_found",
            stage_kind_id="simple_loop.reviewer",
            outcome_id="simple_loop.reviewer.gaps_found",
            target_stage_kind_id="simple_loop.worker",
            target_graph_node_id="simple_loop.worker.gaps",
            emitted_queue_family_id="gap_packet",
            artifact_schema_id="simple_loop.gap_packet",
            payload_projection=_object_projection(
                {
                    "prompt_id": _source_projection(
                        "work_item_payload",
                        "prompt_id",
                    ),
                    "body": _source_projection("work_item_payload", "body"),
                    "work_packet": _source_projection(
                        "work_item_payload",
                        "work_packet",
                    ),
                    "latest_work_result": _source_projection(
                        "work_item_payload",
                        "work_result",
                    ),
                    "gap_packet": _source_projection("artifact_payload"),
                }
            ),
        ),
        _route_action(
            action_id="simple_loop.reviewer.incident_required",
            stage_kind_id="simple_loop.reviewer",
            outcome_id="simple_loop.reviewer.incident_required",
            target_stage_kind_id="simple_loop.manager",
            target_graph_node_id="simple_loop.manager.incident",
            emitted_queue_family_id="incident_report",
            artifact_schema_id="simple_loop.incident_report",
            payload_projection=_object_projection(
                {
                    "prompt_id": _source_projection(
                        "work_item_payload",
                        "prompt_id",
                    ),
                    "body": _source_projection("work_item_payload", "body"),
                    "work_packet": _source_projection(
                        "work_item_payload",
                        "work_packet",
                    ),
                    "latest_work_result": _source_projection(
                        "work_item_payload",
                        "work_result",
                    ),
                    "incident_report": _source_projection("artifact_payload"),
                }
            ),
        ),
        _recovery_action(
            action_id="simple_loop.reviewer.blocked",
            stage_kind_id="simple_loop.reviewer",
            outcome_id="simple_loop.reviewer.blocked",
        ),
        _inert_action(
            action_id="simple_loop.troubleshooter.resolved",
            stage_kind_id=_TROUBLESHOOTER_STAGE_ID,
            outcome_id="simple_loop.troubleshooter.resolved",
            kind="return_to_recorded_source",
            artifact_schema_id="simple_loop.troubleshooting_report",
        ),
        _inert_action(
            action_id="simple_loop.troubleshooter.unresolved",
            stage_kind_id=_TROUBLESHOOTER_STAGE_ID,
            outcome_id="simple_loop.troubleshooter.unresolved",
            kind="return_to_recorded_source",
            artifact_schema_id="simple_loop.troubleshooting_report",
        ),
        _inert_action(
            action_id="simple_loop.troubleshooter.operator_needed",
            stage_kind_id=_TROUBLESHOOTER_STAGE_ID,
            outcome_id="simple_loop.troubleshooter.operator_needed",
            kind="quarantine_lineage",
            artifact_schema_id="simple_loop.troubleshooting_report",
        ),
    ],
    "recovery_policies": [
        {
            "id": "simple_loop.blocked_recovery",
            "source_recovery_action_ids": (
                "simple_loop.manager.blocked",
                "simple_loop.worker.blocked",
                "simple_loop.worker.failed",
                "simple_loop.reviewer.blocked",
            ),
            "return_action_ids": (
                "simple_loop.troubleshooter.resolved",
                "simple_loop.troubleshooter.unresolved",
            ),
            "quarantine_action_ids": (
                "simple_loop.troubleshooter.operator_needed",
            ),
            "recovery_stage_kind_id": _TROUBLESHOOTER_STAGE_ID,
            "recorded_source_selector": "latest_recovery_attempt_for_lineage",
            "attempt_scope": "lineage",
            "immediate_recovery_limit": 1,
            "cooldown_starts_at_attempt": 2,
            "quarantine_threshold_attempt": 3,
            "threshold_behavior": "runtime_quarantine_at_threshold",
            "return_allowed_phases": ("active_recovery", "quarantine_eligible"),
            "reset_trigger_action_ids": (
                "simple_loop.manager.packet_ready",
                "simple_loop.manager.incident_triaged",
                "simple_loop.manager.invalid_prompt",
                "simple_loop.worker.work_done",
                "simple_loop.worker.insufficient_spec",
                "simple_loop.reviewer.accepted",
                "simple_loop.reviewer.gaps_found",
                "simple_loop.reviewer.incident_required",
            ),
            "default_cooldown_seconds": 900,
            "cooldown_wait_state_id": "simple_loop.blocked_recovery.cooldown",
        }
    ],
    "wait_states": [
        {
            "id": "simple_loop.blocked_recovery.cooldown",
            "kind": "timer",
            "policy_id": "simple_loop.blocked_recovery",
            "starts_at_attempt": 2,
            "duration_seconds": 900,
        }
    ],
    "operator_waits": [
        {
            "id": "simple_loop.manager_detail_wait",
            "source_action_ids": ("simple_loop.manager.needs_operator_detail",),
            "wait_scope": "lineage",
            "source_work_item_behavior": "leave_open",
            "unrelated_lineages_continue": True,
            "allowed_resolution_kinds": (
                "resume_recorded_source",
                "close_recorded_source",
                "revise_recorded_source",
            ),
            "payload_schema_id": "simple_loop.work_prompt",
            "target_queue_family_id": "work_prompt",
            "target_stage_kind_id": "simple_loop.manager",
            "target_graph_node_id": "simple_loop.manager.start",
            "target_runner_binding_id": _RUNNER_ID,
            "actor_kind": "local_operator",
            "audit_metadata_requirements": (
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
            ),
            "correlation_key": "wait_id",
            "idempotency": "input_receipt_and_active_wait_status",
            "timeout_policy": "none",
            "expiry_policy": "none",
            "cancellation_policy": "selected_resolution_only",
            "status_effect": "operator_wait_active",
        },
        {
            "id": "simple_loop.manager_incident_wait",
            "source_action_ids": ("simple_loop.manager.incident_triaged",),
            "wait_scope": "lineage",
            "source_work_item_behavior": "close_on_create",
            "unrelated_lineages_continue": True,
            "allowed_resolution_kinds": ("close_recorded_source",),
            "actor_kind": "local_operator",
            "audit_metadata_requirements": (
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
            ),
            "correlation_key": "wait_id",
            "idempotency": "input_receipt_and_active_wait_status",
            "timeout_policy": "none",
            "expiry_policy": "none",
            "cancellation_policy": "selected_resolution_only",
            "status_effect": "operator_wait_active",
        },
    ],
    "counters": [
        {
            "id": "simple_loop.reviewer_gap_counter",
            "kind": "lineage_terminal_action_counter",
            "scope": "lineage",
            "stage_kind_id": "simple_loop.reviewer",
            "increment_action_id": "simple_loop.reviewer.gaps_found",
            "threshold_action_id": "simple_loop.reviewer.incident_required",
            "threshold_count": 4,
        }
    ],
    "intervention_options": [
        {
            "id": "simple_loop.resume_lineage",
            "policy_id": "simple_loop.blocked_recovery",
            "kind": "resume_lineage",
            "legal_source_state": "active_lineage_quarantine",
            "target_selector": (
                "selected_quarantine_or_active_quarantine_by_lineage"
            ),
            "resume_target_selector": "recorded_source",
            "close_behavior": None,
            "supersede_behavior": "supersede_quarantine",
            "attempt_effect": "resolve_attempt",
            "actor_kind": "local_operator",
            "audit_metadata_requirements": (
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
            ),
        },
        {
            "id": "simple_loop.close_lineage",
            "policy_id": "simple_loop.blocked_recovery",
            "kind": "close_lineage",
            "legal_source_state": "active_lineage_quarantine",
            "target_selector": (
                "selected_quarantine_or_active_quarantine_by_lineage"
            ),
            "resume_target_selector": None,
            "close_behavior": "close_ready_or_active_work_in_lineage",
            "supersede_behavior": "supersede_quarantine",
            "attempt_effect": "resolve_attempt",
            "actor_kind": "local_operator",
            "audit_metadata_requirements": (
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
            ),
        },
        {
            "id": "simple_loop.revise_lineage",
            "policy_id": "simple_loop.blocked_recovery",
            "kind": "revise_lineage",
            "legal_source_state": "active_lineage_quarantine",
            "target_selector": (
                "selected_quarantine_or_active_quarantine_by_lineage"
            ),
            "resume_target_selector": None,
            "close_behavior": None,
            "payload_schema_id": "simple_loop.work_packet",
            "target_queue_family_id": "work_packet",
            "target_stage_kind_id": "simple_loop.worker",
            "target_graph_node_id": "simple_loop.worker.start",
            "target_runner_binding_id": _RUNNER_ID,
            "supersede_behavior": "supersede_quarantine",
            "attempt_effect": "resolve_attempt",
            "actor_kind": "local_operator",
            "audit_metadata_requirements": (
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
            ),
        },
    ],
    "runner_bindings": [
        {
            "id": _RUNNER_ID,
            "adapter_kind": "fake_local",
            "stage_kind_ids": (
                "simple_loop.manager",
                "simple_loop.worker",
                "simple_loop.reviewer",
                _TROUBLESHOOTER_STAGE_ID,
            ),
            "presentation": {"display_name": "Default agent runner"},
        }
    ],
}


def workflow_source() -> dict[str, object]:
    return deepcopy(WORKFLOW_SOURCE)


__all__ = ("WORKFLOW_SOURCE", "workflow_source")
