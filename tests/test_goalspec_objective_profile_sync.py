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

SMALL_PRODUCT_GOAL_TEXT = """---
idea_id: IDEA-SMALL-001
title: Lantern Toggle
decomposition_profile: trivial
---

# Lantern Toggle

Add a single lantern toggle interaction for the first playable build.

- Lantern Toggle

Manual validation for on and off state changes.
"""


BROAD_PRODUCT_GOAL_TEXT = """---
idea_id: IDEA-BROAD-001
title: Aura Workshop Expansion
decomposition_profile: involved
---

# Aura Workshop Expansion

Build an involved aura workshop expansion with multiple gameplay systems for the first playable release.

- Aura Collector
- Aura Conduit
- Aura Reservoir
- Aura Infuser
- Aura Forge
- Aura Boss Arena

Progression from collection to routing to infusion to boss payoff.
Progression from solo crafting to coordinated combat trials.
Automated validation for registration, aura behavior, infusion correctness, boss unlocks, and encounter completion.
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


def _run_objective_profile_sync(
    *,
    tmp_path: Path,
    goal_text: str,
    run_id: str,
    emitted_at: datetime,
) -> tuple[Path, object, dict[str, object], dict[str, object], str, dict[str, object]]:
    workspace, paths = _configured_goal_runtime(tmp_path)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    _write_queue_file(raw_goal_path, goal_text)
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
    family_policy = json.loads(paths.objective_family_policy_file.read_text(encoding="utf-8"))
    return workspace, paths, acceptance_profile, synced_profile, synced_markdown, family_policy


def test_execute_goal_intake_moves_trace_metadata_to_frontmatter(tmp_path: Path) -> None:
    workspace, paths = _configured_goal_runtime(tmp_path)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    run_id = "goalspec-trace-split-001"
    emitted_at = _dt("2026-04-07T11:50:00Z")

    _write_queue_file(raw_goal_path, PRODUCT_GOAL_TEXT)
    result = execute_goal_intake(
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

    staged_text = (workspace / result.research_brief_path).read_text(encoding="utf-8")

    assert "trace_source_artifact_path: agents/ideas/raw/goal.md" in staged_text
    assert "trace_stage_contract_path: agents/_goal_intake.md" in staged_text
    assert "Source artifact" not in staged_text
    assert "Stage contract" not in staged_text
    assert "compiled GoalSpec loop" not in staged_text
    assert "## Evidence" in staged_text
    assert "No additional product evidence was provided." in staged_text
    assert "## Route Decision" in staged_text
    assert "Ready for staging now." in staged_text


def test_execute_objective_profile_sync_emits_product_scoped_milestones(tmp_path: Path) -> None:
    _, _, acceptance_profile, synced_profile, synced_markdown, family_policy = _run_objective_profile_sync(
        tmp_path=tmp_path,
        goal_text=PRODUCT_GOAL_TEXT,
        run_id="goalspec-aura-001",
        emitted_at=_dt("2026-04-07T12:00:00Z"),
    )

    milestone_text = " ".join(synced_profile["milestones"])
    blocker_text = " ".join(synced_profile["hard_blockers"])
    assert "Aura Collector" in milestone_text
    assert "aura routing to infusion" in milestone_text
    assert "Normalize queued goal" not in milestone_text
    assert "GoalSpec brief" not in milestone_text
    assert synced_profile["semantic_profile"]["objective_summary"].startswith(
        "Build a compact aura-themed Minecraft mod vertical slice"
    )
    assert synced_profile["semantic_profile"]["capability_domains"][:2] == [
        "Aura Collector",
        "Aura Conduit",
    ]
    assert synced_profile["semantic_profile"]["progression_lines"] == [
        "Progression from crafting to aura routing to infusion."
    ]
    assert "Implementation remains open for the profiled product capabilities" in blocker_text
    assert "GoalSpec" not in blocker_text
    assert acceptance_profile["initial_family_policy_pin"]["active"] is False
    assert family_policy["family_cap_mode"] == "adaptive"
    assert family_policy["initial_family_max_specs"] > 1
    assert family_policy["adaptive_inputs"]["capability_domain_count"] == 5
    assert "Aura Collector" in synced_markdown
    assert "## Objective Summary" in synced_markdown
    assert "## Capability Domains" in synced_markdown


def test_execute_objective_profile_sync_preserves_canonical_lineage_on_staged_revisit(tmp_path: Path) -> None:
    workspace, paths = _configured_goal_runtime(tmp_path)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    first_run_id = "goalspec-first-pass"
    revisit_run_id = "goalspec-revisit-pass"
    emitted_at = _dt("2026-04-07T12:20:00Z")

    _write_queue_file(raw_goal_path, PRODUCT_GOAL_TEXT)
    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=first_run_id,
            emitted_at=emitted_at,
            queue_path=paths.ideas_raw_dir,
            item_path=raw_goal_path,
        ),
        run_id=first_run_id,
        emitted_at=emitted_at,
    )

    staged_path = workspace / goal_intake.research_brief_path
    staged_text = staged_path.read_text(encoding="utf-8")
    staged_text = staged_text.replace(
        "## Summary\nBuild a compact aura-themed Minecraft mod vertical slice for a first playable release.\n",
        "## Summary\nNormalize queued goal into daemon resume metadata only.\n",
    )
    staged_path.write_text(staged_text, encoding="utf-8")

    result = execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(run_id=revisit_run_id, emitted_at=emitted_at, path=staged_path),
        run_id=revisit_run_id,
        emitted_at=emitted_at,
    )
    acceptance_profile = json.loads((workspace / result.profile_state_path).read_text(encoding="utf-8"))
    synced_profile = json.loads((workspace / acceptance_profile["profile_path"]).read_text(encoding="utf-8"))

    assert acceptance_profile["canonical_source_path"].startswith("agents/ideas/archive/raw/goal__goalspec-first-pass__")
    assert acceptance_profile["current_artifact_path"] == goal_intake.research_brief_path
    assert acceptance_profile["source_path"] == acceptance_profile["canonical_source_path"]
    assert synced_profile["canonical_source_path"] == acceptance_profile["canonical_source_path"]
    assert synced_profile["current_artifact_path"] == goal_intake.research_brief_path
    assert "Aura Collector" in " ".join(synced_profile["milestones"])
    assert "daemon resume metadata only" not in synced_profile["semantic_profile"]["objective_summary"]


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
    assert synced_profile["semantic_profile"]["profile_mode"] == "seeded"
    assert synced_profile["semantic_profile"]["semantic_seed_path"] == "agents/objective/semantic_profile_seed.yaml"
    assert [item["id"] for item in synced_profile["semantic_profile"]["milestones"]] == [
        "SEED-FOUNDATION",
        "SEED-INFUSION",
    ]


def test_execute_objective_profile_sync_derives_adaptive_family_policy_from_profile_and_breadth(
    tmp_path: Path,
) -> None:
    _, _, _, _, _, narrow_policy = _run_objective_profile_sync(
        tmp_path=tmp_path / "narrow",
        goal_text=SMALL_PRODUCT_GOAL_TEXT,
        run_id="goalspec-small-001",
        emitted_at=_dt("2026-04-07T13:00:00Z"),
    )
    _, _, _, _, _, broad_policy = _run_objective_profile_sync(
        tmp_path=tmp_path / "broad",
        goal_text=BROAD_PRODUCT_GOAL_TEXT,
        run_id="goalspec-broad-001",
        emitted_at=_dt("2026-04-07T13:15:00Z"),
    )

    assert narrow_policy["adaptive_inputs"]["decomposition_profile"] == "trivial"
    assert broad_policy["adaptive_inputs"]["decomposition_profile"] == "involved"
    assert narrow_policy["initial_family_max_specs"] == 1
    assert broad_policy["initial_family_max_specs"] >= 6
    assert broad_policy["initial_family_max_specs"] > narrow_policy["initial_family_max_specs"]
    assert broad_policy["phase_caps"]["initial_family"] == broad_policy["initial_family_max_specs"]
