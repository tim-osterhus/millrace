"""Blueprint Planning loop document contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, Field, model_validator

from .base import ContractModel
from .stage_metadata import validate_safe_identifier

BlueprintDraftStatus = Literal[
    "queued",
    "active",
    "candidate_ready",
    "rejected",
    "approved",
    "canceled",
    "blocked",
    "superseded",
]
BlueprintEvaluationDecision = Literal["approved", "rejected", "blocked"]
BlueprintSourceWorkItemKind = Literal["spec", "incident"]


class BlueprintManifestDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["blueprint_manifest"] = "blueprint_manifest"

    manifest_id: str
    root_spec_id: str
    root_idea_id: str
    source_work_item_kind: BlueprintSourceWorkItemKind
    source_work_item_id: str
    source_spec_id: str
    draft_ids: tuple[str, ...] = Field(min_length=1)
    draft_count: int = Field(ge=0)
    strict_sequence: bool = True

    spec_summary: str
    decomposition_strategy: str
    global_acceptance_intent: tuple[str, ...] = Field(min_length=1)
    integration_boundary_notes: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    created_at: datetime
    created_by: Literal["manager_blueprint"] = "manager_blueprint"

    @model_validator(mode="after")
    def validate_manifest(self) -> "BlueprintManifestDocument":
        _validate_identifiers(
            manifest_id=self.manifest_id,
            root_spec_id=self.root_spec_id,
            root_idea_id=self.root_idea_id,
            source_work_item_id=self.source_work_item_id,
            source_spec_id=self.source_spec_id,
        )
        _validate_identifier_list(self.draft_ids, field_name="draft_ids")
        if len(set(self.draft_ids)) != len(self.draft_ids):
            raise ValueError("draft_ids must be unique")
        if self.draft_count != len(self.draft_ids):
            raise ValueError("draft_count must equal len(draft_ids)")
        if not self.strict_sequence:
            raise ValueError("strict_sequence must be true")
        _require_nonempty_text(
            spec_summary=self.spec_summary,
            decomposition_strategy=self.decomposition_strategy,
        )
        _require_nonempty_items(
            self.global_acceptance_intent,
            field_name="global_acceptance_intent",
        )
        return self


class BlueprintDraftDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["blueprint_draft"] = "blueprint_draft"

    draft_id: str
    manifest_id: str
    root_spec_id: str
    root_idea_id: str
    source_spec_id: str
    draft_index: int = Field(
        ge=1,
        validation_alias=AliasChoices("draft_index", "sequence_number"),
    )
    depends_on_draft_ids: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("depends_on_draft_ids", "dependency_draft_ids"),
    )

    title: str
    summary: str = Field(validation_alias=AliasChoices("summary", "scope_summary"))
    scope: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    target_paths: tuple[str, ...] = Field(min_length=1)
    acceptance_intent: tuple[str, ...] = Field(
        min_length=1,
        validation_alias=AliasChoices("acceptance_intent", "acceptance"),
    )
    verification_intent: tuple[str, ...] = ()
    dependency_notes: tuple[str, ...] = ()
    integration_boundary_notes: tuple[str, ...] = ()
    context_excerpt: str

    current_revision: int = Field(
        ge=0,
        validation_alias=AliasChoices("current_revision", "revision"),
    )
    latest_blueprint_id: str | None = None
    latest_critique_id: str | None = None
    status: BlueprintDraftStatus = "queued"
    references: tuple[str, ...] = ()

    created_at: datetime
    created_by: Literal["manager_blueprint"] = "manager_blueprint"
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_draft(self) -> "BlueprintDraftDocument":
        _validate_identifiers(
            draft_id=self.draft_id,
            manifest_id=self.manifest_id,
            root_spec_id=self.root_spec_id,
            root_idea_id=self.root_idea_id,
            source_spec_id=self.source_spec_id,
        )
        if self.latest_blueprint_id is not None:
            validate_safe_identifier(self.latest_blueprint_id, field_name="latest_blueprint_id")
        if self.latest_critique_id is not None:
            validate_safe_identifier(self.latest_critique_id, field_name="latest_critique_id")
        _validate_identifier_list(self.depends_on_draft_ids, field_name="depends_on_draft_ids")
        if self.draft_id in self.depends_on_draft_ids:
            raise ValueError("depends_on_draft_ids cannot include draft_id")
        _require_nonempty_text(
            title=self.title,
            summary=self.summary,
            context_excerpt=self.context_excerpt,
        )
        _require_nonempty_items(self.target_paths, field_name="target_paths")
        _require_nonempty_items(self.acceptance_intent, field_name="acceptance_intent")
        return self


class BlueprintPacketDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["blueprint_packet"] = "blueprint_packet"

    blueprint_id: str = Field(validation_alias=AliasChoices("blueprint_id", "packet_id"))
    draft_id: str
    manifest_id: str
    root_spec_id: str
    root_idea_id: str
    revision: int = Field(ge=1)

    title: str
    implementation_scope: tuple[str, ...] = Field(min_length=1)
    intended_files: tuple[str, ...] = Field(min_length=1)
    design_decisions: tuple[str, ...] = Field(min_length=1)
    non_goals: tuple[str, ...] = ()
    dependency_assumptions: tuple[str, ...] = ()
    verification_plan: tuple[str, ...] = Field(min_length=1)
    task_acceptance: tuple[str, ...] = Field(min_length=1)
    required_checks: tuple[str, ...] = Field(min_length=1)
    risk_notes: tuple[str, ...] = Field(min_length=1)
    open_questions: tuple[str, ...] = ()
    references: tuple[str, ...] = ()

    created_at: datetime
    created_by: Literal["contractor_blueprint"] = "contractor_blueprint"

    @model_validator(mode="after")
    def validate_packet(self) -> "BlueprintPacketDocument":
        _validate_identifiers(
            blueprint_id=self.blueprint_id,
            draft_id=self.draft_id,
            manifest_id=self.manifest_id,
            root_spec_id=self.root_spec_id,
            root_idea_id=self.root_idea_id,
        )
        _require_nonempty_text(title=self.title)
        for field_name in (
            "implementation_scope",
            "intended_files",
            "design_decisions",
            "verification_plan",
            "task_acceptance",
            "required_checks",
            "risk_notes",
        ):
            _require_nonempty_items(getattr(self, field_name), field_name=field_name)
        return self

    def ensure_matches_draft(self, draft: BlueprintDraftDocument) -> None:
        _ensure_equal("draft_id", self.draft_id, draft.draft_id)
        _ensure_equal("manifest_id", self.manifest_id, draft.manifest_id)
        _ensure_equal("root_spec_id", self.root_spec_id, draft.root_spec_id)
        _ensure_equal("root_idea_id", self.root_idea_id, draft.root_idea_id)
        if self.revision != draft.current_revision + 1:
            raise ValueError("revision must equal draft current_revision + 1")


class BlueprintCritiqueDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["blueprint_critique"] = "blueprint_critique"

    critique_id: str
    evaluation_id: str
    blueprint_id: str
    draft_id: str
    manifest_id: str
    root_spec_id: str
    root_idea_id: str
    revision: int = Field(ge=1)

    required_changes: tuple[str, ...] = ()
    scope_issues: tuple[str, ...] = ()
    dependency_issues: tuple[str, ...] = ()
    verification_issues: tuple[str, ...] = ()
    acceptance_issues: tuple[str, ...] = ()
    risk_issues: tuple[str, ...] = ()
    blocking_reason: str
    resolved_by_blueprint_id: str | None = None
    resolved_at: datetime | None = None
    references: tuple[str, ...] = ()

    created_at: datetime
    created_by: Literal["evaluator_blueprint"] = "evaluator_blueprint"

    @model_validator(mode="after")
    def validate_critique(self) -> "BlueprintCritiqueDocument":
        _validate_identifiers(
            critique_id=self.critique_id,
            evaluation_id=self.evaluation_id,
            blueprint_id=self.blueprint_id,
            draft_id=self.draft_id,
            manifest_id=self.manifest_id,
            root_spec_id=self.root_spec_id,
            root_idea_id=self.root_idea_id,
        )
        if self.resolved_by_blueprint_id is not None:
            validate_safe_identifier(
                self.resolved_by_blueprint_id,
                field_name="resolved_by_blueprint_id",
            )
        issue_lists = (
            self.required_changes,
            self.scope_issues,
            self.dependency_issues,
            self.verification_issues,
            self.acceptance_issues,
            self.risk_issues,
        )
        if not any(issue_lists):
            raise ValueError("at least one issue list is required")
        for field_name in (
            "required_changes",
            "scope_issues",
            "dependency_issues",
            "verification_issues",
            "acceptance_issues",
            "risk_issues",
        ):
            _validate_nonempty_entries(getattr(self, field_name), field_name=field_name)
        _require_nonempty_text(blocking_reason=self.blocking_reason)
        has_resolution_id = self.resolved_by_blueprint_id is not None
        has_resolution_time = self.resolved_at is not None
        if has_resolution_id != has_resolution_time:
            raise ValueError("resolved_by_blueprint_id and resolved_at must be set together")
        return self

    def ensure_matches_packet(self, packet: BlueprintPacketDocument) -> None:
        _ensure_equal("blueprint_id", self.blueprint_id, packet.blueprint_id)
        _ensure_equal("draft_id", self.draft_id, packet.draft_id)
        _ensure_equal("manifest_id", self.manifest_id, packet.manifest_id)
        _ensure_equal("root_spec_id", self.root_spec_id, packet.root_spec_id)
        _ensure_equal("root_idea_id", self.root_idea_id, packet.root_idea_id)
        _ensure_equal("revision", self.revision, packet.revision)


class BlueprintEvaluationDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["blueprint_evaluation"] = "blueprint_evaluation"

    evaluation_id: str
    blueprint_id: str
    draft_id: str
    manifest_id: str
    root_spec_id: str
    root_idea_id: str
    decision: BlueprintEvaluationDecision

    rubric_findings: tuple[str, ...] = Field(min_length=1)
    lineage_consistency_findings: tuple[str, ...] = ()
    dependency_findings: tuple[str, ...] = ()
    verification_findings: tuple[str, ...] = ()
    overlap_findings: tuple[str, ...] = ()
    required_task_fields: tuple[str, ...] = ()
    critique_id: str | None = None
    references: tuple[str, ...] = ()

    created_at: datetime
    created_by: Literal["evaluator_blueprint"] = "evaluator_blueprint"

    @model_validator(mode="after")
    def validate_evaluation(self) -> "BlueprintEvaluationDocument":
        _validate_identifiers(
            evaluation_id=self.evaluation_id,
            blueprint_id=self.blueprint_id,
            draft_id=self.draft_id,
            manifest_id=self.manifest_id,
            root_spec_id=self.root_spec_id,
            root_idea_id=self.root_idea_id,
        )
        if self.critique_id is not None:
            validate_safe_identifier(self.critique_id, field_name="critique_id")
        _require_nonempty_items(self.rubric_findings, field_name="rubric_findings")
        if self.decision == "approved":
            _require_nonempty_items(
                self.required_task_fields,
                field_name="required_task_fields",
            )
        if self.decision == "rejected" and self.critique_id is None:
            raise ValueError("rejected evaluations require critique_id")
        return self

    def ensure_matches_packet(self, packet: BlueprintPacketDocument) -> None:
        _ensure_equal("blueprint_id", self.blueprint_id, packet.blueprint_id)
        _ensure_equal("draft_id", self.draft_id, packet.draft_id)
        _ensure_equal("manifest_id", self.manifest_id, packet.manifest_id)
        _ensure_equal("root_spec_id", self.root_spec_id, packet.root_spec_id)
        _ensure_equal("root_idea_id", self.root_idea_id, packet.root_idea_id)


class BlueprintPromotionRecord(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["blueprint_promotion"] = "blueprint_promotion"

    promotion_id: str
    blueprint_id: str
    evaluation_id: str
    draft_id: str
    manifest_id: str
    root_spec_id: str
    root_idea_id: str
    generated_task_id: str
    generated_task_path: str
    approved_blueprint_path: str
    evaluation_path: str
    promoted_at: datetime
    promoted_by: Literal["runtime"] = "runtime"

    @model_validator(mode="after")
    def validate_promotion(self) -> "BlueprintPromotionRecord":
        _validate_identifiers(
            promotion_id=self.promotion_id,
            blueprint_id=self.blueprint_id,
            evaluation_id=self.evaluation_id,
            draft_id=self.draft_id,
            manifest_id=self.manifest_id,
            root_spec_id=self.root_spec_id,
            root_idea_id=self.root_idea_id,
            generated_task_id=self.generated_task_id,
        )
        _require_path_contains(
            self.generated_task_path,
            field_name="generated_task_path",
            expected="/tasks/queue/",
        )
        _require_path_contains(
            self.approved_blueprint_path,
            field_name="approved_blueprint_path",
            expected="/blueprints/packets/approved/",
        )
        _require_path_contains(
            self.evaluation_path,
            field_name="evaluation_path",
            expected="/blueprints/evaluations/",
        )
        if self.generated_task_id not in self.generated_task_path:
            raise ValueError("generated_task_path must reference generated_task_id")
        if self.blueprint_id not in self.approved_blueprint_path:
            raise ValueError("approved_blueprint_path must reference blueprint_id")
        if self.evaluation_id not in self.evaluation_path:
            raise ValueError("evaluation_path must reference evaluation_id")
        return self

    def ensure_matches_evaluation(self, evaluation: BlueprintEvaluationDocument) -> None:
        _ensure_equal("blueprint_id", self.blueprint_id, evaluation.blueprint_id)
        _ensure_equal("evaluation_id", self.evaluation_id, evaluation.evaluation_id)
        _ensure_equal("draft_id", self.draft_id, evaluation.draft_id)
        _ensure_equal("manifest_id", self.manifest_id, evaluation.manifest_id)
        _ensure_equal("root_spec_id", self.root_spec_id, evaluation.root_spec_id)
        _ensure_equal("root_idea_id", self.root_idea_id, evaluation.root_idea_id)


def _validate_identifiers(**values: str) -> None:
    for field_name, value in values.items():
        validate_safe_identifier(value, field_name=field_name)


def _validate_identifier_list(values: tuple[str, ...], *, field_name: str) -> None:
    for value in values:
        validate_safe_identifier(value, field_name=field_name)


def _require_nonempty_text(**values: str) -> None:
    for field_name, value in values.items():
        if not value.strip():
            raise ValueError(f"{field_name} is required")


def _require_nonempty_items(values: tuple[str, ...], *, field_name: str) -> None:
    if not values:
        raise ValueError(f"{field_name} is required")
    _validate_nonempty_entries(values, field_name=field_name)


def _validate_nonempty_entries(values: tuple[str, ...], *, field_name: str) -> None:
    for value in values:
        if not value.strip():
            raise ValueError(f"{field_name} entries must not be empty")


def _require_path_contains(value: str, *, field_name: str, expected: str) -> None:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    comparable = f"/{normalized.lstrip('/')}"
    if expected not in comparable:
        raise ValueError(f"{field_name} must include {expected.strip('/')}")


def _ensure_equal(field_name: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise ValueError(f"{field_name} mismatch")


__all__ = [
    "BlueprintCritiqueDocument",
    "BlueprintDraftDocument",
    "BlueprintDraftStatus",
    "BlueprintEvaluationDecision",
    "BlueprintEvaluationDocument",
    "BlueprintManifestDocument",
    "BlueprintPacketDocument",
    "BlueprintPromotionRecord",
    "BlueprintSourceWorkItemKind",
]
