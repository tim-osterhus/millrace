"""Compiled scheduler-policy interpretation for runtime dispatch.

Shared helpers used by bounded tick (tick_cycle.py), daemon supervisor
(supervisor.py), and claim activation (activation.py) so they all read the
same compiled policy instead of owning independent semantic fallback order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from millrace_ai.contracts import LearningStageName, Plane

if TYPE_CHECKING:
    from millrace_ai.architecture import (
        SchedulerPolicyPredicateDefinition,
        SchedulerPolicyRuleDefinition,
        WorkflowPlaneSchedulerPolicyDefinition,
    )


def foreground_claim_order(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition | None,
    *,
    has_open_closure_target: bool = False,
) -> tuple[Plane, ...]:
    """Return ordered claim planes for foreground dispatch from compiled policy.

    When compiled rules are present they are evaluated against the current
    context (``has_open_closure_target``) and the highest-priority matching
    rule determines the foreground order.  When no rules match, or no rules
    are defined, the scalar ``foreground_order`` and ``closure_priority``
    fields provide the fallback behaviour.
    """

    scheduler_policy = _require_scheduler_policy(scheduler_policy)
    if scheduler_policy.rules:
        return _foreground_order_from_rules(
            scheduler_policy, has_open_closure_target=has_open_closure_target
        )

    # Fallback to scalar-field interpretation for backward compatibility.
    return _foreground_order_from_scalar_fields(
        scheduler_policy, has_open_closure_target=has_open_closure_target
    )


def learning_claim_allowed(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition | None,
) -> bool:
    """Return whether a separate post-foreground learning claim is allowed.

    ``"inline"`` (the shipped default) preserves existing behaviour where
    learning is dispatched after the foreground claim loop.  ``"deferred"``
    means learning is never claimed through the normal foreground claim
    channels.  ``"interleaved"`` is reserved for future dispatch modes.
    """

    scheduler_policy = _require_scheduler_policy(scheduler_policy)
    return scheduler_policy.learning_dispatch != "deferred"


# ---------------------------------------------------------------------------
# Rule evaluation helpers
# ---------------------------------------------------------------------------


def _foreground_order_from_rules(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition,
    *,
    has_open_closure_target: bool,
) -> tuple[Plane, ...]:
    """Evaluate compiled rules to determine the foreground plane order."""

    predicate_context = _build_predicate_context(
        has_open_closure_target=has_open_closure_target
    )
    matching_rule = _best_matching_rule(scheduler_policy, context=predicate_context)

    if matching_rule is None:
        # No rule matched; fall back to scalar fields (backward compat).
        return _foreground_order_from_scalar_fields(
            scheduler_policy, has_open_closure_target=has_open_closure_target
        )

    return _resolve_rule_foreground_order(matching_rule, scheduler_policy)


def _build_predicate_context(
    *,
    has_open_closure_target: bool,
) -> dict[str, bool | None]:
    """Build the evaluation context for predicate matching."""
    return {
        "has_open_closure_target": has_open_closure_target,
    }


def _best_matching_rule(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition,
    *,
    context: dict[str, bool | None],
) -> SchedulerPolicyRuleDefinition | None:
    """Return the highest-priority rule whose predicate(s) match *context*."""

    predicates_by_id = {
        pred.predicate_id: pred for pred in scheduler_policy.predicates
    }

    matches: list[SchedulerPolicyRuleDefinition] = []
    for rule in scheduler_policy.rules:
        if _rule_matches_context(rule, predicates_by_id=predicates_by_id, context=context):
            matches.append(rule)

    if not matches:
        return None

    # Stable sort by priority_value descending (higher wins, None = 0).
    matches.sort(key=lambda r: r.priority_value or 0, reverse=True)
    return matches[0]


def _rule_matches_context(
    rule: SchedulerPolicyRuleDefinition,
    *,
    predicates_by_id: dict[str, SchedulerPolicyPredicateDefinition],
    context: dict[str, bool | None],
) -> bool:
    """Return ``True`` when the rule's predicate conditions are satisfied."""

    # Resolve predicate: prefer explicit predicate_id reference, then inline kind.
    predicate = predicates_by_id.get(rule.predicate_id) if rule.predicate_id else None
    predicate_kind = (
        predicate.predicate_kind if predicate is not None else rule.predicate_kind
    )

    if predicate_kind is None:
        return False

    return _evaluate_predicate_kind(predicate_kind, context=context)


def _evaluate_predicate_kind(
    predicate_kind: str,
    *,
    context: dict[str, bool | None],
) -> bool:
    """Evaluate a predicate_kind against the current runtime context."""

    if predicate_kind == "always":
        return True
    if predicate_kind == "closure_target_open":
        return bool(context.get("has_open_closure_target"))
    if predicate_kind == "closure_target_closed":
        return not context.get("has_open_closure_target")
    if predicate_kind == "learning_request_pending":
        return bool(context.get("has_learning_request_pending"))
    if predicate_kind == "plane_backlog_present":
        return bool(context.get("has_plane_backlog_present"))
    return False


def _resolve_rule_foreground_order(
    rule: SchedulerPolicyRuleDefinition,
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition,
) -> tuple[Plane, ...]:
    """Apply a matching rule's effect to produce the foreground order."""

    # When the rule provides an explicit order_override, use it directly.
    if rule.order_override:
        return rule.order_override

    # Use the scalar foreground_order as the base.
    base_order = (
        scheduler_policy.foreground_order
        if scheduler_policy.foreground_order
        else tuple(p for p in scheduler_policy.plane_order)
    )

    effect = rule.effect
    if effect == "invert_plane_order":
        return _swap_execution_before_planning(base_order)
    if effect == "promote_plane" and rule.target_plane is not None:
        return _promote_plane(base_order, rule.target_plane)
    if effect == "block_plane" and rule.target_plane is not None:
        return _block_plane(base_order, rule.target_plane)

    return base_order


def _foreground_order_from_scalar_fields(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition,
    *,
    has_open_closure_target: bool,
) -> tuple[Plane, ...]:
    """Fallback: determine foreground order from scalar fields (backward compat)."""

    if scheduler_policy.foreground_order:
        order = scheduler_policy.foreground_order
    else:
        plane_set = set(scheduler_policy.plane_order)
        order = tuple(
            p
            for p in (Plane.PLANNING, Plane.EXECUTION, Plane.LEARNING)
            if p in plane_set
        )

    if has_open_closure_target:
        if scheduler_policy.closure_priority > 0:
            order = _swap_execution_before_planning(order)

    return order


def _swap_execution_before_planning(
    order: tuple[Plane, ...],
) -> tuple[Plane, ...]:
    """Move ``Plane.EXECUTION`` immediately before ``Plane.PLANNING``."""

    planes = list(order)
    try:
        exec_idx = planes.index(Plane.EXECUTION)
        plan_idx = planes.index(Plane.PLANNING)
    except ValueError:
        return order

    if exec_idx < plan_idx:
        return order  # already in closure-preferred order

    # Move execution just before planning.
    planes.pop(exec_idx)
    planes.insert(plan_idx, Plane.EXECUTION)
    return tuple(planes)


def _promote_plane(order: tuple[Plane, ...], target: Plane) -> tuple[Plane, ...]:
    """Move *target* plane to the front of *order*."""
    planes = list(order)
    try:
        idx = planes.index(target)
    except ValueError:
        return order
    planes.pop(idx)
    planes.insert(0, target)
    return tuple(planes)


def _block_plane(order: tuple[Plane, ...], target: Plane) -> tuple[Plane, ...]:
    """Remove *target* plane from *order*."""
    return tuple(p for p in order if p is not target)


# ---------------------------------------------------------------------------
# Residual-surface interpreter helpers
# ---------------------------------------------------------------------------


def fallback_entry_selection(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition | None,
) -> Literal["recon_on_idle", "skip", "pause"]:
    """Return the fallback entry behaviour when no claim is available for a plane.

    ``"recon_on_idle"`` allows the runtime to trigger workspace reconciliation
    when the claim loop is idle.
    ``"skip"`` means the plane is skipped without triggering reconciliation.
    ``"pause"`` means the entire runtime is paused when no claim is available.
    """

    scheduler_policy = _require_scheduler_policy(scheduler_policy)
    return scheduler_policy.fallback_entry_policy


def learning_target_stage_routing(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition | None,
) -> LearningStageName | None:
    """Return the targeted learning stage from compiled scheduler-policy.

    Returns a typed ``LearningStageName`` so callers do not need to
    import and construct the enum value themselves.  When ``None`` the
    compiled graph's entry-key-based activation is used directly.
    """

    scheduler_policy = _require_scheduler_policy(scheduler_policy)
    kind_id = scheduler_policy.learning_target_stage_kind_id
    if kind_id is None:
        return None
    return LearningStageName(kind_id)


def recovery_fallback_selection(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition | None,
) -> str | None:
    """Return the default recovery fallback node id from compiled scheduler-policy.

    When ``None`` no scheduler-policy default fallback is configured and the
    compiled graph's ``runtime_failure_recovery`` field remains the sole
    default recovery path.  When a non-``None`` node id is returned it should
    be resolved against the plane graph's nodes by the caller.
    """

    scheduler_policy = _require_scheduler_policy(scheduler_policy)
    return scheduler_policy.recovery_fallback_node_id


def backpressure_outcome(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition | None,
    *,
    has_open_closure_target: bool = False,
) -> Literal["block", "defer", "allow"]:
    """Return the claim deferral / backpressure outcome for the current context.

    ``"block"`` means claims that would open a competing closure target are
    blocked.
    ``"defer"`` means the claim is requeued with a backpressure event emitted.
    ``"allow"`` means the claim proceeds without backpressure gate.
    """

    scheduler_policy = _require_scheduler_policy(scheduler_policy)
    if not has_open_closure_target:
        return "allow"
    policy = scheduler_policy.backpressure_policy
    if policy == "allow":
        return "allow"
    if policy == "defer":
        return "defer"
    return "block"


def _require_scheduler_policy(
    scheduler_policy: WorkflowPlaneSchedulerPolicyDefinition | None,
) -> WorkflowPlaneSchedulerPolicyDefinition:
    if scheduler_policy is None:
        raise ValueError("compiled scheduler policy is required for runtime dispatch")
    return scheduler_policy


__all__ = [
    "backpressure_outcome",
    "fallback_entry_selection",
    "foreground_claim_order",
    "learning_claim_allowed",
    "learning_target_stage_routing",
    "recovery_fallback_selection",
]
