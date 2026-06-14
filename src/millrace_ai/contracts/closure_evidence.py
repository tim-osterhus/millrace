"""Closure evidence freshness artifact contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from .base import ContractModel
from .enums import Plane, StageName


class CriterionEvidenceProvenance(str, Enum):
    FRESH = "fresh"
    REVALIDATED = "revalidated"
    HISTORICAL_ONLY = "historical_only"
    MISSING = "missing"


class StaleEvidencePolicy(ContractModel):
    policy_id: Literal["closure_evidence_freshness_v1"] = "closure_evidence_freshness_v1"
    current_decision_provenance: tuple[CriterionEvidenceProvenance, ...] = (
        CriterionEvidenceProvenance.FRESH,
        CriterionEvidenceProvenance.REVALIDATED,
    )
    historical_context_provenance: tuple[CriterionEvidenceProvenance, ...] = (
        CriterionEvidenceProvenance.HISTORICAL_ONLY,
    )
    missing_evidence_provenance: CriterionEvidenceProvenance = CriterionEvidenceProvenance.MISSING
    requires_revalidation_after_watermark: bool = True


class PreviousArbiterEvidence(ContractModel):
    run_id: str | None = None
    request_id: str | None = None
    verdict_path: str | None = None
    report_path: str | None = None
    completed_at: datetime | None = None


class LineageRunEvidence(ContractModel):
    run_id: str
    request_id: str | None = None
    plane: Plane
    stage: StageName
    work_item_family_id: str | None = None
    work_item_id: str | None = None
    terminal_result: str | None = None
    completed_at: datetime
    stage_result_path: str | None = None


class ClosureEvidenceWindow(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["closure_evidence_window"] = "closure_evidence_window"

    root_spec_id: str
    current_arbiter_run_id: str
    current_arbiter_request_id: str
    previous_arbiter: PreviousArbiterEvidence = Field(default_factory=PreviousArbiterEvidence)
    freshness_watermark_at: datetime | None = None
    stale_evidence_policy: StaleEvidencePolicy = Field(default_factory=StaleEvidencePolicy)
    completed_lineage_evidence: tuple[LineageRunEvidence, ...] = ()


__all__ = [
    "ClosureEvidenceWindow",
    "CriterionEvidenceProvenance",
    "LineageRunEvidence",
    "PreviousArbiterEvidence",
    "StaleEvidencePolicy",
]
