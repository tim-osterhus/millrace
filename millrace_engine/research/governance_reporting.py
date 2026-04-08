"""Reporting helpers for the research governance surface."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from ..paths import RuntimePaths
from .governance_models import (
    DriftControlPolicy,
    DriftStatusReport,
    GovernanceCanaryReport,
    InitialFamilyPolicyPinDecision,
    ProgressWatchdogReport,
    QueueGovernorReport,
    ResearchGovernanceReport,
)
from .governance_support import (
    _json_scalar_map,
    _load_json_object,
    _normalize_scalar,
    _relative_path,
)
from .specs import GoalSpecFamilyState


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

    from .goalspec_delivery_integrity import load_goalspec_delivery_integrity_report, sync_goalspec_delivery_integrity
    from .research_progress_watchdog import sync_progress_watchdog

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
    if paths.progress_watchdog_report_file.exists():
        progress_watchdog = ProgressWatchdogReport.model_validate_json(
            paths.progress_watchdog_report_file.read_text(encoding="utf-8")
        )
    else:
        progress_watchdog = sync_progress_watchdog(paths=paths, allow_regeneration=False)
    try:
        goalspec_delivery_integrity = load_goalspec_delivery_integrity_report(paths=paths)
    except (ValidationError, ValueError):
        goalspec_delivery_integrity = None
    if goalspec_delivery_integrity is None:
        goalspec_delivery_integrity = sync_goalspec_delivery_integrity(paths=paths)
    return ResearchGovernanceReport(
        queue_governor=queue_governor,
        governance_canary=evaluate_governance_canary(paths=paths),
        drift=evaluate_family_policy_drift(paths=paths, current_family_state=current_family_state),
        goalspec_delivery_integrity=goalspec_delivery_integrity,
        progress_watchdog=progress_watchdog,
    )


__all__ = [
    "build_queue_governor_report",
    "build_research_governance_report",
    "evaluate_family_policy_drift",
    "evaluate_governance_canary",
    "load_drift_control_policy",
    "load_queue_governor_report",
]
