from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import (
    LearningRequestAction,
    LearningRequestDocument,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime.effects import SourceLifecycleAction, SourceLifecycleIntent
from millrace_ai.workspace.queue_lifecycle import (
    QueueLifecycleInterpreter,
    requeue_active_work_item,
)

NOW = datetime(2026, 5, 21, tzinfo=timezone.utc)


def test_workspace_queue_lifecycle_interpreter_moves_active_spec_to_blocked(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    queue.enqueue_spec(
        SpecDocument(
            spec_id="spec-workspace-lifecycle",
            title="Workspace lifecycle",
            summary="Exercise workspace-scoped lifecycle interpreter tests.",
            source_type="manual",
            goals=("Move active spec to blocked.",),
            constraints=("Keep the interpreter boundary explicit.",),
            acceptance=("Spec lands in blocked.",),
            references=("tests/workspace/test_queue_lifecycle_interpreter.py",),
            created_at=NOW,
            created_by="tests",
        )
    )
    assert queue.claim_next_planning_item() is not None

    destination = QueueLifecycleInterpreter(paths).apply(
        SourceLifecycleIntent(
            lifecycle_plan_id="test.block",
            action=SourceLifecycleAction.BLOCK,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-workspace-lifecycle",
        )
    )

    assert destination == paths.specs_blocked_dir / "spec-workspace-lifecycle.md"
    assert destination.is_file()


# ---------------------------------------------------------------------------
# Lifecycle: complete task via adapter-ID-selected path
# ---------------------------------------------------------------------------


def test_lifecycle_complete_task_via_adapter(tmp_path: Path) -> None:
    """Lifecycle complete for tasks uses the adapter-ID-driven generic path."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    queue.enqueue_task(
        TaskDocument(
            task_id="lc-task-complete",
            title="Lifecycle complete",
            summary="Complete this task via adapter-driven lifecycle.",
            root_idea_id="idea-001",
            root_spec_id="spec-root-001",
            target_paths=("src/",),
            acceptance=("task moves to done",),
            required_checks=("pytest",),
            references=("tests",),
            risk=("none",),
            created_at=NOW,
            created_by="tests",
        )
    )
    assert queue.claim_next_execution_task() is not None

    interpreter = QueueLifecycleInterpreter(paths)
    destination = interpreter.apply(
        SourceLifecycleIntent(
            lifecycle_plan_id="test.complete",
            action=SourceLifecycleAction.COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="lc-task-complete",
        )
    )

    assert destination == paths.tasks_done_dir / "lc-task-complete.md"
    assert destination.is_file()
    assert not (paths.tasks_active_dir / "lc-task-complete.md").exists()


def test_lifecycle_block_task_via_adapter(tmp_path: Path) -> None:
    """Lifecycle block for tasks uses the adapter-ID-driven generic path."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    queue.enqueue_task(
        TaskDocument(
            task_id="lc-task-block",
            title="Lifecycle block",
            summary="Block this task via adapter-driven lifecycle.",
            root_idea_id="idea-001",
            root_spec_id="spec-root-001",
            target_paths=("src/",),
            acceptance=("task moves to blocked",),
            required_checks=("pytest",),
            references=("tests",),
            risk=("none",),
            created_at=NOW,
            created_by="tests",
        )
    )
    assert queue.claim_next_execution_task() is not None

    interpreter = QueueLifecycleInterpreter(paths)
    destination = interpreter.apply(
        SourceLifecycleIntent(
            lifecycle_plan_id="test.block",
            action=SourceLifecycleAction.BLOCK,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="lc-task-block",
        )
    )

    assert destination == paths.tasks_blocked_dir / "lc-task-block.md"
    assert destination.is_file()
    assert not (paths.tasks_active_dir / "lc-task-block.md").exists()


# ---------------------------------------------------------------------------
# Lifecycle: complete learning request via adapter-ID-selected path
# ---------------------------------------------------------------------------


def test_lifecycle_complete_learning_request_via_adapter(tmp_path: Path) -> None:
    """Lifecycle complete for learning requests uses adapter-driven generic path."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    queue.enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="lr-lifecycle",
            title="LR lifecycle",
            summary="Lifecycle complete for learning request.",
            requested_action=LearningRequestAction.CREATE,
            target_stage=None,
            created_at=NOW,
            created_by="tests",
        )
    )
    assert queue.claim_next_learning_request() is not None

    interpreter = QueueLifecycleInterpreter(paths)
    destination = interpreter.apply(
        SourceLifecycleIntent(
            lifecycle_plan_id="test.complete",
            action=SourceLifecycleAction.COMPLETE,
            work_item_kind=WorkItemKind.LEARNING_REQUEST,
            work_item_id="lr-lifecycle",
        )
    )

    assert destination == paths.learning_requests_done_dir / "lr-lifecycle.md"
    assert destination.is_file()


# ---------------------------------------------------------------------------
# Lifecycle: requeue via adapter-ID-selected path
# ---------------------------------------------------------------------------


def test_requeue_active_task_via_adapter(tmp_path: Path) -> None:
    """Requeue for tasks uses the adapter-ID-selected path, not family dispatch."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    queue.enqueue_task(
        TaskDocument(
            task_id="lc-task-requeue",
            title="Requeue task",
            summary="Requeue via adapter-driven path.",
            root_idea_id="idea-001",
            root_spec_id="spec-root-001",
            target_paths=("src/",),
            acceptance=("task requeues",),
            required_checks=("pytest",),
            references=("tests",),
            risk=("none",),
            created_at=NOW,
            created_by="tests",
        )
    )
    assert queue.claim_next_execution_task() is not None

    destination = requeue_active_work_item(
        paths,
        work_item_kind=WorkItemKind.TASK,
        work_item_id="lc-task-requeue",
        reason="test requeue",
    )

    assert destination == paths.tasks_queue_dir / "lc-task-requeue.md"
    assert destination.is_file()
    assert not (paths.tasks_active_dir / "lc-task-requeue.md").exists()


def test_requeue_active_spec_via_adapter(tmp_path: Path) -> None:
    """Requeue for specs uses the adapter-ID-selected path."""
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    queue.enqueue_spec(
        SpecDocument(
            spec_id="lc-spec-requeue",
            title="Requeue spec",
            summary="Requeue spec via adapter-driven path.",
            source_type="manual",
            goals=("spec requeues",),
            constraints=("none",),
            acceptance=("spec back in queue",),
            references=("tests",),
            created_at=NOW,
            created_by="tests",
        )
    )
    assert queue.claim_next_planning_item() is not None

    destination = requeue_active_work_item(
        paths,
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="lc-spec-requeue",
        reason="test requeue",
    )

    assert destination == paths.specs_queue_dir / "lc-spec-requeue.md"
    assert destination.is_file()


# ---------------------------------------------------------------------------
# Lifecycle: adapter-ID-driven dispatch across families
# ---------------------------------------------------------------------------


def test_lifecycle_uses_adapter_id_not_family_id_dispatch(tmp_path: Path) -> None:
    """Prove lifecycle operations select behavior through adapter IDs, not family comparison."""
    from millrace_ai.assets import load_builtin_workflow_primitives
    from millrace_ai.workspace.family_adapters import (
        queue_adapter_for_id,
        resolve_queue_lifecycle_adapter_id,
    )

    bundle = load_builtin_workflow_primitives()
    for family in bundle.work_item_families:
        adapter_id = resolve_queue_lifecycle_adapter_id(family)
        assert adapter_id is not None, f"family {family.family_id} has no adapter ID"
        adapter = queue_adapter_for_id(adapter_id)
        assert adapter is not None, f"adapter {adapter_id} for family {family.family_id} not found"
        assert adapter.family_id == family.family_id, (
            f"adapter {adapter_id}.family_id={adapter.family_id} "
            f"does not match family {family.family_id}"
        )


# ---------------------------------------------------------------------------
# Lifecycle: complete + block for all built-in families
# ---------------------------------------------------------------------------


def test_lifecycle_complete_all_builtin_families(tmp_path: Path) -> None:
    """Complete lifecycle works for all built-in families with adapter-ID paths."""
    from millrace_ai.assets import load_builtin_workflow_primitives

    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    queue = QueueStore(paths)
    bundle = load_builtin_workflow_primitives()

    family_items: dict[str, tuple[WorkItemKind, str]] = {}
    # Enqueue one item per family that supports claim
    for family in bundle.work_item_families:
        fid = family.family_id
        if fid == "task":
            item_id = f"lc-all-{fid}"
            queue.enqueue_task(
                TaskDocument(
                    task_id=item_id,
                    title=f"LC all {fid}",
                    summary="Lifecycle all families test",
                    root_idea_id="idea-001",
                    root_spec_id="spec-root-001",
                    target_paths=("src/",),
                    acceptance=("complete works",),
                    required_checks=("pytest",),
                    references=("tests",),
                    risk=("none",),
                    created_at=NOW,
                    created_by="tests",
                )
            )
            family_items[fid] = (WorkItemKind.TASK, item_id)
        elif fid == "spec":
            item_id = f"lc-all-{fid}"
            queue.enqueue_spec(
                SpecDocument(
                    spec_id=item_id,
                    title=f"LC all {fid}",
                    summary="Lifecycle all families test",
                    source_type="manual",
                    goals=("complete works",),
                    constraints=("none",),
                    acceptance=("done",),
                    references=("tests",),
                    created_at=NOW,
                    created_by="tests",
                )
            )
            family_items[fid] = (WorkItemKind.SPEC, item_id)
        elif fid == "learning_request":
            item_id = f"lc-all-{fid}"
            queue.enqueue_learning_request(
                LearningRequestDocument(
                    learning_request_id=item_id,
                    title=f"LC all {fid}",
                    summary="Lifecycle all families test",
                    requested_action=LearningRequestAction.CREATE,
                    target_stage=None,
                    created_at=NOW,
                    created_by="tests",
                )
            )
            family_items[fid] = (WorkItemKind.LEARNING_REQUEST, item_id)
        elif fid == "incident":
            from millrace_ai.contracts import IncidentDecision, IncidentDocument, Plane

            item_id = f"lc-all-{fid}"
            queue.enqueue_incident(
                IncidentDocument(
                    incident_id=item_id,
                    title=f"LC all {fid}",
                    summary="Lifecycle all families test",
                    root_idea_id="idea-001",
                    root_spec_id="spec-root-001",
                    source_stage="consultant",
                    source_plane=Plane.EXECUTION,
                    failure_class="test",
                    trigger_reason="test",
                    consultant_decision=IncidentDecision.NEEDS_PLANNING,
                    opened_at=NOW,
                    opened_by="tests",
                )
            )
            family_items[fid] = (WorkItemKind.INCIDENT, item_id)
        elif fid == "probe":
            from millrace_ai.contracts import ProbeDocument

            item_id = f"lc-all-{fid}"
            queue.enqueue_probe(
                ProbeDocument(
                    probe_id=item_id,
                    title=f"LC all {fid}",
                    summary="Lifecycle all families test",
                    request="Investigate lifecycle across families.",
                    created_at=NOW,
                    created_by="tests",
                )
            )
            family_items[fid] = (WorkItemKind.PROBE, item_id)

    interpreter = QueueLifecycleInterpreter(paths)
    # Claim and complete each
    from millrace_ai.workspace.queue_selection import claim_next_for_family

    for fid, (kind, item_id) in sorted(family_items.items()):
        claim = claim_next_for_family(paths, fid)
        assert claim is not None, f"failed to claim {fid}"
        assert claim.work_item_id == item_id

        destination = interpreter.apply(
            SourceLifecycleIntent(
                lifecycle_plan_id="test.complete.all",
                action=SourceLifecycleAction.COMPLETE,
                work_item_kind=kind,
                work_item_id=item_id,
            )
        )
        assert destination.is_file(), f"complete failed for {fid} {item_id}"
