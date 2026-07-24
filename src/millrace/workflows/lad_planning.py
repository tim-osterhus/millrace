"""Hosted LAD Planning workflow fixture.

LAD Planning vocabulary appears here as selected workflow data. Generic
compiler and runtime packages must treat these strings as opaque IDs.
"""

from __future__ import annotations

from copy import deepcopy
from typing import cast

from millrace.workflows import lad_execution

_RUNNER_ID = "planning.lad.local_runner"
_EXECUTION_RUNNER_ID = "execution.lad.local_runner"
_RUNNER_INVOKE_CAPABILITY_ID = "capability.runner.invoke"

_STAGE_RESULT_SCHEMA_ID = "planning.artifacts.stage_result"
_RECON_PACKET_SCHEMA_ID = "planning.artifacts.recon_packet"
_GENERATED_TASK_SCHEMA_ID = "planning.artifacts.generated_task"
_GENERATED_SPEC_SCHEMA_ID = "planning.artifacts.generated_spec"
_PLANNER_DISPOSITION_SCHEMA_ID = "planning.artifacts.planner_disposition"
_SPEC_SCHEMA_ID = "planning.artifacts.spec"
_INCIDENT_REPORT_SCHEMA_ID = "planning.artifacts.incident_report"
_TASK_CARDS_SCHEMA_ID = "planning.artifacts.task_cards"
_EXECUTION_TASK_SCHEMA_ID = "execution.artifacts.task"
_REPORT_SCHEMA_ID = "planning.artifacts.report"
_RUBRIC_SCHEMA_ID = "planning.artifacts.rubric"
_VERDICT_SCHEMA_ID = "planning.artifacts.verdict"


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


def _section(
    source: dict[str, object],
    key: str,
) -> tuple[dict[str, object], ...]:
    return tuple(cast(tuple[dict[str, object], ...], source[key]))


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


def _intake_schema(
    *,
    root_kind: str,
    accepted_root_kinds: tuple[str, ...] | None = None,
) -> dict[str, object]:
    root_kind_schema: dict[str, object] = (
        {"const": root_kind}
        if accepted_root_kinds is None
        else {"enum": accepted_root_kinds}
    )
    return {
        "type": "object",
        "required": ("title", "body", "root_source"),
        "properties": {
            "title": _required_string_schema(),
            "body": _required_string_schema(),
            "root_source": {
                "type": "object",
                "required": ("kind", "source_id"),
                "properties": {
                    "kind": root_kind_schema,
                    "source_id": _required_string_schema(),
                },
            },
        },
    }


def _task_card_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ("task_card_id", "title", "body"),
        "properties": {
            "task_card_id": _required_string_schema(),
            "title": _required_string_schema(),
            "body": _required_string_schema(),
        },
    }


def _task_cards_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ("artifact_kind", "cards"),
        "properties": {
            "artifact_kind": {"const": "task_cards"},
            "cards": {
                "type": "array",
                "min_items": 1,
                "unique_by": "task_card_id",
                "items": _task_card_schema(),
            },
        },
    }


def _generated_task_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ("artifact_kind", "task_id", "body"),
        "properties": {
            "artifact_kind": {"const": _GENERATED_TASK_SCHEMA_ID},
            "task_id": _required_string_schema(),
            "body": _required_string_schema(),
        },
    }


def _generated_spec_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ("artifact_kind", "spec_id", "body"),
        "properties": {
            "artifact_kind": {"const": _GENERATED_SPEC_SCHEMA_ID},
            "spec_id": _required_string_schema(),
            "body": _required_string_schema(),
        },
    }


def _asset(
    *,
    stage_id: str,
    kind: str,
    body: str,
    display_name: str,
) -> dict[str, object]:
    path_name = stage_id
    if kind == "skill":
        path_name = f"{stage_id.removeprefix('lad_')}-core"
    path = (
        f"entrypoints/planning/{stage_id}.md"
        if kind == "prompt"
        else f"skills/stage/planning/{path_name}/SKILL.md"
    )
    namespace = "entrypoints" if kind == "prompt" else "skills"
    asset_suffix = (
        stage_id
        if kind == "prompt"
        else f"{stage_id.removeprefix('lad_')}_core"
    )
    return {
        "id": f"planning.{namespace}.{asset_suffix}",
        "kind": kind,
        "body": body,
        "presentation": {
            "display_name": display_name,
            "details": {"path": path},
        },
    }


def _entrypoint_asset_id(stage_id: str) -> str:
    return f"planning.entrypoints.{stage_id}"


def _skill_asset_id(stage_id: str) -> str:
    return f"planning.skills.{stage_id.removeprefix('lad_')}_core"


def _outcome_id(stage_id: str, outcome: str) -> str:
    return f"planning.{stage_id}.{outcome}"


def _node_id(node: str) -> str:
    return f"planning.lad.{node}.start"


def _execution_node_id(node: str) -> str:
    return f"execution.lad.{node}.start"


def _stage(
    *,
    stage_id: str,
    node: str,
    graph_order: int,
    input_queue_family_ids: tuple[str, ...],
    output_queue_family_ids: tuple[str, ...],
    artifact_schema_ids: tuple[str, ...],
    outcomes: tuple[str, ...],
    display_name: str,
) -> dict[str, object]:
    return {
        "id": stage_id,
        "partition_id": "planning",
        "runner_binding_id": _RUNNER_ID,
        "input_queue_family_ids": input_queue_family_ids,
        "output_queue_family_ids": output_queue_family_ids,
        "artifact_schema_ids": artifact_schema_ids,
        "asset_ids": (_entrypoint_asset_id(stage_id), _skill_asset_id(stage_id)),
        "declared_outcome_ids": tuple(_outcome_id(stage_id, item) for item in outcomes),
        "presentation": {
            "display_name": display_name,
            "details": {"node_id": node, "graph_order": graph_order},
        },
    }


def _outcome(
    *,
    stage_id: str,
    outcome: str,
    marker: str,
    display_name: str,
) -> dict[str, object]:
    return {
        "id": _outcome_id(stage_id, outcome),
        "stage_kind_id": stage_id,
        "marker": marker,
        "presentation": {"display_name": display_name},
    }


def _route_action(
    *,
    action_id: str,
    stage_id: str,
    outcome: str,
    target_stage_id: str,
    target_node: str,
    emitted_queue_family_id: str = "stage_result",
    artifact_schema_id: str = _STAGE_RESULT_SCHEMA_ID,
    runner_binding_id: str = _RUNNER_ID,
    asset_ids: tuple[str, ...] | None = None,
    dynamic_target_selector: dict[str, object] | None = None,
    payload_projection: dict[str, object] | None = None,
) -> dict[str, object]:
    selected_asset_ids = asset_ids or (
        _entrypoint_asset_id(target_stage_id),
        _skill_asset_id(target_stage_id),
    )
    record: dict[str, object] = {
        "id": action_id,
        "stage_kind_id": stage_id,
        "outcome_id": _outcome_id(stage_id, outcome),
        "kind": "route",
        "target_stage_kind_id": target_stage_id,
        "target_graph_node_id": target_node,
        "emitted_queue_family_id": emitted_queue_family_id,
        "artifact_schema_id": artifact_schema_id,
        "runner_binding_id": runner_binding_id,
        "asset_ids": selected_asset_ids,
        "payload_projection": payload_projection
        or {"kind": "source", "path": ("artifact_payload",)},
        "presentation": {"display_name": action_id},
    }
    if dynamic_target_selector is not None:
        record["dynamic_target_selector"] = dynamic_target_selector
    return record


def _recovery_route_action(
    *,
    action_id: str,
    stage_id: str,
    outcome: str,
    target_stage_id: str,
    target_node: str,
) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": stage_id,
        "outcome_id": _outcome_id(stage_id, outcome),
        "kind": "recovery_route",
        "target_stage_kind_id": target_stage_id,
        "target_graph_node_id": target_node,
        "runner_binding_id": _RUNNER_ID,
        "asset_ids": (_entrypoint_asset_id(target_stage_id),),
        "presentation": {"display_name": action_id},
    }


def _recovery_context_action(
    *,
    action_id: str,
    stage_id: str,
    outcome: str,
    kind: str,
    artifact_schema_id: str,
) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": stage_id,
        "outcome_id": _outcome_id(stage_id, outcome),
        "kind": kind,
        "artifact_schema_id": artifact_schema_id,
        "presentation": {"display_name": action_id},
    }


def _stage_id_for_node(node: str) -> str:
    stage_ids = {
        "recon": "recon",
        "planner": "lad_planner",
        "manager": "lad_manager",
        "mechanic": "lad_mechanic",
        "auditor": "lad_auditor",
        "arbiter": "lad_arbiter",
    }
    return stage_ids[node]


def _dynamic_route_selector(
    *,
    field_names: tuple[str, ...],
    allowed_nodes: tuple[str, ...],
    disallowed_nodes: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "kind": "observation_payload_route_target",
        "field_names": field_names,
        "disallowed_targets": disallowed_nodes,
        "targets": {
            node: {
                "target_stage_kind_id": _stage_id_for_node(node),
                "target_graph_node_id": _node_id(node),
                "emitted_queue_family_id": "stage_result",
                "runner_binding_id": _RUNNER_ID,
            }
            for node in allowed_nodes
        },
    }


def _close_action(
    *,
    action_id: str,
    stage_id: str,
    outcome: str,
    artifact_schema_id: str,
    kind: str,
) -> dict[str, object]:
    return {
        "id": action_id,
        "stage_kind_id": stage_id,
        "outcome_id": _outcome_id(stage_id, outcome),
        "kind": kind,
        "artifact_schema_id": artifact_schema_id,
        "presentation": {"display_name": action_id},
    }


def _stage_definitions() -> tuple[dict[str, object], ...]:
    return (
        _stage(
            stage_id="recon",
            node="recon",
            graph_order=10,
            input_queue_family_ids=("probe", "stage_result"),
            output_queue_family_ids=(
                "stage_result",
                "recon_packet",
                "task",
                "spec",
                "report",
            ),
            artifact_schema_ids=(
                _STAGE_RESULT_SCHEMA_ID,
                _RECON_PACKET_SCHEMA_ID,
                _GENERATED_TASK_SCHEMA_ID,
                _EXECUTION_TASK_SCHEMA_ID,
                _GENERATED_SPEC_SCHEMA_ID,
                _REPORT_SCHEMA_ID,
            ),
            outcomes=(
                "to_execution",
                "to_planning",
                "noop",
                "recon_blocked",
                "blocked",
            ),
            display_name="LAD Recon",
        ),
        _stage(
            stage_id="lad_planner",
            node="planner",
            graph_order=20,
            input_queue_family_ids=("spec", "stage_result"),
            output_queue_family_ids=("stage_result", "planner_disposition", "spec"),
            artifact_schema_ids=(
                _STAGE_RESULT_SCHEMA_ID,
                _GENERATED_SPEC_SCHEMA_ID,
                _PLANNER_DISPOSITION_SCHEMA_ID,
                _SPEC_SCHEMA_ID,
            ),
            outcomes=("complete", "blocked", "blocked_threshold_recovery"),
            display_name="LAD Planner",
        ),
        _stage(
            stage_id="lad_manager",
            node="manager",
            graph_order=30,
            input_queue_family_ids=("stage_result",),
            output_queue_family_ids=("stage_result", "task_cards"),
            artifact_schema_ids=(
                _STAGE_RESULT_SCHEMA_ID,
                _TASK_CARDS_SCHEMA_ID,
                _REPORT_SCHEMA_ID,
            ),
            outcomes=("complete", "blocked", "blocked_threshold_recovery"),
            display_name="LAD Manager",
        ),
        _stage(
            stage_id="lad_mechanic",
            node="mechanic",
            graph_order=40,
            input_queue_family_ids=("stage_result",),
            output_queue_family_ids=("stage_result", "report"),
            artifact_schema_ids=(_STAGE_RESULT_SCHEMA_ID, _REPORT_SCHEMA_ID),
            outcomes=(
                "complete",
                "blocked",
                "blocked_threshold_recovery",
                "recovered",
                "quarantine",
            ),
            display_name="LAD Mechanic",
        ),
        _stage(
            stage_id="lad_auditor",
            node="auditor",
            graph_order=50,
            input_queue_family_ids=("incident", "stage_result"),
            output_queue_family_ids=("stage_result", "incident_report"),
            artifact_schema_ids=(
                _STAGE_RESULT_SCHEMA_ID,
                _INCIDENT_REPORT_SCHEMA_ID,
            ),
            outcomes=("complete", "blocked", "blocked_threshold_recovery"),
            display_name="LAD Auditor",
        ),
        _stage(
            stage_id="lad_arbiter",
            node="arbiter",
            graph_order=60,
            input_queue_family_ids=("stage_result",),
            output_queue_family_ids=("stage_result", "rubric", "verdict", "report"),
            artifact_schema_ids=(
                _STAGE_RESULT_SCHEMA_ID,
                _RUBRIC_SCHEMA_ID,
                _VERDICT_SCHEMA_ID,
                _REPORT_SCHEMA_ID,
                _INCIDENT_REPORT_SCHEMA_ID,
            ),
            outcomes=("complete", "remediation_needed", "blocked"),
            display_name="LAD Arbiter",
        ),
    )


def _outcomes() -> tuple[dict[str, object], ...]:
    return (
        _outcome(
            stage_id="recon",
            outcome="to_execution",
            marker="RECON_TO_EXECUTION",
            display_name="Recon to execution",
        ),
        _outcome(
            stage_id="recon",
            outcome="to_planning",
            marker="RECON_TO_PLANNING",
            display_name="Recon to planning",
        ),
        _outcome(
            stage_id="recon",
            outcome="noop",
            marker="RECON_NOOP",
            display_name="Recon no-op",
        ),
        _outcome(
            stage_id="recon",
            outcome="recon_blocked",
            marker="RECON_BLOCKED",
            display_name="Recon blocked",
        ),
        _outcome(
            stage_id="recon",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Recon generic blocked",
        ),
        _outcome(
            stage_id="lad_planner",
            outcome="complete",
            marker="PLANNER_COMPLETE",
            display_name="Planner complete",
        ),
        _outcome(
            stage_id="lad_planner",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Planner blocked",
        ),
        _outcome(
            stage_id="lad_planner",
            outcome="blocked_threshold_recovery",
            marker="",
            display_name="Planner blocked threshold recovery",
        ),
        _outcome(
            stage_id="lad_manager",
            outcome="complete",
            marker="MANAGER_COMPLETE",
            display_name="Manager complete",
        ),
        _outcome(
            stage_id="lad_manager",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Manager blocked",
        ),
        _outcome(
            stage_id="lad_manager",
            outcome="blocked_threshold_recovery",
            marker="",
            display_name="Manager blocked threshold recovery",
        ),
        _outcome(
            stage_id="lad_mechanic",
            outcome="complete",
            marker="MECHANIC_COMPLETE",
            display_name="Mechanic complete",
        ),
        _outcome(
            stage_id="lad_mechanic",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Mechanic blocked",
        ),
        _outcome(
            stage_id="lad_mechanic",
            outcome="blocked_threshold_recovery",
            marker="",
            display_name="Mechanic blocked threshold recovery",
        ),
        _outcome(
            stage_id="lad_mechanic",
            outcome="recovered",
            marker="MECHANIC_RECOVERED",
            display_name="Mechanic recovered",
        ),
        _outcome(
            stage_id="lad_mechanic",
            outcome="quarantine",
            marker="MECHANIC_QUARANTINE",
            display_name="Mechanic quarantine",
        ),
        _outcome(
            stage_id="lad_auditor",
            outcome="complete",
            marker="AUDITOR_COMPLETE",
            display_name="Auditor complete",
        ),
        _outcome(
            stage_id="lad_auditor",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Auditor blocked",
        ),
        _outcome(
            stage_id="lad_auditor",
            outcome="blocked_threshold_recovery",
            marker="",
            display_name="Auditor blocked threshold recovery",
        ),
        _outcome(
            stage_id="lad_arbiter",
            outcome="complete",
            marker="ARBITER_COMPLETE",
            display_name="Arbiter complete",
        ),
        _outcome(
            stage_id="lad_arbiter",
            outcome="remediation_needed",
            marker="REMEDIATION_NEEDED",
            display_name="Remediation needed",
        ),
        _outcome(
            stage_id="lad_arbiter",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Arbiter blocked",
        ),
    )


def _actions() -> tuple[dict[str, object], ...]:
    return (
        _route_action(
            action_id="planning.recon_enqueue_task",
            stage_id="recon",
            outcome="to_execution",
            target_stage_id="lad_builder",
            target_node=_execution_node_id("builder"),
            emitted_queue_family_id="task",
            artifact_schema_id=_EXECUTION_TASK_SCHEMA_ID,
            runner_binding_id=_EXECUTION_RUNNER_ID,
            asset_ids=(
                "execution.entrypoints.lad_builder",
                "execution.skills.builder_core",
            ),
        ),
        _route_action(
            action_id="planning.recon_enqueue_spec",
            stage_id="recon",
            outcome="to_planning",
            target_stage_id="lad_planner",
            target_node=_node_id("planner"),
            emitted_queue_family_id="spec",
            artifact_schema_id=_GENERATED_SPEC_SCHEMA_ID,
        ),
        _close_action(
            action_id="planning.recon_noop",
            stage_id="recon",
            outcome="noop",
            artifact_schema_id=_RECON_PACKET_SCHEMA_ID,
            kind="complete_work_item",
        ),
        _close_action(
            action_id="planning.recon_block_work_item",
            stage_id="recon",
            outcome="recon_blocked",
            artifact_schema_id=_REPORT_SCHEMA_ID,
            kind="block_work_item",
        ),
        _close_action(
            action_id="planning.recon_blocked",
            stage_id="recon",
            outcome="blocked",
            artifact_schema_id=_REPORT_SCHEMA_ID,
            kind="block_work_item",
        ),
        _route_action(
            action_id="planning.route_planner_complete",
            stage_id="lad_planner",
            outcome="complete",
            target_stage_id="lad_manager",
            target_node=_node_id("manager"),
            payload_projection={
                "kind": "object",
                "fields": {
                    "planning_result": {
                        "kind": "source",
                        "path": ("artifact_payload",),
                    },
                    "source_request": {
                        "kind": "source",
                        "path": ("work_item_payload",),
                    },
                },
            },
        ),
        _recovery_route_action(
            action_id="planning.route_planner_blocked",
            stage_id="lad_planner",
            outcome="blocked",
            target_stage_id="lad_mechanic",
            target_node=_node_id("mechanic"),
        ),
        _recovery_route_action(
            action_id="planning.escalate_planner_blocked_exhausted",
            stage_id="lad_planner",
            outcome="blocked_threshold_recovery",
            target_stage_id="lad_mechanic",
            target_node=_node_id("mechanic"),
        ),
        _close_action(
            action_id="planning.close_manager_complete",
            stage_id="lad_manager",
            outcome="complete",
            artifact_schema_id=_TASK_CARDS_SCHEMA_ID,
            kind="complete_work_item",
        ),
        _recovery_route_action(
            action_id="planning.route_manager_blocked",
            stage_id="lad_manager",
            outcome="blocked",
            target_stage_id="lad_mechanic",
            target_node=_node_id("mechanic"),
        ),
        _recovery_route_action(
            action_id="planning.escalate_manager_blocked_exhausted",
            stage_id="lad_manager",
            outcome="blocked_threshold_recovery",
            target_stage_id="lad_mechanic",
            target_node=_node_id("mechanic"),
        ),
        _route_action(
            action_id="planning.route_mechanic_complete",
            stage_id="lad_mechanic",
            outcome="complete",
            target_stage_id="lad_planner",
            target_node=_node_id("planner"),
            dynamic_target_selector=_dynamic_route_selector(
                field_names=("resume_stage",),
                disallowed_nodes=("mechanic",),
                allowed_nodes=("planner", "manager", "auditor"),
            ),
        ),
        _recovery_route_action(
            action_id="planning.route_mechanic_blocked",
            stage_id="lad_mechanic",
            outcome="blocked",
            target_stage_id="lad_mechanic",
            target_node=_node_id("mechanic"),
        ),
        _recovery_route_action(
            action_id="planning.escalate_mechanic_blocked_exhausted",
            stage_id="lad_mechanic",
            outcome="blocked_threshold_recovery",
            target_stage_id="lad_mechanic",
            target_node=_node_id("mechanic"),
        ),
        _recovery_context_action(
            action_id="planning.return_mechanic_recovered",
            stage_id="lad_mechanic",
            outcome="recovered",
            kind="return_to_recorded_source",
            artifact_schema_id=_REPORT_SCHEMA_ID,
        ),
        _recovery_context_action(
            action_id="planning.quarantine_mechanic_blocked",
            stage_id="lad_mechanic",
            outcome="quarantine",
            kind="quarantine_lineage",
            artifact_schema_id=_REPORT_SCHEMA_ID,
        ),
        _route_action(
            action_id="planning.route_auditor_complete",
            stage_id="lad_auditor",
            outcome="complete",
            target_stage_id="lad_planner",
            target_node=_node_id("planner"),
        ),
        _recovery_route_action(
            action_id="planning.route_auditor_blocked",
            stage_id="lad_auditor",
            outcome="blocked",
            target_stage_id="lad_mechanic",
            target_node=_node_id("mechanic"),
        ),
        _recovery_route_action(
            action_id="planning.escalate_auditor_blocked_exhausted",
            stage_id="lad_auditor",
            outcome="blocked_threshold_recovery",
            target_stage_id="lad_mechanic",
            target_node=_node_id("mechanic"),
        ),
        _close_action(
            action_id="planning.close_arbiter_complete",
            stage_id="lad_arbiter",
            outcome="complete",
            artifact_schema_id=_VERDICT_SCHEMA_ID,
            kind="complete_work_item",
        ),
        _close_action(
            action_id="planning.closure_gap",
            stage_id="lad_arbiter",
            outcome="remediation_needed",
            artifact_schema_id=_INCIDENT_REPORT_SCHEMA_ID,
            kind="closure_gap",
        ),
        _close_action(
            action_id="planning.close_arbiter_blocked",
            stage_id="lad_arbiter",
            outcome="blocked",
            artifact_schema_id=_REPORT_SCHEMA_ID,
            kind="block_work_item",
        ),
    )


def _queue_family(
    queue_id: str,
    *,
    external_enqueue: bool = False,
    display_name: str,
) -> dict[str, object]:
    return {
        "id": queue_id,
        "external_enqueue": external_enqueue,
        "presentation": {"display_name": display_name},
    }


def _artifact_schemas() -> list[dict[str, object]]:
    return [
        {
            "id": _STAGE_RESULT_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_STAGE_RESULT_SCHEMA_ID),
            "presentation": {"display_name": "Stage result"},
        },
        {
            "id": _RECON_PACKET_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_RECON_PACKET_SCHEMA_ID),
            "presentation": {"display_name": "Recon packet"},
        },
        {
            "id": _GENERATED_TASK_SCHEMA_ID,
            "schema": _generated_task_schema(),
            "presentation": {"display_name": "Generated task"},
        },
        {
            "id": _GENERATED_SPEC_SCHEMA_ID,
            "schema": _generated_spec_schema(),
            "presentation": {"display_name": "Generated spec"},
        },
        {
            "id": _PLANNER_DISPOSITION_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_PLANNER_DISPOSITION_SCHEMA_ID),
            "presentation": {"display_name": "Planner disposition"},
        },
        {
            "id": _SPEC_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_SPEC_SCHEMA_ID),
            "presentation": {"display_name": "Spec"},
        },
        {
            "id": _INCIDENT_REPORT_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_INCIDENT_REPORT_SCHEMA_ID),
            "presentation": {"display_name": "Incident report"},
        },
        {
            "id": _TASK_CARDS_SCHEMA_ID,
            "schema": _task_cards_schema(),
            "presentation": {"display_name": "Task cards"},
        },
        {
            "id": _REPORT_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_REPORT_SCHEMA_ID),
            "presentation": {"display_name": "Report"},
        },
        {
            "id": _RUBRIC_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_RUBRIC_SCHEMA_ID),
            "presentation": {"display_name": "Rubric"},
        },
        {
            "id": _VERDICT_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_VERDICT_SCHEMA_ID),
            "presentation": {"display_name": "Verdict"},
        },
        {
            "id": "planning.intake.spec",
            "schema": _intake_schema(
                root_kind="spec",
                accepted_root_kinds=("idea", "spec"),
            ),
            "presentation": {"display_name": "Spec intake"},
        },
        {
            "id": "planning.intake.probe",
            "schema": _intake_schema(root_kind="probe"),
            "presentation": {"display_name": "Probe intake"},
        },
        {
            "id": "planning.intake.incident",
            "schema": _intake_schema(root_kind="incident"),
            "presentation": {"display_name": "Incident intake"},
        },
    ]


def _blocked_recovery_sources() -> tuple[tuple[str, str], ...]:
    return (
        ("planner", "lad_planner"),
        ("manager", "lad_manager"),
        ("mechanic", "lad_mechanic"),
        ("auditor", "lad_auditor"),
    )


def _recovery_policies() -> tuple[dict[str, object], ...]:
    blocked_actions = tuple(
        f"planning.route_{node}_blocked"
        for node, _stage_id in _blocked_recovery_sources()
    )
    return (
        {
            "id": "planning.blocked.recovery",
            "source_recovery_action_ids": blocked_actions,
            "return_action_ids": ("planning.return_mechanic_recovered",),
            "quarantine_action_ids": ("planning.quarantine_mechanic_blocked",),
            "recovery_stage_kind_id": "lad_mechanic",
            "recorded_source_selector": "latest_recovery_attempt_for_lineage",
            "attempt_scope": "lineage",
            "immediate_recovery_limit": 1,
            "cooldown_starts_at_attempt": 2,
            "quarantine_threshold_attempt": 2,
            "threshold_behavior": "runtime_quarantine_at_threshold",
            "return_allowed_phases": ("active_recovery", "quarantine_eligible"),
            "reset_trigger_action_ids": (
                "planning.route_planner_complete",
                "planning.close_manager_complete",
                "planning.route_auditor_complete",
            ),
            "default_cooldown_seconds": 900,
            "cooldown_wait_state_id": "planning.blocked.recovery.cooldown",
        },
    )


def _wait_states() -> tuple[dict[str, object], ...]:
    return (
        {
            "id": "planning.blocked.recovery.cooldown",
            "kind": "timer",
            "policy_id": "planning.blocked.recovery",
            "starts_at_attempt": 2,
            "duration_seconds": 900,
        },
    )


def _counter(
    *,
    counter_id: str,
    stage_id: str,
    increment_action_id: str,
    threshold_action_id: str,
) -> dict[str, object]:
    return {
        "id": counter_id,
        "kind": "lineage_terminal_action_counter",
        "scope": "lineage",
        "stage_kind_id": stage_id,
        "increment_action_id": increment_action_id,
        "threshold_action_id": threshold_action_id,
        "threshold_count": 2,
    }


def _counters() -> tuple[dict[str, object], ...]:
    return tuple(
        _counter(
            counter_id=f"planning.mechanic_attempt_count.{node}",
            stage_id=stage_id,
            increment_action_id=f"planning.route_{node}_blocked",
            threshold_action_id=f"planning.escalate_{node}_blocked_exhausted",
        )
        for node, stage_id in _blocked_recovery_sources()
    )


def _intervention_option(
    *,
    option_id: str,
    kind: str,
) -> dict[str, object]:
    base: dict[str, object] = {
        "id": option_id,
        "policy_id": "planning.blocked.recovery",
        "kind": kind,
        "legal_source_state": "active_lineage_quarantine",
        "target_selector": "selected_quarantine_or_active_quarantine_by_lineage",
        "supersede_behavior": "supersede_quarantine",
        "attempt_effect": "resolve_attempt",
        "actor_kind": "local_operator",
    }
    if kind == "resume_lineage":
        return {
            **base,
            "resume_target_selector": "recorded_source",
            "close_behavior": None,
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
        }
    if kind == "close_lineage":
        return {
            **base,
            "resume_target_selector": None,
            "close_behavior": "close_ready_or_active_work_in_lineage",
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
        }
    return {
        **base,
        "resume_target_selector": None,
        "close_behavior": None,
        "payload_schema_id": "planning.intake.spec",
        "target_queue_family_id": "spec",
        "target_stage_kind_id": "lad_planner",
        "target_graph_node_id": _node_id("planner"),
        "target_runner_binding_id": _RUNNER_ID,
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
    }


def _intervention_options() -> tuple[dict[str, object], ...]:
    return (
        _intervention_option(
            option_id="planning.blocked.resume_lineage",
            kind="resume_lineage",
        ),
        _intervention_option(
            option_id="planning.blocked.close_lineage",
            kind="close_lineage",
        ),
        _intervention_option(
            option_id="planning.blocked.revise_lineage",
            kind="revise_lineage",
        ),
    )


def _completion_behaviors() -> tuple[dict[str, object], ...]:
    return (
        {
            "id": "planning.closure.completion",
            "trigger": "backlog_drained",
            "readiness_rule": "no_open_lineage_work",
            "request_kind": "closure_target",
            "target_selector": "active_closure_target",
            "target_stage_kind_id": "lad_arbiter",
            "target_graph_node_id": _node_id("arbiter"),
            "runner_binding_id": _RUNNER_ID,
            "request_queue_family_id": "stage_result",
            "pass_action_id": "planning.close_arbiter_complete",
            "gap_action_id": "planning.closure_gap",
            "blocked_action_id": "planning.close_arbiter_blocked",
            "verdict_artifact_schema_id": _VERDICT_SCHEMA_ID,
            "remediation_policy_id": "planning.closure.remediation",
            "accepted_root_source_kinds": (
                "idea",
                "probe",
                "manual",
                "spec",
                "incident",
            ),
            "root_source_resolution": "runtime_inventory",
            "evidence_window_policy": "lineage",
            "rubric_policy": "reuse_or_create",
            "blocked_work_policy": "suppress",
            "skip_if_closed": True,
            "presentation": {"display_name": "LAD closure completion"},
        },
    )


def _remediation_policies() -> tuple[dict[str, object], ...]:
    return (
        {
            "id": "planning.closure.remediation",
            "source_action_id": "planning.closure_gap",
            "target_queue_family_id": "incident",
            "target_stage_kind_id": "lad_auditor",
            "target_graph_node_id": _node_id("auditor"),
            "target_runner_binding_id": _RUNNER_ID,
            "payload_schema_id": "planning.intake.incident",
            "guidance_source": "source_artifact",
            "dedupe_key": "closure_target_and_source_artifact",
            "duplicate_policy": "refuse",
            "suppression_policy": "suppress_repeated_same_evidence",
            "root_source_kind": "incident",
            "presentation": {"display_name": "LAD closure remediation"},
        },
    )


def _assets() -> tuple[dict[str, object], ...]:
    assets: list[dict[str, object]] = []
    for stage_id, display_name in (
        ("recon", "LAD Recon"),
        ("lad_planner", "LAD Planner"),
        ("lad_manager", "LAD Manager"),
        ("lad_mechanic", "LAD Mechanic"),
        ("lad_auditor", "LAD Auditor"),
        ("lad_arbiter", "LAD Arbiter"),
    ):
        assets.append(
            _asset(
                stage_id=stage_id,
                kind="prompt",
                body=f"Execute the selected {display_name} stage contract.",
                display_name=f"{display_name} entrypoint",
            )
        )
        assets.append(
            _asset(
                stage_id=stage_id,
                kind="skill",
                body=f"Apply {display_name} planning discipline.",
                display_name=f"{display_name} core skill",
            )
        )
    return tuple(assets)


def _base_source() -> dict[str, object]:
    execution_source = lad_execution.workflow_source()
    stages = _stage_definitions()
    execution_graphs = _section(execution_source, "graphs")
    execution_partitions = _section(execution_source, "partitions")
    execution_queue_families = _section(execution_source, "queue_families")
    execution_external_routes = _section(execution_source, "external_enqueue_routes")
    execution_artifact_schemas = _section(execution_source, "artifact_schemas")
    execution_assets = _section(execution_source, "assets")
    execution_outcomes = _section(execution_source, "terminal_outcomes")
    execution_stages = _section(execution_source, "stage_kinds")
    execution_actions = _section(execution_source, "terminal_actions")
    execution_recovery_policies = _section(execution_source, "recovery_policies")
    execution_wait_states = _section(execution_source, "wait_states")
    execution_counters = _section(execution_source, "counters")
    execution_interventions = _section(execution_source, "intervention_options")
    execution_runner_bindings = _section(execution_source, "runner_bindings")
    execution_capabilities = _section(execution_source, "capabilities")
    return {
        "lineage_policy": "root_from_external_enqueue",
        "workflow": {
            "id": "planning.lad",
            "version": "0.1",
            "name": "LAD Planning",
            "compatibility_profile": None,
            "required_extensions": (),
        },
        "graphs": [
            {
                "id": "planning.lad.graph",
                "node_ids": (
                    _node_id("recon"),
                    _node_id("planner"),
                    _node_id("manager"),
                    _node_id("mechanic"),
                    _node_id("auditor"),
                    _node_id("arbiter"),
                ),
                "presentation": {"display_name": "LAD Planning Graph"},
            },
            *execution_graphs,
        ],
        "partitions": _merge_by_id(
            (
                {
                    "id": "planning",
                    "kind": "plane",
                    "presentation": {"display_name": "Planning"},
                },
            ),
            execution_partitions,
        ),
        "queue_families": _merge_by_id(
            (
                _queue_family("spec", external_enqueue=True, display_name="Spec"),
                _queue_family("probe", external_enqueue=True, display_name="Probe"),
                _queue_family(
                    "incident",
                    external_enqueue=True,
                    display_name="Incident",
                ),
                _queue_family("stage_result", display_name="Stage result"),
                _queue_family("recon_packet", display_name="Recon packet"),
                _queue_family("generated_task", display_name="Generated task"),
                _queue_family("generated_spec", display_name="Generated spec"),
                _queue_family(
                    "planner_disposition",
                    display_name="Planner disposition",
                ),
                _queue_family("task_cards", display_name="Task cards"),
                _queue_family("incident_report", display_name="Incident report"),
                _queue_family("report", display_name="Report"),
                _queue_family("rubric", display_name="Rubric"),
                _queue_family("verdict", display_name="Verdict"),
            ),
            execution_queue_families,
        ),
        "external_enqueue_routes": [
            {
                "id": "spec",
                "queue_family_id": "spec",
                "graph_node_id": _node_id("planner"),
                "stage_kind_id": "lad_planner",
                "runner_binding_id": _RUNNER_ID,
                "payload_schema_id": "planning.intake.spec",
            },
            {
                "id": "probe",
                "queue_family_id": "probe",
                "graph_node_id": _node_id("recon"),
                "stage_kind_id": "recon",
                "runner_binding_id": _RUNNER_ID,
                "payload_schema_id": "planning.intake.probe",
            },
            {
                "id": "incident",
                "queue_family_id": "incident",
                "graph_node_id": _node_id("auditor"),
                "stage_kind_id": "lad_auditor",
                "runner_binding_id": _RUNNER_ID,
                "payload_schema_id": "planning.intake.incident",
            },
            *execution_external_routes,
        ],
        "artifact_schemas": _merge_by_id(
            tuple(_artifact_schemas()),
            execution_artifact_schemas,
        ),
        "assets": _merge_by_id(_assets(), execution_assets),
        "terminal_outcomes": (*_outcomes(), *execution_outcomes),
        "stage_kinds": (*stages, *execution_stages),
        "terminal_actions": (*_actions(), *execution_actions),
        "recovery_policies": (*_recovery_policies(), *execution_recovery_policies),
        "wait_states": (*_wait_states(), *execution_wait_states),
        "counters": (*_counters(), *execution_counters),
        "completion_behaviors": _completion_behaviors(),
        "remediation_policies": _remediation_policies(),
        "fanout_declarations": [
            {
                "id": "planning.manager.task_cards_to_execution",
                "source_action_id": "planning.close_manager_complete",
                "source_artifact_schema_id": _TASK_CARDS_SCHEMA_ID,
                "item_source_path": ("cards",),
                "item_id_key": "task_card_id",
                "target_route_id": "execution.lad.task",
                "target_payload_schema_id": _EXECUTION_TASK_SCHEMA_ID,
                "target_payload_mapping": {
                    "task_id": ("task_card_id",),
                    "body": ("body",),
                },
                "duplicate_policy": "refuse",
                "root_lineage_policy": "inherit_source_lineage",
                "dependency_policy": "depends_on_source_work_item",
            }
        ],
        "intervention_options": (*_intervention_options(), *execution_interventions),
        "runner_bindings": [
            {
                "id": _RUNNER_ID,
                "adapter_kind": "fake_local",
                "stage_kind_ids": tuple(str(stage["id"]) for stage in stages),
                "required_capability_ids": (_RUNNER_INVOKE_CAPABILITY_ID,),
                "presentation": {"display_name": "Local LAD Planning runner"},
            },
            *execution_runner_bindings,
        ],
        "capabilities": _merge_by_id(execution_capabilities),
    }


def workflow_source() -> dict[str, object]:
    return deepcopy(_base_source())


def workflow_source_with_unselected_catalog() -> dict[str, object]:
    source = workflow_source()
    source["unselected_catalog"] = (
        {
            "id": "execution.unselected",
            "kind": "stage_catalog_entry",
            "catalog_payload": {"stage_kind_id": "execution.lad_builder"},
        },
        {
            "id": "learning.unselected",
            "kind": "stage_catalog_entry",
            "catalog_payload": {"stage_kind_id": "learning.lad_librarian"},
        },
    )
    return source


__all__ = ("workflow_source", "workflow_source_with_unselected_catalog")
