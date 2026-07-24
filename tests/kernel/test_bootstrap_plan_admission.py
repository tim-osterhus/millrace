from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import Any, cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import (
    AdmittedPlan,
    OutcomeId,
    RunnerBindingDeclaration,
    RuntimeState,
    SelectedCompiledPlan,
)
from millrace.contracts.ids import (
    ActionId,
    ArtifactSchemaId,
    CounterId,
    OperatorWaitId,
    QueueFamilyId,
    RecoveryPolicyId,
    RunnerBindingId,
    StageKindId,
    WaitStateId,
)
from millrace.contracts.transition import (
    AdmitPlan,
    AdmitPlanRef,
    InitializeWorkspace,
    SelectDefaultPlan,
    TransitionDecision,
)
from millrace.kernel import UnsupportedMutationError, apply, decide, empty_runtime_state
from millrace.kernel.lookups import operator_wait_for_action, plan_ref_for
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping, lad_execution, simple_loop
from support import generic_fanout, vendor_selection
from support import kernel_ping as kernel_ping_support

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _compile_source(
    source: dict[str, object],
    *,
    codex_donor: bool = False,
) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(
        source,
        **({"selected_runner_policy": _CODEX_POLICY} if codex_donor else {}),
    )
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def _collapse_to_one_codex_runner(source: dict[str, object]) -> dict[str, object]:
    runner = cast(list[dict[str, object]], source["runner_bindings"])[0]
    runner["id"] = "kernel_ping.fake_local_runner"
    runner["adapter_kind"] = "codex"
    runner["stage_kind_ids"] = ("kernel_ping.taskmaster", "kernel_ping.worker")
    source["runner_bindings"] = [runner]
    for stage in cast(list[dict[str, object]], source["stage_kinds"]):
        stage["runner_binding_id"] = runner["id"]
    for route in cast(list[dict[str, object]], source["external_enqueue_routes"]):
        route["runner_binding_id"] = runner["id"]
    for action in cast(list[dict[str, object]], source["terminal_actions"]):
        if action.get("runner_binding_id") is not None:
            action["runner_binding_id"] = runner["id"]
    return runner


def _compile_component_plan() -> SelectedCompiledPlan:
    source = kernel_ping.workflow_source()
    source["capabilities"] = [
        {
            "id": "capability.runner.audit",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        },
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        },
    ]
    runner = _collapse_to_one_codex_runner(source)
    runner.update(
        {
            "adapter_kind": "codex",
            "required_capability_ids": (
                "capability.runner.audit",
                "capability.runner.invoke",
            ),
            "component_pin": {
                "component_kind": "opaque.runner",
                "component_id": "example.component",
                "component_version": "1.2.3",
                "provider_distribution": "example-provider",
                "provider_version": "4.5.6",
                "descriptor_media_type": "application/vnd.example.runner+json",
                "descriptor_sha256": "a" * 64,
                "required_capability_ids": (
                    "capability.runner.audit",
                    "capability.runner.invoke",
                ),
                "legal_terminal_result_ids": ("COMPLETE", "BLOCKED"),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "BLOCKED",
                    "outcome_id": "kernel_ping.taskmaster.blocked",
                },
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "COMPLETE",
                    "outcome_id": "kernel_ping.taskmaster.task_complete",
                },
            ),
        }
    )
    plan, _fingerprint = _compile_source(source)
    return plan


def _compile_component_free_capability_plan() -> SelectedCompiledPlan:
    source = kernel_ping.workflow_source()
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
    ]
    runner = _collapse_to_one_codex_runner(source)
    runner["required_capability_ids"] = ("capability.runner.invoke",)
    runner.pop("component_pin", None)
    runner.pop("terminal_result_mappings", None)
    plan, _fingerprint = _compile_source(source)
    assert plan.runner_bindings[0].component_pin is None
    return plan


def test_admit_plan_accepts_generic_runner_component_authority() -> None:
    plan = _compile_component_plan()
    fingerprint = authority_fingerprint(plan)

    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            "admit-component-plan",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-component-plan"),
    )

    assert decision.accepted is True
    assert decision.refusal is None


def test_direct_admit_plan_accepts_component_free_required_capability() -> None:
    plan = _compile_component_free_capability_plan()
    fingerprint = authority_fingerprint(plan)

    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            "admit-component-free-capability-plan",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(
            transition_id="transition-admit-component-free-capability-plan"
        ),
    )

    assert decision.accepted is True
    assert decision.refusal is None


@pytest.mark.parametrize("corruption", ("missing", "duplicate"))
def test_direct_admit_plan_refuses_component_free_capability_cardinality(
    corruption: str,
) -> None:
    plan = _compile_component_free_capability_plan()
    capability = plan.capabilities[0]
    object.__setattr__(
        plan,
        "capabilities",
        () if corruption == "missing" else (capability, capability),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "runner_component_capability:kernel_ping.fake_local_runner",
    )


@pytest.mark.parametrize(
    ("corruption", "detail"),
    (
        (
            "noncanonical_component_array",
            "runner_component_pin_noncanonical:kernel_ping.fake_local_runner",
        ),
        (
            "mapping_without_pin",
            "runner_component_mapping_without_pin:kernel_ping.fake_local_runner",
        ),
        (
            "duplicate_mapping",
            "runner_component_mapping_duplicate:kernel_ping.fake_local_runner",
        ),
        (
            "duplicate_target",
            "runner_component_mapping_outcome_duplicate:kernel_ping.fake_local_runner",
        ),
        (
            "unknown_result",
            "runner_component_mapping_result:kernel_ping.fake_local_runner",
        ),
        (
            "foreign_stage",
            "runner_component_mapping_stage:kernel_ping.fake_local_runner",
        ),
        (
            "foreign_outcome",
            "runner_component_mapping_outcome_stage:kernel_ping.fake_local_runner",
        ),
        (
            "missing_component_capability",
            "runner_component_capability:kernel_ping.fake_local_runner",
        ),
        (
            "duplicate_selected_capability",
            "runner_component_capability:kernel_ping.fake_local_runner",
        ),
        (
            "reordered_terminal_mappings",
            "runner_component_mapping_noncanonical:kernel_ping.fake_local_runner",
        ),
        (
            "noncanonical_component_capabilities",
            "runner_component_pin_noncanonical:kernel_ping.fake_local_runner",
        ),
        (
            "missing_outcome",
            "runner_component_mapping_outcome:kernel_ping.fake_local_runner",
        ),
        (
            "undeclared_outcome",
            "runner_component_mapping_outcome_declared:kernel_ping.fake_local_runner",
        ),
        (
            "missing_selected_capability",
            "runner_component_capability:kernel_ping.fake_local_runner",
        ),
    ),
)
def test_direct_admit_plan_refuses_invalid_runner_component_authority(
    corruption: str,
    detail: str,
) -> None:
    plan = _compile_component_plan()
    binding = plan.runner_bindings[0]
    pin = binding.component_pin
    assert pin is not None
    mapping = next(
        item
        for item in binding.terminal_result_mappings
        if item.runner_result_id == "COMPLETE"
    )
    if corruption == "noncanonical_component_array":
        object.__setattr__(
            pin,
            "legal_terminal_result_ids",
            ("COMPLETE", "BLOCKED"),
        )
    elif corruption == "mapping_without_pin":
        object.__setattr__(binding, "component_pin", None)
    elif corruption == "duplicate_mapping":
        object.__setattr__(
            binding,
            "terminal_result_mappings",
            (mapping, mapping),
        )
    elif corruption == "duplicate_target":
        object.__setattr__(
            mapping,
            "outcome_id",
            OutcomeId("kernel_ping.taskmaster.blocked"),
        )
    elif corruption == "unknown_result":
        object.__setattr__(mapping, "runner_result_id", "UNKNOWN")
    elif corruption == "foreign_stage":
        object.__setattr__(mapping, "stage_kind_id", StageKindId("kernel_ping.worker"))
        object.__setattr__(
            mapping,
            "outcome_id",
            OutcomeId("kernel_ping.worker.work_complete"),
        )
        object.__setattr__(
            binding,
            "stage_kind_ids",
            (StageKindId("kernel_ping.taskmaster"),),
        )
    elif corruption == "foreign_outcome":
        object.__setattr__(
            mapping,
            "outcome_id",
            OutcomeId("kernel_ping.worker.work_complete"),
        )
    elif corruption == "missing_component_capability":
        object.__setattr__(binding, "required_capability_ids", ())
    elif corruption == "duplicate_selected_capability":
        capability = plan.capabilities[0]
        object.__setattr__(plan, "capabilities", (capability, capability))
    elif corruption == "reordered_terminal_mappings":
        object.__setattr__(
            binding,
            "terminal_result_mappings",
            tuple(reversed(binding.terminal_result_mappings)),
        )
    elif corruption == "noncanonical_component_capabilities":
        object.__setattr__(
            pin,
            "required_capability_ids",
            tuple(reversed(pin.required_capability_ids)),
        )
    elif corruption == "missing_outcome":
        object.__setattr__(mapping, "outcome_id", OutcomeId("missing.outcome"))
    elif corruption == "undeclared_outcome":
        taskmaster = next(
            stage
            for stage in plan.stage_kinds
            if str(stage.id) == "kernel_ping.taskmaster"
        )
        object.__setattr__(
            taskmaster,
            "declared_outcome_ids",
            tuple(
                outcome_id
                for outcome_id in taskmaster.declared_outcome_ids
                if str(outcome_id) != "kernel_ping.taskmaster.blocked"
            ),
        )
    else:
        object.__setattr__(plan, "capabilities", (plan.capabilities[1],))

    _assert_admit_and_select_default_refuse_selected_authority(plan, detail)


def _plan_with_required_extensions(
    plan: SelectedCompiledPlan,
    required_extensions: list[str],
) -> SelectedCompiledPlan:
    return replace(plan, required_extensions=cast(Any, required_extensions))


def _plan_with_terminal_action_kind(
    plan: SelectedCompiledPlan,
    action_kind: str,
) -> SelectedCompiledPlan:
    terminal_actions = list(plan.terminal_actions)
    terminal_actions[0] = replace(
        terminal_actions[0],
        action_kind=action_kind,
    )
    return replace(plan, terminal_actions=tuple(terminal_actions))


def _plan_with_terminal_action_fields(
    plan: SelectedCompiledPlan,
    action_id: str,
    **field_values: object,
) -> SelectedCompiledPlan:
    terminal_actions = list(plan.terminal_actions)
    for index, action in enumerate(terminal_actions):
        if str(action.id) == action_id:
            terminal_actions[index] = replace(cast(Any, action), **field_values)
            return replace(plan, terminal_actions=tuple(terminal_actions))
    raise AssertionError(f"missing action {action_id!r}")


def _plan_with_terminal_action_artifact_schema(
    plan: SelectedCompiledPlan,
    action_id: str,
    artifact_schema_id: str,
) -> SelectedCompiledPlan:
    terminal_actions = list(plan.terminal_actions)
    for index, action in enumerate(terminal_actions):
        if str(action.id) == action_id:
            terminal_actions[index] = replace(
                action,
                artifact_schema_id=ArtifactSchemaId(artifact_schema_id),
            )
            return replace(plan, terminal_actions=tuple(terminal_actions))
    raise AssertionError(f"missing action {action_id!r}")


def _plan_with_external_route_payload_schema(
    plan: SelectedCompiledPlan,
    route_id: str,
    payload_schema_id: str,
) -> SelectedCompiledPlan:
    routes = list(plan.external_enqueue_routes)
    for index, route in enumerate(routes):
        if route.id == route_id:
            routes[index] = replace(
                route,
                payload_schema_id=ArtifactSchemaId(payload_schema_id),
            )
            return replace(plan, external_enqueue_routes=tuple(routes))
    raise AssertionError(f"missing external route {route_id!r}")


def _plan_with_dynamic_route_target(
    plan: SelectedCompiledPlan,
    action_id: str,
    target_name: str,
    target: dict[str, object],
) -> SelectedCompiledPlan:
    terminal_actions = list(plan.terminal_actions)
    for index, action in enumerate(terminal_actions):
        if str(action.id) != action_id:
            continue
        selector = dict(cast(dict[str, object], action.dynamic_target_selector))
        targets = dict(cast(dict[str, object], selector["targets"]))
        targets[target_name] = target
        selector["targets"] = targets
        terminal_actions[index] = replace(
            action,
            dynamic_target_selector=cast(Any, selector),
        )
        return replace(plan, terminal_actions=tuple(terminal_actions))
    raise AssertionError(f"missing action {action_id!r}")


def _plan_with_partitionless_stage(plan: SelectedCompiledPlan) -> SelectedCompiledPlan:
    stage_kinds = list(plan.stage_kinds)
    stage_kinds[0] = replace(stage_kinds[0], partition_id=cast(Any, None))
    return replace(plan, stage_kinds=tuple(stage_kinds))


def _plan_with_runner_adapter_kind(
    plan: SelectedCompiledPlan,
    adapter_kind: str,
) -> SelectedCompiledPlan:
    binding = plan.runner_bindings[0]
    replacement = RunnerBindingDeclaration(
        id=binding.id,
        adapter_kind=adapter_kind,
        stage_kind_ids=binding.stage_kind_ids,
        invocation_timeout_seconds=binding.invocation_timeout_seconds,
        presentation=binding.presentation,
        required_capability_ids=binding.required_capability_ids,
    )
    return replace(plan, runner_bindings=(replacement, *plan.runner_bindings[1:]))


def _plan_with_intervention_option_fields(
    plan: SelectedCompiledPlan,
    option_kind: str,
    **field_values: object,
) -> SelectedCompiledPlan:
    options = list(plan.intervention_options)
    for index, option in enumerate(options):
        if option.option_kind == option_kind:
            options[index] = replace(cast(Any, option), **field_values)
            return replace(plan, intervention_options=tuple(options))
    raise AssertionError(f"missing intervention option kind {option_kind!r}")


def _plan_with_recovery_policy_fields(
    plan: SelectedCompiledPlan,
    **field_values: object,
) -> SelectedCompiledPlan:
    policy = plan.recovery_policies[0]
    return replace(
        plan,
        recovery_policies=(replace(cast(Any, policy), **field_values),),
    )


def _plan_with_orphan_wait_state(plan: SelectedCompiledPlan) -> SelectedCompiledPlan:
    wait = plan.wait_states[0]
    return replace(
        plan,
        wait_states=(
            *plan.wait_states,
            replace(wait, id=WaitStateId("test.orphan_wait")),
        ),
    )


def _plan_with_missing_wait_policy(plan: SelectedCompiledPlan) -> SelectedCompiledPlan:
    wait = plan.wait_states[0]
    return replace(
        plan,
        wait_states=(replace(wait, policy_id=RecoveryPolicyId("test.missing_policy")),),
    )


def _plan_with_counter_stage_kind(
    plan: SelectedCompiledPlan,
    stage_kind_id: str,
) -> SelectedCompiledPlan:
    counter = plan.counters[0]
    return replace(
        plan,
        counters=(replace(counter, stage_kind_id=StageKindId(stage_kind_id)),),
    )


def _plan_with_duplicate_counter(
    plan: SelectedCompiledPlan,
    *,
    increment_action_id: str | None = None,
    threshold_action_id: str | None = None,
) -> SelectedCompiledPlan:
    counter = plan.counters[0]
    duplicate = replace(
        counter,
        id=CounterId(f"{counter.id}.duplicate"),
        increment_action_id=(
            ActionId(increment_action_id)
            if increment_action_id is not None
            else counter.increment_action_id
        ),
        threshold_action_id=(
            ActionId(threshold_action_id)
            if threshold_action_id is not None
            else counter.threshold_action_id
        ),
    )
    return replace(plan, counters=(*plan.counters, duplicate))


def _plan_with_operator_wait_fields(
    plan: SelectedCompiledPlan,
    wait_id: str,
    **field_values: object,
) -> SelectedCompiledPlan:
    operator_waits = list(plan.operator_waits)
    for index, operator_wait in enumerate(operator_waits):
        if str(operator_wait.id) == wait_id:
            operator_waits[index] = replace(cast(Any, operator_wait), **field_values)
            return replace(plan, operator_waits=tuple(operator_waits))
    raise AssertionError(f"missing operator wait {wait_id!r}")


def _plan_with_capability_fields(
    plan: SelectedCompiledPlan,
    **field_values: object,
) -> SelectedCompiledPlan:
    capabilities = list(plan.capabilities)
    capabilities[0] = replace(cast(Any, capabilities[0]), **field_values)
    return replace(plan, capabilities=tuple(capabilities))


def _plan_with_fanout_fields(
    plan: SelectedCompiledPlan,
    **field_values: object,
) -> SelectedCompiledPlan:
    fanout = plan.fanout_declarations[0]
    return replace(
        plan,
        fanout_declarations=(replace(cast(Any, fanout), **field_values),),
    )


def _plan_with_join_fields(
    plan: SelectedCompiledPlan,
    **field_values: object,
) -> SelectedCompiledPlan:
    join = plan.join_declarations[0]
    return replace(
        plan,
        join_declarations=(replace(cast(Any, join), **field_values),),
    )


def _plan_with_first_terminal_action_fields(
    plan: SelectedCompiledPlan,
    **field_values: object,
) -> SelectedCompiledPlan:
    terminal_actions = list(plan.terminal_actions)
    terminal_actions[0] = replace(
        cast(Any, terminal_actions[0]),
        **field_values,
    )
    return replace(plan, terminal_actions=tuple(terminal_actions))


def _plan_without_graph_node(
    plan: SelectedCompiledPlan,
    graph_node_id: str,
) -> SelectedCompiledPlan:
    graph = plan.graphs[0]
    return replace(
        plan,
        graphs=(
            replace(
                graph,
                node_ids=tuple(
                    node_id for node_id in graph.node_ids if node_id != graph_node_id
                ),
            ),
        ),
    )


def _plan_with_duplicate_operator_wait_owner(
    plan: SelectedCompiledPlan,
) -> SelectedCompiledPlan:
    duplicate = replace(
        plan.operator_waits[0],
        id=OperatorWaitId("test.duplicate_manager_detail_wait"),
    )
    return replace(plan, operator_waits=(*plan.operator_waits, duplicate))


def _state_with_admitted_plan(
    plan: SelectedCompiledPlan,
    fingerprint: str,
) -> RuntimeState:
    plan_ref = plan_ref_for(plan, fingerprint)
    return RuntimeState(
        admitted_plans={
            fingerprint: AdmittedPlan(
                plan_ref=plan_ref,
                selected_plan=plan,
            )
        }
    )


def _admit_plan_ref(decision: TransitionDecision) -> AdmitPlanRef:
    return next(
        mutation
        for mutation in decision.mutations
        if isinstance(mutation, AdmitPlanRef)
    )


def _assert_admit_and_select_default_refuse_selected_authority(
    plan: SelectedCompiledPlan,
    detail: str,
) -> None:
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()

    admit_decision = decide(
        state,
        AdmitPlan(
            f"admit-{detail}",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id=f"transition-admit-{detail}"),
    )

    assert admit_decision.accepted is False
    assert admit_decision.refusal is not None
    assert admit_decision.refusal.reason == "unsupported_selected_authority"
    assert admit_decision.refusal.detail == detail

    after_admit_refusal = apply(state, admit_decision)
    assert fingerprint not in after_admit_refusal.admitted_plans
    assert after_admit_refusal.default_plan_ref is None
    assert after_admit_refusal.receipts[f"admit-{detail}"].accepted is False

    duplicate_admit_decision = decide(
        _state_with_admitted_plan(plan, fingerprint),
        AdmitPlan(
            f"duplicate-admit-{detail}",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id=f"transition-duplicate-admit-{detail}"),
    )

    assert duplicate_admit_decision.accepted is False
    assert duplicate_admit_decision.refusal is not None
    assert duplicate_admit_decision.refusal.reason == "unsupported_selected_authority"
    assert duplicate_admit_decision.refusal.detail == detail

    admitted_state = _state_with_admitted_plan(plan, fingerprint)
    select_decision = decide(
        admitted_state,
        SelectDefaultPlan(
            f"select-{detail}",
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id=f"transition-select-{detail}"),
    )

    assert select_decision.accepted is False
    assert select_decision.refusal is not None
    assert select_decision.refusal.reason == "unsupported_selected_authority"
    assert select_decision.refusal.detail == detail

    after_select_refusal = apply(admitted_state, select_decision)
    assert after_select_refusal.admitted_plans == admitted_state.admitted_plans
    assert after_select_refusal.default_plan_ref is None
    assert after_select_refusal.receipts[f"select-{detail}"].accepted is False


def test_initialize_admit_and_select_default_plan_use_control_authority() -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    state = empty_runtime_state()

    initialize = InitializeWorkspace("init")
    init_decision = decide(
        state,
        initialize,
        deterministic_context(transition_id="transition-init"),
    )
    assert init_decision.accepted is True
    assert init_decision.input_family == "control"
    assert init_decision.input_kind == InitializeWorkspace.input_kind

    initialized = apply(state, init_decision)
    assert initialized.admitted_plans == {}
    assert initialized.default_plan_ref is None
    assert initialized.work_items == {}
    assert initialized.activations == {}
    assert initialized.runs == {}

    admit = AdmitPlan(
        "admit-plan",
        selected_plan=plan,
        authority_fingerprint=fingerprint,
    )
    admit_decision = decide(
        initialized,
        admit,
        deterministic_context(transition_id="transition-admit"),
    )
    assert admit_decision.accepted is True
    admitted = apply(initialized, admit_decision)

    assert set(admitted.admitted_plans) == {fingerprint}
    admitted_plan = admitted.admitted_plans[fingerprint]
    assert admitted_plan.plan_ref.authority_fingerprint == fingerprint
    assert admitted_plan.plan_ref.plan_format_version == plan.schema_version
    assert admitted_plan.selected_plan == plan
    with pytest.raises(FrozenInstanceError):
        setattr(admitted_plan.plan_ref, "authority_fingerprint", "sha256:changed")

    select = SelectDefaultPlan("select-plan", authority_fingerprint=fingerprint)
    select_decision = decide(
        admitted,
        select,
        deterministic_context(transition_id="transition-select"),
    )
    assert select_decision.accepted is True
    selected = apply(admitted, select_decision)
    assert selected.default_plan_ref == admitted_plan.plan_ref


def test_fanout_blank_item_id_key_refuses_selected_plan_admission() -> None:
    plan, _fingerprint = generic_fanout.compile_fanout()
    tampered = _plan_with_fanout_fields(plan, item_id_key="")

    _assert_admit_and_select_default_refuse_selected_authority(
        tampered,
        "fanout_item_selector:fanout.packet.children",
    )


def test_fanout_non_closing_source_action_refuses_selected_plan_admission() -> None:
    plan, _fingerprint = generic_fanout.compile_fanout()
    tampered = generic_fanout.plan_with_valid_route_source_action(plan)

    _assert_admit_and_select_default_refuse_selected_authority(
        tampered,
        "fanout_source_action_kind:fanout.packet.children",
    )


def test_join_without_target_generated_route_refuses_selected_plan_admission() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    tampered = replace(
        plan,
        generated_work_routes=tuple(
            route
            for route in plan.generated_work_routes
            if route.id != "vendor_selection.award_join_work"
        ),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        tampered,
        "join_target_route:candidate_evidence_join",
    )


def test_duplicate_join_target_route_refuses_selected_plan_admission() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    target_route = next(
        route
        for route in plan.generated_work_routes
        if route.id == "vendor_selection.award_join_work"
    )
    tampered = replace(
        plan,
        generated_work_routes=(
            *plan.generated_work_routes,
            replace(cast(Any, target_route), id="vendor_selection.award_join_work.v2"),
        ),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        tampered,
        "join_target_route:candidate_evidence_join",
    )


def test_join_stage_schema_mismatch_refuses_selected_plan_admission() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    tampered = _plan_with_join_fields(
        plan,
        required_artifact_schema_ids=(ArtifactSchemaId("OperatorDecision"),),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        tampered,
        "join_required_stage_schema:candidate_evidence_join",
    )


def test_join_id_collision_refuses_selected_plan_admission() -> None:
    plan, _fingerprint = vendor_selection.compile_vendor_selection()
    tampered = _plan_with_join_fields(
        plan,
        id="vendor_selection.request_intake.request_ready",
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        tampered,
        "join_id_collision:vendor_selection.request_intake.request_ready",
    )


def test_admit_plan_refuses_mismatched_fingerprint_before_plan_pin() -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    mismatched_fingerprint = f"{fingerprint}-mismatch"
    state = empty_runtime_state()

    decision = decide(
        state,
        AdmitPlan(
            "admit-mismatch",
            selected_plan=plan,
            authority_fingerprint=mismatched_fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-mismatch"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "plan_fingerprint_mismatch"

    after_refusal = apply(state, decision)
    assert after_refusal.admitted_plans == {}
    assert after_refusal.default_plan_ref is None
    assert mismatched_fingerprint not in after_refusal.admitted_plans


def test_admit_decision_retains_frozen_plan_after_caller_list_mutation() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    required_extensions = ["ext.alpha"]
    plan = _plan_with_required_extensions(source_plan, required_extensions)
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()

    decision = decide(
        state,
        AdmitPlan(
            "admit-mutable-plan",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-mutable-plan"),
    )
    assert decision.accepted is True

    required_extensions.append("ext.beta")

    assert authority_fingerprint(_admit_plan_ref(decision).selected_plan) == fingerprint


def test_admitted_plan_pin_retains_frozen_plan_after_caller_list_mutation() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    required_extensions = ["ext.alpha"]
    plan = _plan_with_required_extensions(source_plan, required_extensions)
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()

    admitted = apply(
        state,
        decide(
            state,
            AdmitPlan(
                "admit-mutable-plan",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            deterministic_context(transition_id="transition-admit-mutable-plan"),
        ),
    )

    required_extensions.append("ext.beta")

    assert (
        authority_fingerprint(admitted.admitted_plans[fingerprint].selected_plan)
        == fingerprint
    )


def test_apply_revalidates_admit_plan_ref_before_pinning() -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    state = empty_runtime_state()
    decision = decide(
        state,
        AdmitPlan("admit-plan", selected_plan=plan, authority_fingerprint=fingerprint),
        deterministic_context(transition_id="transition-admit"),
    )
    assert decision.accepted is True
    valid_admit_ref = _admit_plan_ref(decision)
    tampered_plan = replace(plan, required_extensions=("ext.tampered",))
    tampered_admit_ref = AdmitPlanRef(
        plan_ref=valid_admit_ref.plan_ref,
        selected_plan=tampered_plan,
    )
    tampered_decision = replace(
        decision,
        mutations=tuple(
            tampered_admit_ref if isinstance(mutation, AdmitPlanRef) else mutation
            for mutation in decision.mutations
        ),
    )

    with pytest.raises(UnsupportedMutationError, match="plan fingerprint mismatch"):
        apply(state, tampered_decision)

    assert state.admitted_plans == {}


def test_admit_plan_refuses_unsupported_runtime_terminal_action_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    plan = _plan_with_terminal_action_kind(source_plan, "operator_wait")
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()

    decision = decide(
        state,
        AdmitPlan(
            "admit-unsupported-action",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-unsupported-action"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"

    admitted = apply(state, decision)
    assert fingerprint not in admitted.admitted_plans
    assert admitted.default_plan_ref is None
    assert admitted.receipts["admit-unsupported-action"].accepted is False
    assert admitted.refusals[-1].detail == (
        "operator_wait_missing_authority:kernel_ping.close_worker_success"
    )


def test_admit_and_select_default_refuse_old_terminal_action_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    plan = _plan_with_terminal_action_fields(
        source_plan,
        "execution.close_consultant_needs_plan",
        action_kind="escalate_to_planning",
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "terminal_action_kind:escalate_to_planning",
    )


def test_admit_and_select_default_refuse_deferred_terminal_action_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    plan = _plan_with_terminal_action_kind(
        source_plan,
        "deferred_terminal_action",
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "terminal_action_kind:deferred_terminal_action",
    )


def test_admit_and_select_default_accept_opaque_runner_adapter_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    plan = _plan_with_runner_adapter_kind(source_plan, "opaque_local")
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()

    admit_decision = decide(
        state,
        AdmitPlan(
            "admit-opaque-runner",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-opaque-runner"),
    )
    assert admit_decision.accepted is True, admit_decision.refusal

    admitted = apply(state, admit_decision)
    admitted_plan = admitted.admitted_plans[fingerprint].selected_plan
    assert admitted_plan.runner_bindings[0].adapter_kind == "opaque_local"

    select_decision = decide(
        admitted,
        SelectDefaultPlan(
            "select-opaque-runner",
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-select-opaque-runner"),
    )
    assert select_decision.accepted is True, select_decision.refusal

    selected = apply(admitted, select_decision)
    assert selected.default_plan_ref == admitted.admitted_plans[fingerprint].plan_ref


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("target_stage_kind_id", StageKindId("lad_consultant")),
        ("target_graph_node_id", "execution.lad.consultant.start"),
        ("emitted_queue_family_id", QueueFamilyId("stage_result")),
        ("runner_binding_id", RunnerBindingId("execution.lad.local_runner")),
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
def test_admit_and_select_default_refuse_close_with_escalation_route_authority(
    field_name: str,
    field_value: object,
) -> None:
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    plan = _plan_with_terminal_action_fields(
        source_plan,
        "execution.close_consultant_needs_plan",
        action_kind="close_with_escalation",
        **{field_name: field_value},
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        (
            "terminal_close_with_escalation_route_authority:"
            f"execution.close_consultant_needs_plan.{field_name}"
        ),
    )


def test_admit_plan_refuses_terminal_action_artifact_schema_mismatch() -> None:
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    plan = _plan_with_terminal_action_artifact_schema(
        source_plan,
        "execution.close_updater_complete",
        "execution.artifacts.incident_report",
    )
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()

    decision = decide(
        state,
        AdmitPlan(
            "admit-artifact-mismatch",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-artifact-mismatch"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        "terminal_action_artifact_schema:execution.close_updater_complete"
    )

    after = apply(state, decision)
    assert fingerprint not in after.admitted_plans
    assert after.default_plan_ref is None
    assert after.receipts["admit-artifact-mismatch"].accepted is False


def test_admit_plan_refuses_disallowed_dynamic_route_target() -> None:
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    plan = _plan_with_dynamic_route_target(
        source_plan,
        "execution.route_consultant_complete",
        "consultant",
        {
            "target_stage_kind_id": "lad_consultant",
            "target_graph_node_id": "execution.lad.consultant.start",
            "emitted_queue_family_id": "stage_result",
            "runner_binding_id": "execution.lad.local_runner",
        },
    )
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()

    decision = decide(
        state,
        AdmitPlan(
            "admit-disallowed-dynamic-route",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(
            transition_id="transition-admit-disallowed-dynamic-route"
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == (
        "dynamic_route_disallowed_target:execution.route_consultant_complete"
    )

    after = apply(state, decision)
    assert fingerprint not in after.admitted_plans
    assert after.default_plan_ref is None
    assert after.receipts["admit-disallowed-dynamic-route"].accepted is False


def test_admit_plan_refuses_existing_unsupported_runtime_terminal_action_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    plan = _plan_with_terminal_action_kind(source_plan, "operator_wait")
    fingerprint = authority_fingerprint(plan)
    state = _state_with_admitted_plan(plan, fingerprint)

    decision = decide(
        state,
        AdmitPlan(
            "admit-existing-unsupported-action",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(
            transition_id="transition-admit-existing-unsupported-action"
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"

    admitted = apply(state, decision)
    assert admitted.admitted_plans == state.admitted_plans
    assert admitted.default_plan_ref is None
    assert admitted.receipts["admit-existing-unsupported-action"].accepted is False
    assert admitted.refusals[-1].detail == (
        "operator_wait_missing_authority:kernel_ping.close_worker_success"
    )


def test_admit_plan_refuses_existing_deferred_terminal_action_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    plan = _plan_with_terminal_action_kind(
        source_plan,
        "deferred_terminal_action",
    )
    fingerprint = authority_fingerprint(plan)
    state = _state_with_admitted_plan(plan, fingerprint)

    decision = decide(
        state,
        AdmitPlan(
            "admit-existing-deferred-action",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-existing-deferred"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == "terminal_action_kind:deferred_terminal_action"

    admitted = apply(state, decision)
    assert admitted.admitted_plans == state.admitted_plans
    assert admitted.default_plan_ref is None
    assert admitted.receipts["admit-existing-deferred-action"].accepted is False


def test_select_default_refuses_unsupported_runtime_terminal_action_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    plan = _plan_with_terminal_action_kind(source_plan, "operator_wait")
    fingerprint = authority_fingerprint(plan)
    state = _state_with_admitted_plan(plan, fingerprint)

    decision = decide(
        state,
        SelectDefaultPlan(
            "select-unsupported-action",
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-select-unsupported-action"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"

    selected = apply(state, decision)
    assert selected.admitted_plans == state.admitted_plans
    assert selected.default_plan_ref is None
    assert selected.receipts["select-unsupported-action"].accepted is False
    assert selected.refusals[-1].detail == (
        "operator_wait_missing_authority:kernel_ping.close_worker_success"
    )


def test_select_default_refuses_deferred_terminal_action_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    plan = _plan_with_terminal_action_kind(
        source_plan,
        "deferred_terminal_action",
    )
    fingerprint = authority_fingerprint(plan)
    state = _state_with_admitted_plan(plan, fingerprint)

    decision = decide(
        state,
        SelectDefaultPlan(
            "select-deferred-action",
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-select-deferred"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "unsupported_selected_authority"
    assert decision.refusal.detail == "terminal_action_kind:deferred_terminal_action"

    selected = apply(state, decision)
    assert selected.admitted_plans == state.admitted_plans
    assert selected.default_plan_ref is None
    assert selected.receipts["select-deferred-action"].accepted is False


def test_admit_plan_accepts_partitionless_stage_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    plan = _plan_with_partitionless_stage(source_plan)
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()

    decision = decide(
        state,
        AdmitPlan(
            "admit-partitionless",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-partitionless"),
    )

    assert decision.accepted is True
    assert decision.refusal is None

    admitted = apply(state, decision)
    assert admitted.admitted_plans[fingerprint].selected_plan == plan
    assert admitted.default_plan_ref is None
    assert admitted.receipts["admit-partitionless"].accepted is True
    assert admitted.refusals == ()


def test_select_default_accepts_partitionless_stage_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    plan = _plan_with_partitionless_stage(source_plan)
    fingerprint = authority_fingerprint(plan)
    state = _state_with_admitted_plan(plan, fingerprint)

    decision = decide(
        state,
        SelectDefaultPlan(
            "select-partitionless",
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-select-partitionless"),
    )

    assert decision.accepted is True
    assert decision.refusal is None

    selected = apply(state, decision)
    assert selected.admitted_plans == state.admitted_plans
    assert selected.default_plan_ref == state.admitted_plans[fingerprint].plan_ref
    assert selected.receipts["select-partitionless"].accepted is True
    assert selected.refusals == ()


def test_admit_simple_loop_accepts_full_selected_authority() -> None:
    plan, fingerprint = _compile_source(simple_loop.workflow_source(), codex_donor=True)

    decision = decide(
        empty_runtime_state(),
        AdmitPlan(
            "admit-simple-loop",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-simple-loop"),
    )

    assert decision.accepted is True
    assert decision.refusal is None


@pytest.mark.parametrize(
    ("field_values", "detail"),
    (
        (
            {"capability_kind": "shell.run"},
            "capability_kind:capability.runner.invoke",
        ),
        (
            {"support_status": "maybe"},
            "capability_support_status:capability.runner.invoke",
        ),
        (
            {"grant_status": "maybe"},
            "capability_grant_status:capability.runner.invoke",
        ),
        (
            {"approval_policy_id": "policy.future"},
            "capability_approval_policy:capability.runner.invoke",
        ),
    ),
)
def test_admit_and_select_default_refuse_unsupported_capability_authority(
    field_values: dict[str, object],
    detail: str,
) -> None:
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    plan = _plan_with_capability_fields(source_plan, **field_values)

    _assert_admit_and_select_default_refuse_selected_authority(plan, detail)


def test_admit_and_select_default_refuse_unknown_route_graph_node() -> None:
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    plan = _plan_without_graph_node(source_plan, "execution.lad.builder.start")

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "graph_node_missing:execution.lad.builder.start",
    )


def test_admit_and_select_default_refuse_unknown_external_route_payload_schema() -> (
    None
):
    source_plan, _source_fingerprint = _compile_source(kernel_ping.workflow_source())
    route_id = source_plan.external_enqueue_routes[0].id
    plan = _plan_with_external_route_payload_schema(
        source_plan,
        route_id,
        "missing.external.payload",
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        f"external_enqueue_route_payload_schema:{route_id}",
    )


def test_admit_and_select_default_refuse_duplicate_operator_wait_owner() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_duplicate_operator_wait_owner(source_plan)

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "operator_wait_duplicate_owner:simple_loop.manager.needs_operator_detail",
    )


def test_operator_wait_lookup_refuses_duplicate_owner() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_duplicate_operator_wait_owner(source_plan)

    with pytest.raises(ValueError, match="ambiguous operator wait authority"):
        operator_wait_for_action(
            plan,
            "simple_loop.manager.needs_operator_detail",
        )


def test_admit_and_select_default_refuse_duplicate_operator_wait_action() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    source_action_id = source_plan.operator_waits[0].source_action_ids[0]
    plan = _plan_with_operator_wait_fields(
        source_plan,
        "simple_loop.manager_detail_wait",
        source_action_ids=(source_action_id, source_action_id),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "operator_wait_source_action:simple_loop.manager_detail_wait",
    )


def test_admit_and_select_default_refuse_empty_operator_wait_source_actions() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_operator_wait_fields(
        source_plan,
        "simple_loop.manager_detail_wait",
        source_action_ids=(),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "operator_wait_source_action:simple_loop.manager_detail_wait",
    )


@pytest.mark.parametrize(
    ("allowed_resolution_kinds", "detail"),
    (
        ((), "operator_wait_resolution_kind:simple_loop.manager_detail_wait"),
        (
            ("resume_recorded_source", "resume_recorded_source"),
            "operator_wait_resolution_kind:simple_loop.manager_detail_wait",
        ),
    ),
)
def test_admit_and_select_default_refuse_invalid_operator_wait_resolution_sets(
    allowed_resolution_kinds: tuple[str, ...],
    detail: str,
) -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_operator_wait_fields(
        source_plan,
        "simple_loop.manager_detail_wait",
        allowed_resolution_kinds=allowed_resolution_kinds,
        audit_metadata_requirements=(),
    )

    _assert_admit_and_select_default_refuse_selected_authority(plan, detail)


@pytest.mark.parametrize(
    ("field_values", "detail"),
    (
        (
            {"wait_scope": "workspace"},
            "operator_wait_scope:simple_loop.manager_detail_wait",
        ),
        (
            {"source_work_item_behavior": "pause"},
            "operator_wait_source_work_item_behavior:simple_loop.manager_detail_wait",
        ),
        (
            {"unrelated_lineages_continue": False},
            "operator_wait_unrelated_lineages_continue:simple_loop.manager_detail_wait",
        ),
        (
            {"actor_kind": "remote_operator"},
            "operator_wait_actor_kind:simple_loop.manager_detail_wait",
        ),
        (
            {"audit_metadata_requirements": ()},
            "operator_wait_audit_metadata_requirements:simple_loop.manager_detail_wait",
        ),
        (
            {"correlation_key": "lineage_id"},
            "operator_wait_correlation_key:simple_loop.manager_detail_wait",
        ),
        (
            {"idempotency": "none"},
            "operator_wait_idempotency:simple_loop.manager_detail_wait",
        ),
        (
            {"timeout_policy": "fifteen_minutes"},
            "operator_wait_timeout_policy:simple_loop.manager_detail_wait",
        ),
        (
            {"expiry_policy": "after_timeout"},
            "operator_wait_expiry_policy:simple_loop.manager_detail_wait",
        ),
        (
            {"cancellation_policy": "runtime_cancel"},
            "operator_wait_cancellation_policy:simple_loop.manager_detail_wait",
        ),
        (
            {"status_effect": "blocked"},
            "operator_wait_status_effect:simple_loop.manager_detail_wait",
        ),
    ),
)
def test_admit_and_select_default_refuse_operator_wait_fixed_policy_fields(
    field_values: dict[str, object],
    detail: str,
) -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_operator_wait_fields(
        source_plan,
        "simple_loop.manager_detail_wait",
        **field_values,
    )

    _assert_admit_and_select_default_refuse_selected_authority(plan, detail)


@pytest.mark.parametrize(
    "resolution_kind",
    ("resume_recorded_source", "revise_recorded_source"),
)
def test_admit_and_select_default_refuse_close_on_create_non_close_resolution(
    resolution_kind: str,
) -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_operator_wait_fields(
        source_plan,
        "simple_loop.manager_incident_wait",
        allowed_resolution_kinds=("close_recorded_source", resolution_kind),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "operator_wait_source_work_item_behavior:simple_loop.manager_incident_wait",
    )


def test_admit_and_select_default_refuse_operator_wait_revise_fields() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    detail_wait = source_plan.operator_waits[0]
    plan = _plan_with_operator_wait_fields(
        source_plan,
        "simple_loop.manager_incident_wait",
        payload_schema_id=detail_wait.payload_schema_id,
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "operator_wait_target:simple_loop.manager_incident_wait",
    )


def test_admit_and_select_default_refuse_operator_wait_missing_revise_target() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_operator_wait_fields(
        source_plan,
        "simple_loop.manager_detail_wait",
        target_runner_binding_id=None,
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "operator_wait_target:simple_loop.manager_detail_wait",
    )


def test_admit_and_select_default_refuse_operator_wait_revise_route_schema_mismatch() -> None:  # noqa: E501
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_external_route_payload_schema(
        source_plan,
        route_id="simple_loop.external_work_prompt",
        payload_schema_id="simple_loop.detail_request",
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "operator_wait_target:simple_loop.manager_detail_wait",
    )


def test_admit_and_select_default_refuse_mutated_resume_target_selector() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_intervention_option_fields(
        source_plan,
        "resume_lineage",
        resume_target_selector="latest_active_recovery_activation",
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "intervention_option_resume_target_selector:simple_loop.resume_lineage",
    )


def test_admit_and_select_default_refuse_mutated_close_behavior() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_intervention_option_fields(
        source_plan,
        "close_lineage",
        close_behavior="close_everything_in_workspace",
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "intervention_option_close_behavior:simple_loop.close_lineage",
    )


def test_admit_and_select_default_refuse_truncated_audit_requirements() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    option = next(
        item
        for item in source_plan.intervention_options
        if item.option_kind == "resume_lineage"
    )
    plan = _plan_with_intervention_option_fields(
        source_plan,
        "resume_lineage",
        audit_metadata_requirements=option.audit_metadata_requirements[:-1],
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "intervention_option_audit_metadata_requirements:simple_loop.resume_lineage",
    )


def test_admit_and_select_default_refuse_orphan_wait_state() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_orphan_wait_state(source_plan)

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "wait_state_orphan:test.orphan_wait",
    )


def test_admit_and_select_default_refuse_wait_state_missing_policy() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_missing_wait_policy(source_plan)

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "wait_state_policy:simple_loop.blocked_recovery.cooldown",
    )


def test_admit_and_select_default_refuse_counter_missing_stage_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_counter_stage_kind(source_plan, "test.missing_stage")

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "counter_stage_kind:simple_loop.reviewer_gap_counter",
    )


def test_admit_and_select_default_refuse_counter_threshold_action_reuse() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    counter = source_plan.counters[0]
    plan = replace(
        source_plan,
        counters=(replace(counter, threshold_action_id=counter.increment_action_id),),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "counter_threshold_action:simple_loop.reviewer_gap_counter",
    )


def test_admit_and_select_default_refuse_duplicate_counter_action_owner() -> None:
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    plan = _plan_with_duplicate_counter(source_plan)

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "counter_duplicate_action:execution.route_checker_fix_needed",
    )


def test_admit_and_select_default_refuse_duplicate_counter_threshold_owner() -> None:
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    plan = _plan_with_duplicate_counter(
        source_plan,
        increment_action_id="execution.route_checker_pass",
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "counter_duplicate_action:execution.escalate_checker_fix_exhausted",
    )


def test_admit_and_select_default_refuse_recovery_counter_missing_policy_source() -> (
    None
):
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    policies = list(source_plan.recovery_policies)
    for index, policy in enumerate(policies):
        if str(policy.id) == "execution.fix_needed_recovery":
            policies[index] = replace(
                policy,
                source_recovery_action_ids=(
                    ActionId("execution.route_doublechecker_fix_needed"),
                ),
            )
            break
    else:
        raise AssertionError("missing execution.fix_needed_recovery policy")
    plan = replace(source_plan, recovery_policies=tuple(policies))

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "counter_recovery_policy_source:execution.fix_cycle_count.checker",
    )


@pytest.mark.parametrize(
    ("policy_id", "threshold_action_id"),
    (
        (
            "execution.fix_needed_recovery",
            "execution.escalate_checker_fix_exhausted",
        ),
        (
            "execution.blocked_recovery",
            "execution.escalate_builder_blocked_exhausted",
        ),
    ),
)
def test_admit_and_select_default_refuse_threshold_action_as_policy_source(
    policy_id: str,
    threshold_action_id: str,
) -> None:
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    policies = list(source_plan.recovery_policies)
    for index, policy in enumerate(policies):
        if str(policy.id) == policy_id:
            policies[index] = replace(
                policy,
                source_recovery_action_ids=(ActionId(threshold_action_id),),
            )
            break
    else:
        raise AssertionError(f"missing {policy_id} policy")
    plan = replace(source_plan, recovery_policies=tuple(policies))

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        f"recovery_policy_source_action:{policy_id}",
    )


def test_admit_and_select_default_refuse_recovery_route_wrong_stage_graph_node() -> (
    None
):
    source_plan, _source_fingerprint = _compile_source(
        lad_execution.workflow_source(), codex_donor=True
    )
    actions = list(source_plan.terminal_actions)
    for index, action in enumerate(actions):
        if str(action.id) == "execution.escalate_checker_fix_exhausted":
            actions[index] = replace(
                action,
                target_graph_node_id="execution.lad.consultant.start",
            )
            break
    else:
        raise AssertionError("missing checker fix threshold action")
    plan = replace(source_plan, terminal_actions=tuple(actions))

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "terminal_recovery_route_graph_node_stage:"
        "execution.escalate_checker_fix_exhausted",
    )


def test_admit_and_select_default_refuse_revise_option_without_target() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_intervention_option_fields(
        source_plan,
        "revise_lineage",
        target_graph_node_id=None,
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "intervention_option_target:simple_loop.revise_lineage",
    )


def test_admit_and_select_default_refuse_revise_option_route_schema_mismatch() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_intervention_option_fields(
        source_plan,
        "revise_lineage",
        payload_schema_id=ArtifactSchemaId("simple_loop.work_prompt"),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "intervention_option_target:simple_loop.revise_lineage",
    )


def test_admit_and_select_default_refuse_mutated_source_recovery_actions() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    policy = source_plan.recovery_policies[0]
    plan = _plan_with_recovery_policy_fields(
        source_plan,
        source_recovery_action_ids=(policy.return_action_ids[0],),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "recovery_policy_source_action:simple_loop.blocked_recovery",
    )


def test_admit_and_select_default_refuse_mutated_return_actions() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    policy = source_plan.recovery_policies[0]
    plan = _plan_with_recovery_policy_fields(
        source_plan,
        return_action_ids=(policy.source_recovery_action_ids[0],),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "recovery_policy_return_action:simple_loop.blocked_recovery",
    )


def test_admit_and_select_default_refuse_mutated_quarantine_actions() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    policy = source_plan.recovery_policies[0]
    plan = _plan_with_recovery_policy_fields(
        source_plan,
        quarantine_action_ids=(policy.return_action_ids[0],),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "recovery_policy_quarantine_action:simple_loop.blocked_recovery",
    )


def test_admit_and_select_default_refuse_missing_recovery_stage_kind() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_recovery_policy_fields(
        source_plan,
        recovery_stage_kind_id=StageKindId("test.missing_stage"),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "recovery_policy_stage_kind:simple_loop.blocked_recovery",
    )


def test_admit_and_select_default_refuse_mutated_return_allowed_phases() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_recovery_policy_fields(
        source_plan,
        return_allowed_phases=("active_recovery", "unsupported_phase"),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "recovery_policy_return_allowed_phases:simple_loop.blocked_recovery",
    )


def test_admit_and_select_default_refuse_mutated_reset_trigger_actions() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    policy = source_plan.recovery_policies[0]
    plan = _plan_with_recovery_policy_fields(
        source_plan,
        reset_trigger_action_ids=(
            *policy.reset_trigger_action_ids,
            policy.source_recovery_action_ids[0],
        ),
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "recovery_policy_reset_trigger_action:simple_loop.blocked_recovery",
    )


def test_admit_and_select_default_refuse_mutated_immediate_recovery_limit() -> None:
    source_plan, _source_fingerprint = _compile_source(
        simple_loop.workflow_source(), codex_donor=True
    )
    plan = _plan_with_recovery_policy_fields(
        source_plan,
        immediate_recovery_limit=2,
    )

    _assert_admit_and_select_default_refuse_selected_authority(
        plan,
        "recovery_policy_immediate_recovery_limit:simple_loop.blocked_recovery",
    )


def test_plan_admission_receipts_are_idempotent_by_input_digest() -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    alternate_plan, alternate_fingerprint = _compile_source(
        kernel_ping_support.no_pause_workflow_source()
    )
    state = empty_runtime_state()

    admit = AdmitPlan(
        "admit-plan",
        selected_plan=plan,
        authority_fingerprint=fingerprint,
    )
    admitted = apply(
        state,
        decide(
            state,
            admit,
            deterministic_context(transition_id="transition-admit"),
        ),
    )
    original_receipt = admitted.receipts["admit-plan"]

    replay_decision = decide(
        admitted,
        admit,
        deterministic_context(transition_id="transition-replay"),
    )
    assert replay_decision.accepted is True
    assert replay_decision.disposition == "replayed"
    assert replay_decision.receipt_ref == original_receipt.receipt_ref
    assert replay_decision.mutations == ()
    assert apply(admitted, replay_decision) == admitted

    conflict = AdmitPlan(
        "admit-plan",
        selected_plan=alternate_plan,
        authority_fingerprint=alternate_fingerprint,
    )
    conflict_decision = decide(
        admitted,
        conflict,
        deterministic_context(transition_id="transition-conflict"),
    )
    assert conflict_decision.accepted is False
    assert conflict_decision.refusal is not None
    assert conflict_decision.refusal.reason == "idempotency_conflict"

    after_conflict = apply(admitted, conflict_decision)
    assert after_conflict.admitted_plans == admitted.admitted_plans
    assert after_conflict.default_plan_ref == admitted.default_plan_ref
    assert after_conflict.work_items == admitted.work_items
    assert after_conflict.activations == admitted.activations
    assert after_conflict.runs == admitted.runs
    assert "admit-plan" in after_conflict.receipts
    assert after_conflict.receipts["admit-plan"] == original_receipt


def test_admit_plan_refuses_conflicting_authority_for_existing_fingerprint() -> None:
    plan, fingerprint = _compile_source(kernel_ping.workflow_source())
    conflicting_plan, _conflicting_fingerprint = _compile_source(
        kernel_ping_support.no_pause_workflow_source()
    )
    state = empty_runtime_state()

    admitted = apply(
        state,
        decide(
            state,
            AdmitPlan(
                "admit-original",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            deterministic_context(transition_id="transition-admit-original"),
        ),
    )

    conflict_decision = decide(
        admitted,
        AdmitPlan(
            "admit-conflict",
            selected_plan=conflicting_plan,
            authority_fingerprint=fingerprint,
        ),
        deterministic_context(transition_id="transition-admit-conflict"),
    )

    assert conflict_decision.accepted is False
    assert conflict_decision.refusal is not None
    assert conflict_decision.refusal.reason == "plan_authority_conflict"

    after_conflict = apply(admitted, conflict_decision)
    assert after_conflict.admitted_plans == admitted.admitted_plans
    assert after_conflict.default_plan_ref == admitted.default_plan_ref
    assert after_conflict.work_items == admitted.work_items
    assert after_conflict.activations == admitted.activations
    assert after_conflict.runs == admitted.runs
