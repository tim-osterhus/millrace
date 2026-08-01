from __future__ import annotations

from collections.abc import MutableMapping
from copy import deepcopy
from typing import Any, cast

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import (
    ActionId,
    CapabilityId,
    OutcomeId,
    RecoveryPolicyDeclaration,
    RecoveryPolicyId,
    RunnerBindingDeclaration,
    RunnerComponentPin,
    RunnerTerminalResultMapping,
    SelectedCompiledPlan,
    StageKindDeclaration,
    StageKindId,
    TerminalActionDeclaration,
)
from millrace.workflows import kernel_ping

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


def _source() -> Source:
    return deepcopy(kernel_ping.WORKFLOW_SOURCE)


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _compile_plan(source: Source) -> SelectedCompiledPlan:
    result = compile_workflow(source)
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan


def _compile_codex_plan(source: Source) -> SelectedCompiledPlan:
    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)
    assert result.plan is not None
    return result.plan


def _component_pin(**changes: object) -> RunnerComponentPin:
    fields: dict[str, object] = {
        "component_kind": "opaque.runner",
        "component_id": "example.component",
        "component_version": "1.2.3",
        "provider_distribution": "example-provider",
        "provider_version": "4.5.6",
        "descriptor_media_type": "application/vnd.example.runner+json",
        "descriptor_sha256": "a" * 64,
        "required_capability_ids": (CapabilityId("capability.runner.invoke"),),
        "legal_terminal_result_ids": ("BLOCKED", "COMPLETE"),
    }
    fields.update(changes)
    return RunnerComponentPin(**cast(Any, fields))


def _terminal_mapping(
    runner_result_id: str = "COMPLETE",
    outcome_id: str = "kernel_ping.taskmaster.task_complete",
    stage_kind_id: str = "kernel_ping.taskmaster",
) -> RunnerTerminalResultMapping:
    return RunnerTerminalResultMapping(
        stage_kind_id=StageKindId(stage_kind_id),
        runner_result_id=runner_result_id,
        outcome_id=OutcomeId(outcome_id),
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"component_kind": ""},
        {"component_id": "  "},
        {"component_version": ""},
        {"provider_distribution": ""},
        {"provider_version": ""},
        {"descriptor_media_type": ""},
        {"descriptor_sha256": "A" * 64},
        {
            "required_capability_ids": (
                CapabilityId("capability.runner.invoke"),
                CapabilityId("capability.runner.invoke"),
            )
        },
        {
            "required_capability_ids": (
                CapabilityId("z.capability"),
                CapabilityId("a.capability"),
            )
        },
        {"legal_terminal_result_ids": ("COMPLETE", "COMPLETE")},
        {"legal_terminal_result_ids": ("COMPLETE", "BLOCKED")},
        {"legal_terminal_result_ids": ("",)},
        {"legal_terminal_result_ids": "ABC"},
    ),
)
def test_runner_component_pin_refuses_malformed_or_noncanonical_fields(
    changes: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _component_pin(**changes)


def test_runner_terminal_result_mapping_refuses_blank_result() -> None:
    with pytest.raises(ValueError, match="runner_result_id"):
        _terminal_mapping(runner_result_id="  ")


@pytest.mark.parametrize(
    "case",
    (
        "mapping_without_pin",
        "duplicate_mapping",
        "duplicate_target",
        "unknown_result",
        "noncanonical_mappings",
        "missing_component_capability",
        "foreign_stage",
    ),
)
def test_runner_binding_refuses_malformed_component_mapping_authority(
    case: str,
) -> None:
    binding = _compile_plan(_source()).runner_bindings[0]
    pin = _component_pin()
    mappings = (_terminal_mapping(),)
    required_capability_ids = (CapabilityId("capability.runner.invoke"),)
    if case == "mapping_without_pin":
        pin = None
    elif case == "duplicate_mapping":
        mappings = (mappings[0], mappings[0])
    elif case == "duplicate_target":
        mappings = (
            _terminal_mapping(
                runner_result_id="BLOCKED",
                outcome_id="kernel_ping.taskmaster.task_complete",
            ),
            _terminal_mapping(),
        )
    elif case == "unknown_result":
        mappings = (_terminal_mapping(runner_result_id="UNKNOWN"),)
    elif case == "missing_component_capability":
        required_capability_ids = ()
    elif case == "foreign_stage":
        mappings = (_terminal_mapping(stage_kind_id="missing.stage"),)
    else:
        mappings = (
            _terminal_mapping(),
            _terminal_mapping(
                runner_result_id="BLOCKED",
                outcome_id="kernel_ping.taskmaster.blocked",
            ),
        )

    with pytest.raises(ValueError):
        RunnerBindingDeclaration(
            id=binding.id,
            adapter_kind=binding.adapter_kind,
            stage_kind_ids=binding.stage_kind_ids,
            invocation_timeout_seconds=binding.invocation_timeout_seconds,
            presentation=binding.presentation,
            required_capability_ids=required_capability_ids,
            component_pin=pin,
            terminal_result_mappings=mappings,
        )


def _plan_with_required_extensions(
    plan: SelectedCompiledPlan,
    required_extensions: list[str],
) -> SelectedCompiledPlan:
    return SelectedCompiledPlan(
        workflow=plan.workflow,
        compatibility_profile=plan.compatibility_profile,
        required_extensions=cast(Any, required_extensions),
        graphs=plan.graphs,
        partitions=plan.partitions,
        queue_families=plan.queue_families,
        external_enqueue_routes=plan.external_enqueue_routes,
        generated_work_routes=plan.generated_work_routes,
        artifact_schemas=plan.artifact_schemas,
        assets=plan.assets,
        stage_kinds=plan.stage_kinds,
        terminal_outcomes=plan.terminal_outcomes,
        terminal_actions=plan.terminal_actions,
        recovery_policies=plan.recovery_policies,
        runner_bindings=plan.runner_bindings,
    )


def test_compiled_plan_is_source_mutation_proof() -> None:
    source = _source()
    plan = _compile_plan(source)

    workflow_name = plan.workflow.workflow_name
    first_stage_assets = plan.stage_kinds[0].asset_ids
    first_stage_label = plan.stage_kinds[0].presentation["display_name"]
    first_action_assets = plan.terminal_actions[0].asset_ids

    workflow = cast(Record, source["workflow"])
    workflow["name"] = "Mutated Workflow"
    _records(source, "stage_kinds")[0]["asset_ids"] = ["mutated.asset"]
    _records(source, "stage_kinds")[0]["presentation"] = {
        "display_name": "Mutated Stage"
    }
    _records(source, "terminal_actions")[0]["asset_ids"] = ["mutated.asset"]

    assert plan.workflow.workflow_name == workflow_name
    assert plan.stage_kinds[0].asset_ids == first_stage_assets
    assert plan.stage_kinds[0].presentation["display_name"] == first_stage_label
    assert plan.terminal_actions[0].asset_ids == first_action_assets


def test_partitionless_stage_kind_remains_immutable_authority() -> None:
    source = _source()
    worker_source = next(
        stage
        for stage in _records(source, "stage_kinds")
        if stage["id"] == "kernel_ping.worker"
    )
    worker_source["partition_id"] = None
    plan = _compile_plan(source)
    worker = next(
        stage for stage in plan.stage_kinds if str(stage.id) == "kernel_ping.worker"
    )
    original_fingerprint = authority_fingerprint(plan)

    worker_source["partition_id"] = "craft"

    assert worker.partition_id is None
    assert authority_fingerprint(plan) == original_fingerprint


@pytest.mark.parametrize(
    ("adapter_kind", "expected_error"),
    (
        pytest.param("", ValueError, id="blank"),
        pytest.param(" \t", ValueError, id="whitespace"),
        pytest.param(42, TypeError, id="non-string"),
    ),
)
def test_runner_binding_declaration_refuses_malformed_adapter_kind(
    adapter_kind: object,
    expected_error: type[Exception],
) -> None:
    binding = _compile_plan(_source()).runner_bindings[0]

    with pytest.raises(expected_error):
        RunnerBindingDeclaration(
            id=binding.id,
            adapter_kind=cast(Any, adapter_kind),
            stage_kind_ids=binding.stage_kind_ids,
            invocation_timeout_seconds=binding.invocation_timeout_seconds,
            presentation=binding.presentation,
            required_capability_ids=binding.required_capability_ids,
        )


def test_selected_compiled_plan_normalizes_caller_owned_sequence_lists() -> None:
    source_plan = _compile_plan(_source())
    required_extensions = ["ext.alpha"]
    partitions = list(source_plan.partitions)
    graphs = list(source_plan.graphs)
    queue_families = list(source_plan.queue_families)
    external_enqueue_routes = list(source_plan.external_enqueue_routes)
    generated_work_routes = list(source_plan.generated_work_routes)
    artifact_schemas = list(source_plan.artifact_schemas)
    assets = list(source_plan.assets)
    stage_kinds = list(source_plan.stage_kinds)
    terminal_outcomes = list(source_plan.terminal_outcomes)
    terminal_actions = list(source_plan.terminal_actions)
    runner_bindings = list(source_plan.runner_bindings)
    concurrency_policies = list(source_plan.concurrency_policies)

    plan = SelectedCompiledPlan(
        workflow=source_plan.workflow,
        compatibility_profile=source_plan.compatibility_profile,
        required_extensions=cast(Any, required_extensions),
        graphs=cast(Any, graphs),
        partitions=cast(Any, partitions),
        queue_families=cast(Any, queue_families),
        external_enqueue_routes=cast(Any, external_enqueue_routes),
        generated_work_routes=cast(Any, generated_work_routes),
        artifact_schemas=cast(Any, artifact_schemas),
        assets=cast(Any, assets),
        stage_kinds=cast(Any, stage_kinds),
        terminal_outcomes=cast(Any, terminal_outcomes),
        terminal_actions=cast(Any, terminal_actions),
        recovery_policies=source_plan.recovery_policies,
        runner_bindings=cast(Any, runner_bindings),
        concurrency_policies=cast(Any, concurrency_policies),
    )

    assert isinstance(plan.required_extensions, tuple)
    assert isinstance(plan.graphs, tuple)
    assert isinstance(plan.partitions, tuple)
    assert isinstance(plan.queue_families, tuple)
    assert isinstance(plan.external_enqueue_routes, tuple)
    assert isinstance(plan.generated_work_routes, tuple)
    assert isinstance(plan.artifact_schemas, tuple)
    assert isinstance(plan.assets, tuple)
    assert isinstance(plan.stage_kinds, tuple)
    assert isinstance(plan.terminal_outcomes, tuple)
    assert isinstance(plan.terminal_actions, tuple)
    assert isinstance(plan.runner_bindings, tuple)
    assert isinstance(plan.concurrency_policies, tuple)

    required_extensions.append("ext.beta")
    partitions.clear()
    queue_families.clear()
    external_enqueue_routes.clear()
    generated_work_routes.clear()
    artifact_schemas.clear()
    assets.clear()
    stage_kinds.clear()
    terminal_outcomes.clear()
    terminal_actions.clear()
    runner_bindings.clear()
    concurrency_policies.clear()

    assert plan.required_extensions == ("ext.alpha",)
    assert plan.partitions == source_plan.partitions
    assert plan.queue_families == source_plan.queue_families
    assert plan.external_enqueue_routes == source_plan.external_enqueue_routes
    assert plan.generated_work_routes == source_plan.generated_work_routes
    assert plan.artifact_schemas == source_plan.artifact_schemas
    assert plan.assets == source_plan.assets
    assert plan.stage_kinds == source_plan.stage_kinds
    assert plan.terminal_outcomes == source_plan.terminal_outcomes
    assert plan.terminal_actions == source_plan.terminal_actions
    assert plan.runner_bindings == source_plan.runner_bindings
    assert plan.concurrency_policies == source_plan.concurrency_policies


def test_nested_declaration_sequences_normalize_caller_owned_lists() -> None:
    plan = _compile_plan(_source())
    stage = plan.stage_kinds[0]
    action = plan.terminal_actions[0]
    runner_binding = plan.runner_bindings[0]
    stage_input_queue_ids = list(stage.input_queue_family_ids)
    stage_output_queue_ids = list(stage.output_queue_family_ids)
    stage_artifact_schema_ids = list(stage.artifact_schema_ids)
    stage_asset_ids = list(stage.asset_ids)
    stage_outcome_ids = list(stage.declared_outcome_ids)
    action_asset_ids = list(action.asset_ids)
    runner_stage_kind_ids = list(runner_binding.stage_kind_ids)

    stage_declaration = StageKindDeclaration(
        id=stage.id,
        partition_id=stage.partition_id,
        runner_binding_id=stage.runner_binding_id,
        input_queue_family_ids=cast(Any, stage_input_queue_ids),
        output_queue_family_ids=cast(Any, stage_output_queue_ids),
        artifact_schema_ids=cast(Any, stage_artifact_schema_ids),
        asset_ids=cast(Any, stage_asset_ids),
        declared_outcome_ids=cast(Any, stage_outcome_ids),
        presentation=stage.presentation,
    )
    action_declaration = TerminalActionDeclaration(
        id=action.id,
        stage_kind_id=action.stage_kind_id,
        outcome_id=action.outcome_id,
        action_kind=action.action_kind,
        target_stage_kind_id=action.target_stage_kind_id,
        target_graph_node_id=action.target_graph_node_id,
        emitted_queue_family_id=action.emitted_queue_family_id,
        artifact_schema_id=action.artifact_schema_id,
        runner_binding_id=action.runner_binding_id,
        asset_ids=cast(Any, action_asset_ids),
        payload_projection=action.payload_projection,
        presentation=action.presentation,
    )
    runner_binding_declaration = RunnerBindingDeclaration(
        id=runner_binding.id,
        adapter_kind=runner_binding.adapter_kind,
        stage_kind_ids=cast(Any, runner_stage_kind_ids),
        invocation_timeout_seconds=runner_binding.invocation_timeout_seconds,
        presentation=runner_binding.presentation,
    )

    assert isinstance(stage_declaration.input_queue_family_ids, tuple)
    assert isinstance(stage_declaration.output_queue_family_ids, tuple)
    assert isinstance(stage_declaration.artifact_schema_ids, tuple)
    assert isinstance(stage_declaration.asset_ids, tuple)
    assert isinstance(stage_declaration.declared_outcome_ids, tuple)
    assert isinstance(action_declaration.asset_ids, tuple)
    assert isinstance(runner_binding_declaration.stage_kind_ids, tuple)

    stage_input_queue_ids.clear()
    stage_output_queue_ids.clear()
    stage_artifact_schema_ids.clear()
    stage_asset_ids.clear()
    stage_outcome_ids.clear()
    action_asset_ids.clear()
    runner_stage_kind_ids.clear()

    assert stage_declaration.input_queue_family_ids == stage.input_queue_family_ids
    assert stage_declaration.output_queue_family_ids == stage.output_queue_family_ids
    assert stage_declaration.artifact_schema_ids == stage.artifact_schema_ids
    assert stage_declaration.asset_ids == stage.asset_ids
    assert stage_declaration.declared_outcome_ids == stage.declared_outcome_ids
    assert action_declaration.asset_ids == action.asset_ids
    assert runner_binding_declaration.stage_kind_ids == runner_binding.stage_kind_ids


def test_recovery_policy_declaration_sequences_normalize_caller_owned_lists() -> None:
    source_recovery_action_ids = [ActionId("recovery.source.blocked")]
    return_action_ids = [ActionId("recovery.worker.resolved")]
    quarantine_action_ids = [ActionId("recovery.worker.operator_needed")]
    return_allowed_phases = ["active_recovery", "quarantine_eligible"]
    reset_trigger_action_ids = [ActionId("recovery.source.completed")]

    policy_declaration = RecoveryPolicyDeclaration(
        id=RecoveryPolicyId("recovery.policy"),
        source_recovery_action_ids=cast(Any, source_recovery_action_ids),
        return_action_ids=cast(Any, return_action_ids),
        quarantine_action_ids=cast(Any, quarantine_action_ids),
        recovery_stage_kind_id=StageKindId("recovery.worker"),
        recorded_source_selector="latest_recovery_attempt_for_lineage",
        attempt_scope="lineage",
        immediate_recovery_limit=1,
        cooldown_starts_at_attempt=2,
        quarantine_threshold_attempt=3,
        threshold_behavior="runtime_quarantine_at_threshold",
        return_allowed_phases=cast(Any, return_allowed_phases),
        reset_trigger_action_ids=cast(Any, reset_trigger_action_ids),
        default_cooldown_seconds=900,
    )

    source_recovery_action_ids.clear()
    return_action_ids.clear()
    quarantine_action_ids.clear()
    return_allowed_phases.clear()
    reset_trigger_action_ids.clear()

    assert policy_declaration.source_recovery_action_ids == (
        ActionId("recovery.source.blocked"),
    )
    assert policy_declaration.return_action_ids == (
        ActionId("recovery.worker.resolved"),
    )
    assert policy_declaration.quarantine_action_ids == (
        ActionId("recovery.worker.operator_needed"),
    )
    assert policy_declaration.return_allowed_phases == (
        "active_recovery",
        "quarantine_eligible",
    )
    assert policy_declaration.reset_trigger_action_ids == (
        ActionId("recovery.source.completed"),
    )


def test_caller_owned_lists_cannot_change_fingerprint_after_computation() -> None:
    source_plan = _compile_plan(_source())
    required_extensions = ["ext.alpha"]
    plan = _plan_with_required_extensions(source_plan, required_extensions)
    fingerprint = authority_fingerprint(plan)

    required_extensions.append("ext.beta")

    assert authority_fingerprint(plan) == fingerprint


def test_selected_authority_nested_sequences_cannot_be_mutated() -> None:
    plan = _compile_plan(_source())

    with pytest.raises(AttributeError):
        getattr(plan.stage_kinds, "append")(plan.stage_kinds[0])

    with pytest.raises(AttributeError):
        getattr(plan.stage_kinds, "remove")(plan.stage_kinds[0])

    with pytest.raises(AttributeError):
        setattr(plan, "stage_kinds", ())


def test_selected_authority_nested_mappings_cannot_be_mutated() -> None:
    plan = _compile_plan(_source())
    presentation = cast(MutableMapping[str, object], plan.stage_kinds[0].presentation)
    details = cast(MutableMapping[str, object], presentation["details"])

    with pytest.raises(TypeError):
        presentation["display_name"] = "Mutated Stage"

    with pytest.raises(TypeError):
        details["entrypoint"] = "Mutated Entrypoint"


def test_equivalent_compiles_compare_by_value_not_identity() -> None:
    first = _compile_plan(_source())
    second = _compile_plan(_source())

    assert first == second
    assert first is not second
