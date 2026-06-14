"""Typed work-document contracts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .base import ContractModel
from .enums import (
    IncidentDecision,
    IncidentSeverity,
    IncidentStatusHint,
    LearningRequestAction,
    LearningStageName,
    Plane,
    ProbeStatusHint,
    RootIntakeKind,
    StageName,
    TaskStatusHint,
    WorkItemKind,
)
from .stage_metadata import stage_plane, validate_safe_identifier
from .work_refs import coerce_family_and_kind, normalize_work_item_family_id


class TaskDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["task"] = "task"

    task_id: str
    title: str
    summary: str = ""

    root_idea_id: str | None = None
    root_spec_id: str | None = None
    root_intake_kind: RootIntakeKind | None = None
    root_intake_id: str | None = None
    spec_id: str | None = None
    parent_task_id: str | None = None
    incident_id: str | None = None

    target_paths: tuple[str, ...] = Field(min_length=1)
    acceptance: tuple[str, ...] = Field(min_length=1)
    required_checks: tuple[str, ...] = Field(min_length=1)
    references: tuple[str, ...] = Field(min_length=1)
    risk: tuple[str, ...] = Field(min_length=1)

    depends_on: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    status_hint: TaskStatusHint | None = None
    created_at: datetime
    created_by: str
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_identifier_shape(self) -> "TaskDocument":
        validate_safe_identifier(self.task_id, field_name="task_id")
        if self.root_idea_id is not None:
            validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.root_spec_id is not None:
            validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
        if self.root_intake_id is not None:
            validate_safe_identifier(self.root_intake_id, field_name="root_intake_id")
        if self.spec_id is not None:
            validate_safe_identifier(self.spec_id, field_name="spec_id")
        if self.parent_task_id is not None:
            validate_safe_identifier(self.parent_task_id, field_name="parent_task_id")
        if self.incident_id is not None:
            validate_safe_identifier(self.incident_id, field_name="incident_id")
        return self


class SpecDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["spec"] = "spec"

    spec_id: str
    title: str
    summary: str

    source_type: Literal["idea", "incident", "manual", "derived_spec", "probe"]
    source_id: str | None = None
    parent_spec_id: str | None = None
    root_idea_id: str | None = None
    root_spec_id: str | None = None
    root_intake_kind: RootIntakeKind | None = None
    root_intake_id: str | None = None

    goals: tuple[str, ...] = Field(min_length=1)
    non_goals: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    constraints: tuple[str, ...] = Field(min_length=1)
    assumptions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    target_paths: tuple[str, ...] = ()
    entrypoints: tuple[str, ...] = ()
    required_skills: tuple[str, ...] = ()

    decomposition_hints: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = Field(min_length=1)
    references: tuple[str, ...] = Field(min_length=1)

    created_at: datetime
    created_by: str
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_identifier_shape(self) -> "SpecDocument":
        validate_safe_identifier(self.spec_id, field_name="spec_id")
        if self.root_idea_id is not None:
            validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.root_spec_id is not None:
            validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
        if self.root_intake_id is not None:
            validate_safe_identifier(self.root_intake_id, field_name="root_intake_id")
        if self.source_id is not None:
            validate_safe_identifier(self.source_id, field_name="source_id")
        if self.parent_spec_id is not None:
            validate_safe_identifier(self.parent_spec_id, field_name="parent_spec_id")
        return self


PlannerDispositionValue = Literal[
    "active_source_ready_for_manager",
    "emitted_child_specs",
    "blocked",
]
PlannerDispositionSourceFamily = Literal["spec", "incident"]


class PlannerDispositionDocument(ContractModel):
    schema_version: Literal["1.0"]
    kind: Literal["planner_disposition"]

    source_work_item_family_id: PlannerDispositionSourceFamily
    source_work_item_id: str
    disposition: PlannerDispositionValue
    emitted_spec_ids: tuple[str, ...]
    refined_active_source: bool
    recommended_next_action: str
    created_at: datetime
    created_by: Literal["planner"]

    @model_validator(mode="after")
    def validate_disposition(self) -> "PlannerDispositionDocument":
        validate_safe_identifier(
            self.source_work_item_id,
            field_name="source_work_item_id",
        )
        for spec_id in self.emitted_spec_ids:
            validate_safe_identifier(spec_id, field_name="emitted_spec_ids")
        if len(set(self.emitted_spec_ids)) != len(self.emitted_spec_ids):
            raise ValueError("emitted_spec_ids must be unique")
        if not self.recommended_next_action.strip():
            raise ValueError("recommended_next_action is required")
        if self.disposition == "emitted_child_specs":
            if not self.emitted_spec_ids:
                raise ValueError("emitted_child_specs requires emitted_spec_ids")
        elif self.emitted_spec_ids:
            raise ValueError("emitted_spec_ids are only valid for emitted_child_specs")
        return self


class ProbeDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["probe"] = "probe"

    probe_id: str
    title: str
    summary: str
    request: str

    target_paths: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    status_hint: ProbeStatusHint | None = None
    created_at: datetime
    created_by: str
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_identifier_shape(self) -> "ProbeDocument":
        validate_safe_identifier(self.probe_id, field_name="probe_id")
        if not self.request.strip():
            raise ValueError("request is required")
        return self


class IncidentDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["incident"] = "incident"

    incident_id: str
    title: str
    summary: str

    root_idea_id: str | None = None
    root_spec_id: str | None = None
    root_intake_kind: RootIntakeKind | None = None
    root_intake_id: str | None = None
    source_task_id: str | None = None
    source_spec_id: str | None = None
    source_stage: StageName
    source_plane: Plane

    failure_class: str
    status_hint: IncidentStatusHint | None = None
    severity: IncidentSeverity = IncidentSeverity.MEDIUM
    needs_planning: bool = True

    trigger_reason: str
    observed_symptoms: tuple[str, ...] = ()
    failed_attempts: tuple[str, ...] = ()
    consultant_decision: IncidentDecision

    evidence_paths: tuple[str, ...] = ()
    related_run_ids: tuple[str, ...] = ()
    related_stage_results: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    opened_at: datetime
    opened_by: str
    trigger_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    created_by: str | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_stage_plane_alignment(self) -> "IncidentDocument":
        validate_safe_identifier(self.incident_id, field_name="incident_id")
        if self.root_idea_id is not None:
            validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.root_spec_id is not None:
            validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
        if self.root_intake_id is not None:
            validate_safe_identifier(self.root_intake_id, field_name="root_intake_id")
        if self.source_task_id is not None:
            validate_safe_identifier(self.source_task_id, field_name="source_task_id")
        if self.source_spec_id is not None:
            validate_safe_identifier(self.source_spec_id, field_name="source_spec_id")
        if stage_plane(self.source_stage) != self.source_plane:
            raise ValueError("source_stage must belong to source_plane")
        return self


class LearningRequestDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["learning_request"] = "learning_request"

    learning_request_id: str
    title: str
    summary: str = ""

    requested_action: LearningRequestAction
    target_skill_id: str | None = None
    target_stage: LearningStageName | None = None
    source_refs: tuple[str, ...] = ()
    preferred_output_paths: tuple[str, ...] = ()
    trigger_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    originating_run_ids: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    created_at: datetime
    created_by: str
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_identifier_shape(self) -> "LearningRequestDocument":
        validate_safe_identifier(self.learning_request_id, field_name="learning_request_id")
        if self.target_skill_id is not None:
            validate_safe_identifier(self.target_skill_id, field_name="target_skill_id")
        for run_id in self.originating_run_ids:
            validate_safe_identifier(run_id, field_name="originating_run_ids")
        return self


ClosureBlockingWorkRefType = str
ClosureRootSourceKind = str


class ClosureBlockingWorkRef(ContractModel):
    blocker_type: ClosureBlockingWorkRefType
    reason: str
    work_item_family_id: str | None = None
    work_item_kind: WorkItemKind | None = None
    work_item_id: str | None = None
    state: str | None = None
    root_spec_id: str | None = None
    root_idea_id: str | None = None
    artifact_path: str | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_ref(self) -> "ClosureBlockingWorkRef":
        validate_safe_identifier(self.reason, field_name="reason")
        family_id, work_item_kind = coerce_family_and_kind(
            family_id=self.work_item_family_id,
            work_item_kind=self.work_item_kind,
        )
        if family_id is None and self.work_item_family_id is not None:
            family_id = normalize_work_item_family_id(self.work_item_family_id)
        self.work_item_family_id = family_id
        self.work_item_kind = work_item_kind
        if self.work_item_id is not None:
            validate_safe_identifier(self.work_item_id, field_name="work_item_id")
        if self.state is not None:
            validate_safe_identifier(self.state, field_name="state")
        if self.root_spec_id is not None:
            validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
        if self.root_idea_id is not None:
            validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.work_item_id is None and self.artifact_path is None:
            raise ValueError("closure blocker refs require work_item_id or artifact_path")
        return self


class ClosureRootSource(ContractModel):
    kind: ClosureRootSourceKind
    id: str
    path: str
    title: str | None = None
    summary: str | None = None
    intake_kind: RootIntakeKind | None = None
    intake_id: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> "ClosureRootSource":
        validate_safe_identifier(self.kind, field_name="root_source.kind")
        validate_safe_identifier(self.id, field_name="root_source.id")
        if self.intake_id is not None:
            validate_safe_identifier(self.intake_id, field_name="root_source.intake_id")
        self.path = _validate_workspace_relative_path(
            self.path,
            field_name="root_source.path",
        )
        return self


class ClosureTargetState(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["closure_target_state"] = "closure_target_state"

    root_spec_id: str
    root_source: ClosureRootSource
    root_idea_id: str | None = None
    root_intake_kind: RootIntakeKind | None = None
    root_intake_id: str | None = None
    root_spec_path: str
    root_idea_path: str | None = None
    rubric_path: str
    latest_verdict_path: str | None = None
    latest_report_path: str | None = None
    closure_open: bool = True
    closure_blocked_by_lineage_work: bool = False
    blocking_work_ids: tuple[str, ...] = ()
    blocking_work_refs: tuple[ClosureBlockingWorkRef, ...] = ()
    opened_at: datetime
    closed_at: datetime | None = None
    last_arbiter_run_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_target_payload(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        root_source = payload.get("root_source")
        root_idea_id = payload.get("root_idea_id")
        root_idea_path = payload.get("root_idea_path")
        if root_source is None and isinstance(root_idea_id, str) and isinstance(root_idea_path, str):
            payload["root_source"] = {
                "kind": "idea",
                "id": root_idea_id,
                "path": root_idea_path,
                "intake_kind": payload.get("root_intake_kind"),
                "intake_id": payload.get("root_intake_id"),
            }
        elif isinstance(root_source, dict):
            if payload.get("root_intake_kind") is None and root_source.get("intake_kind") is not None:
                payload["root_intake_kind"] = root_source.get("intake_kind")
            if payload.get("root_intake_id") is None and root_source.get("intake_id") is not None:
                payload["root_intake_id"] = root_source.get("intake_id")
            if root_source.get("kind") == "idea":
                payload.setdefault("root_idea_id", root_source.get("id"))
                payload.setdefault("root_idea_path", root_source.get("path"))

        raw_ids = tuple(str(item) for item in payload.get("blocking_work_ids") or ())
        safe_ids: list[str] = []
        for raw_id in raw_ids:
            if _is_safe_identifier(raw_id):
                safe_ids.append(raw_id.strip())
        payload["blocking_work_ids"] = tuple(dict.fromkeys(safe_ids))
        return payload

    @model_validator(mode="after")
    def validate_target_state(self) -> "ClosureTargetState":
        validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
        self.root_spec_path = _validate_workspace_relative_path(
            self.root_spec_path,
            field_name="root_spec_path",
        )
        self.rubric_path = _validate_workspace_relative_path(
            self.rubric_path,
            field_name="rubric_path",
        )
        if self.latest_verdict_path is not None:
            self.latest_verdict_path = _validate_workspace_relative_path(
                self.latest_verdict_path,
                field_name="latest_verdict_path",
            )
        if self.latest_report_path is not None:
            self.latest_report_path = _validate_workspace_relative_path(
                self.latest_report_path,
                field_name="latest_report_path",
            )
        if self.root_idea_path is not None:
            self.root_idea_path = _validate_workspace_relative_path(
                self.root_idea_path,
                field_name="root_idea_path",
            )
        if self.root_idea_id is not None:
            validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.root_source.kind == "idea":
            if self.root_idea_id is None:
                self.root_idea_id = self.root_source.id
            if self.root_idea_path is None:
                self.root_idea_path = self.root_source.path
        if self.root_intake_id is not None:
            validate_safe_identifier(self.root_intake_id, field_name="root_intake_id")
        if self.root_source.intake_kind is not None and self.root_intake_kind is None:
            self.root_intake_kind = self.root_source.intake_kind
        if self.root_source.intake_id is not None and self.root_intake_id is None:
            self.root_intake_id = self.root_source.intake_id
        if self.last_arbiter_run_id is not None:
            validate_safe_identifier(self.last_arbiter_run_id, field_name="last_arbiter_run_id")
        for work_item_id in self.blocking_work_ids:
            validate_safe_identifier(work_item_id, field_name="blocking_work_ids")
        if self.closed_at is not None and self.closed_at < self.opened_at:
            raise ValueError("closed_at cannot precede opened_at")
        if self.closed_at is not None and self.closure_open:
            raise ValueError("closed closure target cannot remain open")
        if (
            self.blocking_work_ids or self.blocking_work_refs
        ) and not self.closure_blocked_by_lineage_work:
            raise ValueError("blocking work requires closure_blocked_by_lineage_work=true")
        return self


def _validate_workspace_relative_path(value: str, *, field_name: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        raise ValueError(f"{field_name} is required")
    candidate = Path(normalized)
    if candidate.is_absolute():
        raise ValueError(f"{field_name} must be workspace-relative")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"{field_name} must not contain empty or parent traversal parts")
    return normalized


def _is_safe_identifier(value: str) -> bool:
    try:
        validate_safe_identifier(value, field_name="blocking_work_ids")
    except ValueError:
        return False
    return True


__all__ = [
    "ClosureBlockingWorkRef",
    "ClosureRootSource",
    "ClosureRootSourceKind",
    "ClosureTargetState",
    "IncidentDocument",
    "LearningRequestDocument",
    "PlannerDispositionDocument",
    "PlannerDispositionSourceFamily",
    "PlannerDispositionValue",
    "ProbeDocument",
    "SpecDocument",
    "TaskDocument",
]
