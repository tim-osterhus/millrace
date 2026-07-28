from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from kernel.simple_loop_scenarios import (
    bootstrap_to_manager_claim,
    bootstrap_to_manager_cooldown_wait,
    bootstrap_to_manager_ready,
    bootstrap_to_reviewer_accepted,
    bootstrap_to_reviewer_ready,
)
from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.ids import RecoveryPolicyId
from millrace.contracts.state import (
    OperatorInterventionRecord,
    PlanRef,
    RuntimeState,
)
from millrace.contracts.transition import AdmitPlan, SelectDefaultPlan
from millrace.kernel import apply, empty_runtime_state
from millrace.operator import OperatorStatus, operator_status
from millrace.operator.status import (
    QueueFamilyStatus,
    RecentEventStatus,
    StageKindStatus,
)
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    deterministic_context,
    fake_runner_completion_input_id,
)
from millrace.workflows import simple_loop
from support.simple_loop import (
    apply_accepted_input,
    compile_simple_loop,
    runner_observation,
    simple_loop_context,
)

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def stage_kind_status_by_id(
    *,
    stage_kind_id: str,
    state: RuntimeState,
) -> StageKindStatus:
    status = operator_status(state)
    return next(
        stage for stage in status.stage_kinds if stage.stage_kind_id == stage_kind_id
    )


def queue_family_status_by_id(
    *,
    queue_family_id: str,
    status: OperatorStatus,
) -> QueueFamilyStatus:
    return next(
        family
        for family in status.queue_families
        if family.queue_family_id == queue_family_id
    )


def _events_by_input_id(
    events: Sequence[RecentEventStatus],
    input_id: str,
) -> tuple[RecentEventStatus, ...]:
    input_id = fake_runner_completion_input_id(input_id)
    return tuple(event for event in events if event.input_id == input_id)


def test_operator_status_projects_partitionless_stage_as_absence() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_ready(plan, fingerprint)

    status = operator_status(state)

    assert [partition.partition_id for partition in status.partitions] == [
        "implementation",
        "management",
        "review",
    ]
    assert {partition.partition_id for partition in status.partitions}.isdisjoint(
        {None, "None", "troubleshooter"}
    )
    assert [stage.stage_kind_id for stage in status.stage_kinds] == [
        "simple_loop.manager",
        "simple_loop.reviewer",
        "simple_loop.troubleshooter",
        "simple_loop.worker",
    ]
    partitions_by_stage = {
        stage.stage_kind_id: stage.partition_id for stage in status.stage_kinds
    }
    assert partitions_by_stage == {
        "simple_loop.manager": "management",
        "simple_loop.reviewer": "review",
        "simple_loop.troubleshooter": None,
        "simple_loop.worker": "implementation",
    }
    assert [family.queue_family_id for family in status.queue_families] == [
        "gap_packet",
        "incident_report",
        "work_packet",
        "work_prompt",
    ]
    assert {
        family.queue_family_id: family.external_enqueue
        for family in status.queue_families
    } == {
        "gap_packet": False,
        "incident_report": False,
        "work_packet": False,
        "work_prompt": True,
    }


def test_operator_status_projects_simple_loop_reviewer_close() -> None:
    plan, fingerprint = compile_simple_loop()
    reviewer_ready = bootstrap_to_reviewer_ready(plan, fingerprint)
    ready_status = operator_status(reviewer_ready)

    ready_work_packet = queue_family_status_by_id(
        queue_family_id="work_packet",
        status=ready_status,
    )
    assert (
        ready_work_packet.ready_count,
        ready_work_packet.active_count,
        ready_work_packet.closed_count,
        ready_work_packet.quarantined_count,
    ) == (1, 0, 0, 0)

    accepted = bootstrap_to_reviewer_accepted(plan, fingerprint)
    accepted_status = operator_status(accepted)
    accepted_work_packet = queue_family_status_by_id(
        queue_family_id="work_packet",
        status=accepted_status,
    )
    assert (
        accepted_work_packet.ready_count,
        accepted_work_packet.active_count,
        accepted_work_packet.closed_count,
        accepted_work_packet.quarantined_count,
    ) == (0, 0, 1, 0)
    assert accepted_status.active_runs == ()


def test_operator_status_projects_recovery_attempts() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)
    recovered = apply(
        state,
        decide(
            state,
            runner_observation(
                state=state,
                plan=plan,
                fingerprint=fingerprint,
                run_id="run-manager",
                action_id="simple_loop.manager.blocked",
                input_id="observe-manager-blocked",
                artifact_payload={},
            ),
            simple_loop_context("observe-manager-blocked"),
        ),
    )

    status = operator_status(recovered)

    assert len(status.recovery_attempts) == 1
    attempt = status.recovery_attempts[0]
    assert attempt.policy_id == "simple_loop.blocked_recovery"
    assert attempt.lineage_id == "work-prompt"
    assert attempt.plan_fingerprint == fingerprint
    assert attempt.attempt_count == 1
    assert attempt.phase == "active_recovery"
    assert attempt.source_run_id == "run-manager"
    assert attempt.source_work_item_id == "work-prompt"
    assert attempt.source_stage_kind_id == "simple_loop.manager"
    assert attempt.source_queue_family_id == "work_prompt"
    assert attempt.latest_recovery_activation_id == (
        "activation-troubleshooter-manager"
    )


def test_operator_status_projects_cooldown_waits() -> None:
    plan, fingerprint = compile_simple_loop()
    waiting = bootstrap_to_manager_cooldown_wait(
        plan,
        fingerprint,
        observed_at=1000,
    )

    status = operator_status(waiting)

    assert len(status.cooldown_waits) == 1
    wait = status.cooldown_waits[0]
    assert wait.policy_id == "simple_loop.blocked_recovery"
    assert wait.lineage_id == "work-prompt"
    assert wait.plan_fingerprint == fingerprint
    assert wait.attempt_count == 2
    assert wait.source_run_id == "run-manager-retry"
    assert wait.source_work_item_id == "work-prompt"
    assert wait.source_activation_id == "activation-returned-manager"
    assert wait.recovery_action_id == "simple_loop.manager.blocked"
    assert wait.target_stage_kind_id == "simple_loop.troubleshooter"
    assert wait.target_graph_node_id == "simple_loop.troubleshooter.start"
    assert wait.target_runner_binding_id == "simple_loop.default_agent_runner"
    assert wait.created_input_id == fake_runner_completion_input_id(
        "observe-manager-blocked-2"
    )
    assert wait.created_at == 1000
    assert wait.due_at == 1900
    assert wait.consumed_input_id is None
    assert wait.consumed_at is None
    assert wait.resulting_recovery_activation_id is None


def test_operator_status_projects_operator_interventions() -> None:
    plan_ref = PlanRef(
        plan_id="simple_loop:0.1",
        authority_fingerprint=f"sha256:{'a' * 64}",
        plan_format_version=SelectedCompiledPlan.schema_version,
    )
    record = OperatorInterventionRecord(
        record_id="operator-intervention:operator-revise-lineage",
        created_by_input_id="operator-revise-lineage",
        input_payload_digest=f"sha256:{'b' * 64}",
        option_id="simple_loop.revise_lineage",
        kind="revise_lineage",
        result="revised",
        policy_id=RecoveryPolicyId("simple_loop.blocked_recovery"),
        lineage_id="work-prompt",
        quarantine_id="lineage-quarantine:1",
        recovery_attempt_record_id="recovery-attempt:1",
        recovery_attempt_count=3,
        attempt_effect="resolve_attempt",
        selected_plan_ref=plan_ref,
        selected_plan_fingerprint=plan_ref.authority_fingerprint,
        actor_kind="local_operator",
        actor_id="local-operator-tim",
        reason="operator supplied revised packet",
        target_work_item_id="work-operator-revised-packet",
        target_activation_id="activation-operator-revised-worker",
        closed_work_item_ids=(),
        closed_activation_ids=(),
        closed_run_ids=(),
        payload_digest=f"sha256:{'c' * 64}",
        payload_reference="work_item:work-operator-revised-packet:payload",
    )
    state = RuntimeState(operator_interventions={record.record_id: record})

    status = operator_status(state)

    assert len(status.interventions) == 1
    intervention = status.interventions[0]
    assert intervention.record_id == record.record_id
    assert intervention.created_by_input_id == "operator-revise-lineage"
    assert intervention.input_payload_digest == f"sha256:{'b' * 64}"
    assert intervention.option_id == "simple_loop.revise_lineage"
    assert intervention.kind == "revise_lineage"
    assert intervention.result == "revised"
    assert intervention.policy_id == "simple_loop.blocked_recovery"
    assert intervention.lineage_id == "work-prompt"
    assert intervention.quarantine_id == "lineage-quarantine:1"
    assert intervention.recovery_attempt_record_id == "recovery-attempt:1"
    assert intervention.recovery_attempt_count == 3
    assert intervention.attempt_effect == "resolve_attempt"
    assert intervention.selected_plan_fingerprint == plan_ref.authority_fingerprint
    assert intervention.actor_kind == "local_operator"
    assert intervention.actor_id == "local-operator-tim"
    assert intervention.reason == "operator supplied revised packet"
    assert intervention.target_work_item_id == "work-operator-revised-packet"
    assert intervention.target_activation_id == "activation-operator-revised-worker"
    assert intervention.closed_work_item_ids == ()
    assert intervention.closed_activation_ids == ()
    assert intervention.closed_run_ids == ()
    assert intervention.payload_digest == f"sha256:{'c' * 64}"
    assert intervention.payload_reference == (
        "work_item:work-operator-revised-packet:payload"
    )


def test_operator_status_derives_metadata_and_active_runs_from_plan() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_manager_claim(plan, fingerprint)

    status = operator_status(state)

    assert status.selected_plan is not None
    assert status.selected_plan.workflow_id == "simple_loop"
    assert status.selected_plan.workflow_version == "0.1"
    assert status.selected_plan.workflow_name == "Simple Loop"
    assert status.selected_plan.authority_fingerprint == fingerprint
    assert {
        known_plan.authority_fingerprint: known_plan.selected_default
        for known_plan in status.known_plans
    } == {fingerprint: True}
    assert [family.queue_family_id for family in status.queue_families] == [
        "gap_packet",
        "incident_report",
        "work_packet",
        "work_prompt",
    ]
    assert [partition.partition_id for partition in status.partitions] == [
        "implementation",
        "management",
        "review",
    ]
    assert [stage.stage_kind_id for stage in status.stage_kinds] == [
        "simple_loop.manager",
        "simple_loop.reviewer",
        "simple_loop.troubleshooter",
        "simple_loop.worker",
    ]
    partitions_by_stage = {
        stage.stage_kind_id: stage.partition_id for stage in status.stage_kinds
    }
    assert partitions_by_stage["simple_loop.troubleshooter"] is None

    assert len(status.active_runs) == 1
    active_run = status.active_runs[0]
    assert active_run.run_id == "run-manager"
    assert active_run.work_item_id == "work-prompt"
    assert active_run.activation_id == "activation-manager"
    assert active_run.queue_family_id == "work_prompt"
    assert active_run.graph_node_id == "simple_loop.manager.start"
    assert active_run.stage_kind_id == "simple_loop.manager"
    assert active_run.runner_binding_id == "simple_loop.default_agent_runner"
    assert active_run.plan_fingerprint == fingerprint


def test_operator_status_recent_events_include_accepted_and_refused_context() -> None:
    plan, fingerprint = compile_simple_loop()
    state = bootstrap_to_reviewer_accepted(plan, fingerprint)
    duplicate = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-reviewer",
        action_id="simple_loop.reviewer.accepted",
        input_id="observe-reviewer-accepted-again",
        artifact_payload={},
        marker="ACCEPTED",
        observation_payload_overrides={"review_summary": "second observation"},
    )
    decision = decide(
        state,
        duplicate,
        deterministic_context(transition_id="transition-duplicate-reviewer"),
    )
    assert decision.accepted is False
    state = apply(state, decision)

    status = operator_status(state, max_events=8)

    accepted_events = _events_by_input_id(
        status.recent_events,
        "observe-reviewer-accepted",
    )
    refused_events = _events_by_input_id(
        status.recent_events,
        "observe-reviewer-accepted-again",
    )
    assert {(event.source, event.disposition) for event in accepted_events} == {
        ("governance_event", "accepted"),
        ("trace", "accepted"),
    }
    assert {
        (
            event.source,
            event.disposition,
            event.action_id,
            event.authority_source,
            event.refusal_reason,
            event.plan_fingerprint,
        )
        for event in refused_events
    } == {
        (
            "governance_event",
            "refused",
            None,
            None,
            "invalid_observation_authority",
            fingerprint,
        ),
        (
            "trace",
            "refused",
            None,
            None,
            "invalid_observation_authority",
            fingerprint,
        ),
    }


def test_operator_status_sorts_authority_without_partition_count_assumption() -> None:
    source = simple_loop.workflow_source()
    partitions = cast(Sequence[object], source["partitions"])
    stage_kinds = cast(Sequence[object], source["stage_kinds"])
    source["partitions"] = tuple(reversed(partitions))
    source["stage_kinds"] = tuple(reversed(stage_kinds))
    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)
    assert result.plan is not None
    fingerprint = authority_fingerprint(result.plan)
    state = empty_runtime_state()
    for transition_input in (
        AdmitPlan("admit", result.plan, fingerprint),
        SelectDefaultPlan("select", fingerprint),
    ):
        state = apply_accepted_input(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"transition-{transition_input.input_id}",
            ),
        )

    status = operator_status(state)

    assert [partition.partition_id for partition in status.partitions] == [
        "implementation",
        "management",
        "review",
    ]
    assert [stage.stage_kind_id for stage in status.stage_kinds] == [
        "simple_loop.manager",
        "simple_loop.reviewer",
        "simple_loop.troubleshooter",
        "simple_loop.worker",
    ]
    assert len(status.partitions) == 3
    assert len(status.stage_kinds) == 4
    assert any(stage.partition_id is None for stage in status.stage_kinds)
