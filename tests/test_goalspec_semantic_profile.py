from __future__ import annotations

from pathlib import Path

import pytest

from millrace_engine.paths import RuntimePaths
from millrace_engine.research.goalspec_semantic_profile import (
    build_goal_semantic_profile,
    discover_semantic_seed_path,
    load_semantic_seed_document,
)


PRODUCT_GOAL_TEXT = """# Team Workspace Vertical Slice

Build the first usable team workspace vertical slice for collaborative planning.

- Workspace Intake
- Shared Drafts
- Review Queue
- Activity Feed
- Published Summary

Progression from intake to shared drafting to review handoff.
Minimal onboarding guidance.
Automated validation covers entry flow, collaboration state, handoff correctness, and the happy path.
"""

SUPPORT_TICKET_GOAL_WITH_ADMIN_NOISE = """# Shared Workspace Service

Build the first usable shared workspace service for a collaborative team.

## Capability Domains
- Workspace Intake
- Review Inbox
- Stage contract
- agents/ideas/staging
- workspace-service/phase_spec.md

## Progression Lines
- Progression from intake to review to publish confirmation.
- objective_profile_sync
- agents/_goal_intake.md
"""


def test_build_goal_semantic_profile_extracts_product_scoped_content() -> None:
    profile = build_goal_semantic_profile(PRODUCT_GOAL_TEXT)

    assert profile.profile_mode == "heuristic"
    assert profile.objective_summary == "Build the first usable team workspace vertical slice for collaborative planning."
    assert profile.capability_domains == (
        "Workspace Intake",
        "Shared Drafts",
        "Review Queue",
        "Activity Feed",
        "Published Summary",
    )
    assert profile.progression_lines == ("Progression from intake to shared drafting to review handoff.",)
    assert [item.id for item in profile.milestones] == [
        "CAPABILITY-FOUNDATION",
        "CAPABILITY-PROGRESSION",
        "CAPABILITY-ENDSTATE",
    ]
    milestone_text = " ".join(item.outcome for item in profile.milestones)
    assert "Workspace Intake" in milestone_text
    assert "intake to shared drafting to review handoff" in milestone_text
    assert "GoalSpec" not in milestone_text
    assert "objective-profile" not in milestone_text


def test_build_goal_semantic_profile_rejects_control_plane_candidates_across_domains() -> None:
    profile = build_goal_semantic_profile(SUPPORT_TICKET_GOAL_WITH_ADMIN_NOISE)

    assert profile.objective_summary == "Build the first usable shared workspace service for a collaborative team."
    assert profile.capability_domains == (
        "Workspace Intake",
        "Review Inbox",
    )
    assert profile.progression_lines == (
        "Progression from intake to review to publish confirmation.",
    )
    assert {(item.candidate, item.reason) for item in profile.rejected_candidates} == {
        ("Stage contract", "administrative_language"),
        ("agents/ideas/staging", "path_shaped"),
        ("workspace-service/phase_spec.md", "path_shaped"),
        ("objective_profile_sync", "administrative_language"),
        ("agents/_goal_intake.md", "path_shaped"),
    }


def test_seed_document_json_and_yaml_normalization(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paths = RuntimePaths.from_workspace(workspace, Path("agents"))
    paths.objective_dir.mkdir(parents=True, exist_ok=True)

    json_seed = paths.objective_dir / "semantic_profile_seed.json"
    json_seed.write_text(
        (
            "{\n"
            '  "objective": "Ship the team workspace loop.",\n'
            '  "capability_domains": ["Workspace Intake", "Activity Feed"],\n'
            '  "progression_lines": ["From intake to drafting to review handoff."],\n'
            '  "milestones": [\n'
            '    "Bring up the intake and activity loop.",\n'
            '    {"id": "SEED-HANDOFF", "outcome": "Finish the review handoff path.", "capability_scope": ["Review Queue"]}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    yaml_seed = paths.objective_dir / "semantic_profile_seed.yaml"
    yaml_seed.write_text(
        (
            "objective: Ship the seeded workspace loop.\n"
            "capability_domains:\n"
            "  - Workspace Intake\n"
            "  - Shared Drafts\n"
            "milestones:\n"
            "  - id: SEED-FOUNDATION\n"
            "    outcome: Establish intake and drafting handoff.\n"
            "    capability_scope:\n"
            "      - Workspace Intake\n"
            "      - Shared Drafts\n"
        ),
        encoding="utf-8",
    )

    assert discover_semantic_seed_path(paths) == json_seed

    json_profile = build_goal_semantic_profile(
        PRODUCT_GOAL_TEXT,
        semantic_seed_payload=load_semantic_seed_document(json_seed),
        semantic_seed_path="agents/objective/semantic_profile_seed.json",
    )
    assert json_profile.profile_mode == "seeded"
    assert json_profile.semantic_seed_path == "agents/objective/semantic_profile_seed.json"
    assert json_profile.capability_domains == ("Workspace Intake", "Activity Feed")
    assert json_profile.progression_lines == ("From intake to drafting to review handoff.",)
    assert [item.id for item in json_profile.milestones] == ["SEED-001", "SEED-HANDOFF"]
    assert json_profile.milestones[1].capability_scope == ("Review Queue",)

    json_seed.unlink()
    assert discover_semantic_seed_path(paths) == yaml_seed

    yaml_profile = build_goal_semantic_profile(
        PRODUCT_GOAL_TEXT,
        semantic_seed_payload=load_semantic_seed_document(yaml_seed),
        semantic_seed_path="agents/objective/semantic_profile_seed.yaml",
    )
    assert yaml_profile.profile_mode == "seeded"
    assert yaml_profile.capability_domains == ("Workspace Intake", "Shared Drafts")
    assert [item.id for item in yaml_profile.milestones] == ["SEED-FOUNDATION"]
    assert yaml_profile.milestones[0].capability_scope == ("Workspace Intake", "Shared Drafts")


def test_seed_document_filters_control_plane_candidates_and_records_diagnostics() -> None:
    profile = build_goal_semantic_profile(
        PRODUCT_GOAL_TEXT,
        semantic_seed_payload={
            "capability_domains": [
                "Workspace Intake",
                "agents/ideas/specs",
                "goal_intake",
            ],
            "progression_lines": [
                "From intake to drafting to review handoff.",
                "agents/_goal_intake.md",
            ],
            "milestones": [
                {
                    "id": "SEED-FOUNDATION",
                    "outcome": "Establish workspace intake and shared drafting.",
                    "capability_scope": ["Workspace Intake", "phase spec"],
                }
            ],
        },
    )

    assert profile.capability_domains == ("Workspace Intake",)
    assert profile.progression_lines == ("From intake to drafting to review handoff.",)
    assert profile.milestones[0].capability_scope == ("Workspace Intake",)
    assert {(item.candidate, item.reason) for item in profile.rejected_candidates} >= {
        ("agents/ideas/specs", "path_shaped"),
        ("goal_intake", "administrative_language"),
        ("agents/_goal_intake.md", "path_shaped"),
        ("phase spec", "administrative_language"),
    }


def test_seed_milestones_reject_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicated"):
        build_goal_semantic_profile(
            PRODUCT_GOAL_TEXT,
            semantic_seed_payload={
                "milestones": [
                    {"id": "SEED-001", "outcome": "First"},
                    {"id": "SEED-001", "outcome": "Second"},
                ]
            },
        )
