"""Governance-core helpers for GoalSpec family and idempotency guards."""

from __future__ import annotations

from importlib import import_module

from ..paths import RuntimePaths
from .governance_models import (
    DriftControlPolicy,
    DriftStatusReport,
    GoalSpecDeliveryIntegrityReport,
    GoalSpecDeliveryIntegrityState,
    GoalSpecGovernanceError,
    GovernanceCanaryReport,
    InitialFamilyPlanGuardDecision,
    InitialFamilyPolicyPinDecision,
    ProgressWatchdogReport,
    ProgressWatchdogState,
    QueueGovernorReport,
    RecoveryTaskRegenerationReport,
    ResearchGovernanceReport,
    SpecSynthesisIdempotencyDecision,
)
from .governance_reporting import (
    build_queue_governor_report,
    build_research_governance_report,
    evaluate_family_policy_drift,
    evaluate_governance_canary,
    load_drift_control_policy,
    load_queue_governor_report,
)
from .governance_support import (
    DEFAULT_PINNED_FAMILY_POLICY_FIELDS,
    _file_sha256_or_none,
    _load_json_object,
    _normalize_cap,
    _normalize_optional_text,
    _normalize_scalar,
    _relative_path,
    _utcnow,
)
from .specs import (
    FrozenInitialFamilySpecPlan,
    GoalSpecFamilyGovernorState,
    GoalSpecFamilySpecState,
    GoalSpecFamilyState,
)


def _goalspec_delivery_integrity_module():
    return import_module(".goalspec_delivery_integrity", __package__)


def _research_progress_watchdog_module():
    return import_module(".research_progress_watchdog", __package__)


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
    existing_record: object,
    expected_record: object,
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
    for field_name in report_fields:
        next_payload[field_name] = pinned_values[field_name]
    if not pinned_fields:
        pinned_fields = report_fields
    return next_payload, InitialFamilyPolicyPinDecision(
        active=True,
        action="pin",
        reason="frozen-initial-family-policy-preserved",
        pinned_fields=pinned_fields,
        family_policy_path=_relative_path(paths.objective_family_policy_file, relative_to=paths.root),
        spec_family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
    )


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


def evaluate_goalspec_delivery_integrity(*args, **kwargs):
    return _goalspec_delivery_integrity_module().evaluate_goalspec_delivery_integrity(*args, **kwargs)


def sync_goalspec_delivery_integrity(*args, **kwargs):
    return _goalspec_delivery_integrity_module().sync_goalspec_delivery_integrity(*args, **kwargs)


def load_goalspec_delivery_integrity_report(*args, **kwargs):
    return _goalspec_delivery_integrity_module().load_goalspec_delivery_integrity_report(*args, **kwargs)


def evaluate_progress_watchdog(*args, **kwargs):
    return _research_progress_watchdog_module().evaluate_progress_watchdog(*args, **kwargs)


def sync_progress_watchdog(*args, **kwargs):
    return _research_progress_watchdog_module().sync_progress_watchdog(*args, **kwargs)


__all__ = [
    "DEFAULT_PINNED_FAMILY_POLICY_FIELDS",
    "DriftControlPolicy",
    "DriftStatusReport",
    "GoalSpecDeliveryIntegrityReport",
    "GoalSpecDeliveryIntegrityState",
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
    "_file_sha256_or_none",
    "_load_json_object",
    "_normalize_optional_text",
    "_relative_path",
    "_utcnow",
    "evaluate_goalspec_delivery_integrity",
    "evaluate_family_policy_drift",
    "evaluate_governance_canary",
    "evaluate_progress_watchdog",
    "build_reused_spec_synthesis_family_state",
    "evaluate_initial_family_plan_guard",
    "evaluate_spec_synthesis_idempotency",
    "load_drift_control_policy",
    "load_goalspec_delivery_integrity_report",
    "load_queue_governor_report",
    "resolve_family_governor_state",
    "sync_goalspec_delivery_integrity",
    "sync_progress_watchdog",
]
