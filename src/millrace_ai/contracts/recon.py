"""Recon packet contracts for probe intake routing."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel
from .stage_metadata import validate_safe_identifier


class ReconDecision(str, Enum):
    TO_EXECUTION = "to_execution"
    TO_PLANNING = "to_planning"
    BLOCKED = "blocked"
    NOOP = "noop"


class ReconConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReconRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReconPathFinding(ContractModel):
    path: str
    reason: str

    @model_validator(mode="after")
    def validate_shape(self) -> "ReconPathFinding":
        if not self.path.strip():
            raise ValueError("path is required")
        if not self.reason.strip():
            raise ValueError("reason is required")
        return self


class ReconVerificationPlan(ContractModel):
    required_commands: tuple[str, ...] = ()
    focused_checks: tuple[str, ...] = ()
    fallback_checks: tuple[str, ...] = ()


class ReconPacketDocument(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["recon_packet"] = "recon_packet"

    recon_packet_id: str
    probe_id: str
    decision: ReconDecision
    confidence: ReconConfidence
    risk_level: ReconRiskLevel

    request_summary: str
    interpreted_goal: str
    relevant_paths: tuple[ReconPathFinding, ...] = Field(min_length=1)
    relevant_symbols: tuple[str, ...] = ()
    relevant_tests: tuple[ReconPathFinding, ...] = ()
    semantic_invariants: tuple[str, ...] = Field(min_length=1)
    edge_cases_to_preserve: tuple[str, ...] = ()
    verification_plan: ReconVerificationPlan = Field(default_factory=ReconVerificationPlan)
    open_questions: tuple[str, ...] = ()
    handoff_target: Literal["execution", "planning", "blocked", "noop"]
    emitted_task_id: str | None = None
    emitted_spec_id: str | None = None

    created_at: datetime
    created_by: Literal["recon"] = "recon"

    @model_validator(mode="after")
    def validate_packet_shape(self) -> "ReconPacketDocument":
        validate_safe_identifier(self.recon_packet_id, field_name="recon_packet_id")
        validate_safe_identifier(self.probe_id, field_name="probe_id")
        if self.emitted_task_id is not None:
            validate_safe_identifier(self.emitted_task_id, field_name="emitted_task_id")
        if self.emitted_spec_id is not None:
            validate_safe_identifier(self.emitted_spec_id, field_name="emitted_spec_id")

        expected_handoff = {
            ReconDecision.TO_EXECUTION: "execution",
            ReconDecision.TO_PLANNING: "planning",
            ReconDecision.BLOCKED: "blocked",
            ReconDecision.NOOP: "noop",
        }[self.decision]
        if self.handoff_target != expected_handoff:
            raise ValueError("handoff_target must match decision")
        if self.decision is ReconDecision.TO_EXECUTION and self.emitted_task_id is None:
            raise ValueError("to_execution decisions require Emitted-Task-ID")
        if self.decision is not ReconDecision.TO_EXECUTION and self.emitted_task_id is not None:
            if self.decision is ReconDecision.TO_PLANNING:
                raise ValueError(
                    "to_planning decisions require Emitted-Spec-ID; "
                    "Emitted-Task-ID is only valid for to_execution decisions"
                )
            raise ValueError("Emitted-Task-ID is only valid for to_execution decisions")
        if self.decision is ReconDecision.TO_PLANNING and self.emitted_spec_id is None:
            raise ValueError("to_planning decisions require Emitted-Spec-ID")
        if self.decision is not ReconDecision.TO_PLANNING and self.emitted_spec_id is not None:
            if self.decision is ReconDecision.TO_EXECUTION:
                raise ValueError(
                    "to_execution decisions require Emitted-Task-ID; "
                    "Emitted-Spec-ID is only valid for to_planning decisions"
                )
            raise ValueError("Emitted-Spec-ID is only valid for to_planning decisions")
        return self


__all__ = [
    "ReconConfidence",
    "ReconDecision",
    "ReconPacketDocument",
    "ReconPathFinding",
    "ReconRiskLevel",
    "ReconVerificationPlan",
]
