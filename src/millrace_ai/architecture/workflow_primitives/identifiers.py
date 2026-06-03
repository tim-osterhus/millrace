"""Shared workflow primitive identifier aliases and builtin resolver helpers."""

from __future__ import annotations

from typing import Literal

from ..common import normalize_canonical_id

WorkflowPrimitiveId = str
WorkItemFamilyId = str
DocumentAdapterId = str
QueueLifecycleAdapterId = str
QueueClaimPolicyId = str
TerminalActionId = str
LifecycleMutationPlanId = str
RuntimeEffectHandlerId = str
TerminalActionRuntimeOperationId = str
RuntimeEffectOperationRunnerId = str
RuntimeEffectRuleId = str
RequestContextProfileId = str
RequestContextProviderId = str
RequestContextRenderPlanId = str
ArtifactContractId = str
RuntimeEffectMutationPhaseValue = Literal["pre_mutation", "partial_mutation", "unknown"]

_BUILTIN_QUEUE_LIFECYCLE_ADAPTER_IDS: dict[str, QueueLifecycleAdapterId] = {
    "task": "builtin.queue_lifecycle.task",
    "spec": "builtin.queue_lifecycle.spec",
    "probe": "builtin.queue_lifecycle.probe",
    "incident": "builtin.queue_lifecycle.incident",
    "learning_request": "builtin.queue_lifecycle.learning_request",
    "blueprint_draft": "builtin.queue_lifecycle.blueprint_draft",
}


def builtin_queue_lifecycle_adapter_id_for_family(family_id: str) -> QueueLifecycleAdapterId | None:
    normalized_family_id = normalize_canonical_id(family_id, field_label="family_id")
    return _BUILTIN_QUEUE_LIFECYCLE_ADAPTER_IDS.get(normalized_family_id)
