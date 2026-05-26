"""Typed contracts for data-driven workflow primitive definitions."""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from typing import Literal, cast

from pydantic import Field, ValidationInfo, field_validator, model_validator

from millrace_ai.contracts import Plane

from .common import (
    normalize_canonical_id,
    normalize_nonempty_text,
    normalize_status,
)
from .stage_kinds import ArchitectureContractModel

WorkflowPrimitiveId = str
WorkItemFamilyId = str
DocumentAdapterId = str
QueueClaimPolicyId = str
TerminalActionId = str
LifecycleMutationPlanId = str
RuntimeEffectHandlerId = str
RuntimeEffectRuleId = str
RequestContextProfileId = str
ArtifactContractId = str
RuntimeEffectMutationPhaseValue = Literal["pre_mutation", "partial_mutation", "unknown"]


class ArtifactFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"
    TEXT = "text"
    DIRECTORY = "directory"


class ArtifactFilenameAdapterDefinition(ArchitectureContractModel):
    filename: str
    format: ArtifactFormat
    parser_id: str
    renderer_id: str | None = None

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return _normalize_artifact_filename(value, field_label="filename")

    @field_validator("parser_id", "renderer_id")
    @classmethod
    def validate_adapter_id(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return normalize_canonical_id(value, field_label=info.field_name or "adapter id")


class ArtifactContractDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["artifact_contract"] = "artifact_contract"
    artifact_id: ArtifactContractId
    canonical_filename: str
    accepted_filenames: tuple[str, ...] = ()
    preferred_format: ArtifactFormat
    schema_id: str
    filename_adapters: tuple[ArtifactFilenameAdapterDefinition, ...] = Field(min_length=1)
    producer_stage_kind_ids: tuple[str, ...] = ()
    consumer_handler_ids: tuple[RuntimeEffectHandlerId, ...] = ()
    destination_family_id: WorkItemFamilyId | None = None

    @field_validator("artifact_id", "schema_id", "destination_family_id")
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return normalize_canonical_id(value, field_label=info.field_name or "artifact contract id")

    @field_validator("canonical_filename")
    @classmethod
    def validate_canonical_filename(cls, value: str) -> str:
        return _normalize_artifact_filename(value, field_label="canonical_filename")

    @field_validator("accepted_filenames", mode="before")
    @classmethod
    def normalize_accepted_filenames(cls, value: object) -> tuple[str, ...]:
        raw = _ensure_sequence(value, field_label="accepted_filenames", allow_empty=True)
        normalized = [
            _normalize_artifact_filename(str(item), field_label="accepted_filenames")
            for item in raw
        ]
        return _reject_duplicates(normalized, field_label="accepted_filenames")

    @field_validator("producer_stage_kind_ids", "consumer_handler_ids", mode="before")
    @classmethod
    def normalize_reference_ids(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "artifact contract reference ids",
            allow_empty=True,
        )

    @model_validator(mode="after")
    def validate_filename_adapters(self) -> "ArtifactContractDefinition":
        filenames = self.all_filenames
        if len(set(filenames)) != len(filenames):
            raise ValueError("duplicate artifact filename")
        adapter_names = [adapter.filename for adapter in self.filename_adapters]
        if len(set(adapter_names)) != len(adapter_names):
            raise ValueError("duplicate filename_adapters filename")
        declared = set(filenames)
        adapted = set(adapter_names)
        missing = sorted(declared - adapted)
        if missing:
            raise ValueError(
                "filename_adapters must define parser semantics for every artifact filename: "
                + ", ".join(missing)
            )
        extra = sorted(adapted - declared)
        if extra:
            raise ValueError(
                "filename_adapters may only reference declared artifact filenames: "
                + ", ".join(extra)
            )
        canonical_adapter = self.filename_adapters_by_name[self.canonical_filename]
        if canonical_adapter.format is not self.preferred_format:
            raise ValueError("canonical filename adapter format must match preferred_format")
        return self

    @property
    def all_filenames(self) -> tuple[str, ...]:
        return (self.canonical_filename, *self.accepted_filenames)

    @property
    def filename_adapters_by_name(self) -> dict[str, ArtifactFilenameAdapterDefinition]:
        return {adapter.filename: adapter for adapter in self.filename_adapters}


class WorkItemQueueDirs(ArchitectureContractModel):
    queue: str
    active: str
    done: str
    blocked: str
    canceled: str | None = None
    superseded: str | None = None

    @field_validator("queue", "active", "done", "blocked", "canceled", "superseded")
    @classmethod
    def validate_state_dir(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _normalize_runtime_relative_path(
            value,
            field_label=info.field_name or "queue directory",
        )


class WorkItemFamilyDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["work_item_family"] = "work_item_family"
    family_id: WorkItemFamilyId
    plane: Plane
    entry_key: str
    display_name: str
    document_kind: str
    runtime_relative_dir: str
    file_extension: str = ".json"
    schema_id: str
    document_adapter_id: DocumentAdapterId
    queue_dirs: WorkItemQueueDirs
    lifecycle_states: tuple[str, ...] = Field(min_length=1)
    claimable_state: str = "queued"
    active_state: str = "active"
    done_state: str = "done"
    blocked_state: str = "blocked"
    canceled_state: str | None = None
    closure_blocking_states: tuple[str, ...] = ()
    default_entry_key: str | None = None
    id_field: str | None = None
    created_at_field: str = "created_at"
    lineage_fields: tuple[str, ...] = ()
    dependency_field: str | None = None
    one_active_policy: Literal["plane", "lane", "family", "lineage", "work_item", "custom_partition"] = "plane"
    duplicate_policy: Literal["fail", "supersede", "idempotent"] = "fail"
    invalid_artifact_policy: Literal["reject", "block_source", "quarantine"] = "block_source"
    sort_policy: Literal["created_at_asc", "created_at_desc", "lexical_path"] = "created_at_asc"
    operator_capabilities: tuple[str, ...] = ()

    @field_validator("family_id", "entry_key", "document_kind", "schema_id", "document_adapter_id")
    @classmethod
    def validate_canonical_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return normalize_nonempty_text(value, field_label="display_name")

    @field_validator("runtime_relative_dir")
    @classmethod
    def validate_runtime_relative_dir(cls, value: str) -> str:
        return _normalize_runtime_relative_path(value, field_label="runtime_relative_dir")

    @field_validator("file_extension")
    @classmethod
    def validate_file_extension(cls, value: str) -> str:
        return _normalize_file_extension(value, field_label="file_extension")

    @field_validator(
        "lifecycle_states",
        "closure_blocking_states",
        "lineage_fields",
        "operator_capabilities",
        mode="before",
    )
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "id tuple",
            allow_empty=info.field_name != "lifecycle_states",
        )

    @field_validator(
        "claimable_state",
        "active_state",
        "done_state",
        "blocked_state",
        "canceled_state",
        "default_entry_key",
        "id_field",
        "created_at_field",
        "dependency_field",
    )
    @classmethod
    def validate_optional_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @model_validator(mode="after")
    def validate_lifecycle_membership(self) -> "WorkItemFamilyDefinition":
        states = set(self.lifecycle_states)
        semantic_states = {
            "claimable_state": self.claimable_state,
            "active_state": self.active_state,
            "done_state": self.done_state,
            "blocked_state": self.blocked_state,
            "canceled_state": self.canceled_state,
        }
        for field_name, state in semantic_states.items():
            if state is not None and state not in states:
                raise ValueError(f"{field_name} must be declared in lifecycle_states")
        unknown_blocking = set(self.closure_blocking_states) - states
        if unknown_blocking:
            raise ValueError("closure_blocking_states must be declared in lifecycle_states")
        if self.default_entry_key is not None and self.default_entry_key != self.entry_key:
            raise ValueError("default_entry_key must match entry_key for this family")
        return self


class WorkItemDocumentAdapterDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["work_item_document_adapter"] = "work_item_document_adapter"
    adapter_id: DocumentAdapterId
    schema_id: str
    supported_file_extensions: tuple[str, ...] = Field(min_length=1)
    family_ids: tuple[WorkItemFamilyId, ...] = Field(min_length=1)
    can_parse: bool
    can_render: bool
    can_summarize: bool
    supports_dependencies: bool
    supports_lineage: bool

    @field_validator("adapter_id", "schema_id")
    @classmethod
    def validate_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("family_ids", mode="before")
    @classmethod
    def normalize_family_ids(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="family_ids", allow_empty=False)

    @field_validator("supported_file_extensions", mode="before")
    @classmethod
    def normalize_extensions(cls, value: object) -> tuple[str, ...]:
        raw = _ensure_sequence(value, field_label="supported_file_extensions")
        normalized = [
            _normalize_file_extension(str(item), field_label="supported_file_extensions")
            for item in raw
        ]
        return _reject_duplicates(normalized, field_label="supported_file_extensions")

    @model_validator(mode="after")
    def validate_capabilities(self) -> "WorkItemDocumentAdapterDefinition":
        if not (self.can_parse or self.can_render or self.can_summarize):
            raise ValueError("document adapter must support at least one operation")
        return self


class WorkItemPartitionSelectorDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["work_item_partition_selector"] = "work_item_partition_selector"
    selector_id: str
    family_id: WorkItemFamilyId
    output_kind: Literal["lineage", "root_spec", "repo_path_set", "work_item", "custom"]
    supports_static_compile_check: bool

    @field_validator("selector_id", "family_id")
    @classmethod
    def validate_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)


class PlaneQueueClaimPolicyDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["plane_queue_claim_policy"] = "plane_queue_claim_policy"
    policy_id: QueueClaimPolicyId
    plane: Plane
    family_order: tuple[WorkItemFamilyId, ...] = ()
    closure_lineage_policy: Literal["defer_unrelated", "allow_unrelated", "block_all"] = "defer_unrelated"
    empty_behavior: Literal["idle", "check_completion"] = "idle"

    @field_validator("policy_id")
    @classmethod
    def validate_policy_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="policy_id")

    @field_validator("family_order", mode="before")
    @classmethod
    def normalize_family_order(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="family_order", allow_empty=True)


class WorkflowLaneDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["workflow_lane"] = "workflow_lane"
    lane_id: str
    plane: Plane
    allowed_family_ids: tuple[WorkItemFamilyId, ...] = Field(min_length=1)
    claim_policy_id: QueueClaimPolicyId
    max_active_runs: int = Field(default=1, ge=1)
    one_active_scope: Literal[
        "plane",
        "lane",
        "family",
        "lineage",
        "work_item",
        "custom_partition",
    ] = "plane"
    partition_selector_id: str | None = None
    mutation_lock_scope: Literal["workspace", "plane", "lane", "family", "lineage", "work_item"] = "plane"
    result_application_policy: Literal["single_writer_serialized"] = "single_writer_serialized"
    conflict_policy_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_family_alias(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        accepted_family_ids = payload.pop("accepted_family_ids", None)
        if accepted_family_ids is not None and "allowed_family_ids" not in payload:
            payload["allowed_family_ids"] = accepted_family_ids
        return payload

    @field_validator("lane_id", "claim_policy_id", "partition_selector_id", "conflict_policy_id")
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("allowed_family_ids", mode="before")
    @classmethod
    def normalize_allowed_family_ids(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="allowed_family_ids", allow_empty=False)

    @model_validator(mode="after")
    def validate_partitioning(self) -> "WorkflowLaneDefinition":
        if self.one_active_scope == "custom_partition" and self.partition_selector_id is None:
            raise ValueError("partition_selector_id is required for one_active_scope=custom_partition")
        if self.one_active_scope != "custom_partition" and self.partition_selector_id is not None:
            raise ValueError("partition_selector_id is only valid for one_active_scope=custom_partition")
        return self

    @property
    def accepted_family_ids(self) -> tuple[str, ...]:
        return self.allowed_family_ids


class LaneConflictPolicyDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["lane_conflict_policy"] = "lane_conflict_policy"
    policy_id: str
    lane_ids: tuple[str, ...] = Field(min_length=1)
    concurrent_with_lane_ids: tuple[str, ...] = Field(min_length=1)
    conflict_scopes: tuple[Literal["workspace", "plane", "lane", "family", "lineage", "work_item", "repo_path_set"], ...]
    lock_acquisition_order: tuple[str, ...]
    release_policy: Literal[
        "after_result_application",
        "after_lane_idle",
        "manual",
        "on_result_applied",
        "on_lane_drain",
    ] = "after_result_application"
    missing_lock_policy: Literal[
        "reject_compile",
        "pause_lane",
        "block_claim",
        "block_dispatch",
        "runtime_failure",
    ] = "reject_compile"

    @model_validator(mode="before")
    @classmethod
    def normalize_pair_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        first_lane_id = payload.pop("first_lane_id", None)
        second_lane_id = payload.pop("second_lane_id", None)
        if first_lane_id is not None and "lane_ids" not in payload:
            payload["lane_ids"] = (first_lane_id,)
        if second_lane_id is not None and "concurrent_with_lane_ids" not in payload:
            payload["concurrent_with_lane_ids"] = (second_lane_id,)
        return payload

    @field_validator("policy_id")
    @classmethod
    def validate_policy_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="policy_id")

    @field_validator("lane_ids", "concurrent_with_lane_ids", "lock_acquisition_order", mode="before")
    @classmethod
    def normalize_lane_ids(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "lane id tuple",
            allow_empty=info.field_name == "lock_acquisition_order",
        )

    @field_validator("conflict_scopes", mode="before")
    @classmethod
    def normalize_conflict_scopes(cls, value: object) -> tuple[str, ...]:
        raw = _ensure_sequence(value, field_label="conflict_scopes")
        return _reject_duplicates([str(item).strip() for item in raw], field_label="conflict_scopes")

    @model_validator(mode="after")
    def validate_lock_order(self) -> "LaneConflictPolicyDefinition":
        if self.conflict_scopes and not self.lock_acquisition_order:
            raise ValueError("lock_acquisition_order is required when conflict scopes are declared")
        return self

    @property
    def lane_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs: list[tuple[str, str]] = []
        for lane_id in self.lane_ids:
            for concurrent_lane_id in self.concurrent_with_lane_ids:
                if lane_id == concurrent_lane_id:
                    continue
                first_lane_id, second_lane_id = sorted((lane_id, concurrent_lane_id))
                pairs.append((first_lane_id, second_lane_id))
        return tuple(sorted(set(pairs)))

    @property
    def lane_pair(self) -> tuple[str, str]:
        pairs = self.lane_pairs
        if len(pairs) != 1:
            raise ValueError("lane_pair is only available for single-pair conflict policies")
        return pairs[0]


class TerminalActionDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["terminal_action"] = "terminal_action"
    terminal_action_id: TerminalActionId
    terminal_class: Literal["success", "no_op", "followup_needed", "blocked", "escalate_planning"]
    lifecycle_mutation_plan_id: LifecycleMutationPlanId | None = None
    effect_rule_ids: tuple[RuntimeEffectRuleId, ...] = ()
    status_marker: str | None = None
    operator_summary_template: str | None = None
    non_mutating: bool = False

    @field_validator("terminal_action_id", "lifecycle_mutation_plan_id")
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("effect_rule_ids", mode="before")
    @classmethod
    def normalize_effect_rule_ids(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="effect_rule_ids", allow_empty=True)

    @field_validator("status_marker")
    @classmethod
    def validate_status_marker(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_status(value, field_label="status_marker")

    @field_validator("operator_summary_template")
    @classmethod
    def validate_summary_template(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_nonempty_text(value, field_label="operator_summary_template")

    @model_validator(mode="after")
    def validate_lifecycle_plan(self) -> "TerminalActionDefinition":
        if not self.non_mutating and self.lifecycle_mutation_plan_id is None:
            raise ValueError("lifecycle_mutation_plan_id is required for mutating terminal actions")
        return self


class LifecycleMutationPlanDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["lifecycle_mutation_plan"] = "lifecycle_mutation_plan"
    plan_id: LifecycleMutationPlanId
    source_node_id: str
    outcome_id: str
    source_family_id: WorkItemFamilyId
    owner: Literal["terminal_action", "effect_rule", "recovery_policy", "runtime_failure_policy", "none"]
    source_from_state: str
    source_to_state: str | None = None
    ordering: Literal[
        "after_effect_success",
        "before_route",
        "after_route",
        "on_recovery_exhaustion",
        "none",
    ]
    lifecycle_action_id: str | None = None

    @field_validator(
        "plan_id",
        "source_node_id",
        "source_family_id",
        "source_from_state",
        "source_to_state",
        "lifecycle_action_id",
    )
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("outcome_id")
    @classmethod
    def validate_outcome_id(cls, value: str) -> str:
        return normalize_status(value, field_label="outcome_id")

    @model_validator(mode="after")
    def validate_mutation(self) -> "LifecycleMutationPlanDefinition":
        if self.owner == "none":
            if self.source_to_state is not None or self.lifecycle_action_id is not None or self.ordering != "none":
                raise ValueError("owner=none may not declare lifecycle mutation fields")
            return self
        if self.ordering == "none":
            raise ValueError("ordering=none is only valid when owner=none")
        if self.source_to_state is not None and self.lifecycle_action_id is None:
            raise ValueError("lifecycle_action_id is required when source_to_state is declared")
        if self.source_to_state is None and self.lifecycle_action_id is not None:
            raise ValueError("source_to_state is required when lifecycle_action_id is declared")
        return self


class RuntimeEffectHandlerDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_effect_handler"] = "runtime_effect_handler"
    handler_id: RuntimeEffectHandlerId
    source_planes: tuple[Plane, ...] = Field(min_length=1)
    allowed_source_families: tuple[WorkItemFamilyId, ...] = ()
    destination_kinds: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    optional_artifacts: tuple[str, ...] = ()
    returns_source_lifecycle_intent: bool
    requires_lifecycle_mutation_plan: bool
    creates_work_items: bool
    creates_incidents: bool = False
    creates_closure_targets: bool = False
    declared_capabilities: tuple[str, ...] = ()
    failure_classes: tuple[str, ...] = Field(min_length=1)

    @field_validator("handler_id")
    @classmethod
    def validate_handler_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="handler_id")

    @field_validator(
        "allowed_source_families",
        "destination_kinds",
        "required_artifacts",
        "optional_artifacts",
        "declared_capabilities",
        "failure_classes",
        mode="before",
    )
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        field_name = info.field_name or "id tuple"
        return _normalize_unique_id_tuple(value, field_label=field_name, allow_empty=field_name != "failure_classes")

    @model_validator(mode="after")
    def validate_handler_metadata(self) -> "RuntimeEffectHandlerDefinition":
        if self.returns_source_lifecycle_intent and not self.requires_lifecycle_mutation_plan:
            raise ValueError(
                "requires_lifecycle_mutation_plan must be true when returns_source_lifecycle_intent is true"
            )
        if self.creates_work_items and not self.destination_kinds:
            raise ValueError("destination_kinds is required when creates_work_items is true")
        return self


class RuntimeEffectRuleDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_effect_rule"] = "runtime_effect_rule"
    rule_id: RuntimeEffectRuleId
    effect_operation_id: str
    source_node_id: str
    on_outcomes: tuple[str, ...] = Field(min_length=1)
    handler_id: RuntimeEffectHandlerId
    required_run_artifacts: tuple[str, ...] = ()
    destination_family_id: WorkItemFamilyId | None = None
    creates_work_items: bool = False
    required_handler_capabilities: tuple[str, ...] = ()
    duplicate_policy: Literal["fail", "supersede", "idempotent"]
    partial_commit_policy: Literal["block_source", "pause_lane", "stop_daemon", "require_operator"]
    replay_policy: Literal["resume_idempotently", "fail_if_seen", "require_operator"]
    lineage_policy: Literal["preserve_root", "require_root", "derive_from_source"]
    applies_before_route: bool
    lifecycle_mutation_plan_id: LifecycleMutationPlanId | None = None

    @field_validator(
        "rule_id",
        "effect_operation_id",
        "source_node_id",
        "handler_id",
        "destination_family_id",
        "lifecycle_mutation_plan_id",
    )
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("on_outcomes", mode="before")
    @classmethod
    def normalize_outcomes(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_status_tuple(value, field_label="on_outcomes", allow_empty=False)

    @field_validator("required_run_artifacts", mode="before")
    @classmethod
    def normalize_required_run_artifacts(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="required_run_artifacts", allow_empty=True)

    @field_validator("required_handler_capabilities", mode="before")
    @classmethod
    def normalize_required_handler_capabilities(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label="required_handler_capabilities",
            allow_empty=True,
        )

    @model_validator(mode="after")
    def validate_destination(self) -> "RuntimeEffectRuleDefinition":
        if self.creates_work_items and self.destination_family_id is None:
            raise ValueError("destination_family_id is required when creates_work_items is true")
        return self


class OutcomeArtifactDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["outcome_artifact"] = "outcome_artifact"
    outcome_id: str
    required_artifacts: tuple[str, ...] = ()
    optional_artifacts: tuple[str, ...] = ()

    @field_validator("outcome_id")
    @classmethod
    def validate_outcome_id(cls, value: str) -> str:
        return normalize_status(value, field_label="outcome_id")

    @field_validator("required_artifacts", "optional_artifacts", mode="before")
    @classmethod
    def normalize_artifacts(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label=info.field_name or "artifact ids", allow_empty=True)


class RequestContextProfileDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["request_context_profile"] = "request_context_profile"
    profile_id: RequestContextProfileId
    request_kind: str
    required_providers: tuple[str, ...] = Field(min_length=1)
    optional_providers: tuple[str, ...] = ()
    output_path_preferences: dict[str, str] = Field(default_factory=dict)
    visibility_policy: Literal["active_item_only", "lineage_summary", "lineage_full", "closure_target"]

    @field_validator("profile_id", "request_kind")
    @classmethod
    def validate_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("required_providers", "optional_providers", mode="before")
    @classmethod
    def normalize_providers(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "provider ids",
            allow_empty=info.field_name != "required_providers",
        )

    @field_validator("output_path_preferences", mode="before")
    @classmethod
    def normalize_output_path_preferences(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("output_path_preferences must be a mapping")
        normalized: dict[str, str] = {}
        for raw_key, raw_path in value.items():
            key = normalize_canonical_id(str(raw_key), field_label="output_path_preferences key")
            if key in normalized:
                raise ValueError("output_path_preferences may not contain duplicate normalized keys")
            normalized[key] = _normalize_runtime_relative_path(
                str(raw_path),
                field_label="output_path_preferences",
            )
        return normalized


class RequestContextRenderPlan(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["request_context_render_plan"] = "request_context_render_plan"
    render_plan_id: str
    profile_id: RequestContextProfileId
    bundle_schema_version: str
    section_order: tuple[str, ...] = Field(min_length=1)
    artifact_ref_policy: Literal["path_only", "inline_if_small", "summary_only"]
    redaction_policy_id: str
    max_inline_bytes_by_role: dict[str, int] = Field(default_factory=dict)
    missing_optional_provider_policy: Literal["omit", "mention_absent"]

    @field_validator("render_plan_id", "profile_id", "redaction_policy_id")
    @classmethod
    def validate_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("bundle_schema_version")
    @classmethod
    def validate_bundle_schema_version(cls, value: str) -> str:
        return normalize_nonempty_text(value, field_label="bundle_schema_version")

    @field_validator("section_order", mode="before")
    @classmethod
    def normalize_section_order(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="section_order", allow_empty=False)

    @field_validator("max_inline_bytes_by_role", mode="before")
    @classmethod
    def normalize_max_inline_bytes_by_role(cls, value: object) -> dict[str, int]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("max_inline_bytes_by_role must be a mapping")
        normalized: dict[str, int] = {}
        for raw_role, raw_limit in value.items():
            role = normalize_canonical_id(str(raw_role), field_label="max_inline_bytes_by_role key")
            limit = int(raw_limit)
            if limit < 0:
                raise ValueError("max_inline_bytes_by_role values must be non-negative")
            normalized[role] = limit
        return normalized


class WorkflowCompletionBehaviorDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["workflow_completion_behavior"] = "workflow_completion_behavior"
    behavior_id: str
    plane: Plane
    trigger: Literal["backlog_drained"] = "backlog_drained"
    target_scope: Literal["workspace", "plane", "lane", "lineage", "family", "custom"]
    readiness_handler_ids: tuple[str, ...] = Field(min_length=1)
    target_entry_key: str
    target_node_id: str
    request_context_profile_id: RequestContextProfileId
    target_selector: str
    backpressure_policy: Literal["block_same_scope", "block_same_lineage", "block_all", "allow_unrelated"]
    skip_if_already_closed: bool = True
    terminal_action_by_outcome: dict[str, TerminalActionId] = Field(min_length=1)

    @field_validator(
        "behavior_id",
        "target_entry_key",
        "target_node_id",
        "request_context_profile_id",
        "target_selector",
    )
    @classmethod
    def validate_ids(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("readiness_handler_ids", mode="before")
    @classmethod
    def normalize_readiness_handlers(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="readiness_handler_ids", allow_empty=False)

    @field_validator("terminal_action_by_outcome", mode="before")
    @classmethod
    def normalize_terminal_action_by_outcome(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict) or not value:
            raise ValueError("terminal_action_by_outcome must not be empty")
        normalized: dict[str, str] = {}
        for raw_outcome, raw_action in value.items():
            outcome = normalize_status(str(raw_outcome), field_label="terminal_action_by_outcome outcome")
            if outcome in normalized:
                raise ValueError("terminal_action_by_outcome may not contain duplicate normalized outcomes")
            normalized[outcome] = normalize_canonical_id(
                str(raw_action),
                field_label="terminal_action_by_outcome action",
            )
        return normalized


class WorkflowRecoveryPolicyDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["workflow_recovery_policy"] = "workflow_recovery_policy"
    policy_id: str
    source_node_ids: tuple[str, ...] = Field(min_length=1)
    on_outcomes: tuple[str, ...] = Field(min_length=1)
    counter_name: str
    threshold: int = Field(ge=1)
    retry_target_node_id: str | None = None
    exhausted_target_node_id: str | None = None
    exhausted_terminal_state_id: str | None = None
    failure_class_template: str

    @field_validator(
        "policy_id",
        "counter_name",
        "retry_target_node_id",
        "exhausted_target_node_id",
        "exhausted_terminal_state_id",
        "failure_class_template",
    )
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("source_node_ids", mode="before")
    @classmethod
    def normalize_source_nodes(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="source_node_ids", allow_empty=False)

    @field_validator("on_outcomes", mode="before")
    @classmethod
    def normalize_on_outcomes(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_status_tuple(value, field_label="on_outcomes", allow_empty=False)

    @model_validator(mode="after")
    def validate_exhausted_target(self) -> "WorkflowRecoveryPolicyDefinition":
        target_count = int(self.exhausted_target_node_id is not None) + int(
            self.exhausted_terminal_state_id is not None
        )
        if target_count != 1:
            raise ValueError("recovery policy must declare exactly one exhausted target")
        return self


class RuntimeFailurePolicyDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["runtime_failure_policy"] = "runtime_failure_policy"
    policy_id: str
    applies_to_origins: tuple[str, ...] = Field(min_length=1)
    applies_to_planes: tuple[Plane, ...] = Field(min_length=1)
    applies_to_families: tuple[WorkItemFamilyId, ...] = ()
    applies_to_failure_classes: tuple[str, ...] = ()
    applies_to_mutation_phases: tuple[RuntimeEffectMutationPhaseValue, ...] = ()
    applies_to_operation_ids: tuple[str, ...] = ()
    applies_to_handler_ids: tuple[RuntimeEffectHandlerId, ...] = ()
    applies_to_source_node_ids: tuple[str, ...] = ()
    applies_to_source_terminal_state_ids: tuple[str, ...] = ()
    action: Literal[
        "retry_same_node",
        "route_to_recovery_node",
        "block_source_work_item",
        "block_source",
        "route_to_node",
        "retry_source_stage",
        "pause_lane",
        "pause_plane",
        "stop_daemon",
        "create_incident",
        "require_operator",
        "refuse_startup",
    ]
    threshold: int | None = Field(default=None, ge=1)
    counter_name: str | None = None
    failure_class_template: str
    recovery_node_id: str | None = None
    target_node_id: str | None = None
    target_terminal_state_id: str | None = None
    max_attempts: int | None = Field(default=None, ge=1)
    incident_severity: Literal["low", "medium", "high", "critical"] | None = None

    @field_validator(
        "policy_id",
        "counter_name",
        "failure_class_template",
        "recovery_node_id",
        "target_node_id",
        "target_terminal_state_id",
    )
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator(
        "applies_to_origins",
        "applies_to_families",
        "applies_to_failure_classes",
        "applies_to_operation_ids",
        "applies_to_handler_ids",
        "applies_to_source_node_ids",
        "applies_to_source_terminal_state_ids",
        mode="before",
    )
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(
            value,
            field_label=info.field_name or "id tuple",
            allow_empty=info.field_name != "applies_to_origins",
        )

    @field_validator("applies_to_mutation_phases", mode="before")
    @classmethod
    def normalize_mutation_phases(
        cls,
        value: object,
    ) -> tuple[RuntimeEffectMutationPhaseValue, ...]:
        normalized = _normalize_unique_id_tuple(
            value,
            field_label="applies_to_mutation_phases",
            allow_empty=True,
        )
        allowed = {"pre_mutation", "partial_mutation", "unknown"}
        unknown = sorted(set(normalized) - allowed)
        if unknown:
            raise ValueError(
                "applies_to_mutation_phases contains unknown value: "
                + ", ".join(unknown)
            )
        return cast(tuple[RuntimeEffectMutationPhaseValue, ...], normalized)

    @model_validator(mode="after")
    def validate_action_requirements(self) -> "RuntimeFailurePolicyDefinition":
        if self.action == "route_to_recovery_node" and self.recovery_node_id is None:
            raise ValueError("recovery_node_id is required for action=route_to_recovery_node")
        if self.action == "route_to_node" and self.target_node_id is None:
            raise ValueError("target_node_id is required for action=route_to_node")
        if (self.threshold is None) != (self.counter_name is None):
            raise ValueError("threshold and counter_name must be declared together")
        return self


class WorkflowPlaneSchedulerPolicyDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["workflow_plane_scheduler_policy"] = "workflow_plane_scheduler_policy"
    policy_id: str
    plane_order: tuple[Plane, ...] = Field(min_length=1)
    concurrency_policy_id: str | None = None
    lanes: tuple[WorkflowLaneDefinition, ...] = Field(min_length=1)
    claim_policies_by_plane: dict[Plane, PlaneQueueClaimPolicyDefinition]
    completion_check_order: tuple[Plane, ...] = ()
    experimental_multi_lane: bool = False
    lane_conflict_policies: tuple[LaneConflictPolicyDefinition, ...] = ()

    @field_validator("policy_id", "concurrency_policy_id")
    @classmethod
    def validate_ids(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _canonical(value, info)

    @field_validator("plane_order", "completion_check_order")
    @classmethod
    def validate_unique_planes(cls, value: tuple[Plane, ...], info: ValidationInfo) -> tuple[Plane, ...]:
        if len(set(value)) != len(value):
            raise ValueError(f"{info.field_name or 'plane tuple'} may not contain duplicate planes")
        return value

    @model_validator(mode="after")
    def validate_scheduler_closure(self) -> "WorkflowPlaneSchedulerPolicyDefinition":
        plane_set = set(self.plane_order)
        lane_ids: set[str] = set()
        lanes_by_plane: dict[Plane, list[WorkflowLaneDefinition]] = {}
        missing_claim_policies = plane_set - set(self.claim_policies_by_plane)
        if missing_claim_policies:
            raise ValueError("claim_policies_by_plane must include every plane in plane_order")
        for plane, policy in self.claim_policies_by_plane.items():
            if policy.plane is not plane:
                raise ValueError("claim_policies_by_plane keys must match policy.plane")
        for lane in self.lanes:
            if lane.lane_id in lane_ids:
                raise ValueError(f"duplicate lane_id: {lane.lane_id}")
            lane_ids.add(lane.lane_id)
            lanes_by_plane.setdefault(lane.plane, []).append(lane)
            if lane.plane not in plane_set:
                raise ValueError("lanes may only reference planes in plane_order")
            if lane.claim_policy_id != self.claim_policies_by_plane[lane.plane].policy_id:
                raise ValueError("lane claim_policy_id must match its plane claim policy")
            unknown_families = set(lane.allowed_family_ids) - set(
                self.claim_policies_by_plane[lane.plane].family_order
            )
            if unknown_families:
                raise ValueError("lane allowed_family_ids must be included in its claim policy")
            if not self.experimental_multi_lane and lane.max_active_runs != 1:
                raise ValueError("experimental_multi_lane is required for max_active_runs > 1")
        if not self.experimental_multi_lane:
            for plane, lanes in lanes_by_plane.items():
                if len(lanes) > 1:
                    raise ValueError(
                        f"production scheduler allows only one lane per plane; "
                        f"{plane.value} has {len(lanes)} lanes"
                    )
        conflict_pairs = {
            pair
            for policy in self.lane_conflict_policies
            for pair in policy.lane_pairs
        }
        for conflict_policy in self.lane_conflict_policies:
            for lane_id in (*conflict_policy.lane_ids, *conflict_policy.concurrent_with_lane_ids):
                if lane_id not in lane_ids:
                    raise ValueError(f"lane conflict policy references unknown lane {lane_id}")
        if self.experimental_multi_lane:
            for lanes in lanes_by_plane.values():
                for first_index, first_lane in enumerate(lanes):
                    for second_lane in lanes[first_index + 1:]:
                        first_lane_id, second_lane_id = sorted((first_lane.lane_id, second_lane.lane_id))
                        pair = (first_lane_id, second_lane_id)
                        if pair not in conflict_pairs:
                            raise ValueError(f"lane conflict policy missing for lane pair {pair[0]} + {pair[1]}")
        return self


class OperatorControlCapabilityDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["operator_control_capability"] = "operator_control_capability"
    capability_id: str
    action: Literal[
        "pause",
        "resume",
        "stop",
        "cancel",
        "requeue",
        "retry",
        "block",
        "unblock",
        "supersede",
        "archive",
        "inspect",
        "approve",
        "deny",
        "reload_config",
        "recompile",
        "reset_workspace_schema",
        "repair_state",
    ]
    target_type: Literal[
        "workspace",
        "plane",
        "lane",
        "family",
        "lineage",
        "work_item",
        "run",
        "node",
        "completion_target",
    ]
    plane: Plane | None = None
    family_ids: tuple[WorkItemFamilyId, ...] = ()
    lane_ids: tuple[str, ...] = ()
    allowed_lifecycle_states: tuple[str, ...] = ()

    @field_validator("capability_id")
    @classmethod
    def validate_capability_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="capability_id")

    @field_validator("family_ids", "lane_ids", "allowed_lifecycle_states", mode="before")
    @classmethod
    def normalize_id_tuples(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label=info.field_name or "id tuple", allow_empty=True)

    @model_validator(mode="after")
    def validate_target_scope(self) -> "OperatorControlCapabilityDefinition":
        if self.target_type in {"family", "work_item"} and not self.family_ids:
            raise ValueError("family_ids are required for family and work_item control targets")
        if self.target_type == "lane" and not self.lane_ids:
            raise ValueError("lane_ids are required for lane control targets")
        return self


class WorkspaceSchemaEpochDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["workspace_schema_epoch"] = "workspace_schema_epoch"
    epoch_id: str
    minimum_supported_epoch_id: str
    archive_required_from_epoch_ids: tuple[str, ...] = ()
    reset_command: str
    compatibility_notes: tuple[str, ...] = ()

    @field_validator("epoch_id", "minimum_supported_epoch_id")
    @classmethod
    def validate_epoch_id(cls, value: str, info: ValidationInfo) -> str:
        return _canonical(value, info)

    @field_validator("archive_required_from_epoch_ids", mode="before")
    @classmethod
    def normalize_archive_epochs(cls, value: object) -> tuple[str, ...]:
        return _normalize_unique_id_tuple(value, field_label="archive_required_from_epoch_ids", allow_empty=True)

    @field_validator("reset_command")
    @classmethod
    def validate_reset_command(cls, value: str) -> str:
        return normalize_nonempty_text(value, field_label="reset_command")

    @field_validator("compatibility_notes", mode="before")
    @classmethod
    def normalize_compatibility_notes(cls, value: object) -> tuple[str, ...]:
        raw = _ensure_sequence(value, field_label="compatibility_notes", allow_empty=True)
        notes = [normalize_nonempty_text(str(item), field_label="compatibility_notes") for item in raw]
        return tuple(notes)


def _canonical(value: str, info: ValidationInfo) -> str:
    return normalize_canonical_id(value, field_label=info.field_name or "canonical id")


def _ensure_sequence(
    value: object,
    *,
    field_label: str,
    allow_empty: bool = False,
) -> tuple[object, ...]:
    if value is None:
        values: tuple[object, ...] = ()
    elif isinstance(value, str):
        values = (value,)
    else:
        try:
            values = tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError(f"{field_label} must be a sequence") from exc
    if not values and not allow_empty:
        raise ValueError(f"{field_label} must not be empty")
    return values


def _normalize_unique_id_tuple(
    value: object,
    *,
    field_label: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    raw = _ensure_sequence(value, field_label=field_label, allow_empty=allow_empty)
    normalized = [
        normalize_canonical_id(str(item), field_label=field_label)
        for item in raw
    ]
    return _reject_duplicates(normalized, field_label=field_label)


def _normalize_unique_status_tuple(
    value: object,
    *,
    field_label: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    raw = _ensure_sequence(value, field_label=field_label, allow_empty=allow_empty)
    normalized = [normalize_status(str(item), field_label=field_label) for item in raw]
    return _reject_duplicates(normalized, field_label=field_label)


def _reject_duplicates(values: list[str], *, field_label: str) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {field_label} value: {value}")
        seen.add(value)
        deduped.append(value)
    return tuple(deduped)


def _normalize_runtime_relative_path(value: str, *, field_label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or path.as_posix() == ".":
        raise ValueError(f"{field_label} must be a safe runtime-relative path")
    return path.as_posix()


def _normalize_artifact_filename(value: str, *, field_label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or path.as_posix() in {".", ".."}:
        raise ValueError(f"{field_label} must be a safe filename")
    if path.name != path.as_posix() or "/" in normalized or "\\" in normalized:
        raise ValueError(f"{field_label} must be a filename, not a path")
    return path.as_posix()


def _normalize_file_extension(value: str, *, field_label: str) -> str:
    normalized = value.strip().lower()
    if (
        not normalized.startswith(".")
        or normalized == "."
        or "/" in normalized
        or "\\" in normalized
    ):
        raise ValueError(f"{field_label} must be a lowercase file extension such as '.md'")
    return normalized


__all__ = [
    "ArtifactContractDefinition",
    "ArtifactContractId",
    "ArtifactFilenameAdapterDefinition",
    "ArtifactFormat",
    "DocumentAdapterId",
    "LaneConflictPolicyDefinition",
    "LifecycleMutationPlanDefinition",
    "LifecycleMutationPlanId",
    "OperatorControlCapabilityDefinition",
    "OutcomeArtifactDefinition",
    "PlaneQueueClaimPolicyDefinition",
    "QueueClaimPolicyId",
    "RequestContextProfileDefinition",
    "RequestContextProfileId",
    "RequestContextRenderPlan",
    "RuntimeEffectHandlerDefinition",
    "RuntimeEffectHandlerId",
    "RuntimeEffectMutationPhaseValue",
    "RuntimeEffectRuleDefinition",
    "RuntimeEffectRuleId",
    "RuntimeFailurePolicyDefinition",
    "TerminalActionDefinition",
    "TerminalActionId",
    "WorkItemDocumentAdapterDefinition",
    "WorkItemFamilyDefinition",
    "WorkItemFamilyId",
    "WorkItemPartitionSelectorDefinition",
    "WorkItemQueueDirs",
    "WorkflowCompletionBehaviorDefinition",
    "WorkflowLaneDefinition",
    "WorkflowPlaneSchedulerPolicyDefinition",
    "WorkflowPrimitiveId",
    "WorkflowRecoveryPolicyDefinition",
    "WorkspaceSchemaEpochDefinition",
]
