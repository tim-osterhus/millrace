"""Work-item claim and activation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.contracts import (
    ExecutionStageName,
    LearningRequestDocument,
    PlanningStageName,
    StageName,
    WorkItemKind,
)
from millrace_ai.queue_store import QueueClaim, QueueStore
from millrace_ai.state_store import save_snapshot
from millrace_ai.work_documents import read_work_document_as

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

import millrace_ai.runtime.completion_behavior as completion_behavior

from .graph_authority import (
    GraphActivationDecision,
    learning_stage_activation_for_graph,
    work_item_activation_for_graph,
)


def claim_next_work_item(engine: RuntimeEngine) -> None:
    queue = QueueStore(engine.paths)
    claim = queue.claim_next_planning_item()
    if claim is not None:
        activate_claim(engine, claim)
        return

    claim = queue.claim_next_execution_task()
    if claim is not None:
        activate_claim(engine, claim)
        return

    claim = queue.claim_next_learning_request()
    if claim is not None:
        activate_claim(engine, claim)


def activate_claim(engine: RuntimeEngine, claim: QueueClaim) -> None:
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None

    activation = _activation_for_claim(engine, claim)
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_plane": activation.plane,
            "active_stage": activation.stage,
            "active_node_id": activation.node_id,
            "active_stage_kind_id": activation.stage_kind_id,
            "active_run_id": engine._new_run_id(),
            "active_work_item_kind": claim.work_item_kind,
            "active_work_item_id": claim.work_item_id,
            "active_since": engine._now(),
            "current_failure_class": None,
            "updated_at": engine._now(),
        }
    )
    save_snapshot(engine.paths, engine.snapshot)
    completion_behavior.maybe_open_closure_target_for_claim(engine, claim)


def entry_stage_for_kind(work_item_kind: WorkItemKind) -> StageName:
    if work_item_kind is WorkItemKind.TASK:
        return ExecutionStageName.BUILDER
    if work_item_kind is WorkItemKind.SPEC:
        return PlanningStageName.PLANNER
    if work_item_kind is WorkItemKind.LEARNING_REQUEST:
        from millrace_ai.contracts import LearningStageName

        return LearningStageName.ANALYST
    return PlanningStageName.AUDITOR


def _activation_for_claim(engine: RuntimeEngine, claim: QueueClaim) -> GraphActivationDecision:
    assert engine.compiled_plan is not None
    if claim.work_item_kind is not WorkItemKind.LEARNING_REQUEST:
        return work_item_activation_for_graph(engine.compiled_plan, claim.work_item_kind)

    document = read_work_document_as(claim.path, model=LearningRequestDocument)
    if document.target_stage is None:
        return work_item_activation_for_graph(engine.compiled_plan, claim.work_item_kind)
    return learning_stage_activation_for_graph(engine.compiled_plan, document.target_stage)


__all__ = ["activate_claim", "claim_next_work_item", "entry_stage_for_kind"]
