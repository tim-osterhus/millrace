"""Concurrency and scheduling contracts for workflow primitives."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from millrace_ai.contracts import Plane

from ..common import normalize_canonical_id
from ..stage_kinds import ArchitectureContractModel
from ._validation import (
    _canonical,
    _ensure_sequence,
    _normalize_unique_id_tuple,
    _reject_duplicates,
)
from .identifiers import QueueClaimPolicyId, WorkItemFamilyId


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
    mutation_lock_scope: Literal[
        "workspace",
        "plane",
        "lane",
        "family",
        "lineage",
        "work_item",
    ] = "plane"
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
    conflict_scopes: tuple[
        Literal[
            "workspace",
            "plane",
            "lane",
            "family",
            "lineage",
            "work_item",
            "repo_path_set",
        ],
        ...,
    ]
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
                        first_lane_id, second_lane_id = sorted(
                            (first_lane.lane_id, second_lane.lane_id)
                        )
                        pair = (first_lane_id, second_lane_id)
                        if pair not in conflict_pairs:
                            raise ValueError(
                                f"lane conflict policy missing for lane pair {pair[0]} + {pair[1]}"
                            )
        return self
