"""Adaptive family-policy derivation for GoalSpec objective sync."""

from __future__ import annotations

from datetime import datetime

from .goalspec_helpers import _isoformat_z
from .goalspec_semantic_profile import GoalSemanticProfile
from .normalization_helpers import _normalize_optional_text, _normalize_text_sequence
from .specs import GoalSpecDecompositionProfile

_DEFAULT_NOTES = (
    "Family caps are runtime controls and should stay out of downstream synthesis prompts.",
    "Adaptive caps should reflect decomposition breadth rather than speculative LOC.",
)

_INITIAL_CAP_BASE: dict[GoalSpecDecompositionProfile, int] = {
    "": 3,
    "trivial": 1,
    "simple": 2,
    "moderate": 4,
    "involved": 6,
    "complex": 8,
    "massive": 10,
}

_REMEDIATION_CAP_BASE: dict[GoalSpecDecompositionProfile, int] = {
    "": 2,
    "trivial": 1,
    "simple": 1,
    "moderate": 2,
    "involved": 3,
    "complex": 4,
    "massive": 5,
}


def derive_objective_family_policy(
    *,
    current_policy_payload: dict[str, object],
    semantic_profile: GoalSemanticProfile,
    decomposition_profile: GoalSpecDecompositionProfile,
    source_goal_id: str,
    updated_at: datetime,
) -> dict[str, object]:
    """Build deterministic family-policy payload from semantic breadth."""

    policy = dict(current_policy_payload)
    family_cap_mode = str(policy.get("family_cap_mode", "")).strip().lower() or "adaptive"
    if family_cap_mode not in {"adaptive", "static"}:
        family_cap_mode = "adaptive"

    milestone_count = len(semantic_profile.milestones)
    capability_domain_count = len(semantic_profile.capability_domains)
    progression_line_count = len(semantic_profile.progression_lines)

    breadth_bonus = 0
    if milestone_count >= 7:
        breadth_bonus += 1
    if capability_domain_count >= 8:
        breadth_bonus += 1

    profile_key = decomposition_profile if decomposition_profile in _INITIAL_CAP_BASE else ""
    initial_base = _INITIAL_CAP_BASE[profile_key]
    remediation_base = _REMEDIATION_CAP_BASE[profile_key]

    if family_cap_mode == "static":
        initial_cap = max(1, int(policy.get("initial_family_max_specs", initial_base) or initial_base))
        remediation_cap = max(
            1,
            int(policy.get("remediation_family_max_specs", min(initial_cap, remediation_base)) or remediation_base),
        )
    else:
        initial_cap = min(12, initial_base + breadth_bonus)
        remediation_bonus = 1 if milestone_count >= 9 or capability_domain_count >= 10 else 0
        remediation_cap = max(1, min(initial_cap, remediation_base + remediation_bonus))

    notes = _normalize_text_sequence(policy.get("notes") or _DEFAULT_NOTES)
    remediation_only_capabilities = _normalize_text_sequence(policy.get("remediation_only_capabilities"))
    overflow_registry_path = (
        _normalize_optional_text(policy.get("overflow_registry_path")) or "agents/.research_runtime/deferred_follow_ons.json"
    )

    policy.update(
        {
            "schema_version": "1.1",
            "family_cap_mode": family_cap_mode,
            "initial_scope_mode": str(policy.get("initial_scope_mode", "")).strip() or "bounded_initial_family",
            "defer_overflow_follow_ons": bool(policy.get("defer_overflow_follow_ons", True)),
            "complete_initial_family_when_budget_hit": bool(policy.get("complete_initial_family_when_budget_hit", True)),
            "overflow_registry_path": overflow_registry_path,
            "remediation_scope_mode": (
                str(policy.get("remediation_scope_mode", "")).strip() or "bounded_remediation_family"
            ),
            "remediation_only_capabilities": list(remediation_only_capabilities),
            "notes": list(notes),
            "initial_family_max_specs": initial_cap,
            "remediation_family_max_specs": remediation_cap,
            "phase_caps": {
                "initial_family": initial_cap,
                "goal_gap_remediation": remediation_cap,
            },
            "adaptive_inputs": {
                "decomposition_profile": profile_key or "moderate",
                "milestone_count": milestone_count,
                "capability_domain_count": capability_domain_count,
                "progression_line_count": progression_line_count,
                "breadth_bonus": breadth_bonus,
                "initial_cap_clamp": 12,
            },
            "source_goal_id": source_goal_id,
            "updated_at": _isoformat_z(updated_at),
        }
    )
    return policy
