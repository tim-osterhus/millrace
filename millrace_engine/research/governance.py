"""Governance-core helpers for GoalSpec family and idempotency guards."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
import json

from pydantic import Field, field_validator, model_validator

from ..contracts import ContractModel, TaskCard
from ..markdown import TaskStoreDocument, parse_task_store, render_task_store, write_text_atomic
from ..paths import RuntimePaths
from ..queue import load_research_recovery_latch
from .specs import (
    FrozenInitialFamilySpecPlan,
    GoalSpecFamilyGovernorState,
    GoalSpecFamilySpecState,
    GoalSpecFamilyState,
)

if TYPE_CHECKING:
    from .goalspec import SpecSynthesisRecord


GOVERNANCE_REPORT_SCHEMA_VERSION = "1.0"
DEFAULT_PINNED_FAMILY_POLICY_FIELDS = (
    "family_cap_mode",
    "initial_family_max_specs",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_token_sequence(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return tuple(deduped)


def _relative_path(path: Path, *, relative_to: Path) -> str:
    try:
        return path.relative_to(relative_to).as_posix()
    except ValueError:
        return path.as_posix()


def _normalize_cap(value: object, *, minimum: int = 0) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = minimum
    return max(minimum, normalized)


def _normalize_optional_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.strip().split())


def _normalize_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    return default


def _normalize_scalar(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return tuple(_normalize_scalar(item) for item in value)
    if isinstance(value, dict):
        return {
            str(key).strip(): _normalize_scalar(item)
            for key, item in value.items()
            if str(key).strip()
        }
    return value


def _load_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.as_posix()} must contain a JSON object")
    return payload


def _sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _file_sha256_or_none(path: Path) -> str | None:
    if not path.exists():
        return None
    return _sha256_text(path.read_text(encoding="utf-8"))


def _json_scalar_map(values: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key).strip()
        if not key:
            continue
        normalized[key] = _normalize_scalar(raw_value)
    return normalized


def _normalize_datetime_or_none(value: datetime | str | None) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GoalSpecGovernanceError(RuntimeError):
    """Raised when GoalSpec governance cannot admit the requested mutation."""


class InitialFamilyPlanGuardDecision(ContractModel):
    """Explainable result for the initial-family governance guard."""

    action: Literal["allow", "freeze", "validate", "block"]
    reason: str
    frozen: bool = False
    applied_family_max_specs: int = Field(default=0, ge=0)
    proposed_spec_count: int = Field(default=0, ge=0)
    added_spec_ids: tuple[str, ...] = ()
    removed_spec_ids: tuple[str, ...] = ()
    mutated_spec_ids: tuple[str, ...] = ()
    violation_codes: tuple[str, ...] = ()

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("added_spec_ids", "removed_spec_ids", "mutated_spec_ids", "violation_codes", mode="before")
    @classmethod
    def normalize_sequences(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))


class SpecSynthesisIdempotencyDecision(ContractModel):
    """Explainable reuse decision for spec-synthesis restart safety."""

    action: Literal["reuse", "rewrite"]
    reason: str
    mismatch_fields: tuple[str, ...] = ()

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("mismatch_fields", mode="before")
    @classmethod
    def normalize_mismatch_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))


class InitialFamilyPolicyPinDecision(ContractModel):
    """Explainable queue-governor decision for frozen-family policy pinning."""

    active: bool = False
    action: Literal["none", "pin"] = "none"
    reason: str
    pinned_fields: tuple[str, ...] = ()
    family_policy_path: str = ""
    spec_family_state_path: str = ""

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("pinned_fields", mode="before")
    @classmethod
    def normalize_pinned_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))

    @field_validator("family_policy_path", "spec_family_state_path", mode="before")
    @classmethod
    def normalize_optional_paths(cls, value: str | Path | None) -> str:
        if isinstance(value, Path):
            return value.as_posix()
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_active_fields(self) -> "InitialFamilyPolicyPinDecision":
        if self.active and self.action != "pin":
            raise ValueError("active policy pin decisions must use action=pin")
        if self.active and not self.pinned_fields:
            raise ValueError("active policy pin decisions must record pinned_fields")
        if self.action == "none" and self.active:
            raise ValueError("inactive action mismatch")
        return self


class QueueGovernorReport(ContractModel):
    """Operator-facing queue-governor outcome for frozen-family policy pinning."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    updated_at: datetime | None = None
    goal_id: str = ""
    report_path: str = ""
    status: Literal["not_applicable", "compliant", "pinned"] = "not_applicable"
    reason: str
    initial_family_policy_pin: InitialFamilyPolicyPinDecision | None = None

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("goal_id", "report_path", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | Path | None) -> str:
        if isinstance(value, Path):
            return value.as_posix()
        return _normalize_optional_text(value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")


class DriftControlPolicy(ContractModel):
    """Typed drift-detector policy for frozen-family policy drift."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    watched_family_policy_fields: tuple[str, ...] = DEFAULT_PINNED_FAMILY_POLICY_FIELDS
    hard_latch_on_policy_drift: bool = False

    @field_validator("watched_family_policy_fields", mode="before")
    @classmethod
    def normalize_watched_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return DEFAULT_PINNED_FAMILY_POLICY_FIELDS
        normalized = _normalize_token_sequence(tuple(str(item) for item in value))
        return normalized or DEFAULT_PINNED_FAMILY_POLICY_FIELDS

    @field_validator("hard_latch_on_policy_drift", mode="before")
    @classmethod
    def normalize_hard_latch(cls, value: object) -> bool:
        return _normalize_bool(value, default=False)


class DriftStatusReport(ContractModel):
    """Operator-facing drift-detector status for frozen-family policy drift."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    report_path: str = ""
    policy_path: str = ""
    family_policy_path: str = ""
    spec_family_state_path: str = ""
    status: Literal["not_applicable", "missing_policy", "clear", "warning", "hard_latch"] = "not_applicable"
    reason: str
    watched_fields: tuple[str, ...] = ()
    drift_fields: tuple[str, ...] = ()
    warning_active: bool = False
    hard_latch_active: bool = False

    @field_validator("report_path", "policy_path", "family_policy_path", "spec_family_state_path", mode="before")
    @classmethod
    def normalize_path_fields(cls, value: str | Path | None) -> str:
        if isinstance(value, Path):
            return value.as_posix()
        return _normalize_optional_text(value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("watched_fields", "drift_fields", mode="before")
    @classmethod
    def normalize_field_sequences(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))


class GovernanceCanaryReport(ContractModel):
    """Operator-facing comparison of current vs baseline drift-control policy."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    report_path: str = ""
    baseline_policy_path: str = ""
    current_policy_path: str = ""
    status: Literal["not_configured", "baseline_missing", "match", "drifted"] = "not_configured"
    reason: str
    changed_fields: tuple[str, ...] = ()

    @field_validator("report_path", "baseline_policy_path", "current_policy_path", mode="before")
    @classmethod
    def normalize_path_fields(cls, value: str | Path | None) -> str:
        if isinstance(value, Path):
            return value.as_posix()
        return _normalize_optional_text(value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="reason")

    @field_validator("changed_fields", mode="before")
    @classmethod
    def normalize_changed_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        return _normalize_token_sequence(tuple(str(item) for item in value))


class ResearchGovernanceReport(ContractModel):
    """Aggregated governance visibility for research/operator reporting."""

    queue_governor: QueueGovernorReport
    governance_canary: GovernanceCanaryReport
    drift: DriftStatusReport
    progress_watchdog: "ProgressWatchdogReport"


class RecoveryTaskRegenerationReport(ContractModel):
    """Bounded recovery-task regeneration visibility for one frozen batch."""

    status: Literal["not_applicable", "not_needed", "regenerated", "manual_only"] = "not_applicable"
    reason: str
    decision_type: Literal["regenerated_backlog_work", "durable_remediation_decision", ""] = ""
    remediation_spec_id: str = ""
    remediation_record_path: str = ""
    taskaudit_record_path: str = ""
    task_provenance_path: str = ""
    lineage_path: str = ""
    visible_task_count_before: int = Field(default=0, ge=0)
    visible_task_count_after: int = Field(default=0, ge=0)
    regenerated_task_id: str = ""
    regenerated_task_title: str = ""
    family_policy_path: str = ""
    spec_family_state_path: str = ""
    family_policy_history_preserved: bool | None = None
    family_policy_history_reason: str = ""

    @field_validator(
        "reason",
        "remediation_spec_id",
        "remediation_record_path",
        "taskaudit_record_path",
        "task_provenance_path",
        "lineage_path",
        "regenerated_task_id",
        "regenerated_task_title",
        "family_policy_path",
        "spec_family_state_path",
        "family_policy_history_reason",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if isinstance(value, Path):
            return value.as_posix()
        text = _normalize_optional_text(value)
        if field_name == "reason" and not text:
            raise ValueError("reason may not be empty")
        return text


class ProgressWatchdogReport(ContractModel):
    """Explainable progress-watchdog view over the recovery-latch seam."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    updated_at: datetime | None = None
    report_path: str = ""
    state_path: str = ""
    latch_path: str = ""
    status: Literal["not_active", "waiting", "ready", "regenerated", "stalled"] = "not_active"
    reason: str
    batch_id: str = ""
    recovery_decision_type: Literal["regenerated_backlog_work", "durable_remediation_decision", ""] = ""
    remediation_spec_id: str = ""
    visible_recovery_task_count: int = Field(default=0, ge=0)
    expected_pending_card_count: int = Field(default=0, ge=0)
    expected_backlog_card_count: int = Field(default=0, ge=0)
    escalation_action: Literal["none", "monitor", "regenerate_task", "manual_review"] = "none"
    recovery_regeneration: RecoveryTaskRegenerationReport | None = None

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator(
        "report_path",
        "state_path",
        "latch_path",
        "reason",
        "batch_id",
        "remediation_spec_id",
        mode="before",
    )
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if isinstance(value, Path):
            return value.as_posix()
        text = _normalize_optional_text(value)
        if field_name == "reason" and not text:
            raise ValueError("reason may not be empty")
        return text


class ProgressWatchdogState(ContractModel):
    """Persisted progress-watchdog state written by the engine."""

    schema_version: Literal["1.0"] = GOVERNANCE_REPORT_SCHEMA_VERSION
    updated_at: datetime | None = None
    batch_id: str = ""
    status: Literal["not_active", "waiting", "ready", "regenerated", "stalled"] = "not_active"
    reason: str
    remediation_spec_id: str = ""
    visible_recovery_task_count: int = Field(default=0, ge=0)
    escalation_action: Literal["none", "monitor", "regenerate_task", "manual_review"] = "none"

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str | None) -> datetime | None:
        return _normalize_datetime_or_none(value)

    @field_validator("batch_id", "reason", "remediation_spec_id", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: str | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        text = _normalize_optional_text(value)
        if field_name == "reason" and not text:
            raise ValueError("reason may not be empty")
        return text


def resolve_family_governor_state(
    *,
    paths: RuntimePaths,
    current_state: GoalSpecFamilyState,
    policy_payload: dict[str, object],
) -> GoalSpecFamilyGovernorState:
    """Resolve the effective family-governor snapshot for the current family phase."""

    if (
        current_state.initial_family_plan is not None
        and current_state.initial_family_plan.frozen
        and current_state.initial_family_plan.completed_at is None
        and current_state.family_governor is not None
    ):
        return current_state.family_governor

    initial_family_max_specs = _normalize_cap(
        policy_payload.get("initial_family_max_specs"),
        minimum=0,
    )
    remediation_family_max_specs = _normalize_cap(
        policy_payload.get("remediation_family_max_specs"),
        minimum=0,
    )
    if current_state.family_phase == "goal_gap_remediation":
        applied_family_max_specs = remediation_family_max_specs
    else:
        applied_family_max_specs = max(1, initial_family_max_specs)
    return GoalSpecFamilyGovernorState(
        policy_path=_relative_path(paths.objective_family_policy_file, relative_to=paths.root),
        initial_family_max_specs=initial_family_max_specs,
        remediation_family_max_specs=remediation_family_max_specs,
        applied_family_max_specs=applied_family_max_specs,
    )


def evaluate_initial_family_plan_guard(
    *,
    current_state: GoalSpecFamilyState,
    candidate_spec_id: str,
    proposed_spec_order: tuple[str, ...],
    proposed_specs: dict[str, GoalSpecFamilySpecState],
) -> InitialFamilyPlanGuardDecision:
    """Evaluate whether the proposed initial-family mutation is admissible."""

    proposed_spec_count = len(proposed_spec_order)
    governor = current_state.family_governor or GoalSpecFamilyGovernorState()
    applied_family_max_specs = governor.applied_family_max_specs

    if current_state.family_phase != "initial_family":
        return InitialFamilyPlanGuardDecision(
            action="allow",
            reason="non-initial-family-phase",
            frozen=False,
            applied_family_max_specs=applied_family_max_specs,
            proposed_spec_count=proposed_spec_count,
        )

    if current_state.initial_family_plan is None:
        if applied_family_max_specs and proposed_spec_count > applied_family_max_specs:
            return InitialFamilyPlanGuardDecision(
                action="block",
                reason="family-governor-cap-exceeded",
                frozen=False,
                applied_family_max_specs=applied_family_max_specs,
                proposed_spec_count=proposed_spec_count,
                added_spec_ids=(candidate_spec_id,),
                violation_codes=("family-cap-exceeded",),
            )
        return InitialFamilyPlanGuardDecision(
            action="freeze",
            reason="frozen-initial-family-plan",
            frozen=False,
            applied_family_max_specs=applied_family_max_specs,
            proposed_spec_count=proposed_spec_count,
        )

    plan = current_state.initial_family_plan
    if not plan.frozen:
        return InitialFamilyPlanGuardDecision(
            action="allow",
            reason="initial-family-plan-not-frozen",
            frozen=False,
            applied_family_max_specs=applied_family_max_specs,
            proposed_spec_count=proposed_spec_count,
        )

    added_spec_ids = tuple(spec_id for spec_id in proposed_spec_order if spec_id not in plan.spec_order)
    removed_spec_ids = tuple(spec_id for spec_id in plan.spec_order if spec_id not in proposed_spec_order)
    violation_codes: list[str] = []
    mutated_spec_ids: list[str] = []

    if added_spec_ids:
        violation_codes.append("added-spec-ids")
    if removed_spec_ids:
        violation_codes.append("removed-spec-ids")
    if not added_spec_ids and not removed_spec_ids and tuple(proposed_spec_order) != plan.spec_order:
        violation_codes.append("spec-order-changed")

    for spec_id in plan.spec_order:
        frozen_spec = plan.specs.get(spec_id)
        live_spec = proposed_specs.get(spec_id)
        if frozen_spec is None or live_spec is None:
            continue
        if _frozen_spec_signature(frozen_spec) != _live_spec_signature(live_spec):
            mutated_spec_ids.append(spec_id)
            violation_codes.extend(_spec_violation_codes(frozen_spec, live_spec))

    if violation_codes:
        return InitialFamilyPlanGuardDecision(
            action="block",
            reason="frozen-initial-family-plan-drift",
            frozen=True,
            applied_family_max_specs=applied_family_max_specs,
            proposed_spec_count=proposed_spec_count,
            added_spec_ids=added_spec_ids,
            removed_spec_ids=removed_spec_ids,
            mutated_spec_ids=tuple(mutated_spec_ids),
            violation_codes=tuple(violation_codes),
        )

    return InitialFamilyPlanGuardDecision(
        action="validate",
        reason="frozen-plan-conforms",
        frozen=True,
        applied_family_max_specs=applied_family_max_specs,
        proposed_spec_count=proposed_spec_count,
    )


def build_reused_spec_synthesis_family_state(
    *,
    expected_family_state: GoalSpecFamilyState,
    existing_family_state: GoalSpecFamilyState,
) -> GoalSpecFamilyState:
    """Preserve restart-stable timestamps when a synthesis run is safely reusable."""

    initial_family_plan = expected_family_state.initial_family_plan
    if initial_family_plan is not None and existing_family_state.initial_family_plan is not None:
        initial_family_plan = initial_family_plan.model_copy(
            update={
                "frozen_at": existing_family_state.initial_family_plan.frozen_at,
                "completed_at": existing_family_state.initial_family_plan.completed_at,
            }
        )
    return expected_family_state.model_copy(
        update={
            "updated_at": existing_family_state.updated_at,
            "initial_family_plan": initial_family_plan,
        }
    )


def evaluate_spec_synthesis_idempotency(
    *,
    existing_record: "SpecSynthesisRecord",
    expected_record: "SpecSynthesisRecord",
    existing_family_state: GoalSpecFamilyState,
    expected_family_state: GoalSpecFamilyState,
    actual_queue_spec_text: str,
    actual_golden_spec_text: str,
    actual_phase_spec_text: str,
    actual_decision_text: str,
    expected_queue_spec_text: str,
    expected_phase_spec_text: str,
    expected_decision_text: str,
) -> SpecSynthesisIdempotencyDecision:
    """Return whether an existing spec-synthesis run can be reused safely."""

    mismatch_fields: list[str] = []
    if existing_record != expected_record:
        mismatch_fields.append("record")
    if existing_family_state != expected_family_state:
        mismatch_fields.append("family-state")
    if actual_queue_spec_text != expected_queue_spec_text:
        mismatch_fields.append("queue-spec")
    if actual_golden_spec_text != expected_queue_spec_text:
        mismatch_fields.append("golden-spec")
    if actual_phase_spec_text != expected_phase_spec_text:
        mismatch_fields.append("phase-spec")
    if actual_decision_text != expected_decision_text:
        mismatch_fields.append("decision")

    if mismatch_fields:
        return SpecSynthesisIdempotencyDecision(
            action="rewrite",
            reason="existing-artifacts-diverged",
            mismatch_fields=tuple(mismatch_fields),
        )
    return SpecSynthesisIdempotencyDecision(
        action="reuse",
        reason="existing-artifacts-match",
    )


def apply_initial_family_policy_pin(
    *,
    paths: RuntimePaths,
    current_policy_payload: dict[str, object],
    current_family_state: GoalSpecFamilyState | None,
) -> tuple[dict[str, object], InitialFamilyPolicyPinDecision]:
    """Preserve frozen-family policy fields during objective-profile refresh."""

    report_fields = DEFAULT_PINNED_FAMILY_POLICY_FIELDS
    decision = InitialFamilyPolicyPinDecision(
        active=False,
        action="none",
        reason="no-active-frozen-initial-family",
        family_policy_path=_relative_path(paths.objective_family_policy_file, relative_to=paths.root),
        spec_family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
    )
    if current_family_state is None or current_family_state.initial_family_plan is None:
        return dict(current_policy_payload), decision

    plan = current_family_state.initial_family_plan
    if not plan.frozen:
        return dict(current_policy_payload), decision.model_copy(update={"reason": "initial-family-plan-not-frozen"})
    if plan.completed_at is not None:
        return dict(current_policy_payload), decision.model_copy(update={"reason": "frozen-initial-family-complete"})

    pinned_values = {
        "family_cap_mode": plan.family_cap_mode,
        "initial_family_max_specs": plan.initial_family_max_specs,
    }
    next_payload = dict(current_policy_payload)
    pinned_fields = tuple(
        field_name
        for field_name in report_fields
        if _normalize_scalar(next_payload.get(field_name)) != _normalize_scalar(pinned_values.get(field_name))
    )
    if not pinned_fields:
        return next_payload, decision.model_copy(
            update={"reason": "frozen-initial-family-policy-already-compliant"}
        )

    for field_name in pinned_fields:
        next_payload[field_name] = pinned_values[field_name]
    return next_payload, InitialFamilyPolicyPinDecision(
        active=True,
        action="pin",
        reason="frozen-initial-family-policy-preserved",
        pinned_fields=pinned_fields,
        family_policy_path=_relative_path(paths.objective_family_policy_file, relative_to=paths.root),
        spec_family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
    )


def build_queue_governor_report(
    *,
    paths: RuntimePaths,
    goal_id: str,
    updated_at: datetime,
    pin_decision: InitialFamilyPolicyPinDecision,
) -> QueueGovernorReport:
    """Render the queue-governor visibility record for the latest sync."""

    if pin_decision.active:
        status: Literal["not_applicable", "compliant", "pinned"] = "pinned"
    elif pin_decision.reason == "frozen-initial-family-policy-already-compliant":
        status = "compliant"
    else:
        status = "not_applicable"
    return QueueGovernorReport(
        updated_at=updated_at,
        goal_id=goal_id,
        report_path=_relative_path(paths.queue_governor_report_file, relative_to=paths.root),
        status=status,
        reason=pin_decision.reason,
        initial_family_policy_pin=pin_decision,
    )


def load_queue_governor_report(path: Path) -> QueueGovernorReport | None:
    """Load a persisted queue-governor report when present."""

    if not path.exists():
        return None
    return QueueGovernorReport.model_validate(_load_json_object(path))


def load_drift_control_policy(path: Path) -> DriftControlPolicy:
    """Load drift-detector policy or return closed-world defaults."""

    if not path.exists():
        return DriftControlPolicy()
    return DriftControlPolicy.model_validate(_load_json_object(path))


def evaluate_family_policy_drift(
    *,
    paths: RuntimePaths,
    current_family_state: GoalSpecFamilyState | None,
) -> DriftStatusReport:
    """Evaluate frozen-family policy drift against the current family-policy file."""

    policy = load_drift_control_policy(paths.drift_control_policy_file)
    base_report = DriftStatusReport(
        report_path=_relative_path(paths.drift_status_report_file, relative_to=paths.root),
        policy_path=_relative_path(paths.drift_control_policy_file, relative_to=paths.root),
        family_policy_path=_relative_path(paths.objective_family_policy_file, relative_to=paths.root),
        spec_family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        reason="no-active-frozen-initial-family",
        watched_fields=policy.watched_family_policy_fields,
    )
    if current_family_state is None or current_family_state.initial_family_plan is None:
        return base_report

    plan = current_family_state.initial_family_plan
    if not plan.frozen or plan.completed_at is not None:
        return base_report.model_copy(update={"reason": "frozen-initial-family-complete"})
    if not paths.objective_family_policy_file.exists():
        return base_report.model_copy(
            update={
                "status": "missing_policy",
                "reason": "family-policy-file-missing",
            }
        )

    current_policy_payload = _load_json_object(paths.objective_family_policy_file)
    expected_fields: dict[str, object] = {
        "family_cap_mode": plan.family_cap_mode,
        "initial_family_max_specs": plan.initial_family_max_specs,
    }
    drift_fields = tuple(
        field_name
        for field_name in policy.watched_family_policy_fields
        if field_name in expected_fields
        and _normalize_scalar(current_policy_payload.get(field_name))
        != _normalize_scalar(expected_fields.get(field_name))
    )
    if not drift_fields:
        return base_report.model_copy(
            update={
                "status": "clear",
                "reason": "frozen-family-policy-clear",
            }
        )
    status: Literal["warning", "hard_latch"] = (
        "hard_latch" if policy.hard_latch_on_policy_drift else "warning"
    )
    return base_report.model_copy(
        update={
            "status": status,
            "reason": "frozen-family-policy-drift-detected",
            "drift_fields": drift_fields,
            "warning_active": True,
            "hard_latch_active": policy.hard_latch_on_policy_drift,
        }
    )


def evaluate_governance_canary(*, paths: RuntimePaths) -> GovernanceCanaryReport:
    """Compare current and baseline drift-control policies for operator visibility."""

    report_path = _relative_path(paths.governance_canary_report_file, relative_to=paths.root)
    baseline_path = _relative_path(paths.governance_canary_baseline_policy_file, relative_to=paths.root)
    current_path = _relative_path(paths.drift_control_policy_file, relative_to=paths.root)
    if not paths.drift_control_policy_file.exists():
        return GovernanceCanaryReport(
            report_path=report_path,
            baseline_policy_path=baseline_path,
            current_policy_path=current_path,
            status="not_configured",
            reason="drift-control-policy-missing",
        )
    if not paths.governance_canary_baseline_policy_file.exists():
        return GovernanceCanaryReport(
            report_path=report_path,
            baseline_policy_path=baseline_path,
            current_policy_path=current_path,
            status="baseline_missing",
            reason="governance-canary-baseline-missing",
        )

    baseline_payload = _json_scalar_map(_load_json_object(paths.governance_canary_baseline_policy_file))
    current_payload = _json_scalar_map(_load_json_object(paths.drift_control_policy_file))
    changed_fields = tuple(
        sorted(
            {
                key
                for key in set(baseline_payload) | set(current_payload)
                if baseline_payload.get(key) != current_payload.get(key)
            }
        )
    )
    if not changed_fields:
        return GovernanceCanaryReport(
            report_path=report_path,
            baseline_policy_path=baseline_path,
            current_policy_path=current_path,
            status="match",
            reason="governance-canary-match",
        )
    return GovernanceCanaryReport(
        report_path=report_path,
        baseline_policy_path=baseline_path,
        current_policy_path=current_path,
        status="drifted",
        reason="governance-canary-policy-drift",
        changed_fields=changed_fields,
    )


def build_research_governance_report(paths: RuntimePaths) -> ResearchGovernanceReport:
    """Load additive governance visibility for the research report surface."""

    queue_governor = load_queue_governor_report(paths.queue_governor_report_file)
    if queue_governor is None:
        queue_governor = QueueGovernorReport(
            report_path=_relative_path(paths.queue_governor_report_file, relative_to=paths.root),
            status="not_applicable",
            reason="queue-governor-report-missing",
        )

    current_family_state = None
    if paths.goal_spec_family_state_file.exists():
        current_family_state = GoalSpecFamilyState.model_validate(
            _load_json_object(paths.goal_spec_family_state_file)
        )
    progress_watchdog = None
    if paths.progress_watchdog_report_file.exists():
        progress_watchdog = ProgressWatchdogReport.model_validate_json(
            paths.progress_watchdog_report_file.read_text(encoding="utf-8")
        )
    else:
        progress_watchdog = sync_progress_watchdog(paths=paths, allow_regeneration=False)
    return ResearchGovernanceReport(
        queue_governor=queue_governor,
        governance_canary=evaluate_governance_canary(paths=paths),
        drift=evaluate_family_policy_drift(paths=paths, current_family_state=current_family_state),
        progress_watchdog=progress_watchdog,
    )


def _read_task_store_cards(path: Path) -> tuple[TaskCard, ...]:
    if not path.exists():
        return ()
    return tuple(parse_task_store(path.read_text(encoding="utf-8"), source_file=path).cards)


def _visible_recovery_cards(paths: RuntimePaths, *, remediation_spec_id: str) -> tuple[TaskCard, ...]:
    cards: list[TaskCard] = []
    for store_path in (paths.tasks_file, paths.backlog_file, paths.taskspending_file):
        cards.extend(card for card in _read_task_store_cards(store_path) if card.spec_id == remediation_spec_id)
    return tuple(cards)


def _append_backlog_task(paths: RuntimePaths, *, title: str, body: str, spec_id: str) -> TaskCard:
    task_date = _utcnow().date().isoformat()
    card = TaskCard.model_validate(
        {
            "heading": f"## {task_date} - {title}",
            "body": f"- **Spec-ID:** {spec_id}\n\n{body.strip()}",
        }
    )
    backlog_document = parse_task_store(
        paths.backlog_file.read_text(encoding="utf-8"),
        source_file=paths.backlog_file,
    )
    updated = TaskStoreDocument(preamble=backlog_document.preamble, cards=[*backlog_document.cards, card])
    write_text_atomic(paths.backlog_file, render_task_store(updated))
    return card


def _governance_history_status(paths: RuntimePaths) -> tuple[bool | None, str]:
    if not paths.goal_spec_family_state_file.exists():
        return None, "goal-spec-family-state-missing"
    if not paths.objective_family_policy_file.exists():
        return None, "family-policy-file-missing"
    family_state = GoalSpecFamilyState.model_validate(_load_json_object(paths.goal_spec_family_state_file))
    plan = family_state.initial_family_plan
    if plan is None or not plan.frozen:
        return None, "no-active-frozen-family-policy-history"
    return True, "frozen-family-policy-history-preserved"


def _resolve_record_path(path_token: str | Path, *, relative_to: Path) -> Path:
    candidate = Path(path_token)
    if candidate.is_absolute():
        return candidate
    return relative_to / candidate


def _regenerate_audit_recovery_task(
    paths: RuntimePaths,
    *,
    remediation_record_path: Path,
    remediation_spec_id: str,
    visible_count_before: int,
) -> RecoveryTaskRegenerationReport:
    from .audit import AuditRemediationRecord

    if not remediation_record_path.exists():
        preserved, preservation_reason = _governance_history_status(paths)
        return RecoveryTaskRegenerationReport(
            status="manual_only",
            reason="audit-remediation-record-missing",
            decision_type="durable_remediation_decision",
            remediation_spec_id=remediation_spec_id,
            remediation_record_path=_relative_path(remediation_record_path, relative_to=paths.root),
            visible_task_count_before=visible_count_before,
            visible_task_count_after=visible_count_before,
            family_policy_path=_relative_path(paths.objective_family_policy_file, relative_to=paths.root),
            spec_family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
            family_policy_history_preserved=preserved,
            family_policy_history_reason=preservation_reason,
        )

    remediation_record = AuditRemediationRecord.model_validate(_load_json_object(remediation_record_path))
    before_policy_hash = _file_sha256_or_none(paths.objective_family_policy_file)
    before_family_state_hash = _file_sha256_or_none(paths.goal_spec_family_state_file)
    body = "\n".join(
        [
            f"- **Goal:** Restore governed audit remediation work for `{remediation_record.audit_id}`.",
            (
                "- **Context:** Regenerated by the progress watchdog from "
                f"`{_relative_path(remediation_record_path, relative_to=paths.root)}` "
                "after the frozen recovery batch no longer had visible remediation work."
            ),
            (
                "- **Acceptance:** Follow the durable remediation record and re-check the backlog/audit path "
                f"documented at `{_relative_path(remediation_record_path, relative_to=paths.root)}`."
            ),
            f"- **Notes:** Original remediation task id was `{remediation_record.remediation_task_id}`.",
        ]
    )
    regenerated_card = _append_backlog_task(
        paths,
        title=remediation_record.remediation_task_title,
        body=body,
        spec_id=remediation_record.remediation_spec_id,
    )
    after_policy_hash = _file_sha256_or_none(paths.objective_family_policy_file)
    after_family_state_hash = _file_sha256_or_none(paths.goal_spec_family_state_file)
    preserved = before_policy_hash == after_policy_hash and before_family_state_hash == after_family_state_hash
    return RecoveryTaskRegenerationReport(
        status="regenerated",
        reason="audit-remediation-task-regenerated",
        decision_type="durable_remediation_decision",
        remediation_spec_id=remediation_record.remediation_spec_id,
        remediation_record_path=_relative_path(remediation_record_path, relative_to=paths.root),
        visible_task_count_before=visible_count_before,
        visible_task_count_after=len(
            _visible_recovery_cards(paths, remediation_spec_id=remediation_record.remediation_spec_id)
        ),
        regenerated_task_id=regenerated_card.task_id,
        regenerated_task_title=regenerated_card.title,
        family_policy_path=_relative_path(paths.objective_family_policy_file, relative_to=paths.root),
        spec_family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        family_policy_history_preserved=preserved,
        family_policy_history_reason=(
            "frozen-family-policy-history-preserved"
            if preserved
            else "unexpected-family-policy-history-mutation"
        ),
    )


def _evaluate_recovery_regeneration(
    paths: RuntimePaths,
    *,
    decision_type: Literal["regenerated_backlog_work", "durable_remediation_decision"],
    remediation_spec_id: str,
    remediation_record_path: Path,
    taskaudit_record_path: str,
    task_provenance_path: str,
    lineage_path: str,
    visible_count_before: int,
    allow_regeneration: bool,
) -> RecoveryTaskRegenerationReport:
    preserved, preservation_reason = _governance_history_status(paths)
    base_report = RecoveryTaskRegenerationReport(
        reason="recovery-regeneration-not-applicable",
        decision_type=decision_type,
        remediation_spec_id=remediation_spec_id,
        remediation_record_path=_relative_path(remediation_record_path, relative_to=paths.root),
        taskaudit_record_path=taskaudit_record_path,
        task_provenance_path=task_provenance_path,
        lineage_path=lineage_path,
        visible_task_count_before=visible_count_before,
        visible_task_count_after=visible_count_before,
        family_policy_path=_relative_path(paths.objective_family_policy_file, relative_to=paths.root),
        spec_family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        family_policy_history_preserved=preserved,
        family_policy_history_reason=preservation_reason,
    )
    if visible_count_before > 0:
        return base_report.model_copy(
            update={
                "status": "not_needed",
                "reason": "visible-recovery-work-present",
            }
        )
    if decision_type == "durable_remediation_decision":
        if not allow_regeneration:
            return base_report.model_copy(
                update={
                    "status": "manual_only",
                    "reason": "durable-recovery-task-missing",
                }
            )
        return _regenerate_audit_recovery_task(
            paths,
            remediation_record_path=remediation_record_path,
            remediation_spec_id=remediation_spec_id,
            visible_count_before=visible_count_before,
        )
    return base_report.model_copy(
        update={
            "status": "manual_only",
            "reason": "regenerated-family-work-missing",
        }
    )


def evaluate_progress_watchdog(
    *,
    paths: RuntimePaths,
    allow_regeneration: bool = False,
) -> ProgressWatchdogReport:
    """Evaluate one explainable progress-watchdog snapshot over the recovery latch."""

    latch = load_research_recovery_latch(paths.research_recovery_latch_file)
    report = ProgressWatchdogReport(
        updated_at=_utcnow(),
        report_path=_relative_path(paths.progress_watchdog_report_file, relative_to=paths.root),
        state_path=_relative_path(paths.progress_watchdog_state_file, relative_to=paths.root),
        latch_path=_relative_path(paths.research_recovery_latch_file, relative_to=paths.root),
        reason="no-research-recovery-latch",
    )
    if latch is None:
        return report

    report = report.model_copy(
        update={
            "batch_id": latch.batch_id,
            "reason": "frozen-batch-awaiting-remediation-decision",
        }
    )
    decision = latch.remediation_decision
    if decision is None:
        return report.model_copy(
            update={
                "status": "waiting",
                "escalation_action": "monitor",
            }
        )

    visible_count_before = len(_visible_recovery_cards(paths, remediation_spec_id=decision.remediation_spec_id))
    regeneration_report = _evaluate_recovery_regeneration(
        paths,
        decision_type=decision.decision_type,
        remediation_spec_id=decision.remediation_spec_id,
        remediation_record_path=_resolve_record_path(decision.remediation_record_path, relative_to=paths.root),
        taskaudit_record_path=_normalize_optional_text(
            None if decision.taskaudit_record_path is None else decision.taskaudit_record_path.as_posix()
        ),
        task_provenance_path=_normalize_optional_text(
            None if decision.task_provenance_path is None else decision.task_provenance_path.as_posix()
        ),
        lineage_path=_normalize_optional_text(
            None if decision.lineage_path is None else decision.lineage_path.as_posix()
        ),
        visible_count_before=visible_count_before,
        allow_regeneration=allow_regeneration,
    )
    visible_count_after = len(_visible_recovery_cards(paths, remediation_spec_id=decision.remediation_spec_id))
    if visible_count_after > 0:
        return report.model_copy(
            update={
                "status": "regenerated" if regeneration_report.status == "regenerated" else "ready",
                "reason": (
                    "durable-recovery-task-regenerated"
                    if regeneration_report.status == "regenerated"
                    else "visible-recovery-work-present"
                ),
                "recovery_decision_type": decision.decision_type,
                "remediation_spec_id": decision.remediation_spec_id,
                "visible_recovery_task_count": visible_count_after,
                "expected_pending_card_count": decision.pending_card_count,
                "expected_backlog_card_count": decision.backlog_card_count,
                "escalation_action": "none" if regeneration_report.status != "regenerated" else "regenerate_task",
                "recovery_regeneration": regeneration_report,
            }
        )
    return report.model_copy(
        update={
            "status": "stalled",
            "reason": "visible-recovery-work-missing",
            "recovery_decision_type": decision.decision_type,
            "remediation_spec_id": decision.remediation_spec_id,
            "visible_recovery_task_count": 0,
            "expected_pending_card_count": decision.pending_card_count,
            "expected_backlog_card_count": decision.backlog_card_count,
            "escalation_action": (
                "regenerate_task"
                if decision.decision_type == "durable_remediation_decision"
                else "manual_review"
            ),
            "recovery_regeneration": regeneration_report,
        }
    )


def sync_progress_watchdog(
    *,
    paths: RuntimePaths,
    allow_regeneration: bool = False,
) -> ProgressWatchdogReport:
    """Persist one progress-watchdog snapshot for engine-side visibility."""

    report = evaluate_progress_watchdog(paths=paths, allow_regeneration=allow_regeneration)
    state = ProgressWatchdogState(
        updated_at=report.updated_at,
        batch_id=report.batch_id,
        status=report.status,
        reason=report.reason,
        remediation_spec_id=report.remediation_spec_id,
        visible_recovery_task_count=report.visible_recovery_task_count,
        escalation_action=report.escalation_action,
    )
    paths.progress_watchdog_state_file.parent.mkdir(parents=True, exist_ok=True)
    paths.progress_watchdog_report_file.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(paths.progress_watchdog_state_file, state.model_dump_json(indent=2) + "\n")
    write_text_atomic(paths.progress_watchdog_report_file, report.model_dump_json(indent=2) + "\n")
    return report


def _frozen_spec_signature(spec: FrozenInitialFamilySpecPlan) -> tuple[str, str, tuple[str, ...]]:
    return (
        spec.title,
        spec.decomposition_profile,
        spec.depends_on_specs,
    )


def _live_spec_signature(spec: GoalSpecFamilySpecState) -> tuple[str, str, tuple[str, ...]]:
    return (
        spec.title,
        spec.decomposition_profile,
        spec.depends_on_specs,
    )


def _spec_violation_codes(
    frozen_spec: FrozenInitialFamilySpecPlan,
    live_spec: GoalSpecFamilySpecState,
) -> tuple[str, ...]:
    violations: list[str] = []
    if frozen_spec.title != live_spec.title:
        violations.append("title-changed")
    if frozen_spec.decomposition_profile != live_spec.decomposition_profile:
        violations.append("decomposition-profile-changed")
    if frozen_spec.depends_on_specs != live_spec.depends_on_specs:
        violations.append("dependency-list-changed")
    return tuple(violations)


__all__ = [
    "DEFAULT_PINNED_FAMILY_POLICY_FIELDS",
    "DriftControlPolicy",
    "DriftStatusReport",
    "GoalSpecGovernanceError",
    "GovernanceCanaryReport",
    "InitialFamilyPlanGuardDecision",
    "InitialFamilyPolicyPinDecision",
    "ProgressWatchdogReport",
    "ProgressWatchdogState",
    "QueueGovernorReport",
    "RecoveryTaskRegenerationReport",
    "ResearchGovernanceReport",
    "apply_initial_family_policy_pin",
    "build_queue_governor_report",
    "build_research_governance_report",
    "SpecSynthesisIdempotencyDecision",
    "evaluate_family_policy_drift",
    "evaluate_governance_canary",
    "evaluate_progress_watchdog",
    "build_reused_spec_synthesis_family_state",
    "evaluate_initial_family_plan_guard",
    "evaluate_spec_synthesis_idempotency",
    "load_drift_control_policy",
    "load_queue_governor_report",
    "resolve_family_governor_state",
    "sync_progress_watchdog",
]
