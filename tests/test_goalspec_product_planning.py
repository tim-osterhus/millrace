from __future__ import annotations

from datetime import datetime, timezone

import pytest

from millrace_engine.research.goalspec import (
    AcceptanceProfileRecord,
    ContractorClassificationPayload,
    ContractorProfileArtifact,
    GoalSource,
)
from millrace_engine.research.goalspec_helpers import GoalSpecExecutionError
from millrace_engine.research.goalspec_product_planning import (
    derive_goal_product_plan,
    find_abstract_phase_steps,
    minimum_phase_step_count,
)
from millrace_engine.research.goalspec_semantic_profile import GoalSemanticProfile, SemanticProfileMilestone
from millrace_engine.research.state import ResearchQueueFamily


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _goal_source(*, title: str, body: str, decomposition_profile: str = "moderate") -> GoalSource:
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
        decomposition_profile=decomposition_profile,
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


def _contractor_profile(
    *,
    goal_id: str,
    run_id: str,
    shape_class: str,
    specificity_level: str,
    fallback_mode: str = "apply_resolved_profiles_only",
    archetype: str = "",
    host_platform: str = "",
    stack_hints: tuple[str, ...] = (),
    specializations: dict[str, str] | None = None,
    resolved_profile_ids: tuple[str, ...] = (),
    unresolved_specializations: tuple[str, ...] = (),
    specialization_provenance: tuple[dict[str, object], ...] = (),
    contradictions: tuple[str, ...] = (),
) -> ContractorProfileArtifact:
    return ContractorProfileArtifact.model_validate(
        {
            "goal_id": goal_id,
            "run_id": run_id,
            "updated_at": _dt("2026-04-10T18:00:00Z"),
            "source_path": "agents/ideas/archive/raw/goal.md",
            "canonical_source_path": "agents/ideas/archive/raw/goal.md",
            "current_artifact_path": "agents/ideas/staging/goal.md",
            "profile_report_path": "agents/reports/contractor_profile.md",
            "specificity_level": specificity_level,
            "shape_class": shape_class,
            "classification": ContractorClassificationPayload(
                shape_class=shape_class,
                archetype=archetype,
                host_platform=host_platform,
                stack_hints=stack_hints,
                specializations=specializations or {},
            ).model_dump(mode="json"),
            "candidate_classifications": [{"label": shape_class, "score": 0.92}],
            "confidence": 0.92,
            "fallback_mode": fallback_mode,
            "resolved_profile_ids": resolved_profile_ids,
            "unresolved_specializations": unresolved_specializations,
            "specialization_provenance": specialization_provenance,
            "capability_hints": (),
            "environment_hints": (),
            "browse_used": False,
            "browse_notes": "Local evidence was sufficient.",
            "evidence": ("Planner test fixture.",),
            "abstentions": (),
            "contradictions": contradictions,
            "notes": "",
        }
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

    assert plan.planning_profile == "framework_runtime"
    assert plan.implementation_surfaces[0].path == "millrace_engine/research/goalspec_goal_intake.py"
    assert len(plan.phase_steps) >= minimum_phase_step_count("moderate")
    assert not find_abstract_phase_steps(plan.phase_steps)


def test_derive_goal_product_plan_prefers_contractor_minecraft_mod_shape_without_loader_overlay() -> None:
    source = _goal_source(
        title="Aura Progression Mod",
        body="Build a Minecraft mod with aura progression, gameplay content, and GameTests.",
    )
    profile = _acceptance_profile(
        title=source.title,
        objective_summary="Build a Minecraft mod with aura progression, gameplay content, and GameTests.",
        capability_domains=("Aura Progression", "Registrations", "Gameplay Tests"),
        progression_lines=("Progression from registrations to gameplay proof in-game.",),
    )
    contractor_profile = _contractor_profile(
        goal_id=source.idea_id,
        run_id="planner-run-001",
        shape_class="platform_extension",
        specificity_level="L4",
        archetype="gameplay_mod",
        host_platform="minecraft",
        stack_hints=("jvm", "gradle"),
        specializations={"loader": "fabric"},
        resolved_profile_ids=("shape.platform_extension@1", "host.minecraft@1", "stack.jvm_gradle@1"),
        unresolved_specializations=("loader=fabric",),
        specialization_provenance=(
            {
                "key": "loader",
                "value": "fabric",
                "provenance": "source_requested",
                "support_state": "unsupported",
                "evidence_path": "agents/ideas/archive/raw/goal.md",
                "evidence": ["The canonical source explicitly references `fabric`."],
                "notes": "Specialization request preserved from the source goal.",
            },
            {
                "key": "loader",
                "value": "fabric",
                "provenance": "workspace_grounded",
                "support_state": "unsupported",
                "evidence_path": "mods/aura-progression-mod/src/main/resources/fabric.mod.json",
                "evidence": ["Workspace repo evidence includes Fabric loader metadata."],
                "notes": "Local repo files ground the requested Fabric loader without promoting it to a supported overlay.",
            },
        ),
    )

    plan = derive_goal_product_plan(source=source, profile=profile, contractor_profile=contractor_profile)

    assert plan.planning_profile == "generic_product"
    assert [surface.path for surface in plan.implementation_surfaces] == [
        "mods/aura-progression-mod/src/main/java",
        "mods/aura-progression-mod/src/main/java/aura-progression",
        "mods/aura-progression-mod/src/main/java/registrations",
        "mods/aura-progression-mod/src/main/java/gameplay-tests",
        "mods/aura-progression-mod/src/main/resources",
    ]
    assert [surface.path for surface in plan.verification_surfaces] == [
        "mods/aura-progression-mod/src/gametest/java",
        "mods/aura-progression-mod/src/test/java",
    ]
    assert any("loader-specific overlay" in step for step in plan.phase_steps)


def test_derive_goal_product_plan_falls_back_when_loader_is_not_workspace_grounded() -> None:
    source = _goal_source(
        title="Aura Progression Mod",
        body="Build a Minecraft mod with aura progression, gameplay content, and GameTests.",
    )
    profile = _acceptance_profile(
        title=source.title,
        objective_summary="Build a Minecraft mod with aura progression, gameplay content, and GameTests.",
        capability_domains=("Aura Progression", "Registrations", "Gameplay Tests"),
        progression_lines=("Progression from registrations to gameplay proof in-game.",),
    )
    contractor_profile = _contractor_profile(
        goal_id=source.idea_id,
        run_id="planner-run-001",
        shape_class="platform_extension",
        specificity_level="L4",
        archetype="gameplay_mod",
        host_platform="minecraft",
        stack_hints=("jvm", "gradle"),
        specializations={"loader": "fabric"},
        resolved_profile_ids=("shape.platform_extension@1", "host.minecraft@1", "stack.jvm_gradle@1"),
        unresolved_specializations=("loader=fabric",),
        specialization_provenance=(
            {
                "key": "loader",
                "value": "fabric",
                "provenance": "source_requested",
                "support_state": "unsupported",
                "evidence_path": "agents/ideas/archive/raw/goal.md",
                "evidence": ["The canonical source explicitly references `fabric`."],
                "notes": "Specialization request preserved from the source goal.",
            },
        ),
    )

    plan = derive_goal_product_plan(source=source, profile=profile, contractor_profile=contractor_profile)

    assert [surface.path for surface in plan.implementation_surfaces] == [
        "src/aura-progression-mod/entrypoint",
        "src/aura-progression-mod/aura-progression",
        "src/aura-progression-mod/registrations",
        "src/aura-progression-mod/gameplay-tests",
        "src/aura-progression-mod/workflow",
    ]
    assert [surface.path for surface in plan.verification_surfaces] == [
        "tests/aura-progression-mod/flow",
        "tests/aura-progression-mod/regression",
    ]


def test_derive_goal_product_plan_prefers_contractor_network_business_system_shape() -> None:
    source = _goal_source(
        title="Church Management System",
        body="Build a church management system for membership, check-in, and pastoral follow-up.",
    )
    profile = _acceptance_profile(
        title=source.title,
        objective_summary="Build a church management system for membership, check-in, and pastoral follow-up.",
        capability_domains=("Membership Directory", "Check-In Workflow", "Pastoral Follow-Up"),
        progression_lines=("Progression from member intake to check-in to pastoral follow-up.",),
    )
    contractor_profile = _contractor_profile(
        goal_id=source.idea_id,
        run_id="planner-run-001",
        shape_class="network_application",
        specificity_level="L3",
        archetype="crud_business_system",
        resolved_profile_ids=("shape.network_application@1", "archetype.crud_business_system@1"),
    )

    plan = derive_goal_product_plan(source=source, profile=profile, contractor_profile=contractor_profile)

    assert [surface.path for surface in plan.implementation_surfaces] == [
        "src/church-management-system/application",
        "src/church-management-system/application/membership-directory",
        "src/church-management-system/application/check-in-workflow",
        "src/church-management-system/application/pastoral-follow-up",
        "src/church-management-system/workflows",
    ]
    assert [surface.path for surface in plan.verification_surfaces] == [
        "tests/church-management-system/network_flow",
        "tests/church-management-system/workflow_regression",
    ]


@pytest.mark.parametrize(
    ("shape_class", "archetype", "expected_paths"),
    [
        (
            "automation_tool",
            "developer_cli",
            (
                "src/release-audit-cli/cli",
                "src/release-audit-cli/commands/package-selection",
                "src/release-audit-cli/commands/reporting",
                "src/release-audit-cli/exit_contracts",
                "tests/release-audit-cli/cli_flow",
                "tests/release-audit-cli/cli_regression",
            ),
        ),
        (
            "library_framework",
            "sdk_library",
            (
                "src/team-workspace-sdk/api",
                "src/team-workspace-sdk/api/workspace-client",
                "src/team-workspace-sdk/api/review-api",
                "src/team-workspace-sdk/adapters",
                "tests/team-workspace-sdk/contract",
                "tests/team-workspace-sdk/regression",
            ),
        ),
    ],
)
def test_derive_goal_product_plan_prefers_contractor_tool_and_library_shapes(
    shape_class: str,
    archetype: str,
    expected_paths: tuple[str, ...],
) -> None:
    title = "Release Audit CLI" if shape_class == "automation_tool" else "Team Workspace SDK"
    body = "Build a CLI for release audits." if shape_class == "automation_tool" else "Build an SDK for team workspace integrations."
    profile = _acceptance_profile(
        title=title,
        objective_summary=body,
        capability_domains=("Package Selection", "Reporting") if shape_class == "automation_tool" else ("Workspace Client", "Review API"),
        progression_lines=("Progression from first bounded command to proof." if shape_class == "automation_tool" else "Progression from first client call to integration proof.",),
    )
    source = _goal_source(title=title, body=body)
    contractor_profile = _contractor_profile(
        goal_id=source.idea_id,
        run_id="planner-run-001",
        shape_class=shape_class,
        specificity_level="L2",
        archetype=archetype,
        resolved_profile_ids=(f"shape.{shape_class}@1", f"archetype.{archetype}@1"),
    )

    plan = derive_goal_product_plan(source=source, profile=profile, contractor_profile=contractor_profile)
    actual_paths = tuple(surface.path for surface in (*plan.implementation_surfaces, *plan.verification_surfaces))

    assert actual_paths == expected_paths


def test_derive_goal_product_plan_falls_back_for_ambiguous_mixed_shape() -> None:
    source = _goal_source(
        title="Team System",
        body="Build something for teams, maybe a service or a CLI, but the exact product shape is unclear.",
    )
    profile = _acceptance_profile(
        title=source.title,
        objective_summary="Build something for teams, maybe a service or a CLI, but the exact product shape is unclear.",
        capability_domains=("Team Operations", "Mixed Delivery"),
        progression_lines=("Progression remains unclear across service and local-tool expectations.",),
    )
    contractor_profile = _contractor_profile(
        goal_id=source.idea_id,
        run_id="planner-run-001",
        shape_class="network_application",
        specificity_level="L1",
        fallback_mode="conservative_shape_only",
        archetype="crud_business_system",
        contradictions=("The goal mixes standalone product language with service/backend cues.",),
        resolved_profile_ids=("shape.network_application@1",),
    )

    plan = derive_goal_product_plan(source=source, profile=profile, contractor_profile=contractor_profile)

    assert [surface.path for surface in plan.implementation_surfaces] == [
        "src/team-system/entrypoint",
        "src/team-system/team-operations",
        "src/team-system/mixed-delivery",
        "src/team-system/workflow",
    ]
    assert [surface.path for surface in plan.verification_surfaces] == [
        "tests/team-system/flow",
        "tests/team-system/regression",
    ]


@pytest.mark.parametrize(
    ("title", "body", "objective_summary", "capability_domains", "progression_lines", "expected_planning_profile"),
    [
        (
            "Modernize Goal Intake",
            "Create real GoalSpec intake and objective sync stages.",
            "Create real GoalSpec intake and objective sync stages.",
            ("Goal Intake", "Objective Profile Sync"),
            ("Progression from intake to objective sync to restart-safe completion.",),
            "framework_runtime",
        ),
        (
            "Team Workspace Vertical Slice",
            "Build the first usable team workspace vertical slice for collaborative planning.",
            "Build the first usable team workspace vertical slice for collaborative planning.",
            ("Workspace Intake", "Shared Drafts", "Review Queue"),
            ("Progression from intake to shared drafting to review handoff.",),
            "generic_product",
        ),
        (
            "Shared Notes Console",
            "Build the first usable shared-notes console for a collaborative team.",
            "Build the first usable shared-notes console for a collaborative team.",
            ("Note Capture", "Review Inbox"),
            ("Progression from note capture to review to publish confirmation.",),
            "generic_product",
        ),
        (
            "Neighborhood Events Hub",
            "Build the first usable neighborhood events experience.",
            "Build the first usable neighborhood events experience.",
            ("Event Discovery", "RSVP Tracking"),
            ("Progression from discovery to RSVP confirmation proof.",),
            "generic_product",
        ),
    ],
)
@pytest.mark.parametrize("decomposition_profile", ["simple", "moderate", "involved"])
def test_derive_goal_product_plan_supported_profiles_meet_density_floors(
    title: str,
    body: str,
    objective_summary: str,
    capability_domains: tuple[str, ...],
    progression_lines: tuple[str, ...],
    expected_planning_profile: str,
    decomposition_profile: str,
) -> None:
    source = _goal_source(title=title, body=body, decomposition_profile=decomposition_profile)
    profile = _acceptance_profile(
        title=title,
        objective_summary=objective_summary,
        capability_domains=capability_domains,
        progression_lines=progression_lines,
    )

    plan = derive_goal_product_plan(source=source, profile=profile)

    assert plan.planning_profile == expected_planning_profile
    assert len(plan.phase_steps) >= minimum_phase_step_count(decomposition_profile)
    assert not find_abstract_phase_steps(plan.phase_steps)
    assert any("`" in step for step in plan.phase_steps)
