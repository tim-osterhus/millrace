"""Compiler helpers for graph dynamic policies."""

from __future__ import annotations

from millrace_ai.architecture import (
    CompiledGraphResumePolicyPlan,
    CompiledGraphThresholdPolicyPlan,
    GraphLoopResumePolicyDefinition,
    GraphLoopThresholdPolicyDefinition,
)
from millrace_ai.config import RuntimeConfig


def compile_graph_resume_policies(
    policies: tuple[GraphLoopResumePolicyDefinition, ...],
) -> tuple[CompiledGraphResumePolicyPlan, ...]:
    return tuple(
        CompiledGraphResumePolicyPlan(
            policy_id=policy.policy_id,
            source_node_id=policy.source_node_id,
            on_outcome=policy.on_outcome,
            default_target_node_id=policy.default_target_node_id,
            metadata_stage_keys=policy.metadata_stage_keys,
            disallowed_target_node_ids=policy.disallowed_target_node_ids,
            route_reason=policy.route_reason,
        )
        for policy in policies
    )


def compile_graph_threshold_policies(
    policies: tuple[GraphLoopThresholdPolicyDefinition, ...],
    *,
    config: RuntimeConfig,
) -> tuple[CompiledGraphThresholdPolicyPlan, ...]:
    return tuple(
        CompiledGraphThresholdPolicyPlan(
            policy_id=policy.policy_id,
            source_node_ids=policy.source_node_ids,
            on_outcome=policy.on_outcome,
            counter_name=policy.counter_name,
            threshold=resolved_threshold_for_policy(policy, config=config),
            exhausted_target_node_id=policy.exhausted_target_node_id,
            exhausted_terminal_state_id=policy.exhausted_terminal_state_id,
            recovery_counter_mutation_name=policy.recovery_counter_mutation_name,
            exhausted_counter_mutation_name=policy.exhausted_counter_mutation_name,
            route_reason=policy.route_reason,
            exhausted_route_reason=policy.exhausted_route_reason,
            default_failure_class_template=policy.default_failure_class_template,
            exhausted_failure_class_template=policy.exhausted_failure_class_template,
        )
        for policy in policies
    )


# Counter-id to config-attribute mapping for operator overrides.
# GraphLoopCounterName enum values are used only as lookup keys;
# the active runtime authority is the generic counter_id string.
_CONFIG_OVERRIDES_BY_COUNTER_ID: dict[str, str] = {
    "fix_cycle_count": "max_fix_cycles",
    "troubleshoot_attempt_count": "max_troubleshoot_attempts_before_consult",
    "mechanic_attempt_count": "max_mechanic_attempts",
}


def resolved_threshold_for_policy(
    policy: GraphLoopThresholdPolicyDefinition,
    *,
    config: RuntimeConfig,
) -> int:
    """Resolve the recovery threshold for a threshold policy.

    Takes the tighter (minimum) of the graph-loop policy threshold and
    any operator-configured RuntimeConfig override.  Falls back to the
    generic config default when neither declares a positive threshold.

    Semantics: the threshold is a ceiling on recovery attempts; the lower
    value is always the binding constraint.
    """
    candidates: list[int] = []

    if policy.threshold is not None and policy.threshold > 0:
        candidates.append(policy.threshold)

    counter_id = policy.counter_name.value
    config_attr = _CONFIG_OVERRIDES_BY_COUNTER_ID.get(counter_id)
    if config_attr is not None:
        config_value = getattr(config.recovery, config_attr, None)
        if isinstance(config_value, int) and config_value > 0:
            candidates.append(config_value)

    if candidates:
        return min(candidates)
    return config.recovery.max_repair_attempts


__all__ = [
    "compile_graph_resume_policies",
    "compile_graph_threshold_policies",
    "resolved_threshold_for_policy",
]
