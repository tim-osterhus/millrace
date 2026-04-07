from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.contracts import ResearchMode, ResearchStatus
from millrace_engine.events import EventType
from millrace_engine.research.goalspec import execute_goal_intake, execute_objective_profile_sync
from millrace_engine.research.state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership, ResearchRuntimeMode
from tests.support import load_workspace_fixture


PRODUCT_GOAL_TEXT = """---
idea_id: IDEA-AURA-001
title: Aura Workshop Vertical Slice
---

# Aura Workshop Vertical Slice

Build a compact aura-themed Minecraft mod vertical slice for a first playable release.

- Aura Collector
- Aura Conduit
- Aura Reservoir
- Aura Infuser
- one aura-powered infused weapon

Progression from crafting to aura routing to infusion.
Minimal in-game teaching.
Automated validation for registration, aura behavior, infusion correctness, and the happy path.
"""


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _configured_goal_runtime(tmp_path: Path) -> tuple[Path, object]:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    loaded = load_engine_config(config_path)
    loaded.config.research.mode = ResearchMode.GOALSPEC
    return workspace, build_runtime_paths(loaded.config)


def _write_queue_file(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _goal_queue_checkpoint(*, run_id: str, emitted_at: datetime, queue_path: Path, item_path: Path) -> ResearchCheckpoint:
    return ResearchCheckpoint(
        checkpoint_id=run_id,
        mode=ResearchRuntimeMode.GOALSPEC,
        status=ResearchStatus.GOALSPEC_RUNNING,
        node_id="goal_intake",
        stage_kind_id="research.goal-intake",
        started_at=emitted_at,
        updated_at=emitted_at,
        owned_queues=(
            ResearchQueueOwnership(
                family=ResearchQueueFamily.GOALSPEC,
                queue_path=queue_path,
                item_path=item_path,
                owner_token=run_id,
                acquired_at=emitted_at,
            ),
        ),
    )


def _goal_active_request_checkpoint(*, run_id: str, emitted_at: datetime, path: Path) -> ResearchCheckpoint:
    return ResearchCheckpoint(
        checkpoint_id=run_id,
        mode=ResearchRuntimeMode.GOALSPEC,
        status=ResearchStatus.GOALSPEC_RUNNING,
        node_id="objective_profile_sync",
        stage_kind_id="research.objective-profile-sync",
        started_at=emitted_at,
        updated_at=emitted_at,
        active_request={
            "event_type": EventType.IDEA_SUBMITTED,
            "received_at": emitted_at,
            "payload": {"path": path.as_posix()},
            "queue_family": ResearchQueueFamily.GOALSPEC,
        },
    )


def test_execute_objective_profile_sync_emits_product_scoped_milestones(tmp_path: Path) -> None:
    workspace, paths = _configured_goal_runtime(tmp_path)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    run_id = "goalspec-aura-001"
    emitted_at = _dt("2026-04-07T12:00:00Z")

    _write_queue_file(raw_goal_path, PRODUCT_GOAL_TEXT)
    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=paths.ideas_raw_dir,
            item_path=raw_goal_path,
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )

    staged_path = workspace / goal_intake.research_brief_path
    result = execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(run_id=run_id, emitted_at=emitted_at, path=staged_path),
        run_id=run_id,
        emitted_at=emitted_at,
    )

    acceptance_profile = json.loads((workspace / result.profile_state_path).read_text(encoding="utf-8"))
    synced_profile = json.loads(
        (workspace / acceptance_profile["profile_path"]).read_text(encoding="utf-8")
    )
    synced_markdown = (workspace / acceptance_profile["profile_markdown_path"]).read_text(encoding="utf-8")

    milestone_text = " ".join(synced_profile["milestones"])
    assert "Aura Collector" in milestone_text
    assert "aura routing to infusion" in milestone_text
    assert "Normalize queued goal" not in milestone_text
    assert "GoalSpec brief" not in milestone_text
    assert "Aura Collector" in synced_markdown


def test_execute_objective_profile_sync_prefers_workspace_semantic_seed(tmp_path: Path) -> None:
    workspace, paths = _configured_goal_runtime(tmp_path)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    seed_path = workspace / "agents" / "objective" / "semantic_profile_seed.yaml"
    run_id = "goalspec-aura-seeded"
    emitted_at = _dt("2026-04-07T12:30:00Z")

    _write_queue_file(raw_goal_path, PRODUCT_GOAL_TEXT)
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(
        (
            "objective: Ship the aura workshop loop.\n"
            "milestones:\n"
            "  - id: SEED-FOUNDATION\n"
            "    outcome: Bring up aura collection and storage.\n"
            "  - id: SEED-INFUSION\n"
            "    outcome: Complete the infused weapon payoff.\n"
        ),
        encoding="utf-8",
    )

    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=paths.ideas_raw_dir,
            item_path=raw_goal_path,
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )

    staged_path = workspace / goal_intake.research_brief_path
    result = execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(run_id=run_id, emitted_at=emitted_at, path=staged_path),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    acceptance_profile = json.loads((workspace / result.profile_state_path).read_text(encoding="utf-8"))
    synced_profile = json.loads(
        (workspace / acceptance_profile["profile_path"]).read_text(encoding="utf-8")
    )

    assert synced_profile["milestones"] == [
        "Bring up aura collection and storage.",
        "Complete the infused weapon payoff.",
    ]
