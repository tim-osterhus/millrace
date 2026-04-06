"""Public enums and Pydantic models for frozen-plan compilation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
import re

from pydantic import Field, field_validator, model_validator

from .config import ConfigApplyBoundary
from .contracts import (
    ContractModel,
    ControlPlane,
    LoopEdgeKind,
    LoopTerminalClass,
    LoopTerminalState,
    ModePolicyToggles,
    OutlinePolicy,
    ReasoningEffort,
    RegistryObjectRef,
    RegistrySourceKind,
    ResearchParticipationMode,
    RunnerKind,
    StageArtifactInput,
    StageArtifactOutput,
    StageIdempotencePolicy,
    StageOverrideField,
)
from .materialization_models import MaterializedAssetBinding, ProvenanceEntry
from .provenance import FrozenPlanIdentity, RuntimeProvenanceContext


COMPILER_VERSION = "01b-core"
PLAN_SCHEMA_VERSION = "1.0"
RESOLVED_SNAPSHOT_SCHEMA_VERSION = "1.0"
DIAGNOSTIC_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _normalize_unique_text_items(value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if not value:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _expected_resume_states(terminal_states: tuple[LoopTerminalState, ...]) -> tuple["FrozenResumeState", ...]:
    return tuple(
        FrozenResumeState(
            status=terminal_state.writes_status,
            terminal_state_id=terminal_state.terminal_state_id,
            terminal_class=terminal_state.terminal_class,
        )
        for terminal_state in sorted(
            terminal_states,
            key=lambda item: (item.writes_status, item.terminal_state_id),
        )
    )


class CompileStatus(str, Enum):
    OK = "ok"
    FAIL = "fail"


class DiagnosticSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class CompilePhase(str, Enum):
    MATERIALIZE = "materialize"
    VALIDATE = "validate"
    EMIT = "emit"


class FrozenPlanSourceKind(str, Enum):
    REGISTRY = "registry"
    ASSET = "asset"


class CompilerDiagnostic(ContractModel):
    code: str
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    phase: CompilePhase
    path: str
    message: str
    object_ref: str | None = None
    source_ref: str | None = None
    related_refs: tuple[str, ...] = ()

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not DIAGNOSTIC_CODE_RE.fullmatch(normalized):
            raise ValueError("compiler diagnostic codes must use uppercase underscore tokens")
        return normalized

    @field_validator("path", "message", "object_ref", "source_ref")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split()) if getattr(info, "field_name", None) == "message" else value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("related_refs", mode="before")
    @classmethod
    def normalize_related_refs(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        return _normalize_unique_text_items(value)


class FrozenParameterRebindingRule(ContractModel):
    plane: ControlPlane
    node_id: str
    kind_id: str
    field: StageOverrideField
    current_value: Any = None
    rebind_at_boundary: ConfigApplyBoundary = ConfigApplyBoundary.STAGE_BOUNDARY
    stage_kind_ref: RegistryObjectRef

    @field_validator("node_id", "kind_id")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("parameter rebinding rule identifiers may not be empty")
        return normalized


class FrozenPlanSourceRef(ContractModel):
    kind: FrozenPlanSourceKind
    object_ref: str
    title: str | None = None
    aliases: tuple[str, ...] = ()
    registry_source_kind: RegistrySourceKind | None = None
    source_ref: str | None = None
    source_layer: str
    sha256: str

    @field_validator("object_ref", "source_layer", "sha256", "title", "source_ref")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        return _normalize_unique_text_items(value)


class FrozenResumeState(ContractModel):
    status: str
    terminal_state_id: str
    terminal_class: LoopTerminalClass

    @field_validator("status", "terminal_state_id")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("resume state fields may not be empty")
        return normalized


class FrozenTransition(ContractModel):
    edge_id: str
    from_node_id: str
    to_node_id: str | None = None
    terminal_state_id: str | None = None
    terminal_status: str | None = None
    on_outcomes: tuple[str, ...]
    kind: LoopEdgeKind
    priority: int
    max_attempts: int | None = None
    condition: dict[str, Any] | None = None

    @field_validator("edge_id", "from_node_id", "to_node_id", "terminal_state_id", "terminal_status")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("transition identifiers may not be empty")
        return normalized


class FrozenStagePlan(ContractModel):
    plane: ControlPlane
    node_id: str
    kind_id: str
    stage_kind_ref: RegistryObjectRef
    handler_ref: str
    model_profile_ref: RegistryObjectRef | None = None
    runner: RunnerKind | None = None
    model: str | None = None
    effort: ReasoningEffort | None = None
    allow_search: bool | None = None
    prompt_asset_ref: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)
    prompt_asset: MaterializedAssetBinding | None = None
    running_status: str
    terminal_statuses: tuple[str, ...]
    success_statuses: tuple[str, ...]
    routing_outcomes: tuple[str, ...]
    input_artifacts: tuple[StageArtifactInput, ...]
    output_artifacts: tuple[StageArtifactOutput, ...]
    idempotence_policy: StageIdempotencePolicy
    retry_max_attempts: int = Field(ge=0)
    retry_exhausted_outcome: str
    runtime_bundle_outputs: tuple[str, ...]

    @field_validator(
        "node_id",
        "kind_id",
        "handler_ref",
        "model",
        "prompt_asset_ref",
        "running_status",
        "retry_exhausted_outcome",
    )
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized


class FrozenLoopPlan(ContractModel):
    requested_ref: RegistryObjectRef
    resolved_ref: RegistryObjectRef | None = None
    parent_ref: RegistryObjectRef | None = None
    plane: ControlPlane
    entry_node_id: str
    task_authoring_profile_ref: RegistryObjectRef | None = None
    model_profile_ref: RegistryObjectRef | None = None
    outline_policy: OutlinePolicy | None = None
    stages: tuple[FrozenStagePlan, ...]
    transitions: tuple[FrozenTransition, ...]
    terminal_states: tuple[LoopTerminalState, ...]
    resume_states: tuple[FrozenResumeState, ...]
    provenance: tuple[ProvenanceEntry, ...] = ()

    @field_validator("entry_node_id")
    @classmethod
    def validate_entry_node_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("entry_node_id may not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_alignment(self) -> "FrozenLoopPlan":
        if any(stage.plane is not self.plane for stage in self.stages):
            raise ValueError("frozen loop stage planes must match the loop plane")
        if self.resume_states != _expected_resume_states(self.terminal_states):
            raise ValueError("resume_states must match the terminal-state index derived from terminal_states")
        return self


class FrozenRunPlanContent(ContractModel):
    schema_version: str = PLAN_SCHEMA_VERSION
    compiler_version: str = COMPILER_VERSION
    selection_ref: RegistryObjectRef
    selected_mode_ref: RegistryObjectRef | None = None
    selected_execution_loop_ref: RegistryObjectRef | None = None
    selected_research_loop_ref: RegistryObjectRef | None = None
    task_authoring_profile_ref: RegistryObjectRef | None = None
    model_profile_ref: RegistryObjectRef | None = None
    research_participation: ResearchParticipationMode = ResearchParticipationMode.NONE
    outline_policy: OutlinePolicy | None = None
    policy_toggles: ModePolicyToggles | None = None
    execution_plan: FrozenLoopPlan | None = None
    research_plan: FrozenLoopPlan | None = None
    parameter_rebinding_rules: tuple[FrozenParameterRebindingRule, ...] = ()
    source_refs: tuple[FrozenPlanSourceRef, ...] = ()

    @model_validator(mode="after")
    def validate_plans(self) -> "FrozenRunPlanContent":
        if self.execution_plan is None and self.research_plan is None:
            raise ValueError("frozen run plans must include at least one plane-local plan")
        if self.execution_plan is not None:
            if self.execution_plan.plane is not ControlPlane.EXECUTION:
                raise ValueError("execution_plan must own the execution plane")
            if self.selected_execution_loop_ref is None:
                raise ValueError("execution_plan requires selected_execution_loop_ref")
            if self.execution_plan.requested_ref != self.selected_execution_loop_ref:
                raise ValueError("selected_execution_loop_ref must match execution_plan.requested_ref")
        elif self.selected_execution_loop_ref is not None:
            raise ValueError("selected_execution_loop_ref requires execution_plan")
        if self.research_plan is not None:
            if self.research_plan.plane is not ControlPlane.RESEARCH:
                raise ValueError("research_plan must own the research plane")
            if self.selected_research_loop_ref is None:
                raise ValueError("research_plan requires selected_research_loop_ref")
            if self.research_plan.requested_ref != self.selected_research_loop_ref:
                raise ValueError("selected_research_loop_ref must match research_plan.requested_ref")
        elif self.selected_research_loop_ref is not None:
            raise ValueError("selected_research_loop_ref requires research_plan")

        if self.selected_mode_ref is not None:
            if self.selection_ref != self.selected_mode_ref:
                raise ValueError("selection_ref must match selected_mode_ref when a mode is selected")
        else:
            selected_loop_refs = tuple(
                ref for ref in (self.selected_execution_loop_ref, self.selected_research_loop_ref) if ref is not None
            )
            if not selected_loop_refs:
                raise ValueError("selection_ref must be explained by a selected mode or selected loop")
            if self.selection_ref not in selected_loop_refs:
                raise ValueError("selection_ref must match the selected loop ref for direct loop compiles")
        return self


class FrozenRunPlan(ContractModel):
    run_id: str
    compiled_at: datetime
    content_hash: str
    content: FrozenRunPlanContent
    compile_diagnostics: tuple[CompilerDiagnostic, ...] = ()

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("run_id may not be empty")
        return normalized

    @property
    def identity(self) -> FrozenPlanIdentity:
        return FrozenPlanIdentity(
            plan_id=f"frozen-plan:{self.content_hash}",
            run_id=self.run_id,
            compiled_at=self.compiled_at,
            content_hash=self.content_hash,
            selection_ref=self.content.selection_ref,
        )

    def runtime_provenance_context(self) -> RuntimeProvenanceContext:
        from .compiler_rebinding import resolved_snapshot_id_for_run, runtime_stage_parameter_map

        return RuntimeProvenanceContext(
            snapshot_id=resolved_snapshot_id_for_run(self.run_id, self.content_hash),
            frozen_plan=self.identity,
            stage_bound_execution_parameters=runtime_stage_parameter_map(self.content),
        )


class CompileTimeResolvedSnapshot(ContractModel):
    schema_version: str = RESOLVED_SNAPSHOT_SCHEMA_VERSION
    snapshot_id: str
    created_at: datetime
    run_id: str
    selection_ref: RegistryObjectRef
    frozen_plan: FrozenPlanIdentity
    content: FrozenRunPlanContent
    compile_diagnostics: tuple[CompilerDiagnostic, ...] = ()

    @field_validator("snapshot_id", "run_id")
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("snapshot text fields may not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_alignment(self) -> "CompileTimeResolvedSnapshot":
        from .compiler_rebinding import resolved_snapshot_id_for_run

        if self.selection_ref != self.content.selection_ref:
            raise ValueError("snapshot selection_ref must match content.selection_ref")
        if self.frozen_plan.selection_ref != self.content.selection_ref:
            raise ValueError("snapshot frozen_plan.selection_ref must match content.selection_ref")
        if self.frozen_plan.run_id != self.run_id:
            raise ValueError("snapshot frozen_plan.run_id must match run_id")
        expected_snapshot_id = resolved_snapshot_id_for_run(self.run_id, self.frozen_plan.content_hash)
        if self.snapshot_id != expected_snapshot_id:
            raise ValueError("snapshot_id must match the derived run/hash snapshot id")
        return self

    def runtime_provenance_context(self) -> RuntimeProvenanceContext:
        from .compiler_rebinding import runtime_stage_parameter_map

        return RuntimeProvenanceContext(
            snapshot_id=self.snapshot_id,
            frozen_plan=self.frozen_plan,
            stage_bound_execution_parameters=runtime_stage_parameter_map(self.content),
        )


class CompileDiagnosticsArtifact(ContractModel):
    run_id: str
    generated_at: datetime
    selection_ref: RegistryObjectRef
    result: CompileStatus
    content_hash: str | None = None
    diagnostics: tuple[CompilerDiagnostic, ...] = ()

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("run_id may not be empty")
        return normalized


class CompileArtifacts(ContractModel):
    run_dir: Path
    compile_diagnostics_json_path: Path
    resolved_snapshot_json_path: Path | None = None
    resolved_snapshot_markdown_path: Path | None = None
    frozen_plan_json_path: Path | None = None
    frozen_plan_markdown_path: Path | None = None

    @model_validator(mode="after")
    def validate_plan_paths(self) -> "CompileArtifacts":
        if (self.resolved_snapshot_json_path is None) != (self.resolved_snapshot_markdown_path is None):
            raise ValueError("resolved snapshot artifact paths must either both be present or both be omitted")
        if (self.frozen_plan_json_path is None) != (self.frozen_plan_markdown_path is None):
            raise ValueError("frozen plan artifact paths must either both be present or both be omitted")
        return self


class CompileResult(ContractModel):
    status: CompileStatus
    selection_ref: RegistryObjectRef
    run_id: str
    diagnostics: tuple[CompilerDiagnostic, ...] = ()
    plan: FrozenRunPlan | None = None
    snapshot: CompileTimeResolvedSnapshot | None = None
    artifacts: CompileArtifacts | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> "CompileResult":
        if self.status is CompileStatus.OK and (self.plan is None or self.snapshot is None or self.artifacts is None):
            raise ValueError("successful compile results must include plan, snapshot, and artifacts")
        if self.status is CompileStatus.OK and (
            self.artifacts.resolved_snapshot_json_path is None
            or self.artifacts.resolved_snapshot_markdown_path is None
            or self.artifacts.frozen_plan_json_path is None
            or self.artifacts.frozen_plan_markdown_path is None
        ):
            raise ValueError("successful compile results must include snapshot and frozen plan artifact paths")
        if self.status is CompileStatus.FAIL and self.plan is not None:
            raise ValueError("failed compile results may not include a frozen plan")
        if self.status is CompileStatus.FAIL and self.snapshot is not None:
            raise ValueError("failed compile results may not include a resolved snapshot")
        return self


__all__ = [
    "COMPILER_VERSION",
    "PLAN_SCHEMA_VERSION",
    "RESOLVED_SNAPSHOT_SCHEMA_VERSION",
    "CompileArtifacts",
    "CompileDiagnosticsArtifact",
    "CompilePhase",
    "CompileResult",
    "CompileStatus",
    "CompileTimeResolvedSnapshot",
    "CompilerDiagnostic",
    "DiagnosticSeverity",
    "FrozenLoopPlan",
    "FrozenParameterRebindingRule",
    "FrozenPlanSourceKind",
    "FrozenPlanSourceRef",
    "FrozenResumeState",
    "FrozenRunPlan",
    "FrozenRunPlanContent",
    "FrozenStagePlan",
    "FrozenTransition",
]
