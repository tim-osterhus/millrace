from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import cast

import pytest

from millrace.compiler import (
    DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY,
    SelectedRunnerAdapterPolicy,
    compile_workflow,
)
from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.export import (
    CompiledPlanExportError,
    compiled_plan_export_bytes,
    verify_compiled_plan_export_bytes,
)
from millrace.contracts import SelectedCompiledPlan
from millrace.contracts.compiled_plan import (
    RecoveryPolicyDeclaration,
    RunnerRuntimeFailureMapping,
    StageKindDeclaration,
    TerminalActionDeclaration,
    canonical_authority_bytes,
)
from millrace.contracts.ids import (
    OutcomeId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.runner_payload_capacity import (
    installed_payload_capacity_pin,
    payload_capacity_pin_record,
)
from millrace.contracts.runtime_failures import runtime_failure_mapping_refusal
from support import generic_admission
from tests.compiler.test_pi_runner_bindings import EFFECTS

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)
_BASE_RUNNER_EXPORT_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "id",
        "adapter_kind",
        "stage_kind_ids",
        "invocation_timeout_seconds",
        "required_capability_ids",
        "component_pin",
        "terminal_result_mappings",
    }
)


@dataclass(frozen=True)
class _UnexpectedRunnerBinding:
    id: str
    runtime_failure_mappings: tuple[RunnerRuntimeFailureMapping, ...]


def _compile(source: dict[str, object]):
    bindings = cast(list[dict[str, object]], source.get("runner_bindings", []))
    uses_pi = bool(bindings) and bindings[0].get("adapter_kind") == "pi_rpc"
    policy = (
        DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY if uses_pi else _CODEX_POLICY
    )
    return compile_workflow(source, selected_runner_policy=policy)


def _mapping(
    *,
    stage_kind_id: str = generic_admission.PARENT_STAGE_ID,
    action_id: str = generic_admission.RECOVERY_SOURCE_ACTION_ID,
    failure_kind: str = "timeout",
) -> dict[str, object]:
    return {
        "stage_kind_id": stage_kind_id,
        "failure_kind": failure_kind,
        "action_id": action_id,
    }


def _mapped_source() -> dict[str, object]:
    source = generic_admission.source()
    binding = cast(list[dict[str, object]], source["runner_bindings"])[0]
    binding["runtime_failure_mappings"] = [_mapping()]
    return source


def _source_with_payload_capacity(*, mapped: bool) -> dict[str, object]:
    source = generic_admission.source()
    binding = cast(list[dict[str, object]], source["runner_bindings"])[0]
    binding.update(
        {
            "adapter_kind": "pi_rpc",
            "component_pin": None,
            "terminal_result_mappings": (),
            "required_capability_ids": EFFECTS,
            "payload_capacity_pin": payload_capacity_pin_record(
                installed_payload_capacity_pin()
            ),
        }
    )
    capabilities = cast(list[dict[str, object]], source["capabilities"])
    present = {str(item["id"]) for item in capabilities}
    capabilities.extend(
        {
            "id": capability_id,
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
            "approval_policy_id": None,
        }
        for capability_id in EFFECTS
        if capability_id not in present
    )
    if mapped:
        binding["runtime_failure_mappings"] = [_mapping()]
    return source


def _plan(source: dict[str, object]) -> SelectedCompiledPlan:
    result = _compile(source)
    assert result.plan is not None, result.diagnostics
    return result.plan


def _runner_export(plan: SelectedCompiledPlan) -> dict[str, object]:
    envelope = json.loads(compiled_plan_export_bytes(plan))
    return cast(dict[str, object], envelope["selected_authority"]["runner_bindings"][0])


def _append_child_recovery_source(source: dict[str, object]) -> str:
    action_id = "admission.child_recover"
    outcome_id = "admission.child.needs_recovery"
    stages = cast(list[dict[str, object]], source["stage_kinds"])
    child = next(
        stage
        for stage in stages
        if stage.get("id") == generic_admission.CHILD_STAGE_ID
    )
    child["declared_outcome_ids"] = (
        *cast(tuple[str, ...], child["declared_outcome_ids"]),
        outcome_id,
    )
    cast(list[dict[str, object]], source["terminal_outcomes"]).append(
        {
            "id": outcome_id,
            "stage_kind_id": generic_admission.CHILD_STAGE_ID,
            "marker": "CHILD_RECOVERY_REQUIRED",
        }
    )
    cast(list[dict[str, object]], source["terminal_actions"]).append(
        {
            "id": action_id,
            "stage_kind_id": generic_admission.CHILD_STAGE_ID,
            "outcome_id": outcome_id,
            "kind": "recovery_route",
            "target_stage_kind_id": generic_admission.RECOVERY_STAGE_ID,
            "target_graph_node_id": generic_admission.RECOVERY_NODE_ID,
            "runner_binding_id": generic_admission.RUNNER_ID,
            "asset_ids": ("admission.recovery_prompt",),
        }
    )
    policy = cast(list[dict[str, object]], source["recovery_policies"])[0]
    policy["source_recovery_action_ids"] = (
        *cast(tuple[str, ...], policy["source_recovery_action_ids"]),
        action_id,
    )
    return action_id


def test_omitted_and_empty_mappings_preserve_schema3_authority() -> None:
    omitted = generic_admission.source()
    empty = deepcopy(omitted)
    cast(list[dict[str, object]], empty["runner_bindings"])[0][
        "runtime_failure_mappings"
    ] = []

    omitted_plan = _plan(omitted)
    empty_plan = _plan(empty)
    assert omitted_plan.schema_version == 18
    assert type(omitted_plan.runner_bindings[0]).schema_version == 3
    assert type(empty_plan.runner_bindings[0]).schema_version == 3
    assert canonical_authority_bytes(omitted_plan) == canonical_authority_bytes(
        empty_plan
    )
    assert set(_runner_export(omitted_plan)) == _BASE_RUNNER_EXPORT_KEYS
    assert set(_runner_export(empty_plan)) == _BASE_RUNNER_EXPORT_KEYS


def test_runtime_failure_mapping_is_selected_and_exported_as_schema5() -> None:
    source = _mapped_source()
    plan = _plan(source)
    binding = plan.runner_bindings[0]

    assert plan.schema_version == 18
    assert type(binding).schema_version == 5
    mappings = binding.runtime_failure_mappings
    assert len(mappings) == 1
    assert (
        str(mappings[0].stage_kind_id),
        mappings[0].failure_kind,
        str(mappings[0].action_id),
    ) == (
        generic_admission.PARENT_STAGE_ID,
        "timeout",
        generic_admission.RECOVERY_SOURCE_ACTION_ID,
    )
    assert not binding.terminal_result_mappings
    assert {str(stage.id) for stage in plan.stage_kinds} == {
        str(stage["id"])
        for stage in cast(list[dict[str, object]], source["stage_kinds"])
    }
    assert {str(outcome.id) for outcome in plan.terminal_outcomes} == {
        str(outcome["id"])
        for outcome in cast(list[dict[str, object]], source["terminal_outcomes"])
    }
    expected_markers = {
        str(outcome["id"]): str(outcome["marker"])
        for outcome in cast(list[dict[str, object]], source["terminal_outcomes"])
    }
    assert {
        str(outcome.id): outcome.marker for outcome in plan.terminal_outcomes
    } == expected_markers

    runner_export = _runner_export(plan)
    assert runner_export["schema_version"] == 5
    assert set(runner_export) == _BASE_RUNNER_EXPORT_KEYS | {
        "runtime_failure_mappings"
    }
    assert runner_export["runtime_failure_mappings"] == [
        {
            "record_kind": "runner_runtime_failure_mapping",
            "schema_version": 1,
            **_mapping(),
        }
    ]
    verified = verify_compiled_plan_export_bytes(compiled_plan_export_bytes(plan))
    assert verified.authority_fingerprint == authority_fingerprint(plan)


def test_mapping_and_payload_capacity_select_schema6_and_export_both() -> None:
    plan = _plan(_source_with_payload_capacity(mapped=True))
    binding = plan.runner_bindings[0]

    assert type(binding).schema_version == 6
    runner_export = _runner_export(plan)
    assert runner_export["schema_version"] == 6
    assert set(runner_export) == _BASE_RUNNER_EXPORT_KEYS | {
        "payload_capacity_pin",
        "runtime_failure_mappings",
    }
    assert "payload_capacity_pin" in runner_export
    assert runner_export["runtime_failure_mappings"] == [
        {
            "record_kind": "runner_runtime_failure_mapping",
            "schema_version": 1,
            **_mapping(),
        }
    ]
    verify_compiled_plan_export_bytes(compiled_plan_export_bytes(plan))


def test_payload_capacity_without_mapping_keeps_schema4_export_shape() -> None:
    source_without_mapping = _source_with_payload_capacity(mapped=False)
    source_with_empty_mapping = deepcopy(source_without_mapping)
    cast(list[dict[str, object]], source_with_empty_mapping["runner_bindings"])[0][
        "runtime_failure_mappings"
    ] = []
    plan = _plan(source_without_mapping)
    empty_plan = _plan(source_with_empty_mapping)
    runner_export = _runner_export(plan)

    assert type(plan.runner_bindings[0]).schema_version == 4
    assert type(empty_plan.runner_bindings[0]).schema_version == 4
    assert canonical_authority_bytes(plan) == canonical_authority_bytes(empty_plan)
    assert runner_export["schema_version"] == 4
    assert set(runner_export) == _BASE_RUNNER_EXPORT_KEYS | {"payload_capacity_pin"}
    assert _runner_export(empty_plan) == runner_export


@pytest.mark.parametrize(
    "mutation",
    [
        "record_kind",
        "schema_version",
        "missing_field",
        "extra_field",
        "failure_kind",
        "duplicate_pair",
    ],
)
def test_export_rejects_malformed_runtime_failure_mapping(mutation: str) -> None:
    envelope = json.loads(compiled_plan_export_bytes(_plan(_mapped_source())))
    runner = envelope["selected_authority"]["runner_bindings"][0]
    mapping = runner["runtime_failure_mappings"][0]
    if mutation == "record_kind":
        mapping["record_kind"] = "wrong_kind"
    elif mutation == "schema_version":
        mapping["schema_version"] = 2
    elif mutation == "missing_field":
        mapping.pop("action_id")
    elif mutation == "extra_field":
        mapping["default_action_id"] = generic_admission.RECOVERY_SOURCE_ACTION_ID
    elif mutation == "failure_kind":
        mapping["failure_kind"] = "cancelled"
    else:
        runner["runtime_failure_mappings"].append(dict(mapping))

    export_bytes = json.dumps(
        envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    with pytest.raises(CompiledPlanExportError):
        verify_compiled_plan_export_bytes(export_bytes)


def test_runtime_failure_mappings_are_canonically_ordered_before_fingerprinting() -> (
    None
):
    first = generic_admission.source()
    child_action_id = _append_child_recovery_source(first)
    binding = cast(list[dict[str, object]], first["runner_bindings"])[0]
    mappings = [
        _mapping(
            stage_kind_id=generic_admission.PARENT_STAGE_ID,
            action_id=generic_admission.RECOVERY_SOURCE_ACTION_ID,
        ),
        _mapping(
            stage_kind_id=generic_admission.CHILD_STAGE_ID,
            action_id=child_action_id,
        ),
    ]
    binding["runtime_failure_mappings"] = mappings
    second = deepcopy(first)
    cast(list[dict[str, object]], second["runner_bindings"])[0][
        "runtime_failure_mappings"
    ] = list(reversed(mappings))

    first_plan = _plan(first)
    second_plan = _plan(second)
    expected = [generic_admission.CHILD_STAGE_ID, generic_admission.PARENT_STAGE_ID]
    assert [
        str(mapping.stage_kind_id)
        for mapping in first_plan.runner_bindings[0].runtime_failure_mappings
    ] == expected
    assert canonical_authority_bytes(first_plan) == canonical_authority_bytes(
        second_plan
    )
    assert authority_fingerprint(first_plan) == authority_fingerprint(second_plan)


def test_source_binding_can_differ_from_recovery_target_binding() -> None:
    source = _mapped_source()
    source_binding = cast(list[dict[str, object]], source["runner_bindings"])[0]
    target_binding_id = "admission.recovery_runner"
    source_binding["stage_kind_ids"] = (
        generic_admission.PARENT_STAGE_ID,
        generic_admission.CHILD_STAGE_ID,
    )
    target_binding = deepcopy(source_binding)
    target_binding.update(
        {
            "id": target_binding_id,
            "stage_kind_ids": (generic_admission.RECOVERY_STAGE_ID,),
        }
    )
    target_binding.pop("runtime_failure_mappings")
    cast(list[dict[str, object]], source["runner_bindings"]).append(target_binding)
    recovery_stage = next(
        stage
        for stage in cast(list[dict[str, object]], source["stage_kinds"])
        if stage.get("id") == generic_admission.RECOVERY_STAGE_ID
    )
    recovery_stage["runner_binding_id"] = target_binding_id
    for action in cast(list[dict[str, object]], source["terminal_actions"]):
        if action.get("kind") == "recovery_route":
            action["runner_binding_id"] = target_binding_id

    plan = _plan(source)
    selected_source_binding = next(
        binding
        for binding in plan.runner_bindings
        if str(binding.id) == generic_admission.RUNNER_ID
    )
    assert str(
        selected_source_binding.runtime_failure_mappings[0].stage_kind_id
    ) == generic_admission.PARENT_STAGE_ID


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_failure_kind",
        "unknown_action",
        "wrong_stage",
        "binding_does_not_own_stage",
        "stage_uses_another_binding",
        "non_recovery_action",
        "outcome_not_declared_by_stage",
        "no_selected_recovery_policy",
        "ambiguous_recovery_policy",
        "recovery_action_has_artifact_schema",
        "duplicate_stage_failure_pair",
        "extra_mapping_field",
        "missing_mapping_field",
        "nonobject_mapping_record",
        "mapping_collection_not_array",
    ],
)
def test_invalid_runtime_failure_mapping_is_rejected(mutation: str) -> None:
    source = _mapped_source()
    binding = cast(list[dict[str, object]], source["runner_bindings"])[0]
    mappings = cast(list[dict[str, object]], binding["runtime_failure_mappings"])
    mapping = mappings[0]
    if mutation == "unknown_failure_kind":
        mapping["failure_kind"] = "cancelled"
    elif mutation == "unknown_action":
        mapping["action_id"] = "admission.missing"
    elif mutation == "wrong_stage":
        mapping["stage_kind_id"] = generic_admission.CHILD_STAGE_ID
    elif mutation == "binding_does_not_own_stage":
        binding["stage_kind_ids"] = tuple(
            stage_id
            for stage_id in cast(tuple[str, ...], binding["stage_kind_ids"])
            if stage_id != generic_admission.PARENT_STAGE_ID
        )
    elif mutation == "stage_uses_another_binding":
        stages = cast(list[dict[str, object]], source["stage_kinds"])
        stage = next(
            stage
            for stage in stages
            if stage.get("id") == generic_admission.PARENT_STAGE_ID
        )
        stage["runner_binding_id"] = "admission.other_runner"
    elif mutation == "non_recovery_action":
        mapping["action_id"] = "admission.complete"
    elif mutation == "outcome_not_declared_by_stage":
        stages = cast(list[dict[str, object]], source["stage_kinds"])
        stage = next(
            stage
            for stage in stages
            if stage.get("id") == generic_admission.PARENT_STAGE_ID
        )
        stage["declared_outcome_ids"] = tuple(
            outcome_id
            for outcome_id in cast(tuple[str, ...], stage["declared_outcome_ids"])
            if outcome_id != "admission.needs_recovery"
        )
    elif mutation == "no_selected_recovery_policy":
        mapping["action_id"] = generic_admission.ALTERNATE_RECOVERY_SOURCE_ACTION_ID
    elif mutation == "ambiguous_recovery_policy":
        policies = cast(list[dict[str, object]], source["recovery_policies"])
        duplicate = deepcopy(policies[0])
        duplicate["id"] = "admission.recovery_policy_duplicate"
        policies.append(duplicate)
    elif mutation == "recovery_action_has_artifact_schema":
        action = next(
            action
            for action in cast(list[dict[str, object]], source["terminal_actions"])
            if action.get("id") == generic_admission.RECOVERY_SOURCE_ACTION_ID
        )
        action["artifact_schema_id"] = generic_admission.OTHER_SCHEMA_ID
    elif mutation == "duplicate_stage_failure_pair":
        mappings.append(
            _mapping(
                action_id=generic_admission.ALTERNATE_RECOVERY_SOURCE_ACTION_ID
            )
        )
    elif mutation == "extra_mapping_field":
        mapping["default_action_id"] = generic_admission.RECOVERY_SOURCE_ACTION_ID
    elif mutation == "missing_mapping_field":
        mapping.pop("action_id")
    elif mutation == "nonobject_mapping_record":
        binding["runtime_failure_mappings"] = [42]
    else:
        binding["runtime_failure_mappings"] = None

    result = _compile(source)
    assert result.plan is None, mutation
    assert any(
        diagnostic.code
        in {
            "invalid_runner_runtime_failure_mapping",
            "duplicate_runner_runtime_failure_mapping",
        }
        for diagnostic in result.diagnostics
    ), (mutation, result.diagnostics)


def test_runtime_failure_mapping_admission_matches_compiler_authority_checks() -> None:
    plan = _plan(_mapped_source())
    assert runtime_failure_mapping_refusal(plan) is None

    # The compiler rejects these authored forms before constructing a plan. These
    # equivalent in-memory corruptions exercise the admission boundary directly.
    binding = plan.runner_bindings[0]
    mapping = binding.runtime_failure_mappings[0]
    recovery_action = next(
        action
        for action in plan.terminal_actions
        if action.id == mapping.action_id
    )
    parent_stage = next(
        stage
        for stage in plan.stage_kinds
        if str(stage.id) == generic_admission.PARENT_STAGE_ID
    )
    policy = plan.recovery_policies[0]

    mutations: dict[str, SelectedCompiledPlan] = {}
    mutations["selected_binding_declaration"] = _replace_plan_binding(
        plan,
        _UnexpectedRunnerBinding(
            str(binding.id), binding.runtime_failure_mappings
        ),
    )
    mutations["action_owner"] = _replace_plan_action(
        plan,
        replace(
            recovery_action,
            stage_kind_id=StageKindId(generic_admission.CHILD_STAGE_ID),
        ),
    )
    mutations["action_outcome"] = _replace_plan_action(
        plan,
        replace(recovery_action, outcome_id=OutcomeId("admission.missing_outcome")),
    )
    mutations["mapped_stage_binding"] = _replace_plan_stage(
        plan,
        replace(parent_stage, runner_binding_id=RunnerBindingId("admission.other")),
    )
    mutations["no_selected_policy"] = _replace_plan_policy(
        plan,
        replace(
            policy,
            source_recovery_action_ids=tuple(
                action_id
                for action_id in policy.source_recovery_action_ids
                if action_id != mapping.action_id
            ),
        ),
    )
    mutations["ambiguous_selected_policy"] = replace(
        plan, recovery_policies=(*plan.recovery_policies, policy)
    )
    mutations["action_artifact_schema"] = _replace_plan_action(
        plan,
        replace(
            recovery_action,
            artifact_schema_id=plan.artifact_schemas[0].id,
        ),
    )

    expected_prefixes = {
        "selected_binding_declaration": "runtime_failure_mapping_shape:",
        "action_owner": "runtime_failure_mapping_authority:",
        "action_outcome": "runtime_failure_mapping_authority:",
        "mapped_stage_binding": "runtime_failure_mapping_authority:",
        "no_selected_policy": "runtime_failure_mapping_authority:",
        "ambiguous_selected_policy": "runtime_failure_mapping_authority:",
        "action_artifact_schema": "runtime_failure_mapping_artifact:",
    }
    for mutation_name, corrupted_plan in mutations.items():
        refusal = runtime_failure_mapping_refusal(corrupted_plan)
        assert refusal is not None, mutation_name
        assert refusal.startswith(expected_prefixes[mutation_name]), (
            mutation_name,
            refusal,
        )


def _replace_plan_binding(
    plan: SelectedCompiledPlan, binding: object
) -> SelectedCompiledPlan:
    corrupted = replace(plan)
    object.__setattr__(corrupted, "runner_bindings", (binding,))
    return corrupted


def _replace_plan_action(
    plan: SelectedCompiledPlan, action: TerminalActionDeclaration
) -> SelectedCompiledPlan:
    return replace(
        plan,
        terminal_actions=tuple(
            action if selected.id == action.id else selected
            for selected in plan.terminal_actions
        ),
    )


def _replace_plan_stage(
    plan: SelectedCompiledPlan, stage: StageKindDeclaration
) -> SelectedCompiledPlan:
    return replace(
        plan,
        stage_kinds=tuple(
            stage if selected.id == stage.id else selected
            for selected in plan.stage_kinds
        ),
    )


def _replace_plan_policy(
    plan: SelectedCompiledPlan, policy: RecoveryPolicyDeclaration
) -> SelectedCompiledPlan:
    return replace(
        plan,
        recovery_policies=tuple(
            policy if selected.id == policy.id else selected
            for selected in plan.recovery_policies
        ),
    )
