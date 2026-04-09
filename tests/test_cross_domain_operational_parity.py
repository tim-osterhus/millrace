from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

import pytest

from millrace_engine.contracts import ResearchMode, ResearchStatus
from millrace_engine.markdown import parse_task_cards, parse_task_store
from millrace_engine.planes.research import ResearchPlane
from millrace_engine.queue import TaskQueue
from millrace_engine.research.dispatcher import compile_research_dispatch, resolve_research_dispatch_selection
from millrace_engine.research.goalspec import (
    execute_completion_manifest_draft,
    execute_goal_intake,
    execute_objective_profile_sync,
    execute_spec_synthesis,
)
from millrace_engine.research.queues import discover_research_queues
from millrace_engine.research.state import ResearchQueueFamily, ResearchRuntimeMode
from millrace_engine.research.taskmaster import execute_taskmaster
from tests.test_queue import make_queue
from tests.test_research_dispatcher import (
    _configured_runtime,
    _dt,
    _field_block_lines,
    _goal_active_request_checkpoint,
    _goal_queue_checkpoint,
    _prepare_reviewed_spec_for_taskmaster,
    _replace_markdown_section,
    _write_audit_file,
    _write_completion_manifest,
    _write_gaps_file,
    _write_queue_file,
    _write_typed_objective_contract,
)


@dataclass(frozen=True)
class DomainCase:
    case_id: str
    raw_goal_text: str
    broad_goal_text: str
    taskmaster_title: str
    taskmaster_body: str
    split_paths: tuple[str, ...]
    repo_kind: str
    implementation_prefixes: tuple[str, ...]
    verification_prefixes: tuple[str, ...]
    canonical_goal_relative_path: str
    canonical_goal_text: str
    goal_id: str
    goal_gap_milestone_id: str
    goal_gap_outcome: str
    goal_gap_scope: tuple[str, ...]
    goal_gap_gap_id: str
    goal_gap_gap_title: str
    recovery_active_title: str
    recovery_dependent_title: str
    recovery_unrelated_title: str
    recovery_regenerated_title: str


AURA_CASE = DomainCase(
    case_id="aura",
    raw_goal_text=(
        "---\n"
        "idea_id: IDEA-AURA-PARITY-001\n"
        "title: Aura Workshop Vertical Slice\n"
        "decomposition_profile: moderate\n"
        "---\n\n"
        "# Aura Workshop Vertical Slice\n\n"
        "Build the first playable aura workshop vertical slice for the mod.\n\n"
        "## Capability Domains\n"
        "- Aura Collector\n"
        "- Aura Conduit\n"
        "- Aura Reservoir\n"
        "- Aura Infuser\n"
        "- infused weapon payoff\n\n"
        "## Progression Lines\n"
        "- Progression from crafting to aura routing to infusion.\n"
        "- Automated validation covers registration, aura behavior, infusion correctness, and the happy path.\n"
    ),
    broad_goal_text=(
        "---\n"
        "idea_id: IDEA-AURA-BROAD-001\n"
        "title: Aura Workshop Expansion\n"
        "decomposition_profile: simple\n"
        "---\n\n"
        "# Aura Workshop Expansion\n\n"
        "Build a broad but still early aura workshop slice without widening the initial family too early.\n\n"
        "## Capability Domains\n"
        "- Aura Collector\n"
        "- Aura Conduit\n"
        "- Aura Reservoir\n"
        "- Aura Infuser\n"
        "- Aura Forge\n"
        "- Aura Boss Arena\n\n"
        "## Progression Lines\n"
        "- Progression from collection to routing to infusion to boss payoff.\n"
        "- Progression from solo crafting to coordinated combat trials.\n"
    ),
    taskmaster_title="Aura Workshop Vertical Slice",
    taskmaster_body=(
        "Build the first playable aura workshop vertical slice for the mod.\n\n"
        "## Capability Domains\n"
        "- Aura collector gameplay\n"
        "- Aura conduit routing\n\n"
        "## Progression Lines\n"
        "- Progression from collection to conduit routing to first playable proof.\n"
    ),
    split_paths=(
        "src/main/java/com/example/aura/AuraWorkshopVerticalSliceContent.java",
        "src/main/java/com/example/aura/AuraCollectorGameplayBlock.java",
        "src/main/java/com/example/aura/AuraConduitRoutingBlock.java",
        "src/main/resources/assets/aura/lang/en_us.json",
        "src/test/java/com/example/aura/AuraWorkshopVerticalSliceFlowTest.java",
        "src/gametest/java/com/example/aura/AuraWorkshopVerticalSliceGameTest.java",
    ),
    repo_kind="minecraft_fabric_mod",
    implementation_prefixes=(
        "src/main/java/com/example/aura/",
        "src/main/resources/data/aura/",
    ),
    verification_prefixes=(
        "src/test/java/com/example/aura/",
        "src/gametest/java/com/example/aura/",
    ),
    canonical_goal_relative_path="agents/objective/aura-goal-gap-source.md",
    canonical_goal_text=(
        "---\n"
        "idea_id: IDEA-AURA-GAP-001\n"
        "title: Aura Goal Gap Remediation Source\n"
        "---\n\n"
        "# Aura Goal Gap Remediation Source\n\n"
        "Restore queue-empty marathon audit parity for aura workshop completion.\n"
    ),
    goal_id="IDEA-AURA-GAP-001",
    goal_gap_milestone_id="MILESTONE-AURA-GAP-001",
    goal_gap_outcome="Restore aura workshop goal gap remediation family staging",
    goal_gap_scope=("aura goal gap remediation", "marathon audit"),
    goal_gap_gap_id="GAP-AURA-201",
    goal_gap_gap_title="Restore aura workshop goal gap remediation family staging",
    recovery_active_title="Implement aura collector persistence",
    recovery_dependent_title="Hook aura collector UI refresh",
    recovery_unrelated_title="Document aura release notes",
    recovery_regenerated_title="Regenerated aura recovery slice",
)

SUPPORT_CASE = DomainCase(
    case_id="support-ticket",
    raw_goal_text=(
        "---\n"
        "idea_id: IDEA-SUPPORT-PARITY-001\n"
        "title: Support Ticket Service\n"
        "decomposition_profile: moderate\n"
        "---\n\n"
        "# Support Ticket Service\n\n"
        "Build the first usable support-ticket web app for a Python service.\n\n"
        "## Capability Domains\n"
        "- Ticket creation API\n"
        "- Agent inbox triage dashboard\n"
        "- Escalation notifications\n\n"
        "## Progression Lines\n"
        "- Progression from ticket intake to assignment to resolution confirmation.\n"
        "- Automated validation covers API behavior and core service flow.\n"
    ),
    broad_goal_text=(
        "---\n"
        "idea_id: IDEA-SUPPORT-BROAD-001\n"
        "title: Support Ticket Platform Expansion\n"
        "decomposition_profile: involved\n"
        "---\n\n"
        "# Support Ticket Platform Expansion\n\n"
        "Build a broad but still early support-ticket platform slice without widening the initial family too early.\n\n"
        "## Capability Domains\n"
        "- Ticket creation API\n"
        "- Agent inbox triage dashboard\n"
        "- Assignment routing\n"
        "- SLA escalation workflow\n"
        "- Customer notifications\n"
        "- Resolution analytics\n\n"
        "## Progression Lines\n"
        "- Progression from intake to assignment to escalation.\n"
        "- Progression from customer notification to verified resolution reporting.\n"
    ),
    taskmaster_title="Support Ticket Service",
    taskmaster_body=(
        "Build the first usable support-ticket web app for a Python service.\n\n"
        "## Capability Domains\n"
        "- Ticket creation API\n"
        "- Agent inbox triage dashboard\n"
        "- Escalation notifications\n"
        "- Resolution analytics\n\n"
        "## Progression Lines\n"
        "- Progression from ticket intake to assignment to resolution confirmation.\n"
        "- Automated validation covers API behavior, inbox triage, escalation flow, and resolution reporting.\n"
    ),
    split_paths=(
        "src/support-ticket-service/api.py",
        "src/support-ticket-service/service.py",
        "src/support-ticket-service/models.py",
        "src/support-ticket-service/notifications.py",
        "tests/test_support-ticket-service_api.py",
        "tests/test_support-ticket-service_service.py",
    ),
    repo_kind="python_product",
    implementation_prefixes=("src/support-ticket-service/",),
    verification_prefixes=("tests/test_support-ticket-service_",),
    canonical_goal_relative_path="agents/objective/support-goal-gap-source.md",
    canonical_goal_text=(
        "---\n"
        "idea_id: IDEA-SUPPORT-GAP-001\n"
        "title: Support Ticket Goal Gap Remediation Source\n"
        "---\n\n"
        "# Support Ticket Goal Gap Remediation Source\n\n"
        "Restore queue-empty marathon audit parity for the support ticket service.\n"
    ),
    goal_id="IDEA-SUPPORT-GAP-001",
    goal_gap_milestone_id="MILESTONE-SUPPORT-GAP-001",
    goal_gap_outcome="Restore support ticket goal gap remediation family staging",
    goal_gap_scope=("support goal gap remediation", "marathon audit"),
    goal_gap_gap_id="GAP-SUPPORT-201",
    goal_gap_gap_title="Restore support ticket goal gap remediation family staging",
    recovery_active_title="Implement ticket intake persistence",
    recovery_dependent_title="Wire ticket assignment dashboard refresh",
    recovery_unrelated_title="Publish support launch notes",
    recovery_regenerated_title="Regenerated support recovery slice",
)


DOMAIN_CASES = (AURA_CASE, SUPPORT_CASE)


def _task_card(
    *,
    title: str,
    goal: str,
    spec_id: str | None = None,
    dependency_title: str | None = None,
) -> str:
    lines = [
        f"## 2026-04-09 - {title}",
        "",
        f"- **Goal:** {goal}",
        "- **Context:** Cross-domain parity regression coverage.",
    ]
    if spec_id is not None:
        lines.append(f"- **Spec-ID:** {spec_id}")
    if dependency_title is not None:
        if dependency_title == "none":
            lines.append("- **Dependencies:** none")
        else:
            lines.extend(
                [
                    "- **Dependencies:**",
                    f"  - 2026-04-09 - {dependency_title}",
                ]
            )
    lines.extend(
        [
            "- **Deliverables:**",
            "  - Preserve deterministic queue behavior.",
            "- **Acceptance:** Recovery semantics remain parity-aligned.",
            f"- **Notes:** {title} is part of cross-domain parity coverage.",
        ]
    )
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("case", DOMAIN_CASES, ids=lambda case: case.case_id)
def test_cross_domain_auto_precedence_prefers_goalspec_over_audit(tmp_path: Path, case: DomainCase) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "raw" / "goal.md", case.raw_goal_text)
    _write_queue_file(
        workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-CROSS-001.md",
        "---\naudit_id: AUD-CROSS-001\nscope: cross-domain-precedence\ntrigger: manual\nstatus: incoming\n---\n\n# Audit\n",
    )
    plane = ResearchPlane(config, paths)

    dispatch = plane.dispatch_ready_work(run_id=f"{case.case_id}-auto-precedence", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.GOALSPEC
    snapshot = plane.snapshot_state()
    assert snapshot.queue_snapshot.goalspec_ready is True
    assert snapshot.queue_snapshot.audit_ready is True
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "goal_intake"
    assert plane.status_store.read() is ResearchStatus.GOAL_INTAKE_RUNNING


@pytest.mark.parametrize("case", DOMAIN_CASES, ids=lambda case: case.case_id)
def test_cross_domain_goal_family_defers_completion_manifest_and_synthesis(tmp_path: Path, case: DomainCase) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    _write_queue_file(workspace / "agents" / "ideas" / "raw" / "goal.md", case.raw_goal_text)
    plane = ResearchPlane(config, paths)
    run_id = f"{case.case_id}-goalspec-cadence"

    first_dispatch = plane.sync_runtime(trigger="engine-start", run_id=run_id, resolve_assets=False)

    assert first_dispatch is not None
    first_snapshot = plane.snapshot_state()
    assert first_snapshot.checkpoint is not None
    assert first_snapshot.checkpoint.node_id == "objective_profile_sync"
    assert plane.status_store.read() is ResearchStatus.OBJECTIVE_PROFILE_SYNC_RUNNING
    assert not paths.audit_completion_manifest_file.exists()

    second_dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=False)

    assert second_dispatch is not None
    second_snapshot = plane.snapshot_state()
    assert second_snapshot.checkpoint is not None
    assert second_snapshot.checkpoint.node_id == "completion_manifest_draft"
    assert plane.status_store.read() is ResearchStatus.COMPLETION_MANIFEST_RUNNING
    assert paths.objective_profile_sync_state_file.exists()
    assert not paths.audit_completion_manifest_file.exists()

    third_dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=False)

    assert third_dispatch is not None
    third_snapshot = plane.snapshot_state()
    assert third_snapshot.checkpoint is not None
    assert third_snapshot.checkpoint.node_id == "spec_synthesis"
    assert plane.status_store.read() is ResearchStatus.SPEC_SYNTHESIS_RUNNING
    assert paths.audit_completion_manifest_file.exists()
    completion_manifest = json.loads(paths.audit_completion_manifest_file.read_text(encoding="utf-8"))
    assert completion_manifest["repo_kind"] == case.repo_kind
    for prefix in case.implementation_prefixes:
        assert any(
            path.startswith(prefix)
            for path in (surface["path"] for surface in completion_manifest["implementation_surfaces"])
        )
    for prefix in case.verification_prefixes:
        assert any(
            path.startswith(prefix)
            for path in (surface["path"] for surface in completion_manifest["verification_surfaces"])
        )


@pytest.mark.parametrize("case", DOMAIN_CASES, ids=lambda case: case.case_id)
def test_cross_domain_spec_synthesis_keeps_broad_family_single_spec_until_later_cycle(
    tmp_path: Path,
    case: DomainCase,
) -> None:
    workspace, _, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    emitted_at = _dt("2026-04-09T18:00:00Z")
    run_id = f"{case.case_id}-broad-family"
    staged_path = workspace / "agents" / "ideas" / "staging" / "goal.md"

    _write_queue_file(raw_goal_path, case.broad_goal_text)
    execute_goal_intake(
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
    staged_candidates = sorted(paths.ideas_staging_dir.glob("*.md"))
    assert staged_candidates
    staged_path = staged_candidates[0]
    execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="spec_synthesis",
            stage_kind_id="research.spec-synthesis",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    family_policy = json.loads(paths.objective_family_policy_file.read_text(encoding="utf-8"))
    completion_manifest = execute_completion_manifest_draft(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="spec_synthesis",
            stage_kind_id="research.spec-synthesis",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    ).draft_state

    result = execute_spec_synthesis(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            status=ResearchStatus.SPEC_SYNTHESIS_RUNNING,
            node_id="spec_synthesis",
            stage_kind_id="research.spec-synthesis",
        ),
        run_id=run_id,
        completion_manifest=completion_manifest,
        emitted_at=emitted_at,
    )

    family_state = json.loads((workspace / result.family_state_path).read_text(encoding="utf-8"))
    phase_text = (workspace / result.phase_spec_path).read_text(encoding="utf-8")
    decision_text = (workspace / result.decision_path).read_text(encoding="utf-8")

    assert family_policy["initial_family_max_specs"] >= 1
    assert family_policy["adaptive_inputs"]["capability_domain_count"] == 6
    assert family_policy["adaptive_inputs"]["progression_line_count"] == 2
    assert family_state["family_complete"] is True
    assert len(family_state["spec_order"]) == 1
    assert len(family_state["specs"]) == 1
    assert all(not spec_id.endswith("-02") for spec_id in family_state["specs"])
    assert phase_text.count("Planned later initial-family specs:") == 1
    assert "- None." in phase_text
    assert "Carry the drafted GoalSpec package" not in phase_text
    assert "Planned later specs: none" in decision_text


@pytest.mark.parametrize("case", (AURA_CASE,), ids=lambda case: case.case_id)
def test_cross_domain_taskmaster_splits_oversized_phase_steps_with_profile_scaled_envelope(
    tmp_path: Path,
    case: DomainCase,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    _write_queue_file(workspace / "agents" / "ideas" / "raw" / "goal.md", case.raw_goal_text)
    plane = ResearchPlane(config, paths)
    run_id = f"{case.case_id}-taskmaster-split"

    for _ in range(6):
        dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=False)
        assert dispatch is not None

    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "taskmaster"
    family_state = json.loads(paths.goal_spec_family_state_file.read_text(encoding="utf-8"))
    spec_id = family_state["active_spec_id"]
    phase_path = workspace / family_state["specs"][spec_id]["stable_spec_paths"][1]
    joined_paths = " and ".join(f"`{path}`" for path in case.split_paths)
    phase_text = _replace_markdown_section(
        phase_path.read_text(encoding="utf-8"),
        "Work Plan",
        "\n".join(
            [
                "## Work Plan",
                f"1. Implement the broad launch slice across {joined_paths} while preserving the same bounded vertical-slice contract.",
            ]
        ),
    )
    phase_path.write_text(phase_text, encoding="utf-8")

    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)
    assert selection is not None
    dispatch = compile_research_dispatch(
        paths,
        selection,
        run_id=run_id,
        queue_discovery=discovery,
        resolve_assets=False,
    )

    reviewed_candidates = sorted(paths.ideas_specs_reviewed_dir.glob("*.md"))
    assert reviewed_candidates
    reviewed_path = reviewed_candidates[0]
    result = execute_taskmaster(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=_dt("2026-04-09T18:30:00Z"),
            queue_path=reviewed_path.parent,
            item_path=reviewed_path,
            status=ResearchStatus.TASKMASTER_RUNNING,
            node_id="taskmaster",
            stage_kind_id="research.taskmaster",
        ),
        dispatch=dispatch,
        run_id=run_id,
        emitted_at=_dt("2026-04-09T18:30:00Z"),
    )

    shard = parse_task_store((workspace / result.shard_path).read_text(encoding="utf-8"), source_file=workspace / result.shard_path)
    record = json.loads((workspace / result.record_path).read_text(encoding="utf-8"))

    assert result.card_count == 6
    assert record["profile_selection"]["expected_min_cards"] == 6
    assert record["profile_selection"]["expected_max_cards"] == 10
    assert [card.title.split(" - ", 1)[0] for card in shard.cards] == [
        f"{spec_id} PHASE_01.1a",
        f"{spec_id} PHASE_01.1b",
        f"{spec_id} PHASE_01.1c",
        f"{spec_id} PHASE_01.1d",
        f"{spec_id} PHASE_01.1e",
        f"{spec_id} PHASE_01.1f",
    ]
    assert all(len(_field_block_lines(card.body, "Files to touch")) == 1 for card in shard.cards)
    shard_text = (workspace / result.shard_path).read_text(encoding="utf-8")
    for path in case.split_paths:
        assert path in shard_text


@pytest.mark.parametrize("case", DOMAIN_CASES, ids=lambda case: case.case_id)
def test_cross_domain_dependency_quarantine_retains_unrelated_backlog(tmp_path: Path, case: DomainCase) -> None:
    queue, workspace = make_queue(tmp_path)
    active_markdown = _task_card(
        title=case.recovery_active_title,
        goal=f"Implement the first bounded {case.case_id} capability slice.",
        spec_id=f"SPEC-{case.case_id.upper()}-ACTIVE",
        dependency_title="none",
    )
    dependent_markdown = _task_card(
        title=case.recovery_dependent_title,
        goal=f"Connect the dependent {case.case_id} flow after the active slice lands.",
        spec_id=f"SPEC-{case.case_id.upper()}-DEPENDENT",
        dependency_title=case.recovery_active_title,
    )
    unrelated_markdown = _task_card(
        title=case.recovery_unrelated_title,
        goal=f"Keep unrelated {case.case_id} docs work moving independently.",
        spec_id=f"SPEC-{case.case_id.upper()}-DOCS",
        dependency_title="none",
    )
    missing_metadata_markdown = _task_card(
        title=f"{case.case_id} stakeholder notes",
        goal=f"Preserve manually queued {case.case_id} notes.",
    )
    (workspace / "agents/tasks.md").write_text(f"# Active Task\n\n{active_markdown}", encoding="utf-8")
    (workspace / "agents/tasksbacklog.md").write_text(
        f"# Task Backlog\n\n{dependent_markdown}\n\n{unrelated_markdown}\n\n{missing_metadata_markdown}",
        encoding="utf-8",
    )

    active_card = queue.active_task()
    assert active_card is not None
    latch = queue.quarantine(
        active_card,
        f"Consult exhausted the {case.case_id} local path",
        Path(f"agents/ideas/incidents/incoming/INC-{case.case_id.upper()}-DEPENDENCY-001.md"),
        quarantine_mode_requested="dependency",
    )

    backlog_cards = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert [card.title for card in backlog_cards] == [case.recovery_unrelated_title]
    backburner_text = (workspace / "agents/tasksbackburner.md").read_text(encoding="utf-8")
    assert case.recovery_active_title in backburner_text
    assert case.recovery_dependent_title in backburner_text
    assert f"{case.case_id} stakeholder notes" in backburner_text
    assert case.recovery_unrelated_title not in backburner_text
    assert latch.quarantine_mode_requested == "dependency"
    assert latch.quarantine_mode_applied == "dependency"
    assert latch.quarantine_reason == "dependency_overlap_match"
    assert latch.frozen_backlog_cards == 2
    assert latch.retained_backlog_cards == 1
    assert latch.missing_metadata_quarantined == 1


@pytest.mark.parametrize("case", DOMAIN_CASES, ids=lambda case: case.case_id)
def test_cross_domain_thaw_restores_frozen_cards_once_visible_work_reappears(
    tmp_path: Path,
    case: DomainCase,
) -> None:
    queue, workspace = make_queue(tmp_path)
    active_markdown = _task_card(
        title=case.recovery_active_title,
        goal=f"Implement the first bounded {case.case_id} capability slice.",
        spec_id=f"SPEC-{case.case_id.upper()}-ACTIVE",
        dependency_title="none",
    )
    dependent_markdown = _task_card(
        title=case.recovery_dependent_title,
        goal=f"Connect the dependent {case.case_id} flow after the active slice lands.",
        spec_id=f"SPEC-{case.case_id.upper()}-DEPENDENT",
        dependency_title=case.recovery_active_title,
    )
    regenerated_markdown = _task_card(
        title=case.recovery_regenerated_title,
        goal=f"Provide visible regenerated {case.case_id} backlog work.",
        spec_id=f"SPEC-{case.case_id.upper()}-REGEN",
        dependency_title="none",
    )
    (workspace / "agents/tasks.md").write_text(f"# Active Task\n\n{active_markdown}", encoding="utf-8")
    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{dependent_markdown}", encoding="utf-8")

    active_card = queue.active_task()
    assert active_card is not None
    latch = queue.quarantine(
        active_card,
        f"Consult exhausted the {case.case_id} local path",
        Path(f"agents/ideas/incidents/incoming/INC-{case.case_id.upper()}-THAW-001.md"),
    )

    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{regenerated_markdown}", encoding="utf-8")

    thawed = queue.thaw(latch)

    assert thawed == 2
    backlog_cards = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert [card.title for card in backlog_cards] == [
        case.recovery_regenerated_title,
        case.recovery_active_title,
        case.recovery_dependent_title,
    ]
    assert not (workspace / "agents/.runtime/research_recovery_latch.json").exists()


@pytest.mark.parametrize("case", DOMAIN_CASES, ids=lambda case: case.case_id)
def test_cross_domain_queue_empty_audit_stages_goal_gap_remediation_family(
    tmp_path: Path,
    case: DomainCase,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    canonical_goal_path = workspace / case.canonical_goal_relative_path
    canonical_goal_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_goal_path.write_text(case.canonical_goal_text, encoding="utf-8")
    paths.objective_family_policy_file.parent.mkdir(parents=True, exist_ok=True)
    paths.objective_family_policy_file.write_text(
        json.dumps(
            {
                "family_cap_mode": "static",
                "initial_family_max_specs": 4,
                "remediation_family_max_specs": 1,
                "overflow_registry_path": "agents/.research_runtime/deferred_follow_ons.json",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    incoming_path = workspace / "agents" / "ideas" / "audit" / "incoming" / f"AUD-{case.case_id.upper()}-706.md"
    required_command = "pytest -q tests/test_cross_domain_operational_parity.py"
    _write_audit_file(
        incoming_path,
        audit_id=f"AUD-{case.case_id.upper()}-706",
        trigger="queue_empty",
        status="incoming",
        scope=f"{case.case_id}-goal-gap-remediation-family",
        commands=[required_command],
    )
    _write_completion_manifest(workspace, configured=True, commands=[required_command])
    _write_typed_objective_contract(
        workspace,
        profile_id=f"{case.case_id}-goal-gap-family-profile",
        goal_id=case.goal_id,
        title=f"{case.taskmaster_title} goal gap remediation family objective",
        source_path=case.canonical_goal_relative_path,
        require_open_gaps_zero=False,
        semantic_milestones=[
            {
                "id": case.goal_gap_milestone_id,
                "outcome": case.goal_gap_outcome,
                "capability_scope": list(case.goal_gap_scope),
            }
        ],
    )
    _write_gaps_file(
        workspace,
        open_rows=[
            {
                "gap_id": case.goal_gap_gap_id,
                "title": case.goal_gap_gap_title,
                "area": "research",
                "owner": "qa",
                "severity": "S2",
                "notes": f"{case.goal_gap_milestone_id} remains unresolved after the queue-empty completion pass.",
            }
        ],
    )
    plane = ResearchPlane(config, paths)

    dispatch = plane.sync_runtime(trigger="engine-start", run_id=f"{case.case_id}-audit-sync-706", resolve_assets=False)

    selection_path = workspace / "agents" / "reports" / "goal_gap_remediation_selection.json"
    staged_idea_path = workspace / "agents" / "ideas" / "staging" / f"{case.goal_id}__goal-gap-remediation.md"
    family_state_path = paths.goal_spec_family_state_file

    assert dispatch is not None
    assert plane.status_store.read() is ResearchStatus.AUDIT_FAIL
    assert selection_path.exists()
    assert staged_idea_path.exists()
    assert family_state_path.exists()

    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    family_state = json.loads(family_state_path.read_text(encoding="utf-8"))
    staged_text = staged_idea_path.read_text(encoding="utf-8")

    assert selection["goal_id"] == case.goal_id
    assert selection["family_phase"] == "goal_gap_remediation"
    assert selection["total_remediation_items"] == 1
    assert selection["family_decomposition_profile"] == "trivial"
    assert selection["applied_family_max_specs"] == 1
    assert selection["output_idea_path"] == f"agents/ideas/staging/{case.goal_id}__goal-gap-remediation.md"
    assert family_state["goal_id"] == case.goal_id
    assert family_state["family_phase"] == "goal_gap_remediation"
    assert family_state["family_complete"] is False
    assert family_state["family_governor"]["applied_family_max_specs"] == 1
    assert f"canonical_source_path: {case.canonical_goal_relative_path}" in staged_text
    assert "decomposition_profile: trivial" in staged_text

    discovery = discover_research_queues(paths)
    assert discovery.family_scan(ResearchQueueFamily.GOALSPEC).ready is True
    next_dispatch = plane.dispatch_ready_work(run_id=f"{case.case_id}-goal-gap-follow-on", resolve_assets=False)

    assert next_dispatch is not None
    assert next_dispatch.selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
    assert next_dispatch.entry_stage.node_id == "objective_profile_sync"
