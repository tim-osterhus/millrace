"""Legacy loop configuration contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .base import ContractModel
from .enums import LoopEdgeKind, Plane, StageName, TerminalResult
from .stage_metadata import legal_terminal_results, stage_plane


class CompletionBehaviorDefinition(ContractModel):
    trigger: Literal["backlog_drained"]
    readiness_rule: Literal["no_open_lineage_work"]
    stage: StageName
    request_kind: Literal["closure_target"]
    target_selector: Literal["active_closure_target"]
    rubric_policy: Literal["reuse_or_create"]
    blocked_work_policy: Literal["suppress"]
    skip_if_already_closed: bool = True
    on_pass_terminal_result: TerminalResult
    on_gap_terminal_result: TerminalResult
    create_incident_on_gap: bool = False

    @model_validator(mode="after")
    def validate_completion_behavior(self) -> "CompletionBehaviorDefinition":
        if self.on_pass_terminal_result == self.on_gap_terminal_result:
            raise ValueError("completion behavior pass/gap results must differ")
        legal_results = legal_terminal_results(self.stage)
        if self.on_pass_terminal_result.value not in legal_results:
            raise ValueError("on_pass_terminal_result is not legal for completion stage")
        if self.on_gap_terminal_result.value not in legal_results:
            raise ValueError("on_gap_terminal_result is not legal for completion stage")
        return self


class LoopEdgeDefinition(ContractModel):
    source_stage: StageName
    on_terminal_result: TerminalResult
    target_stage: StageName | None = None
    terminal_result: TerminalResult | None = None
    edge_kind: LoopEdgeKind = LoopEdgeKind.NORMAL
    max_attempts: int | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "LoopEdgeDefinition":
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        has_target = self.target_stage is not None
        has_terminal = self.terminal_result is not None

        if has_target == has_terminal:
            raise ValueError("exactly one of target_stage or terminal_result must be set")

        if self.edge_kind == LoopEdgeKind.TERMINAL and not has_terminal:
            raise ValueError("terminal edges require terminal_result")

        if self.edge_kind != LoopEdgeKind.TERMINAL and not has_target:
            raise ValueError("non-terminal edges require target_stage")

        return self


class LoopConfigDefinition(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["loop_config"] = "loop_config"

    loop_id: str
    plane: Plane
    stages: tuple[StageName, ...]
    entry_stage: StageName
    edges: tuple[LoopEdgeDefinition, ...]
    terminal_results: tuple[TerminalResult, ...]
    completion_behavior: CompletionBehaviorDefinition | None = None

    @model_validator(mode="after")
    def validate_loop_integrity(self) -> "LoopConfigDefinition":
        stage_values = [stage.value for stage in self.stages]
        stage_set = set(stage_values)

        if self.entry_stage.value not in stage_set:
            raise ValueError("entry_stage must be in stages")

        if len(stage_set) != len(self.stages):
            raise ValueError("stages must be unique")

        for stage in self.stages:
            if stage_plane(stage) != self.plane:
                raise ValueError("stages must belong to the loop plane")

        terminal_values = {result.value for result in self.terminal_results}

        has_terminal_path = False
        for edge in self.edges:
            if edge.source_stage.value not in stage_set:
                raise ValueError("edge source_stage must be in stages")

            legal_results = legal_terminal_results(edge.source_stage)
            if edge.on_terminal_result.value not in legal_results:
                raise ValueError("edge on_terminal_result is not legal for source_stage")

            if edge.target_stage is not None and edge.target_stage.value not in stage_set:
                raise ValueError("edge target_stage must be in stages")

            if edge.terminal_result is not None:
                if edge.terminal_result.value not in terminal_values:
                    raise ValueError("edge terminal_result must be in terminal_results")
                has_terminal_path = True

        if self.completion_behavior is not None:
            if self.completion_behavior.stage.value not in stage_set:
                raise ValueError("completion_behavior stage must be in stages")
            if stage_plane(self.completion_behavior.stage) != self.plane:
                raise ValueError("completion_behavior stage must belong to loop plane")
            if self.completion_behavior.on_pass_terminal_result.value not in terminal_values:
                raise ValueError(
                    "completion_behavior on_pass_terminal_result must be in terminal_results"
                )
            if self.completion_behavior.on_gap_terminal_result.value not in terminal_values:
                raise ValueError(
                    "completion_behavior on_gap_terminal_result must be in terminal_results"
                )

        if not has_terminal_path:
            raise ValueError("loop must include at least one terminal edge")

        return self


__all__ = [
    "CompletionBehaviorDefinition",
    "LoopConfigDefinition",
    "LoopEdgeDefinition",
]
