"""Workflow recovery and runtime failure policy validation."""

from __future__ import annotations

from collections.abc import Iterable

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    FrozenGraphPlanePlan,
    RegisteredStageKindDefinition,
    RuntimeEffectHandlerDefinition,
    RuntimeEffectOperationDefinition,
    RuntimeEffectOperationRunnerDefinition,
    RuntimeEffectRuleDefinition,
    RuntimeFailurePolicyDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.assets import WorkflowPrimitiveBundle
from millrace_ai.contracts import Plane

from ..outcomes import CompilerValidationError
from .operation_runners import legacy_handler_ids_for_operation
from .repair_closures import validate_runtime_effect_repair_route_closure


def validate_recovery_policies(
    *,
    workflow_primitives: WorkflowPrimitiveBundle,
    stage_kinds_by_node_id: dict[str, RegisteredStageKindDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    terminal_state_ids: set[str],
) -> None:
    for policy in workflow_primitives.recovery_policies:
        for source_node_id in policy.source_node_ids:
            stage_kind = _stage_kind_for_node_or_kind(
                source_node_id,
                stage_kinds_by_node_id=stage_kinds_by_node_id,
                stage_kinds=stage_kinds,
                source_label=f"workflow recovery policy {policy.policy_id}",
            )
            for outcome in policy.on_outcomes:
                if outcome not in stage_kind.legal_outcomes:
                    raise CompilerValidationError(
                        f"workflow recovery policy {policy.policy_id} references illegal outcome "
                        f"{outcome} for source node {source_node_id}"
                    )
        for target_node_id in (policy.retry_target_node_id, policy.exhausted_target_node_id):
            if target_node_id is not None:
                _stage_kind_for_node_or_kind(
                    target_node_id,
                    stage_kinds_by_node_id=stage_kinds_by_node_id,
                    stage_kinds=stage_kinds,
                    source_label=f"workflow recovery policy {policy.policy_id}",
                )
        if (
            policy.exhausted_terminal_state_id is not None
            and policy.exhausted_terminal_state_id not in terminal_state_ids
        ):
            raise CompilerValidationError(
                f"workflow recovery policy {policy.policy_id} references unknown terminal "
                f"state {policy.exhausted_terminal_state_id}"
            )


def validate_runtime_failure_policies(
    *,
    workflow_primitives: WorkflowPrimitiveBundle,
    artifact_contracts_by_id: dict[str, ArtifactContractDefinition],
    families_by_id: dict[str, WorkItemFamilyDefinition],
    runtime_effect_handlers_by_id: dict[str, RuntimeEffectHandlerDefinition],
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
    runtime_effect_rules_by_id: dict[str, RuntimeEffectRuleDefinition],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    stage_kinds_by_node_id: dict[str, RegisteredStageKindDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    terminal_state_ids: set[str],
) -> None:
    graph_node_ids_by_plane = {
        plane: {node.node_id for node in graph.nodes}
        for plane, graph in graphs_by_plane.items()
    }
    graph_stage_kind_ids_by_plane = {
        plane: {node.stage_kind_id for node in graph.nodes}
        for plane, graph in graphs_by_plane.items()
    }
    terminal_state_ids_by_plane = {
        plane: {state.terminal_state_id for state in graph.terminal_states}
        for plane, graph in graphs_by_plane.items()
    }
    selected_node_ids = {
        node_id
        for node_ids in graph_node_ids_by_plane.values()
        for node_id in node_ids
    }
    runtime_effect_failure_classes = {
        failure_class
        for handler in runtime_effect_handlers_by_id.values()
        for failure_class in getattr(handler, "failure_classes")
    } | {
        mapping.failure_class
        for operation in runtime_effect_operations_by_id.values()
        for mapping in operation.failure_mappings
    }
    for policy in workflow_primitives.runtime_failure_policies:
        active_planes = _runtime_failure_policy_active_planes(
            policy,
            graph_node_ids_by_plane=graph_node_ids_by_plane,
            graph_stage_kind_ids_by_plane=graph_stage_kind_ids_by_plane,
        )
        for family_id in policy.applies_to_families:
            if family_id not in families_by_id:
                raise CompilerValidationError(
                    f"runtime failure policy {policy.policy_id} references unknown family "
                    f"{family_id}"
                )
        runtime_effect_policy = "runtime_effect" in policy.applies_to_origins
        if runtime_effect_policy:
            for handler_id in policy.applies_to_handler_ids:
                if handler_id not in runtime_effect_handlers_by_id:
                    raise CompilerValidationError(
                        f"runtime failure policy {policy.policy_id} references unknown "
                        f"runtime effect handler {handler_id}"
                    )
            for operation_id in policy.applies_to_operation_ids:
                if operation_id not in runtime_effect_operations_by_id:
                    raise CompilerValidationError(
                        f"runtime failure policy {policy.policy_id} references unknown "
                        f"runtime effect operation {operation_id}"
                    )
            _validate_runtime_failure_policy_effect_identity_scope(
                policy,
                runtime_effect_operations_by_id=runtime_effect_operations_by_id,
                runtime_effect_runners_by_id=runtime_effect_runners_by_id,
            )
            for failure_class in policy.applies_to_failure_classes:
                if failure_class not in runtime_effect_failure_classes:
                    raise CompilerValidationError(
                        f"runtime failure policy {policy.policy_id} references undeclared "
                        f"runtime effect failure class {failure_class}"
                    )
            for source_node_id in policy.applies_to_source_node_ids:
                _validate_runtime_failure_policy_node_reference(
                    source_node_id,
                    selected_node_ids=selected_node_ids,
                    stage_kinds=stage_kinds,
                    policy_id=policy.policy_id,
                    role="source",
                )
            _validate_policy_nodes_in_declared_planes(
                policy_id=policy.policy_id,
                node_ids=policy.applies_to_source_node_ids,
                active_planes=active_planes,
                graph_node_ids_by_plane=graph_node_ids_by_plane,
                role="source",
            )
            _validate_policy_terminal_states_in_declared_planes(
                policy_id=policy.policy_id,
                terminal_state_ids=policy.applies_to_source_terminal_state_ids,
                active_planes=active_planes,
                all_terminal_state_ids=terminal_state_ids,
                terminal_state_ids_by_plane=terminal_state_ids_by_plane,
                role="source",
            )
        if policy.recovery_node_id is not None:
            _validate_runtime_failure_policy_node_reference(
                policy.recovery_node_id,
                selected_node_ids=selected_node_ids,
                stage_kinds=stage_kinds,
                policy_id=policy.policy_id,
                role="recovery",
            )
            _validate_policy_nodes_in_declared_planes(
                policy_id=policy.policy_id,
                node_ids=(policy.recovery_node_id,),
                active_planes=active_planes,
                graph_node_ids_by_plane=graph_node_ids_by_plane,
                role="recovery",
            )
        if policy.target_node_id is not None:
            _validate_runtime_failure_policy_node_reference(
                policy.target_node_id,
                selected_node_ids=selected_node_ids,
                stage_kinds=stage_kinds,
                policy_id=policy.policy_id,
                role="target",
            )
            _validate_policy_nodes_in_declared_planes(
                policy_id=policy.policy_id,
                node_ids=(policy.target_node_id,),
                active_planes=active_planes,
                graph_node_ids_by_plane=graph_node_ids_by_plane,
                role="target",
            )
            for plane in active_planes:
                if policy.target_node_id not in graph_node_ids_by_plane.get(plane, set()):
                    continue
                target_stage_kind = stage_kinds_by_node_id[policy.target_node_id]
                for family_id in policy.applies_to_families:
                    if family_id not in target_stage_kind.allowed_work_item_families:
                        raise CompilerValidationError(
                            f"runtime failure policy {policy.policy_id} target node "
                            f"{policy.target_node_id} cannot start family {family_id}"
                        )
        if policy.target_terminal_state_id is not None:
            _validate_policy_terminal_states_in_declared_planes(
                policy_id=policy.policy_id,
                terminal_state_ids=(policy.target_terminal_state_id,),
                active_planes=active_planes,
                all_terminal_state_ids=terminal_state_ids,
                terminal_state_ids_by_plane=terminal_state_ids_by_plane,
                role="target",
            )
        if runtime_effect_policy and policy.action == "route_to_node":
            validate_runtime_effect_repair_route_closure(
                policy,
                active_planes=active_planes,
                artifact_contracts_by_id=artifact_contracts_by_id,
                families_by_id=families_by_id,
                runtime_effect_operations_by_id=runtime_effect_operations_by_id,
                runtime_effect_runners_by_id=runtime_effect_runners_by_id,
                runtime_effect_rules_by_id=runtime_effect_rules_by_id,
                graphs_by_plane=graphs_by_plane,
                graph_node_ids_by_plane=graph_node_ids_by_plane,
                stage_kinds_by_node_id=stage_kinds_by_node_id,
            )


def _runtime_failure_policy_active_planes(
    policy: RuntimeFailurePolicyDefinition,
    *,
    graph_node_ids_by_plane: dict[Plane, set[str]],
    graph_stage_kind_ids_by_plane: dict[Plane, set[str]],
) -> tuple[Plane, ...]:
    return tuple(
        plane
        for plane in getattr(policy, "applies_to_planes")
        if _runtime_failure_policy_is_active_for_plane(
            policy,
            graph_node_ids_by_plane.get(plane, set()),
            graph_stage_kind_ids_by_plane.get(plane, set()),
        )
    )


def _policy_activating_node_ids(policy: RuntimeFailurePolicyDefinition) -> tuple[str, ...]:
    return tuple(
        node_id
        for node_id in (
            *getattr(policy, "applies_to_source_node_ids", ()),
            getattr(policy, "target_node_id", None),
            getattr(policy, "recovery_node_id", None),
        )
        if node_id is not None
    )


def _validate_runtime_failure_policy_effect_identity_scope(
    policy: RuntimeFailurePolicyDefinition,
    *,
    runtime_effect_operations_by_id: dict[str, RuntimeEffectOperationDefinition],
    runtime_effect_runners_by_id: dict[str, RuntimeEffectOperationRunnerDefinition],
) -> None:
    if not policy.applies_to_handler_ids or not policy.applies_to_operation_ids:
        return
    selected_handler_ids = set(policy.applies_to_handler_ids)
    legacy_handlers_by_operation_id = {
        operation_id: legacy_handler_ids_for_operation(
            operation_id,
            runtime_effect_operations_by_id=runtime_effect_operations_by_id,
            runtime_effect_runners_by_id=runtime_effect_runners_by_id,
        )
        for operation_id in policy.applies_to_operation_ids
    }
    compatible_handler_ids = set().union(*legacy_handlers_by_operation_id.values())
    for handler_id in sorted(selected_handler_ids - compatible_handler_ids):
        raise CompilerValidationError(
            f"runtime failure policy {policy.policy_id} handler {handler_id} is not a "
            f"legacy alias for operation ids {', '.join(policy.applies_to_operation_ids)}"
        )
    for operation_id, legacy_handler_ids in sorted(legacy_handlers_by_operation_id.items()):
        if not (selected_handler_ids & legacy_handler_ids):
            raise CompilerValidationError(
                f"runtime failure policy {policy.policy_id} operation {operation_id} has no "
                "selected legacy handler alias"
            )


def _validate_runtime_failure_policy_node_reference(
    node_id: str,
    *,
    selected_node_ids: set[str],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    policy_id: str,
    role: str,
) -> None:
    if node_id in selected_node_ids or node_id in stage_kinds:
        return
    role_label = f"{role} " if role == "target" else ""
    raise CompilerValidationError(
        f"runtime failure policy {policy_id} references unknown {role_label}node {node_id}"
    )


def _validate_policy_nodes_in_declared_planes(
    *,
    policy_id: str,
    node_ids: Iterable[str],
    active_planes: Iterable[Plane],
    graph_node_ids_by_plane: dict[Plane, set[str]],
    role: str,
) -> None:
    for plane in active_planes:
        plane_node_ids = graph_node_ids_by_plane.get(plane)
        if plane_node_ids is None:
            continue
        for node_id in node_ids:
            if node_id not in plane_node_ids:
                raise CompilerValidationError(
                    f"runtime failure policy {policy_id} {role} node {node_id} "
                    f"is not in plane {plane.value}"
                )


def _validate_policy_terminal_states_in_declared_planes(
    *,
    policy_id: str,
    terminal_state_ids: Iterable[str],
    active_planes: Iterable[Plane],
    all_terminal_state_ids: set[str],
    terminal_state_ids_by_plane: dict[Plane, set[str]],
    role: str,
) -> None:
    for plane in active_planes:
        plane_terminal_state_ids = terminal_state_ids_by_plane.get(plane)
        if plane_terminal_state_ids is None:
            continue
        for terminal_state_id in terminal_state_ids:
            if terminal_state_id not in all_terminal_state_ids:
                raise CompilerValidationError(
                    f"runtime failure policy {policy_id} references unknown terminal "
                    f"state {terminal_state_id}"
                )
            if terminal_state_id not in plane_terminal_state_ids:
                raise CompilerValidationError(
                    f"runtime failure policy {policy_id} {role} terminal state "
                    f"{terminal_state_id} is not in plane {plane.value}"
                )


def _runtime_failure_policy_is_active_for_plane(
    policy: RuntimeFailurePolicyDefinition,
    plane_node_ids: set[str],
    plane_stage_kind_ids: set[str],
) -> bool:
    source_node_ids = tuple(getattr(policy, "applies_to_source_node_ids", ()))
    if not source_node_ids:
        return True
    return any(
        node_id in plane_node_ids or node_id in plane_stage_kind_ids
        for node_id in _policy_activating_node_ids(policy)
    )


def _stage_kind_for_node_or_kind(
    node_or_kind_id: str,
    *,
    stage_kinds_by_node_id: dict[str, RegisteredStageKindDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    source_label: str,
) -> RegisteredStageKindDefinition:
    stage_kind = stage_kinds_by_node_id.get(node_or_kind_id) or stage_kinds.get(node_or_kind_id)
    if stage_kind is None:
        raise CompilerValidationError(f"{source_label} references unknown node {node_or_kind_id}")
    return stage_kind


__all__ = [
    "validate_recovery_policies",
    "validate_runtime_failure_policies",
]
