"""Typed contracts for additive graph-shaped loop definitions."""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from millrace_ai.contracts import CapabilityPolicyOverride, CapabilityRequest, Plane

from .common import (
    dedupe_preserve_order,
    normalize_canonical_id,
    normalize_nonempty_text,
    normalize_status,
)
from .stage_kinds import ArchitectureContractModel

_ALLOWED_ENTRYPOINT_PREFIX = "entrypoints/"
_ALLOWED_SKILL_PREFIX = "skills/"

class GraphLoopEntryKey(str, Enum):
    TASK = "task"
    PROBE = "probe"
    SPEC = "spec"
    INCIDENT = "incident"
    BLUEPRINT_DRAFT = "blueprint_draft"
    CLOSURE_TARGET = "closure_target"
    LEARNING_REQUEST = "learning_request"


GraphLoopEntryKeyValue = GraphLoopEntryKey | str


def graph_loop_entry_key_value(value: GraphLoopEntryKeyValue) -> str:
    raw_value = value.value if isinstance(value, GraphLoopEntryKey) else getattr(value, "value", value)
    return str(raw_value)


def normalize_graph_loop_entry_key(value: object) -> GraphLoopEntryKeyValue:
    if isinstance(value, GraphLoopEntryKey):
        return value
    normalized = normalize_canonical_id(str(value), field_label="entry_key")
    try:
        return GraphLoopEntryKey(normalized)
    except ValueError:
        return normalized


class GraphLoopEdgeKind(str, Enum):
    NORMAL = "normal"
    RETRY = "retry"
    ESCALATION = "escalation"
    HANDOFF = "handoff"
    TERMINAL = "terminal"


class GraphLoopTerminalClass(str, Enum):
    SUCCESS = "success"
    NO_OP = "no_op"
    FOLLOWUP_NEEDED = "followup_needed"
    BLOCKED = "blocked"
    ESCALATE_PLANNING = "escalate_planning"


class GraphLoopCounterName(str, Enum):
    FIX_CYCLE_COUNT = "fix_cycle_count"
    TROUBLESHOOT_ATTEMPT_COUNT = "troubleshoot_attempt_count"
    MECHANIC_ATTEMPT_COUNT = "mechanic_attempt_count"
    CONSULTANT_INVOCATIONS = "consultant_invocations"


class GraphLoopNodeDefinition(ArchitectureContractModel):
    node_id: str
    stage_kind_id: str
    entrypoint_path: str | None = None
    attached_skill_additions: tuple[str, ...] = ()
    runner_name: str | None = None
    model_name: str | None = None
    thinking_level: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)
    request_context_profile_id: str | None = None
    context_render_plan_id: str | None = None
    execution_capability_requests: tuple[CapabilityRequest, ...] = ()
    execution_capability_policies: tuple[CapabilityPolicyOverride, ...] = ()

    @field_validator("node_id", "stage_kind_id", "request_context_profile_id", "context_render_plan_id")
    @classmethod
    def validate_canonical_ids(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", None) or "canonical id"
        return normalize_canonical_id(value, field_label=field_name)

    @field_validator("entrypoint_path")
    @classmethod
    def validate_entrypoint_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_markdown_asset_path(
            value,
            field_label="entrypoint_path",
            required_prefix=_ALLOWED_ENTRYPOINT_PREFIX,
        )

    @field_validator("attached_skill_additions", mode="before")
    @classmethod
    def normalize_attached_skill_additions(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        normalized = [
            _normalize_markdown_asset_path(
                str(item),
                field_label="attached skill path",
                required_prefix=_ALLOWED_SKILL_PREFIX,
            )
            for item in value
        ]
        return dedupe_preserve_order(normalized)

    @field_validator("thinking_level")
    @classmethod
    def validate_thinking_level(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_nonempty_text(value, field_label="thinking_level")

    def declared_override_names(self) -> set[str]:
        overrides: set[str] = set()
        if self.entrypoint_path is not None:
            overrides.add("entrypoint_path")
        if self.attached_skill_additions:
            overrides.add("attached_skill_additions")
        if self.runner_name is not None:
            overrides.add("runner_name")
        if self.model_name is not None:
            overrides.add("model_name")
        if self.thinking_level is not None:
            overrides.add("thinking_level")
        if self.timeout_seconds is not None:
            overrides.add("timeout_seconds")
        if self.execution_capability_requests:
            overrides.add("execution_capability_requests")
        if self.execution_capability_policies:
            overrides.add("execution_capability_policies")
        return overrides


class GraphLoopEntryDefinition(ArchitectureContractModel):
    entry_key: GraphLoopEntryKeyValue
    node_id: str

    @field_validator("entry_key", mode="before")
    @classmethod
    def validate_entry_key(cls, value: object) -> GraphLoopEntryKeyValue:
        return normalize_graph_loop_entry_key(value)

    @field_validator("entry_key", mode="after")
    @classmethod
    def coerce_known_entry_key(cls, value: GraphLoopEntryKeyValue) -> GraphLoopEntryKeyValue:
        return normalize_graph_loop_entry_key(value)

    @field_validator("node_id")
    @classmethod
    def validate_node_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="entry node id")


class GraphLoopTerminalStateDefinition(ArchitectureContractModel):
    terminal_state_id: str
    terminal_class: GraphLoopTerminalClass
    terminal_action_id: str
    writes_status: str
    router_reason: str | None = None
    failure_class_template: str | None = None
    emits_artifacts: tuple[str, ...] = ()
    ends_plane_run: bool = True

    @field_validator("terminal_state_id", "terminal_action_id", "router_reason", "failure_class_template")
    @classmethod
    def validate_terminal_state_id(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", None) or "terminal_state_id"
        return normalize_canonical_id(value, field_label=field_name)

    @field_validator("writes_status")
    @classmethod
    def validate_writes_status(cls, value: str) -> str:
        return normalize_status(value, field_label="writes_status")

    @field_validator("emits_artifacts", mode="before")
    @classmethod
    def normalize_emits_artifacts(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        normalized = [
            normalize_canonical_id(str(item), field_label="terminal emitted artifact")
            for item in value
        ]
        return dedupe_preserve_order(normalized)

class GraphLoopEdgeDefinition(ArchitectureContractModel):
    edge_id: str
    from_node_id: str
    to_node_id: str | None = None
    terminal_state_id: str | None = None
    on_outcomes: tuple[str, ...] = Field(min_length=1)
    kind: GraphLoopEdgeKind = GraphLoopEdgeKind.NORMAL
    priority: int = 100
    description: str | None = None
    max_attempts: int | None = Field(default=None, ge=1)

    @field_validator("edge_id", "from_node_id", "to_node_id", "terminal_state_id")
    @classmethod
    def validate_canonical_refs(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", None) or "canonical ref"
        return normalize_canonical_id(value, field_label=field_name)

    @field_validator("on_outcomes", mode="before")
    @classmethod
    def normalize_on_outcomes(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        normalized = [normalize_status(str(item), field_label="edge outcome") for item in value]
        return dedupe_preserve_order(normalized)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_nonempty_text(value, field_label="edge description")

    @model_validator(mode="after")
    def validate_targets(self) -> "GraphLoopEdgeDefinition":
        target_count = int(self.to_node_id is not None) + int(self.terminal_state_id is not None)
        if target_count != 1:
            raise ValueError(
                f"edge {self.edge_id} must target exactly one node or terminal_state_id"
            )
        if self.kind is GraphLoopEdgeKind.TERMINAL and self.terminal_state_id is None:
            raise ValueError(
                f"edge {self.edge_id} with kind=terminal must target a terminal_state_id"
            )
        if self.kind is GraphLoopEdgeKind.RETRY and self.max_attempts is None:
            raise ValueError(f"retry edge {self.edge_id} must declare max_attempts")
        if self.kind is not GraphLoopEdgeKind.RETRY and self.max_attempts is not None:
            raise ValueError(
                f"edge {self.edge_id} may only declare max_attempts when kind=retry"
            )
        return self


class GraphLoopRootSourcePolicyDefinition(ArchitectureContractModel):
    accepted_kinds: tuple[str, ...] = Field(min_length=1)
    resolution: Literal["runtime_inventory"] = "runtime_inventory"

    @field_validator("accepted_kinds")
    @classmethod
    def validate_accepted_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = [
            normalize_canonical_id(kind, field_label="root_source_policy.accepted_kinds")
            for kind in value
        ]
        if not normalized:
            raise ValueError("root_source_policy.accepted_kinds must not be empty")
        return dedupe_preserve_order(normalized)


class GraphLoopCompletionBehaviorDefinition(ArchitectureContractModel):
    trigger: Literal["backlog_drained"]
    readiness_rule: Literal["no_open_lineage_work"]
    target_node_id: str
    request_kind: Literal["closure_target"]
    target_selector: Literal["active_closure_target"]
    root_source_policy: GraphLoopRootSourcePolicyDefinition
    rubric_policy: Literal["reuse_or_create"]
    blocked_work_policy: Literal["suppress"]
    skip_if_already_closed: bool = True
    on_pass_terminal_state_id: str
    on_gap_terminal_state_id: str
    create_incident_on_gap: bool = False

    @field_validator("target_node_id", "on_pass_terminal_state_id", "on_gap_terminal_state_id")
    @classmethod
    def validate_canonical_ids(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "canonical id"
        return normalize_canonical_id(value, field_label=field_name)

    @model_validator(mode="after")
    def validate_distinct_terminal_states(self) -> "GraphLoopCompletionBehaviorDefinition":
        if self.on_pass_terminal_state_id == self.on_gap_terminal_state_id:
            raise ValueError("completion behavior pass/gap terminal states must differ")
        return self


class GraphLoopResumePolicyDefinition(ArchitectureContractModel):
    policy_id: str
    source_node_id: str
    on_outcome: str
    default_target_node_id: str
    metadata_stage_keys: tuple[str, ...] = ()
    disallowed_target_node_ids: tuple[str, ...] = ()
    route_reason: str | None = None

    @field_validator(
        "policy_id",
        "source_node_id",
        "default_target_node_id",
        "route_reason",
        mode="before",
    )
    @classmethod
    def validate_canonical_refs(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", None) or "canonical id"
        return normalize_canonical_id(str(value), field_label=field_name)

    @field_validator("on_outcome")
    @classmethod
    def validate_on_outcome(cls, value: str) -> str:
        return normalize_status(value, field_label="resume policy outcome")

    @field_validator("metadata_stage_keys", "disallowed_target_node_ids", mode="before")
    @classmethod
    def normalize_tuple_refs(
        cls,
        value: tuple[str, ...] | list[str] | None,
        info: object,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        field_name = getattr(info, "field_name", None) or "tuple ref"
        normalized = [
            normalize_canonical_id(str(item), field_label=field_name)
            for item in value
        ]
        return dedupe_preserve_order(normalized)


class GraphLoopThresholdPolicyDefinition(ArchitectureContractModel):
    policy_id: str
    source_node_ids: tuple[str, ...] = Field(min_length=1)
    on_outcome: str
    counter_name: GraphLoopCounterName
    threshold: int = Field(ge=1)
    exhausted_target_node_id: str | None = None
    exhausted_terminal_state_id: str | None = None
    recovery_counter_mutation_name: GraphLoopCounterName | None = None
    exhausted_counter_mutation_name: GraphLoopCounterName | None = None
    route_reason: str | None = None
    exhausted_route_reason: str | None = None
    default_failure_class_template: str | None = None
    exhausted_failure_class_template: str | None = None

    @field_validator(
        "policy_id",
        "exhausted_target_node_id",
        "exhausted_terminal_state_id",
        "route_reason",
        "exhausted_route_reason",
        "default_failure_class_template",
        "exhausted_failure_class_template",
    )
    @classmethod
    def validate_canonical_refs(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", None) or "canonical ref"
        return normalize_canonical_id(value, field_label=field_name)

    @field_validator("source_node_ids", mode="before")
    @classmethod
    def normalize_source_node_ids(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        normalized = [
            normalize_canonical_id(str(item), field_label="source_node_ids")
            for item in value
        ]
        return dedupe_preserve_order(normalized)

    @field_validator("on_outcome")
    @classmethod
    def validate_on_outcome(cls, value: str) -> str:
        return normalize_status(value, field_label="threshold policy outcome")

    @model_validator(mode="after")
    def validate_exhausted_target_shape(self) -> "GraphLoopThresholdPolicyDefinition":
        target_count = int(self.exhausted_target_node_id is not None) + int(
            self.exhausted_terminal_state_id is not None
        )
        if target_count != 1:
            raise ValueError(
                "threshold policies must define exactly one exhausted target node or terminal state"
            )
        return self


class GraphLoopRuntimeFailureRecoveryDefinition(ArchitectureContractModel):
    default_repair_node_id: str
    default_failure_class_template: str = "runtime_failure_recovery"
    counter_name: GraphLoopCounterName
    threshold: int = Field(ge=1)
    exhausted_terminal_state_id: str | None = None

    @field_validator("default_repair_node_id", "default_failure_class_template", "exhausted_terminal_state_id")
    @classmethod
    def validate_canonical_refs(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", None) or "canonical ref"
        return normalize_canonical_id(value, field_label=field_name)


class GraphLoopDynamicPoliciesDefinition(ArchitectureContractModel):
    resume_policies: tuple[GraphLoopResumePolicyDefinition, ...] = ()
    threshold_policies: tuple[GraphLoopThresholdPolicyDefinition, ...] = ()

    @model_validator(mode="after")
    def validate_unique_policy_ids(self) -> "GraphLoopDynamicPoliciesDefinition":
        policy_ids = [
            *[policy.policy_id for policy in self.resume_policies],
            *[policy.policy_id for policy in self.threshold_policies],
        ]
        if len(set(policy_ids)) != len(policy_ids):
            raise ValueError("graph loops may not contain duplicate dynamic policy ids")
        return self


class GraphLoopDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["graph_loop"] = "graph_loop"

    loop_id: str
    plane: Plane
    nodes: tuple[GraphLoopNodeDefinition, ...] = Field(min_length=1)
    edges: tuple[GraphLoopEdgeDefinition, ...] = Field(min_length=1)
    entry_nodes: tuple[GraphLoopEntryDefinition, ...] = Field(min_length=1)
    terminal_states: tuple[GraphLoopTerminalStateDefinition, ...] = Field(min_length=1)
    handoff_terminal_only_outcomes: tuple[str, ...] = ()
    dynamic_policies: GraphLoopDynamicPoliciesDefinition | None = None
    runtime_failure_recovery: GraphLoopRuntimeFailureRecoveryDefinition | None = None
    completion_behavior: GraphLoopCompletionBehaviorDefinition | None = None

    @field_validator("loop_id")
    @classmethod
    def validate_loop_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="loop_id")

    @field_validator("handoff_terminal_only_outcomes", mode="before")
    @classmethod
    def normalize_handoff_terminal_only_outcomes(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        normalized = [
            normalize_status(str(item), field_label="handoff terminal-only outcome")
            for item in value
        ]
        return dedupe_preserve_order(normalized)

    @model_validator(mode="after")
    def validate_graph(self) -> "GraphLoopDefinition":
        node_ids = [node.node_id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("graph loops may not contain duplicate node ids")

        terminal_state_ids = [state.terminal_state_id for state in self.terminal_states]
        if len(set(terminal_state_ids)) != len(terminal_state_ids):
            raise ValueError("graph loops may not contain duplicate terminal state ids")

        edge_ids = [edge.edge_id for edge in self.edges]
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("graph loops may not contain duplicate edge ids")

        entry_keys = [entry.entry_key for entry in self.entry_nodes]
        if len(set(entry_keys)) != len(entry_keys):
            raise ValueError("graph loops may not contain duplicate entry keys")

        node_id_set = set(node_ids)
        terminal_state_id_set = set(terminal_state_ids)
        for entry in self.entry_nodes:
            if entry.node_id not in node_id_set:
                raise ValueError(
                    f"entry key {graph_loop_entry_key_value(entry.entry_key)} "
                    f"references unknown node_id {entry.node_id}"
                )

        for edge in self.edges:
            if edge.from_node_id not in node_id_set:
                raise ValueError(
                    f"edge {edge.edge_id} references unknown from_node_id {edge.from_node_id}"
                )
            if edge.to_node_id is not None and edge.to_node_id not in node_id_set:
                raise ValueError(
                    f"edge {edge.edge_id} references unknown to_node_id {edge.to_node_id}"
                )
            if (
                edge.terminal_state_id is not None
                and edge.terminal_state_id not in terminal_state_id_set
            ):
                raise ValueError(
                    f"edge {edge.edge_id} references unknown terminal_state_id {edge.terminal_state_id}"
                )

        if self.dynamic_policies is not None:
            for resume_policy in self.dynamic_policies.resume_policies:
                if resume_policy.source_node_id not in node_id_set:
                    raise ValueError(
                        "resume policy "
                        f"{resume_policy.policy_id} references unknown source_node_id "
                        f"{resume_policy.source_node_id}"
                    )
                if resume_policy.default_target_node_id not in node_id_set:
                    raise ValueError(
                        "resume policy "
                        f"{resume_policy.policy_id} references unknown default_target_node_id "
                        f"{resume_policy.default_target_node_id}"
                    )
                unknown_disallowed = sorted(
                    node_id
                    for node_id in resume_policy.disallowed_target_node_ids
                    if node_id not in node_id_set
                )
                if unknown_disallowed:
                    raise ValueError(
                        "resume policy "
                        f"{resume_policy.policy_id} references unknown disallowed_target_node_ids "
                        f"{', '.join(unknown_disallowed)}"
                    )
            for threshold_policy in self.dynamic_policies.threshold_policies:
                unknown_sources = sorted(
                    node_id
                    for node_id in threshold_policy.source_node_ids
                    if node_id not in node_id_set
                )
                if unknown_sources:
                    raise ValueError(
                        "threshold policy "
                        f"{threshold_policy.policy_id} references unknown source_node_ids "
                        f"{', '.join(unknown_sources)}"
                    )
                if (
                    threshold_policy.exhausted_target_node_id is not None
                    and threshold_policy.exhausted_target_node_id not in node_id_set
                ):
                    raise ValueError(
                        "threshold policy "
                        f"{threshold_policy.policy_id} references unknown exhausted_target_node_id "
                        f"{threshold_policy.exhausted_target_node_id}"
                    )
                if (
                    threshold_policy.exhausted_terminal_state_id is not None
                    and threshold_policy.exhausted_terminal_state_id not in terminal_state_id_set
                ):
                    raise ValueError(
                        "threshold policy "
                        f"{threshold_policy.policy_id} references unknown exhausted_terminal_state_id "
                        f"{threshold_policy.exhausted_terminal_state_id}"
                    )

        if self.runtime_failure_recovery is not None:
            recovery = self.runtime_failure_recovery
            if recovery.default_repair_node_id not in node_id_set:
                raise ValueError(
                    "runtime_failure_recovery references unknown default_repair_node_id "
                    f"{recovery.default_repair_node_id}"
                )
            if (
                recovery.exhausted_terminal_state_id is not None
                and recovery.exhausted_terminal_state_id not in terminal_state_id_set
            ):
                raise ValueError(
                    "runtime_failure_recovery references unknown exhausted_terminal_state_id "
                    f"{recovery.exhausted_terminal_state_id}"
                )

        if self.completion_behavior is not None:
            if self.completion_behavior.target_node_id not in node_id_set:
                raise ValueError(
                    "completion behavior references unknown target_node_id "
                    f"{self.completion_behavior.target_node_id}"
                )
            if (
                self.completion_behavior.on_pass_terminal_state_id
                not in terminal_state_id_set
            ):
                raise ValueError(
                    "completion behavior references unknown on_pass_terminal_state_id "
                    f"{self.completion_behavior.on_pass_terminal_state_id}"
                )
            if (
                self.completion_behavior.on_gap_terminal_state_id
                not in terminal_state_id_set
            ):
                raise ValueError(
                    "completion behavior references unknown on_gap_terminal_state_id "
                    f"{self.completion_behavior.on_gap_terminal_state_id}"
                )

        return self


def _normalize_markdown_asset_path(
    value: str,
    *,
    field_label: str,
    required_prefix: str,
) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_label} must stay inside packaged runtime assets")
    if not normalized.startswith(required_prefix):
        raise ValueError(f"{field_label} must start with {required_prefix!r}")
    if path.suffix.lower() != ".md":
        raise ValueError(f"{field_label} must point at a markdown asset")
    return normalized


__all__ = [
    "GraphLoopCounterName",
    "GraphLoopCompletionBehaviorDefinition",
    "GraphLoopDynamicPoliciesDefinition",
    "GraphLoopDefinition",
    "GraphLoopEdgeDefinition",
    "GraphLoopEdgeKind",
    "GraphLoopEntryDefinition",
    "GraphLoopEntryKey",
    "GraphLoopEntryKeyValue",
    "GraphLoopNodeDefinition",
    "GraphLoopResumePolicyDefinition",
    "GraphLoopRootSourcePolicyDefinition",
    "GraphLoopRuntimeFailureRecoveryDefinition",
    "GraphLoopThresholdPolicyDefinition",
    "GraphLoopTerminalClass",
    "GraphLoopTerminalStateDefinition",
]
