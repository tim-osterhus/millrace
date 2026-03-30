"""Execution-side search and network guard helpers."""

from __future__ import annotations

from pydantic import field_validator

from ..contracts import ContractModel


class ExecutionNetworkGuardDecision(ContractModel):
    """Effective execution-side search and network allowances."""

    requested_search: bool
    mode_allows_search: bool
    search_policy_allows: bool
    network_policy_allows: bool
    allow_search: bool
    allow_network: bool
    policy_blocked: bool
    reason: str

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("reason may not be empty")
        return normalized


def evaluate_execution_network_guard(
    *,
    requested_search: bool,
    mode_allows_search: bool,
    search_enabled: bool,
    search_exception: bool,
    network_guard_enabled: bool,
    execution_network_policy: str,
    execution_network_exception: bool,
) -> ExecutionNetworkGuardDecision:
    """Resolve one execution-stage search/network policy decision."""

    search_policy_allows = search_enabled or search_exception
    network_policy_allows = (
        not network_guard_enabled
        or execution_network_policy == "allow"
        or execution_network_exception
    )
    allow_search = requested_search and mode_allows_search and search_policy_allows and network_policy_allows
    allow_network = network_policy_allows

    if requested_search and not mode_allows_search:
        reason = "Mode policy disables execution search for this frozen plan."
    elif requested_search and not search_policy_allows:
        reason = "Execution search is disabled by policy."
    elif requested_search and not network_policy_allows:
        reason = "Execution network guard blocks search/network access."
    elif requested_search:
        reason = "Execution search and network access are allowed."
    elif not network_policy_allows:
        reason = "Execution network guard enforces clean-room local-only execution."
    else:
        reason = "Execution stage does not require search and network access is allowed."

    return ExecutionNetworkGuardDecision(
        requested_search=requested_search,
        mode_allows_search=mode_allows_search,
        search_policy_allows=search_policy_allows,
        network_policy_allows=network_policy_allows,
        allow_search=allow_search,
        allow_network=allow_network,
        policy_blocked=requested_search and not allow_search,
        reason=reason,
    )


__all__ = ["ExecutionNetworkGuardDecision", "evaluate_execution_network_guard"]
