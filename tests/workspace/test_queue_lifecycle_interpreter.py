from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.contracts import SpecDocument, WorkItemKind
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime.effects import SourceLifecycleAction, SourceLifecycleIntent
from millrace_ai.workspace.queue_lifecycle import QueueLifecycleInterpreter

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
