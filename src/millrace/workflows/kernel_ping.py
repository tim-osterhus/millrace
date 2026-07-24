"""Base diagnostic workflow shipped with Millrace."""

from __future__ import annotations

from copy import deepcopy

WORKFLOW_SOURCE: dict[str, object] = {
    "lineage_policy": "root_from_external_enqueue",
    "workflow": {
        "id": "kernel_ping",
        "version": "0.1",
        "name": "Kernel Ping",
        "compatibility_profile": None,
        "required_extensions": (),
    },
    "graphs": [
        {
            "id": "kernel_ping.graph",
            "node_ids": (
                "kernel_ping.taskmaster.start",
                "kernel_ping.worker.start",
                "kernel_ping.taskmaster.review",
            ),
            "presentation": {"display_name": "Kernel Ping Graph"},
        }
    ],
    "partitions": [
        {
            "id": "craft",
            "kind": "plane",
            "presentation": {
                "display_name": "Craft",
                "description": (
                    "Turn prompts into tested work through Taskmaster and Worker."
                ),
            },
        }
    ],
    "queue_families": [
        {
            "id": "prompt",
            "external_enqueue": True,
            "presentation": {
                "display_name": "Prompt",
                "description": "User-provided generic prompt.",
            },
        },
        {
            "id": "task_artifact",
            "external_enqueue": False,
            "presentation": {
                "display_name": "Task Artifact",
                "description": "Runtime-accepted Taskmaster artifact.",
            },
        },
        {
            "id": "task_incident",
            "external_enqueue": False,
            "presentation": {
                "display_name": "Task Incident",
                "description": "Runtime-generated repair request.",
            },
        },
    ],
    "external_enqueue_routes": [
        {
            "id": "kernel_ping.external_prompt",
            "queue_family_id": "prompt",
            "graph_node_id": "kernel_ping.taskmaster.start",
            "stage_kind_id": "kernel_ping.taskmaster",
            "runner_binding_id": "kernel_ping.taskmaster_runner",
        }
    ],
    "artifact_schemas": [
        {
            "id": "kernel_ping.task_artifact",
            "schema": {
                "type": "object",
                "required": (
                    "artifact_kind",
                    "source_prompt_id",
                    "title",
                    "objective",
                    "requirements",
                    "completion_tests",
                ),
                "properties": {
                    "artifact_kind": {"const": "kernel_ping.task_artifact"},
                    "artifact_version": {"type": "integer"},
                    "source_prompt_id": {"type": "string"},
                    "title": {"type": "string"},
                    "objective": {"type": "string"},
                    "requirements": {
                        "type": "array",
                        "min_items": 1,
                        "items": {
                            "type": "object",
                            "required": ("id", "description"),
                            "properties": {
                                "id": {"type": "string", "min_length": 1},
                                "description": {
                                    "type": "string",
                                    "min_length": 1,
                                },
                            },
                        },
                    },
                    "completion_tests": {
                        "type": "array",
                        "min_items": 1,
                        "items": {
                            "type": "object",
                            "required": ("id", "description", "expected_result"),
                            "properties": {
                                "id": {"type": "string", "min_length": 1},
                                "description": {
                                    "type": "string",
                                    "min_length": 1,
                                },
                                "expected_result": {
                                    "type": "string",
                                    "min_length": 1,
                                },
                            },
                        },
                    },
                },
            },
            "presentation": {"display_name": "Task artifact schema"},
        },
        {
            "id": "kernel_ping.task_incident",
            "schema": {
                "type": "object",
                "required": (
                    "incident_kind",
                    "incident_version",
                    "source_prompt_id",
                    "source_task_artifact_id",
                    "worker_run_id",
                    "reason",
                    "worker_summary",
                    "missing_details",
                    "requested_taskmaster_action",
                ),
                "properties": {
                    "incident_kind": {"const": "kernel_ping.task_incident"},
                    "incident_version": {"type": "integer"},
                    "source_prompt_id": {"type": "string"},
                    "source_task_artifact_id": {"type": "string"},
                    "worker_run_id": {"type": "string"},
                    "reason": {"const": "insufficient_task_detail"},
                    "worker_summary": {"type": "string", "min_length": 1},
                    "missing_details": {
                        "type": "array",
                        "min_items": 1,
                        "items": {"type": "string", "min_length": 1},
                    },
                    "requested_taskmaster_action": {"const": "revise_task_artifact"},
                },
            },
            "presentation": {"display_name": "Task incident schema"},
        },
    ],
    "assets": [
        {
            "id": "kernel_ping.taskmaster_prompt",
            "kind": "prompt",
            "body": "Convert prompt input into the declared task artifact.",
            "presentation": {
                "display_name": "Taskmaster entrypoint prompt",
                "details": {"entrypoint": "Taskmaster"},
            },
        },
        {
            "id": "kernel_ping.worker_prompt",
            "kind": "prompt",
            "body": "Complete the declared work and report a legal marker.",
            "presentation": {
                "display_name": "Worker entrypoint prompt",
                "details": {"entrypoint": "Worker"},
            },
        },
        {
            "id": "kernel_ping.tdd_core",
            "kind": "skill",
            "body": "State completion tests first and satisfy them before success.",
            "presentation": {"display_name": "TDD core"},
        },
        {
            "id": "kernel_ping.task_artifact_authoring",
            "kind": "skill",
            "body": "Author concise executable task artifacts with tests.",
            "presentation": {"display_name": "Task artifact authoring"},
        },
    ],
    "terminal_outcomes": [
        {
            "id": "kernel_ping.taskmaster.task_complete",
            "stage_kind_id": "kernel_ping.taskmaster",
            "marker": "TASK_COMPLETE",
            "presentation": {"display_name": "Task complete"},
        },
        {
            "id": "kernel_ping.taskmaster.blocked",
            "stage_kind_id": "kernel_ping.taskmaster",
            "marker": "BLOCKED",
            "presentation": {"display_name": "Taskmaster blocked"},
        },
        {
            "id": "kernel_ping.worker.work_complete",
            "stage_kind_id": "kernel_ping.worker",
            "marker": "WORK_COMPLETE",
            "presentation": {"display_name": "Work complete"},
        },
        {
            "id": "kernel_ping.worker.needs_review",
            "stage_kind_id": "kernel_ping.worker",
            "marker": "NEEDS_REVIEW",
            "presentation": {"display_name": "Needs review"},
        },
        {
            "id": "kernel_ping.worker.blocked",
            "stage_kind_id": "kernel_ping.worker",
            "marker": "BLOCKED",
            "presentation": {"display_name": "Worker blocked"},
        },
    ],
    "stage_kinds": [
        {
            "id": "kernel_ping.taskmaster",
            "partition_id": "craft",
            "runner_binding_id": "kernel_ping.taskmaster_runner",
            "input_queue_family_ids": ("prompt", "task_incident"),
            "output_queue_family_ids": ("task_artifact",),
            "artifact_schema_ids": (
                "kernel_ping.task_artifact",
                "kernel_ping.task_incident",
            ),
            "asset_ids": (
                "kernel_ping.taskmaster_prompt",
                "kernel_ping.tdd_core",
                "kernel_ping.task_artifact_authoring",
            ),
            "declared_outcome_ids": (
                "kernel_ping.taskmaster.task_complete",
                "kernel_ping.taskmaster.blocked",
            ),
            "presentation": {
                "display_name": "Taskmaster",
                "description": "Turn prompt or incident input into a task artifact.",
                "details": {"entrypoint": "Taskmaster"},
            },
        },
        {
            "id": "kernel_ping.worker",
            "partition_id": "craft",
            "runner_binding_id": "kernel_ping.worker_runner",
            "input_queue_family_ids": ("task_artifact",),
            "output_queue_family_ids": ("task_incident",),
            "artifact_schema_ids": (
                "kernel_ping.task_artifact",
                "kernel_ping.task_incident",
            ),
            "asset_ids": ("kernel_ping.worker_prompt", "kernel_ping.tdd_core"),
            "declared_outcome_ids": (
                "kernel_ping.worker.work_complete",
                "kernel_ping.worker.needs_review",
                "kernel_ping.worker.blocked",
            ),
            "presentation": {
                "display_name": "Worker",
                "description": "Implement work and report declared outcomes.",
                "details": {"entrypoint": "Worker"},
            },
        },
    ],
    "terminal_actions": [
        {
            "id": "kernel_ping.route_taskmaster_success",
            "stage_kind_id": "kernel_ping.taskmaster",
            "outcome_id": "kernel_ping.taskmaster.task_complete",
            "kind": "route",
            "target_stage_kind_id": "kernel_ping.worker",
            "target_graph_node_id": "kernel_ping.worker.start",
            "emitted_queue_family_id": "task_artifact",
            "artifact_schema_id": "kernel_ping.task_artifact",
            "runner_binding_id": "kernel_ping.worker_runner",
            "asset_ids": ("kernel_ping.worker_prompt", "kernel_ping.tdd_core"),
            "payload_projection": {
                "kind": "source",
                "path": ("artifact_payload",),
            },
            "presentation": {"display_name": "Route task artifact to Worker"},
        },
        {
            "id": "kernel_ping.pause_taskmaster_blocked",
            "stage_kind_id": "kernel_ping.taskmaster",
            "outcome_id": "kernel_ping.taskmaster.blocked",
            "kind": "pause_quarantine",
            "target_stage_kind_id": None,
            "target_graph_node_id": None,
            "emitted_queue_family_id": None,
            "artifact_schema_id": None,
            "runner_binding_id": None,
            "asset_ids": (),
            "payload_projection": None,
            "presentation": {"display_name": "Pause on Taskmaster block"},
        },
        {
            "id": "kernel_ping.close_worker_success",
            "stage_kind_id": "kernel_ping.worker",
            "outcome_id": "kernel_ping.worker.work_complete",
            "kind": "close",
            "target_stage_kind_id": None,
            "target_graph_node_id": None,
            "emitted_queue_family_id": None,
            "artifact_schema_id": None,
            "runner_binding_id": None,
            "asset_ids": (),
            "payload_projection": None,
            "presentation": {"display_name": "Close completed work"},
        },
        {
            "id": "kernel_ping.route_worker_review",
            "stage_kind_id": "kernel_ping.worker",
            "outcome_id": "kernel_ping.worker.needs_review",
            "kind": "create_incident_route",
            "target_stage_kind_id": "kernel_ping.taskmaster",
            "target_graph_node_id": "kernel_ping.taskmaster.review",
            "emitted_queue_family_id": "task_incident",
            "artifact_schema_id": "kernel_ping.task_incident",
            "runner_binding_id": "kernel_ping.taskmaster_runner",
            "asset_ids": (
                "kernel_ping.taskmaster_prompt",
                "kernel_ping.tdd_core",
                "kernel_ping.task_artifact_authoring",
            ),
            "payload_projection": {
                "kind": "object",
                "fields": {
                    "incident_kind": {
                        "kind": "literal",
                        "value": "kernel_ping.task_incident",
                    },
                    "incident_version": {"kind": "literal", "value": 1},
                    "source_prompt_id": {
                        "kind": "source",
                        "path": ("work_item_payload", "source_prompt_id"),
                    },
                    "source_task_artifact_id": {
                        "kind": "source",
                        "path": ("run_metadata", "work_item_id"),
                    },
                    "worker_run_id": {
                        "kind": "source",
                        "path": ("run_metadata", "run_id"),
                    },
                    "reason": {
                        "kind": "literal",
                        "value": "insufficient_task_detail",
                    },
                    "worker_summary": {
                        "kind": "source",
                        "path": ("artifact_payload", "worker_summary"),
                    },
                    "missing_details": {
                        "kind": "source",
                        "path": ("artifact_payload", "missing_details"),
                    },
                    "requested_taskmaster_action": {
                        "kind": "literal",
                        "value": "revise_task_artifact",
                    },
                },
            },
            "presentation": {"display_name": "Route review incident"},
        },
        {
            "id": "kernel_ping.pause_worker_blocked",
            "stage_kind_id": "kernel_ping.worker",
            "outcome_id": "kernel_ping.worker.blocked",
            "kind": "pause_quarantine",
            "target_stage_kind_id": None,
            "target_graph_node_id": None,
            "emitted_queue_family_id": None,
            "artifact_schema_id": None,
            "runner_binding_id": None,
            "asset_ids": (),
            "payload_projection": None,
            "presentation": {"display_name": "Pause on Worker block"},
        },
    ],
    "runner_bindings": [
        {
            "id": "kernel_ping.taskmaster_runner",
            "adapter_kind": "fake_local",
            "stage_kind_ids": ("kernel_ping.taskmaster",),
            "required_capability_ids": (
                "capability.runner.invoke",
                "terminal.intent",
                "unrestricted.filesystem.read",
                "unrestricted.filesystem.write",
                "unrestricted.process.execute",
            ),
            "component_pin": {
                "component_kind": "runner",
                "component_id": "millforge-base",
                "component_version": "2",
                "provider_distribution": "millforge",
                "provider_version": "0.1.0",
                "descriptor_media_type": "application/json",
                "descriptor_sha256": (
                    "0bace7b27871b03cd7ffe59951953348b3da3214536178d6f447a21de4403464"
                ),
                "required_capability_ids": (
                    "terminal.intent",
                    "unrestricted.filesystem.read",
                    "unrestricted.filesystem.write",
                    "unrestricted.process.execute",
                ),
                "legal_terminal_result_ids": ("BLOCKED", "TASK_COMPLETE"),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "BLOCKED",
                    "outcome_id": "kernel_ping.taskmaster.blocked",
                },
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "TASK_COMPLETE",
                    "outcome_id": "kernel_ping.taskmaster.task_complete",
                },
            ),
            "presentation": {"display_name": "Millforge Taskmaster runner"},
        },
        {
            "id": "kernel_ping.worker_runner",
            "adapter_kind": "fake_local",
            "stage_kind_ids": ("kernel_ping.worker",),
            "required_capability_ids": (
                "capability.runner.invoke",
                "terminal.intent",
                "unrestricted.filesystem.read",
                "unrestricted.filesystem.write",
                "unrestricted.process.execute",
            ),
            "component_pin": {
                "component_kind": "runner",
                "component_id": "millforge-base",
                "component_version": "2",
                "provider_distribution": "millforge",
                "provider_version": "0.1.0",
                "descriptor_media_type": "application/json",
                "descriptor_sha256": (
                    "d6b5c75f48565b939ee4d6e30b83e3ad203764b7bda02890ca515a9bfb3318f0"
                ),
                "required_capability_ids": (
                    "terminal.intent",
                    "unrestricted.filesystem.read",
                    "unrestricted.filesystem.write",
                    "unrestricted.process.execute",
                ),
                "legal_terminal_result_ids": (
                    "BLOCKED",
                    "NEEDS_REVIEW",
                    "WORK_COMPLETE",
                ),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": "kernel_ping.worker",
                    "runner_result_id": "BLOCKED",
                    "outcome_id": "kernel_ping.worker.blocked",
                },
                {
                    "stage_kind_id": "kernel_ping.worker",
                    "runner_result_id": "NEEDS_REVIEW",
                    "outcome_id": "kernel_ping.worker.needs_review",
                },
                {
                    "stage_kind_id": "kernel_ping.worker",
                    "runner_result_id": "WORK_COMPLETE",
                    "outcome_id": "kernel_ping.worker.work_complete",
                },
            ),
            "presentation": {"display_name": "Millforge Worker runner"},
        },
    ],
    "capabilities": [
        {
            "id": capability_id,
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
            "approval_policy_id": None,
        }
        for capability_id in (
            "capability.runner.invoke",
            "terminal.intent",
            "unrestricted.filesystem.read",
            "unrestricted.filesystem.write",
            "unrestricted.process.execute",
        )
    ],
}


def workflow_source() -> dict[str, object]:
    return deepcopy(WORKFLOW_SOURCE)


__all__ = (
    "WORKFLOW_SOURCE",
    "workflow_source",
)
