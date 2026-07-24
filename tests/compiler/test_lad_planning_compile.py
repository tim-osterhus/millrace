from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy
from millrace.compiler import compile_workflow as _raw_compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import (
    QueueFamilyId,
    RunnerBindingId,
    SelectedCompiledPlan,
    StageKindId,
)
from millrace.contracts.ids import CapabilityId
from millrace.kernel.projection import ProjectionContext, evaluate_projection

Source = dict[str, object]
Record = dict[str, object]
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _compile_codex(source: Source):
    return _raw_compile_workflow(source, selected_runner_policy=_CODEX_POLICY)


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _compile(source: Source) -> SelectedCompiledPlan:
    result = _compile_codex(source)
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan


def test_lad_planning_compiles_into_selected_authority() -> None:
    from millrace.workflows import lad_planning

    plan = _compile(lad_planning.workflow_source())

    assert str(plan.workflow.workflow_id) == "planning.lad"
    assert plan.workflow.workflow_name == "LAD Planning"
    assert plan.lineage_policy == "root_from_external_enqueue"
    graphs = {str(graph.id): graph for graph in plan.graphs}
    assert set(graphs) == {"planning.lad.graph", "execution.lad.graph"}
    assert graphs["planning.lad.graph"].node_ids == (
        "planning.lad.recon.start",
        "planning.lad.planner.start",
        "planning.lad.manager.start",
        "planning.lad.mechanic.start",
        "planning.lad.auditor.start",
        "planning.lad.arbiter.start",
    )
    assert graphs["execution.lad.graph"].node_ids == (
        "execution.lad.builder.start",
        "execution.lad.checker.start",
        "execution.lad.fixer.start",
        "execution.lad.doublechecker.start",
        "execution.lad.updater.start",
        "execution.lad.troubleshooter.start",
        "execution.lad.consultant.start",
    )

    assert {str(queue.id) for queue in plan.queue_families} == {
        "spec",
        "probe",
        "incident",
        "task",
        "stage_result",
        "recon_packet",
        "generated_task",
        "generated_spec",
        "planner_disposition",
        "task_cards",
        "incident_report",
        "report",
        "rubric",
        "verdict",
    }
    assert QueueFamilyId("idea") not in {queue.id for queue in plan.queue_families}

    routes = {route.id: route for route in plan.external_enqueue_routes}
    assert set(routes) == {"spec", "probe", "incident", "execution.lad.task"}
    assert routes["spec"].queue_family_id == QueueFamilyId("spec")
    assert routes["spec"].graph_node_id == "planning.lad.planner.start"
    assert routes["spec"].stage_kind_id == StageKindId("lad_planner")
    assert str(routes["spec"].payload_schema_id) == "planning.intake.spec"
    assert routes["probe"].queue_family_id == QueueFamilyId("probe")
    assert routes["probe"].graph_node_id == "planning.lad.recon.start"
    assert routes["probe"].stage_kind_id == StageKindId("recon")
    assert str(routes["probe"].payload_schema_id) == "planning.intake.probe"
    assert routes["incident"].queue_family_id == QueueFamilyId("incident")
    assert routes["incident"].graph_node_id == "planning.lad.auditor.start"
    assert routes["incident"].stage_kind_id == StageKindId("lad_auditor")
    assert str(routes["incident"].payload_schema_id) == "planning.intake.incident"
    assert routes["execution.lad.task"].queue_family_id == QueueFamilyId("task")
    assert routes["execution.lad.task"].graph_node_id == "execution.lad.builder.start"
    assert routes["execution.lad.task"].stage_kind_id == StageKindId("lad_builder")
    assert str(routes["execution.lad.task"].payload_schema_id) == (
        "execution.artifacts.task"
    )
    assert {route.runner_binding_id for route in routes.values()} == {
        RunnerBindingId("planning.lad.local_runner"),
        RunnerBindingId("execution.lad.local_runner"),
    }

    stage_assets = {
        str(stage.id): {str(asset_id) for asset_id in stage.asset_ids}
        for stage in plan.stage_kinds
    }
    assert set(stage_assets) == {
        "lad_arbiter",
        "lad_auditor",
        "lad_builder",
        "lad_checker",
        "lad_consultant",
        "lad_doublechecker",
        "lad_fixer",
        "lad_manager",
        "lad_mechanic",
        "lad_planner",
        "lad_troubleshooter",
        "lad_updater",
        "recon",
    }
    assert stage_assets["recon"] == {
        "planning.entrypoints.recon",
        "planning.skills.recon_core",
    }
    assert stage_assets["lad_planner"] == {
        "planning.entrypoints.lad_planner",
        "planning.skills.planner_core",
    }
    assert stage_assets["lad_manager"] == {
        "planning.entrypoints.lad_manager",
        "planning.skills.manager_core",
    }
    assert stage_assets["lad_mechanic"] == {
        "planning.entrypoints.lad_mechanic",
        "planning.skills.mechanic_core",
    }
    assert stage_assets["lad_auditor"] == {
        "planning.entrypoints.lad_auditor",
        "planning.skills.auditor_core",
    }
    assert stage_assets["lad_arbiter"] == {
        "planning.entrypoints.lad_arbiter",
        "planning.skills.arbiter_core",
    }
    assert stage_assets["lad_builder"] == {
        "execution.entrypoints.lad_builder",
        "execution.skills.builder_core",
    }
    assert stage_assets["lad_checker"] == {
        "execution.entrypoints.lad_checker",
        "execution.skills.checker_core",
    }
    assert stage_assets["lad_fixer"] == {
        "execution.entrypoints.lad_fixer",
        "execution.skills.fixer_core",
    }
    assert stage_assets["lad_doublechecker"] == {
        "execution.entrypoints.lad_doublechecker",
        "execution.skills.doublechecker_core",
    }
    assert stage_assets["lad_updater"] == {
        "execution.entrypoints.lad_updater",
        "execution.skills.updater_core",
    }
    assert stage_assets["lad_troubleshooter"] == {
        "execution.entrypoints.lad_troubleshooter",
        "execution.skills.troubleshooter_core",
    }
    assert stage_assets["lad_consultant"] == {
        "execution.entrypoints.lad_consultant",
        "execution.skills.consultant_core",
    }
    runner_by_stage = {
        str(stage.id): str(stage.runner_binding_id) for stage in plan.stage_kinds
    }
    assert {
        stage_id
        for stage_id, runner_id in runner_by_stage.items()
        if runner_id == "planning.lad.local_runner"
    } == {
        "recon",
        "lad_planner",
        "lad_manager",
        "lad_mechanic",
        "lad_auditor",
        "lad_arbiter",
    }
    assert {
        stage_id
        for stage_id, runner_id in runner_by_stage.items()
        if runner_id == "execution.lad.local_runner"
    } == {
        "lad_builder",
        "lad_checker",
        "lad_fixer",
        "lad_doublechecker",
        "lad_updater",
        "lad_troubleshooter",
        "lad_consultant",
    }
    assert CapabilityId("capability.runner.invoke") in {
        capability.id for capability in plan.capabilities
    }


def test_lad_planning_selected_outcomes_actions_and_artifacts_are_closed() -> None:
    from millrace.workflows import lad_planning

    plan = _compile(lad_planning.workflow_source())
    actions = {str(action.id): action for action in plan.terminal_actions}
    markers_by_stage = {
        str(stage.id): {
            outcome.marker
            for outcome in plan.terminal_outcomes
            if outcome.stage_kind_id == stage.id
        }
        for stage in plan.stage_kinds
    }
    artifact_schemas = {str(schema.id) for schema in plan.artifact_schemas}

    assert markers_by_stage["recon"] == {
        "RECON_TO_EXECUTION",
        "RECON_TO_PLANNING",
        "RECON_NOOP",
        "RECON_BLOCKED",
        "BLOCKED",
    }
    assert markers_by_stage["lad_planner"] == {"PLANNER_COMPLETE", "BLOCKED", ""}
    assert markers_by_stage["lad_manager"] == {"MANAGER_COMPLETE", "BLOCKED", ""}
    assert markers_by_stage["lad_mechanic"] == {
        "MECHANIC_COMPLETE",
        "BLOCKED",
        "",
        "MECHANIC_RECOVERED",
        "MECHANIC_QUARANTINE",
    }
    assert markers_by_stage["lad_auditor"] == {"AUDITOR_COMPLETE", "BLOCKED", ""}
    assert markers_by_stage["lad_arbiter"] == {
        "ARBITER_COMPLETE",
        "REMEDIATION_NEEDED",
        "BLOCKED",
    }

    assert actions["planning.route_planner_complete"].action_kind == "route"
    assert str(actions["planning.route_planner_complete"].target_stage_kind_id) == (
        "lad_manager"
    )
    assert actions["planning.route_auditor_complete"].action_kind == "route"
    assert str(actions["planning.route_auditor_complete"].target_stage_kind_id) == (
        "lad_planner"
    )
    assert actions["planning.route_planner_blocked"].action_kind == "recovery_route"
    assert str(actions["planning.route_planner_blocked"].target_stage_kind_id) == (
        "lad_mechanic"
    )
    assert actions["planning.route_manager_blocked"].action_kind == "recovery_route"
    assert actions["planning.route_auditor_blocked"].action_kind == "recovery_route"
    assert actions["planning.close_manager_complete"].action_kind == (
        "complete_work_item"
    )
    assert str(actions["planning.close_manager_complete"].artifact_schema_id) == (
        "planning.artifacts.task_cards"
    )
    assert actions["planning.recon_enqueue_task"].action_kind == "route"
    assert str(actions["planning.recon_enqueue_task"].emitted_queue_family_id) == (
        "task"
    )
    assert str(actions["planning.recon_enqueue_task"].target_stage_kind_id) == (
        "lad_builder"
    )
    assert str(actions["planning.recon_enqueue_task"].artifact_schema_id) == (
        "execution.artifacts.task"
    )
    assert "execution.route_builder_complete" in actions
    assert "execution.close_updater_complete" in actions
    assert markers_by_stage["lad_checker"] == {
        "CHECKER_PASS",
        "FIX_NEEDED",
        "",
        "BLOCKED",
        "RUNTIME_FAILURE",
        "RUNTIME_FAILURE_ESCALATE",
    }
    assert actions["planning.recon_enqueue_spec"].action_kind == "route"
    assert str(actions["planning.recon_enqueue_spec"].emitted_queue_family_id) == (
        "spec"
    )
    assert str(actions["planning.recon_enqueue_spec"].target_stage_kind_id) == (
        "lad_planner"
    )
    assert str(actions["planning.recon_enqueue_spec"].artifact_schema_id) == (
        "planning.artifacts.generated_spec"
    )
    assert actions["planning.closure_gap"].action_kind == "closure_gap"
    assert len(plan.fanout_declarations) == 1
    fanout = plan.fanout_declarations[0]
    assert str(fanout.id) == "planning.manager.task_cards_to_execution"
    assert str(fanout.source_action_id) == "planning.close_manager_complete"
    assert str(fanout.source_artifact_schema_id) == "planning.artifacts.task_cards"
    assert fanout.target_route_id == "execution.lad.task"
    assert str(fanout.target_payload_schema_id) == "execution.artifacts.task"
    assert fanout.root_lineage_policy == "inherit_source_lineage"
    assert fanout.dependency_policy == "depends_on_source_work_item"
    assert "planning.artifacts.task_cards" in artifact_schemas
    assert "planning.artifacts.generated_task" in artifact_schemas
    assert "planning.artifacts.generated_spec" in artifact_schemas
    assert "execution.artifacts.task" in artifact_schemas
    assert "planning.intake.spec" in artifact_schemas
    assert "planning.intake.probe" in artifact_schemas
    assert "planning.intake.incident" in artifact_schemas


def test_planner_route_preserves_source_request_and_planning_result() -> None:
    from millrace.workflows import lad_planning

    plan = _compile(lad_planning.workflow_source())
    action = next(
        item
        for item in plan.terminal_actions
        if str(item.id) == "planning.route_planner_complete"
    )
    source_request = {
        "title": "Exact source",
        "body": "Create result.txt containing exactly: preserved literal.",
    }
    planning_result = {
        "artifact_kind": "planning.artifacts.stage_result",
        "summary": "Ready for decomposition.",
    }

    projected = evaluate_projection(
        action.payload_projection,
        ProjectionContext(
            work_item_payload=source_request,
            artifact_payload=planning_result,
            observation_payload={"marker": "PLANNER_COMPLETE"},
            run_metadata={"run_id": "planner-run"},
            plan_metadata={"workflow_id": "planning.lad"},
        ),
    )

    assert projected.accepted is True
    assert projected.value == {
        "planning_result": planning_result,
        "source_request": source_request,
    }


def test_lad_planning_selects_mechanic_recovery_and_threshold_blocking() -> None:
    from millrace.workflows import lad_planning

    plan = _compile(lad_planning.workflow_source())
    actions = {str(action.id): action for action in plan.terminal_actions}
    policies = {str(policy.id): policy for policy in plan.recovery_policies}
    counters = {str(counter.id): counter for counter in plan.counters}
    waits = {str(wait.id): wait for wait in plan.wait_states}
    interventions = {str(option.id): option for option in plan.intervention_options}

    assert actions["planning.route_planner_blocked"].action_kind == "recovery_route"
    assert actions["planning.route_manager_blocked"].action_kind == "recovery_route"
    assert actions["planning.route_auditor_blocked"].action_kind == "recovery_route"
    assert actions["planning.route_mechanic_blocked"].action_kind == "recovery_route"
    assert actions["planning.escalate_planner_blocked_exhausted"].action_kind == (
        "recovery_route"
    )
    assert actions["planning.escalate_manager_blocked_exhausted"].action_kind == (
        "recovery_route"
    )
    assert actions["planning.escalate_auditor_blocked_exhausted"].action_kind == (
        "recovery_route"
    )
    assert actions["planning.escalate_mechanic_blocked_exhausted"].action_kind == (
        "recovery_route"
    )
    assert actions["planning.return_mechanic_recovered"].action_kind == (
        "return_to_recorded_source"
    )
    assert actions["planning.quarantine_mechanic_blocked"].action_kind == (
        "quarantine_lineage"
    )

    mechanic_complete = actions["planning.route_mechanic_complete"]
    assert mechanic_complete.action_kind == "route"
    assert str(mechanic_complete.target_stage_kind_id) == "lad_planner"
    assert mechanic_complete.target_graph_node_id == "planning.lad.planner.start"
    assert isinstance(mechanic_complete.dynamic_target_selector, Mapping)
    assert mechanic_complete.dynamic_target_selector["field_names"] == ("resume_stage",)
    assert "mechanic" not in mechanic_complete.dynamic_target_selector["targets"]

    policy = policies["planning.blocked.recovery"]
    assert tuple(str(item) for item in policy.source_recovery_action_ids) == (
        "planning.route_planner_blocked",
        "planning.route_manager_blocked",
        "planning.route_mechanic_blocked",
        "planning.route_auditor_blocked",
    )
    assert tuple(str(item) for item in policy.return_action_ids) == (
        "planning.return_mechanic_recovered",
    )
    assert tuple(str(item) for item in policy.quarantine_action_ids) == (
        "planning.quarantine_mechanic_blocked",
    )
    assert str(policy.recovery_stage_kind_id) == "lad_mechanic"
    assert policy.immediate_recovery_limit == 1
    assert policy.cooldown_starts_at_attempt == 2
    assert policy.quarantine_threshold_attempt == 2
    assert policy.threshold_behavior == "runtime_quarantine_at_threshold"
    assert policy.default_cooldown_seconds == 900
    assert str(policy.cooldown_wait_state_id) == "planning.blocked.recovery.cooldown"
    assert "planning.blocked.recovery.cooldown" in waits

    assert counters["planning.mechanic_attempt_count.planner"].threshold_count == 2
    planner_counter = counters["planning.mechanic_attempt_count.planner"]
    assert str(planner_counter.increment_action_id) == "planning.route_planner_blocked"
    assert str(planner_counter.threshold_action_id) == (
        "planning.escalate_planner_blocked_exhausted"
    )
    assert (
        interventions["planning.blocked.resume_lineage"].option_kind == "resume_lineage"
    )
    assert (
        interventions["planning.blocked.close_lineage"].option_kind == "close_lineage"
    )
    revise = interventions["planning.blocked.revise_lineage"]
    assert revise.option_kind == "revise_lineage"
    assert str(revise.target_stage_kind_id) == "lad_planner"
    assert str(revise.payload_schema_id) == "planning.intake.spec"


def test_lad_planning_selects_completion_behavior_and_remediation_policy() -> None:
    from millrace.workflows import lad_planning

    plan = _compile(lad_planning.workflow_source())
    behaviors = {str(item.id): item for item in plan.completion_behaviors}
    remediation = {str(item.id): item for item in plan.remediation_policies}
    actions = {str(action.id): action for action in plan.terminal_actions}

    behavior = behaviors["planning.closure.completion"]
    assert behavior.trigger == "backlog_drained"
    assert behavior.readiness_rule == "no_open_lineage_work"
    assert behavior.request_kind == "closure_target"
    assert behavior.target_selector == "active_closure_target"
    assert str(behavior.target_stage_kind_id) == "lad_arbiter"
    assert behavior.target_graph_node_id == "planning.lad.arbiter.start"
    assert str(behavior.runner_binding_id) == "planning.lad.local_runner"
    assert str(behavior.request_queue_family_id) == "stage_result"
    assert str(behavior.pass_action_id) == "planning.close_arbiter_complete"
    assert str(behavior.gap_action_id) == "planning.closure_gap"
    assert str(behavior.blocked_action_id) == "planning.close_arbiter_blocked"
    assert str(behavior.verdict_artifact_schema_id) == "planning.artifacts.verdict"
    assert str(behavior.remediation_policy_id) == "planning.closure.remediation"
    assert behavior.accepted_root_source_kinds == (
        "idea",
        "probe",
        "manual",
        "spec",
        "incident",
    )

    assert actions["planning.close_arbiter_complete"].action_kind == (
        "complete_work_item"
    )
    assert actions["planning.closure_gap"].action_kind == "closure_gap"
    assert actions["planning.close_arbiter_blocked"].action_kind == "block_work_item"
    assert "close_closure_target" not in {
        str(action.id) for action in plan.terminal_actions
    }

    policy = remediation["planning.closure.remediation"]
    assert str(policy.source_action_id) == "planning.closure_gap"
    assert str(policy.target_queue_family_id) == "incident"
    assert str(policy.target_stage_kind_id) == "lad_auditor"
    assert policy.target_graph_node_id == "planning.lad.auditor.start"
    assert str(policy.target_runner_binding_id) == "planning.lad.local_runner"
    assert str(policy.payload_schema_id) == "planning.intake.incident"
    assert policy.dedupe_key == "closure_target_and_source_artifact"
    assert policy.duplicate_policy == "refuse"


def test_lad_planning_selected_authority_excludes_unselected_catalog() -> None:
    from millrace.workflows import lad_planning

    base_plan = _compile(lad_planning.workflow_source())
    catalog_plan = _compile(lad_planning.workflow_source_with_unselected_catalog())

    assert authority_fingerprint(base_plan) == authority_fingerprint(catalog_plan)
    assert "execution.lad_builder" not in {
        str(stage.id) for stage in catalog_plan.stage_kinds
    }
    assert "learning.lad_librarian" not in {
        str(stage.id) for stage in catalog_plan.stage_kinds
    }


def test_lad_planning_refuses_conflicting_composed_authority(monkeypatch) -> None:
    from millrace.workflows import lad_planning

    base_execution_source = lad_planning.lad_execution.workflow_source

    def conflicting_execution_source():
        source = base_execution_source()
        stage_result = next(
            queue
            for queue in _records(source, "queue_families")
            if queue["id"] == "stage_result"
        )
        stage_result["presentation"] = {"display_name": "Conflicting Stage Result"}
        return source

    monkeypatch.setattr(
        lad_planning.lad_execution,
        "workflow_source",
        conflicting_execution_source,
    )

    with pytest.raises(ValueError, match="conflicting selected declaration"):
        lad_planning.workflow_source()


def test_lad_planning_invalid_route_target_is_diagnostic() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    route = next(route for route in _records(source, "external_enqueue_routes"))
    route["graph_node_id"] = "planning.lad.missing.start"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path == "external_enqueue_routes[0].graph_node_id"
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "graph_node"
    assert error.context["referenced_id"] == "planning.lad.missing.start"


def test_lad_planning_invalid_route_payload_schema_is_diagnostic() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    route = next(route for route in _records(source, "external_enqueue_routes"))
    route["payload_schema_id"] = "planning.intake.missing"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path == "external_enqueue_routes[0].payload_schema_id"
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "artifact_schema"
    assert error.context["referenced_id"] == "planning.intake.missing"


def test_lad_planning_duplicate_queue_family_is_diagnostic() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    families = _records(source, "queue_families")
    duplicate = dict(families[0])
    source["queue_families"] = (*families, duplicate)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "duplicate_id"
    )
    assert error.context["duplicate_id"] == "spec"


def test_lad_planning_missing_stage_asset_is_diagnostic() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    stage = next(
        item for item in _records(source, "stage_kinds") if item["id"] == "recon"
    )
    stage["asset_ids"] = ("planning.entrypoints.recon", "planning.skills.missing")

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path == "stage_kinds[0].asset_ids[1]"
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "asset"
    assert error.context["referenced_id"] == "planning.skills.missing"


def test_lad_planning_unresolved_outcome_is_diagnostic() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    action = next(
        item
        for item in _records(source, "terminal_actions")
        if item["id"] == "planning.route_planner_complete"
    )
    action["outcome_id"] = "planning.lad_planner.MISSING"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path.endswith(".outcome_id")
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "terminal_outcome"
    assert error.context["referenced_id"] == "planning.lad_planner.MISSING"


def test_lad_planning_invalid_artifact_schema_is_diagnostic() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    schemas = _records(source, "artifact_schemas")
    task_cards = next(
        item for item in schemas if item["id"] == "planning.artifacts.task_cards"
    )
    schema = cast(dict[str, object], task_cards["schema"])
    schema["type"] = "number"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "invalid_artifact_schema"
    )
    assert error.context["schema_id"] == "planning.artifacts.task_cards"


def test_lad_planning_invalid_completion_request_kind_is_diagnostic() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    behavior = next(iter(_records(source, "completion_behaviors")))
    behavior["request_kind"] = "wrong_request"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path == "completion_behaviors[0].request_kind"
    )
    assert error.code == "invalid_completion_behavior_declaration"
    assert error.context["behavior_id"] == "planning.closure.completion"
    assert error.context["reason"] == "unsupported_request_kind"


def test_lad_planning_missing_completion_target_graph_node_is_diagnostic() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    behavior = next(iter(_records(source, "completion_behaviors")))
    behavior["target_graph_node_id"] = "planning.lad.missing.start"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path == "completion_behaviors[0].target_graph_node_id"
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "graph_node"
    assert error.context["referenced_id"] == "planning.lad.missing.start"


def test_lad_planning_invalid_remediation_guidance_source_is_diagnostic() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    policy = next(iter(_records(source, "remediation_policies")))
    policy["guidance_source"] = "static"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path == "remediation_policies[0].guidance_source"
    )
    assert error.code == "invalid_remediation_policy_declaration"
    assert error.context["policy_id"] == "planning.closure.remediation"
    assert error.context["reason"] == "unsupported_guidance_source"


def test_lad_planning_invalid_remediation_source_action_is_diagnostic() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    policy = next(iter(_records(source, "remediation_policies")))
    policy["source_action_id"] = "planning.close_arbiter_complete"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path == "remediation_policies[0].source_action_id"
        and diagnostic.code == "invalid_remediation_policy_declaration"
    )
    assert error.context["policy_id"] == "planning.closure.remediation"
    assert error.context["reason"] == "unsupported_source_action_kind"


def test_lad_planning_compatibility_profile_is_refused() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    workflow = cast(dict[str, object], source["workflow"])
    workflow["compatibility_profile"] = "lad_codex"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "unsupported_compatibility_profile"
    )
    assert error.declaration_path == "workflow.compatibility_profile"
    assert error.context["compatibility_profile"] == "lad_codex"


def test_lad_planning_old_loop_override_is_refused_if_authored() -> None:
    from millrace.workflows import lad_planning

    source = lad_planning.workflow_source()
    source["old_loop_config_override"] = {"path": "assets/loops/planning/lad.json"}

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "unknown_source_section"
    )
    assert error.declaration_path == "old_loop_config_override"
