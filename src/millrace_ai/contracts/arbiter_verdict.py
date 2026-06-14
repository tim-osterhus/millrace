"""Arbiter verdict artifact contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, ConfigDict, Field, JsonValue, model_validator

from .base import ContractModel
from .closure_evidence import CriterionEvidenceProvenance
from .enums import ResultClass
from .terminal_outcomes import TerminalResult


class ArbiterCriterionEvidence(ContractModel):
    model_config = ConfigDict(extra="allow")

    criterion_id: str = Field(validation_alias=AliasChoices("criterion_id", "id"))
    provenance: CriterionEvidenceProvenance
    decision_relevant: bool | None = None
    criterion_role: Literal["deciding", "context"] | None = None
    status: str | None = None
    title: str | None = None
    evidence_depth: str | None = None
    evidence: tuple[str, ...] = Field(
        default_factory=tuple,
        validation_alias=AliasChoices("evidence", "evidence_refs"),
    )
    summary: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def contributes_to_decision(self) -> bool:
        if self.decision_relevant is not None:
            return self.decision_relevant
        return self.criterion_role != "context"


class ArbiterVerdict(ContractModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["arbiter_verdict"] = "arbiter_verdict"

    root_spec_id: str | None = None
    root_idea_id: str | None = None
    request_id: str | None = None
    run_id: str | None = None
    decided_at: datetime | None = None
    status: str
    terminal_result: TerminalResult | None = None
    result_class: ResultClass | None = None
    rubric_path: str | None = None
    report_path: str | None = None
    remediation_incident_path: str | None = None
    summary: str | None = None
    criteria: tuple[ArbiterCriterionEvidence, ...] = ()
    checks: tuple[JsonValue, ...] = ()
    parity_gaps: tuple[JsonValue, ...] = ()
    remediation_guidance: tuple[JsonValue, ...] | None = None
    residual_uncertainty: tuple[str, ...] | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_evidence_aliases(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "criteria" not in payload:
            for alias in ("criterion_evidence", "criteria_evidence"):
                candidate = payload.get(alias)
                if candidate is not None:
                    payload["criteria"] = candidate
                    break
        payload.pop("criterion_evidence", None)
        payload.pop("criteria_evidence", None)
        return payload

    @property
    def decision_provenance(self) -> tuple[CriterionEvidenceProvenance, ...]:
        return tuple(
            criterion.provenance
            for criterion in self.criteria
            if criterion.contributes_to_decision
        )


__all__ = ["ArbiterCriterionEvidence", "ArbiterVerdict"]
