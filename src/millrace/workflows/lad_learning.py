"""Hosted full LAD workflow fixture with selected Learning authority."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

from millrace.workflows import lad_planning

_RUNNER_ID = "learning.standard.local_runner"
_RUNNER_INVOKE_CAPABILITY_ID = "capability.runner.invoke"
_REQUEST_SCHEMA_ID = "learning.intake.request"
_STAGE_RESULT_SCHEMA_ID = "learning.artifacts.stage_result"
_RESEARCH_PACKET_SCHEMA_ID = "learning.artifacts.research_packet"
_SKILL_CANDIDATE_SCHEMA_ID = "learning.artifacts.skill_candidate"
_PROFESSOR_NOTES_SCHEMA_ID = "learning.artifacts.professor_notes"
_SKILL_UPDATE_SCHEMA_ID = "learning.artifacts.skill_update"
_CURATOR_DECISION_SCHEMA_ID = "learning.artifacts.curator_decision"
_SKILL_INSTALL_REPORT_SCHEMA_ID = "learning.artifacts.skill_install_report"
_REPORT_SCHEMA_ID = "learning.artifacts.report"
_CURATOR_EFFECT_DECLARATION_ID = "learning.effect.curator.workspace_skill_update"
_LIBRARIAN_EFFECT_DECLARATION_ID = (
    "learning.effect.librarian.workspace_skill_install_report"
)
_FAKE_LOCAL_EFFECT_PROVIDER_REF = "provider.fake_local.workspace"
_FAKE_LOCAL_EFFECT_CAPABILITY_POLICY_REF = "policy.fake_local.no_real_side_effects"
_OPERATOR_WAIT_AUDIT_METADATA_REQUIREMENTS = (
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
_LEARNING_REQUEST_SOURCE_SCHEMA_IDS = frozenset(
    {
        "execution.artifacts.stage_result",
        "execution.artifacts.report",
        "execution.artifacts.incident_report",
        "planning.artifacts.stage_result",
    }
)
_STAGE_ARTIFACT_SCHEMA_IDS = {
    "analyst": (
        _RESEARCH_PACKET_SCHEMA_ID,
        _REPORT_SCHEMA_ID,
    ),
    "professor": (
        _RESEARCH_PACKET_SCHEMA_ID,
        _SKILL_CANDIDATE_SCHEMA_ID,
        _PROFESSOR_NOTES_SCHEMA_ID,
        _REPORT_SCHEMA_ID,
    ),
    "curator": (
        _SKILL_CANDIDATE_SCHEMA_ID,
        _SKILL_UPDATE_SCHEMA_ID,
        _CURATOR_DECISION_SCHEMA_ID,
        _REPORT_SCHEMA_ID,
    ),
    "librarian": (
        _SKILL_INSTALL_REPORT_SCHEMA_ID,
        _REPORT_SCHEMA_ID,
    ),
}
_NOOP_ARTIFACT_SCHEMA_IDS = {
    "analyst": _RESEARCH_PACKET_SCHEMA_ID,
    "professor": _PROFESSOR_NOTES_SCHEMA_ID,
    "curator": _CURATOR_DECISION_SCHEMA_ID,
    "librarian": _SKILL_INSTALL_REPORT_SCHEMA_ID,
}


def _section(
    source: dict[str, object],
    key: str,
) -> tuple[dict[str, object], ...]:
    return tuple(cast(tuple[dict[str, object], ...], source.get(key, ())))


def _merge_by_id(*groups: tuple[dict[str, object], ...]) -> list[dict[str, object]]:
    selected: dict[str, dict[str, object]] = {}
    for group in groups:
        for record in group:
            record_id = str(record["id"])
            existing = selected.get(record_id)
            if existing is not None and existing != record:
                raise ValueError(f"conflicting selected declaration for id {record_id}")
            selected.setdefault(record_id, record)
    return list(selected.values())


def _required_string_schema() -> dict[str, object]:
    return {"type": "string", "min_length": 1}


def _object_schema(
    *,
    artifact_kind: str,
    required: tuple[str, ...] = ("summary",),
) -> dict[str, object]:
    return {
        "type": "object",
        "required": ("artifact_kind", *required),
        "properties": {
            "artifact_kind": {"const": artifact_kind},
            **{field: _required_string_schema() for field in required},
        },
    }


def _request_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ("request_id", "body", "root_source"),
        "properties": {
            "request_id": _required_string_schema(),
            "body": _required_string_schema(),
            "root_source": {
                "type": "object",
                "required": ("kind", "source_id"),
                "properties": {
                    "kind": _required_string_schema(),
                    "source_id": _required_string_schema(),
                },
            },
            "target_skill_id": _required_string_schema(),
            "preferred_output_paths": {
                "type": "array",
                "min_items": 1,
                "items": _required_string_schema(),
            },
        },
    }


def _learning_request_array_schema() -> dict[str, object]:
    return {
        "type": "array",
        "min_items": 1,
        "unique_by": "request_id",
        "items": _request_schema(),
    }


def _schema_with_optional_learning_requests(
    record: dict[str, object],
) -> dict[str, object]:
    if str(record.get("id")) not in _LEARNING_REQUEST_SOURCE_SCHEMA_IDS:
        return record
    updated = deepcopy(record)
    schema = dict(cast(dict[str, object], updated["schema"]))
    properties = dict(cast(dict[str, object], schema.get("properties", {})))
    properties["learning_requests"] = _learning_request_array_schema()
    schema["properties"] = properties
    updated["schema"] = schema
    return updated


def _entrypoint_asset_id(stage_id: str) -> str:
    return f"learning.entrypoints.{stage_id}"


def _skill_asset_id(stage_id: str) -> str:
    return f"learning.skills.{stage_id}_core"


def _node_id(stage_id: str) -> str:
    return f"learning.standard.{stage_id}"


def _outcome_id(stage_id: str, outcome: str) -> str:
    return f"learning.{stage_id}.{outcome}"


def _asset(stage_id: str, *, kind: str, display_name: str) -> dict[str, object]:
    namespace = "entrypoints" if kind == "prompt" else "skills"
    path = (
        f"entrypoints/learning/{stage_id}.md"
        if kind == "prompt"
        else f"skills/stage/learning/{stage_id}-core/SKILL.md"
    )
    suffix = stage_id if kind == "prompt" else f"{stage_id}_core"
    return {
        "id": f"learning.{namespace}.{suffix}",
        "kind": kind,
        "body": f"Execute the selected Learning {display_name} stage contract.",
        "presentation": {
            "display_name": f"Learning {display_name} {kind}",
            "details": {"path": path},
        },
    }


def _stage(
    stage_id: str,
    *,
    graph_order: int,
    input_queue_family_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "id": stage_id,
        "partition_id": "learning",
        "runner_binding_id": _RUNNER_ID,
        "input_queue_family_ids": input_queue_family_ids,
        "output_queue_family_ids": ("stage_result", "learning_request"),
        "artifact_schema_ids": (
            _REQUEST_SCHEMA_ID,
            *_STAGE_ARTIFACT_SCHEMA_IDS[stage_id],
        ),
        "asset_ids": (_entrypoint_asset_id(stage_id), _skill_asset_id(stage_id)),
        "declared_outcome_ids": (
            _outcome_id(stage_id, "complete"),
            _outcome_id(stage_id, "noop"),
            _outcome_id(stage_id, "blocked"),
        ),
        "presentation": {
            "display_name": f"Learning {stage_id.title()}",
            "details": {"node_id": stage_id, "graph_order": graph_order},
        },
    }


def _outcome(stage_id: str, outcome: str, marker: str) -> dict[str, object]:
    return {
        "id": _outcome_id(stage_id, outcome),
        "stage_kind_id": stage_id,
        "marker": marker,
        "presentation": {"display_name": marker},
    }


def _route_action(
    *,
    action_id: str,
    stage_id: str,
    outcome: str,
    target_stage_id: str,
    artifact_schema_id: str,
) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": stage_id,
        "outcome_id": _outcome_id(stage_id, outcome),
        "kind": "route",
        "target_stage_kind_id": target_stage_id,
        "target_graph_node_id": _node_id(target_stage_id),
        "emitted_queue_family_id": "stage_result",
        "artifact_schema_id": artifact_schema_id,
        "runner_binding_id": _RUNNER_ID,
        "asset_ids": (
            _entrypoint_asset_id(target_stage_id),
            _skill_asset_id(target_stage_id),
        ),
        "payload_projection": {"kind": "source", "path": ("artifact_payload",)},
        "presentation": {"display_name": action_id},
    }


def _close_action(
    *,
    action_id: str,
    stage_id: str,
    outcome: str,
    artifact_schema_id: str,
    kind: str = "complete_work_item",
) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": stage_id,
        "outcome_id": _outcome_id(stage_id, outcome),
        "kind": kind,
        "artifact_schema_id": artifact_schema_id,
        "presentation": {"display_name": action_id},
    }


def _artifact_schemas() -> tuple[dict[str, object], ...]:
    return (
        {
            "id": _REQUEST_SCHEMA_ID,
            "schema": _request_schema(),
            "presentation": {"display_name": "Learning request"},
        },
        {
            "id": _STAGE_RESULT_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_STAGE_RESULT_SCHEMA_ID),
            "presentation": {"display_name": "Learning stage result"},
        },
        {
            "id": _RESEARCH_PACKET_SCHEMA_ID,
            "schema": _object_schema(
                artifact_kind=_RESEARCH_PACKET_SCHEMA_ID,
                required=("summary", "research_notes"),
            ),
            "presentation": {"display_name": "Learning research packet"},
        },
        {
            "id": _SKILL_CANDIDATE_SCHEMA_ID,
            "schema": _object_schema(
                artifact_kind=_SKILL_CANDIDATE_SCHEMA_ID,
                required=("summary", "skill_id", "candidate_body"),
            ),
            "presentation": {"display_name": "Learning skill candidate"},
        },
        {
            "id": _PROFESSOR_NOTES_SCHEMA_ID,
            "schema": _object_schema(
                artifact_kind=_PROFESSOR_NOTES_SCHEMA_ID,
                required=("summary", "notes"),
            ),
            "presentation": {"display_name": "Learning professor notes"},
        },
        {
            "id": _SKILL_UPDATE_SCHEMA_ID,
            "schema": _object_schema(
                artifact_kind=_SKILL_UPDATE_SCHEMA_ID,
                required=("summary", "target_skill_id", "update_body"),
            ),
            "presentation": {"display_name": "Learning skill update"},
        },
        {
            "id": _CURATOR_DECISION_SCHEMA_ID,
            "schema": _object_schema(
                artifact_kind=_CURATOR_DECISION_SCHEMA_ID,
                required=("summary", "decision"),
            ),
            "presentation": {"display_name": "Learning curator decision"},
        },
        {
            "id": _SKILL_INSTALL_REPORT_SCHEMA_ID,
            "schema": _object_schema(
                artifact_kind=_SKILL_INSTALL_REPORT_SCHEMA_ID,
                required=("summary", "target_skill_id", "installed_path"),
            ),
            "presentation": {"display_name": "Learning skill install report"},
        },
        {
            "id": _REPORT_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_REPORT_SCHEMA_ID),
            "presentation": {"display_name": "Learning blocked report"},
        },
    )


def _fanout(
    *,
    fanout_id: str,
    source_action_id: str,
    source_artifact_schema_id: str,
    target_route_id: str = "learning.trigger.analyst",
    target_payload_mapping: dict[str, tuple[str, ...]] | None = None,
    dependency_policy: str = "none",
) -> dict[str, object]:
    return {
        "id": fanout_id,
        "source_action_id": source_action_id,
        "source_artifact_schema_id": source_artifact_schema_id,
        "item_source_path": ("learning_requests",),
        "item_id_key": "request_id",
        "target_route_id": target_route_id,
        "target_payload_schema_id": _REQUEST_SCHEMA_ID,
        "target_payload_mapping": target_payload_mapping
        or {
            "request_id": ("request_id",),
            "body": ("body",),
            "root_source": ("root_source",),
        },
        "source_state_policy": "accepted_terminal_observation",
        "duplicate_policy": "refuse",
        "root_lineage_policy": "inherit_source_lineage",
        "dependency_policy": dependency_policy,
    }


def _effect_declarations() -> tuple[dict[str, object], ...]:
    return (
        {
            "id": _CURATOR_EFFECT_DECLARATION_ID,
            "terminal_action_id": "learning.close_curator_complete",
            "artifact_schema_id": _SKILL_UPDATE_SCHEMA_ID,
            "provider_ref": _FAKE_LOCAL_EFFECT_PROVIDER_REF,
            "capability_policy_ref": _FAKE_LOCAL_EFFECT_CAPABILITY_POLICY_REF,
            "target_ref_kind": "workspace_skill_update",
            "target_ref_schema": "learning.effects.target.workspace_skill_update.v1",
            "allowed_reconciliation_statuses": ("applied", "no_op", "refused"),
            "real_side_effects_allowed": False,
        },
        {
            "id": _LIBRARIAN_EFFECT_DECLARATION_ID,
            "terminal_action_id": "learning.close_librarian_complete",
            "artifact_schema_id": _SKILL_INSTALL_REPORT_SCHEMA_ID,
            "provider_ref": _FAKE_LOCAL_EFFECT_PROVIDER_REF,
            "capability_policy_ref": _FAKE_LOCAL_EFFECT_CAPABILITY_POLICY_REF,
            "target_ref_kind": "workspace_skill_install_report",
            "target_ref_schema": (
                "learning.effects.target.workspace_skill_install_report.v1"
            ),
            "allowed_reconciliation_statuses": ("applied", "no_op", "refused"),
            "real_side_effects_allowed": False,
        },
    )


def _blocked_operator_wait(stage_id: str) -> dict[str, object]:
    return {
        "id": f"learning.{stage_id}_blocked_wait",
        "source_action_ids": (f"learning.close_{stage_id}_blocked",),
        "wait_scope": "lineage",
        "source_work_item_behavior": "leave_open",
        "unrelated_lineages_continue": True,
        "allowed_resolution_kinds": (
            "resume_recorded_source",
            "close_recorded_source",
            "revise_recorded_source",
        ),
        "payload_schema_id": _REQUEST_SCHEMA_ID,
        "target_queue_family_id": "learning_request",
        "target_stage_kind_id": "analyst",
        "target_graph_node_id": _node_id("analyst"),
        "target_runner_binding_id": _RUNNER_ID,
        "actor_kind": "local_operator",
        "audit_metadata_requirements": _OPERATOR_WAIT_AUDIT_METADATA_REQUIREMENTS,
        "correlation_key": "wait_id",
        "idempotency": "input_receipt_and_active_wait_status",
        "timeout_policy": "none",
        "expiry_policy": "none",
        "cancellation_policy": "selected_resolution_only",
        "status_effect": "operator_wait_active",
    }


def _base_source() -> dict[str, object]:
    planning_source = lad_planning.workflow_source()
    planning_graphs = _section(planning_source, "graphs")
    planning_partitions = _section(planning_source, "partitions")
    planning_queues = _section(planning_source, "queue_families")
    planning_routes = _section(planning_source, "external_enqueue_routes")
    planning_schemas = tuple(
        _schema_with_optional_learning_requests(record)
        for record in _section(planning_source, "artifact_schemas")
    )
    planning_assets = _section(planning_source, "assets")
    planning_outcomes = _section(planning_source, "terminal_outcomes")
    planning_stages = _section(planning_source, "stage_kinds")
    planning_actions = _section(planning_source, "terminal_actions")
    return {
        "lineage_policy": "root_from_external_enqueue",
        "workflow": {
            "id": "lad.full",
            "version": "0.1",
            "name": "Full LAD",
            "compatibility_profile": None,
            "required_extensions": (),
        },
        "graphs": [
            *planning_graphs,
            {
                "id": "learning.standard.graph",
                "node_ids": (
                    _node_id("analyst"),
                    _node_id("professor"),
                    _node_id("curator"),
                    _node_id("librarian"),
                ),
                "presentation": {"display_name": "Learning Standard Graph"},
            },
        ],
        "partitions": _merge_by_id(
            planning_partitions,
            (
                {
                    "id": "learning",
                    "kind": "plane",
                    "presentation": {"display_name": "Learning"},
                },
            ),
        ),
        "queue_families": _merge_by_id(
            planning_queues,
            (
                {
                    "id": "learning_request",
                    "external_enqueue": True,
                    "presentation": {"display_name": "Learning request"},
                },
            ),
        ),
        "external_enqueue_routes": [
            *planning_routes,
            {
                "id": "learning_request",
                "queue_family_id": "learning_request",
                "graph_node_id": _node_id("analyst"),
                "stage_kind_id": "analyst",
                "runner_binding_id": _RUNNER_ID,
                "payload_schema_id": _REQUEST_SCHEMA_ID,
            },
        ],
        "generated_work_routes": [
            {
                "id": "learning.trigger.analyst",
                "queue_family_id": "learning_request",
                "graph_node_id": _node_id("analyst"),
                "stage_kind_id": "analyst",
                "runner_binding_id": _RUNNER_ID,
                "payload_schema_id": _REQUEST_SCHEMA_ID,
            },
            {
                "id": "learning.trigger.librarian",
                "queue_family_id": "learning_request",
                "graph_node_id": _node_id("librarian"),
                "stage_kind_id": "librarian",
                "runner_binding_id": _RUNNER_ID,
                "payload_schema_id": _REQUEST_SCHEMA_ID,
            },
        ],
        "artifact_schemas": _merge_by_id(planning_schemas, _artifact_schemas()),
        "assets": _merge_by_id(
            planning_assets,
            tuple(
                asset
                for stage_id in ("analyst", "professor", "curator", "librarian")
                for asset in (
                    _asset(stage_id, kind="prompt", display_name=stage_id.title()),
                    _asset(stage_id, kind="skill", display_name=stage_id.title()),
                )
            ),
        ),
        "stage_kinds": (
            *planning_stages,
            _stage(
                "analyst",
                graph_order=10,
                input_queue_family_ids=("learning_request", "stage_result"),
            ),
            _stage(
                "professor",
                graph_order=20,
                input_queue_family_ids=("stage_result",),
            ),
            _stage("curator", graph_order=30, input_queue_family_ids=("stage_result",)),
            _stage(
                "librarian",
                graph_order=40,
                input_queue_family_ids=("learning_request", "stage_result"),
            ),
        ),
        "terminal_outcomes": (
            *planning_outcomes,
            *(
                _outcome(stage_id, outcome, marker)
                for stage_id, markers in {
                    "analyst": {
                        "complete": "ANALYST_COMPLETE",
                        "noop": "ANALYST_NOOP",
                        "blocked": "BLOCKED",
                    },
                    "professor": {
                        "complete": "PROFESSOR_COMPLETE",
                        "noop": "PROFESSOR_NOOP",
                        "blocked": "BLOCKED",
                    },
                    "curator": {
                        "complete": "CURATOR_COMPLETE",
                        "noop": "CURATOR_NOOP",
                        "blocked": "BLOCKED",
                    },
                    "librarian": {
                        "complete": "LIBRARIAN_COMPLETE",
                        "noop": "LIBRARIAN_NOOP",
                        "blocked": "BLOCKED",
                    },
                }.items()
                for outcome, marker in markers.items()
            ),
        ),
        "terminal_actions": (
            *planning_actions,
            _route_action(
                action_id="learning.route_analyst_complete",
                stage_id="analyst",
                outcome="complete",
                target_stage_id="professor",
                artifact_schema_id=_RESEARCH_PACKET_SCHEMA_ID,
            ),
            _route_action(
                action_id="learning.route_professor_complete",
                stage_id="professor",
                outcome="complete",
                target_stage_id="curator",
                artifact_schema_id=_SKILL_CANDIDATE_SCHEMA_ID,
            ),
            _close_action(
                action_id="learning.close_curator_complete",
                stage_id="curator",
                outcome="complete",
                artifact_schema_id=_SKILL_UPDATE_SCHEMA_ID,
            ),
            _close_action(
                action_id="learning.close_librarian_complete",
                stage_id="librarian",
                outcome="complete",
                artifact_schema_id=_SKILL_INSTALL_REPORT_SCHEMA_ID,
            ),
            *(
                _close_action(
                    action_id=f"learning.close_{stage_id}_{outcome}",
                    stage_id=stage_id,
                    outcome=outcome,
                    artifact_schema_id=(
                        _REPORT_SCHEMA_ID
                        if outcome == "blocked"
                        else _NOOP_ARTIFACT_SCHEMA_IDS[stage_id]
                    ),
                    kind=(
                        "operator_wait"
                        if outcome == "blocked"
                        else "complete_work_item"
                    ),
                )
                for stage_id in ("analyst", "professor", "curator", "librarian")
                for outcome in ("noop", "blocked")
            ),
        ),
        "effect_declarations": _effect_declarations(),
        "fanout_declarations": (
            *_section(planning_source, "fanout_declarations"),
            _fanout(
                fanout_id="learning.trigger.execution.doublechecker_pass",
                source_action_id="execution.route_doublechecker_pass",
                source_artifact_schema_id="execution.artifacts.stage_result",
            ),
            _fanout(
                fanout_id="learning.trigger.execution.troubleshooter_complete",
                source_action_id="execution.return_troubleshooter_complete",
                source_artifact_schema_id="execution.artifacts.report",
            ),
            _fanout(
                fanout_id="learning.trigger.execution.troubleshooter_blocked",
                source_action_id="execution.route_troubleshooter_blocked",
                source_artifact_schema_id="execution.artifacts.stage_result",
            ),
            _fanout(
                fanout_id="learning.trigger.execution.consultant_complete",
                source_action_id="execution.route_consultant_complete",
                source_artifact_schema_id="execution.artifacts.report",
            ),
            _fanout(
                fanout_id="learning.trigger.execution.consultant_blocked",
                source_action_id="execution.close_consultant_blocked",
                source_artifact_schema_id="execution.artifacts.report",
            ),
            _fanout(
                fanout_id="learning.trigger.execution.needs_planning",
                source_action_id="execution.close_consultant_needs_plan",
                source_artifact_schema_id="execution.artifacts.incident_report",
            ),
            _fanout(
                fanout_id="learning.trigger.planning.planner_complete",
                source_action_id="planning.route_planner_complete",
                source_artifact_schema_id="planning.artifacts.stage_result",
                target_route_id="learning.trigger.librarian",
                target_payload_mapping={
                    "request_id": ("request_id",),
                    "body": ("body",),
                    "root_source": ("root_source",),
                    "target_skill_id": ("target_skill_id",),
                    "preferred_output_paths": ("preferred_output_paths",),
                },
            ),
        ),
        "concurrency_policies": (
            {
                "id": "foreground.planning",
                "partition_id": "planning",
                "max_active_runs": 1,
                "coexist_partition_ids": ("learning",),
            },
            {
                "id": "foreground.execution",
                "partition_id": "execution",
                "max_active_runs": 1,
                "coexist_partition_ids": ("learning",),
            },
            {
                "id": "learning.standard",
                "partition_id": "learning",
                "max_active_runs": 1,
                "coexist_partition_ids": ("planning", "execution"),
            },
        ),
        "recovery_policies": _section(planning_source, "recovery_policies"),
        "wait_states": _section(planning_source, "wait_states"),
        "counters": _section(planning_source, "counters"),
        "completion_behaviors": _section(planning_source, "completion_behaviors"),
        "remediation_policies": _section(planning_source, "remediation_policies"),
        "intervention_options": _section(planning_source, "intervention_options"),
        "operator_waits": (
            *_section(planning_source, "operator_waits"),
            *(
                _blocked_operator_wait(stage_id)
                for stage_id in ("analyst", "professor", "curator", "librarian")
            ),
        ),
        "runner_bindings": _merge_by_id(
            _section(planning_source, "runner_bindings"),
            (
                {
                    "id": _RUNNER_ID,
                    "adapter_kind": "fake_local",
                    "stage_kind_ids": (
                        "analyst",
                        "professor",
                        "curator",
                        "librarian",
                    ),
                    "presentation": {"display_name": "Learning runner"},
                    "required_capability_ids": (_RUNNER_INVOKE_CAPABILITY_ID,),
                },
            ),
        ),
        "capabilities": _section(planning_source, "capabilities"),
    }


def workflow_source() -> dict[str, object]:
    return deepcopy(_base_source())


__all__ = ("workflow_source",)
