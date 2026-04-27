"""Compiler helpers for graph dynamic policies."""

from __future__ import annotations

from millrace_ai.architecture import (
    CompiledGraphResumePolicyPlan,
    CompiledGraphThresholdPolicyPlan,
    GraphLoopCounterName,
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
        )
        for policy in policies
    )


def resolved_threshold_for_policy(
    policy: GraphLoopThresholdPolicyDefinition,
    *,
    config: RuntimeConfig,
) -> int:
    if policy.counter_name is GraphLoopCounterName.FIX_CYCLE_COUNT:
        return config.recovery.max_fix_cycles
    if policy.counter_name is GraphLoopCounterName.TROUBLESHOOT_ATTEMPT_COUNT:
        return config.recovery.max_troubleshoot_attempts_before_consult
    if policy.counter_name is GraphLoopCounterName.MECHANIC_ATTEMPT_COUNT:
        return config.recovery.max_mechanic_attempts
    return policy.threshold


__all__ = [
    "compile_graph_resume_policies",
    "compile_graph_threshold_policies",
    "resolved_threshold_for_policy",
]
