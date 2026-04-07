from __future__ import annotations

from pathlib import Path

import pytest

from millrace_engine.paths import RuntimePaths
from millrace_engine.research.goalspec_semantic_profile import (
    build_goal_semantic_profile,
    discover_semantic_seed_path,
    load_semantic_seed_document,
)


PRODUCT_GOAL_TEXT = """# Aura Workshop Vertical Slice

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


def test_build_goal_semantic_profile_extracts_product_scoped_content() -> None:
    profile = build_goal_semantic_profile(PRODUCT_GOAL_TEXT)

    assert profile.profile_mode == "heuristic"
    assert profile.objective_summary == (
        "Build a compact aura-themed Minecraft mod vertical slice for a first playable release."
    )
    assert profile.capability_domains == (
        "Aura Collector",
        "Aura Conduit",
        "Aura Reservoir",
        "Aura Infuser",
        "one aura-powered infused weapon",
    )
    assert profile.progression_lines == ("Progression from crafting to aura routing to infusion.",)
    assert [item.id for item in profile.milestones] == [
        "CAPABILITY-FOUNDATION",
        "CAPABILITY-PROGRESSION",
        "CAPABILITY-ENDSTATE",
    ]
    milestone_text = " ".join(item.outcome for item in profile.milestones)
    assert "Aura Collector" in milestone_text
    assert "aura routing to infusion" in milestone_text
    assert "GoalSpec" not in milestone_text
    assert "objective-profile" not in milestone_text


def test_seed_document_json_and_yaml_normalization(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paths = RuntimePaths.from_workspace(workspace, Path("agents"))
    paths.objective_dir.mkdir(parents=True, exist_ok=True)

    json_seed = paths.objective_dir / "semantic_profile_seed.json"
    json_seed.write_text(
        (
            "{\n"
            '  "objective": "Ship the aura workshop loop.",\n'
            '  "capability_domains": ["Aura Collector", "Aura Reservoir"],\n'
            '  "progression_lines": ["From collection to storage to infusion."],\n'
            '  "milestones": [\n'
            '    "Bring up the collector and reservoir loop.",\n'
            '    {"id": "SEED-INFUSION", "outcome": "Finish the infusion payoff.", "capability_scope": ["Aura Infuser"]}\n'
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    yaml_seed = paths.objective_dir / "semantic_profile_seed.yaml"
    yaml_seed.write_text(
        (
            "objective: Ship the seeded aura loop.\n"
            "capability_domains:\n"
            "  - Aura Collector\n"
            "  - Aura Conduit\n"
            "milestones:\n"
            "  - id: SEED-FOUNDATION\n"
            "    outcome: Establish aura collection and transfer.\n"
            "    capability_scope:\n"
            "      - Aura Collector\n"
            "      - Aura Conduit\n"
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
    assert json_profile.capability_domains == ("Aura Collector", "Aura Reservoir")
    assert json_profile.progression_lines == ("From collection to storage to infusion.",)
    assert [item.id for item in json_profile.milestones] == ["SEED-001", "SEED-INFUSION"]
    assert json_profile.milestones[1].capability_scope == ("Aura Infuser",)

    json_seed.unlink()
    assert discover_semantic_seed_path(paths) == yaml_seed

    yaml_profile = build_goal_semantic_profile(
        PRODUCT_GOAL_TEXT,
        semantic_seed_payload=load_semantic_seed_document(yaml_seed),
        semantic_seed_path="agents/objective/semantic_profile_seed.yaml",
    )
    assert yaml_profile.profile_mode == "seeded"
    assert yaml_profile.capability_domains == ("Aura Collector", "Aura Conduit")
    assert [item.id for item in yaml_profile.milestones] == ["SEED-FOUNDATION"]
    assert yaml_profile.milestones[0].capability_scope == ("Aura Collector", "Aura Conduit")


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
