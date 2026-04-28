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
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueClaim, QueueStore
from millrace_ai.state_store import save_snapshot
from millrace_ai.work_documents import read_work_document_as
from millrace_ai.workspace.queue_selection import list_deferred_root_spec_ids

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
    open_target = completion_behavior.active_closure_target(engine)
    if open_target is not None:
        _claim_next_open_closure_lineage_work(engine, queue, root_spec_id=open_target.root_spec_id)
        return

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
    closure_preparation = completion_behavior.prepare_closure_target_for_claim(engine, claim)
    if not closure_preparation.allowed:
        _backpressure_claim(engine, claim, open_root_spec_id=closure_preparation.open_root_spec_id)
        return

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


def _claim_next_open_closure_lineage_work(
    engine: RuntimeEngine,
    queue: QueueStore,
    *,
    root_spec_id: str,
) -> None:
    deferred_root_spec_ids = list_deferred_root_spec_ids(
        engine.paths,
        open_root_spec_id=root_spec_id,
    )
    if deferred_root_spec_ids:
        _emit_closure_target_backpressure(
            engine,
            open_root_spec_id=root_spec_id,
            deferred_root_spec_ids=deferred_root_spec_ids,
        )

    claim = queue.claim_next_execution_task(root_spec_id=root_spec_id)
    if claim is not None:
        activate_claim(engine, claim)
        return

    claim = queue.claim_next_planning_item(root_spec_id=root_spec_id)
    if claim is not None:
        activate_claim(engine, claim)


def _backpressure_claim(
    engine: RuntimeEngine,
    claim: QueueClaim,
    *,
    open_root_spec_id: str | None,
) -> None:
    if claim.work_item_kind is WorkItemKind.SPEC:
        QueueStore(engine.paths).requeue_spec(
            claim.work_item_id,
            reason="open closure target backpressure",
        )
    if open_root_spec_id is not None:
        _emit_closure_target_backpressure(
            engine,
            open_root_spec_id=open_root_spec_id,
            deferred_root_spec_ids=list_deferred_root_spec_ids(
                engine.paths,
                open_root_spec_id=open_root_spec_id,
            ),
        )


def _emit_closure_target_backpressure(
    engine: RuntimeEngine,
    *,
    open_root_spec_id: str,
    deferred_root_spec_ids: tuple[str, ...],
) -> None:
    write_runtime_event(
        engine.paths,
        event_type="closure_target_backpressure",
        data={
            "open_root_spec_id": open_root_spec_id,
            "deferred_root_spec_ids": list(deferred_root_spec_ids),
            "reason": "open_closure_target",
        },
    )


__all__ = ["activate_claim", "claim_next_work_item", "entry_stage_for_kind"]
