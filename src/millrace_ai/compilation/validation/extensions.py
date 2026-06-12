"""Extension manifest compile-time validation.

Validates that required extension declarations in mode configs are satisfied
by discovered extension package manifests.  Rejects missing or unavailable
extensions with clear compiler diagnostics.

Also checks that graph-loop stage-kind vocabulary owned by undeclared
extension domains is rejected at compile time rather than discovered
at runtime.

ADRs: ADR-0012, ADR-0015.
"""

from __future__ import annotations

from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

from millrace_ai.architecture import (
    GraphLoopDefinition,
    RegisteredStageKindDefinition,
    RuntimeFailurePolicyDefinition,
)
from millrace_ai.contracts import ModeDefinition, Plane
from millrace_ai.contracts.extensions import RequiredExtensionDeclaration
from millrace_ai.extensions import ExtensionDomain, ExtensionItemKind, ExtensionPackageManifest

from ..outcomes import CompilerValidationError

# ---------------------------------------------------------------------------
# Domain → extension package id mapping for built-in extensions.
# Each shipped built-in extension manifest follows this convention.
# ---------------------------------------------------------------------------

_BUILTIN_DOMAIN_PACKAGE_IDS: dict[ExtensionDomain, str] = {
    ExtensionDomain.GENERIC: "millrace.generic",
    ExtensionDomain.RECON: "millrace.recon",
    ExtensionDomain.CLOSURE: "millrace.closure",
    ExtensionDomain.BLUEPRINT: "millrace.blueprint",
    ExtensionDomain.LEARNING: "millrace.learning",
}


@dataclass(frozen=True)
class _OwnershipRef:
    item_kind: ExtensionItemKind
    item_id: str
    referenced_by: str


@dataclass(frozen=True)
class _OwnedItem:
    package_id: str
    item_kind: ExtensionItemKind
    item_id: str


def _ownership_key(item_kind: ExtensionItemKind, item_id: str) -> tuple[ExtensionItemKind, str]:
    return item_kind, item_id.lower()


def _build_manifest_ownership_index(
    manifests_by_id: dict[str, ExtensionPackageManifest],
) -> dict[tuple[ExtensionItemKind, str], _OwnedItem]:
    """Build package ownership from extension manifest items."""
    ownership: dict[tuple[ExtensionItemKind, str], _OwnedItem] = {}
    for manifest in manifests_by_id.values():
        for item in manifest.items:
            key = _ownership_key(item.item_kind, item.item_id)
            existing = ownership.get(key)
            if existing is not None and existing.package_id != manifest.package_id:
                raise CompilerValidationError(
                    f"Duplicate extension-owned vocabulary item "
                    f"{item.item_kind.value!r} {item.item_id!r}: "
                    f"packages {existing.package_id!r} and {manifest.package_id!r} "
                    f"both declare ownership."
                )
            ownership[key] = _OwnedItem(
                package_id=manifest.package_id,
                item_kind=item.item_kind,
                item_id=item.item_id,
            )
    return ownership


def _known_registry_ids(
    *,
    stage_kinds: dict[str, RegisteredStageKindDefinition] | None,
    terminal_actions_by_id: dict[str, object] | None,
    workflow_primitives: object | None,
    scheduler_policy: object | None,
    recovery_policies: tuple[object, ...] | None,
) -> dict[ExtensionItemKind, set[str]]:
    known: dict[ExtensionItemKind, set[str]] = {}

    def add(kind: ExtensionItemKind, ids: set[str]) -> None:
        known.setdefault(kind, set()).update(item_id.lower() for item_id in ids)

    if stage_kinds is not None:
        add(ExtensionItemKind.STAGE_KIND, set(stage_kinds))
    if terminal_actions_by_id is not None:
        add(ExtensionItemKind.TERMINAL_ACTION, set(terminal_actions_by_id))
    if workflow_primitives is not None:
        add(ExtensionItemKind.ARTIFACT_CONTRACT, _ids(workflow_primitives, "artifact_contracts", "artifact_id"))
        add(ExtensionItemKind.REQUEST_CONTEXT_PROFILE, _ids(workflow_primitives, "request_context_profiles", "profile_id"))
        add(ExtensionItemKind.CONTEXT_PROVIDER, _ids(workflow_primitives, "request_context_providers", "provider_id"))
        add(
            ExtensionItemKind.REQUEST_CONTEXT_RENDER_PLAN,
            _ids(workflow_primitives, "request_context_render_plans", "render_plan_id"),
        )
        add(ExtensionItemKind.WORK_ITEM_FAMILY, _ids(workflow_primitives, "work_item_families", "family_id"))
        add(ExtensionItemKind.DOCUMENT_ADAPTER, _ids(workflow_primitives, "document_adapters", "adapter_id"))
        add(
            ExtensionItemKind.QUEUE_LIFECYCLE_POLICY,
            {
                str(getattr(family, "queue_lifecycle_adapter_id"))
                for family in getattr(workflow_primitives, "work_item_families", ())
                if getattr(family, "queue_lifecycle_adapter_id", None) is not None
            },
        )
        add(ExtensionItemKind.QUEUE_CLAIM_POLICY, _ids(workflow_primitives, "queue_claim_policies", "policy_id"))
        add(ExtensionItemKind.LIFECYCLE_MUTATION_PLAN, _ids(workflow_primitives, "lifecycle_mutation_plans", "plan_id"))
        add(ExtensionItemKind.RUNTIME_EFFECT_HANDLER, _ids(workflow_primitives, "runtime_effect_handlers", "handler_id"))
        add(ExtensionItemKind.RUNTIME_EFFECT_RUNNER, _ids(workflow_primitives, "runtime_effect_runners", "runner_id"))
        add(ExtensionItemKind.OPERATION_RUNNER, _ids(workflow_primitives, "runtime_effect_runners", "runner_id"))
        add(ExtensionItemKind.RUNTIME_EFFECT_RULE, _ids(workflow_primitives, "runtime_effect_rules", "rule_id"))
        add(ExtensionItemKind.RUNTIME_EFFECT_STORE, _ids(workflow_primitives, "effect_stores", "store_id"))
        add(ExtensionItemKind.RUNTIME_EFFECT_VALIDATOR, _ids(workflow_primitives, "effect_validators", "validator_id"))
        add(ExtensionItemKind.RUNTIME_EFFECT_OPERATION, _ids(workflow_primitives, "runtime_effect_operations", "operation_id"))
        add(ExtensionItemKind.RUNTIME_OPERATION, _ids(workflow_primitives, "runtime_operations", "operation_id"))
        add(ExtensionItemKind.RUNTIME_EFFECT_PRIMITIVE, _ids(workflow_primitives, "runtime_effect_primitives", "primitive_id"))
        add(ExtensionItemKind.RECOVERY_POLICY, _ids(workflow_primitives, "recovery_policies", "policy_id"))
        add(ExtensionItemKind.FAILURE_POLICY, _ids(workflow_primitives, "runtime_failure_policies", "policy_id"))
        add(ExtensionItemKind.RUNTIME_FAILURE_POLICY, _ids(workflow_primitives, "runtime_failure_policies", "policy_id"))
        add(ExtensionItemKind.SCHEDULER_POLICY, _ids(workflow_primitives, "scheduler_policies", "policy_id"))
        epoch = getattr(workflow_primitives, "workspace_schema_epoch", None)
        if epoch is not None:
            add(ExtensionItemKind.WORKSPACE_SCHEMA_EPOCH, {str(getattr(epoch, "epoch_id", ""))})
    if scheduler_policy is not None:
        add(ExtensionItemKind.SCHEDULER_POLICY, {str(getattr(scheduler_policy, "policy_id", ""))})
    if recovery_policies is not None:
        add(
            ExtensionItemKind.RECOVERY_POLICY,
            {str(getattr(policy, "policy_id", "")) for policy in recovery_policies},
        )
    return known


def _ids(source: object, collection_attr: str, id_attr: str) -> set[str]:
    return {
        str(getattr(item, id_attr))
        for item in getattr(source, collection_attr, ())
        if getattr(item, id_attr, None) is not None
    }


def _add_ref(
    refs: list[_OwnershipRef],
    item_kind: ExtensionItemKind,
    item_id: object | None,
    referenced_by: str,
) -> None:
    if item_id is not None:
        refs.append(_OwnershipRef(item_kind, str(item_id), referenced_by))


def _add_family_refs(
    refs: list[_OwnershipRef],
    *,
    family_id: object | None,
    family: object | None,
    referenced_by: str,
) -> None:
    _add_ref(refs, ExtensionItemKind.WORK_ITEM_FAMILY, family_id, referenced_by)
    if family is None:
        return
    family_ref = f"work item family {getattr(family, 'family_id', family_id)!r} selected by {referenced_by}"
    _add_ref(
        refs,
        ExtensionItemKind.DOCUMENT_ADAPTER,
        getattr(family, "document_adapter_id", None),
        family_ref,
    )
    _add_ref(
        refs,
        ExtensionItemKind.QUEUE_LIFECYCLE_POLICY,
        getattr(family, "queue_lifecycle_adapter_id", None),
        family_ref,
    )


def _selected_graph_node_ids(graph_loops: dict[Plane, GraphLoopDefinition]) -> set[str]:
    return {
        node.node_id
        for graph_loop in graph_loops.values()
        for node in graph_loop.nodes
    }


def _collect_runtime_effect_refs(
    *,
    refs: list[_OwnershipRef],
    graph_loops: dict[Plane, GraphLoopDefinition],
    workflow_primitives: object | None,
    families_by_id: dict[str, object],
) -> None:
    if workflow_primitives is None:
        return

    node_refs = {
        node.node_id: f"graph {graph_loop.loop_id!r} node {node.node_id!r}"
        for graph_loop in graph_loops.values()
        for node in graph_loop.nodes
    }
    selected_node_ids = _selected_graph_node_ids(graph_loops)
    selected_operation_ids: set[str] = set()
    selected_handler_ids: set[str] = set()

    for rule in getattr(workflow_primitives, "runtime_effect_rules", ()):
        source_node_id = getattr(rule, "source_node_id", None)
        if source_node_id not in selected_node_ids:
            continue
        rule_id = getattr(rule, "rule_id", None)
        rule_ref = f"runtime effect rule {rule_id!r} selected by {node_refs[source_node_id]}"
        _add_ref(refs, ExtensionItemKind.RUNTIME_EFFECT_RULE, rule_id, node_refs[source_node_id])

        operation_id = getattr(rule, "effect_operation_id", None)
        handler_id = getattr(rule, "handler_id", None)
        _add_ref(refs, ExtensionItemKind.RUNTIME_EFFECT_OPERATION, operation_id, rule_ref)
        _add_ref(refs, ExtensionItemKind.RUNTIME_EFFECT_HANDLER, handler_id, rule_ref)
        if operation_id is not None:
            selected_operation_ids.add(str(operation_id))
        if handler_id is not None:
            selected_handler_ids.add(str(handler_id))

        family_id = getattr(rule, "destination_family_id", None)
        if family_id is not None:
            _add_family_refs(
                refs,
                family_id=family_id,
                family=families_by_id.get(str(family_id)),
                referenced_by=rule_ref,
            )
        for plan_id in (
            getattr(rule, "lifecycle_mutation_plan_id", None),
            getattr(rule, "source_completion_lifecycle_mutation_plan_id", None),
            getattr(rule, "source_blocking_lifecycle_mutation_plan_id", None),
        ):
            _add_ref(refs, ExtensionItemKind.LIFECYCLE_MUTATION_PLAN, plan_id, rule_ref)
        for plan_map_name in (
            "source_completion_lifecycle_mutation_plan_ids_by_family",
            "source_blocking_lifecycle_mutation_plan_ids_by_family",
        ):
            for family_id, plan_id in getattr(rule, plan_map_name, {}).items():
                _add_family_refs(
                    refs,
                    family_id=family_id,
                    family=families_by_id.get(str(family_id)),
                    referenced_by=rule_ref,
                )
                _add_ref(refs, ExtensionItemKind.LIFECYCLE_MUTATION_PLAN, plan_id, rule_ref)
        for artifact_id in getattr(rule, "required_run_artifacts", ()):
            _add_ref(refs, ExtensionItemKind.ARTIFACT_CONTRACT, artifact_id, rule_ref)

    operations_by_id = _map_by_attr(workflow_primitives, "runtime_effect_operations", "operation_id")
    handlers_by_id = _map_by_attr(workflow_primitives, "runtime_effect_handlers", "handler_id")
    runners = tuple(getattr(workflow_primitives, "runtime_effect_runners", ()))

    for operation_id in tuple(selected_operation_ids):
        operation = operations_by_id.get(operation_id)
        if operation is None:
            continue
        operation_ref = f"runtime effect operation {operation_id!r}"
        for handler_id in getattr(operation, "legacy_handler_ids", ()):
            _add_ref(refs, ExtensionItemKind.RUNTIME_EFFECT_HANDLER, handler_id, operation_ref)
            selected_handler_ids.add(str(handler_id))
        for artifact_id in (
            *getattr(operation, "required_artifacts", ()),
            *getattr(operation, "produced_artifacts", ()),
        ):
            _add_ref(refs, ExtensionItemKind.ARTIFACT_CONTRACT, artifact_id, operation_ref)
        for step in getattr(operation, "steps", ()):
            step_ref = f"{operation_ref} step {getattr(step, 'step_id', '<unknown>')!r}"
            _add_ref(
                refs,
                ExtensionItemKind.RUNTIME_EFFECT_PRIMITIVE,
                getattr(step, "primitive_id", None),
                step_ref,
            )
            _add_ref(
                refs,
                ExtensionItemKind.RUNTIME_EFFECT_STORE,
                getattr(step, "store_id", None),
                step_ref,
            )
            for validator_id in getattr(step, "validator_ids", ()):
                _add_ref(refs, ExtensionItemKind.RUNTIME_EFFECT_VALIDATOR, validator_id, step_ref)
            for artifact_id in getattr(step, "reads_artifact_ids", ()):
                _add_ref(refs, ExtensionItemKind.ARTIFACT_CONTRACT, artifact_id, step_ref)
        for mapping in getattr(operation, "failure_mappings", ()):
            _add_ref(
                refs,
                ExtensionItemKind.RUNTIME_EFFECT_VALIDATOR,
                getattr(mapping, "validator_id", None),
                operation_ref,
            )
        for repair in getattr(operation, "repair_closure_contracts", ()):
            _add_ref(
                refs,
                ExtensionItemKind.RUNTIME_EFFECT_OPERATION,
                getattr(repair, "repair_operation_id", None),
                operation_ref,
            )
            for artifact_id in getattr(repair, "required_repair_evidence_artifact_ids", ()):
                _add_ref(refs, ExtensionItemKind.ARTIFACT_CONTRACT, artifact_id, operation_ref)
            family_id = getattr(repair, "affected_source_family_id", None)
            if family_id is not None:
                _add_family_refs(
                    refs,
                    family_id=family_id,
                    family=families_by_id.get(str(family_id)),
                    referenced_by=operation_ref,
                )

    for handler_id in tuple(selected_handler_ids):
        handler = handlers_by_id.get(handler_id)
        if handler is None:
            continue
        handler_ref = f"runtime effect handler {handler_id!r}"
        for family_id in getattr(handler, "allowed_source_families", ()):
            _add_family_refs(
                refs,
                family_id=family_id,
                family=families_by_id.get(str(family_id)),
                referenced_by=handler_ref,
            )
        for family_id in getattr(handler, "destination_kinds", ()):
            _add_family_refs(
                refs,
                family_id=family_id,
                family=families_by_id.get(str(family_id)),
                referenced_by=handler_ref,
            )
        for artifact_id in (
            *getattr(handler, "required_artifacts", ()),
            *getattr(handler, "optional_artifacts", ()),
        ):
            _add_ref(refs, ExtensionItemKind.ARTIFACT_CONTRACT, artifact_id, handler_ref)

    for runner in runners:
        runner_operations = {str(item) for item in getattr(runner, "operation_ids", ())}
        runner_handlers = {str(item) for item in getattr(runner, "legacy_handler_ids", ())}
        if not (
            runner_operations & selected_operation_ids
            or runner_handlers & selected_handler_ids
        ):
            continue
        runner_ref = f"runtime effect runner {getattr(runner, 'runner_id', '<unknown>')!r}"
        _add_ref(
            refs,
            ExtensionItemKind.RUNTIME_EFFECT_RUNNER,
            getattr(runner, "runner_id", None),
            runner_ref,
        )
        for operation_id in runner_operations & selected_operation_ids:
            _add_ref(refs, ExtensionItemKind.RUNTIME_EFFECT_OPERATION, operation_id, runner_ref)
        for handler_id in runner_handlers & selected_handler_ids:
            _add_ref(refs, ExtensionItemKind.RUNTIME_EFFECT_HANDLER, handler_id, runner_ref)


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


def _runtime_failure_policy_activation_ref(
    policy: RuntimeFailurePolicyDefinition,
    *,
    active_planes: tuple[Plane, ...],
    graph_loops: dict[Plane, GraphLoopDefinition],
    graph_node_ids_by_plane: dict[Plane, set[str]],
    graph_stage_kind_ids_by_plane: dict[Plane, set[str]],
) -> str:
    policy_id = getattr(policy, "policy_id", "<unknown>")
    activating_node_ids = _policy_activating_node_ids(policy)
    if not active_planes:
        return f"runtime failure policy {policy_id!r} active"
    if not activating_node_ids:
        return f"runtime failure policy {policy_id!r} active for plane {active_planes[0].value!r}"
    for plane in active_planes:
        plane_node_ids = graph_node_ids_by_plane.get(plane, set())
        plane_stage_kind_ids = graph_stage_kind_ids_by_plane.get(plane, set())
        graph_loop = graph_loops.get(plane)
        if graph_loop is None:
            continue
        for node_id in activating_node_ids:
            if node_id in plane_node_ids:
                return (
                    f"runtime failure policy {policy_id!r} active for plane {plane.value!r} "
                    f"via graph {graph_loop.loop_id!r} node {node_id!r}"
                )
            if node_id in plane_stage_kind_ids:
                return (
                    f"runtime failure policy {policy_id!r} active for plane {plane.value!r} "
                    f"via stage kind {node_id!r}"
                )
    return f"runtime failure policy {policy_id!r} active for plane {active_planes[0].value!r}"


def _collect_runtime_failure_policy_refs(
    *,
    refs: list[_OwnershipRef],
    graph_loops: dict[Plane, GraphLoopDefinition],
    workflow_primitives: object | None,
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    families_by_id: dict[str, object],
) -> None:
    if workflow_primitives is None:
        return

    graph_node_ids_by_plane = {
        plane: {node.node_id for node in graph.nodes}
        for plane, graph in graph_loops.items()
    }
    graph_stage_kind_ids_by_plane = {
        plane: {node.stage_kind_id for node in graph.nodes}
        for plane, graph in graph_loops.items()
    }
    selected_node_ids = _selected_graph_node_ids(graph_loops)

    for policy in getattr(workflow_primitives, "runtime_failure_policies", ()):
        active_planes = _runtime_failure_policy_active_planes(
            policy,
            graph_node_ids_by_plane=graph_node_ids_by_plane,
            graph_stage_kind_ids_by_plane=graph_stage_kind_ids_by_plane,
        )
        if not active_planes:
            continue

        policy_ref = _runtime_failure_policy_activation_ref(
            policy,
            active_planes=active_planes,
            graph_loops=graph_loops,
            graph_node_ids_by_plane=graph_node_ids_by_plane,
            graph_stage_kind_ids_by_plane=graph_stage_kind_ids_by_plane,
        )
        policy_id = getattr(policy, "policy_id", None)
        _add_ref(refs, ExtensionItemKind.RUNTIME_FAILURE_POLICY, policy_id, policy_ref)

        for family_id in getattr(policy, "applies_to_families", ()):
            _add_family_refs(
                refs,
                family_id=family_id,
                family=families_by_id.get(str(family_id)),
                referenced_by=policy_ref,
            )
        for operation_id in getattr(policy, "applies_to_operation_ids", ()):
            _add_ref(refs, ExtensionItemKind.RUNTIME_EFFECT_OPERATION, operation_id, policy_ref)
        for handler_id in getattr(policy, "applies_to_handler_ids", ()):
            _add_ref(refs, ExtensionItemKind.RUNTIME_EFFECT_HANDLER, handler_id, policy_ref)

        for source_node_id in getattr(policy, "applies_to_source_node_ids", ()):
            if source_node_id in selected_node_ids:
                continue
            if source_node_id in stage_kinds:
                _add_ref(
                    refs,
                    ExtensionItemKind.STAGE_KIND,
                    source_node_id,
                    f"{policy_ref} source selector stage kind {source_node_id!r}",
                )

        recovery_node_id = getattr(policy, "recovery_node_id", None)
        if recovery_node_id is not None and recovery_node_id not in selected_node_ids:
            if recovery_node_id in stage_kinds:
                _add_ref(
                    refs,
                    ExtensionItemKind.STAGE_KIND,
                    recovery_node_id,
                    f"{policy_ref} recovery selector stage kind {recovery_node_id!r}",
                )

        target_node_id = getattr(policy, "target_node_id", None)
        if target_node_id is not None and target_node_id not in selected_node_ids:
            if target_node_id in stage_kinds:
                _add_ref(
                    refs,
                    ExtensionItemKind.STAGE_KIND,
                    target_node_id,
                    f"{policy_ref} target selector stage kind {target_node_id!r}",
                )

        for mapping in getattr(policy, "repair_closure_mappings", ()):
            mapping_ref = (
                f"runtime failure policy {policy_id!r} repair closure mapping "
                f"{getattr(mapping, 'source_operation_id', '<unknown>')!r}/"
                f"{getattr(mapping, 'failure_class', '<unknown>')!r}"
            )
            _add_ref(
                refs,
                ExtensionItemKind.RUNTIME_EFFECT_OPERATION,
                getattr(mapping, "source_operation_id", None),
                mapping_ref,
            )
            _add_ref(
                refs,
                ExtensionItemKind.RUNTIME_EFFECT_OPERATION,
                getattr(mapping, "repair_operation_id", None),
                mapping_ref,
            )
            family_id = getattr(mapping, "affected_source_family_id", None)
            if family_id is not None:
                _add_family_refs(
                    refs,
                    family_id=family_id,
                    family=families_by_id.get(str(family_id)),
                    referenced_by=mapping_ref,
                )
            for artifact_id in getattr(mapping, "required_repair_evidence_artifact_ids", ()):
                _add_ref(refs, ExtensionItemKind.ARTIFACT_CONTRACT, artifact_id, mapping_ref)


def _validate_manifest_items_exist(
    *,
    ownership: dict[tuple[ExtensionItemKind, str], _OwnedItem],
    known_ids: dict[ExtensionItemKind, set[str]],
) -> None:
    for owned in ownership.values():
        ids_for_kind = known_ids.get(owned.item_kind)
        if ids_for_kind is not None and owned.item_id.lower() not in ids_for_kind:
            raise CompilerValidationError(
                f"Extension manifest {owned.package_id!r} declares unknown "
                f"registry item {owned.item_kind.value!r} {owned.item_id!r}."
            )


def _collect_referenced_items(
    *,
    graph_loops: dict[Plane, GraphLoopDefinition],
    stage_kinds: dict[str, RegisteredStageKindDefinition],
    terminal_actions_by_id: dict[str, object] | None,
    workflow_primitives: object | None,
    scheduler_policy: object | None,
    recovery_policies: tuple[object, ...] | None,
) -> tuple[_OwnershipRef, ...]:
    refs: list[_OwnershipRef] = []
    profiles_by_id = _map_by_attr(workflow_primitives, "request_context_profiles", "profile_id")
    families_by_id = _map_by_attr(workflow_primitives, "work_item_families", "family_id")

    for plane, graph_loop in graph_loops.items():
        for node in graph_loop.nodes:
            node_ref = f"graph {graph_loop.loop_id!r} node {node.node_id!r}"
            refs.append(_OwnershipRef(ExtensionItemKind.STAGE_KIND, node.stage_kind_id, node_ref))
            stage_kind = stage_kinds.get(node.stage_kind_id)
            profile_id = node.request_context_profile_id or getattr(stage_kind, "request_context_profile_id", None)
            render_plan_id = node.context_render_plan_id or getattr(stage_kind, "context_render_plan_id", None)
            if profile_id is not None:
                refs.append(_OwnershipRef(ExtensionItemKind.REQUEST_CONTEXT_PROFILE, profile_id, node_ref))
                profile = profiles_by_id.get(profile_id)
                if profile is not None:
                    profile_ref = f"request context profile {profile_id!r}"
                    refs.append(
                        _OwnershipRef(
                            ExtensionItemKind.CONTEXT_PROVIDER,
                            profile.provider_id,
                            profile_ref,
                        )
                    )
                    refs.append(
                        _OwnershipRef(
                            ExtensionItemKind.REQUEST_CONTEXT_RENDER_PLAN,
                            profile.primary_render_plan_id,
                            profile_ref,
                        )
                    )
            if render_plan_id is not None:
                refs.append(_OwnershipRef(ExtensionItemKind.REQUEST_CONTEXT_RENDER_PLAN, render_plan_id, node_ref))
        for entry in graph_loop.entry_nodes:
            family = families_by_id.get(entry.entry_key)
            if family is not None:
                entry_key = getattr(entry.entry_key, "value", entry.entry_key)
                _add_family_refs(
                    refs,
                    family_id=family.family_id,
                    family=family,
                    referenced_by=f"graph {graph_loop.loop_id!r} entry {entry_key!r}",
                )
        for terminal_state in graph_loop.terminal_states:
            state_ref = f"graph {graph_loop.loop_id!r} terminal state {terminal_state.terminal_state_id!r}"
            refs.append(_OwnershipRef(ExtensionItemKind.TERMINAL_ACTION, terminal_state.terminal_action_id, state_ref))
            if terminal_actions_by_id is not None:
                action = terminal_actions_by_id.get(terminal_state.terminal_action_id)
                if action is not None:
                    lifecycle_plan_id = getattr(action, "lifecycle_mutation_plan_id", None)
                    runtime_operation_id = getattr(action, "runtime_operation_id", None)
                    if lifecycle_plan_id is not None:
                        action_ref = f"terminal action {terminal_state.terminal_action_id!r}"
                        refs.append(
                            _OwnershipRef(
                                ExtensionItemKind.LIFECYCLE_MUTATION_PLAN,
                                lifecycle_plan_id,
                                action_ref,
                            )
                        )
                    if runtime_operation_id is not None:
                        action_ref = f"terminal action {terminal_state.terminal_action_id!r}"
                        refs.append(
                            _OwnershipRef(
                                ExtensionItemKind.RUNTIME_OPERATION,
                                runtime_operation_id,
                                action_ref,
                            )
                        )

    if scheduler_policy is not None:
        scheduler_ref = f"scheduler policy {getattr(scheduler_policy, 'policy_id', '<unknown>')!r}"
        refs.append(_OwnershipRef(ExtensionItemKind.SCHEDULER_POLICY, getattr(scheduler_policy, "policy_id"), scheduler_ref))
        for lane in getattr(scheduler_policy, "lanes", ()):
            lane_ref = f"{scheduler_ref} lane {getattr(lane, 'lane_id', '<unknown>')!r}"
            refs.append(_OwnershipRef(ExtensionItemKind.QUEUE_CLAIM_POLICY, getattr(lane, "claim_policy_id"), lane_ref))
            for family_id in getattr(lane, "allowed_family_ids", ()):
                _add_family_refs(
                    refs,
                    family_id=family_id,
                    family=families_by_id.get(str(family_id)),
                    referenced_by=lane_ref,
                )
        for claim_policy in getattr(scheduler_policy, "claim_policies_by_plane", {}).values():
            refs.append(_OwnershipRef(ExtensionItemKind.QUEUE_CLAIM_POLICY, getattr(claim_policy, "policy_id"), scheduler_ref))
            for family_id in getattr(claim_policy, "family_order", ()):
                claim_ref = (
                    f"queue claim policy {getattr(claim_policy, 'policy_id', '<unknown>')!r}"
                )
                _add_family_refs(
                    refs,
                    family_id=family_id,
                    family=families_by_id.get(str(family_id)),
                    referenced_by=claim_ref,
                )

    for policy in recovery_policies or ():
        refs.append(
            _OwnershipRef(
                ExtensionItemKind.RECOVERY_POLICY,
                getattr(policy, "policy_id"),
                f"mode-selected recovery policy {getattr(policy, 'policy_id', '<unknown>')!r}",
            )
        )

    _collect_runtime_effect_refs(
        refs=refs,
        graph_loops=graph_loops,
        workflow_primitives=workflow_primitives,
        families_by_id=families_by_id,
    )
    _collect_runtime_failure_policy_refs(
        refs=refs,
        graph_loops=graph_loops,
        workflow_primitives=workflow_primitives,
        stage_kinds=stage_kinds,
        families_by_id=families_by_id,
    )

    return tuple(ref for ref in refs if ref.item_id is not None)


def _map_by_attr(source: object | None, collection_attr: str, id_attr: str) -> dict[str, object]:
    if source is None:
        return {}
    return {
        str(getattr(item, id_attr)): item
        for item in getattr(source, collection_attr, ())
        if getattr(item, id_attr, None) is not None
    }


def _parse_version_safe(version_str: str) -> Version | None:
    """Parse a semver string safely, returning None on failure."""
    try:
        return Version(version_str)
    except InvalidVersion:
        return None


# Reverse index for canonical domain ownership validation.
_PACKAGE_ID_TO_CANONICAL_DOMAIN: dict[str, ExtensionDomain] = {
    pkg_id: domain for domain, pkg_id in _BUILTIN_DOMAIN_PACKAGE_IDS.items()
}


def validate_required_extensions(
    *,
    mode: ModeDefinition,
    discovered_manifests: tuple[ExtensionPackageManifest, ...],
    graph_loops: dict[Plane, GraphLoopDefinition] | None = None,
    stage_kinds: dict[str, RegisteredStageKindDefinition] | None = None,
    terminal_actions_by_id: dict[str, object] | None = None,
    workflow_primitives: object | None = None,
    scheduler_policy: object | None = None,
    recovery_policies: tuple[object, ...] | None = None,
) -> None:
    """Validate that every required extension declared by a mode is available.

    Rejects with clear CompilerValidationError messages when:
    - A required extension package_id is not found among discovered manifests
    - A required extension declares a min_version that the discovered manifest
      does not satisfy
    - A discovered extension manifest declares a domain that conflicts with
      the canonical domain mapping (source-of-truth ownership check)
    - A graph loop references stage-kind or terminal-action vocabulary owned
      by an extension domain that the mode does not declare as required

    When graph_loops and stage_kinds are provided, also checks that all
    extension-domain vocabulary used by the compiled plan is covered by
    the mode's required_extensions declarations.
    """
    required_extensions_raw = getattr(mode, "required_extensions", None) or ()

    manifests_by_id: dict[str, ExtensionPackageManifest] = {
        manifest.package_id: manifest for manifest in discovered_manifests
    }
    ownership = _build_manifest_ownership_index(manifests_by_id)
    known_ids = _known_registry_ids(
        stage_kinds=stage_kinds,
        terminal_actions_by_id=terminal_actions_by_id,
        workflow_primitives=workflow_primitives,
        scheduler_policy=scheduler_policy,
        recovery_policies=recovery_policies,
    )
    _validate_manifest_items_exist(ownership=ownership, known_ids=known_ids)

    # --- Canonical domain ownership cross-validation ---
    # Each discovered extension manifest must declare a domain that matches
    # the canonical source-of-truth mapping.  This catches conflicting
    # owners when per-manifest metadata disagrees with the central mapping.
    for manifest in discovered_manifests:
        canonical_domain = _PACKAGE_ID_TO_CANONICAL_DOMAIN.get(manifest.package_id)
        if canonical_domain is not None and manifest.domain != canonical_domain:
            raise CompilerValidationError(
                f"Extension manifest {manifest.package_id!r} declares domain "
                f"{manifest.domain.value!r} but canonical source of truth expects "
                f"{canonical_domain.value!r}.  Fix the manifest domain to match "
                f"the canonical mapping."
            )

    declared_extension_ids: set[str] = set()

    for raw_entry in required_extensions_raw:
        if isinstance(raw_entry, dict):
            try:
                declaration = RequiredExtensionDeclaration.model_validate(raw_entry)
            except Exception as exc:
                raise CompilerValidationError(
                    f"Invalid required-extension declaration in mode {mode.mode_id!r}: {exc}"
                ) from exc
        elif isinstance(raw_entry, RequiredExtensionDeclaration):
            declaration = raw_entry
        else:
            raise CompilerValidationError(
                f"Invalid required-extension declaration type in mode {mode.mode_id!r}: "
                f"expected dict or RequiredExtensionDeclaration, got {type(raw_entry).__name__}"
            )

        extension_id = declaration.extension_package_id
        declared_extension_ids.add(extension_id)
        manifest = manifests_by_id.get(extension_id)

        if manifest is None:
            available = sorted(manifests_by_id) if manifests_by_id else []
            available_msg = (
                f" Available: {', '.join(available)}"
                if available
                else " No extension manifests discovered."
            )
            raise CompilerValidationError(
                f"Mode {mode.mode_id!r} requires extension package {extension_id!r} "
                f"which was not found among discovered manifests.{available_msg}"
            )

        if declaration.min_version is not None:
            declared_version = _parse_version_safe(manifest.version)
            min_version = _parse_version_safe(declaration.min_version)

            if declared_version is not None and min_version is not None:
                if declared_version < min_version:
                    raise CompilerValidationError(
                        f"Mode {mode.mode_id!r} requires extension package "
                        f"{extension_id!r} >= {declaration.min_version}, "
                        f"but discovered manifest has version {manifest.version}"
                    )

    # --- Undeclared manifest-owned vocabulary check ---
    if graph_loops is not None and stage_kinds is not None:
        referenced_items = _collect_referenced_items(
            graph_loops=graph_loops,
            stage_kinds=stage_kinds,
            terminal_actions_by_id=terminal_actions_by_id,
            workflow_primitives=workflow_primitives,
            scheduler_policy=scheduler_policy,
            recovery_policies=recovery_policies,
        )
        for ref in referenced_items:
            owned = ownership.get(_ownership_key(ref.item_kind, ref.item_id))
            if owned is None or owned.package_id in declared_extension_ids:
                continue
            raise CompilerValidationError(
                f"Mode {mode.mode_id!r} references extension-owned vocabulary "
                f"without declaring required extension package {owned.package_id!r}: "
                f"item_kind={owned.item_kind.value!r}, item_id={owned.item_id!r}, "
                f"referenced_by={ref.referenced_by}. Add {owned.package_id!r} "
                f"to required_extensions."
            )


__all__ = [
    "validate_required_extensions",
]
