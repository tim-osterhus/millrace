"""Hosted LAD execution workflow fixtures.

LAD appears here as selected workflow data. Compiler, kernel, and testing
helpers must treat these identifiers as opaque authority values.
"""

from __future__ import annotations

from copy import deepcopy

_RUNNER_ID = "execution.lad.local_runner"
_RUNNER_INVOKE_CAPABILITY_ID = "capability.runner.invoke"

_STAGE_RESULT_SCHEMA_ID = "execution.artifacts.stage_result"
_TASK_SCHEMA_ID = "execution.artifacts.task"
_REPORT_SCHEMA_ID = "execution.artifacts.report"
_INTEGRATION_REPORT_SCHEMA_ID = "execution.artifacts.integration_report"
_INCIDENT_REPORT_SCHEMA_ID = "execution.artifacts.incident_report"
_BUILDER_SUMMARY_SCHEMA_ID = "execution.artifacts.builder_summary"


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


def _task_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ("task_id", "body"),
        "properties": {
            "task_id": _required_string_schema(),
            "body": _required_string_schema(),
        },
    }


def _asset(
    *,
    node: str,
    kind: str,
    body: str,
    display_name: str,
) -> dict[str, object]:
    path = (
        f"entrypoints/execution/{node}.md"
        if kind == "prompt"
        else (
            "skills/stage/execution/"
            f"{node.removesuffix('_core').replace('_', '-')}-core/SKILL.md"
        )
    )
    namespace = "entrypoints" if kind == "prompt" else "skills"
    return {
        "id": f"execution.{namespace}.{node}",
        "kind": kind,
        "body": body,
        "presentation": {
            "display_name": display_name,
            "details": {"path": path},
        },
    }


def _entrypoint_asset_id(stage_id: str) -> str:
    return f"execution.entrypoints.{stage_id}"


def _skill_asset_id(stage_id: str) -> str:
    return f"execution.skills.{stage_id.removeprefix('lad_')}_core"


def _stage_kind_id(stage_id: str) -> str:
    return stage_id


def _outcome_id(stage_id: str, outcome: str) -> str:
    return f"execution.{stage_id}.{outcome}"


def _node_id(workflow_id: str, node: str) -> str:
    return f"{workflow_id}.{node}.start"


def _graph_node_ids(workflow_id: str, include_integrator: bool) -> tuple[str, ...]:
    nodes = ["builder"]
    if include_integrator:
        nodes.append("integrator")
    nodes.extend(
        [
            "checker",
            "fixer",
            "doublechecker",
            "updater",
            "troubleshooter",
            "consultant",
        ]
    )
    return tuple(_node_id(workflow_id, node) for node in nodes)


def _stage(
    *,
    node: str,
    stage_id: str,
    graph_order: int,
    input_queue_family_ids: tuple[str, ...],
    output_queue_family_ids: tuple[str, ...],
    artifact_schema_ids: tuple[str, ...],
    outcomes: tuple[str, ...],
    display_name: str,
) -> dict[str, object]:
    return {
        "id": _stage_kind_id(stage_id),
        "partition_id": "execution",
        "runner_binding_id": _RUNNER_ID,
        "input_queue_family_ids": input_queue_family_ids,
        "output_queue_family_ids": output_queue_family_ids,
        "artifact_schema_ids": artifact_schema_ids,
        "asset_ids": (_entrypoint_asset_id(stage_id), _skill_asset_id(stage_id)),
        "declared_outcome_ids": tuple(_outcome_id(stage_id, item) for item in outcomes),
        "presentation": {
            "display_name": display_name,
            "details": {
                "node_id": node,
                "graph_order": graph_order,
            },
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
        "stage_kind_id": _stage_kind_id(stage_id),
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
    emitted_queue_family_id: str,
    artifact_schema_id: str = _STAGE_RESULT_SCHEMA_ID,
    dynamic_target_selector: dict[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "id": action_id,
        "stage_kind_id": _stage_kind_id(stage_id),
        "outcome_id": _outcome_id(stage_id, outcome),
        "kind": "route",
        "target_stage_kind_id": _stage_kind_id(target_stage_id),
        "target_graph_node_id": target_node,
        "emitted_queue_family_id": emitted_queue_family_id,
        "artifact_schema_id": artifact_schema_id,
        "runner_binding_id": _RUNNER_ID,
        "asset_ids": (
            _entrypoint_asset_id(target_stage_id),
            _skill_asset_id(target_stage_id),
        ),
        "payload_projection": {"kind": "source", "path": ("artifact_payload",)},
        "presentation": {"display_name": action_id},
    }
    if dynamic_target_selector is not None:
        record["dynamic_target_selector"] = dynamic_target_selector
    return record


def _dynamic_route_selector(
    *,
    workflow_id: str,
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
                "target_stage_kind_id": _stage_kind_id(_stage_id_for_node(node)),
                "target_graph_node_id": _node_id(workflow_id, node),
                "emitted_queue_family_id": "stage_result",
                "runner_binding_id": _RUNNER_ID,
            }
            for node in allowed_nodes
        },
    }


def _stage_id_for_node(node: str) -> str:
    return f"lad_{node}"


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
        "stage_kind_id": _stage_kind_id(stage_id),
        "outcome_id": _outcome_id(stage_id, outcome),
        "kind": "recovery_route",
        "target_stage_kind_id": _stage_kind_id(target_stage_id),
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
        "stage_kind_id": _stage_kind_id(stage_id),
        "outcome_id": _outcome_id(stage_id, outcome),
        "kind": kind,
        "artifact_schema_id": artifact_schema_id,
        "presentation": {"display_name": action_id},
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
        "stage_kind_id": _stage_kind_id(stage_id),
        "outcome_id": _outcome_id(stage_id, outcome),
        "kind": kind,
        "artifact_schema_id": artifact_schema_id,
        "presentation": {"display_name": action_id},
    }


def _stage_definitions(include_integrator: bool) -> tuple[dict[str, object], ...]:
    common_result_artifacts = (_STAGE_RESULT_SCHEMA_ID, _REPORT_SCHEMA_ID)
    runtime_failure_outcomes = ("runtime_failed", "runtime_failed_exhausted")
    builder_artifacts = (
        (_TASK_SCHEMA_ID, *common_result_artifacts, _BUILDER_SUMMARY_SCHEMA_ID)
        if include_integrator
        else (_TASK_SCHEMA_ID, *common_result_artifacts)
    )
    builder_outputs = (
        ("stage_result", "builder_summary")
        if include_integrator
        else ("stage_result",)
    )
    checker_artifacts = (
        (*common_result_artifacts, _INTEGRATION_REPORT_SCHEMA_ID)
        if include_integrator
        else common_result_artifacts
    )
    stages = [
        _stage(
            node="builder",
            stage_id="lad_builder",
            graph_order=10,
            input_queue_family_ids=("task", "stage_result"),
            output_queue_family_ids=builder_outputs,
            artifact_schema_ids=builder_artifacts,
            outcomes=(
                "complete",
                "blocked",
                *runtime_failure_outcomes,
            ),
            display_name="LAD Builder",
        ),
    ]
    if include_integrator:
        stages.append(
            _stage(
                node="integrator",
                stage_id="lad_integrator",
                graph_order=20,
                input_queue_family_ids=("builder_summary", "stage_result"),
                output_queue_family_ids=("stage_result",),
                artifact_schema_ids=(
                    _STAGE_RESULT_SCHEMA_ID,
                    _REPORT_SCHEMA_ID,
                    _INTEGRATION_REPORT_SCHEMA_ID,
                    _BUILDER_SUMMARY_SCHEMA_ID,
                ),
                outcomes=(
                    "complete",
                    "blocked",
                    *runtime_failure_outcomes,
                ),
                display_name="LAD Integrator",
            )
        )
    stages.extend(
        [
            _stage(
                node="checker",
                stage_id="lad_checker",
                graph_order=30,
                input_queue_family_ids=("stage_result",),
                output_queue_family_ids=("stage_result",),
                artifact_schema_ids=checker_artifacts,
                outcomes=(
                    "pass",
                    "fix_needed",
                    "blocked",
                    *runtime_failure_outcomes,
                ),
                display_name="LAD Checker",
            ),
            _stage(
                node="fixer",
                stage_id="lad_fixer",
                graph_order=40,
                input_queue_family_ids=("stage_result",),
                output_queue_family_ids=("stage_result",),
                artifact_schema_ids=common_result_artifacts,
                outcomes=(
                    "complete",
                    "blocked",
                    *runtime_failure_outcomes,
                ),
                display_name="LAD Fixer",
            ),
            _stage(
                node="doublechecker",
                stage_id="lad_doublechecker",
                graph_order=50,
                input_queue_family_ids=("stage_result",),
                output_queue_family_ids=("stage_result",),
                artifact_schema_ids=common_result_artifacts,
                outcomes=(
                    "pass",
                    "fix_needed",
                    "blocked",
                    *runtime_failure_outcomes,
                ),
                display_name="LAD Doublechecker",
            ),
            _stage(
                node="updater",
                stage_id="lad_updater",
                graph_order=60,
                input_queue_family_ids=("stage_result",),
                output_queue_family_ids=("stage_result",),
                artifact_schema_ids=common_result_artifacts,
                outcomes=(
                    "complete",
                    "blocked",
                    *runtime_failure_outcomes,
                ),
                display_name="LAD Updater",
            ),
            _stage(
                node="troubleshooter",
                stage_id="lad_troubleshooter",
                graph_order=70,
                input_queue_family_ids=("stage_result",),
                output_queue_family_ids=("stage_result",),
                artifact_schema_ids=common_result_artifacts,
                outcomes=(
                    "complete",
                    "blocked",
                    "recovered",
                    "recovery_blocked",
                    *runtime_failure_outcomes,
                ),
                display_name="LAD Troubleshooter",
            ),
            _stage(
                node="consultant",
                stage_id="lad_consultant",
                graph_order=80,
                input_queue_family_ids=("stage_result",),
                output_queue_family_ids=("stage_result",),
                artifact_schema_ids=(
                    _STAGE_RESULT_SCHEMA_ID,
                    _REPORT_SCHEMA_ID,
                    _INCIDENT_REPORT_SCHEMA_ID,
                ),
                outcomes=(
                    "complete",
                    "needs_plan",
                    "blocked",
                    "recovered",
                    "quarantine",
                ),
                display_name="LAD Consultant",
            ),
        ]
    )
    return tuple(stages)


def _outcomes(include_integrator: bool) -> tuple[dict[str, object], ...]:
    outcomes = [
        _outcome(
            stage_id="lad_builder",
            outcome="complete",
            marker="BUILDER_COMPLETE",
            display_name="Builder complete",
        ),
        _outcome(
            stage_id="lad_builder",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Builder blocked",
        ),
        _outcome(
            stage_id="lad_checker",
            outcome="pass",
            marker="CHECKER_PASS",
            display_name="Checker pass",
        ),
        _outcome(
            stage_id="lad_checker",
            outcome="fix_needed",
            marker="FIX_NEEDED",
            display_name="Fix needed",
        ),
        _outcome(
            stage_id="lad_checker",
            outcome="fix_threshold_recovery",
            marker="",
            display_name="Checker fix threshold recovery",
        ),
        _outcome(
            stage_id="lad_checker",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Checker blocked",
        ),
        _outcome(
            stage_id="lad_fixer",
            outcome="complete",
            marker="FIXER_COMPLETE",
            display_name="Fixer complete",
        ),
        _outcome(
            stage_id="lad_fixer",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Fixer blocked",
        ),
        _outcome(
            stage_id="lad_doublechecker",
            outcome="pass",
            marker="DOUBLECHECK_PASS",
            display_name="Doublecheck pass",
        ),
        _outcome(
            stage_id="lad_doublechecker",
            outcome="fix_needed",
            marker="FIX_NEEDED",
            display_name="Doublecheck fix needed",
        ),
        _outcome(
            stage_id="lad_doublechecker",
            outcome="fix_threshold_recovery",
            marker="",
            display_name="Doublechecker fix threshold recovery",
        ),
        _outcome(
            stage_id="lad_doublechecker",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Doublechecker blocked",
        ),
        _outcome(
            stage_id="lad_updater",
            outcome="complete",
            marker="UPDATE_COMPLETE",
            display_name="Update complete",
        ),
        _outcome(
            stage_id="lad_updater",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Updater blocked",
        ),
        _outcome(
            stage_id="lad_troubleshooter",
            outcome="complete",
            marker="TROUBLESHOOT_COMPLETE",
            display_name="Troubleshoot complete",
        ),
        _outcome(
            stage_id="lad_troubleshooter",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Troubleshooter blocked",
        ),
        _outcome(
            stage_id="lad_troubleshooter",
            outcome="recovered",
            marker="TROUBLESHOOT_RECOVERED",
            display_name="Troubleshooter recovered source",
        ),
        _outcome(
            stage_id="lad_troubleshooter",
            outcome="recovery_blocked",
            marker="TROUBLESHOOT_QUARANTINE",
            display_name="Troubleshooter recovery blocked",
        ),
        _outcome(
            stage_id="lad_consultant",
            outcome="complete",
            marker="CONSULT_COMPLETE",
            display_name="Consult complete",
        ),
        _outcome(
            stage_id="lad_consultant",
            outcome="needs_plan",
            marker="NEEDS_PLANNING",
            display_name="Needs plan",
        ),
        _outcome(
            stage_id="lad_consultant",
            outcome="blocked",
            marker="BLOCKED",
            display_name="Consultant blocked",
        ),
        _outcome(
            stage_id="lad_consultant",
            outcome="recovered",
            marker="CONSULT_RECOVERED",
            display_name="Consultant recovered source",
        ),
        _outcome(
            stage_id="lad_consultant",
            outcome="quarantine",
            marker="CONSULT_QUARANTINE",
            display_name="Consultant quarantine",
        ),
    ]
    if include_integrator:
        outcomes.extend(
            [
                _outcome(
                    stage_id="lad_integrator",
                    outcome="complete",
                    marker="INTEGRATION_COMPLETE",
                    display_name="Integration complete",
                ),
                _outcome(
                    stage_id="lad_integrator",
                    outcome="blocked",
                    marker="BLOCKED",
                    display_name="Integrator blocked",
                ),
            ]
        )
    for node, stage_id in _blocked_recovery_sources(include_integrator):
        outcomes.extend(
            [
                _outcome(
                    stage_id=stage_id,
                    outcome="blocked_threshold_recovery",
                    marker="",
                    display_name=f"{node.title()} blocked threshold recovery",
                ),
                _outcome(
                    stage_id=stage_id,
                    outcome="runtime_failed",
                    marker="RUNTIME_FAILURE",
                    display_name=f"{node.title()} runtime failure",
                ),
                _outcome(
                    stage_id=stage_id,
                    outcome="runtime_failed_exhausted",
                    marker="RUNTIME_FAILURE_ESCALATE",
                    display_name=f"{node.title()} runtime failure escalation",
                ),
            ]
        )
    return tuple(outcomes)


def _actions(
    workflow_id: str,
    include_integrator: bool,
) -> tuple[dict[str, object], ...]:
    first_success_target = "lad_integrator" if include_integrator else "lad_checker"
    first_success_node = "integrator" if include_integrator else "checker"
    first_success_queue = "builder_summary" if include_integrator else "stage_result"
    resumable_nodes = (
        (
            "builder",
            "integrator",
            "checker",
            "fixer",
            "doublechecker",
            "updater",
        )
        if include_integrator
        else (
            "builder",
            "checker",
            "fixer",
            "doublechecker",
            "updater",
        )
    )
    consultant_target_nodes = (
        (*resumable_nodes, "troubleshooter")
        if include_integrator
        else (
            "builder",
            "checker",
            "fixer",
            "doublechecker",
            "updater",
            "troubleshooter",
        )
    )
    actions = [
        _route_action(
            action_id="execution.route_builder_complete",
            stage_id="lad_builder",
            outcome="complete",
            target_stage_id=first_success_target,
            target_node=_node_id(workflow_id, first_success_node),
            emitted_queue_family_id=first_success_queue,
            artifact_schema_id=(
                _BUILDER_SUMMARY_SCHEMA_ID
                if include_integrator
                else _STAGE_RESULT_SCHEMA_ID
            ),
        ),
        _route_action(
            action_id="execution.route_builder_blocked",
            stage_id="lad_builder",
            outcome="blocked",
            target_stage_id="lad_troubleshooter",
            target_node=_node_id(workflow_id, "troubleshooter"),
            emitted_queue_family_id="stage_result",
        ),
        _route_action(
            action_id="execution.route_checker_pass",
            stage_id="lad_checker",
            outcome="pass",
            target_stage_id="lad_updater",
            target_node=_node_id(workflow_id, "updater"),
            emitted_queue_family_id="stage_result",
        ),
        _route_action(
            action_id="execution.route_checker_fix_needed",
            stage_id="lad_checker",
            outcome="fix_needed",
            target_stage_id="lad_fixer",
            target_node=_node_id(workflow_id, "fixer"),
            emitted_queue_family_id="stage_result",
        ),
        _route_action(
            action_id="execution.route_checker_blocked",
            stage_id="lad_checker",
            outcome="blocked",
            target_stage_id="lad_troubleshooter",
            target_node=_node_id(workflow_id, "troubleshooter"),
            emitted_queue_family_id="stage_result",
        ),
        _route_action(
            action_id="execution.route_fixer_complete",
            stage_id="lad_fixer",
            outcome="complete",
            target_stage_id="lad_doublechecker",
            target_node=_node_id(workflow_id, "doublechecker"),
            emitted_queue_family_id="stage_result",
        ),
        _route_action(
            action_id="execution.route_fixer_blocked",
            stage_id="lad_fixer",
            outcome="blocked",
            target_stage_id="lad_troubleshooter",
            target_node=_node_id(workflow_id, "troubleshooter"),
            emitted_queue_family_id="stage_result",
        ),
        _route_action(
            action_id="execution.route_doublechecker_pass",
            stage_id="lad_doublechecker",
            outcome="pass",
            target_stage_id="lad_updater",
            target_node=_node_id(workflow_id, "updater"),
            emitted_queue_family_id="stage_result",
        ),
        _route_action(
            action_id="execution.route_doublechecker_fix_needed",
            stage_id="lad_doublechecker",
            outcome="fix_needed",
            target_stage_id="lad_fixer",
            target_node=_node_id(workflow_id, "fixer"),
            emitted_queue_family_id="stage_result",
        ),
        _route_action(
            action_id="execution.route_doublechecker_blocked",
            stage_id="lad_doublechecker",
            outcome="blocked",
            target_stage_id="lad_troubleshooter",
            target_node=_node_id(workflow_id, "troubleshooter"),
            emitted_queue_family_id="stage_result",
        ),
        _close_action(
            action_id="execution.close_updater_complete",
            stage_id="lad_updater",
            outcome="complete",
            artifact_schema_id=_REPORT_SCHEMA_ID,
            kind="complete_work_item",
        ),
        _route_action(
            action_id="execution.route_updater_blocked",
            stage_id="lad_updater",
            outcome="blocked",
            target_stage_id="lad_troubleshooter",
            target_node=_node_id(workflow_id, "troubleshooter"),
            emitted_queue_family_id="stage_result",
        ),
        _route_action(
            action_id="execution.return_troubleshooter_complete",
            stage_id="lad_troubleshooter",
            outcome="complete",
            target_stage_id="lad_builder",
            target_node=_node_id(workflow_id, "builder"),
            emitted_queue_family_id="stage_result",
            artifact_schema_id=_REPORT_SCHEMA_ID,
            dynamic_target_selector=_dynamic_route_selector(
                workflow_id=workflow_id,
                field_names=("resume_stage",),
                disallowed_nodes=("consultant",),
                allowed_nodes=resumable_nodes,
            ),
        ),
        _route_action(
            action_id="execution.route_troubleshooter_blocked",
            stage_id="lad_troubleshooter",
            outcome="blocked",
            target_stage_id="lad_consultant",
            target_node=_node_id(workflow_id, "consultant"),
            emitted_queue_family_id="stage_result",
        ),
        _route_action(
            action_id="execution.route_consultant_complete",
            stage_id="lad_consultant",
            outcome="complete",
            target_stage_id="lad_troubleshooter",
            target_node=_node_id(workflow_id, "troubleshooter"),
            emitted_queue_family_id="stage_result",
            artifact_schema_id=_REPORT_SCHEMA_ID,
            dynamic_target_selector=_dynamic_route_selector(
                workflow_id=workflow_id,
                field_names=("target_stage", "resume_stage"),
                disallowed_nodes=("consultant",),
                allowed_nodes=consultant_target_nodes,
            ),
        ),
        _close_action(
            action_id="execution.close_consultant_needs_plan",
            stage_id="lad_consultant",
            outcome="needs_plan",
            artifact_schema_id=_INCIDENT_REPORT_SCHEMA_ID,
            kind="close_with_escalation",
        ),
        _close_action(
            action_id="execution.close_consultant_blocked",
            stage_id="lad_consultant",
            outcome="blocked",
            artifact_schema_id=_REPORT_SCHEMA_ID,
            kind="block_work_item",
        ),
    ]
    if include_integrator:
        actions.extend(
            [
                _route_action(
                    action_id="execution.route_integrator_complete",
                    stage_id="lad_integrator",
                    outcome="complete",
                    target_stage_id="lad_checker",
                    target_node=_node_id(workflow_id, "checker"),
                    emitted_queue_family_id="stage_result",
                    artifact_schema_id=_INTEGRATION_REPORT_SCHEMA_ID,
                ),
                _route_action(
                    action_id="execution.route_integrator_blocked",
                    stage_id="lad_integrator",
                    outcome="blocked",
                    target_stage_id="lad_troubleshooter",
                    target_node=_node_id(workflow_id, "troubleshooter"),
                    emitted_queue_family_id="stage_result",
                ),
            ]
        )
    actions.extend(_recovery_actions(workflow_id, include_integrator))
    return tuple(actions)


def _recovery_actions(
    workflow_id: str,
    include_integrator: bool,
) -> tuple[dict[str, object], ...]:
    blocked_sources = _blocked_recovery_sources(include_integrator)
    actions: list[dict[str, object]] = [
        _recovery_route_action(
            action_id="execution.escalate_checker_fix_exhausted",
            stage_id="lad_checker",
            outcome="fix_threshold_recovery",
            target_stage_id="lad_troubleshooter",
            target_node=_node_id(workflow_id, "troubleshooter"),
        ),
        _recovery_route_action(
            action_id="execution.escalate_doublechecker_fix_exhausted",
            stage_id="lad_doublechecker",
            outcome="fix_threshold_recovery",
            target_stage_id="lad_troubleshooter",
            target_node=_node_id(workflow_id, "troubleshooter"),
        ),
        _recovery_context_action(
            action_id="execution.return_troubleshooter_recovered",
            stage_id="lad_troubleshooter",
            outcome="recovered",
            kind="return_to_recorded_source",
            artifact_schema_id=_REPORT_SCHEMA_ID,
        ),
        _recovery_context_action(
            action_id="execution.quarantine_troubleshooter_blocked",
            stage_id="lad_troubleshooter",
            outcome="recovery_blocked",
            kind="quarantine_lineage",
            artifact_schema_id=_REPORT_SCHEMA_ID,
        ),
        _recovery_context_action(
            action_id="execution.return_consultant_recovered",
            stage_id="lad_consultant",
            outcome="recovered",
            kind="return_to_recorded_source",
            artifact_schema_id=_REPORT_SCHEMA_ID,
        ),
        _recovery_context_action(
            action_id="execution.quarantine_consultant_blocked",
            stage_id="lad_consultant",
            outcome="quarantine",
            kind="quarantine_lineage",
            artifact_schema_id=_REPORT_SCHEMA_ID,
        ),
    ]
    for node, stage_id in blocked_sources:
        actions.append(
            _recovery_route_action(
                action_id=f"execution.escalate_{node}_blocked_exhausted",
                stage_id=stage_id,
                outcome="blocked_threshold_recovery",
                target_stage_id="lad_consultant",
                target_node=_node_id(workflow_id, "consultant"),
            )
        )
        actions.append(
            _recovery_route_action(
                action_id=f"execution.recover_{node}_runtime_failure",
                stage_id=stage_id,
                outcome="runtime_failed",
                target_stage_id="lad_troubleshooter",
                target_node=_node_id(workflow_id, "troubleshooter"),
            )
        )
        actions.append(
            _close_action(
                action_id=f"execution.close_{node}_runtime_failure_exhausted",
                stage_id=stage_id,
                outcome="runtime_failed_exhausted",
                artifact_schema_id=_STAGE_RESULT_SCHEMA_ID,
                kind="block_work_item",
            )
        )
    return tuple(actions)


def _blocked_recovery_sources(include_integrator: bool) -> tuple[tuple[str, str], ...]:
    sources = [
        ("builder", "lad_builder"),
        ("checker", "lad_checker"),
        ("fixer", "lad_fixer"),
        ("doublechecker", "lad_doublechecker"),
        ("updater", "lad_updater"),
        ("troubleshooter", "lad_troubleshooter"),
    ]
    if include_integrator:
        sources.insert(1, ("integrator", "lad_integrator"))
    return tuple(sources)


def _recovery_policies(include_integrator: bool) -> tuple[dict[str, object], ...]:
    blocked_actions = tuple(
        f"execution.route_{node}_blocked"
        for node, _stage_id in _blocked_recovery_sources(include_integrator)
    )
    runtime_failure_actions = tuple(
        f"execution.recover_{node}_runtime_failure"
        for node, _stage_id in _blocked_recovery_sources(include_integrator)
    )
    return (
        {
            "id": "execution.fix_needed_recovery",
            "source_recovery_action_ids": (
                "execution.route_checker_fix_needed",
                "execution.route_doublechecker_fix_needed",
            ),
            "return_action_ids": ("execution.return_troubleshooter_recovered",),
            "quarantine_action_ids": (
                "execution.quarantine_troubleshooter_blocked",
            ),
            "recovery_stage_kind_id": "lad_troubleshooter",
            "recorded_source_selector": "latest_recovery_attempt_for_lineage",
            "attempt_scope": "lineage",
            "immediate_recovery_limit": 1,
            "cooldown_starts_at_attempt": 2,
            "quarantine_threshold_attempt": 3,
            "threshold_behavior": "runtime_quarantine_at_threshold",
            "return_allowed_phases": ("active_recovery", "quarantine_eligible"),
            "reset_trigger_action_ids": (
                "execution.route_checker_pass",
                "execution.route_fixer_complete",
                "execution.route_doublechecker_pass",
                "execution.close_updater_complete",
            ),
            "default_cooldown_seconds": 900,
            "cooldown_wait_state_id": "execution.fix_needed_recovery.cooldown",
        },
        {
            "id": "execution.blocked_recovery",
            "source_recovery_action_ids": blocked_actions,
            "return_action_ids": ("execution.return_consultant_recovered",),
            "quarantine_action_ids": ("execution.quarantine_consultant_blocked",),
            "recovery_stage_kind_id": "lad_consultant",
            "recorded_source_selector": "latest_recovery_attempt_for_lineage",
            "attempt_scope": "lineage",
            "immediate_recovery_limit": 1,
            "cooldown_starts_at_attempt": 2,
            "quarantine_threshold_attempt": 3,
            "threshold_behavior": "runtime_quarantine_at_threshold",
            "return_allowed_phases": ("active_recovery", "quarantine_eligible"),
            "reset_trigger_action_ids": (
                "execution.route_builder_complete",
                "execution.route_checker_pass",
                "execution.route_fixer_complete",
                "execution.route_doublechecker_pass",
                "execution.close_updater_complete",
                "execution.return_troubleshooter_complete",
            ),
            "default_cooldown_seconds": 900,
            "cooldown_wait_state_id": "execution.blocked_recovery.cooldown",
        },
        {
            "id": "execution.runtime_failure_recovery",
            "source_recovery_action_ids": runtime_failure_actions,
            "return_action_ids": ("execution.return_troubleshooter_recovered",),
            "quarantine_action_ids": (
                "execution.quarantine_troubleshooter_blocked",
            ),
            "recovery_stage_kind_id": "lad_troubleshooter",
            "recorded_source_selector": "latest_recovery_attempt_for_lineage",
            "attempt_scope": "lineage",
            "immediate_recovery_limit": 1,
            "cooldown_starts_at_attempt": 2,
            "quarantine_threshold_attempt": 3,
            "threshold_behavior": "runtime_quarantine_at_threshold",
            "return_allowed_phases": ("active_recovery", "quarantine_eligible"),
            "reset_trigger_action_ids": (
                "execution.route_builder_complete",
                "execution.route_checker_pass",
                "execution.route_fixer_complete",
                "execution.route_doublechecker_pass",
                "execution.close_updater_complete",
                "execution.return_troubleshooter_complete",
            ),
            "default_cooldown_seconds": 900,
            "cooldown_wait_state_id": "execution.runtime_failure_recovery.cooldown",
        },
    )


def _wait_states() -> tuple[dict[str, object], ...]:
    return (
        {
            "id": "execution.fix_needed_recovery.cooldown",
            "kind": "timer",
            "policy_id": "execution.fix_needed_recovery",
            "starts_at_attempt": 2,
            "duration_seconds": 900,
        },
        {
            "id": "execution.blocked_recovery.cooldown",
            "kind": "timer",
            "policy_id": "execution.blocked_recovery",
            "starts_at_attempt": 2,
            "duration_seconds": 900,
        },
        {
            "id": "execution.runtime_failure_recovery.cooldown",
            "kind": "timer",
            "policy_id": "execution.runtime_failure_recovery",
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


def _counters(include_integrator: bool) -> tuple[dict[str, object], ...]:
    counters = [
        _counter(
            counter_id="execution.fix_cycle_count.checker",
            stage_id="lad_checker",
            increment_action_id="execution.route_checker_fix_needed",
            threshold_action_id="execution.escalate_checker_fix_exhausted",
        ),
        _counter(
            counter_id="execution.fix_cycle_count.doublechecker",
            stage_id="lad_doublechecker",
            increment_action_id="execution.route_doublechecker_fix_needed",
            threshold_action_id="execution.escalate_doublechecker_fix_exhausted",
        ),
    ]
    for node, stage_id in _blocked_recovery_sources(include_integrator):
        counters.append(
            _counter(
                counter_id=f"execution.troubleshoot_attempt_count.{node}",
                stage_id=stage_id,
                increment_action_id=f"execution.route_{node}_blocked",
                threshold_action_id=f"execution.escalate_{node}_blocked_exhausted",
            )
        )
        counters.append(
            _counter(
                counter_id=f"execution.runtime_failure_count.{node}",
                stage_id=stage_id,
                increment_action_id=f"execution.recover_{node}_runtime_failure",
                threshold_action_id=(
                    f"execution.close_{node}_runtime_failure_exhausted"
                ),
            )
        )
    return tuple(counters)


def _intervention_option(
    *,
    option_id: str,
    policy_id: str,
    kind: str,
    workflow_id: str,
) -> dict[str, object]:
    base: dict[str, object] = {
        "id": option_id,
        "policy_id": policy_id,
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
        "payload_schema_id": _TASK_SCHEMA_ID,
        "target_queue_family_id": "task",
        "target_stage_kind_id": "lad_builder",
        "target_graph_node_id": _node_id(workflow_id, "builder"),
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


def _intervention_options(workflow_id: str) -> tuple[dict[str, object], ...]:
    options: list[dict[str, object]] = []
    for policy_id in ("execution.fix_needed_recovery", "execution.blocked_recovery"):
        suffix = policy_id.removeprefix("execution.").removesuffix("_recovery")
        options.extend(
            [
                _intervention_option(
                    option_id=f"execution.{suffix}.resume_lineage",
                    policy_id=policy_id,
                    kind="resume_lineage",
                    workflow_id=workflow_id,
                ),
                _intervention_option(
                    option_id=f"execution.{suffix}.close_lineage",
                    policy_id=policy_id,
                    kind="close_lineage",
                    workflow_id=workflow_id,
                ),
                _intervention_option(
                    option_id=f"execution.{suffix}.revise_lineage",
                    policy_id=policy_id,
                    kind="revise_lineage",
                    workflow_id=workflow_id,
                ),
            ]
        )
    return tuple(options)


def _assets(include_integrator: bool) -> tuple[dict[str, object], ...]:
    asset_specs = [
        ("lad_builder", "LAD Builder"),
        ("lad_checker", "LAD Checker"),
        ("lad_fixer", "LAD Fixer"),
        ("lad_doublechecker", "LAD Doublechecker"),
        ("lad_updater", "LAD Updater"),
        ("lad_troubleshooter", "LAD Troubleshooter"),
        ("lad_consultant", "LAD Consultant"),
    ]
    if include_integrator:
        asset_specs.insert(1, ("lad_integrator", "LAD Integrator"))
    assets: list[dict[str, object]] = []
    for stage_id, display_name in asset_specs:
        assets.append(
            _asset(
                node=stage_id,
                kind="prompt",
                body=f"Execute the selected {display_name} stage contract.",
                display_name=f"{display_name} entrypoint",
            )
        )
        skill_node = f"{stage_id.removeprefix('lad_')}_core"
        assets.append(
            _asset(
                node=skill_node,
                kind="skill",
                body=f"Apply {display_name} execution discipline.",
                display_name=f"{display_name} core skill",
            )
        )
    return tuple(assets)


def _base_source(*, include_integrator: bool) -> dict[str, object]:
    workflow_id = "execution.lad_integrator" if include_integrator else "execution.lad"
    workflow_name = (
        "LAD Execution With Integrator" if include_integrator else "LAD Execution"
    )
    stages = _stage_definitions(include_integrator)
    queue_families = [
        {
            "id": "task",
            "external_enqueue": True,
            "presentation": {"display_name": "Task"},
        },
        {
            "id": "stage_result",
            "external_enqueue": False,
            "presentation": {"display_name": "Stage result"},
        },
    ]
    if include_integrator:
        queue_families.append(
            {
                "id": "builder_summary",
                "external_enqueue": False,
                "presentation": {"display_name": "Builder summary"},
            }
        )
    artifact_schemas = [
        {
            "id": _TASK_SCHEMA_ID,
            "schema": _task_schema(),
            "presentation": {"display_name": "Task"},
        },
        {
            "id": _STAGE_RESULT_SCHEMA_ID,
            "schema": _object_schema(
                artifact_kind=_STAGE_RESULT_SCHEMA_ID,
            ),
            "presentation": {"display_name": "Stage result"},
        },
        {
            "id": _REPORT_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_REPORT_SCHEMA_ID),
            "presentation": {"display_name": "Report"},
        },
        {
            "id": _INCIDENT_REPORT_SCHEMA_ID,
            "schema": _object_schema(artifact_kind=_INCIDENT_REPORT_SCHEMA_ID),
            "presentation": {"display_name": "Incident report"},
        },
    ]
    if include_integrator:
        artifact_schemas.extend(
            [
                {
                    "id": _INTEGRATION_REPORT_SCHEMA_ID,
                    "schema": _object_schema(
                        artifact_kind=_INTEGRATION_REPORT_SCHEMA_ID,
                    ),
                    "presentation": {"display_name": "Integration report"},
                },
                {
                    "id": _BUILDER_SUMMARY_SCHEMA_ID,
                    "schema": _object_schema(
                        artifact_kind=_BUILDER_SUMMARY_SCHEMA_ID,
                    ),
                    "presentation": {"display_name": "Builder summary"},
                },
            ]
        )

    return {
        "lineage_policy": "root_from_external_enqueue",
        "workflow": {
            "id": workflow_id,
            "version": "0.1",
            "name": workflow_name,
            "compatibility_profile": None,
            "required_extensions": (),
        },
        "graphs": [
            {
                "id": f"{workflow_id}.graph",
                "node_ids": _graph_node_ids(workflow_id, include_integrator),
                "presentation": {"display_name": f"{workflow_name} Graph"},
            }
        ],
        "partitions": [
            {
                "id": "execution",
                "kind": "plane",
                "presentation": {"display_name": "Execution"},
            }
        ],
        "queue_families": queue_families,
        "external_enqueue_routes": [
            {
                "id": f"{workflow_id}.task",
                "queue_family_id": "task",
                "graph_node_id": _node_id(workflow_id, "builder"),
                "stage_kind_id": _stage_kind_id("lad_builder"),
                "runner_binding_id": _RUNNER_ID,
                "payload_schema_id": _TASK_SCHEMA_ID,
            }
        ],
        "artifact_schemas": artifact_schemas,
        "assets": _assets(include_integrator),
        "terminal_outcomes": _outcomes(include_integrator),
        "stage_kinds": stages,
        "terminal_actions": _actions(workflow_id, include_integrator),
        "recovery_policies": _recovery_policies(include_integrator),
        "wait_states": _wait_states(),
        "counters": _counters(include_integrator),
        "intervention_options": _intervention_options(workflow_id),
        "runner_bindings": [
            {
                "id": _RUNNER_ID,
                "adapter_kind": "fake_local",
                "stage_kind_ids": tuple(str(stage["id"]) for stage in stages),
                "required_capability_ids": (_RUNNER_INVOKE_CAPABILITY_ID,),
                "presentation": {"display_name": "Local LAD runner"},
            }
        ],
        "capabilities": [
            {
                "id": _RUNNER_INVOKE_CAPABILITY_ID,
                "kind": "runner.invoke",
                "support_status": "supported",
                "grant_status": "granted",
                "approval_policy_id": None,
            }
        ],
    }


def workflow_source() -> dict[str, object]:
    return deepcopy(_base_source(include_integrator=False))


def integrator_workflow_source() -> dict[str, object]:
    return deepcopy(_base_source(include_integrator=True))


def workflow_source_with_unselected_catalog() -> dict[str, object]:
    source = workflow_source()
    source["unselected_catalog"] = (
        {
            "id": "planning.unselected",
            "kind": "stage_catalog_entry",
            "catalog_payload": {"stage_kind_id": "planning.lad_planner"},
        },
        {
            "id": "learning.unselected",
            "kind": "stage_catalog_entry",
            "catalog_payload": {"stage_kind_id": "learning.lad_librarian"},
        },
        {
            "id": "recon.unselected",
            "kind": "stage_catalog_entry",
            "catalog_payload": {"stage_kind_id": "recon.lad_researcher"},
        },
    )
    return source


__all__ = (
    "integrator_workflow_source",
    "workflow_source",
    "workflow_source_with_unselected_catalog",
)
