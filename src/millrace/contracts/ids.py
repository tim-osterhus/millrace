"""String-backed identifier value objects used by runtime contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _StringBackedId:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("identifier value must be non-empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class WorkflowId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowVersion(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class GraphId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class PartitionId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class QueueFamilyId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactSchemaId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class EffectDeclarationId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class AssetId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class CapabilityId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class StageKindId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class OutcomeId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class ActionId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class WaitStateId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class CounterId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class CompletionBehaviorId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class RemediationPolicyId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class FanoutId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class InterventionOptionId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class OperatorWaitId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class RecoveryPolicyId(_StringBackedId):
    pass


@dataclass(frozen=True, slots=True)
class RunnerBindingId(_StringBackedId):
    pass


__all__ = (
    "ActionId",
    "ArtifactSchemaId",
    "AssetId",
    "CapabilityId",
    "CompletionBehaviorId",
    "CounterId",
    "EffectDeclarationId",
    "FanoutId",
    "GraphId",
    "InterventionOptionId",
    "OperatorWaitId",
    "OutcomeId",
    "PartitionId",
    "QueueFamilyId",
    "RecoveryPolicyId",
    "RemediationPolicyId",
    "RunnerBindingId",
    "StageKindId",
    "WaitStateId",
    "WorkflowId",
    "WorkflowVersion",
)
