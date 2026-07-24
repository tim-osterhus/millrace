from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Protocol, cast

from millrace.compiler import SelectedRunnerAdapterPolicy
from millrace.compiler import compile_workflow as _raw_compile_workflow
from millrace.compiler.canonical import (
    authority_fingerprint,
    canonical_authority_bytes,
)
from millrace.contracts import (
    ArtifactSchemaId,
    PartitionId,
    QueueFamilyId,
    RunnerBindingId,
    SelectedCompiledPlan,
    StageKindId,
    WorkflowId,
    WorkflowVersion,
)
from millrace.kernel.projection import ProjectionContext, evaluate_projection
from millrace.workflows import simple_loop

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _compile_codex(source: Mapping[str, object]):
    return _raw_compile_workflow(source, selected_runner_policy=_CODEX_POLICY)


class _HasId(Protocol):
    @property
    def id(self) -> object: ...


def _id_values(records: Iterable[_HasId]) -> set[str]:
    return {str(record.id) for record in records}


def _compiled_simple_loop_plan() -> SelectedCompiledPlan:
    result = _compile_codex(simple_loop.workflow_source())
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan


def test_simple_loop_fixture_compiles_as_non_lad_selected_authority() -> None:
    source = simple_loop.workflow_source()

    result = _compile_codex(source)

    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None

    plan = result.plan
    assert plan.workflow.workflow_id == WorkflowId("simple_loop")
    assert plan.workflow.workflow_version == WorkflowVersion("0.1")
    assert plan.workflow.workflow_name == "Simple Loop"
    assert plan.compatibility_profile is None
    assert plan.required_extensions == ()

    assert _id_values(plan.partitions) == {"management", "implementation", "review"}
    assert _id_values(plan.queue_families) == {
        "work_prompt",
        "work_packet",
        "gap_packet",
        "incident_report",
    }

    assert len(plan.external_enqueue_routes) == 1
    external_route = plan.external_enqueue_routes[0]
    assert external_route.queue_family_id == QueueFamilyId("work_prompt")
    assert external_route.stage_kind_id == StageKindId("simple_loop.manager")
    assert external_route.graph_node_id == "simple_loop.manager.start"
    assert external_route.payload_schema_id == ArtifactSchemaId(
        "simple_loop.work_prompt"
    )
    assert external_route.runner_binding_id == RunnerBindingId(
        "simple_loop.default_agent_runner"
    )

    stages = {str(stage.id): stage for stage in plan.stage_kinds}
    assert set(stages) == {
        "simple_loop.manager",
        "simple_loop.worker",
        "simple_loop.reviewer",
        "simple_loop.troubleshooter",
    }
    assert stages["simple_loop.manager"].partition_id == PartitionId("management")
    assert stages["simple_loop.worker"].partition_id == PartitionId("implementation")
    assert stages["simple_loop.reviewer"].partition_id == PartitionId("review")
    assert stages["simple_loop.troubleshooter"].partition_id is None

    assert all(outcome.marker for outcome in plan.terminal_outcomes)

    runner = plan.runner_bindings[0]
    assert runner.id == RunnerBindingId("simple_loop.default_agent_runner")
    assert {str(stage_id) for stage_id in runner.stage_kind_ids} == set(stages)

    actions_by_stage_outcome = {
        (str(action.stage_kind_id), str(action.outcome_id)): action
        for action in plan.terminal_actions
    }
    assert len(actions_by_stage_outcome) == len(plan.terminal_actions)
    for outcome in plan.terminal_outcomes:
        assert (
            str(outcome.stage_kind_id),
            str(outcome.id),
        ) in actions_by_stage_outcome

    assert {str(wait.id) for wait in plan.operator_waits} == {
        "simple_loop.manager_detail_wait",
        "simple_loop.manager_incident_wait",
    }
    operator_wait_actions = {
        str(action_id)
        for wait in plan.operator_waits
        for action_id in wait.source_action_ids
    }

    for action in plan.terminal_actions:
        assert action.action_kind != "lineage_update"
        if action.action_kind == "route":
            assert action.target_stage_kind_id is not None
            assert action.target_graph_node_id is not None
            assert action.emitted_queue_family_id is not None
            assert action.artifact_schema_id is not None
            assert action.runner_binding_id is not None
            assert action.payload_projection is not None
        if action.action_kind == "operator_wait":
            assert str(action.id) in operator_wait_actions
            assert action.emitted_queue_family_id is None
            assert action.payload_projection is None
        if action.action_kind == "recovery_route":
            assert action.target_stage_kind_id == StageKindId(
                "simple_loop.troubleshooter"
            )
            assert action.target_graph_node_id == "simple_loop.troubleshooter.start"
            assert action.runner_binding_id == RunnerBindingId(
                "simple_loop.default_agent_runner"
            )
            assert action.asset_ids
            assert action.emitted_queue_family_id is None
            assert action.artifact_schema_id is None
            assert action.payload_projection is None

    work_packet_schema = next(
        schema
        for schema in plan.artifact_schemas
        if schema.id == ArtifactSchemaId("simple_loop.work_packet")
    )
    required = cast(tuple[str, ...], work_packet_schema.schema["required"])
    properties = cast(dict[str, object], work_packet_schema.schema["properties"])
    assert "completion_definition" in required
    assert "completion_definition" in properties


def test_simple_loop_selected_recovery_policy_compiles_exact_authority() -> None:
    plan = _compiled_simple_loop_plan()
    actions = {str(action.id): action for action in plan.terminal_actions}

    assert len(plan.recovery_policies) == 1
    policy = plan.recovery_policies[0]

    assert str(policy.id) == "simple_loop.blocked_recovery"
    assert set(map(str, policy.source_recovery_action_ids)) == {
        "simple_loop.manager.blocked",
        "simple_loop.worker.blocked",
        "simple_loop.worker.failed",
        "simple_loop.reviewer.blocked",
    }
    assert all(
        actions[action_id].action_kind == "recovery_route"
        for action_id in map(str, policy.source_recovery_action_ids)
    )
    assert tuple(map(str, policy.return_action_ids)) == (
        "simple_loop.troubleshooter.resolved",
        "simple_loop.troubleshooter.unresolved",
    )
    assert all(
        actions[action_id].action_kind == "return_to_recorded_source"
        for action_id in map(str, policy.return_action_ids)
    )
    assert all(
        actions[action_id].artifact_schema_id
        == ArtifactSchemaId("simple_loop.troubleshooting_report")
        for action_id in map(str, policy.return_action_ids)
    )
    assert tuple(map(str, policy.quarantine_action_ids)) == (
        "simple_loop.troubleshooter.operator_needed",
    )
    assert (
        actions["simple_loop.troubleshooter.operator_needed"].action_kind
        == "quarantine_lineage"
    )
    assert actions[
        "simple_loop.troubleshooter.operator_needed"
    ].artifact_schema_id == ArtifactSchemaId("simple_loop.troubleshooting_report")
    assert str(policy.recovery_stage_kind_id) == "simple_loop.troubleshooter"
    assert policy.recorded_source_selector == "latest_recovery_attempt_for_lineage"
    assert policy.attempt_scope == "lineage"
    assert policy.immediate_recovery_limit == 1
    assert policy.cooldown_starts_at_attempt == 2
    assert policy.quarantine_threshold_attempt == 3
    assert policy.threshold_behavior == "runtime_quarantine_at_threshold"
    assert str(policy.cooldown_wait_state_id) == (
        "simple_loop.blocked_recovery.cooldown"
    )
    assert tuple(policy.return_allowed_phases) == (
        "active_recovery",
        "quarantine_eligible",
    )
    assert policy.default_cooldown_seconds == 900
    assert authority_fingerprint(
        replace(
            plan,
            recovery_policies=(
                replace(
                    policy,
                    default_cooldown_seconds=policy.default_cooldown_seconds + 1,
                ),
            ),
        )
    ) != authority_fingerprint(plan)
    assert tuple(map(str, policy.reset_trigger_action_ids)) == (
        "simple_loop.manager.packet_ready",
        "simple_loop.manager.incident_triaged",
        "simple_loop.manager.invalid_prompt",
        "simple_loop.worker.work_done",
        "simple_loop.worker.insufficient_spec",
        "simple_loop.reviewer.accepted",
        "simple_loop.reviewer.gaps_found",
        "simple_loop.reviewer.incident_required",
    )

    changed_threshold = replace(policy, quarantine_threshold_attempt=4)
    changed_scope = replace(policy, attempt_scope="source")
    changed_selector = replace(policy, recorded_source_selector="source_stage")
    changed_reset = replace(
        policy,
        reset_trigger_action_ids=policy.reset_trigger_action_ids[:-1],
    )
    for changed_policy in (
        changed_threshold,
        changed_scope,
        changed_selector,
        changed_reset,
    ):
        changed_plan = replace(plan, recovery_policies=(changed_policy,))
        assert authority_fingerprint(changed_plan) != authority_fingerprint(plan)


def test_simple_loop_selected_wait_counter_and_lineage_authority_compile() -> None:
    plan = _compiled_simple_loop_plan()

    assert getattr(plan, "lineage_policy") == "root_from_external_enqueue"

    wait_states = {str(wait.id): wait for wait in getattr(plan, "wait_states")}
    assert set(wait_states) == {"simple_loop.blocked_recovery.cooldown"}
    wait = wait_states["simple_loop.blocked_recovery.cooldown"]
    assert wait.wait_kind == "timer"
    assert str(wait.policy_id) == "simple_loop.blocked_recovery"
    assert wait.starts_at_attempt == 2
    assert wait.duration_seconds == 900

    counters = {str(counter.id): counter for counter in getattr(plan, "counters")}
    assert set(counters) == {"simple_loop.reviewer_gap_counter"}
    counter = counters["simple_loop.reviewer_gap_counter"]
    assert counter.counter_kind == "lineage_terminal_action_counter"
    assert counter.scope == "lineage"
    assert str(counter.stage_kind_id) == "simple_loop.reviewer"
    assert str(counter.increment_action_id) == "simple_loop.reviewer.gaps_found"
    assert str(counter.threshold_action_id) == (
        "simple_loop.reviewer.incident_required"
    )
    assert counter.threshold_count == 4

    assert authority_fingerprint(
        replace(
            plan,
            lineage_policy="none",
        )
    ) != authority_fingerprint(plan)
    assert authority_fingerprint(
        replace(
            plan,
            wait_states=(replace(wait, duration_seconds=wait.duration_seconds + 1),),
        )
    ) != authority_fingerprint(plan)
    assert authority_fingerprint(
        replace(
            plan,
            counters=(replace(counter, threshold_count=counter.threshold_count + 1),),
        )
    ) != authority_fingerprint(plan)


def test_simple_loop_selected_intervention_options_compile_exact_authority() -> None:
    plan = _compiled_simple_loop_plan()

    options = {
        str(option.id): option for option in getattr(plan, "intervention_options")
    }

    assert set(options) == {
        "simple_loop.resume_lineage",
        "simple_loop.close_lineage",
        "simple_loop.revise_lineage",
    }
    resume = options["simple_loop.resume_lineage"]
    assert str(resume.policy_id) == "simple_loop.blocked_recovery"
    assert resume.option_kind == "resume_lineage"
    assert resume.legal_source_state == "active_lineage_quarantine"
    assert resume.target_selector == (
        "selected_quarantine_or_active_quarantine_by_lineage"
    )
    assert resume.resume_target_selector == "recorded_source"
    assert resume.close_behavior is None
    assert resume.supersede_behavior == "supersede_quarantine"
    assert resume.attempt_effect == "resolve_attempt"
    assert resume.actor_kind == "local_operator"
    assert resume.audit_metadata_requirements == (
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

    revise = options["simple_loop.revise_lineage"]
    assert str(revise.policy_id) == "simple_loop.blocked_recovery"
    assert revise.option_kind == "revise_lineage"
    assert revise.legal_source_state == "active_lineage_quarantine"
    assert revise.target_selector == (
        "selected_quarantine_or_active_quarantine_by_lineage"
    )
    assert revise.resume_target_selector is None
    assert revise.close_behavior is None
    assert revise.payload_schema_id == ArtifactSchemaId("simple_loop.work_packet")
    assert revise.target_queue_family_id == QueueFamilyId("work_packet")
    assert revise.target_stage_kind_id == StageKindId("simple_loop.worker")
    assert revise.target_graph_node_id == "simple_loop.worker.start"
    assert revise.target_runner_binding_id == RunnerBindingId(
        "simple_loop.default_agent_runner"
    )
    assert revise.supersede_behavior == "supersede_quarantine"
    assert revise.attempt_effect == "resolve_attempt"
    assert revise.actor_kind == "local_operator"
    assert revise.audit_metadata_requirements == (
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

    close = options["simple_loop.close_lineage"]
    assert str(close.policy_id) == "simple_loop.blocked_recovery"
    assert close.option_kind == "close_lineage"
    assert close.legal_source_state == "active_lineage_quarantine"
    assert close.target_selector == (
        "selected_quarantine_or_active_quarantine_by_lineage"
    )
    assert close.resume_target_selector is None
    assert close.close_behavior == "close_ready_or_active_work_in_lineage"
    assert close.supersede_behavior == "supersede_quarantine"
    assert close.attempt_effect == "resolve_attempt"
    assert close.actor_kind == "local_operator"
    assert close.audit_metadata_requirements == (
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

    changed_options = (
        replace(resume, audit_metadata_requirements=("input_digest",)),
        revise,
        close,
    )
    assert authority_fingerprint(
        replace(plan, intervention_options=changed_options)
    ) != authority_fingerprint(plan)
    for changed_revise in (
        replace(
            revise,
            payload_schema_id=ArtifactSchemaId("simple_loop.detail_request"),
        ),
        replace(revise, target_queue_family_id=QueueFamilyId("gap_packet")),
        replace(
            revise,
            target_stage_kind_id=StageKindId("simple_loop.reviewer"),
        ),
        replace(revise, target_graph_node_id="simple_loop.reviewer.start"),
        replace(
            revise,
            target_runner_binding_id=RunnerBindingId("simple_loop.alternate_runner"),
        ),
        replace(revise, attempt_effect="leave_attempt_active"),
    ):
        assert authority_fingerprint(
            replace(plan, intervention_options=(resume, changed_revise, close))
        ) != authority_fingerprint(plan)
    assert b"intervention_options" in canonical_authority_bytes(plan)


def test_unselected_artifact_schema_does_not_affect_selected_authority() -> None:
    plan = _compiled_simple_loop_plan()
    source = simple_loop.workflow_source()
    artifact_schemas = cast(list[dict[str, object]], source["artifact_schemas"])
    artifact_schemas.append(
        {
            "id": "simple_loop.unused_operator_payload",
            "schema": {
                "type": "object",
                "required": ("artifact_kind",),
                "properties": {
                    "artifact_kind": {"const": "simple_loop.unused_operator_payload"}
                },
            },
            "presentation": {"display_name": "Unused operator payload"},
        }
    )

    result = _compile_codex(source)

    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    assert "simple_loop.unused_operator_payload" not in _id_values(
        result.plan.artifact_schemas
    )
    assert authority_fingerprint(result.plan) == authority_fingerprint(plan)


def test_simple_loop_route_projections_preserve_downstream_context() -> None:
    plan = _compiled_simple_loop_plan()
    actions = {str(action.id): action for action in plan.terminal_actions}
    source_prompt = {
        "prompt_id": "prompt-1",
        "body": "Preserve this exact sentence, including punctuation.",
    }
    work_packet = {
        "artifact_kind": "simple_loop.work_packet",
        "source_prompt_id": "prompt-1",
        "title": "Implement route context",
        "objective": "Preserve context for downstream stages",
        "completion_definition": "Reviewer can see packet and result context.",
    }
    work_result = {
        "artifact_kind": "simple_loop.work_result",
        "summary": "Implemented route context.",
    }
    detail_request = {
        "artifact_kind": "simple_loop.detail_request",
        "missing_details": ("Clarify target reviewer checks.",),
    }
    gap_packet = {
        "artifact_kind": "simple_loop.gap_packet",
        "gaps": ("Add route projection coverage.",),
    }
    incident_report = {
        "artifact_kind": "simple_loop.incident_report",
        "reason": "Repeated review failure.",
    }

    packet_ready = evaluate_projection(
        actions["simple_loop.manager.packet_ready"].payload_projection,
        ProjectionContext(
            work_item_payload=source_prompt,
            artifact_payload=work_packet,
            observation_payload={"marker": "PACKET_READY"},
            run_metadata={"run_id": "manager-run"},
            plan_metadata={"workflow_id": "simple_loop"},
        ),
    )
    assert packet_ready.accepted is True
    assert packet_ready.value == source_prompt | {"work_packet": work_packet}

    work_done = evaluate_projection(
        actions["simple_loop.worker.work_done"].payload_projection,
        ProjectionContext(
            work_item_payload=source_prompt | {"work_packet": work_packet},
            artifact_payload=work_result,
            observation_payload={"marker": "WORK_DONE"},
            run_metadata={"run_id": "worker-run"},
            plan_metadata={"workflow_id": "simple_loop"},
        ),
    )
    assert work_done.accepted is True
    assert work_done.value == source_prompt | {
        "work_packet": work_packet,
        "work_result": work_result,
    }

    insufficient_spec = evaluate_projection(
        actions["simple_loop.worker.insufficient_spec"].payload_projection,
        ProjectionContext(
            work_item_payload=source_prompt | {"work_packet": work_packet},
            artifact_payload=detail_request,
            observation_payload={"marker": "INSUFFICIENT_SPEC"},
            run_metadata={"run_id": "worker-run"},
            plan_metadata={"workflow_id": "simple_loop"},
        ),
    )
    assert insufficient_spec.accepted is True
    assert insufficient_spec.value == source_prompt | {
        "work_packet": work_packet,
        "detail_request": detail_request,
    }

    gaps_found = evaluate_projection(
        actions["simple_loop.reviewer.gaps_found"].payload_projection,
        ProjectionContext(
            work_item_payload={
                **source_prompt,
                "work_packet": work_packet,
                "work_result": work_result,
            },
            artifact_payload=gap_packet,
            observation_payload={"marker": "GAPS_FOUND"},
            run_metadata={"run_id": "reviewer-run"},
            plan_metadata={"workflow_id": "simple_loop"},
        ),
    )
    assert gaps_found.accepted is True
    assert gaps_found.value == source_prompt | {
        "work_packet": work_packet,
        "latest_work_result": work_result,
        "gap_packet": gap_packet,
    }

    incident_required = evaluate_projection(
        actions["simple_loop.reviewer.incident_required"].payload_projection,
        ProjectionContext(
            work_item_payload={
                **source_prompt,
                "work_packet": work_packet,
                "work_result": work_result,
            },
            artifact_payload=incident_report,
            observation_payload={"marker": "INCIDENT_REQUIRED"},
            run_metadata={"run_id": "reviewer-run"},
            plan_metadata={"workflow_id": "simple_loop"},
        ),
    )
    assert incident_required.accepted is True
    assert incident_required.value == source_prompt | {
        "work_packet": work_packet,
        "latest_work_result": work_result,
        "incident_report": incident_report,
    }

    packet_ready_action = actions["simple_loop.manager.packet_ready"]
    projection_without_prompt_body = {
        "kind": "object",
        "fields": {
            "prompt_id": {
                "kind": "source",
                "path": ("work_item_payload", "prompt_id"),
            },
            "work_packet": {
                "kind": "source",
                "path": ("artifact_payload",),
            },
        },
    }
    changed_actions = tuple(
        replace(
            action,
            payload_projection=projection_without_prompt_body,
        )
        if action.id == packet_ready_action.id
        else action
        for action in plan.terminal_actions
    )
    assert authority_fingerprint(
        replace(plan, terminal_actions=changed_actions)
    ) != authority_fingerprint(plan)


def test_simple_loop_compiled_schemas_require_nonblank_text() -> None:
    plan = _compiled_simple_loop_plan()
    schemas = {str(schema.id): schema.schema for schema in plan.artifact_schemas}

    work_packet_properties = cast(
        Mapping[str, Mapping[str, object]],
        schemas["simple_loop.work_packet"]["properties"],
    )
    for field_name in (
        "source_prompt_id",
        "title",
        "objective",
        "completion_definition",
    ):
        assert work_packet_properties[field_name] == {
            "type": "string",
            "min_length": 1,
        }

    work_result_properties = cast(
        Mapping[str, Mapping[str, object]],
        schemas["simple_loop.work_result"]["properties"],
    )
    assert work_result_properties["summary"] == {
        "type": "string",
        "min_length": 1,
    }

    incident_properties = cast(
        Mapping[str, Mapping[str, object]],
        schemas["simple_loop.incident_report"]["properties"],
    )
    assert incident_properties["reason"] == {
        "type": "string",
        "min_length": 1,
    }

    troubleshooting_properties = cast(
        Mapping[str, Mapping[str, object]],
        schemas["simple_loop.troubleshooting_report"]["properties"],
    )
    assert troubleshooting_properties["result"] == {
        "type": "string",
        "min_length": 1,
    }

    detail_properties = cast(
        Mapping[str, Mapping[str, object]],
        schemas["simple_loop.detail_request"]["properties"],
    )
    assert detail_properties["missing_details"] == {
        "type": "array",
        "min_items": 1,
        "items": {"type": "string", "min_length": 1},
    }

    gap_properties = cast(
        Mapping[str, Mapping[str, object]],
        schemas["simple_loop.gap_packet"]["properties"],
    )
    assert gap_properties["gaps"] == {
        "type": "array",
        "min_items": 1,
        "items": {"type": "string", "min_length": 1},
    }


def test_simple_loop_selected_authority_contains_no_hidden_defaults() -> None:
    result = _compile_codex(simple_loop.workflow_source())

    assert result.plan is not None
    rendered = canonical_authority_bytes(result.plan).decode("utf-8")

    for forbidden in (
        "kernel_ping",
        "craft",
        "task_artifact",
        "task_incident",
        "lad_codex",
        "execution",
        "planning",
        "learning",
    ):
        assert forbidden not in rendered

    for authored in (
        "management",
        "implementation",
        "review",
        "work_prompt",
        "work_packet",
        "gap_packet",
        "incident_report",
    ):
        assert authored in rendered
