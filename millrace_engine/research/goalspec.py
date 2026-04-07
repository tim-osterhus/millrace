"""Executable GoalSpec stage helpers for Goal Intake through Spec Review."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import field_validator

from ..contracts import ContractModel, SpecInterviewPolicy, _normalize_datetime
from .goalspec_helpers import (
    GoalSpecExecutionError,
    _normalize_decomposition_profile,
    _normalize_path_token,
    _normalize_required_text,
    resolve_goal_source,
)
from .goalspec_semantic_profile import GoalSemanticProfile
from .goalspec_scope_diagnostics import ScopeDivergenceRecord, ScopeSurfaceDiagnostic
from .governance import InitialFamilyPolicyPinDecision
from .specs import (
    GoalSpecDecompositionProfile,
)
from .state import ResearchQueueFamily, ResearchQueueOwnership


GOALSPEC_ARTIFACT_SCHEMA_VERSION = "1.0"


class GoalSource(ContractModel):
    """Normalized source metadata for one GoalSpec intake artifact."""

    source_path: str
    relative_source_path: str
    queue_family: ResearchQueueFamily = ResearchQueueFamily.GOALSPEC
    idea_id: str
    title: str
    decomposition_profile: GoalSpecDecompositionProfile = "simple"
    frontmatter: dict[str, str] = {}
    body: str
    checksum_sha256: str

    @field_validator("source_path", "relative_source_path", "idea_id", "title", "checksum_sha256")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("body may not be empty")
        return normalized


class GoalIntakeRecord(ContractModel):
    """Durable runtime record for one Goal Intake execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["goal_intake"] = "goal_intake"
    run_id: str
    emitted_at: datetime
    source_path: str
    archived_source_path: str = ""
    research_brief_path: str
    idea_id: str
    title: str
    decomposition_profile: GoalSpecDecompositionProfile = "simple"
    source_checksum_sha256: str

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "source_path",
        "research_brief_path",
        "idea_id",
        "title",
        "source_checksum_sha256",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("archived_source_path", mode="before")
    @classmethod
    def normalize_archived_source_path(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)


class AcceptanceProfileRecord(ContractModel):
    """Machine-readable acceptance profile emitted by objective sync."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    profile_id: str
    goal_id: str
    title: str
    run_id: str
    updated_at: datetime
    source_path: str
    research_brief_path: str
    semantic_profile: GoalSemanticProfile
    milestones: tuple[str, ...]
    hard_blockers: tuple[str, ...]

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("profile_id", "goal_id", "title", "run_id", "source_path", "research_brief_path")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class ObjectiveProfileSyncStateRecord(ContractModel):
    """Canonical current objective-profile sync state."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    profile_id: str
    goal_id: str
    title: str
    run_id: str
    updated_at: datetime
    source_path: str
    research_brief_path: str
    profile_path: str
    profile_markdown_path: str
    report_path: str
    goal_intake_record_path: str
    initial_family_policy_pin: InitialFamilyPolicyPinDecision | None = None

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "profile_id",
        "goal_id",
        "title",
        "run_id",
        "source_path",
        "research_brief_path",
        "profile_path",
        "profile_markdown_path",
        "report_path",
        "goal_intake_record_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class ObjectiveProfileSyncRecord(ContractModel):
    """Durable runtime record for one objective-profile sync execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["objective_profile_sync"] = "objective_profile_sync"
    run_id: str
    emitted_at: datetime
    goal_id: str
    title: str
    source_path: str
    research_brief_path: str
    profile_state_path: str
    profile_path: str
    profile_markdown_path: str
    report_path: str

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "goal_id",
        "title",
        "source_path",
        "research_brief_path",
        "profile_state_path",
        "profile_path",
        "profile_markdown_path",
        "report_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class GoalIntakeExecutionResult(ContractModel):
    """Resolved outputs from one Goal Intake execution."""

    record_path: str
    archived_source_path: str = ""
    research_brief_path: str
    queue_ownership: ResearchQueueOwnership


class ObjectiveProfileSyncExecutionResult(ContractModel):
    """Resolved outputs from one Objective Profile Sync execution."""

    record_path: str
    profile_state_path: str
    queue_ownership: ResearchQueueOwnership


class CompletionManifestDraftArtifact(ContractModel):
    """One planned artifact captured in the completion-manifest draft."""

    artifact_kind: str
    path: str
    purpose: str

    @field_validator("artifact_kind", "path", "purpose")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class CompletionManifestDraftStateRecord(ContractModel):
    """Canonical completion-manifest draft state for the current GoalSpec source."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["completion_manifest_draft"] = "completion_manifest_draft"
    draft_id: str
    goal_id: str
    title: str
    run_id: str
    updated_at: datetime
    source_path: str
    research_brief_path: str
    objective_profile_state_path: str
    objective_profile_path: str
    completion_manifest_plan_path: str
    goal_intake_record_path: str
    acceptance_focus: tuple[str, ...]
    open_questions: tuple[str, ...]
    required_outputs: tuple[CompletionManifestDraftArtifact, ...]

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "draft_id",
        "goal_id",
        "title",
        "run_id",
        "source_path",
        "research_brief_path",
        "objective_profile_state_path",
        "objective_profile_path",
        "completion_manifest_plan_path",
        "goal_intake_record_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class CompletionManifestDraftRecord(ContractModel):
    """Per-run runtime record for one completion-manifest drafting execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["completion_manifest_draft_record"] = "completion_manifest_draft_record"
    run_id: str
    emitted_at: datetime
    goal_id: str
    title: str
    source_path: str
    research_brief_path: str
    draft_path: str
    report_path: str
    objective_profile_path: str

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "goal_id",
        "title",
        "source_path",
        "research_brief_path",
        "draft_path",
        "report_path",
        "objective_profile_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class CompletionManifestDraftExecutionResult(ContractModel):
    """Resolved outputs from one internal completion-manifest drafting execution."""

    record_path: str
    draft_path: str
    report_path: str
    objective_profile_path: str
    draft_state: CompletionManifestDraftStateRecord


class SpecSynthesisRecord(ContractModel):
    """Per-run runtime record for one Spec Synthesis execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["spec_synthesis"] = "spec_synthesis"
    run_id: str
    emitted_at: datetime
    goal_id: str
    spec_id: str
    title: str
    source_path: str
    research_brief_path: str
    objective_profile_path: str
    completion_manifest_path: str
    queue_spec_path: str
    golden_spec_path: str
    phase_spec_path: str
    decision_path: str
    family_state_path: str

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "goal_id",
        "spec_id",
        "title",
        "source_path",
        "research_brief_path",
        "objective_profile_path",
        "completion_manifest_path",
        "queue_spec_path",
        "golden_spec_path",
        "phase_spec_path",
        "decision_path",
        "family_state_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class SpecSynthesisExecutionResult(ContractModel):
    """Resolved outputs from one Spec Synthesis execution."""

    record_path: str
    queue_spec_path: str
    golden_spec_path: str
    phase_spec_path: str
    decision_path: str
    family_state_path: str
    queue_ownership: ResearchQueueOwnership


class SpecInterviewRecord(ContractModel):
    """Per-run runtime record for one Spec Interview execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["spec_interview"] = "spec_interview"
    run_id: str
    emitted_at: datetime
    spec_id: str
    title: str
    source_path: str
    question_path: str = ""
    decision_path: str = ""
    policy: SpecInterviewPolicy
    resolution: Literal["skipped", "repo_answered", "waiting_for_operator", "operator_resolved"]
    blocking: bool = False

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("run_id", "spec_id", "title", "source_path")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("question_path", "decision_path", mode="before")
    @classmethod
    def normalize_optional_paths(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)


class SpecInterviewExecutionResult(ContractModel):
    """Resolved outputs from one Spec Interview execution."""

    record_path: str = ""
    question_path: str = ""
    decision_path: str = ""
    blocked: bool = False
    queue_ownership: ResearchQueueOwnership


class SpecReviewExecutionResult(ContractModel):
    """Resolved outputs from one Spec Review execution."""

    record_path: str
    questions_path: str
    decision_path: str
    reviewed_path: str
    lineage_path: str
    stable_registry_path: str
    family_state_path: str
    queue_ownership: ResearchQueueOwnership

from .goalspec_stage_support import (
    execute_completion_manifest_draft,
    execute_goal_intake,
    execute_objective_profile_sync,
    execute_spec_interview,
    execute_spec_review,
    execute_spec_synthesis,
    next_stage_for_success,
    research_stage_for_node,
)


__all__ = [
    "AcceptanceProfileRecord",
    "CompletionManifestDraftArtifact",
    "CompletionManifestDraftExecutionResult",
    "CompletionManifestDraftRecord",
    "CompletionManifestDraftStateRecord",
    "GOALSPEC_ARTIFACT_SCHEMA_VERSION",
    "GoalIntakeExecutionResult",
    "GoalIntakeRecord",
    "GoalSource",
    "GoalSpecExecutionError",
    "ObjectiveProfileSyncExecutionResult",
    "ObjectiveProfileSyncRecord",
    "ObjectiveProfileSyncStateRecord",
    "SpecInterviewExecutionResult",
    "SpecInterviewRecord",
    "ScopeDivergenceRecord",
    "ScopeSurfaceDiagnostic",
    "SpecSynthesisExecutionResult",
    "SpecSynthesisRecord",
    "execute_spec_interview",
    "SpecReviewExecutionResult",
    "execute_completion_manifest_draft",
    "execute_goal_intake",
    "execute_objective_profile_sync",
    "execute_spec_review",
    "execute_spec_synthesis",
    "next_stage_for_success",
    "research_stage_for_node",
    "resolve_goal_source",
]
