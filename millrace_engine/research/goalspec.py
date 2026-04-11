"""Executable GoalSpec stage helpers for Goal Intake through Spec Review."""

from __future__ import annotations

from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator

from ..contracts import ContractModel, SpecInterviewPolicy, _normalize_datetime
from .goalspec_helpers import (
    GoalSpecExecutionError,
    _normalize_decomposition_profile,
    _normalize_path_token,
    _normalize_required_text,
    resolve_goal_source,
)
from .goalspec_scope_diagnostics import ScopeDivergenceRecord, ScopeSurfaceDiagnostic
from .goalspec_semantic_profile import GoalSemanticProfile
from .governance_models import InitialFamilyPolicyPinDecision
from .specs import (
    GoalSpecDecompositionProfile,
)
from .state import ResearchQueueFamily, ResearchQueueOwnership

GOALSPEC_ARTIFACT_SCHEMA_VERSION = "1.0"
ContractorSpecificityLevel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]
ContractorShapeClass = Literal[
    "platform_extension",
    "interactive_application",
    "network_application",
    "service_backend",
    "automation_tool",
    "library_framework",
    "data_system",
    "content_system",
    "unknown",
]
ContractorFallbackMode = Literal[
    "apply_resolved_profiles_only",
    "conservative_shape_only",
    "abstain_unknown",
]
GoalSpecSpecializationProvenance = Literal[
    "source_requested",
    "workspace_grounded",
    "contractor_resolved",
    "planner_invented",
]
GoalSpecSpecializationSupportState = Literal["supported", "unsupported", "proposed"]


def _normalize_model_sequence(value: object, *, field_name: str) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, dict):
        return (value,)
    if isinstance(value, str):
        raise ValueError(f"{field_name} must be a sequence of objects")
    return tuple(value)


class GoalSource(ContractModel):
    """Normalized source metadata for one GoalSpec intake artifact."""

    current_artifact_path: str
    current_artifact_relative_path: str
    canonical_source_path: str
    canonical_relative_source_path: str
    source_path: str
    relative_source_path: str
    queue_family: ResearchQueueFamily = ResearchQueueFamily.GOALSPEC
    idea_id: str
    title: str
    decomposition_profile: GoalSpecDecompositionProfile = "simple"
    frontmatter: dict[str, str] = {}
    body: str
    canonical_body: str
    checksum_sha256: str

    @model_validator(mode="before")
    @classmethod
    def migrate_lineage_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        current_path = str(payload.get("current_artifact_path") or payload.get("source_path") or "").strip()
        current_relative_path = str(
            payload.get("current_artifact_relative_path") or payload.get("relative_source_path") or ""
        ).strip()
        canonical_path = str(payload.get("canonical_source_path") or current_path).strip()
        canonical_relative_path = str(
            payload.get("canonical_relative_source_path") or current_relative_path or canonical_path
        ).strip()
        payload.setdefault("source_path", current_path)
        payload.setdefault("relative_source_path", current_relative_path)
        payload.setdefault("current_artifact_path", current_path)
        payload.setdefault("current_artifact_relative_path", current_relative_path)
        payload.setdefault("canonical_source_path", canonical_path)
        payload.setdefault("canonical_relative_source_path", canonical_relative_path)
        payload.setdefault("canonical_body", payload.get("body") or "")
        return payload

    @field_validator(
        "current_artifact_path",
        "current_artifact_relative_path",
        "canonical_source_path",
        "canonical_relative_source_path",
        "source_path",
        "relative_source_path",
        "idea_id",
        "title",
        "checksum_sha256",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("body", "canonical_body")
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
    canonical_source_path: str
    current_artifact_path: str
    source_path: str
    archived_source_path: str = ""
    research_brief_path: str
    idea_id: str
    title: str
    decomposition_profile: GoalSpecDecompositionProfile = "simple"
    source_checksum_sha256: str

    @model_validator(mode="before")
    @classmethod
    def migrate_lineage_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        canonical_source_path = str(
            payload.get("canonical_source_path")
            or payload.get("archived_source_path")
            or payload.get("source_path")
            or ""
        ).strip()
        current_artifact_path = str(
            payload.get("current_artifact_path") or payload.get("research_brief_path") or ""
        ).strip()
        payload.setdefault("canonical_source_path", canonical_source_path)
        payload.setdefault("current_artifact_path", current_artifact_path)
        return payload

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "canonical_source_path",
        "current_artifact_path",
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
    canonical_source_path: str
    current_artifact_path: str
    source_path: str
    research_brief_path: str
    semantic_profile: GoalSemanticProfile
    milestones: tuple[str, ...]
    hard_blockers: tuple[str, ...]

    @model_validator(mode="before")
    @classmethod
    def migrate_lineage_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        canonical_source_path = str(payload.get("canonical_source_path") or payload.get("source_path") or "").strip()
        current_artifact_path = str(
            payload.get("current_artifact_path") or payload.get("research_brief_path") or ""
        ).strip()
        payload.setdefault("canonical_source_path", canonical_source_path)
        payload.setdefault("current_artifact_path", current_artifact_path)
        return payload

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "profile_id",
        "goal_id",
        "title",
        "run_id",
        "canonical_source_path",
        "current_artifact_path",
        "source_path",
        "research_brief_path",
    )
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
    canonical_source_path: str
    current_artifact_path: str
    source_path: str
    research_brief_path: str
    profile_path: str
    profile_markdown_path: str
    report_path: str
    goal_intake_record_path: str
    contractor_record_path: str = ""
    contractor_profile_path: str = ""
    contractor_report_path: str = ""
    contractor_schema_path: str = ""
    contractor_specificity_level: ContractorSpecificityLevel | None = None
    contractor_shape_class: ContractorShapeClass | None = None
    contractor_fallback_mode: ContractorFallbackMode | None = None
    contractor_specialization_provenance: tuple[GoalSpecSpecializationRecord, ...] = ()
    initial_family_policy_pin: InitialFamilyPolicyPinDecision | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_lineage_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        canonical_source_path = str(payload.get("canonical_source_path") or payload.get("source_path") or "").strip()
        current_artifact_path = str(
            payload.get("current_artifact_path") or payload.get("research_brief_path") or ""
        ).strip()
        payload.setdefault("canonical_source_path", canonical_source_path)
        payload.setdefault("current_artifact_path", current_artifact_path)
        return payload

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "contractor_record_path",
        "contractor_profile_path",
        "contractor_report_path",
        "contractor_schema_path",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value) if isinstance(value, Path) else ("" if value is None else str(value).strip())

    @field_validator("contractor_specialization_provenance", mode="before")
    @classmethod
    def normalize_specialization_provenance(
        cls, value: object
    ) -> tuple[GoalSpecSpecializationRecord | dict[str, object], ...]:
        return _normalize_model_sequence(value, field_name="contractor_specialization_provenance")

    @field_validator(
        "profile_id",
        "goal_id",
        "title",
        "run_id",
        "canonical_source_path",
        "current_artifact_path",
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
    canonical_source_path: str
    current_artifact_path: str
    source_path: str
    research_brief_path: str
    profile_state_path: str
    profile_path: str
    profile_markdown_path: str
    report_path: str
    contractor_record_path: str = ""
    contractor_profile_path: str = ""
    contractor_report_path: str = ""
    contractor_schema_path: str = ""
    contractor_specificity_level: ContractorSpecificityLevel | None = None
    contractor_shape_class: ContractorShapeClass | None = None
    contractor_fallback_mode: ContractorFallbackMode | None = None
    contractor_specialization_provenance: tuple[GoalSpecSpecializationRecord, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def migrate_lineage_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        canonical_source_path = str(payload.get("canonical_source_path") or payload.get("source_path") or "").strip()
        current_artifact_path = str(
            payload.get("current_artifact_path") or payload.get("research_brief_path") or ""
        ).strip()
        payload.setdefault("canonical_source_path", canonical_source_path)
        payload.setdefault("current_artifact_path", current_artifact_path)
        return payload

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "contractor_record_path",
        "contractor_profile_path",
        "contractor_report_path",
        "contractor_schema_path",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value) if isinstance(value, Path) else ("" if value is None else str(value).strip())

    @field_validator("contractor_specialization_provenance", mode="before")
    @classmethod
    def normalize_specialization_provenance(
        cls, value: object
    ) -> tuple[GoalSpecSpecializationRecord | dict[str, object], ...]:
        return _normalize_model_sequence(value, field_name="contractor_specialization_provenance")

    @field_validator(
        "run_id",
        "goal_id",
        "title",
        "canonical_source_path",
        "current_artifact_path",
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
    contractor_record_path: str = ""
    queue_ownership: ResearchQueueOwnership


class GoalSpecSpecializationRecord(ContractModel):
    """Typed specialization provenance carried through GoalSpec runtime state."""

    key: str
    value: str
    provenance: GoalSpecSpecializationProvenance
    support_state: GoalSpecSpecializationSupportState
    evidence_path: str = ""
    evidence: tuple[str, ...] = ()
    notes: str = ""

    @field_validator("key", "value")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("evidence_path", "notes", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value) if isinstance(value, Path) else ("" if value is None else str(value).strip())

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            items = (value,)
        else:
            items = tuple(value)
        normalized: list[str] = []
        for item in items:
            text = str(item).strip()
            if not text:
                raise ValueError("evidence may not contain blank values")
            normalized.append(text)
        return tuple(normalized)


class ContractorClassificationCandidate(ContractModel):
    """One scored candidate label produced during Contractor classification."""

    label: str
    score: float

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="label")


class ContractorClassificationPayload(ContractModel):
    """Typed Contractor layered-classification payload."""

    shape_class: ContractorShapeClass
    archetype: str
    host_platform: str
    stack_hints: tuple[str, ...]
    specializations: dict[str, str]

    @field_validator("archetype", "host_platform", mode="before")
    @classmethod
    def normalize_scalar_fields(cls, value: str | None) -> str:
        return "" if value is None else str(value).strip()

    @field_validator("stack_hints", mode="before")
    @classmethod
    def normalize_stack_hints(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            items = (value,)
        else:
            items = tuple(value)
        normalized: list[str] = []
        for item in items:
            hint = str(item).strip()
            if hint:
                normalized.append(hint)
        return tuple(normalized)

    @field_validator("specializations", mode="before")
    @classmethod
    def normalize_specializations(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("specializations must be a JSON object")
        normalized: dict[str, str] = {}
        for raw_key, raw_item in value.items():
            key = str(raw_key).strip()
            item = str(raw_item).strip()
            if not key:
                raise ValueError("specializations may not contain blank keys")
            if not item:
                raise ValueError(f"specializations[{key!r}] may not be blank")
            normalized[key] = item
        return normalized


class ContractorProfileArtifact(ContractModel):
    """Validated authoritative Contractor profile artifact."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["contractor_profile"] = "contractor_profile"
    goal_id: str
    run_id: str
    updated_at: datetime
    source_path: str
    canonical_source_path: str
    current_artifact_path: str
    profile_report_path: str = ""
    specificity_level: ContractorSpecificityLevel
    shape_class: ContractorShapeClass
    classification: ContractorClassificationPayload
    candidate_classifications: tuple[ContractorClassificationCandidate, ...] = ()
    confidence: float
    fallback_mode: ContractorFallbackMode
    resolved_profile_ids: tuple[str, ...]
    unresolved_specializations: tuple[str, ...]
    specialization_provenance: tuple[GoalSpecSpecializationRecord, ...] = ()
    capability_hints: tuple[str, ...]
    environment_hints: tuple[str, ...]
    browse_used: bool
    browse_notes: str = ""
    evidence: tuple[str, ...]
    abstentions: tuple[str, ...]
    contradictions: tuple[str, ...]
    notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def migrate_lineage_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        canonical_source_path = str(payload.get("canonical_source_path") or payload.get("source_path") or "").strip()
        current_artifact_path = str(payload.get("current_artifact_path") or payload.get("source_path") or "").strip()
        payload.setdefault("canonical_source_path", canonical_source_path)
        payload.setdefault("current_artifact_path", current_artifact_path)
        return payload

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "goal_id",
        "run_id",
        "source_path",
        "canonical_source_path",
        "current_artifact_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("profile_report_path", "browse_notes", "notes", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value) if isinstance(value, Path) else ("" if value is None else str(value).strip())

    @field_validator("specialization_provenance", mode="before")
    @classmethod
    def normalize_specialization_provenance(
        cls, value: object
    ) -> tuple[GoalSpecSpecializationRecord | dict[str, object], ...]:
        return _normalize_model_sequence(value, field_name="specialization_provenance")

    @field_validator(
        "resolved_profile_ids",
        "unresolved_specializations",
        "capability_hints",
        "environment_hints",
        "evidence",
        "abstentions",
        "contradictions",
        mode="before",
    )
    @classmethod
    def normalize_text_sequence(cls, value: object, info: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            items = (value,)
        else:
            items = tuple(value)
        normalized: list[str] = []
        field_name = getattr(info, "field_name", "value")
        for item in items:
            text = str(item).strip()
            if not text:
                raise ValueError(f"{field_name} may not contain blank values")
            normalized.append(text)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_consistency(self) -> "ContractorProfileArtifact":
        if self.classification.shape_class != self.shape_class:
            raise ValueError("classification.shape_class must match shape_class")
        if not self.evidence:
            raise ValueError("evidence must contain at least one item")
        unresolved_tokens = set(self.unresolved_specializations)
        for item in self.specialization_provenance:
            token = f"{item.key}={item.value}"
            if item.support_state == "supported" and token in unresolved_tokens:
                raise ValueError(
                    "specialization_provenance may not mark an unresolved specialization as supported"
                )
        return self


class ContractorExecutionRecord(ContractModel):
    """Durable runtime record for one Contractor execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["contractor_execution"] = "contractor_execution"
    run_id: str
    emitted_at: datetime
    goal_id: str
    title: str
    canonical_source_path: str
    current_artifact_path: str
    source_path: str
    source_checksum_sha256: str = ""
    canonical_source_checksum_sha256: str = ""
    research_brief_path: str
    profile_path: str
    report_path: str
    schema_path: str
    record_path: str = ""
    profile_specificity_level: ContractorSpecificityLevel
    shape_class: ContractorShapeClass
    fallback_mode: ContractorFallbackMode
    specialization_provenance: tuple[GoalSpecSpecializationRecord, ...] = ()
    browse_used: bool

    @model_validator(mode="before")
    @classmethod
    def migrate_lineage_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        canonical_source_path = str(payload.get("canonical_source_path") or payload.get("source_path") or "").strip()
        current_artifact_path = str(
            payload.get("current_artifact_path") or payload.get("research_brief_path") or ""
        ).strip()
        payload.setdefault("canonical_source_path", canonical_source_path)
        payload.setdefault("current_artifact_path", current_artifact_path)
        return payload

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "goal_id",
        "title",
        "canonical_source_path",
        "current_artifact_path",
        "source_path",
        "research_brief_path",
        "profile_path",
        "report_path",
        "schema_path",
        "record_path",
        mode="before",
    )
    @classmethod
    def normalize_required_paths_and_text(cls, value: str | Path, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if field_name.endswith("_path"):
            return _normalize_required_text(_normalize_path_token(value), field_name=field_name)
        return _normalize_required_text(str(value), field_name=field_name)

    @field_validator("specialization_provenance", mode="before")
    @classmethod
    def normalize_specialization_provenance(
        cls, value: object
    ) -> tuple[GoalSpecSpecializationRecord | dict[str, object], ...]:
        return _normalize_model_sequence(value, field_name="specialization_provenance")

    @field_validator("source_checksum_sha256", mode="before")
    @classmethod
    def normalize_source_checksum_sha256(cls, value: str | None) -> str:
        return "" if value is None else str(value).strip()

    @field_validator("canonical_source_checksum_sha256", mode="before")
    @classmethod
    def normalize_canonical_source_checksum_sha256(cls, value: str | None) -> str:
        return "" if value is None else str(value).strip()


class ContractorExecutionResult(ContractModel):
    """Resolved outputs from one Contractor execution."""

    record_path: str
    profile_path: str
    report_path: str
    schema_path: str
    profile: ContractorProfileArtifact


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


class CompletionManifestDraftSurface(ContractModel):
    """One product implementation or verification surface captured in the manifest."""

    surface_kind: str
    path: str
    purpose: str

    @field_validator("surface_kind", "path", "purpose")
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
    canonical_source_path: str
    current_artifact_path: str
    source_path: str
    research_brief_path: str
    objective_profile_state_path: str
    objective_profile_path: str
    completion_manifest_plan_path: str
    goal_intake_record_path: str
    planning_profile: str
    contractor_profile_path: str = ""
    contractor_specificity_level: str = ""
    contractor_shape_class: str = ""
    contractor_fallback_mode: str = ""
    acceptance_focus: tuple[str, ...]
    open_questions: tuple[str, ...]
    contractor_capability_hints: tuple[str, ...] = ()
    contractor_environment_hints: tuple[str, ...] = ()
    contractor_unresolved_specializations: tuple[str, ...] = ()
    contractor_specialization_provenance: tuple[GoalSpecSpecializationRecord, ...] = ()
    contractor_abstentions: tuple[str, ...] = ()
    contractor_contradictions: tuple[str, ...] = ()
    required_artifacts: tuple[CompletionManifestDraftArtifact, ...]
    implementation_surfaces: tuple[CompletionManifestDraftSurface, ...]
    verification_surfaces: tuple[CompletionManifestDraftSurface, ...]

    @model_validator(mode="before")
    @classmethod
    def migrate_lineage_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        canonical_source_path = str(payload.get("canonical_source_path") or payload.get("source_path") or "").strip()
        current_artifact_path = str(
            payload.get("current_artifact_path") or payload.get("research_brief_path") or ""
        ).strip()
        legacy_repo_kind = str(payload.pop("repo_kind", "") or "").strip()
        planning_profile = str(payload.get("planning_profile") or "").strip()
        if not planning_profile and legacy_repo_kind:
            planning_profile = "framework_runtime" if legacy_repo_kind == "millrace_python_runtime" else "generic_product"
        payload.setdefault("canonical_source_path", canonical_source_path)
        payload.setdefault("current_artifact_path", current_artifact_path)
        payload.setdefault("planning_profile", planning_profile)
        return payload

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "draft_id",
        "goal_id",
        "title",
        "run_id",
        "canonical_source_path",
        "current_artifact_path",
        "source_path",
        "research_brief_path",
        "objective_profile_state_path",
        "objective_profile_path",
        "completion_manifest_plan_path",
        "goal_intake_record_path",
        "planning_profile",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator(
        "contractor_profile_path",
        "contractor_specificity_level",
        "contractor_shape_class",
        "contractor_fallback_mode",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value) if isinstance(value, Path) else ("" if value is None else str(value).strip())

    @field_validator(
        "contractor_specialization_provenance",
        mode="before",
    )
    @classmethod
    def normalize_specialization_provenance(
        cls, value: object
    ) -> tuple[GoalSpecSpecializationRecord | dict[str, object], ...]:
        return _normalize_model_sequence(value, field_name="contractor_specialization_provenance")

    @field_validator(
        "acceptance_focus",
        "open_questions",
        "contractor_capability_hints",
        "contractor_environment_hints",
        "contractor_unresolved_specializations",
        "contractor_abstentions",
        "contractor_contradictions",
        mode="before",
    )
    @classmethod
    def normalize_text_sequence(cls, value: object, info: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            items = (value,)
        else:
            items = tuple(value)
        normalized: list[str] = []
        field_name = getattr(info, "field_name", "value")
        for item in items:
            text = str(item).strip()
            if not text:
                raise ValueError(f"{field_name} may not contain blank values")
            normalized.append(text)
        return tuple(normalized)


class CompletionManifestDraftRecord(ContractModel):
    """Per-run runtime record for one completion-manifest drafting execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["completion_manifest_draft_record"] = "completion_manifest_draft_record"
    run_id: str
    emitted_at: datetime
    goal_id: str
    title: str
    canonical_source_path: str
    current_artifact_path: str
    source_path: str
    research_brief_path: str
    draft_path: str
    report_path: str
    objective_profile_path: str

    @model_validator(mode="before")
    @classmethod
    def migrate_lineage_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        canonical_source_path = str(payload.get("canonical_source_path") or payload.get("source_path") or "").strip()
        current_artifact_path = str(
            payload.get("current_artifact_path") or payload.get("research_brief_path") or ""
        ).strip()
        payload.setdefault("canonical_source_path", canonical_source_path)
        payload.setdefault("current_artifact_path", current_artifact_path)
        return payload

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "goal_id",
        "title",
        "canonical_source_path",
        "current_artifact_path",
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
    canonical_source_path: str
    current_artifact_path: str
    source_path: str
    research_brief_path: str
    objective_profile_path: str
    completion_manifest_path: str
    queue_spec_path: str
    golden_spec_path: str
    phase_spec_path: str
    decision_path: str
    family_state_path: str

    @model_validator(mode="before")
    @classmethod
    def migrate_lineage_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        canonical_source_path = str(payload.get("canonical_source_path") or payload.get("source_path") or "").strip()
        current_artifact_path = str(
            payload.get("current_artifact_path") or payload.get("research_brief_path") or ""
        ).strip()
        payload.setdefault("canonical_source_path", canonical_source_path)
        payload.setdefault("current_artifact_path", current_artifact_path)
        return payload

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "goal_id",
        "spec_id",
        "title",
        "canonical_source_path",
        "current_artifact_path",
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


def _goalspec_stage_support_module():
    return import_module(".goalspec_stage_support", __package__)


def research_stage_for_node(plan, node_id):
    return _goalspec_stage_support_module().research_stage_for_node(plan, node_id)


def next_stage_for_success(plan, node_id):
    return _goalspec_stage_support_module().next_stage_for_success(plan, node_id)


def execute_goal_intake(paths, checkpoint, *, run_id, emitted_at=None):
    return _goalspec_stage_support_module().execute_goal_intake(
        paths,
        checkpoint,
        run_id=run_id,
        emitted_at=emitted_at,
    )


def execute_objective_profile_sync(paths, checkpoint, *, run_id, emitted_at=None):
    return _goalspec_stage_support_module().execute_objective_profile_sync(
        paths,
        checkpoint,
        run_id=run_id,
        emitted_at=emitted_at,
    )


def execute_completion_manifest_draft(paths, checkpoint, *, run_id, emitted_at=None):
    return _goalspec_stage_support_module().execute_completion_manifest_draft(
        paths,
        checkpoint,
        run_id=run_id,
        emitted_at=emitted_at,
    )


def execute_spec_synthesis(
    paths,
    checkpoint,
    *,
    run_id,
    completion_manifest=None,
    emitted_at=None,
):
    return _goalspec_stage_support_module().execute_spec_synthesis(
        paths,
        checkpoint,
        run_id=run_id,
        completion_manifest=completion_manifest,
        emitted_at=emitted_at,
    )


def execute_spec_interview(paths, checkpoint, *, run_id, policy, emitted_at=None):
    return _goalspec_stage_support_module().execute_spec_interview(
        paths,
        checkpoint,
        run_id=run_id,
        policy=policy,
        emitted_at=emitted_at,
    )


def execute_spec_review(paths, checkpoint, *, run_id, emitted_at=None):
    return _goalspec_stage_support_module().execute_spec_review(
        paths,
        checkpoint,
        run_id=run_id,
        emitted_at=emitted_at,
    )


__all__ = [
    "AcceptanceProfileRecord",
    "ContractorClassificationCandidate",
    "ContractorClassificationPayload",
    "ContractorExecutionRecord",
    "ContractorExecutionResult",
    "ContractorProfileArtifact",
    "CompletionManifestDraftArtifact",
    "CompletionManifestDraftSurface",
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
