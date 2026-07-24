from __future__ import annotations

from pathlib import Path

import pytest

from millrace.operator import operator_status
from substrate._runtime_store_support import persist_and_load_runtime_state
from support import lad_learning


def test_full_lad_status_projection_after_restart(tmp_path: Path) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.closed_source_learning_effect_state(
        plan,
        fingerprint,
        reconciliation_status="applied",
    )

    status = operator_status(persist_and_load_runtime_state(tmp_path, state))

    assert status.selected_plan is not None
    assert status.selected_plan.workflow_id == "lad.full"
    assert status.selected_plan.authority_fingerprint == fingerprint
    assert {family.queue_family_id for family in status.queue_families} >= {
        "task",
        "spec",
        "incident",
        "learning_request",
    }
    assert any(
        row.fanout_id == "learning.trigger.execution.needs_planning"
        and row.target_route_id == "learning.trigger.analyst"
        for row in status.generated_work
    )
    assert any(row.status == "applied" for row in status.effects)
    assert any(
        artifact.source_action_id == "execution.close_consultant_needs_plan"
        for artifact in status.artifacts
    )


@pytest.mark.parametrize("state_kind", lad_learning.FULL_LAD_CLOSURE_ROOT_STATE_KINDS)
def test_full_lad_status_projects_closure_root_authority_across_learning_aftermath(
    tmp_path: Path,
    state_kind: str,
) -> None:
    plan, fingerprint = lad_learning.compile_lad_learning()
    state = lad_learning.full_lad_closure_root_state(
        plan,
        fingerprint,
        state_kind=state_kind,
    )

    status = operator_status(persist_and_load_runtime_state(tmp_path, state))

    closure = next(
        target
        for target in status.closure_targets
        if target.closure_target_id == "closure-target-learning"
    )
    generated = next(
        row
        for row in status.generated_work
        if row.item_key == "closure-librarian-learning"
    )
    assert closure.status == "open"
    assert closure.selected_plan_fingerprint == fingerprint
    assert closure.closure_root_work_item_id == "root-spec-closure"
    assert closure.root_source_kind == "spec"
    assert closure.root_source_id == "closure-source-1"
    assert closure.lineage_id == "root-spec-closure"
    assert generated.lineage_id == closure.lineage_id
    assert generated.target_queue_family_id == "learning_request"

    if state_kind == "active_wait":
        wait = next(iter(status.operator_waits))
        assert wait.status == "active"
        assert wait.lineage_id == closure.lineage_id
    elif state_kind == "effect_pending":
        effect = next(iter(status.effects))
        assert effect.status == "pending"
        assert effect.lineage_id == closure.lineage_id
    elif state_kind.startswith("effect_"):
        effect = next(iter(status.effects))
        assert effect.status == state_kind.removeprefix("effect_")
        assert effect.lineage_id == closure.lineage_id
