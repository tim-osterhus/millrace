from __future__ import annotations

from datetime import datetime, timezone

import pytest

from millrace_engine.research.goalspec import AcceptanceProfileRecord, GoalSource
from millrace_engine.research.goalspec_helpers import GoalSpecExecutionError
from millrace_engine.research.goalspec_product_planning import derive_goal_product_plan
from millrace_engine.research.goalspec_semantic_profile import GoalSemanticProfile, SemanticProfileMilestone
from millrace_engine.research.state import ResearchQueueFamily


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _goal_source(*, title: str, body: str) -> GoalSource:
    return GoalSource(
        current_artifact_path="/tmp/workspace/agents/ideas/staging/goal.md",
        current_artifact_relative_path="agents/ideas/staging/goal.md",
        canonical_source_path="/tmp/workspace/agents/ideas/archive/raw/goal.md",
        canonical_relative_source_path="agents/ideas/archive/raw/goal.md",
        source_path="/tmp/workspace/agents/ideas/archive/raw/goal.md",
        relative_source_path="agents/ideas/archive/raw/goal.md",
        queue_family=ResearchQueueFamily.GOALSPEC,
        idea_id="IDEA-PLANNER-001",
        title=title,
        decomposition_profile="moderate",
        frontmatter={},
        body=body,
        canonical_body=body,
        checksum_sha256="planner-001",
    )


def _acceptance_profile(
    *,
    title: str,
    objective_summary: str,
    capability_domains: tuple[str, ...],
    progression_lines: tuple[str, ...],
) -> AcceptanceProfileRecord:
    semantic_profile = GoalSemanticProfile(
        profile_mode="heuristic",
        objective_summary=objective_summary,
        capability_domains=capability_domains,
        progression_lines=progression_lines,
        milestones=(
            SemanticProfileMilestone(
                id="CAPABILITY-FOUNDATION",
                outcome="Deliver the bounded product slice.",
                capability_scope=capability_domains[:1],
            ),
        ),
    )
    return AcceptanceProfileRecord(
        profile_id="planner-profile",
        goal_id="IDEA-PLANNER-001",
        title=title,
        run_id="planner-run-001",
        updated_at=_dt("2026-04-07T18:00:00Z"),
        canonical_source_path="agents/ideas/archive/raw/goal.md",
        current_artifact_path="agents/ideas/staging/goal.md",
        source_path="agents/ideas/archive/raw/goal.md",
        research_brief_path="agents/ideas/staging/goal.md",
        semantic_profile=semantic_profile,
        milestones=("Deliver the bounded product slice.",),
        hard_blockers=("Implementation remains open for the profiled product objective.",),
    )


def test_derive_goal_product_plan_rejects_contaminated_semantic_labels_for_product_scope() -> None:
    source = _goal_source(
        title="Support Ticket Service",
        body="Build the first usable support-ticket web app for a Python service.",
    )
    profile = _acceptance_profile(
        title="Support Ticket Service",
        objective_summary="GoalSpec planning surface",
        capability_domains=("Stage contract", "agents/ideas/staging"),
        progression_lines=("agents/_goal_intake.md",),
    )

    with pytest.raises(GoalSpecExecutionError, match="Planner refused contaminated semantic labels") as excinfo:
        derive_goal_product_plan(source=source, profile=profile)

    message = str(excinfo.value)
    assert "Stage contract" in message
    assert "agents/ideas/staging" in message
    assert "agents/_goal_intake.md" in message


def test_derive_goal_product_plan_preserves_framework_internal_runtime_goals() -> None:
    source = _goal_source(
        title="Modernize Goal Intake",
        body="Create real GoalSpec intake and objective sync stages.",
    )
    profile = _acceptance_profile(
        title="Modernize Goal Intake",
        objective_summary="Create real GoalSpec intake and objective sync stages.",
        capability_domains=("Goal Intake", "Objective Profile Sync"),
        progression_lines=("Progression from intake to objective sync to restart-safe completion.",),
    )

    plan = derive_goal_product_plan(source=source, profile=profile)

    assert plan.repo_kind == "millrace_python_runtime"
    assert plan.implementation_surfaces[0].path == "millrace_engine/research/goalspec_goal_intake.py"
