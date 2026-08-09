"""Immutable compiled-plan authority contract records."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, ClassVar, TypeAlias, TypeVar, cast

from millrace.contracts.fingerprints import (
    AUTHORITY_FINGERPRINT_DOMAIN_PREFIX,
    AuthorityFingerprint,
)
from millrace.contracts.ids import (
    ActionId,
    ArtifactSchemaId,
    AssetId,
    CapabilityId,
    CompletionBehaviorId,
    CounterId,
    EffectDeclarationId,
    FanoutId,
    GraphId,
    InterventionOptionId,
    OperatorWaitId,
    OutcomeId,
    PartitionId,
    QueueFamilyId,
    RecoveryPolicyId,
    RemediationPolicyId,
    RunnerBindingId,
    StageKindId,
    WaitStateId,
    WorkflowId,
    WorkflowVersion,
)

AuthorityValue: TypeAlias = (
    str
    | int
    | bool
    | None
    | tuple["AuthorityValue", ...]
    | Mapping[str, "AuthorityValue"]
)

CanonicalValue: TypeAlias = (
    str | int | bool | None | list["CanonicalValue"] | dict[str, "CanonicalValue"]
)

T = TypeVar("T")


class UnsupportedAuthorityValueError(ValueError):
    """Raised when selected authority contains a non-canonical value type."""


class CanonicalAuthorityError(ValueError):
    """Raised when selected authority cannot be canonically represented."""


def freeze_authority_value(value: object) -> AuthorityValue:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                raise UnsupportedAuthorityValueError(
                    "authority map keys must be strings"
                )
        frozen = {
            key: freeze_authority_value(nested_value)
            for key, nested_value in value.items()
        }
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_authority_value(item) for item in value)
    raise UnsupportedAuthorityValueError(
        f"unsupported authority value type: {type(value).__name__}"
    )


def freeze_authority_mapping(
    value: Mapping[str, object],
) -> Mapping[str, AuthorityValue]:
    for key in value:
        if not isinstance(key, str):
            raise UnsupportedAuthorityValueError("authority map keys must be strings")
    frozen = {
        key: freeze_authority_value(nested_value)
        for key, nested_value in value.items()
    }
    return MappingProxyType(frozen)


def _freeze_sequence(value: Iterable[T]) -> tuple[T, ...]:
    return tuple(value)


@dataclass(frozen=True, slots=True)
class WorkflowIdentity:
    record_kind: ClassVar[str] = "workflow_identity"
    schema_version: ClassVar[int] = 1

    workflow_id: WorkflowId
    workflow_version: WorkflowVersion
    workflow_name: str


@dataclass(frozen=True, slots=True)
class GraphDeclaration:
    record_kind: ClassVar[str] = "graph_declaration"
    schema_version: ClassVar[int] = 1

    id: GraphId
    node_ids: tuple[str, ...]
    presentation: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_ids", _freeze_sequence(self.node_ids))
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class PartitionDeclaration:
    record_kind: ClassVar[str] = "partition_declaration"
    schema_version: ClassVar[int] = 1

    id: PartitionId
    partition_kind: str
    presentation: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class QueueFamilyDeclaration:
    record_kind: ClassVar[str] = "queue_family_declaration"
    schema_version: ClassVar[int] = 1

    id: QueueFamilyId
    external_enqueue: bool
    presentation: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class ExternalEnqueueRouteDeclaration:
    record_kind: ClassVar[str] = "external_enqueue_route_declaration"
    schema_version: ClassVar[int] = 1

    id: str
    queue_family_id: QueueFamilyId
    graph_node_id: str
    stage_kind_id: StageKindId
    runner_binding_id: RunnerBindingId
    payload_schema_id: ArtifactSchemaId | None = None


@dataclass(frozen=True, slots=True)
class GeneratedWorkRouteDeclaration:
    record_kind: ClassVar[str] = "generated_work_route_declaration"
    schema_version: ClassVar[int] = 1

    id: str
    queue_family_id: QueueFamilyId
    graph_node_id: str
    stage_kind_id: StageKindId
    runner_binding_id: RunnerBindingId
    payload_schema_id: ArtifactSchemaId | None = None


@dataclass(frozen=True, slots=True)
class ArtifactSchemaDeclaration:
    record_kind: ClassVar[str] = "artifact_schema_declaration"
    schema_version: ClassVar[int] = 1

    id: ArtifactSchemaId
    schema: Mapping[str, AuthorityValue]
    presentation: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "schema",
            freeze_authority_mapping(cast(Mapping[str, object], self.schema)),
        )
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class AssetDeclaration:
    record_kind: ClassVar[str] = "asset_declaration"
    schema_version: ClassVar[int] = 1

    id: AssetId
    asset_kind: str
    body: str
    presentation: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class StageKindDeclaration:
    record_kind: ClassVar[str] = "stage_kind_declaration"
    schema_version: ClassVar[int] = 1

    id: StageKindId
    partition_id: PartitionId | None
    runner_binding_id: RunnerBindingId
    input_queue_family_ids: tuple[QueueFamilyId, ...]
    output_queue_family_ids: tuple[QueueFamilyId, ...]
    artifact_schema_ids: tuple[ArtifactSchemaId, ...]
    asset_ids: tuple[AssetId, ...]
    declared_outcome_ids: tuple[OutcomeId, ...]
    presentation: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_queue_family_ids",
            _freeze_sequence(self.input_queue_family_ids),
        )
        object.__setattr__(
            self,
            "output_queue_family_ids",
            _freeze_sequence(self.output_queue_family_ids),
        )
        object.__setattr__(
            self,
            "artifact_schema_ids",
            _freeze_sequence(self.artifact_schema_ids),
        )
        object.__setattr__(self, "asset_ids", _freeze_sequence(self.asset_ids))
        object.__setattr__(
            self,
            "declared_outcome_ids",
            _freeze_sequence(self.declared_outcome_ids),
        )
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class TerminalOutcomeDeclaration:
    record_kind: ClassVar[str] = "terminal_outcome_declaration"
    schema_version: ClassVar[int] = 1

    id: OutcomeId
    stage_kind_id: StageKindId
    marker: str
    presentation: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class TerminalActionDeclaration:
    record_kind: ClassVar[str] = "terminal_action_declaration"
    schema_version: ClassVar[int] = 1

    id: ActionId
    stage_kind_id: StageKindId
    outcome_id: OutcomeId
    action_kind: str
    target_stage_kind_id: StageKindId | None
    target_graph_node_id: str | None
    emitted_queue_family_id: QueueFamilyId | None
    artifact_schema_id: ArtifactSchemaId | None
    runner_binding_id: RunnerBindingId | None
    asset_ids: tuple[AssetId, ...]
    payload_projection: AuthorityValue | None
    presentation: Mapping[str, AuthorityValue]
    dynamic_target_selector: AuthorityValue | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_ids", _freeze_sequence(self.asset_ids))
        object.__setattr__(
            self,
            "payload_projection",
            freeze_authority_value(self.payload_projection),
        )
        object.__setattr__(
            self,
            "dynamic_target_selector",
            freeze_authority_value(self.dynamic_target_selector),
        )
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class EffectDeclaration:
    record_kind: ClassVar[str] = "effect_declaration"
    schema_version: ClassVar[int] = 1

    effect_declaration_id: EffectDeclarationId
    terminal_action_id: ActionId
    artifact_schema_id: ArtifactSchemaId
    provider_ref: str
    capability_policy_ref: str
    target_ref_kind: str
    target_ref_schema: str
    allowed_reconciliation_statuses: tuple[str, ...]
    real_side_effects_allowed: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_reconciliation_statuses",
            _freeze_sequence(self.allowed_reconciliation_statuses),
        )


@dataclass(frozen=True, slots=True)
class CapabilityDeclaration:
    record_kind: ClassVar[str] = "capability_declaration"
    schema_version: ClassVar[int] = 1

    id: CapabilityId
    capability_kind: str
    support_status: str
    grant_status: str
    approval_policy_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunnerComponentPin:
    record_kind: ClassVar[str] = "runner_component_pin"
    schema_version: ClassVar[int] = 2

    component_kind: str
    component_id: str
    component_version: str
    provider_distribution: str
    provider_version: str
    descriptor_media_type: str
    descriptor_sha256: str
    required_capability_ids: tuple[CapabilityId, ...]
    legal_terminal_result_ids: tuple[str, ...]
    max_work_item_payload_bytes: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "component_kind",
            "component_id",
            "component_version",
            "provider_distribution",
            "provider_version",
            "descriptor_media_type",
        ):
            _require_nonblank_text(getattr(self, field_name), field_name)
        _require_raw_sha256_digest(self.descriptor_sha256, "descriptor_sha256")
        object.__setattr__(
            self,
            "required_capability_ids",
            _freeze_sequence(self.required_capability_ids),
        )
        if isinstance(self.legal_terminal_result_ids, (str, bytes)):
            raise TypeError("legal_terminal_result_ids must be a sequence of strings")
        object.__setattr__(
            self,
            "legal_terminal_result_ids",
            _freeze_sequence(self.legal_terminal_result_ids),
        )
        _require_canonical_capability_ids(self.required_capability_ids)
        _require_canonical_text_values(
            self.legal_terminal_result_ids,
            "legal_terminal_result_ids",
        )
        if self.max_work_item_payload_bytes is not None and (
            type(self.max_work_item_payload_bytes) is not int
            or self.max_work_item_payload_bytes <= 0
        ):
            raise ValueError("max_work_item_payload_bytes must be positive or null")


@dataclass(frozen=True, slots=True)
class RunnerTerminalResultMapping:
    record_kind: ClassVar[str] = "runner_terminal_result_mapping"
    schema_version: ClassVar[int] = 1

    stage_kind_id: StageKindId
    runner_result_id: str
    outcome_id: OutcomeId

    def __post_init__(self) -> None:
        if not isinstance(self.stage_kind_id, StageKindId):
            raise TypeError("stage_kind_id must be a StageKindId")
        _require_nonblank_text(self.runner_result_id, "runner_result_id")
        if not isinstance(self.outcome_id, OutcomeId):
            raise TypeError("outcome_id must be an OutcomeId")


@dataclass(frozen=True, slots=True)
class RunnerBindingDeclaration:
    record_kind: ClassVar[str] = "runner_binding_declaration"
    schema_version: ClassVar[int] = 3

    id: RunnerBindingId
    adapter_kind: str
    stage_kind_ids: tuple[StageKindId, ...]
    invocation_timeout_seconds: int
    presentation: Mapping[str, AuthorityValue]
    required_capability_ids: tuple[CapabilityId, ...] = ()
    component_pin: RunnerComponentPin | None = None
    terminal_result_mappings: tuple[RunnerTerminalResultMapping, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_kind, str):
            raise TypeError("adapter_kind must be a string")
        if not self.adapter_kind.strip():
            raise ValueError("adapter_kind must be nonblank")
        if type(self.invocation_timeout_seconds) is not int:
            raise TypeError("invocation_timeout_seconds must be an integer")
        if self.invocation_timeout_seconds <= 0:
            raise ValueError("invocation_timeout_seconds must be positive")
        object.__setattr__(
            self,
            "stage_kind_ids",
            _freeze_sequence(self.stage_kind_ids),
        )
        object.__setattr__(
            self,
            "required_capability_ids",
            _freeze_sequence(self.required_capability_ids),
        )
        object.__setattr__(
            self,
            "terminal_result_mappings",
            _freeze_sequence(self.terminal_result_mappings),
        )
        if self.component_pin is not None and not isinstance(
            self.component_pin,
            RunnerComponentPin,
        ):
            raise TypeError("component_pin must be a RunnerComponentPin or None")
        if any(
            not isinstance(mapping, RunnerTerminalResultMapping)
            for mapping in self.terminal_result_mappings
        ):
            raise TypeError(
                "terminal_result_mappings must contain RunnerTerminalResultMapping"
            )
        if any(
            mapping.stage_kind_id not in self.stage_kind_ids
            for mapping in self.terminal_result_mappings
        ):
            raise ValueError("terminal result mapping stage must belong to the binding")
        if self.component_pin is None and self.terminal_result_mappings:
            raise ValueError("terminal result mappings require a component pin")
        if self.component_pin is not None:
            legal_result_ids = set(self.component_pin.legal_terminal_result_ids)
            if any(
                mapping.runner_result_id not in legal_result_ids
                for mapping in self.terminal_result_mappings
            ):
                raise ValueError("terminal result mapping uses an unknown result")
            if not set(self.component_pin.required_capability_ids).issubset(
                self.required_capability_ids
            ):
                raise ValueError(
                    "component capabilities must be required by the runner binding"
                )
        _require_canonical_terminal_result_mappings(self.terminal_result_mappings)
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class RecoveryPolicyDeclaration:
    record_kind: ClassVar[str] = "recovery_policy_declaration"
    schema_version: ClassVar[int] = 1

    id: RecoveryPolicyId
    source_recovery_action_ids: tuple[ActionId, ...]
    return_action_ids: tuple[ActionId, ...]
    quarantine_action_ids: tuple[ActionId, ...]
    recovery_stage_kind_id: StageKindId
    recorded_source_selector: str
    attempt_scope: str
    immediate_recovery_limit: int
    cooldown_starts_at_attempt: int
    quarantine_threshold_attempt: int
    threshold_behavior: str
    return_allowed_phases: tuple[str, ...]
    reset_trigger_action_ids: tuple[ActionId, ...]
    default_cooldown_seconds: int
    cooldown_wait_state_id: WaitStateId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_recovery_action_ids",
            _freeze_sequence(self.source_recovery_action_ids),
        )
        object.__setattr__(
            self,
            "return_action_ids",
            _freeze_sequence(self.return_action_ids),
        )
        object.__setattr__(
            self,
            "quarantine_action_ids",
            _freeze_sequence(self.quarantine_action_ids),
        )
        object.__setattr__(
            self,
            "return_allowed_phases",
            _freeze_sequence(self.return_allowed_phases),
        )
        object.__setattr__(
            self,
            "reset_trigger_action_ids",
            _freeze_sequence(self.reset_trigger_action_ids),
        )


@dataclass(frozen=True, slots=True)
class WaitStateDeclaration:
    record_kind: ClassVar[str] = "wait_state_declaration"
    schema_version: ClassVar[int] = 1

    id: WaitStateId
    wait_kind: str
    policy_id: RecoveryPolicyId
    starts_at_attempt: int
    duration_seconds: int


@dataclass(frozen=True, slots=True)
class CounterDeclaration:
    record_kind: ClassVar[str] = "counter_declaration"
    schema_version: ClassVar[int] = 1

    id: CounterId
    counter_kind: str
    scope: str
    stage_kind_id: StageKindId
    increment_action_id: ActionId
    threshold_action_id: ActionId
    threshold_count: int


@dataclass(frozen=True, slots=True)
class CompletionBehaviorDeclaration:
    record_kind: ClassVar[str] = "completion_behavior_declaration"
    schema_version: ClassVar[int] = 2

    id: CompletionBehaviorId
    trigger: str
    readiness_rule: str
    request_kind: str
    target_selector: str
    target_stage_kind_id: StageKindId
    target_graph_node_id: str
    runner_binding_id: RunnerBindingId
    request_queue_family_id: QueueFamilyId
    pass_action_id: ActionId
    gap_action_id: ActionId
    blocked_action_id: ActionId
    verdict_artifact_schema_id: ArtifactSchemaId
    evidence_artifact_schema_ids: tuple[ArtifactSchemaId, ...]
    evidence_item_limit: int
    request_payload_byte_limit: int
    remediation_policy_id: RemediationPolicyId
    accepted_root_source_kinds: tuple[str, ...]
    root_source_resolution: str
    evidence_window_policy: str
    rubric_policy: str
    blocked_work_policy: str
    skip_if_closed: bool
    presentation: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "accepted_root_source_kinds",
            _freeze_sequence(self.accepted_root_source_kinds),
        )
        object.__setattr__(
            self,
            "evidence_artifact_schema_ids",
            _freeze_sequence(self.evidence_artifact_schema_ids),
        )
        if any(
            not isinstance(value, ArtifactSchemaId)
            for value in self.evidence_artifact_schema_ids
        ):
            raise TypeError(
                "evidence_artifact_schema_ids must contain ArtifactSchemaId"
            )
        if not self.evidence_artifact_schema_ids:
            raise ValueError("evidence_artifact_schema_ids must be non-empty")
        if len(set(self.evidence_artifact_schema_ids)) != len(
            self.evidence_artifact_schema_ids
        ):
            raise ValueError("evidence_artifact_schema_ids must be unique")
        if type(self.evidence_item_limit) is not int or not (
            1 <= self.evidence_item_limit <= 256
        ):
            raise ValueError("evidence_item_limit must be between 1 and 256")
        if type(self.request_payload_byte_limit) is not int or (
            self.request_payload_byte_limit <= 0
        ):
            raise ValueError("request_payload_byte_limit must be positive")
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class RemediationPolicyDeclaration:
    record_kind: ClassVar[str] = "remediation_policy_declaration"
    schema_version: ClassVar[int] = 1

    id: RemediationPolicyId
    source_action_id: ActionId
    target_queue_family_id: QueueFamilyId
    target_stage_kind_id: StageKindId
    target_graph_node_id: str
    target_runner_binding_id: RunnerBindingId
    payload_schema_id: ArtifactSchemaId
    guidance_source: str
    dedupe_key: str
    duplicate_policy: str
    suppression_policy: str
    root_source_kind: str
    presentation: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "presentation",
            freeze_authority_mapping(cast(Mapping[str, object], self.presentation)),
        )


@dataclass(frozen=True, slots=True)
class FanoutDeclaration:
    record_kind: ClassVar[str] = "fanout_declaration"
    schema_version: ClassVar[int] = 2

    id: FanoutId
    source_action_id: ActionId
    source_artifact_schema_id: ArtifactSchemaId
    item_source_path: tuple[str, ...]
    item_id_key: str
    target_route_id: str
    source_state_policy: str
    target_queue_family_id: QueueFamilyId
    target_stage_kind_id: StageKindId
    target_graph_node_id: str
    target_runner_binding_id: RunnerBindingId
    target_payload_schema_id: ArtifactSchemaId
    target_payload_mapping: Mapping[str, AuthorityValue]
    duplicate_policy: str
    root_lineage_policy: str
    dependency_policy: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "item_source_path",
            _freeze_sequence(self.item_source_path),
        )
        object.__setattr__(
            self,
            "target_payload_mapping",
            freeze_authority_mapping(
                cast(Mapping[str, object], self.target_payload_mapping)
            ),
        )


@dataclass(frozen=True, slots=True)
class JoinDeclaration:
    record_kind: ClassVar[str] = "join_declaration"
    schema_version: ClassVar[int] = 1

    id: str
    target_stage_kind_id: StageKindId
    correlation_key: str
    required_artifact_schema_ids: tuple[ArtifactSchemaId, ...]
    missing_policy: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_artifact_schema_ids",
            _freeze_sequence(self.required_artifact_schema_ids),
        )


@dataclass(frozen=True, slots=True)
class ConcurrencyPolicyDeclaration:
    record_kind: ClassVar[str] = "concurrency_policy_declaration"
    schema_version: ClassVar[int] = 1

    id: str
    partition_id: PartitionId
    max_active_runs: int
    coexist_partition_ids: tuple[PartitionId, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "coexist_partition_ids",
            _freeze_sequence(self.coexist_partition_ids),
        )


@dataclass(frozen=True, slots=True)
class InterventionOptionDeclaration:
    record_kind: ClassVar[str] = "intervention_option_declaration"
    schema_version: ClassVar[int] = 1

    id: InterventionOptionId
    policy_id: RecoveryPolicyId
    option_kind: str
    legal_source_state: str
    target_selector: str
    resume_target_selector: str | None
    close_behavior: str | None
    payload_schema_id: ArtifactSchemaId | None
    target_queue_family_id: QueueFamilyId | None
    target_stage_kind_id: StageKindId | None
    target_graph_node_id: str | None
    target_runner_binding_id: RunnerBindingId | None
    supersede_behavior: str
    attempt_effect: str
    actor_kind: str
    audit_metadata_requirements: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "audit_metadata_requirements",
            _freeze_sequence(self.audit_metadata_requirements),
        )


@dataclass(frozen=True, slots=True)
class OperatorWaitDeclaration:
    record_kind: ClassVar[str] = "operator_wait_declaration"
    schema_version: ClassVar[int] = 2

    id: OperatorWaitId
    source_action_ids: tuple[ActionId, ...]
    wait_scope: str
    source_work_item_behavior: str
    project_source_artifact: bool
    unrelated_lineages_continue: bool
    allowed_resolution_kinds: tuple[str, ...]
    payload_schema_id: ArtifactSchemaId | None
    target_queue_family_id: QueueFamilyId | None
    target_stage_kind_id: StageKindId | None
    target_graph_node_id: str | None
    target_runner_binding_id: RunnerBindingId | None
    actor_kind: str
    audit_metadata_requirements: tuple[str, ...]
    correlation_key: str
    idempotency: str
    timeout_policy: str
    expiry_policy: str
    cancellation_policy: str
    status_effect: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_action_ids",
            _freeze_sequence(self.source_action_ids),
        )
        object.__setattr__(
            self,
            "allowed_resolution_kinds",
            _freeze_sequence(self.allowed_resolution_kinds),
        )
        object.__setattr__(
            self,
            "audit_metadata_requirements",
            _freeze_sequence(self.audit_metadata_requirements),
        )


@dataclass(frozen=True, slots=True)
class SelectedWorkflowPackageAssetPin:
    record_kind: ClassVar[str] = "selected_workflow_package_asset_pin"
    schema_version: ClassVar[int] = 1

    asset_id: str
    content_digest: str

    def __post_init__(self) -> None:
        _require_non_empty_text(self.asset_id, "asset_id")
        _require_sha256_digest(self.content_digest, "content_digest")


@dataclass(frozen=True, slots=True)
class SelectedWorkflowPackageDependencyPin:
    record_kind: ClassVar[str] = "selected_workflow_package_dependency_pin"
    schema_version: ClassVar[int] = 1

    package_id: str
    package_version: str
    package_format_version: str

    def __post_init__(self) -> None:
        _require_non_empty_text(self.package_id, "package_id")
        _require_non_empty_text(self.package_version, "package_version")
        _require_non_empty_text(
            self.package_format_version,
            "package_format_version",
        )


@dataclass(frozen=True, slots=True)
class SelectedWorkflowPackagePin:
    record_kind: ClassVar[str] = "selected_workflow_package_pin"
    schema_version: ClassVar[int] = 1

    package_id: str
    package_version: str
    package_format_version: str
    workflow_id: str
    workflow_version: str
    entrypoint: str
    selected_asset_pins: tuple[SelectedWorkflowPackageAssetPin, ...]
    selected_dependency_pins: tuple[SelectedWorkflowPackageDependencyPin, ...]

    def __post_init__(self) -> None:
        _require_non_empty_text(self.package_id, "package_id")
        _require_non_empty_text(self.package_version, "package_version")
        _require_non_empty_text(
            self.package_format_version,
            "package_format_version",
        )
        _require_non_empty_text(self.workflow_id, "workflow_id")
        _require_non_empty_text(self.workflow_version, "workflow_version")
        _require_non_empty_text(self.entrypoint, "entrypoint")
        object.__setattr__(
            self,
            "selected_asset_pins",
            _freeze_sequence(self.selected_asset_pins),
        )
        object.__setattr__(
            self,
            "selected_dependency_pins",
            _freeze_sequence(self.selected_dependency_pins),
        )
        _require_unique_asset_pins(self.selected_asset_pins)
        _require_unique_dependency_pins(self.selected_dependency_pins)


@dataclass(frozen=True, slots=True)
class SelectedCompiledPlan:
    record_kind: ClassVar[str] = "selected_compiled_plan"
    schema_version: ClassVar[int] = 16

    workflow: WorkflowIdentity
    compatibility_profile: None
    required_extensions: tuple[str, ...]
    graphs: tuple[GraphDeclaration, ...]
    partitions: tuple[PartitionDeclaration, ...]
    queue_families: tuple[QueueFamilyDeclaration, ...]
    external_enqueue_routes: tuple[ExternalEnqueueRouteDeclaration, ...]
    generated_work_routes: tuple[GeneratedWorkRouteDeclaration, ...]
    artifact_schemas: tuple[ArtifactSchemaDeclaration, ...]
    assets: tuple[AssetDeclaration, ...]
    stage_kinds: tuple[StageKindDeclaration, ...]
    terminal_outcomes: tuple[TerminalOutcomeDeclaration, ...]
    terminal_actions: tuple[TerminalActionDeclaration, ...]
    recovery_policies: tuple[RecoveryPolicyDeclaration, ...]
    runner_bindings: tuple[RunnerBindingDeclaration, ...]
    wait_states: tuple[WaitStateDeclaration, ...] = ()
    counters: tuple[CounterDeclaration, ...] = ()
    completion_behaviors: tuple[CompletionBehaviorDeclaration, ...] = ()
    remediation_policies: tuple[RemediationPolicyDeclaration, ...] = ()
    fanout_declarations: tuple[FanoutDeclaration, ...] = ()
    join_declarations: tuple[JoinDeclaration, ...] = ()
    concurrency_policies: tuple[ConcurrencyPolicyDeclaration, ...] = ()
    lineage_policy: str = "root_from_external_enqueue"
    intervention_options: tuple[InterventionOptionDeclaration, ...] = ()
    operator_waits: tuple[OperatorWaitDeclaration, ...] = ()
    capabilities: tuple[CapabilityDeclaration, ...] = ()
    effect_declarations: tuple[EffectDeclaration, ...] = ()
    workflow_package_pin: SelectedWorkflowPackagePin | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_extensions",
            _freeze_sequence(self.required_extensions),
        )
        object.__setattr__(self, "graphs", _freeze_sequence(self.graphs))
        object.__setattr__(self, "partitions", _freeze_sequence(self.partitions))
        object.__setattr__(
            self,
            "queue_families",
            _freeze_sequence(self.queue_families),
        )
        object.__setattr__(
            self,
            "external_enqueue_routes",
            _freeze_sequence(self.external_enqueue_routes),
        )
        object.__setattr__(
            self,
            "generated_work_routes",
            _freeze_sequence(self.generated_work_routes),
        )
        object.__setattr__(
            self,
            "artifact_schemas",
            _freeze_sequence(self.artifact_schemas),
        )
        object.__setattr__(self, "assets", _freeze_sequence(self.assets))
        object.__setattr__(self, "stage_kinds", _freeze_sequence(self.stage_kinds))
        object.__setattr__(
            self,
            "terminal_outcomes",
            _freeze_sequence(self.terminal_outcomes),
        )
        object.__setattr__(
            self,
            "terminal_actions",
            _freeze_sequence(self.terminal_actions),
        )
        object.__setattr__(
            self,
            "effect_declarations",
            _freeze_sequence(self.effect_declarations),
        )
        object.__setattr__(
            self,
            "recovery_policies",
            _freeze_sequence(self.recovery_policies),
        )
        object.__setattr__(
            self,
            "wait_states",
            _freeze_sequence(self.wait_states),
        )
        object.__setattr__(
            self,
            "counters",
            _freeze_sequence(self.counters),
        )
        object.__setattr__(
            self,
            "completion_behaviors",
            _freeze_sequence(self.completion_behaviors),
        )
        object.__setattr__(
            self,
            "remediation_policies",
            _freeze_sequence(self.remediation_policies),
        )
        object.__setattr__(
            self,
            "fanout_declarations",
            _freeze_sequence(self.fanout_declarations),
        )
        object.__setattr__(
            self,
            "join_declarations",
            _freeze_sequence(self.join_declarations),
        )
        object.__setattr__(
            self,
            "concurrency_policies",
            _freeze_sequence(self.concurrency_policies),
        )
        object.__setattr__(
            self,
            "runner_bindings",
            _freeze_sequence(self.runner_bindings),
        )
        object.__setattr__(
            self,
            "intervention_options",
            _freeze_sequence(self.intervention_options),
        )
        object.__setattr__(
            self,
            "operator_waits",
            _freeze_sequence(self.operator_waits),
        )
        object.__setattr__(
            self,
            "capabilities",
            _freeze_sequence(self.capabilities),
        )


def runner_component_authority_refusal(
    selected_plan: SelectedCompiledPlan,
) -> str | None:
    stage_by_id = {stage.id: stage for stage in selected_plan.stage_kinds}
    outcome_by_id = {
        outcome.id: outcome for outcome in selected_plan.terminal_outcomes
    }
    capability_counts: dict[CapabilityId, int] = {}
    for capability in selected_plan.capabilities:
        capability_counts[capability.id] = capability_counts.get(capability.id, 0) + 1
    for binding in selected_plan.runner_bindings:
        if any(
            capability_counts.get(capability_id, 0) != 1
            for capability_id in binding.required_capability_ids
        ):
            return f"runner_component_capability:{binding.id}"
        pin = binding.component_pin
        mappings = binding.terminal_result_mappings
        if pin is None:
            if mappings:
                return f"runner_component_mapping_without_pin:{binding.id}"
            continue
        if _runner_component_pin_refusal(pin) is not None:
            return f"runner_component_pin_noncanonical:{binding.id}"
        if not isinstance(mappings, tuple):
            return f"runner_component_mapping_noncanonical:{binding.id}"
        mapping_keys: set[tuple[StageKindId, str]] = set()
        mapping_outcomes: set[tuple[StageKindId, OutcomeId]] = set()
        mapping_sort_keys: list[tuple[bytes, bytes, bytes]] = []
        for mapping in mappings:
            if not isinstance(mapping, RunnerTerminalResultMapping):
                return f"runner_component_mapping_noncanonical:{binding.id}"
            key = (mapping.stage_kind_id, mapping.runner_result_id)
            if key in mapping_keys:
                return f"runner_component_mapping_duplicate:{binding.id}"
            mapping_keys.add(key)
            outcome_key = (mapping.stage_kind_id, mapping.outcome_id)
            if outcome_key in mapping_outcomes:
                return f"runner_component_mapping_outcome_duplicate:{binding.id}"
            mapping_outcomes.add(outcome_key)
            mapping_sort_keys.append(_terminal_result_mapping_sort_key(mapping))
            if mapping.runner_result_id not in pin.legal_terminal_result_ids:
                return f"runner_component_mapping_result:{binding.id}"
            if mapping.stage_kind_id not in binding.stage_kind_ids:
                return f"runner_component_mapping_stage:{binding.id}"
            stage = stage_by_id.get(mapping.stage_kind_id)
            if stage is None or stage.runner_binding_id != binding.id:
                return f"runner_component_mapping_stage:{binding.id}"
            outcome = outcome_by_id.get(mapping.outcome_id)
            if outcome is None:
                return f"runner_component_mapping_outcome:{binding.id}"
            if outcome.stage_kind_id != mapping.stage_kind_id:
                return f"runner_component_mapping_outcome_stage:{binding.id}"
            if mapping.outcome_id not in stage.declared_outcome_ids:
                return f"runner_component_mapping_outcome_declared:{binding.id}"
        if mapping_sort_keys != sorted(mapping_sort_keys):
            return f"runner_component_mapping_noncanonical:{binding.id}"
        binding_capability_ids = set(binding.required_capability_ids)
        if not set(pin.required_capability_ids).issubset(binding_capability_ids):
            return f"runner_component_capability:{binding.id}"
    return None


def _require_non_empty_text(value: object, field_name: str) -> None:
    if isinstance(value, str) and value:
        return
    raise ValueError(f"{field_name} must be non-empty text")


def _require_nonblank_text(value: object, field_name: str) -> None:
    if isinstance(value, str) and value.strip():
        return
    raise ValueError(f"{field_name} must be nonblank text")


def _require_raw_sha256_digest(value: object, field_name: str) -> None:
    if (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        return
    raise ValueError(f"{field_name} must be 64 lowercase hexadecimal characters")


def _bytewise_text_key(value: object) -> bytes:
    return str(value).encode("utf-8")


def _require_canonical_capability_ids(values: tuple[CapabilityId, ...]) -> None:
    if any(not isinstance(value, CapabilityId) for value in values):
        raise TypeError("required_capability_ids must contain CapabilityId values")
    rendered = tuple(str(value) for value in values)
    if any(not value.strip() for value in rendered):
        raise ValueError("required_capability_ids must contain nonblank values")
    if len(set(rendered)) != len(rendered):
        raise ValueError("required_capability_ids must contain unique values")
    if tuple(sorted(values, key=_bytewise_text_key)) != values:
        raise ValueError("required_capability_ids must be in canonical order")


def _require_canonical_text_values(values: tuple[str, ...], field_name: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{field_name} must contain nonblank strings")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must contain unique values")
    if tuple(sorted(values, key=_bytewise_text_key)) != values:
        raise ValueError(f"{field_name} must be in canonical order")


def _terminal_result_mapping_sort_key(
    mapping: RunnerTerminalResultMapping,
) -> tuple[bytes, bytes, bytes]:
    return (
        _bytewise_text_key(mapping.stage_kind_id),
        _bytewise_text_key(mapping.runner_result_id),
        _bytewise_text_key(mapping.outcome_id),
    )


def _require_canonical_terminal_result_mappings(
    mappings: tuple[RunnerTerminalResultMapping, ...],
) -> None:
    keys = tuple(
        (mapping.stage_kind_id, mapping.runner_result_id) for mapping in mappings
    )
    if len(set(keys)) != len(keys):
        raise ValueError("terminal result mapping keys must be unique")
    outcomes = tuple(
        (mapping.stage_kind_id, mapping.outcome_id) for mapping in mappings
    )
    if len(set(outcomes)) != len(outcomes):
        raise ValueError("terminal result mapping outcomes must be unique per stage")
    if tuple(sorted(mappings, key=_terminal_result_mapping_sort_key)) != mappings:
        raise ValueError("terminal result mappings must be in canonical order")


def _runner_component_pin_refusal(pin: object) -> str | None:
    if not isinstance(pin, RunnerComponentPin):
        return "record_type"
    for field_name in (
        "component_kind",
        "component_id",
        "component_version",
        "provider_distribution",
        "provider_version",
        "descriptor_media_type",
    ):
        value = getattr(pin, field_name)
        if not isinstance(value, str) or not value.strip():
            return field_name
    try:
        _require_raw_sha256_digest(pin.descriptor_sha256, "descriptor_sha256")
        if not isinstance(pin.required_capability_ids, tuple):
            return "required_capability_ids"
        if not isinstance(pin.legal_terminal_result_ids, tuple):
            return "legal_terminal_result_ids"
        if pin.max_work_item_payload_bytes is not None and (
            type(pin.max_work_item_payload_bytes) is not int
            or pin.max_work_item_payload_bytes <= 0
        ):
            return "max_work_item_payload_bytes"
        _require_canonical_capability_ids(pin.required_capability_ids)
        _require_canonical_text_values(
            pin.legal_terminal_result_ids,
            "legal_terminal_result_ids",
        )
    except (TypeError, ValueError):
        return "canonical_fields"
    return None


def _require_sha256_digest(value: object, field_name: str) -> None:
    if (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    ):
        return
    raise ValueError(f"{field_name} must be a sha256 digest")


def _require_unique_asset_pins(
    pins: tuple[SelectedWorkflowPackageAssetPin, ...],
) -> None:
    seen: set[str] = set()
    for pin in pins:
        if pin.asset_id in seen:
            raise ValueError("duplicate selected asset pin")
        seen.add(pin.asset_id)


def _require_unique_dependency_pins(
    pins: tuple[SelectedWorkflowPackageDependencyPin, ...],
) -> None:
    seen: set[tuple[str, str, str]] = set()
    for pin in pins:
        key = (pin.package_id, pin.package_version, pin.package_format_version)
        if key in seen:
            raise ValueError("duplicate selected dependency pin")
        seen.add(key)


@dataclass(frozen=True, slots=True)
class CompiledPlanEnvelope:
    selected_authority: SelectedCompiledPlan
    diagnostics: tuple[object, ...] = ()
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata or {})),
        )


def canonical_authority_bytes(value: object) -> bytes:
    canonical_value = _canonical_value(value)
    try:
        serialized = json.dumps(
            canonical_value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalAuthorityError("value is not canonical authority") from exc
    return serialized.encode("utf-8")


def authority_fingerprint(value: object) -> AuthorityFingerprint:
    if isinstance(value, bytes):
        raise CanonicalAuthorityError(
            "authority fingerprints require canonicalizable authority values"
        )
    authority_bytes = canonical_authority_bytes(value)
    digest = sha256(AUTHORITY_FINGERPRINT_DOMAIN_PREFIX + authority_bytes).hexdigest()
    return f"sha256:{digest}"


def verify_authority_fingerprint(
    selected_plan: SelectedCompiledPlan,
    authority_fingerprint_value: AuthorityFingerprint,
) -> bool:
    return authority_fingerprint(selected_plan) == authority_fingerprint_value


def _canonical_value(value: object) -> CanonicalValue:
    if isinstance(value, CompiledPlanEnvelope):
        return _canonical_value(value.selected_authority)
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if _is_string_backed_id(value):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        return _canonical_mapping(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical_record(value)
    raise CanonicalAuthorityError(
        f"unsupported canonical authority type: {type(value).__name__}"
    )


def _canonical_mapping(value: Mapping[Any, Any]) -> dict[str, CanonicalValue]:
    canonical: dict[str, CanonicalValue] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise CanonicalAuthorityError(
                "canonical authority map keys must be strings"
            )
        canonical[key] = _canonical_value(nested_value)
    return canonical


def _canonical_record(value: object) -> dict[str, CanonicalValue]:
    record_kind = getattr(value, "record_kind", None)
    schema_version = getattr(value, "schema_version", None)
    if not isinstance(record_kind, str) or type(schema_version) is not int:
        raise CanonicalAuthorityError(
            "canonical authority records need record kind and schema version"
        )

    record: dict[str, Any] = {
        "record_kind": record_kind,
        "schema_version": schema_version,
    }
    for field in fields(cast(Any, value)):
        if field.name == "presentation":
            continue
        record[field.name] = getattr(value, field.name)
    return _canonical_mapping(record)


def _is_string_backed_id(value: object) -> bool:
    return (
        value.__class__.__module__ == "millrace.contracts.ids"
        and isinstance(getattr(value, "value", None), str)
    )


__all__ = (
    "AUTHORITY_FINGERPRINT_DOMAIN_PREFIX",
    "ArtifactSchemaDeclaration",
    "AssetDeclaration",
    "AuthorityValue",
    "CapabilityDeclaration",
    "CanonicalAuthorityError",
    "CompiledPlanEnvelope",
    "CompletionBehaviorDeclaration",
    "CounterDeclaration",
    "EffectDeclaration",
    "ExternalEnqueueRouteDeclaration",
    "FanoutDeclaration",
    "GraphDeclaration",
    "InterventionOptionDeclaration",
    "JoinDeclaration",
    "OperatorWaitDeclaration",
    "PartitionDeclaration",
    "QueueFamilyDeclaration",
    "RecoveryPolicyDeclaration",
    "RemediationPolicyDeclaration",
    "RunnerBindingDeclaration",
    "SelectedCompiledPlan",
    "SelectedWorkflowPackageAssetPin",
    "SelectedWorkflowPackageDependencyPin",
    "SelectedWorkflowPackagePin",
    "StageKindDeclaration",
    "TerminalActionDeclaration",
    "TerminalOutcomeDeclaration",
    "UnsupportedAuthorityValueError",
    "WaitStateDeclaration",
    "WorkflowIdentity",
    "authority_fingerprint",
    "canonical_authority_bytes",
    "freeze_authority_mapping",
    "freeze_authority_value",
    "verify_authority_fingerprint",
)
