"""Typed work-document contracts."""

from __future__ import annotations

from datetime import datetime
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
    StageName,
    TaskStatusHint,
)
from .stage_metadata import stage_plane, validate_safe_identifier


class TaskDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["task"] = "task"

    task_id: str
    title: str
    summary: str = ""

    root_idea_id: str | None = None
    root_spec_id: str | None = None
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

    source_type: Literal["idea", "incident", "manual", "derived_spec"]
    source_id: str | None = None
    parent_spec_id: str | None = None
    root_idea_id: str | None = None
    root_spec_id: str | None = None

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
        if self.source_id is not None:
            validate_safe_identifier(self.source_id, field_name="source_id")
        if self.parent_spec_id is not None:
            validate_safe_identifier(self.parent_spec_id, field_name="parent_spec_id")
        return self


class IncidentDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["incident"] = "incident"

    incident_id: str
    title: str
    summary: str

    root_idea_id: str | None = None
    root_spec_id: str | None = None
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
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_stage_plane_alignment(self) -> "IncidentDocument":
        validate_safe_identifier(self.incident_id, field_name="incident_id")
        if self.root_idea_id is not None:
            validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.root_spec_id is not None:
            validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
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


class ClosureTargetState(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["closure_target_state"] = "closure_target_state"

    root_spec_id: str
    root_idea_id: str
    root_spec_path: str
    root_idea_path: str
    rubric_path: str
    latest_verdict_path: str | None = None
    latest_report_path: str | None = None
    closure_open: bool = True
    closure_blocked_by_lineage_work: bool = False
    blocking_work_ids: tuple[str, ...] = ()
    opened_at: datetime
    closed_at: datetime | None = None
    last_arbiter_run_id: str | None = None

    @model_validator(mode="after")
    def validate_target_state(self) -> "ClosureTargetState":
        validate_safe_identifier(self.root_spec_id, field_name="root_spec_id")
        validate_safe_identifier(self.root_idea_id, field_name="root_idea_id")
        if self.last_arbiter_run_id is not None:
            validate_safe_identifier(self.last_arbiter_run_id, field_name="last_arbiter_run_id")
        for work_item_id in self.blocking_work_ids:
            validate_safe_identifier(work_item_id, field_name="blocking_work_ids")
        if self.closed_at is not None and self.closed_at < self.opened_at:
            raise ValueError("closed_at cannot precede opened_at")
        if self.closed_at is not None and self.closure_open:
            raise ValueError("closed closure target cannot remain open")
        if self.blocking_work_ids and not self.closure_blocked_by_lineage_work:
            raise ValueError("blocking_work_ids require closure_blocked_by_lineage_work=true")
        return self


__all__ = [
    "ClosureTargetState",
    "IncidentDocument",
    "LearningRequestDocument",
    "SpecDocument",
    "TaskDocument",
]
