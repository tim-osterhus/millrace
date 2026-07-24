from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy
from millrace.compiler import compile_workflow as _raw_compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import QueueFamilyId, RunnerBindingId, StageKindId
from millrace.contracts.ids import CapabilityId
from millrace.workflows import lad_execution

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


def _compile(source: Source):
    result = _compile_codex(source)
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan


def _terminal_action(source: Source, action_id: str) -> Record:
    return next(
        action
        for action in _records(source, "terminal_actions")
        if action["id"] == action_id
    )


def _terminal_action_index(source: Source, action: Record) -> int:
    return _records(source, "terminal_actions").index(action)


def test_lad_execution_base_compiles_into_selected_authority() -> None:
    source = lad_execution.workflow_source()

    plan = _compile(source)

    assert str(plan.workflow.workflow_id) == "execution.lad"
    assert plan.workflow.workflow_name == "LAD Execution"
    assert plan.lineage_policy == "root_from_external_enqueue"
    assert [str(graph.id) for graph in plan.graphs] == ["execution.lad.graph"]
    assert plan.graphs[0].node_ids[0] == "execution.lad.builder.start"
    assert QueueFamilyId("task") in {queue.id for queue in plan.queue_families}
    assert QueueFamilyId("builder_summary") not in {
        queue.id for queue in plan.queue_families
    }
    assert len(plan.external_enqueue_routes) == 1
    route = plan.external_enqueue_routes[0]
    assert route.queue_family_id == QueueFamilyId("task")
    assert route.graph_node_id == "execution.lad.builder.start"
    assert route.stage_kind_id == StageKindId("lad_builder")
    assert route.runner_binding_id == RunnerBindingId("execution.lad.local_runner")
    assert str(route.payload_schema_id) == "execution.artifacts.task"

    builder = next(
        stage for stage in plan.stage_kinds if stage.id == StageKindId("lad_builder")
    )
    assert builder.input_queue_family_ids == (
        QueueFamilyId("task"),
        QueueFamilyId("stage_result"),
    )
    assert "execution.artifacts.task" in {
        str(schema_id) for schema_id in builder.artifact_schema_ids
    }
    assert {str(asset_id) for asset_id in builder.asset_ids} == {
        "execution.entrypoints.lad_builder",
        "execution.skills.builder_core",
    }
    assert CapabilityId("capability.runner.invoke") in {
        capability.id for capability in plan.capabilities
    }
    runner = plan.runner_bindings[0]
    assert runner.required_capability_ids == (CapabilityId("capability.runner.invoke"),)


def test_lad_integrator_first_dispatch_still_routes_to_builder() -> None:
    plan = _compile(lad_execution.integrator_workflow_source())

    assert str(plan.workflow.workflow_id) == "execution.lad_integrator"
    assert [str(graph.id) for graph in plan.graphs] == [
        "execution.lad_integrator.graph"
    ]
    route = plan.external_enqueue_routes[0]
    assert route.queue_family_id == QueueFamilyId("task")
    assert route.graph_node_id == "execution.lad_integrator.builder.start"
    assert route.stage_kind_id == StageKindId("lad_builder")
    assert str(route.payload_schema_id) == "execution.artifacts.task"


def test_lad_terminal_actions_encode_selected_action_classes() -> None:
    plan = _compile(lad_execution.workflow_source())
    actions = {str(action.id): action for action in plan.terminal_actions}

    assert actions["execution.close_updater_complete"].action_kind == (
        "complete_work_item"
    )
    assert actions["execution.close_consultant_needs_plan"].action_kind == (
        "close_with_escalation"
    )
    assert actions["execution.close_consultant_blocked"].action_kind == (
        "block_work_item"
    )


def test_lad_rejects_old_plane_directed_terminal_action_kind() -> None:
    source = lad_execution.workflow_source()
    action = _terminal_action(source, "execution.close_consultant_needs_plan")
    action["kind"] = "escalate_to_planning"
    action_index = _terminal_action_index(source, action)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "unsupported_terminal_action_kind"
    )
    assert error.declaration_path == f"terminal_actions[{action_index}].kind"
    assert error.context["action_id"] == "execution.close_consultant_needs_plan"
    assert error.context["action_kind"] == "escalate_to_planning"


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("target_stage_kind_id", "lad_consultant"),
        ("target_graph_node_id", "execution.lad.consultant.start"),
        ("emitted_queue_family_id", "stage_result"),
        ("runner_binding_id", "execution.lad.local_runner"),
        ("payload_projection", {"kind": "source", "path": ("artifact_payload",)}),
        (
            "dynamic_target_selector",
            {
                "kind": "observation_payload_route_target",
                "field_names": ("target_stage",),
                "targets": {
                    "builder": {
                        "target_stage_kind_id": "lad_builder",
                        "target_graph_node_id": "execution.lad.builder.start",
                        "emitted_queue_family_id": "stage_result",
                        "runner_binding_id": "execution.lad.local_runner",
                    },
                },
            },
        ),
    ),
)
def test_lad_close_with_escalation_rejects_route_authority_fields(
    field_name: str,
    field_value: Any,
) -> None:
    source = lad_execution.workflow_source()
    action = _terminal_action(source, "execution.close_consultant_needs_plan")
    action["kind"] = "close_with_escalation"
    action[field_name] = field_value
    action_index = _terminal_action_index(source, action)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "terminal_close_with_escalation_route_authority"
    )
    assert error.declaration_path == f"terminal_actions[{action_index}].{field_name}"
    assert error.context["referrer_path"] == f"terminal_actions[{action_index}]"
    assert error.context["action_id"] == "execution.close_consultant_needs_plan"
    assert error.context["field_name"] == field_name


def test_lad_recovery_and_counter_authority_is_selected_data() -> None:
    plan = _compile(lad_execution.workflow_source())
    actions = {str(action.id): action for action in plan.terminal_actions}
    policies = {str(policy.id): policy for policy in plan.recovery_policies}
    counters = {str(counter.id): counter for counter in plan.counters}
    intervention_options = {
        str(option.id): option for option in plan.intervention_options
    }
    artifact_schemas = {str(schema.id): schema for schema in plan.artifact_schemas}

    assert actions["execution.route_checker_fix_needed"].action_kind == "route"
    assert actions["execution.escalate_checker_fix_exhausted"].action_kind == (
        "recovery_route"
    )
    assert actions["execution.route_builder_blocked"].action_kind == "route"
    assert actions["execution.escalate_builder_blocked_exhausted"].action_kind == (
        "recovery_route"
    )
    assert actions["execution.return_troubleshooter_recovered"].action_kind == (
        "return_to_recorded_source"
    )
    assert actions["execution.return_troubleshooter_complete"].action_kind == "route"
    troubleshooter_selector = actions[
        "execution.return_troubleshooter_complete"
    ].dynamic_target_selector
    assert isinstance(troubleshooter_selector, Mapping)
    assert troubleshooter_selector["field_names"] == ("resume_stage",)
    assert set(troubleshooter_selector["targets"]) == {
        "builder",
        "checker",
        "fixer",
        "doublechecker",
        "updater",
    }
    consultant_selector = actions[
        "execution.route_consultant_complete"
    ].dynamic_target_selector
    assert isinstance(consultant_selector, Mapping)
    assert consultant_selector["field_names"] == ("target_stage", "resume_stage")
    assert "consultant" not in consultant_selector["targets"]
    assert actions["execution.quarantine_troubleshooter_blocked"].action_kind == (
        "quarantine_lineage"
    )

    fix_policy = policies["execution.fix_needed_recovery"]
    assert tuple(
        str(action_id) for action_id in fix_policy.source_recovery_action_ids
    ) == (
        "execution.route_checker_fix_needed",
        "execution.route_doublechecker_fix_needed",
    )
    assert str(fix_policy.recovery_stage_kind_id) == "lad_troubleshooter"
    assert fix_policy.immediate_recovery_limit == 1
    assert fix_policy.cooldown_starts_at_attempt == 2
    assert fix_policy.quarantine_threshold_attempt == 3
    assert tuple(str(action_id) for action_id in fix_policy.return_action_ids) == (
        "execution.return_troubleshooter_recovered",
    )
    assert tuple(str(action_id) for action_id in fix_policy.quarantine_action_ids) == (
        "execution.quarantine_troubleshooter_blocked",
    )

    blocked_policy = policies["execution.blocked_recovery"]
    assert "execution.route_builder_blocked" in {
        str(action_id) for action_id in blocked_policy.source_recovery_action_ids
    }
    assert str(blocked_policy.recovery_stage_kind_id) == "lad_consultant"
    assert blocked_policy.cooldown_starts_at_attempt == 2
    assert blocked_policy.quarantine_threshold_attempt == 3
    assert tuple(str(action_id) for action_id in blocked_policy.return_action_ids) == (
        "execution.return_consultant_recovered",
    )
    assert tuple(
        str(action_id) for action_id in blocked_policy.quarantine_action_ids
    ) == ("execution.quarantine_consultant_blocked",)
    runtime_failure_policy = policies["execution.runtime_failure_recovery"]
    assert "execution.recover_builder_runtime_failure" in {
        str(action_id)
        for action_id in runtime_failure_policy.source_recovery_action_ids
    }
    assert str(runtime_failure_policy.recovery_stage_kind_id) == "lad_troubleshooter"
    assert actions["execution.recover_builder_runtime_failure"].action_kind == (
        "recovery_route"
    )
    assert (
        actions["execution.close_builder_runtime_failure_exhausted"].action_kind
        == "block_work_item"
    )

    assert counters["execution.fix_cycle_count.checker"].threshold_count == 2
    assert counters["execution.fix_cycle_count.doublechecker"].threshold_count == 2
    assert counters["execution.troubleshoot_attempt_count.builder"].threshold_count == 2
    assert counters["execution.runtime_failure_count.builder"].threshold_count == 2

    assert "execution.artifacts.task" in artifact_schemas
    assert "execution.artifacts.builder_summary" not in artifact_schemas
    assert "execution.artifacts.integration_report" not in artifact_schemas
    assert "lad_integrator" not in {str(stage.id) for stage in plan.stage_kinds}
    assert "execution.entrypoints.lad_integrator" not in {
        str(asset.id) for asset in plan.assets
    }
    assert "execution.skills.integrator_core" not in {
        str(asset.id) for asset in plan.assets
    }
    assert "execution.lad.integrator.start" not in plan.graphs[0].node_ids
    assert "execution.route_integrator_complete" not in actions
    assert "execution.route_integrator_blocked" not in actions
    assert "FIX_NEEDED_ESCALATE" not in {
        outcome.marker for outcome in plan.terminal_outcomes
    }
    assert "BLOCKED_ESCALATE" not in {
        outcome.marker for outcome in plan.terminal_outcomes
    }
    resume_option = intervention_options["execution.blocked.resume_lineage"]
    close_option = intervention_options["execution.blocked.close_lineage"]
    revise_option = intervention_options["execution.blocked.revise_lineage"]
    assert str(resume_option.policy_id) == "execution.blocked_recovery"
    assert resume_option.option_kind == "resume_lineage"
    assert close_option.option_kind == "close_lineage"
    assert revise_option.option_kind == "revise_lineage"
    assert str(revise_option.payload_schema_id) == "execution.artifacts.task"
    assert str(revise_option.target_queue_family_id) == "task"
    assert str(revise_option.target_stage_kind_id) == "lad_builder"


def test_lad_integrator_blocked_recovery_sources_include_integrator() -> None:
    plan = _compile(lad_execution.integrator_workflow_source())
    blocked_policy = next(
        policy
        for policy in plan.recovery_policies
        if str(policy.id) == "execution.blocked_recovery"
    )

    assert "execution.route_integrator_blocked" in {
        str(action_id) for action_id in blocked_policy.source_recovery_action_ids
    }
    assert "FIX_NEEDED_ESCALATE" not in {
        outcome.marker for outcome in plan.terminal_outcomes
    }
    assert "BLOCKED_ESCALATE" not in {
        outcome.marker for outcome in plan.terminal_outcomes
    }


def test_lad_integrator_route_artifact_authority_is_selected_explicitly() -> None:
    plan = _compile(lad_execution.integrator_workflow_source())
    actions = {str(action.id): action for action in plan.terminal_actions}
    builder = next(
        stage for stage in plan.stage_kinds if stage.id == StageKindId("lad_builder")
    )
    checker = next(
        stage for stage in plan.stage_kinds if stage.id == StageKindId("lad_checker")
    )
    integrator = next(
        stage for stage in plan.stage_kinds if stage.id == StageKindId("lad_integrator")
    )

    builder_route = actions["execution.route_builder_complete"]
    assert str(builder_route.emitted_queue_family_id) == "builder_summary"
    assert str(builder_route.artifact_schema_id) == (
        "execution.artifacts.builder_summary"
    )
    assert "execution.artifacts.builder_summary" in {
        str(schema_id) for schema_id in builder.artifact_schema_ids
    }

    integrator_route = actions["execution.route_integrator_complete"]
    assert str(integrator_route.artifact_schema_id) == (
        "execution.artifacts.integration_report"
    )
    assert "execution.artifacts.integration_report" in {
        str(schema_id) for schema_id in checker.artifact_schema_ids
    }
    assert "execution.artifacts.report" in {
        str(schema_id) for schema_id in integrator.artifact_schema_ids
    }
    assert QueueFamilyId("stage_result") in integrator.input_queue_family_ids

    resume_selector = actions[
        "execution.return_troubleshooter_complete"
    ].dynamic_target_selector
    assert isinstance(resume_selector, Mapping)
    assert "integrator" in resume_selector["targets"]
    consultant_selector = actions[
        "execution.route_consultant_complete"
    ].dynamic_target_selector
    assert isinstance(consultant_selector, Mapping)
    assert "integrator" in consultant_selector["targets"]


def test_lad_recovery_policy_rejects_threshold_action_as_source() -> None:
    source = lad_execution.workflow_source()
    policy = next(
        item
        for item in _records(source, "recovery_policies")
        if item["id"] == "execution.fix_needed_recovery"
    )
    policy["source_recovery_action_ids"] = ("execution.escalate_checker_fix_exhausted",)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "recovery_policy_threshold_action_source"
    )
    assert error.declaration_path.endswith(".source_recovery_action_ids[0]")
    assert error.context["referenced_id"] == (
        "execution.escalate_checker_fix_exhausted"
    )
    assert error.context["counter_id"] == "execution.fix_cycle_count.checker"


def test_lad_recovery_counter_requires_increment_policy_source() -> None:
    source = lad_execution.workflow_source()
    policy = next(
        item
        for item in _records(source, "recovery_policies")
        if item["id"] == "execution.fix_needed_recovery"
    )
    policy["source_recovery_action_ids"] = ("execution.route_doublechecker_fix_needed",)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "counter_recovery_policy_source_missing"
    )
    assert error.declaration_path == "counters[0].increment_action_id"
    assert error.context["counter_id"] == "execution.fix_cycle_count.checker"
    assert error.context["increment_action_id"] == "execution.route_checker_fix_needed"


def test_lad_counter_duplicate_action_ownership_is_rejected() -> None:
    source = lad_execution.workflow_source()
    counters = cast(tuple[Record, ...], source["counters"])
    duplicate = dict(counters[0])
    duplicate["id"] = "execution.fix_cycle_count.checker_duplicate"
    source["counters"] = (*counters, duplicate)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "duplicate_counter_action"
    )
    assert error.declaration_path.endswith(".increment_action_id")
    assert error.context["existing_counter_id"] == "execution.fix_cycle_count.checker"


def test_lad_counter_duplicate_threshold_action_ownership_is_rejected() -> None:
    source = lad_execution.workflow_source()
    counters = cast(tuple[Record, ...], source["counters"])
    duplicate = dict(counters[0])
    duplicate["id"] = "execution.fix_cycle_count.checker_threshold_duplicate"
    duplicate["increment_action_id"] = "execution.route_checker_pass"
    source["counters"] = (*counters, duplicate)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "duplicate_counter_action"
        and diagnostic.declaration_path.endswith(".threshold_action_id")
    )
    assert error.context["action_id"] == "execution.escalate_checker_fix_exhausted"
    assert error.context["existing_counter_id"] == "execution.fix_cycle_count.checker"


def test_lad_terminal_action_artifact_schema_must_belong_to_source_stage() -> None:
    source = lad_execution.workflow_source()
    action = next(
        item
        for item in _records(source, "terminal_actions")
        if item["id"] == "execution.close_updater_complete"
    )
    action["artifact_schema_id"] = "execution.artifacts.incident_report"

    result = _compile_codex(source)

    assert result.plan is None
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    assert len(errors) == 1
    error = errors[0]
    assert error.code == "terminal_action_artifact_schema_mismatch"
    assert error.declaration_path.endswith(".artifact_schema_id")
    assert error.context["action_id"] == "execution.close_updater_complete"
    assert error.context["source_stage_kind_id"] == "lad_updater"
    assert error.context["artifact_schema_id"] == "execution.artifacts.incident_report"


def test_lad_compatibility_profile_is_refused() -> None:
    source = lad_execution.workflow_source()
    workflow = cast(dict[str, object], source["workflow"])
    workflow["compatibility_profile"] = "lad_codex"

    result = _compile_codex(source)

    assert result.plan is None
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    assert len(errors) == 1
    error = errors[0]
    assert error.code == "unsupported_compatibility_profile"
    assert error.declaration_path == "workflow.compatibility_profile"
    assert error.context["compatibility_profile"] == "lad_codex"


def test_lad_selected_authority_excludes_unselected_non_execution_planes() -> None:
    base_plan = _compile(lad_execution.workflow_source())
    catalog_plan = _compile(lad_execution.workflow_source_with_unselected_catalog())

    assert authority_fingerprint(base_plan) == authority_fingerprint(catalog_plan)

    selected_stage_ids = {str(stage.id) for stage in catalog_plan.stage_kinds}
    assert "planning.lad_planner" not in selected_stage_ids
    assert "learning.lad_librarian" not in selected_stage_ids
    assert "recon.lad_researcher" not in selected_stage_ids


def test_lad_capability_reference_diagnostics_are_structured() -> None:
    source = lad_execution.workflow_source()
    runner = _records(source, "runner_bindings")[0]
    runner["required_capability_ids"] = ("missing.capability",)

    result = _compile_codex(source)

    assert result.plan is None
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    assert len(errors) == 1
    error = errors[0]
    assert error.code == "missing_reference"
    assert error.declaration_path == "runner_bindings[0].required_capability_ids[0]"
    assert error.context["reference_kind"] == "capability"
    assert error.context["referenced_id"] == "missing.capability"


def test_lad_runner_binding_must_require_runner_invoke() -> None:
    source = lad_execution.workflow_source()
    runner = _records(source, "runner_bindings")[0]
    runner["required_capability_ids"] = ()

    result = _compile_codex(source)

    assert result.plan is None
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    assert len(errors) == 1
    error = errors[0]
    assert error.code == "runner_binding_missing_runner_invoke"
    assert error.declaration_path == "runner_bindings[0].required_capability_ids"
    assert error.context["required_capability_kind"] == "runner.invoke"


def test_lad_external_route_graph_node_must_be_declared_by_graph() -> None:
    source = lad_execution.workflow_source()
    route = _records(source, "external_enqueue_routes")[0]
    route["graph_node_id"] = "execution.lad.missing.start"

    result = _compile_codex(source)

    assert result.plan is None
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    error = next(
        diagnostic
        for diagnostic in errors
        if diagnostic.declaration_path == "external_enqueue_routes[0].graph_node_id"
    )
    assert error.code == "missing_reference"
    assert error.declaration_path == "external_enqueue_routes[0].graph_node_id"
    assert error.context["reference_kind"] == "graph_node"
    assert error.context["referenced_id"] == "execution.lad.missing.start"


def test_lad_terminal_action_graph_node_must_be_declared_by_graph() -> None:
    source = lad_execution.workflow_source()
    action = _records(source, "terminal_actions")[0]
    action["target_graph_node_id"] = "execution.lad.missing.start"

    result = _compile_codex(source)

    assert result.plan is None
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    graph_errors = tuple(
        error for error in errors if error.declaration_path.endswith("graph_node_id")
    )
    assert len(graph_errors) == 1
    error = graph_errors[0]
    assert error.code == "missing_reference"
    assert error.declaration_path == "terminal_actions[0].target_graph_node_id"
    assert error.context["reference_kind"] == "graph_node"
    assert error.context["referenced_id"] == "execution.lad.missing.start"


def test_lad_recovery_route_graph_node_must_match_selected_stage() -> None:
    source = lad_execution.workflow_source()
    action = next(
        item
        for item in _records(source, "terminal_actions")
        if item["id"] == "execution.escalate_checker_fix_exhausted"
    )
    action["target_graph_node_id"] = "execution.lad.consultant.start"
    action_index = _records(source, "terminal_actions").index(action)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == "terminal_recovery_route_graph_node_stage_mismatch"
    )
    assert error.declaration_path == (
        f"terminal_actions[{action_index}].target_graph_node_id"
    )
    assert error.context["target_stage_kind_id"] == "lad_troubleshooter"
    assert error.context["graph_node_stage_kind_id"] == "lad_consultant"


def test_lad_dynamic_route_selector_requires_nonempty_field_names() -> None:
    source = lad_execution.workflow_source()
    action = next(
        item
        for item in _records(source, "terminal_actions")
        if item["id"] == "execution.return_troubleshooter_complete"
    )
    selector = cast(dict[str, object], action["dynamic_target_selector"])
    selector["field_names"] = ()
    action_index = _records(source, "terminal_actions").index(action)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    assert error.code == "invalid_dynamic_route_selector"
    assert error.declaration_path == (
        f"terminal_actions[{action_index}].dynamic_target_selector.field_names"
    )
    assert error.context["action_id"] == "execution.return_troubleshooter_complete"


def test_lad_dynamic_route_selector_target_must_match_route_contract() -> None:
    source = lad_execution.workflow_source()
    action = next(
        item
        for item in _records(source, "terminal_actions")
        if item["id"] == "execution.return_troubleshooter_complete"
    )
    selector = cast(dict[str, object], action["dynamic_target_selector"])
    targets = cast(dict[str, object], selector["targets"])
    builder_target = cast(dict[str, object], targets["builder"])
    builder_target["emitted_queue_family_id"] = "task"
    action_index = _records(source, "terminal_actions").index(action)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    assert error.code == "terminal_dynamic_route_target_mismatch"
    assert error.declaration_path == (
        f"terminal_actions[{action_index}].dynamic_target_selector.targets.builder"
    )
    assert error.context["action_id"] == "execution.return_troubleshooter_complete"
    assert error.context["target_name"] == "builder"


def test_lad_dynamic_route_selector_rejects_disallowed_target() -> None:
    source = lad_execution.workflow_source()
    action = next(
        item
        for item in _records(source, "terminal_actions")
        if item["id"] == "execution.route_consultant_complete"
    )
    selector = cast(dict[str, object], action["dynamic_target_selector"])
    targets = cast(dict[str, object], selector["targets"])
    targets["consultant"] = {
        "target_stage_kind_id": "lad_consultant",
        "target_graph_node_id": "execution.lad.consultant.start",
        "emitted_queue_family_id": "stage_result",
        "runner_binding_id": "execution.lad.local_runner",
    }
    action_index = _records(source, "terminal_actions").index(action)

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    assert error.code == "invalid_dynamic_route_selector"
    assert error.declaration_path == (
        f"terminal_actions[{action_index}].dynamic_target_selector.targets.consultant"
    )
    assert error.context["reason"] == "disallowed_target"
    assert error.context["target_name"] == "consultant"


def test_lad_dynamic_route_selector_graph_node_must_be_declared_by_graph() -> None:
    source = lad_execution.workflow_source()
    action = next(
        item
        for item in _records(source, "terminal_actions")
        if item["id"] == "execution.return_troubleshooter_complete"
    )
    selector = cast(dict[str, object], action["dynamic_target_selector"])
    targets = cast(dict[str, object], selector["targets"])
    builder_target = cast(dict[str, object], targets["builder"])
    builder_target["target_graph_node_id"] = "execution.lad.missing.start"
    action_index = _records(source, "terminal_actions").index(action)

    result = _compile_codex(source)

    assert result.plan is None
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    error = next(
        diagnostic for diagnostic in errors if diagnostic.code == "missing_reference"
    )
    assert error.declaration_path == (
        f"terminal_actions[{action_index}].dynamic_target_selector."
        "targets.builder.target_graph_node_id"
    )
    assert error.context["reference_kind"] == "graph_node"
    assert error.context["referenced_id"] == "execution.lad.missing.start"


def test_lad_graph_node_ids_are_the_graph_node_declaration_authority() -> None:
    source = lad_execution.workflow_source()
    graph = _records(source, "graphs")[0]
    graph["node_ids"] = tuple(
        node
        for node in cast(tuple[str, ...], graph["node_ids"])
        if node != "execution.lad.builder.start"
    )

    result = _compile_codex(source)

    assert result.plan is None
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    graph_errors = tuple(
        error
        for error in errors
        if error.context.get("referenced_id") == "execution.lad.builder.start"
    )
    assert graph_errors
    assert all(error.code == "missing_reference" for error in graph_errors)


@pytest.mark.parametrize(
    ("source_field", "source_value", "diagnostic_field"),
    (
        ("kind", "runner.self_grant", "kind"),
        ("support_status", "maybe", "support_status"),
        ("grant_status", "maybe", "grant_status"),
        ("approval_policy_id", "missing.policy", "approval_policy_id"),
    ),
)
def test_lad_unsupported_capability_values_are_rejected_by_compiler(
    source_field: str,
    source_value: str,
    diagnostic_field: str,
) -> None:
    source = lad_execution.workflow_source()
    capability = _records(source, "capabilities")[0]
    capability[source_field] = source_value

    result = _compile_codex(source)

    assert result.plan is None
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    assert len(errors) == 1
    error = errors[0]
    assert error.code == "unsupported_capability_value"
    assert error.declaration_path == f"capabilities[0].{source_field}"
    assert error.context["capability_id"] == "capability.runner.invoke"
    assert error.context["field_name"] == diagnostic_field


def test_lad_capability_policy_changes_participate_in_fingerprint() -> None:
    granted = _compile(lad_execution.workflow_source())
    denied_source = lad_execution.workflow_source()
    denied_capability = _records(denied_source, "capabilities")[0]
    denied_capability["grant_status"] = "denied"
    denied = _compile(denied_source)

    assert authority_fingerprint(granted) != authority_fingerprint(denied)
