"""Neutral selected-effect authority fixture."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import cast

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import ReconcileEffect, TransitionContext
from millrace.testing import deterministic_context
from millrace.workflows import kernel_ping
from support import kernel_ping as kernel_ping_support

EFFECT_DECLARATION_ID = "kernel_ping.effect.record_task"
EFFECT_ACTION_ID = "kernel_ping.close_taskmaster_effect_ready"


def source() -> dict[str, object]:
    workflow_source = deepcopy(kernel_ping.WORKFLOW_SOURCE)
    stage = next(
        record
        for record in cast(list[dict[str, object]], workflow_source["stage_kinds"])
        if record["id"] == "kernel_ping.taskmaster"
    )
    stage["declared_outcome_ids"] = (
        *cast(tuple[str, ...], stage["declared_outcome_ids"]),
        "kernel_ping.taskmaster.effect_ready",
    )
    cast(list[dict[str, object]], workflow_source["terminal_outcomes"]).append(
        {
            "id": "kernel_ping.taskmaster.effect_ready",
            "stage_kind_id": "kernel_ping.taskmaster",
            "marker": "EFFECT_READY",
        }
    )
    cast(list[dict[str, object]], workflow_source["terminal_actions"]).append(
        {
            "id": EFFECT_ACTION_ID,
            "stage_kind_id": "kernel_ping.taskmaster",
            "outcome_id": "kernel_ping.taskmaster.effect_ready",
            "kind": "complete_work_item",
            "artifact_schema_id": "kernel_ping.task_artifact",
        }
    )
    workflow_source["effect_declarations"] = [
        {
            "id": EFFECT_DECLARATION_ID,
            "terminal_action_id": EFFECT_ACTION_ID,
            "artifact_schema_id": "kernel_ping.task_artifact",
            "provider_ref": "provider.fake_local.workspace",
            "capability_policy_ref": "policy.fake_local.no_real_side_effects",
            "target_ref_kind": "workspace_record",
            "target_ref_schema": "kernel_ping.effects.target.workspace_record.v1",
            "allowed_reconciliation_statuses": ("applied", "no_op", "refused"),
            "real_side_effects_allowed": False,
        }
    ]
    return workflow_source


def compile_effect_plan(
    workflow_source: Mapping[str, object] | None = None,
) -> tuple[SelectedCompiledPlan, str]:
    result = compile_workflow(workflow_source or source())
    assert result.plan is not None
    return result.plan, authority_fingerprint(result.plan)


def context(input_id: str) -> TransitionContext:
    return deterministic_context(transition_id=f"transition-{input_id}")


def runtime_state(*, reconciliation_status: str | None = None) -> RuntimeState:
    plan, fingerprint = compile_effect_plan()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = kernel_ping_support.apply_accepted_input(
        state,
        kernel_ping_support.runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id=EFFECT_ACTION_ID,
            input_id="observe-effect-ready",
            artifact_payload=kernel_ping_support.task_artifact_payload(),
        ),
        context("observe-effect-ready"),
    )
    if reconciliation_status is not None:
        effect = next(iter(state.effect_proposals.values()))
        reconcile_input_id = f"reconcile-effect-{reconciliation_status}"
        state = kernel_ping_support.apply_accepted_input(
            state,
            ReconcileEffect(
                reconcile_input_id,
                effect_id=effect.effect_id,
                provider_ref="provider.fake_local.workspace",
                status=reconciliation_status,
                result={
                    "provider_result_id": f"result-{reconciliation_status}",
                    "summary": "Recorded as local test evidence.",
                },
            ),
            context(reconcile_input_id),
        )
    return state
