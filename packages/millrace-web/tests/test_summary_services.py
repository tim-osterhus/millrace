from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.events import write_runtime_event
from millrace_ai.extensions.builtin.blueprint.contracts import BlueprintDraftDocument
from millrace_ai.extensions.builtin.blueprint.state import enqueue_blueprint_draft
from millrace_ai.paths import initialize_workspace

from millrace_web.services.event_stream import list_event_summaries
from millrace_web.services.snapshot_reader import build_workspace_summary
from millrace_web.services.workspace_registry import WorkspaceRegistry


def test_summary_dto_reads_runtime_snapshot_and_queue_depths(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    (paths.tasks_queue_dir / "TASK-001.md").write_text("# Task\n", encoding="utf-8")
    (paths.tasks_active_dir / "TASK-002.md").write_text("# Task\n", encoding="utf-8")
    (paths.specs_done_dir / "SPEC-001.md").write_text("# Spec\n", encoding="utf-8")
    (paths.learning_requests_queue_dir / "learn-001.md").write_text("# Learn\n", encoding="utf-8")
    enqueue_blueprint_draft(
        paths,
        BlueprintDraftDocument(
            draft_id="draft-blueprint-001",
            manifest_id="manifest-blueprint-001",
            root_spec_id="spec-blueprint-001",
            root_idea_id="idea-blueprint-001",
            source_spec_id="spec-blueprint-001",
            draft_index=1,
            title="Blueprint Draft 001",
            summary="Dashboard queue depth fixture.",
            target_paths=("packages/millrace-web/src/millrace_web/services/queue_reader.py",),
            acceptance_intent=("Dashboard reports Blueprint draft depth.",),
            context_excerpt="Dashboard queue depth fixture.",
            current_revision=0,
            created_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        ),
    )

    workspace = WorkspaceRegistry.from_paths([paths.root]).get("workspace")

    summary = build_workspace_summary(workspace)

    assert summary.workspace.id == "workspace"
    assert summary.daemon.state == "stopped"
    assert summary.runtime.mode_id == "lad_codex"
    assert summary.compiled_plan.id == "bootstrap"
    assert summary.queues.tasks.incoming == 1
    assert summary.queues.tasks.active == 1
    assert summary.queues.specs.done == 1
    assert summary.queues.learning.incoming == 1
    assert summary.queues.blueprint_drafts.incoming == 1
    assert summary.queues.graph_owned_families["blueprint_draft"].incoming == 1
    assert summary.usage_governance.enabled is False


def test_event_summaries_suppress_repeated_idle_noise(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    workspace = WorkspaceRegistry.from_paths([paths.root]).get("workspace")
    occurred_at = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    write_runtime_event(
        paths,
        event_type="idle",
        data={"reason": "no_work"},
        occurred_at=occurred_at,
    )
    write_runtime_event(
        paths,
        event_type="idle",
        data={"reason": "no_work"},
        occurred_at=occurred_at.replace(minute=1),
    )
    write_runtime_event(
        paths,
        event_type="stage_start",
        data={"plane": "execution", "stage": "checker", "run_id": "run-001"},
        occurred_at=occurred_at.replace(minute=2),
    )

    events = list_event_summaries(workspace)

    assert [event.event_type for event in events] == ["idle", "stage_start"]
    assert events[0].details == "reason=no_work"
