"""Pure helpers for compiled plane-concurrency policy decisions."""

from __future__ import annotations

from collections.abc import Iterable

from millrace_ai.contracts import Plane, PlaneConcurrencyPolicyDefinition


def can_dispatch_plane(
    *,
    policy: PlaneConcurrencyPolicyDefinition | None,
    active_planes: Iterable[Plane],
    candidate: Plane,
) -> bool:
    """Return whether a candidate plane may start beside the active planes."""

    active_plane_set = set(active_planes)
    if candidate in active_plane_set:
        return False
    if not active_plane_set:
        return True
    if policy is None:
        return False

    for active_plane in active_plane_set:
        pair = frozenset((candidate, active_plane))
        if _pair_in_groups(pair, policy.mutually_exclusive_planes):
            return False
        if not _pair_in_groups(pair, policy.may_run_concurrently):
            return False
    return True


def _pair_in_groups(pair: frozenset[Plane], groups: Iterable[tuple[Plane, ...]]) -> bool:
    return any(pair.issubset(group) for group in groups)


__all__ = ["can_dispatch_plane"]
