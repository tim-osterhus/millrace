"""Loop-family contracts for loop architecture."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from .contracts import ContractModel
from .loop_architecture_common import (
    ControlPlane,
    LoopEdgeKind,
    LoopTerminalClass,
    OutlineMode,
    PersistedObjectEnvelope,
    PersistedObjectKind,
    RegistryObjectRef,
    _dedupe,
    _normalize_canonical_id,
    _normalize_optional_text,
    _normalize_status,
    _normalize_trigger_token,
)
from .loop_architecture_stage_contracts import LoopStageNode


class LoopTerminalState(ContractModel):
    terminal_state_id: str
    terminal_class: LoopTerminalClass
    writes_status: str
    emits_artifacts: tuple[str, ...] = ()
    ends_plane_run: bool = True

    @field_validator("terminal_state_id")
    @classmethod
    def validate_terminal_state_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="terminal state id")

    @field_validator("writes_status")
    @classmethod
    def validate_writes_status(cls, value: str) -> str:
        return _normalize_status(value, field_label="terminal state status")

    @field_validator("emits_artifacts", mode="before")
    @classmethod
    def normalize_emits_artifacts(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        names = [_normalize_canonical_id(str(item), field_label="terminal emitted artifact") for item in value]
        return _dedupe(names, field_label="")


class EdgeAlwaysCondition(ContractModel):
    kind: Literal["always"] = "always"


class EdgeFactEqualsCondition(ContractModel):
    kind: Literal["fact_equals"] = "fact_equals"
    fact: str
    value: bool | int | float | str

    @field_validator("fact")
    @classmethod
    def validate_fact(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="edge condition fact")


class EdgeArtifactPresentCondition(ContractModel):
    kind: Literal["artifact_present"] = "artifact_present"
    artifact_name: str

    @field_validator("artifact_name")
    @classmethod
    def validate_artifact_name(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="edge condition artifact name")


LoopEdgeCondition: TypeAlias = Annotated[
    EdgeAlwaysCondition | EdgeFactEqualsCondition | EdgeArtifactPresentCondition,
    Field(discriminator="kind"),
]


class LoopEdge(ContractModel):
    edge_id: str
    from_node_id: str
    to_node_id: str | None = None
    terminal_state_id: str | None = None
    on_outcomes: tuple[str, ...]
    condition: LoopEdgeCondition | None = None
    priority: int = 100
    kind: LoopEdgeKind = LoopEdgeKind.NORMAL
    description: str | None = None
    max_attempts: int | None = Field(default=None, ge=1)

    @field_validator("edge_id")
    @classmethod
    def validate_edge_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="edge id")

    @field_validator("from_node_id", "to_node_id")
    @classmethod
    def validate_node_ids(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "node id").replace("_", " ")
        return _normalize_canonical_id(value, field_label=field_name)

    @field_validator("terminal_state_id")
    @classmethod
    def validate_terminal_state_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_canonical_id(value, field_label="edge terminal state id")

    @field_validator("on_outcomes", mode="before")
    @classmethod
    def normalize_on_outcomes(
        cls,
        value: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        outcomes = [_normalize_trigger_token(str(item), field_label="edge outcome") for item in value]
        return _dedupe(outcomes, field_label="on_outcomes")

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_label="edge description")

    @model_validator(mode="after")
    def validate_targets(self) -> "LoopEdge":
        target_count = int(self.to_node_id is not None) + int(self.terminal_state_id is not None)
        if target_count != 1:
            raise ValueError(f"edge {self.edge_id} must target exactly one node or terminal state")
        if self.kind is LoopEdgeKind.TERMINAL and self.terminal_state_id is None:
            raise ValueError(f"edge {self.edge_id} with kind terminal must target a terminal_state_id")
        if self.kind is LoopEdgeKind.RETRY and self.max_attempts is None:
            raise ValueError(f"retry edge {self.edge_id} must declare max_attempts")
        if self.kind is not LoopEdgeKind.RETRY and self.max_attempts is not None:
            raise ValueError(f"edge {self.edge_id} may only declare max_attempts when kind=retry")
        return self


class OutlinePolicy(ContractModel):
    mode: OutlineMode = OutlineMode.HYBRID
    shard_glob: str | None = None

    @field_validator("shard_glob")
    @classmethod
    def validate_shard_glob(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_label="outline shard_glob")


class LoopConfigPayload(ContractModel):
    plane: ControlPlane
    nodes: tuple[LoopStageNode, ...]
    edges: tuple[LoopEdge, ...]
    entry_node_id: str
    terminal_states: tuple[LoopTerminalState, ...]
    task_authoring_profile_ref: RegistryObjectRef | None = None
    task_authoring_required: bool = False
    model_profile_ref: RegistryObjectRef | None = None
    outline_policy: OutlinePolicy | None = None

    @field_validator("entry_node_id")
    @classmethod
    def validate_entry_node_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="entry node id")

    @model_validator(mode="after")
    def validate_graph(self) -> "LoopConfigPayload":
        if not self.nodes:
            raise ValueError("loop configs must declare at least one node")
        node_ids = [node.node_id for node in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("loop configs may not contain duplicate node ids")
        if self.entry_node_id not in set(node_ids):
            raise ValueError(f"entry node {self.entry_node_id} is not declared in loop nodes")
        terminal_ids = [state.terminal_state_id for state in self.terminal_states]
        if len(set(terminal_ids)) != len(terminal_ids):
            raise ValueError("loop configs may not contain duplicate terminal state ids")
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("loop configs may not contain duplicate edge ids")
        node_id_set = set(node_ids)
        terminal_id_set = set(terminal_ids)
        for edge in self.edges:
            if edge.from_node_id not in node_id_set:
                raise ValueError(f"edge {edge.edge_id} references unknown from_node_id {edge.from_node_id}")
            if edge.to_node_id is not None and edge.to_node_id not in node_id_set:
                raise ValueError(f"edge {edge.edge_id} references unknown to_node_id {edge.to_node_id}")
            if edge.terminal_state_id is not None and edge.terminal_state_id not in terminal_id_set:
                raise ValueError(
                    f"edge {edge.edge_id} references unknown terminal_state_id {edge.terminal_state_id}"
                )
        if self.task_authoring_required and self.task_authoring_profile_ref is None:
            raise ValueError("task_authoring_required=true requires task_authoring_profile_ref")
        if (
            self.task_authoring_profile_ref
            and self.task_authoring_profile_ref.kind is not PersistedObjectKind.TASK_AUTHORING_PROFILE
        ):
            raise ValueError("task_authoring_profile_ref must reference a task_authoring_profile object")
        if self.model_profile_ref and self.model_profile_ref.kind is not PersistedObjectKind.MODEL_PROFILE:
            raise ValueError("model_profile_ref must reference a model_profile object")
        return self


class LoopConfigDefinition(PersistedObjectEnvelope):
    kind: Literal["loop_config"] = "loop_config"
    payload: LoopConfigPayload

    @model_validator(mode="after")
    def validate_loop_definition(self) -> "LoopConfigDefinition":
        if self.extends and self.extends.kind is not PersistedObjectKind.LOOP_CONFIG:
            raise ValueError("loop configs may extend only other loop_config objects")
        if self.extends and self.extends.id == self.id and self.extends.version == self.version:
            raise ValueError("loop configs may not extend themselves")
        return self


__all__ = [
    "EdgeAlwaysCondition",
    "EdgeArtifactPresentCondition",
    "EdgeFactEqualsCondition",
    "LoopConfigDefinition",
    "LoopConfigPayload",
    "LoopEdge",
    "LoopEdgeCondition",
    "LoopTerminalState",
    "OutlinePolicy",
]
