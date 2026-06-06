"""Workflow primitive asset loading."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    LifecycleMutationPlanDefinition,
    PlaneQueueClaimPolicyDefinition,
    RequestContextProfileDefinition,
    RequestContextProviderDefinition,
    RequestContextRenderPlan,
    RuntimeEffectHandlerDefinition,
    RuntimeEffectOperationDefinition,
    RuntimeEffectOperationRunnerDefinition,
    RuntimeEffectPrimitiveDefinition,
    RuntimeEffectRuleDefinition,
    RuntimeEffectStoreDefinition,
    RuntimeEffectValidatorDefinition,
    RuntimeFailurePolicyDefinition,
    RuntimeOperationDefinition,
    TerminalActionDefinition,
    WorkflowPlaneSchedulerPolicyDefinition,
    WorkflowRecoveryPolicyDefinition,
    WorkItemDocumentAdapterDefinition,
    WorkItemFamilyDefinition,
    WorkspaceSchemaEpochDefinition,
)
from millrace_ai.errors import AssetValidationError

from .effect_operations import (
    discover_effect_store_definitions,
    discover_effect_validator_definitions,
    discover_runtime_effect_operation_definitions,
    discover_runtime_effect_primitive_definitions,
    discover_runtime_operation_definitions,
)

ASSETS_ROOT = Path(__file__).resolve().parent
ARTIFACT_CONTRACT_REGISTRY_ROOT = Path("registry/artifact_contracts")
REQUEST_CONTEXT_PROFILE_REGISTRY_ROOT = Path("registry/request_context_profiles")
REQUEST_CONTEXT_PROVIDER_REGISTRY_ROOT = Path("registry/request_context_providers")
REQUEST_CONTEXT_RENDER_PLAN_REGISTRY_ROOT = Path("registry/request_context_render_plans")
WORK_ITEM_FAMILY_REGISTRY_ROOT = Path("registry/work_item_families")
DOCUMENT_ADAPTER_REGISTRY_ROOT = Path("registry/document_adapters")
QUEUE_CLAIM_POLICY_REGISTRY_ROOT = Path("registry/queue_claim_policies")
TERMINAL_ACTION_REGISTRY_ROOT = Path("registry/terminal_actions")
LIFECYCLE_MUTATION_PLAN_REGISTRY_ROOT = Path("registry/lifecycle_mutation_plans")
RUNTIME_EFFECT_HANDLER_REGISTRY_ROOT = Path("registry/runtime_effect_handlers")
RUNTIME_EFFECT_RUNNER_REGISTRY_ROOT = Path("registry/runtime_effect_runners")
RUNTIME_EFFECT_RULE_REGISTRY_ROOT = Path("registry/runtime_effect_rules")
RECOVERY_POLICY_REGISTRY_ROOT = Path("registry/recovery_policies")
RUNTIME_FAILURE_POLICY_REGISTRY_ROOT = Path("registry/runtime_failure_policies")
SCHEDULER_POLICY_REGISTRY_ROOT = Path("registry/scheduler_policies")
WORKSPACE_SCHEMA_EPOCH_REGISTRY_ROOT = Path("registry/workspace_schema_epochs")

BUILTIN_WORK_ITEM_FAMILY_PATHS: dict[str, Path] = {
    "task": WORK_ITEM_FAMILY_REGISTRY_ROOT / "task.json",
    "spec": WORK_ITEM_FAMILY_REGISTRY_ROOT / "spec.json",
    "probe": WORK_ITEM_FAMILY_REGISTRY_ROOT / "probe.json",
    "incident": WORK_ITEM_FAMILY_REGISTRY_ROOT / "incident.json",
    "learning_request": WORK_ITEM_FAMILY_REGISTRY_ROOT / "learning_request.json",
    "blueprint_draft": WORK_ITEM_FAMILY_REGISTRY_ROOT / "blueprint_draft.json",
}
SHIPPED_WORK_ITEM_FAMILY_IDS: tuple[str, ...] = tuple(BUILTIN_WORK_ITEM_FAMILY_PATHS)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class WorkflowAssetError(AssetValidationError):
    """Raised when workflow primitive assets cannot be resolved or validated."""


@dataclass(frozen=True, slots=True)
class WorkflowPrimitiveBundle:
    artifact_contracts: tuple[ArtifactContractDefinition, ...]
    request_context_profiles: tuple[RequestContextProfileDefinition, ...]
    request_context_providers: tuple[RequestContextProviderDefinition, ...]
    request_context_render_plans: tuple[RequestContextRenderPlan, ...]
    work_item_families: tuple[WorkItemFamilyDefinition, ...]
    document_adapters: tuple[WorkItemDocumentAdapterDefinition, ...]
    queue_claim_policies: tuple[PlaneQueueClaimPolicyDefinition, ...]
    terminal_actions: tuple[TerminalActionDefinition, ...]
    lifecycle_mutation_plans: tuple[LifecycleMutationPlanDefinition, ...]
    runtime_effect_handlers: tuple[RuntimeEffectHandlerDefinition, ...]
    runtime_effect_runners: tuple[RuntimeEffectOperationRunnerDefinition, ...]
    runtime_effect_rules: tuple[RuntimeEffectRuleDefinition, ...]
    effect_stores: tuple[RuntimeEffectStoreDefinition, ...]
    effect_validators: tuple[RuntimeEffectValidatorDefinition, ...]
    runtime_effect_operations: tuple[RuntimeEffectOperationDefinition, ...]
    runtime_operations: tuple[RuntimeOperationDefinition, ...]
    runtime_effect_primitives: tuple[RuntimeEffectPrimitiveDefinition, ...]
    recovery_policies: tuple[WorkflowRecoveryPolicyDefinition, ...]
    runtime_failure_policies: tuple[RuntimeFailurePolicyDefinition, ...]
    scheduler_policies: tuple[WorkflowPlaneSchedulerPolicyDefinition, ...]
    workspace_schema_epoch: WorkspaceSchemaEpochDefinition | None = None


def load_builtin_workflow_primitives(
    *,
    assets_root: Path | None = None,
) -> WorkflowPrimitiveBundle:
    return WorkflowPrimitiveBundle(
        artifact_contracts=discover_artifact_contract_definitions(assets_root=assets_root),
        request_context_profiles=discover_request_context_profile_definitions(assets_root=assets_root),
        request_context_providers=discover_request_context_provider_definitions(assets_root=assets_root),
        request_context_render_plans=discover_request_context_render_plan_definitions(assets_root=assets_root),
        work_item_families=discover_work_item_family_definitions(assets_root=assets_root),
        document_adapters=discover_work_item_document_adapter_definitions(assets_root=assets_root),
        queue_claim_policies=discover_plane_queue_claim_policy_definitions(assets_root=assets_root),
        terminal_actions=discover_terminal_action_definitions(assets_root=assets_root),
        lifecycle_mutation_plans=discover_lifecycle_mutation_plan_definitions(assets_root=assets_root),
        runtime_effect_handlers=discover_runtime_effect_handler_definitions(assets_root=assets_root),
        runtime_effect_runners=discover_runtime_effect_runner_definitions(assets_root=assets_root),
        runtime_effect_rules=discover_runtime_effect_rule_definitions(assets_root=assets_root),
        effect_stores=discover_effect_store_definitions(assets_root=assets_root),
        effect_validators=discover_effect_validator_definitions(assets_root=assets_root),
        runtime_effect_operations=discover_runtime_effect_operation_definitions(assets_root=assets_root),
        runtime_operations=discover_runtime_operation_definitions(assets_root=assets_root),
        runtime_effect_primitives=discover_runtime_effect_primitive_definitions(assets_root=assets_root),
        recovery_policies=discover_workflow_recovery_policy_definitions(assets_root=assets_root),
        runtime_failure_policies=discover_runtime_failure_policy_definitions(assets_root=assets_root),
        scheduler_policies=discover_scheduler_policy_definitions(assets_root=assets_root),
        workspace_schema_epoch=load_workspace_schema_epoch_definition(assets_root=assets_root),
    )


def load_builtin_work_item_family_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[WorkItemFamilyDefinition, ...]:
    root = _resolve_assets_root(assets_root)
    return tuple(
        load_builtin_work_item_family_definition(family_id, assets_root=root)
        for family_id in SHIPPED_WORK_ITEM_FAMILY_IDS
    )


def load_builtin_work_item_family_definition(
    family_id: str,
    *,
    assets_root: Path | None = None,
) -> WorkItemFamilyDefinition:
    root = _resolve_assets_root(assets_root)
    relative_path = BUILTIN_WORK_ITEM_FAMILY_PATHS.get(family_id)
    if relative_path is None:
        raise WorkflowAssetError(f"Unknown built-in work item family id: {family_id}")
    asset_path = root / relative_path
    definitions = _load_definitions_at_path(
        asset_path,
        model=WorkItemFamilyDefinition,
        asset_kind="work item family",
    )
    if len(definitions) != 1:
        raise WorkflowAssetError(f"Expected exactly one work item family definition in asset: {asset_path}")
    family = definitions[0]
    if family.family_id != family_id:
        raise WorkflowAssetError(
            f"Work item family asset id mismatch: expected {family_id}, found {family.family_id}"
        )
    return family


def discover_work_item_family_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[WorkItemFamilyDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=WORK_ITEM_FAMILY_REGISTRY_ROOT,
        model=WorkItemFamilyDefinition,
        id_attr="family_id",
        asset_kind="work item family",
    )


def discover_artifact_contract_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[ArtifactContractDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=ARTIFACT_CONTRACT_REGISTRY_ROOT,
        model=ArtifactContractDefinition,
        id_attr="artifact_id",
        asset_kind="artifact contract",
    )


def discover_request_context_profile_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RequestContextProfileDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=REQUEST_CONTEXT_PROFILE_REGISTRY_ROOT,
        model=RequestContextProfileDefinition,
        id_attr="profile_id",
        asset_kind="request context profile",
    )


def discover_request_context_provider_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RequestContextProviderDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=REQUEST_CONTEXT_PROVIDER_REGISTRY_ROOT,
        model=RequestContextProviderDefinition,
        id_attr="provider_id",
        asset_kind="request context provider",
    )


def discover_request_context_render_plan_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RequestContextRenderPlan, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=REQUEST_CONTEXT_RENDER_PLAN_REGISTRY_ROOT,
        model=RequestContextRenderPlan,
        id_attr="render_plan_id",
        asset_kind="request context render plan",
    )


def discover_work_item_document_adapter_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[WorkItemDocumentAdapterDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=DOCUMENT_ADAPTER_REGISTRY_ROOT,
        model=WorkItemDocumentAdapterDefinition,
        id_attr="adapter_id",
        asset_kind="work item document adapter",
    )


def discover_plane_queue_claim_policy_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[PlaneQueueClaimPolicyDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=QUEUE_CLAIM_POLICY_REGISTRY_ROOT,
        model=PlaneQueueClaimPolicyDefinition,
        id_attr="policy_id",
        asset_kind="plane queue claim policy",
    )


def discover_terminal_action_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[TerminalActionDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=TERMINAL_ACTION_REGISTRY_ROOT,
        model=TerminalActionDefinition,
        id_attr="terminal_action_id",
        asset_kind="terminal action",
    )


def discover_lifecycle_mutation_plan_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[LifecycleMutationPlanDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=LIFECYCLE_MUTATION_PLAN_REGISTRY_ROOT,
        model=LifecycleMutationPlanDefinition,
        id_attr="plan_id",
        asset_kind="lifecycle mutation plan",
    )


def discover_runtime_effect_handler_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RuntimeEffectHandlerDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=RUNTIME_EFFECT_HANDLER_REGISTRY_ROOT,
        model=RuntimeEffectHandlerDefinition,
        id_attr="handler_id",
        asset_kind="runtime effect handler",
    )


def discover_runtime_effect_runner_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RuntimeEffectOperationRunnerDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=RUNTIME_EFFECT_RUNNER_REGISTRY_ROOT,
        model=RuntimeEffectOperationRunnerDefinition,
        id_attr="runner_id",
        asset_kind="runtime effect runner",
    )


def discover_runtime_effect_rule_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RuntimeEffectRuleDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=RUNTIME_EFFECT_RULE_REGISTRY_ROOT,
        model=RuntimeEffectRuleDefinition,
        id_attr="rule_id",
        asset_kind="runtime effect rule",
    )


def discover_workflow_recovery_policy_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[WorkflowRecoveryPolicyDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=RECOVERY_POLICY_REGISTRY_ROOT,
        model=WorkflowRecoveryPolicyDefinition,
        id_attr="policy_id",
        asset_kind="workflow recovery policy",
    )


def discover_scheduler_policy_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[WorkflowPlaneSchedulerPolicyDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=SCHEDULER_POLICY_REGISTRY_ROOT,
        model=WorkflowPlaneSchedulerPolicyDefinition,
        id_attr="policy_id",
        asset_kind="scheduler policy",
    )


def discover_runtime_failure_policy_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[RuntimeFailurePolicyDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=RUNTIME_FAILURE_POLICY_REGISTRY_ROOT,
        model=RuntimeFailurePolicyDefinition,
        id_attr="policy_id",
        asset_kind="runtime failure policy",
    )


def discover_workspace_schema_epoch_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[WorkspaceSchemaEpochDefinition, ...]:
    return _discover_definitions(
        assets_root=assets_root,
        registry_root=WORKSPACE_SCHEMA_EPOCH_REGISTRY_ROOT,
        model=WorkspaceSchemaEpochDefinition,
        id_attr="epoch_id",
        asset_kind="workspace schema epoch",
    )


def load_workspace_schema_epoch_definition(
    *,
    assets_root: Path | None = None,
) -> WorkspaceSchemaEpochDefinition | None:
    epochs = discover_workspace_schema_epoch_definitions(assets_root=assets_root)
    if not epochs:
        return None
    if len(epochs) > 1:
        raise WorkflowAssetError("Expected at most one workspace schema epoch definition")
    return epochs[0]


def _discover_definitions(
    *,
    assets_root: Path | None,
    registry_root: Path,
    model: type[_ModelT],
    id_attr: str,
    asset_kind: str,
) -> tuple[_ModelT, ...]:
    root = _resolve_assets_root(assets_root)
    discovered: list[_ModelT] = []
    seen_ids: dict[str, Path] = {}

    for asset_path in _discover_json_paths(root, registry_root):
        definitions = _load_definitions_at_path(asset_path, model=model, asset_kind=asset_kind)
        for definition in definitions:
            primitive_id = str(getattr(definition, id_attr))
            previous_path = seen_ids.get(primitive_id)
            if previous_path is not None:
                raise WorkflowAssetError(
                    f"Duplicate discovered {asset_kind} id: {primitive_id} "
                    f"({previous_path}, {asset_path})"
                )
            seen_ids[primitive_id] = asset_path
            discovered.append(definition)

    return tuple(sorted(discovered, key=lambda definition: str(getattr(definition, id_attr))))


def _load_definitions_at_path(
    path: Path,
    *,
    model: type[_ModelT],
    asset_kind: str,
) -> tuple[_ModelT, ...]:
    payload = _load_json_asset(path, asset_kind=asset_kind)
    items = _definition_items(payload, asset_kind=asset_kind, path=path)
    definitions: list[_ModelT] = []
    for item in items:
        try:
            definitions.append(model.model_validate(item))
        except ValidationError as exc:
            first_error = exc.errors()[0]["msg"] if exc.errors() else "validation failed"
            raise WorkflowAssetError(
                f"Invalid {asset_kind} definition in asset: {path} ({first_error})"
            ) from exc
    return tuple(definitions)


def _definition_items(payload: Any, *, asset_kind: str, path: Path) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, dict) and "definitions" in payload:
        definitions = payload["definitions"]
    else:
        definitions = payload

    if isinstance(definitions, dict):
        items = (definitions,)
    elif isinstance(definitions, list):
        items = tuple(definitions)
    else:
        raise WorkflowAssetError(f"Invalid JSON in {asset_kind} asset: {path}")

    if not all(isinstance(item, dict) for item in items):
        raise WorkflowAssetError(f"Invalid JSON in {asset_kind} asset: {path}")
    return items


def _discover_json_paths(assets_root: Path, registry_root: Path) -> tuple[Path, ...]:
    root = assets_root / registry_root
    if not root.is_dir():
        return ()
    return tuple(sorted(path for path in root.rglob("*.json") if path.is_file()))


def _load_json_asset(path: Path, *, asset_kind: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowAssetError(f"Cannot read {asset_kind} asset: {path}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowAssetError(f"Invalid JSON in {asset_kind} asset: {path}") from exc


def _resolve_assets_root(assets_root: Path | None) -> Path:
    if assets_root is None:
        return ASSETS_ROOT
    return Path(assets_root)


__all__ = [
    "ASSETS_ROOT",
    "ARTIFACT_CONTRACT_REGISTRY_ROOT",
    "BUILTIN_WORK_ITEM_FAMILY_PATHS",
    "DOCUMENT_ADAPTER_REGISTRY_ROOT",
    "LIFECYCLE_MUTATION_PLAN_REGISTRY_ROOT",
    "QUEUE_CLAIM_POLICY_REGISTRY_ROOT",
    "RECOVERY_POLICY_REGISTRY_ROOT",
    "REQUEST_CONTEXT_PROFILE_REGISTRY_ROOT",
    "REQUEST_CONTEXT_PROVIDER_REGISTRY_ROOT",
    "REQUEST_CONTEXT_RENDER_PLAN_REGISTRY_ROOT",
    "RUNTIME_EFFECT_HANDLER_REGISTRY_ROOT",
    "RUNTIME_EFFECT_RUNNER_REGISTRY_ROOT",
    "RUNTIME_EFFECT_RULE_REGISTRY_ROOT",
    "RUNTIME_FAILURE_POLICY_REGISTRY_ROOT",
    "SCHEDULER_POLICY_REGISTRY_ROOT",
    "SHIPPED_WORK_ITEM_FAMILY_IDS",
    "TERMINAL_ACTION_REGISTRY_ROOT",
    "WORK_ITEM_FAMILY_REGISTRY_ROOT",
    "WORKSPACE_SCHEMA_EPOCH_REGISTRY_ROOT",
    "WorkflowAssetError",
    "WorkflowPrimitiveBundle",
    "discover_artifact_contract_definitions",
    "discover_effect_store_definitions",
    "discover_effect_validator_definitions",
    "discover_lifecycle_mutation_plan_definitions",
    "discover_plane_queue_claim_policy_definitions",
    "discover_request_context_profile_definitions",
    "discover_request_context_provider_definitions",
    "discover_request_context_render_plan_definitions",
    "discover_runtime_effect_handler_definitions",
    "discover_runtime_effect_runner_definitions",
    "discover_runtime_effect_operation_definitions",
    "discover_runtime_effect_primitive_definitions",
    "discover_runtime_effect_rule_definitions",
    "discover_runtime_failure_policy_definitions",
    "discover_scheduler_policy_definitions",
    "discover_terminal_action_definitions",
    "discover_work_item_document_adapter_definitions",
    "discover_work_item_family_definitions",
    "discover_workflow_recovery_policy_definitions",
    "discover_workspace_schema_epoch_definitions",
    "load_builtin_work_item_family_definition",
    "load_builtin_work_item_family_definitions",
    "load_builtin_workflow_primitives",
    "load_workspace_schema_epoch_definition",
]
