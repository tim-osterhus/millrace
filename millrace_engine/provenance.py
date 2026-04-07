"""Typed provenance helpers for compile snapshots and runtime transition history."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable
import json

from pydantic import ConfigDict, Field, field_validator, model_validator

from .config import ConfigApplyBoundary
from .contracts import (
    ContractModel,
    ControlPlane,
    ReasoningEffort,
    RegistryObjectRef,
    RunnerKind,
    StageOverrideField,
)

if TYPE_CHECKING:
    from .policies.hooks import PolicyEvaluationRecord


TRANSITION_HISTORY_SCHEMA_VERSION = "1.0"
ROUTING_MODE_ATTRIBUTE = "routing_mode"
PROCEDURE_INJECTION_ATTRIBUTE = "procedure_injection"
CONTEXT_FACT_INJECTION_ATTRIBUTE = "context_fact_injection"
COMPOUNDING_PROFILE_ATTRIBUTE = "compounding_profile"
COMPOUNDING_BUDGET_ATTRIBUTE = "compounding_budget"
RUNTIME_REBINDABLE_STAGE_FIELDS = frozenset(
    {
        StageOverrideField.MODEL_PROFILE_REF,
        StageOverrideField.RUNNER,
        StageOverrideField.MODEL,
        StageOverrideField.EFFORT,
        StageOverrideField.ALLOW_SEARCH,
        StageOverrideField.TIMEOUT_SECONDS,
    }
)


def _normalize_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _normalize_text(value: str | None, *, field_label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    return normalized


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    return str(value)


def _normalize_routing_mode(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def runtime_stage_parameter_key(plane: object, node_id: str) -> str:
    """Return the normalized key used for compile-time stage parameter baselines."""

    plane_token = getattr(plane, "value", plane)
    normalized_plane = _normalize_text(str(plane_token), field_label="plane")
    normalized_node = _normalize_text(node_id, field_label="node_id")
    return f"{normalized_plane}.{normalized_node}"


def is_runtime_rebindable_stage_field(field: StageOverrideField) -> bool:
    """Return whether one stage override field is legal for runtime parameter rebinding."""

    return field in RUNTIME_REBINDABLE_STAGE_FIELDS


def runtime_rebindable_stage_fields(
    fields: Iterable[StageOverrideField],
) -> tuple[StageOverrideField, ...]:
    """Return the runtime-legal subset of a stage-kind override declaration."""

    return tuple(field for field in fields if is_runtime_rebindable_stage_field(field))


class FrozenPlanIdentity(ContractModel):
    """Stable identity for one frozen plan artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    run_id: str
    compiled_at: datetime
    content_hash: str
    selection_ref: RegistryObjectRef

    @field_validator("plan_id", "run_id", "content_hash")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value")) or ""

    @field_validator("compiled_at", mode="before")
    @classmethod
    def normalize_compiled_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @model_validator(mode="after")
    def validate_identity(self) -> "FrozenPlanIdentity":
        expected_plan_id = f"frozen-plan:{self.content_hash}"
        if self.plan_id != expected_plan_id:
            raise ValueError("plan_id must match the canonical frozen-plan content-hash format")
        return self


class RuntimeProvenanceContext(ContractModel):
    """Compile-time provenance carried into a later runtime invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str | None = None
    frozen_plan: FrozenPlanIdentity | None = None
    stage_bound_execution_parameters: dict[str, "BoundExecutionParameters"] = Field(default_factory=dict)

    @field_validator("snapshot_id")
    @classmethod
    def validate_snapshot_id(cls, value: str | None) -> str | None:
        return _normalize_text(value, field_label="snapshot_id")

    @field_validator("stage_bound_execution_parameters", mode="before")
    @classmethod
    def normalize_stage_bound_execution_parameters(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, "BoundExecutionParameters"]:
        if not value:
            return {}
        normalized: dict[str, BoundExecutionParameters] = {}
        for raw_key, raw_parameters in value.items():
            key = _normalize_text(str(raw_key), field_label="stage_bound_execution_parameters key")
            if key is None:
                continue
            normalized[key] = BoundExecutionParameters.model_validate(raw_parameters)
        return normalized

    def stage_parameters_for(self, plane: object, node_id: str) -> "BoundExecutionParameters | None":
        """Return compile-time execution parameters for one stage if present."""

        return self.stage_bound_execution_parameters.get(runtime_stage_parameter_key(plane, node_id))


class BoundExecutionParameters(ContractModel):
    """Actual execution knobs bound for one stage invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_profile_ref: RegistryObjectRef | None = None
    runner: RunnerKind | None = None
    model: str | None = None
    effort: ReasoningEffort | None = None
    allow_search: bool | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        return _normalize_text(value, field_label="model")

    def override_fields(self) -> frozenset[StageOverrideField]:
        field_map = {
            "model_profile_ref": StageOverrideField.MODEL_PROFILE_REF,
            "runner": StageOverrideField.RUNNER,
            "model": StageOverrideField.MODEL,
            "effort": StageOverrideField.EFFORT,
            "allow_search": StageOverrideField.ALLOW_SEARCH,
            "timeout_seconds": StageOverrideField.TIMEOUT_SECONDS,
        }
        present: set[StageOverrideField] = set()
        for field_name, override_field in field_map.items():
            if getattr(self, field_name) is not None:
                present.add(override_field)
        return frozenset(present)

    def value_for(self, field: StageOverrideField) -> Any:
        value_map = {
            StageOverrideField.MODEL_PROFILE_REF: self.model_profile_ref,
            StageOverrideField.RUNNER: self.runner,
            StageOverrideField.MODEL: self.model,
            StageOverrideField.EFFORT: self.effort,
            StageOverrideField.ALLOW_SEARCH: self.allow_search,
            StageOverrideField.TIMEOUT_SECONDS: self.timeout_seconds,
        }
        return value_map[field]

    def apply(self, override: "BoundExecutionParameters") -> "BoundExecutionParameters":
        updates = {
            field_name: getattr(override, field_name)
            for field_name in type(self).model_fields
            if getattr(override, field_name) is not None
        }
        return self.model_copy(update=updates)


class ExecutionParameterRebindingRequest(ContractModel):
    """One legal runtime attempt to rebind future execution parameters for a stage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plane: ControlPlane
    node_id: str
    parameters: BoundExecutionParameters
    boundary: ConfigApplyBoundary = ConfigApplyBoundary.STAGE_BOUNDARY
    reason: str | None = None

    @field_validator("node_id", "reason")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value"))

    @model_validator(mode="after")
    def validate_parameters_present(self) -> "ExecutionParameterRebindingRequest":
        if not self.parameters.override_fields():
            raise ValueError("parameter rebinding requests must set at least one execution parameter")
        return self


class AppliedExecutionParameterRebinding(ContractModel):
    """Normalized record of one applied runtime rebinding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plane: ControlPlane
    node_id: str
    boundary: ConfigApplyBoundary
    applied_fields: tuple[StageOverrideField, ...]
    previous_parameters: BoundExecutionParameters
    requested_parameters: BoundExecutionParameters
    updated_parameters: BoundExecutionParameters
    reason: str | None = None

    @field_validator("node_id", "reason")
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value"))


class RuntimeTransitionRecord(ContractModel):
    """Append-only runtime transition history record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = TRANSITION_HISTORY_SCHEMA_VERSION
    event_id: str
    previous_event_id: str | None = None
    run_id: str
    snapshot_id: str | None = None
    frozen_plan: FrozenPlanIdentity | None = None
    timestamp: datetime
    observed_timestamp: datetime
    phase: str = "runtime"
    event_name: str
    source: str
    plane: ControlPlane
    node_id: str
    kind_id: str | None = None
    outcome: str | None = None
    selected_edge_id: str | None = None
    selected_terminal_state_id: str | None = None
    selected_edge_reason: str | None = None
    condition_inputs: dict[str, Any] = Field(default_factory=dict)
    condition_result: bool | None = None
    status_before: str | None = None
    status_after: str | None = None
    active_task_before: str | None = None
    active_task_after: str | None = None
    artifacts_emitted: tuple[str, ...] = ()
    queue_mutations_applied: tuple[str, ...] = ()
    bound_execution_parameters: BoundExecutionParameters = Field(default_factory=BoundExecutionParameters)
    policy_evaluation: dict[str, Any] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "event_id",
        "previous_event_id",
        "run_id",
        "snapshot_id",
        "phase",
        "event_name",
        "source",
        "node_id",
        "kind_id",
        "outcome",
        "selected_edge_id",
        "selected_terminal_state_id",
        "selected_edge_reason",
        "status_before",
        "status_after",
        "active_task_before",
        "active_task_after",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None, info: object) -> str | None:
        return _normalize_text(value, field_label=getattr(info, "field_name", "value"))

    @field_validator("timestamp", "observed_timestamp", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("artifacts_emitted", "queue_mutations_applied", mode="before")
    @classmethod
    def normalize_strings(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = _normalize_text(str(item), field_label="string tuple value")
            if text is None or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)

    @field_validator("condition_inputs", "attributes", mode="before")
    @classmethod
    def normalize_payload_maps(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        return _json_safe(value or {})

    @field_validator("policy_evaluation", mode="before")
    @classmethod
    def normalize_policy_evaluation(cls, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        from .policies.hooks import PolicyEvaluationRecord

        if hasattr(value, "model_dump"):
            return _json_safe(PolicyEvaluationRecord.model_validate(value.model_dump(mode="python")).model_dump(mode="json"))
        if isinstance(value, dict):
            return _json_safe(PolicyEvaluationRecord.model_validate(value).model_dump(mode="json"))
        raise TypeError("policy_evaluation must be a mapping or model instance")

    @model_validator(mode="after")
    def validate_provenance_alignment(self) -> "RuntimeTransitionRecord":
        if self.frozen_plan is not None and self.frozen_plan.run_id != self.run_id:
            raise ValueError("runtime history frozen plan run_id must match record run_id")
        return self

    @property
    def routing_mode(self) -> str | None:
        return _normalize_routing_mode(self.attributes.get(ROUTING_MODE_ATTRIBUTE))

    @property
    def has_policy_evaluation(self) -> bool:
        return self.policy_evaluation is not None

    def policy_evaluation_record(self) -> "PolicyEvaluationRecord | None":
        """Return the parsed policy evaluation record when one was persisted."""

        if self.policy_evaluation is None:
            return None
        from .policies.hooks import PolicyEvaluationRecord

        return PolicyEvaluationRecord.model_validate(self.policy_evaluation)

    @property
    def policy_hook(self) -> str | None:
        record = self.policy_evaluation_record()
        return record.hook.value if record is not None else None

    @property
    def policy_evaluator(self) -> str | None:
        record = self.policy_evaluation_record()
        return record.evaluator if record is not None else None

    @property
    def policy_decision(self) -> str | None:
        record = self.policy_evaluation_record()
        return record.decision.value if record is not None else None


def routing_modes_from_records(records: Iterable[RuntimeTransitionRecord]) -> tuple[str, ...]:
    """Return the sorted unique routing modes observed across runtime history records."""

    return tuple(sorted({mode for record in records if (mode := record.routing_mode) is not None}))


def latest_policy_transition_record(
    records: Iterable[RuntimeTransitionRecord],
) -> RuntimeTransitionRecord | None:
    """Return the last transition record that persisted a policy evaluation."""

    materialized = tuple(records)
    for record in reversed(materialized):
        if record.has_policy_evaluation:
            return record
    return None


def policy_evaluation_records_from_transitions(
    records: Iterable[RuntimeTransitionRecord],
) -> tuple["PolicyEvaluationRecord", ...]:
    """Return parsed policy-evaluation records in transition-history order."""

    parsed: list[PolicyEvaluationRecord] = []
    for record in records:
        evaluation = record.policy_evaluation_record()
        if evaluation is not None:
            parsed.append(evaluation)
    return tuple(parsed)


class TransitionHistoryStore:
    """Append-only JSONL writer for runtime transition history."""

    def __init__(
        self,
        history_path: Path,
        *,
        run_id: str,
        provenance: RuntimeProvenanceContext | None = None,
    ) -> None:
        self.history_path = history_path
        self.run_id = run_id
        self.provenance = provenance or RuntimeProvenanceContext()
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self._existing_count = self._line_count()
        self._last_event_id = self._last_record_event_id()

    def append(
        self,
        *,
        event_name: str,
        source: str,
        plane: ControlPlane,
        node_id: str,
        kind_id: str | None = None,
        outcome: str | None = None,
        selected_edge_id: str | None = None,
        selected_terminal_state_id: str | None = None,
        selected_edge_reason: str | None = None,
        condition_inputs: dict[str, Any] | None = None,
        condition_result: bool | None = None,
        status_before: str | None = None,
        status_after: str | None = None,
        active_task_before: str | None = None,
        active_task_after: str | None = None,
        artifacts_emitted: Iterable[str] | None = None,
        queue_mutations_applied: Iterable[str] | None = None,
        bound_execution_parameters: BoundExecutionParameters | None = None,
        policy_evaluation: Any | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> RuntimeTransitionRecord:
        sequence = self._existing_count + 1
        moment = datetime.now(timezone.utc)
        event_id = f"{self.run_id}-transition-{sequence:04d}"
        record = RuntimeTransitionRecord.model_validate(
            {
                "event_id": event_id,
                "previous_event_id": self._last_event_id,
                "run_id": self.run_id,
                "snapshot_id": self.provenance.snapshot_id,
                "frozen_plan": self.provenance.frozen_plan,
                "timestamp": moment,
                "observed_timestamp": moment,
                "event_name": event_name,
                "source": source,
                "plane": plane,
                "node_id": node_id,
                "kind_id": kind_id,
                "outcome": outcome,
                "selected_edge_id": selected_edge_id,
                "selected_terminal_state_id": selected_terminal_state_id,
                "selected_edge_reason": selected_edge_reason,
                "condition_inputs": condition_inputs or {},
                "condition_result": condition_result,
                "status_before": status_before,
                "status_after": status_after,
                "active_task_before": active_task_before,
                "active_task_after": active_task_after,
                "artifacts_emitted": tuple(artifacts_emitted or ()),
                "queue_mutations_applied": tuple(queue_mutations_applied or ()),
                "bound_execution_parameters": bound_execution_parameters or BoundExecutionParameters(),
                "policy_evaluation": policy_evaluation,
                "attributes": attributes or {},
            }
        )
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
        self._existing_count = sequence
        self._last_event_id = record.event_id
        return record

    @property
    def record_count(self) -> int:
        """Return the number of non-empty records currently written."""

        return self._existing_count

    def _line_count(self) -> int:
        if not self.history_path.exists():
            return 0
        with self.history_path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def _last_record_event_id(self) -> str | None:
        if not self.history_path.exists():
            return None
        with self.history_path.open("r", encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line.strip()]
        if not lines:
            return None
        payload = json.loads(lines[-1])
        value = payload.get("event_id")
        return str(value).strip() if value is not None else None


def read_transition_history(history_path: Path) -> tuple[RuntimeTransitionRecord, ...]:
    """Load one durable transition-history file."""

    if not history_path.exists():
        return ()
    records: list[RuntimeTransitionRecord] = []
    with history_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            records.append(RuntimeTransitionRecord.model_validate_json(text))
    return tuple(records)


def clear_transition_history(history_path: Path) -> None:
    """Remove one stale transition-history artifact if present."""

    try:
        history_path.unlink()
    except FileNotFoundError:
        return
    except IsADirectoryError as exc:
        raise OSError(f"expected transition-history path to be a file: {history_path}") from exc


__all__ = [
    "AppliedExecutionParameterRebinding",
    "BoundExecutionParameters",
    "clear_transition_history",
    "COMPOUNDING_BUDGET_ATTRIBUTE",
    "COMPOUNDING_PROFILE_ATTRIBUTE",
    "CONTEXT_FACT_INJECTION_ATTRIBUTE",
    "ExecutionParameterRebindingRequest",
    "FrozenPlanIdentity",
    "PROCEDURE_INJECTION_ATTRIBUTE",
    "ROUTING_MODE_ATTRIBUTE",
    "RUNTIME_REBINDABLE_STAGE_FIELDS",
    "latest_policy_transition_record",
    "policy_evaluation_records_from_transitions",
    "RuntimeProvenanceContext",
    "RuntimeTransitionRecord",
    "TRANSITION_HISTORY_SCHEMA_VERSION",
    "TransitionHistoryStore",
    "is_runtime_rebindable_stage_field",
    "read_transition_history",
    "routing_modes_from_records",
    "runtime_stage_parameter_key",
    "runtime_rebindable_stage_fields",
]
