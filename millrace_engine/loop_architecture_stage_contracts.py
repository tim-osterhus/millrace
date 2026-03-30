"""Stage-family contracts for loop architecture."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import (
    ContractModel,
    ReasoningEffort,
    RunnerKind,
    RunnerResult,
    _normalize_path,
)
from .loop_architecture_common import (
    ArtifactMultiplicity,
    ArtifactPersistence,
    ControlPlane,
    PersistedObjectEnvelope,
    PersistedObjectKind,
    QueueMutationPolicy,
    RegistryObjectRef,
    StageIdempotencePolicy,
    StageOverrideField,
    _dedupe,
    _normalize_canonical_id,
    _normalize_optional_text,
    _normalize_reference,
    _normalize_routing_token,
    _normalize_semver,
    _normalize_status,
    _normalize_stage_selector,
    _normalize_text,
    _normalize_trigger_token,
)


class StageArtifactInput(ContractModel):
    name: str
    kind: str
    required: bool = True
    multiplicity: ArtifactMultiplicity = ArtifactMultiplicity.ONE

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="input artifact name")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="input artifact kind")


class StageArtifactOutput(ContractModel):
    name: str
    kind: str
    required_on: tuple[str, ...] = ()
    persistence: ArtifactPersistence = ArtifactPersistence.RUNTIME_BUNDLE

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="output artifact name")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="output artifact kind")

    @field_validator("required_on", mode="before")
    @classmethod
    def normalize_required_on(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        normalized = [
            _normalize_trigger_token(str(item), field_label="output artifact required_on value")
            for item in value
        ]
        return _dedupe(normalized, field_label="")


class StageRetryPolicy(ContractModel):
    max_attempts: int = Field(default=0, ge=0)
    backoff_seconds: float = Field(default=0, ge=0)
    exhausted_outcome: str | None = None

    @field_validator("exhausted_outcome")
    @classmethod
    def validate_exhausted_outcome(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_routing_token(value, field_label="retry exhausted outcome")


class LoopStageNodeOverrides(ContractModel):
    model_profile_ref: RegistryObjectRef | None = None
    runner: RunnerKind | None = None
    model: str | None = None
    effort: ReasoningEffort | None = None
    allow_search: bool | None = None
    prompt_asset_ref: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_label="override model")

    @field_validator("prompt_asset_ref")
    @classmethod
    def validate_prompt_asset_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_reference(value, field_label="prompt asset ref")

    @model_validator(mode="after")
    def validate_override_refs(self) -> "LoopStageNodeOverrides":
        if (
            self.model_profile_ref is not None
            and self.model_profile_ref.kind is not PersistedObjectKind.MODEL_PROFILE
        ):
            raise ValueError("node model_profile_ref must reference a model_profile object")
        return self

    def override_fields(self) -> frozenset[StageOverrideField]:
        field_map = {
            "model_profile_ref": StageOverrideField.MODEL_PROFILE_REF,
            "runner": StageOverrideField.RUNNER,
            "model": StageOverrideField.MODEL,
            "effort": StageOverrideField.EFFORT,
            "allow_search": StageOverrideField.ALLOW_SEARCH,
            "prompt_asset_ref": StageOverrideField.PROMPT_ASSET_REF,
            "timeout_seconds": StageOverrideField.TIMEOUT_SECONDS,
        }
        present: set[StageOverrideField] = set()
        for field_name, override_field in field_map.items():
            if getattr(self, field_name) is not None:
                present.add(override_field)
        return frozenset(present)


class StageArtifactBinding(ContractModel):
    input_artifact: str
    source_node_id: str
    source_artifact: str

    @field_validator("input_artifact")
    @classmethod
    def validate_input_artifact(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="binding input artifact")

    @field_validator("source_node_id")
    @classmethod
    def validate_source_node_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="binding source node id")

    @field_validator("source_artifact")
    @classmethod
    def validate_source_artifact(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="binding source artifact")


class LoopStageNode(ContractModel):
    node_id: str
    kind_id: str
    overrides: LoopStageNodeOverrides = Field(default_factory=LoopStageNodeOverrides)
    artifact_bindings: tuple[StageArtifactBinding, ...] = ()

    @field_validator("node_id")
    @classmethod
    def validate_node_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="node id")

    @field_validator("kind_id")
    @classmethod
    def validate_kind_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="node kind id")

    @model_validator(mode="after")
    def validate_binding_uniqueness(self) -> "LoopStageNode":
        input_names = [binding.input_artifact for binding in self.artifact_bindings]
        if len(set(input_names)) != len(input_names):
            raise ValueError(f"loop node {self.node_id} may not bind the same input artifact twice")
        return self


class StageResultArtifact(ContractModel):
    kind: str
    path: Path | None = None
    ref: str | None = None
    summary: str | None = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="result artifact kind")

    @field_validator("path", mode="before")
    @classmethod
    def normalize_path(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_reference(value, field_label="result artifact ref")

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_label="result artifact summary")

    @model_validator(mode="after")
    def validate_presence(self) -> "StageResultArtifact":
        if self.path is None and self.ref is None and self.summary is None:
            raise ValueError("result artifacts must expose at least one locator or summary field")
        return self


class StructuredStageResultMetadata(ContractModel):
    runner: RunnerKind | None = None
    model: str | None = None
    effort: ReasoningEffort | None = None
    allow_search: bool | None = None
    prompt_asset_ref: str | None = None
    resolved_model_profile_ref: RegistryObjectRef | None = None
    resolved_asset_refs: tuple[str, ...] = ()

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_label="metadata model")

    @field_validator("prompt_asset_ref")
    @classmethod
    def validate_prompt_asset_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_reference(value, field_label="metadata prompt asset ref")

    @field_validator("resolved_asset_refs", mode="before")
    @classmethod
    def normalize_asset_refs(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        refs = [_normalize_reference(str(item), field_label="resolved asset ref") for item in value]
        return _dedupe(refs, field_label="")


class StructuredStageResult(ContractModel):
    stage_node_id: str
    kind_id: str
    plane: ControlPlane
    outcome: str
    status: str
    artifacts: dict[str, StageResultArtifact] = Field(default_factory=dict)
    metadata: StructuredStageResultMetadata = Field(default_factory=StructuredStageResultMetadata)
    runner_result: RunnerResult | None = None

    @field_validator("stage_node_id")
    @classmethod
    def validate_stage_node_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="stage node id")

    @field_validator("kind_id")
    @classmethod
    def validate_kind_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="stage result kind id")

    @field_validator("outcome")
    @classmethod
    def validate_outcome(cls, value: str) -> str:
        return _normalize_routing_token(value, field_label="stage result outcome")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return _normalize_status(value, field_label="stage result status")

    @model_validator(mode="before")
    @classmethod
    def populate_metadata_from_runner_result(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        runner_result = payload.get("runner_result")
        if runner_result is None:
            return payload
        if not isinstance(runner_result, RunnerResult):
            runner_result = RunnerResult.model_validate(runner_result)
            payload["runner_result"] = runner_result
        metadata = dict(payload.get("metadata") or {})
        metadata.setdefault("runner", runner_result.runner)
        metadata.setdefault("model", runner_result.model)
        payload["metadata"] = metadata
        return payload

    @model_validator(mode="after")
    def validate_artifact_names(self) -> "StructuredStageResult":
        normalized: dict[str, StageResultArtifact] = {}
        for name, artifact in self.artifacts.items():
            normalized_name = _normalize_canonical_id(name, field_label="stage result artifact name")
            normalized[normalized_name] = artifact
        object.__setattr__(self, "artifacts", normalized)
        return self


class RegisteredStageKindPayload(ContractModel):
    kind_id: str
    contract_version: str
    plane: ControlPlane
    handler_ref: str
    context_schema_ref: str
    result_schema_ref: str
    running_status: str
    terminal_statuses: tuple[str, ...]
    success_statuses: tuple[str, ...]
    input_artifacts: tuple[StageArtifactInput, ...] = ()
    output_artifacts: tuple[StageArtifactOutput, ...] = ()
    idempotence_policy: StageIdempotencePolicy
    retry_policy: StageRetryPolicy = Field(default_factory=StageRetryPolicy)
    queue_mutation_policy: QueueMutationPolicy = QueueMutationPolicy.RUNTIME_ONLY
    routing_outcomes: tuple[str, ...]
    legal_predecessors: tuple[str, ...] = ()
    legal_successors: tuple[str, ...] = ()
    allowed_overrides: tuple[StageOverrideField, ...] = ()

    @field_validator("kind_id")
    @classmethod
    def validate_kind_id(cls, value: str) -> str:
        return _normalize_canonical_id(value, field_label="stage kind id")

    @field_validator("contract_version")
    @classmethod
    def validate_contract_version(cls, value: str) -> str:
        return _normalize_semver(value, field_label="stage contract version")

    @field_validator("handler_ref", "context_schema_ref", "result_schema_ref")
    @classmethod
    def validate_refs(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "reference")
        return _normalize_reference(value, field_label=field_name.replace("_", " "))

    @field_validator("running_status")
    @classmethod
    def validate_running_status(cls, value: str) -> str:
        return _normalize_status(value, field_label="running status")

    @field_validator("terminal_statuses", mode="before")
    @classmethod
    def normalize_terminal_statuses(
        cls,
        value: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        statuses = [_normalize_status(str(item), field_label="terminal status") for item in value]
        return _dedupe(statuses, field_label="terminal_statuses")

    @field_validator("success_statuses", mode="before")
    @classmethod
    def normalize_success_statuses(
        cls,
        value: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        statuses = [_normalize_status(str(item), field_label="success status") for item in value]
        return _dedupe(statuses, field_label="success_statuses")

    @field_validator("routing_outcomes", mode="before")
    @classmethod
    def normalize_routing_outcomes(
        cls,
        value: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        outcomes = [_normalize_routing_token(str(item), field_label="routing outcome") for item in value]
        return _dedupe(outcomes, field_label="routing_outcomes")

    @field_validator("legal_predecessors", "legal_successors", mode="before")
    @classmethod
    def normalize_stage_selectors(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        selectors = [_normalize_stage_selector(str(item)) for item in value]
        return _dedupe(selectors, field_label="")

    @field_validator("allowed_overrides", mode="before")
    @classmethod
    def normalize_allowed_overrides(
        cls,
        value: tuple[StageOverrideField, ...] | list[StageOverrideField | str] | None,
    ) -> tuple[StageOverrideField, ...]:
        if not value:
            return ()
        normalized: list[StageOverrideField] = []
        seen: set[StageOverrideField] = set()
        for item in value:
            field = item if isinstance(item, StageOverrideField) else StageOverrideField(str(item))
            if field in seen:
                continue
            seen.add(field)
            normalized.append(field)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_contract(self) -> "RegisteredStageKindPayload":
        expected_prefix = f"{self.plane.value}."
        if not self.kind_id.startswith(expected_prefix):
            raise ValueError(f"stage kind id {self.kind_id} must start with {expected_prefix}")
        if self.running_status in self.terminal_statuses:
            raise ValueError("running_status may not also appear in terminal_statuses")
        if not set(self.success_statuses).issubset(set(self.terminal_statuses)):
            raise ValueError("success_statuses must be a subset of terminal_statuses")
        input_names = [artifact.name for artifact in self.input_artifacts]
        if len(set(input_names)) != len(input_names):
            raise ValueError(f"registered stage kind {self.kind_id} has duplicate input artifact names")
        output_names = [artifact.name for artifact in self.output_artifacts]
        if len(set(output_names)) != len(output_names):
            raise ValueError(f"registered stage kind {self.kind_id} has duplicate output artifact names")
        allowed_triggers = set(self.routing_outcomes) | set(self.terminal_statuses)
        for artifact in self.output_artifacts:
            invalid = [item for item in artifact.required_on if item not in allowed_triggers]
            if invalid:
                formatted = ", ".join(sorted(invalid))
                raise ValueError(
                    f"registered stage kind {self.kind_id} output {artifact.name} has unknown required_on values: {formatted}"
                )
        if self.retry_policy.exhausted_outcome and self.retry_policy.exhausted_outcome not in self.routing_outcomes:
            raise ValueError("retry exhausted outcome must appear in routing_outcomes")
        return self

    def validate_loop_node(self, node: LoopStageNode) -> LoopStageNode:
        if node.kind_id != self.kind_id:
            raise ValueError(f"loop node {node.node_id} does not reference stage kind {self.kind_id}")
        unsupported = sorted(
            field.value
            for field in node.overrides.override_fields()
            if field not in set(self.allowed_overrides)
        )
        if unsupported:
            formatted = ", ".join(unsupported)
            raise ValueError(
                f"loop node {node.node_id} overrides unsupported fields for {self.kind_id}: {formatted}"
            )
        known_inputs = {artifact.name for artifact in self.input_artifacts}
        for binding in node.artifact_bindings:
            if binding.input_artifact not in known_inputs:
                raise ValueError(
                    f"loop node {node.node_id} binds unknown input artifact {binding.input_artifact} for {self.kind_id}"
                )
        return node

    def validate_stage_result(self, result: StructuredStageResult) -> StructuredStageResult:
        if result.kind_id != self.kind_id:
            raise ValueError(f"stage result kind_id {result.kind_id} does not match {self.kind_id}")
        if result.plane is not self.plane:
            raise ValueError(
                f"stage result plane {result.plane.value} does not match stage kind plane {self.plane.value}"
            )
        if result.outcome not in self.routing_outcomes:
            raise ValueError(f"stage result outcome {result.outcome} is not declared for {self.kind_id}")
        legal_statuses = set(self.terminal_statuses) | {self.running_status}
        if result.status not in legal_statuses:
            raise ValueError(f"stage result status {result.status} is not declared for {self.kind_id}")
        declared_artifacts = {artifact.name: artifact for artifact in self.output_artifacts}
        unexpected = sorted(name for name in result.artifacts if name not in declared_artifacts)
        if unexpected:
            formatted = ", ".join(unexpected)
            raise ValueError(f"stage result for {self.kind_id} contains undeclared artifacts: {formatted}")
        for name, artifact in result.artifacts.items():
            declared_kind = declared_artifacts.get(name)
            if declared_kind is not None and artifact.kind != declared_kind.kind:
                raise ValueError(
                    f"stage result artifact {name} for {self.kind_id} must declare kind {declared_kind.kind}, got {artifact.kind}"
                )
        required_artifacts = {
            artifact.name
            for artifact in self.output_artifacts
            if result.outcome in artifact.required_on or result.status in artifact.required_on
        }
        missing = sorted(name for name in required_artifacts if name not in result.artifacts)
        if missing:
            formatted = ", ".join(missing)
            raise ValueError(f"stage result for {self.kind_id} is missing required artifacts: {formatted}")
        return result


class RegisteredStageKindDefinition(PersistedObjectEnvelope):
    kind: Literal["registered_stage_kind"] = "registered_stage_kind"
    payload: RegisteredStageKindPayload

    @model_validator(mode="after")
    def validate_stage_definition(self) -> "RegisteredStageKindDefinition":
        if self.payload.kind_id != self.id:
            raise ValueError("registered stage kind envelope id must match payload.kind_id")
        if self.extends is not None:
            raise ValueError("registered stage kinds do not support extends")
        return self


__all__ = [
    "LoopStageNode",
    "LoopStageNodeOverrides",
    "RegisteredStageKindDefinition",
    "RegisteredStageKindPayload",
    "StageArtifactBinding",
    "StageArtifactInput",
    "StageArtifactOutput",
    "StageResultArtifact",
    "StageRetryPolicy",
    "StructuredStageResult",
    "StructuredStageResultMetadata",
]
