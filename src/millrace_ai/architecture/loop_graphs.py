"""Typed contracts for additive graph-shaped loop definitions."""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from millrace_ai.contracts import Plane

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
    SPEC = "spec"
    INCIDENT = "incident"
    CLOSURE_TARGET = "closure_target"


class GraphLoopEdgeKind(str, Enum):
    NORMAL = "normal"
    RETRY = "retry"
    ESCALATION = "escalation"
    HANDOFF = "handoff"
    TERMINAL = "terminal"


class GraphLoopTerminalClass(str, Enum):
    SUCCESS = "success"
    FOLLOWUP_NEEDED = "followup_needed"
    BLOCKED = "blocked"
    ESCALATE_PLANNING = "escalate_planning"


class GraphLoopNodeDefinition(ArchitectureContractModel):
    node_id: str
    stage_kind_id: str
    entrypoint_path: str | None = None
    attached_skill_additions: tuple[str, ...] = ()
    runner_name: str | None = None
    model_name: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)

    @field_validator("node_id", "stage_kind_id")
    @classmethod
    def validate_canonical_ids(cls, value: str, info: object) -> str:
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
        if self.timeout_seconds is not None:
            overrides.add("timeout_seconds")
        return overrides


class GraphLoopEntryDefinition(ArchitectureContractModel):
    entry_key: GraphLoopEntryKey
    node_id: str

    @field_validator("node_id")
    @classmethod
    def validate_node_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="entry node id")


class GraphLoopTerminalStateDefinition(ArchitectureContractModel):
    terminal_state_id: str
    terminal_class: GraphLoopTerminalClass
    writes_status: str
    emits_artifacts: tuple[str, ...] = ()
    ends_plane_run: bool = True

    @field_validator("terminal_state_id")
    @classmethod
    def validate_terminal_state_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="terminal_state_id")

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


class GraphLoopCompletionBehaviorDefinition(ArchitectureContractModel):
    trigger: Literal["backlog_drained"]
    readiness_rule: Literal["no_open_lineage_work"]
    target_node_id: str
    request_kind: Literal["closure_target"]
    target_selector: Literal["active_closure_target"]
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


class GraphLoopDefinition(ArchitectureContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["graph_loop"] = "graph_loop"

    loop_id: str
    plane: Plane
    nodes: tuple[GraphLoopNodeDefinition, ...] = Field(min_length=1)
    edges: tuple[GraphLoopEdgeDefinition, ...] = Field(min_length=1)
    entry_nodes: tuple[GraphLoopEntryDefinition, ...] = Field(min_length=1)
    terminal_states: tuple[GraphLoopTerminalStateDefinition, ...] = Field(min_length=1)
    completion_behavior: GraphLoopCompletionBehaviorDefinition | None = None

    @field_validator("loop_id")
    @classmethod
    def validate_loop_id(cls, value: str) -> str:
        return normalize_canonical_id(value, field_label="loop_id")

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
                    f"entry key {entry.entry_key.value} references unknown node_id {entry.node_id}"
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
    "GraphLoopCompletionBehaviorDefinition",
    "GraphLoopDefinition",
    "GraphLoopEdgeDefinition",
    "GraphLoopEdgeKind",
    "GraphLoopEntryDefinition",
    "GraphLoopEntryKey",
    "GraphLoopNodeDefinition",
    "GraphLoopTerminalClass",
    "GraphLoopTerminalStateDefinition",
]
