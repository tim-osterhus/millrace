from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import re
import sys

import pytest

import millrace_engine.planes.research as research_plane_module
import millrace_engine.research.goalspec_scope_diagnostics as goalspec_scope_diagnostics_module
import millrace_engine.research.goalspec_spec_synthesis as goalspec_spec_synthesis_module
from millrace_engine.config import ConfigApplyBoundary, build_runtime_paths, load_engine_config
from millrace_engine.contracts import (
    CrossPlaneParentRun,
    ExecutionResearchHandoff,
    ExecutionStatus,
    ResearchMode,
    ResearchStatus,
    SpecInterviewPolicy,
    StageType,
)
from millrace_engine.control import EngineControl
from millrace_engine.engine import MillraceEngine
from millrace_engine.events import EventBus, EventRecord, EventSource, EventType
from millrace_engine.markdown import parse_task_store
from millrace_engine.planes.research import ResearchLockUnavailableError, ResearchPlane
from millrace_engine.queue import TaskQueue, load_research_recovery_latch
from millrace_engine.research import (
    build_research_governance_report,
    CompiledResearchDispatchError,
    entry_stage_type_for_dispatch,
    TaskauditExecutionError,
    execute_taskaudit,
)
from millrace_engine.research.dispatcher import (
    compile_research_dispatch,
    resolve_research_dispatch_selection,
)
from millrace_engine.research.goalspec_product_planning import (
    minimum_phase_package_count,
    minimum_phase_step_count,
)
from millrace_engine.research.goalspec import (
    execute_completion_manifest_draft,
    execute_goal_intake,
    execute_objective_profile_sync,
    execute_spec_interview,
    execute_spec_review,
    execute_spec_synthesis,
)
from millrace_engine.research.taskmaster import (
    TaskmasterExecutionError,
    execute_taskmaster,
    taskmaster_card_envelope,
)
from millrace_engine.research.interview import answer_interview_question, list_interview_questions
from millrace_engine.research.queues import discover_research_queues
from millrace_engine.research.specs import GoalSpecFamilyState, build_initial_family_plan_snapshot
from millrace_engine.research.state import (
    apply_research_runtime_state_migration,
    preview_research_runtime_state_migration,
    ResearchCheckpoint,
    ResearchQueueFamily,
    ResearchQueueOwnership,
    ResearchQueueSelectionAuthority,
    ResearchRuntimeMode,
    load_research_runtime_state,
)
from tests.support import load_workspace_fixture


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _configured_runtime(
    tmp_path: Path,
    *,
    mode: ResearchMode,
    interview_policy: str = "off",
) -> tuple[Path, object, object]:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    loaded = load_engine_config(config_path)
    loaded.config.research.mode = mode
    loaded.config.research.interview_policy = interview_policy
    return workspace, loaded.config, build_runtime_paths(loaded.config)


def _resume_research_until_settled(
    plane: ResearchPlane,
    *,
    trigger: str,
    run_id: str | None = None,
    resolve_assets: bool = False,
    max_passes: int = 12,
):
    dispatch = None
    for _ in range(max_passes):
        dispatch = plane.sync_runtime(trigger=trigger, run_id=run_id, resolve_assets=resolve_assets)
        snapshot = plane.snapshot_state()
        if snapshot.checkpoint is None or plane.status_store.read() is ResearchStatus.BLOCKED:
            return dispatch
    raise AssertionError("research plane did not settle within the allowed pass budget")


def _run_research_until_settled(
    plane: ResearchPlane,
    *,
    run_id: str,
    trigger: str = "engine-start",
    resolve_assets: bool = False,
    max_passes: int = 12,
):
    dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=resolve_assets)
    snapshot = plane.snapshot_state()
    if snapshot.checkpoint is None or plane.status_store.read() is ResearchStatus.BLOCKED:
        return dispatch
    return _resume_research_until_settled(
        plane,
        trigger=trigger,
        run_id=run_id,
        resolve_assets=resolve_assets,
        max_passes=max_passes - 1,
    )


def _write_queue_file(path: Path, body: str = "# queued\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _write_json_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _replace_markdown_section(document: str, heading: str, replacement: str) -> str:
    marker = f"## {heading}"
    start = document.index(marker)
    next_heading = document.find("\n## ", start + len(marker))
    if next_heading == -1:
        end = len(document)
    else:
        end = next_heading + 1
    prefix = document[:start]
    suffix = document[end:]
    updated = prefix + replacement.rstrip() + "\n"
    if suffix and not suffix.startswith("\n"):
        updated += "\n"
    return updated + suffix.lstrip("\n")


def _field_block_lines(body: str, field_name: str) -> tuple[str, ...]:
    marker = f"- **{field_name}:**"
    lines: list[str] = []
    capture = False
    for raw_line in body.splitlines():
        stripped = raw_line.rstrip()
        if stripped == marker:
            capture = True
            continue
        if not capture:
            continue
        if stripped.startswith("- **"):
            break
        if stripped.strip():
            lines.append(stripped.strip().lstrip("- ").strip("`"))
    return tuple(lines)


def _assert_product_grounded_stage_artifacts(queue_spec_text: str, phase_spec_text: str) -> None:
    queue_lower = queue_spec_text.casefold()
    phase_lower = phase_spec_text.casefold()

    assert "## Product Surfaces" in queue_spec_text
    assert "## Governance Artifacts" in queue_spec_text
    assert "## Implementation Surfaces" in phase_spec_text
    assert "## Verification Surfaces" in phase_spec_text
    assert "goalspec draft package" not in queue_lower
    assert "task queue maintenance" not in queue_lower
    assert "reviewable runtime implementation slice" not in phase_lower
    assert "implement the first bounded capability slice" not in phase_lower
    assert "add or update proof" not in phase_lower
    assert "close this phase with bounded handoff evidence" not in phase_lower


def _assert_product_first_task_cards(cards: object) -> None:
    bodies = [card.body for card in cards]
    assert bodies

    for body in bodies:
        files_to_touch = _field_block_lines(body, "Files to touch")
        assert files_to_touch
        assert any(not path.startswith("agents/") for path in files_to_touch)
        assert not all(path.startswith("agents/") for path in files_to_touch)
        lowered = body.casefold()
        assert "goalspec draft package" not in lowered
        assert "task queue maintenance" not in lowered
        assert "implement the first bounded capability slice" not in lowered
        assert "add or update proof" not in lowered
        assert "close this phase with bounded handoff evidence" not in lowered


def _goal_queue_checkpoint(
    *,
    run_id: str,
    emitted_at: datetime,
    queue_path: Path,
    item_path: Path,
    owner_token: str | None = None,
    status: ResearchStatus = ResearchStatus.GOALSPEC_RUNNING,
    node_id: str = "goal_intake",
    stage_kind_id: str = "research.goal-intake",
) -> ResearchCheckpoint:
    return ResearchCheckpoint(
        checkpoint_id=run_id,
        mode=ResearchRuntimeMode.GOALSPEC,
        status=status,
        node_id=node_id,
        stage_kind_id=stage_kind_id,
        started_at=emitted_at,
        updated_at=emitted_at,
        owned_queues=(
            ResearchQueueOwnership(
                family=ResearchQueueFamily.GOALSPEC,
                queue_path=queue_path,
                item_path=item_path,
                owner_token=run_id if owner_token is None else owner_token,
                acquired_at=emitted_at,
            ),
        ),
    )


def _goal_active_request_checkpoint(
    *,
    run_id: str,
    emitted_at: datetime,
    path: Path,
    status: ResearchStatus = ResearchStatus.GOALSPEC_RUNNING,
    node_id: str,
    stage_kind_id: str,
) -> ResearchCheckpoint:
    return ResearchCheckpoint(
        checkpoint_id=run_id,
        mode=ResearchRuntimeMode.GOALSPEC,
        status=status,
        node_id=node_id,
        stage_kind_id=stage_kind_id,
        started_at=emitted_at,
        updated_at=emitted_at,
        active_request={
            "event_type": EventType.IDEA_SUBMITTED,
            "received_at": emitted_at,
            "payload": {"path": path.as_posix()},
            "queue_family": ResearchQueueFamily.GOALSPEC,
        },
    )


def _prepare_reviewed_spec_for_taskmaster(
    tmp_path: Path,
    *,
    run_id: str,
    emitted_at: datetime,
    title: str,
    body: str,
    decomposition_profile: str = "simple",
    idea_id: str | None = None,
) -> tuple[Path, object, object, object, Path]:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    suffix = run_id.rsplit("-", 1)[-1]
    resolved_idea_id = idea_id or f"IDEA-{suffix.upper()}"
    goal_text = (
        "---\n"
        f"idea_id: {resolved_idea_id}\n"
        f"title: {title}\n"
        f"decomposition_profile: {decomposition_profile}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )
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
    execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="objective_profile_sync",
            stage_kind_id="research.objective-profile-sync",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
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
    )
    synthesis = execute_spec_synthesis(
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
        completion_manifest=completion_manifest.draft_state,
        emitted_at=emitted_at,
    )
    queue_spec_path = workspace / synthesis.queue_spec_path
    review = execute_spec_review(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=queue_spec_path.parent,
            item_path=queue_spec_path,
            status=ResearchStatus.SPEC_REVIEW_RUNNING,
            node_id="spec_review",
            stage_kind_id="research.spec-review",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    reviewed_path = workspace / review.reviewed_path
    return workspace, config, paths, synthesis, reviewed_path


def _prepare_emitted_spec_family(
    tmp_path: Path,
    *,
    run_id: str,
    emitted_at: datetime,
    title: str,
    body: str,
    decomposition_profile: str = "simple",
    idea_id: str | None = None,
) -> tuple[Path, object, object, object, Path, Path]:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    suffix = run_id.rsplit("-", 1)[-1]
    resolved_idea_id = idea_id or f"IDEA-{suffix.upper()}"
    goal_text = (
        "---\n"
        f"idea_id: {resolved_idea_id}\n"
        f"title: {title}\n"
        f"decomposition_profile: {decomposition_profile}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )
    _write_queue_file(raw_goal_path, goal_text)

    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=raw_goal_path.parent,
            item_path=raw_goal_path,
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    staged_path = workspace / goal_intake.research_brief_path
    execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="objective_profile_sync",
            stage_kind_id="research.objective-profile-sync",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
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
    )
    synthesis = execute_spec_synthesis(
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
        completion_manifest=completion_manifest.draft_state,
        emitted_at=emitted_at,
    )
    return workspace, config, paths, synthesis, workspace / synthesis.queue_spec_path, staged_path


def _configure_auto_blocker_runtime(
    tmp_path: Path,
    *,
    title: str,
    goal: str,
    acceptance: str,
) -> tuple[Path, Path, object, object, TaskQueue, object]:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('mode = "stub"', 'mode = "auto"', 1),
        encoding="utf-8",
    )
    (workspace / "agents" / "tasks.md").write_text(
        "\n".join(
            [
                "# Active Task",
                "",
                f"## 2026-03-19 - {title}",
                "",
                f"- **Goal:** {goal}",
                f"- **Acceptance:** {acceptance}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    queue = TaskQueue(paths)
    active_task = queue.active_task()
    assert active_task is not None
    return workspace, config_path, loaded, paths, queue, active_task


def _quarantine_blocker_with_handoff(
    queue: TaskQueue,
    active_task: object,
    *,
    workspace: Path,
    incident_path: str,
    diagnostics_name: str,
    handoff_id: str,
    parent_run_id: str,
    frozen_plan_id: str,
    frozen_plan_hash: str,
    snapshot_id: str | None = None,
    reason: str = "Consult exhausted the local path",
    task_id: str | None = None,
    task_title: str | None = None,
    recovery_batch_id: str | None = None,
    failure_signature: str | None = None,
) -> tuple[Path, Path, object, ExecutionResearchHandoff]:
    diagnostics_dir = workspace / "agents" / "diagnostics" / diagnostics_name
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    latch = queue.quarantine(
        active_task,
        reason,
        Path(incident_path),
        diagnostics_dir=diagnostics_dir,
    )
    handoff = ExecutionResearchHandoff(
        handoff_id=handoff_id,
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id=parent_run_id,
            snapshot_id=snapshot_id or f"snapshot-{parent_run_id}",
            frozen_plan_id=frozen_plan_id,
            frozen_plan_hash=frozen_plan_hash,
            transition_history_path=Path(f"agents/runs/{parent_run_id}/transition_history.jsonl"),
        ),
        task_id=active_task.task_id if task_id is None else task_id,
        task_title=active_task.title if task_title is None else task_title,
        stage="Consult",
        reason=reason,
        incident_path=latch.incident_path,
        diagnostics_dir=diagnostics_dir,
        recovery_batch_id=latch.batch_id if recovery_batch_id is None else recovery_batch_id,
        failure_signature=latch.failure_signature if failure_signature is None else failure_signature,
        frozen_backlog_cards=latch.frozen_backlog_cards,
        retained_backlog_cards=latch.retained_backlog_cards,
    )
    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    latch_path.write_text(
        latch.model_copy(update={"handoff": handoff}).model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    return diagnostics_dir, latch_path, latch, handoff


def _blocker_deferred_request(
    *,
    task_id: str,
    received_at: str,
    handoff: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_type": EventType.NEEDS_RESEARCH.value,
        "received_at": received_at,
        "payload": {"task_id": task_id},
        "queue_family": ResearchQueueFamily.BLOCKER.value,
    }
    if handoff is not None:
        payload["handoff"] = handoff
    return payload


def _write_research_runtime_state(
    workspace: Path,
    *,
    deferred_requests: list[dict[str, object]],
) -> None:
    _write_json_file(
        workspace / "agents/research_state.json",
        {
            "schema_version": "1.0",
            "updated_at": "2026-03-19T12:00:00Z",
            "current_mode": "AUTO",
            "last_mode": "STUB",
            "mode_reason": "research-plane-initialized",
            "cycle_count": 0,
            "transition_count": 0,
            "deferred_requests": deferred_requests,
            "queue_snapshot": {
                "goalspec_ready": False,
                "incident_ready": False,
                "blocker_ready": True,
                "audit_ready": False,
                "selected_family": None,
                "ownerships": [],
                "last_scanned_at": "2026-03-19T12:00:00Z",
            },
        },
    )


def _write_incident_file(
    path: Path,
    *,
    incident_id: str,
    title: str,
    status: str = "incoming",
    severity: str = "S2",
    summary: str = "Preserve incident lineage.",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"incident_id: {incident_id}",
                f"status: {status}",
                f"severity: {severity}",
                "opened_at: 2026-03-21T12:00:00Z",
                "updated_at: 2026-03-21T12:05:00Z",
                "---",
                "",
                f"# {title}",
                "",
                "## Summary",
                f"- {summary}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_audit_file(
    path: Path,
    *,
    audit_id: str,
    trigger: str = "manual",
    status: str = "incoming",
    scope: str = "manual-audit",
    commands: list[str] | None = None,
    summaries: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"audit_id: {audit_id}",
        f"scope: {scope}",
        f"trigger: {trigger}",
        f"status: {status}",
        "owner: qa",
        "created_at: 2026-03-21T12:00:00Z",
        "updated_at: 2026-03-21T12:05:00Z",
        "---",
        "",
        f"# Audit {audit_id}",
        "",
        "## Objective",
        "- Validate the queue contract.",
        "",
    ]
    if commands:
        lines.extend(["## Commands", *[f"- {command}" for command in commands], ""])
    if summaries:
        lines.extend(["## Summary", *[f"- {summary}" for summary in summaries], ""])
    path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _write_completion_manifest(
    workspace: Path,
    *,
    configured: bool,
    commands: list[str] | None = None,
    profile_id: str = "completion.manifest.test",
    notes: list[str] | None = None,
) -> None:
    manifest_path = workspace / "agents" / "audit" / "completion_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    required_commands = []
    for index, command in enumerate(commands or (), start=1):
        required_commands.append(
            {
                "id": f"cmd-{index}",
                "required": True,
                "category": "quality",
                "timeout_secs": 300,
                "command": command,
            }
        )
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "profile_id": profile_id,
                "configured": configured,
                "notes": notes
                or (
                    ["Configured for deterministic audit completion coverage."]
                    if configured
                    else ["Deliberately left unconfigured for fail-closed coverage."]
                ),
                "required_completion_commands": required_commands,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_typed_objective_contract(
    workspace: Path,
    *,
    profile_id: str = "goal-profile-legacy",
    goal_id: str = "IDEA-LEGACY-001",
    title: str = "Legacy workspace objective",
    source_path: str = "agents/ideas/raw/goal.md",
    updated_at: str = "2026-03-21T12:05:00Z",
    require_open_gaps_zero: bool = True,
    semantic_milestones: list[dict[str, object]] | None = None,
) -> None:
    contract_path = workspace / "agents" / "objective" / "contract.yaml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "objective_id": goal_id,
                "objective_root": ".",
                "completion": {
                    "authoritative_decision_file": "agents/reports/completion_decision.json",
                    "fallback_decision_file": "agents/reports/audit_gate_decision.json",
                    "require_task_store_cards_zero": True,
                    "require_open_gaps_zero": require_open_gaps_zero,
                },
                "objective_profile": {
                    "profile_id": profile_id,
                    "title": title,
                    "source_path": source_path,
                    "updated_at": updated_at,
                    "semantic_profile": {
                        "milestones": [] if semantic_milestones is None else semantic_milestones,
                    },
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_malformed_typed_objective_contract(
    workspace: Path,
    *,
    objective_id: str = "OBJ-BROKEN-001",
    completion: dict[str, object] | None = None,
) -> None:
    contract_path = workspace / "agents" / "objective" / "contract.yaml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "objective_id": objective_id,
                "objective_root": ".",
                "completion": {} if completion is None else completion,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_gaps_file(
    workspace: Path,
    *,
    open_gap_count: int = 0,
    open_rows: list[dict[str, str]] | None = None,
) -> None:
    gaps_path = workspace / "agents" / "gaps.md"
    lines = [
        "# Gaps",
        "",
        "## Open Gaps",
        "",
        "| Gap ID | Title | Area | Owner | Severity | Status | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if open_rows is not None:
        for index, row in enumerate(open_rows, start=1):
            lines.append(
                "| {gap_id} | {title} | {area} | {owner} | {severity} | open | {notes} |".format(
                    gap_id=row.get("gap_id", f"GAP-{index:03d}"),
                    title=row.get("title", f"Open issue {index}"),
                    area=row.get("area", "runtime"),
                    owner=row.get("owner", "qa"),
                    severity=row.get("severity", "S2"),
                    notes=row.get("notes", "Needs closure"),
                )
            )
    else:
        for index in range(open_gap_count):
            lines.append(
                f"| GAP-{index + 1:03d} | Open issue {index + 1} | runtime | qa | S2 | open | Needs closure |"
            )
    lines.extend(["", "## Closed Gaps", ""])
    gaps_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _persist_research_mode(config_path: Path, mode: ResearchMode) -> None:
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace('mode = "stub"', f'mode = "{mode.value}"', 1), encoding="utf-8")


def _write_update_stage_driver(tmp_path: Path) -> Path:
    script = tmp_path / "update_stage_driver.py"
    script.write_text(
        "\n".join(
            [
                "print('Update performed maintenance only')",
                "print('### UPDATE_COMPLETE')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return script


def test_auto_selection_uses_auto_mode_and_goal_family(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "raw" / "goal.md")

    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)

    assert selection is not None
    assert selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
    assert selection.reason == "goal-or-spec-queue-ready"
    assert selection.selected_mode_ref.id == "mode.research_goalspec"
    assert selection.queue_snapshot.selected_family is ResearchQueueFamily.GOALSPEC


@pytest.mark.parametrize(
    ("queue_relative_path", "body", "expected_entry_node"),
    [
        (
            "agents/ideas/raw/goal.md",
            "---\nidea_id: IDEA-ENTRY-RAW\ntitle: Raw Goal\n---\n\n# Raw Goal\n\nRaw seed.\n",
            "goal_intake",
        ),
        (
            "agents/ideas/staging/IDEA-ENTRY-STAGE__staged-goal.md",
            "---\nidea_id: IDEA-ENTRY-STAGE\ntitle: Staged Goal\n---\n\n# Staged Goal\n\nStaged brief.\n",
            "objective_profile_sync",
        ),
        (
            "agents/ideas/specs/SPEC-ENTRY-001__queued-spec.md",
            "---\nspec_id: SPEC-ENTRY-001\ntitle: Queued Spec\n---\n\n# Queued Spec\n\nQueued review work.\n",
            "spec_review",
        ),
        (
            "agents/ideas/specs_reviewed/SPEC-ENTRY-002__reviewed-spec.md",
            "---\nspec_id: SPEC-ENTRY-002\ntitle: Reviewed Spec\n---\n\n# Reviewed Spec\n\nReady for taskmaster.\n",
            "taskmaster",
        ),
    ],
)
def test_goalspec_queue_roots_route_to_owned_entry_stage(
    tmp_path: Path,
    queue_relative_path: str,
    body: str,
    expected_entry_node: str,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / queue_relative_path, body)

    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)

    assert selection is not None
    assert selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
    assert selection.entry_node_id == expected_entry_node

    dispatch = compile_research_dispatch(
        paths,
        selection,
        run_id=f"route-{expected_entry_node}",
        queue_discovery=discovery,
        resolve_assets=False,
    )

    assert dispatch.entry_stage.node_id == expected_entry_node
    assert dispatch.checkpoint().node_id == expected_entry_node


@pytest.mark.parametrize(
    ("mode", "expected_mode_id", "expected_entry_node"),
    [
        (ResearchMode.GOALSPEC, "mode.research_goalspec", "goal_intake"),
        (ResearchMode.INCIDENT, "mode.research_incident", "incident_intake"),
        (ResearchMode.AUDIT, "mode.research_audit", "audit_intake"),
    ],
)
def test_forced_modes_compile_requested_research_plan(
    tmp_path: Path,
    mode: ResearchMode,
    expected_mode_id: str,
    expected_entry_node: str,
) -> None:
    _, config, paths = _configured_runtime(tmp_path, mode=mode)

    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)

    assert selection is not None
    assert selection.runtime_mode is ResearchRuntimeMode.from_value(mode)
    assert selection.reason == "forced-by-config"
    assert selection.selected_mode_ref.id == expected_mode_id

    dispatch = compile_research_dispatch(
        paths,
        selection,
        run_id=f"forced-{mode.value}",
        queue_discovery=discovery,
        resolve_assets=False,
    )

    assert dispatch.research_plan.entry_node_id == expected_entry_node
    assert dispatch.compile_result.plan is not None
    assert dispatch.compile_result.plan.content.selected_mode_ref is not None
    assert dispatch.compile_result.plan.content.selected_mode_ref.id == expected_mode_id


def test_auto_selection_prefers_goalspec_over_audit_when_both_are_ready(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "raw" / "goal.md")
    _write_queue_file(workspace / "agents" / "ideas" / "audit" / "incoming" / "audit.md")

    plane = ResearchPlane(config, paths)
    dispatch = plane.dispatch_ready_work(run_id="research-auto-mixed-run", resolve_assets=False)

    snapshot = plane.snapshot_state()
    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.GOALSPEC
    assert dispatch.research_plan.entry_node_id == "goal_intake"
    assert plane.active_dispatch() is not None
    assert snapshot.queue_snapshot.goalspec_ready is True
    assert snapshot.queue_snapshot.audit_ready is True
    assert snapshot.current_mode is ResearchRuntimeMode.GOALSPEC
    assert snapshot.mode_reason == "goal-or-spec-queue-ready"
    assert snapshot.queue_snapshot.selected_family is ResearchQueueFamily.GOALSPEC
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "goal_intake"
    assert plane.status_store.read() is ResearchStatus.GOAL_INTAKE_RUNNING


def test_research_plane_dispatches_compiled_auto_plan(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md")
    plane = ResearchPlane(config, paths)

    dispatch = plane.dispatch_ready_work(run_id="research-auto-run", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.INCIDENT
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.INCIDENT
    assert dispatch.research_plan.entry_node_id == "incident_intake"

    snapshot = plane.snapshot_state()
    assert snapshot.current_mode is ResearchRuntimeMode.INCIDENT
    assert snapshot.mode_reason == "incident-queue-ready"
    assert snapshot.queue_snapshot.selected_family is ResearchQueueFamily.INCIDENT
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.loop_ref is not None
    assert snapshot.checkpoint.loop_ref.id == "research.incident"
    assert snapshot.checkpoint.node_id == "incident_intake"
    assert snapshot.checkpoint.stage_kind_id == "research.incident-intake"
    assert snapshot.deferred_requests == ()
    assert entry_stage_type_for_dispatch(dispatch).value == "incident_intake"


def test_research_plane_run_ready_work_executes_auto_incident_stages_through_archive(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    incoming_path = workspace / "agents" / "ideas" / "incidents" / "incoming" / "INC-AUTO-001.md"
    working_path = workspace / "agents" / "ideas" / "incidents" / "working" / "INC-AUTO-001.md"
    resolved_path = workspace / "agents" / "ideas" / "incidents" / "resolved" / "INC-AUTO-001.md"
    archived_path = workspace / "agents" / "ideas" / "incidents" / "archived" / "INC-AUTO-001.md"
    _write_incident_file(
        incoming_path,
        incident_id="INC-AUTO-001",
        title="Auto incident runtime execution",
        summary="Advance one incident through archive deterministically.",
    )
    plane = ResearchPlane(config, paths)

    dispatch = plane.run_ready_work(run_id="incident-auto-run", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.INCIDENT
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.INCIDENT
    snapshot = plane.snapshot_state()
    assert snapshot.current_mode is ResearchRuntimeMode.AUTO
    assert snapshot.checkpoint is None
    assert snapshot.lock_state is None
    assert snapshot.queue_snapshot.selected_family is None
    assert snapshot.queue_snapshot.ownerships == ()
    assert plane.status_store.read() is ResearchStatus.IDLE
    assert not incoming_path.exists()
    assert not working_path.exists()
    assert not resolved_path.exists()
    assert archived_path.exists()

    intake_record_path = paths.research_runtime_dir / "incidents" / "intake" / "incident-auto-run.json"
    resolve_record_path = paths.research_runtime_dir / "incidents" / "resolve" / "incident-auto-run.json"
    remediation_record_path = paths.research_runtime_dir / "incidents" / "remediation" / "incident-auto-run.json"
    archive_record_path = paths.research_runtime_dir / "incidents" / "archive" / "incident-auto-run.json"
    lineage_path = paths.research_runtime_dir / "incidents" / "lineage" / "inc-auto-001.json"
    taskmaster_record_path = paths.research_runtime_dir / "goalspec" / "taskmaster" / "incident-auto-run.json"
    taskaudit_record_path = paths.research_runtime_dir / "goalspec" / "taskaudit" / "incident-auto-run.json"
    task_provenance_path = workspace / "agents" / "task_provenance.json"

    intake_record = json.loads(intake_record_path.read_text(encoding="utf-8"))
    resolve_record = json.loads(resolve_record_path.read_text(encoding="utf-8"))
    remediation_record = json.loads(remediation_record_path.read_text(encoding="utf-8"))
    archive_record = json.loads(archive_record_path.read_text(encoding="utf-8"))
    lineage_record = json.loads(lineage_path.read_text(encoding="utf-8"))
    taskmaster_record = json.loads(taskmaster_record_path.read_text(encoding="utf-8"))
    taskaudit_record = json.loads(taskaudit_record_path.read_text(encoding="utf-8"))
    task_provenance = json.loads(task_provenance_path.read_text(encoding="utf-8"))
    backlog = parse_task_store((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))

    assert intake_record["working_path"] == "agents/ideas/incidents/working/INC-AUTO-001.md"
    assert resolve_record["resolved_path"] == "agents/ideas/incidents/resolved/INC-AUTO-001.md"
    assert resolve_record["remediation_record_path"] == "agents/.research_runtime/incidents/remediation/incident-auto-run.json"
    assert resolve_record["remediation_spec_id"] == "SPEC-INC-AUTO-001"
    assert archive_record["archived_path"] == "agents/ideas/incidents/archived/INC-AUTO-001.md"
    assert archive_record["evidence_paths"] == [
        "agents/.research_runtime/incidents/intake/incident-auto-run.json",
        "agents/.research_runtime/incidents/resolve/incident-auto-run.json",
        "agents/.research_runtime/incidents/remediation/incident-auto-run.json",
        "agents/.research_runtime/goalspec/taskmaster/incident-auto-run.json",
        "agents/.research_runtime/goalspec/taskaudit/incident-auto-run.json",
        "agents/task_provenance.json",
        "agents/.research_runtime/incidents/lineage/inc-auto-001.json",
    ]
    assert remediation_record["incident_id"] == "INC-AUTO-001"
    assert remediation_record["resolved_path"] == "agents/ideas/incidents/resolved/INC-AUTO-001.md"
    assert remediation_record["fix_spec"]["spec_id"] == "SPEC-INC-AUTO-001"
    assert remediation_record["fix_spec"]["reviewed_path"] == "agents/ideas/specs_reviewed/SPEC-INC-AUTO-001__auto-incident-runtime-execution-remediation.md"
    assert remediation_record["taskmaster_record_path"] == "agents/.research_runtime/goalspec/taskmaster/incident-auto-run.json"
    assert remediation_record["taskaudit_record_path"] == "agents/.research_runtime/goalspec/taskaudit/incident-auto-run.json"
    assert remediation_record["task_provenance_path"] == "agents/task_provenance.json"
    assert lineage_record["source_path"] == "agents/ideas/incidents/incoming/INC-AUTO-001.md"
    assert lineage_record["working_path"] == "agents/ideas/incidents/working/INC-AUTO-001.md"
    assert lineage_record["resolved_path"] == "agents/ideas/incidents/resolved/INC-AUTO-001.md"
    assert lineage_record["archived_path"] == "agents/ideas/incidents/archived/INC-AUTO-001.md"
    assert lineage_record["current_path"] == "agents/ideas/incidents/archived/INC-AUTO-001.md"
    assert lineage_record["remediation_spec_id"] == "SPEC-INC-AUTO-001"
    assert lineage_record["remediation_record_path"] == "agents/.research_runtime/incidents/remediation/incident-auto-run.json"
    assert lineage_record["last_stage"] == "incident_archive"
    assert taskmaster_record["spec_id"] == "SPEC-INC-AUTO-001"
    assert taskmaster_record["profile_selection"]["selected_mode_ref"]["id"] == "mode.research_incident"
    assert taskaudit_record["merged_spec_ids"] == ["SPEC-INC-AUTO-001"]
    assert task_provenance["taskaudit"]["record_path"] == "agents/.research_runtime/goalspec/taskaudit/incident-auto-run.json"
    assert len(backlog.cards) == 3
    assert all(card.spec_id == "SPEC-INC-AUTO-001" for card in backlog.cards)


def test_research_plane_run_ready_work_defers_auto_goalspec_after_goal_intake(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    raw_goal_text = (
        "---\n"
        "idea_id: IDEA-AUTO-42\n"
        "title: Modernize Goal Intake\n"
        "decomposition_profile: moderate\n"
        "---\n\n"
        "# Modernize Goal Intake\n\n"
        "Create real GoalSpec intake and objective sync stages.\n"
    )
    _write_queue_file(raw_goal_path, raw_goal_text)
    plane = ResearchPlane(config, paths)

    dispatch = plane.run_ready_work(run_id="goalspec-auto-run", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.GOALSPEC
    snapshot = plane.snapshot_state()
    assert snapshot.current_mode is ResearchRuntimeMode.GOALSPEC
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "objective_profile_sync"
    assert snapshot.lock_state is None
    assert snapshot.queue_snapshot.selected_family is ResearchQueueFamily.GOALSPEC
    assert snapshot.queue_snapshot.ownerships != ()
    assert plane.status_store.read() is ResearchStatus.OBJECTIVE_PROFILE_SYNC_RUNNING

    archived_rel_path = (
        "agents/ideas/archive/raw/"
        f"goal__goalspec-auto-run__{sha256(raw_goal_text.encode('utf-8')).hexdigest()[:12]}.md"
    )
    assert not raw_goal_path.exists()
    assert (workspace / archived_rel_path).exists()
    assert (
        workspace / "agents" / ".research_runtime" / "goalspec" / "goal_intake" / "goalspec-auto-run.json"
    ).exists()
    assert not (
        workspace / "agents" / ".research_runtime" / "goalspec" / "taskmaster" / "goalspec-auto-run.json"
    ).exists()
    assert not (
        workspace / "agents" / ".research_runtime" / "goalspec" / "taskaudit" / "goalspec-auto-run.json"
    ).exists()
    backlog = parse_task_store((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))
    assert backlog.cards == []


def test_research_plane_defers_completion_manifest_and_synthesis_into_later_goal_spec_passes(
    tmp_path: Path,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    _write_queue_file(
        raw_goal_path,
        "---\n"
        "idea_id: IDEA-CADENCE-42\n"
        "title: Support Ticket Service\n"
        "---\n\n"
        "# Support Ticket Service\n\n"
        "Build the first usable support-ticket web app with ticket intake and assignment.\n",
    )
    plane = ResearchPlane(config, paths)

    first_dispatch = plane.sync_runtime(trigger="engine-start", run_id="goalspec-cadence-42", resolve_assets=False)

    assert first_dispatch is not None
    first_snapshot = plane.snapshot_state()
    assert first_snapshot.checkpoint is not None
    assert first_snapshot.checkpoint.node_id == "objective_profile_sync"
    assert plane.status_store.read() is ResearchStatus.OBJECTIVE_PROFILE_SYNC_RUNNING
    assert not paths.audit_completion_manifest_file.exists()

    second_dispatch = plane.run_ready_work(run_id="goalspec-cadence-42", resolve_assets=False)

    assert second_dispatch is not None
    second_snapshot = plane.snapshot_state()
    assert second_snapshot.checkpoint is not None
    assert second_snapshot.checkpoint.node_id == "completion_manifest_draft"
    assert plane.status_store.read() is ResearchStatus.COMPLETION_MANIFEST_RUNNING
    assert paths.objective_profile_sync_state_file.exists()
    assert not paths.audit_completion_manifest_file.exists()
    assert not (paths.goalspec_spec_synthesis_records_dir / "goalspec-cadence-42.json").exists()

    third_dispatch = plane.run_ready_work(run_id="goalspec-cadence-42", resolve_assets=False)

    assert third_dispatch is not None
    third_snapshot = plane.snapshot_state()
    assert third_snapshot.checkpoint is not None
    assert third_snapshot.checkpoint.node_id == "spec_synthesis"
    assert plane.status_store.read() is ResearchStatus.SPEC_SYNTHESIS_RUNNING
    assert paths.audit_completion_manifest_file.exists()
    assert (paths.goalspec_completion_manifest_records_dir / "goalspec-cadence-42.json").exists()
    assert not (paths.goalspec_spec_synthesis_records_dir / "goalspec-cadence-42.json").exists()


def test_research_plane_staging_dispatch_never_reenters_goal_intake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    staged_path = workspace / "agents" / "ideas" / "staging" / "IDEA-STAGE-LOOP__staged-goal.md"
    _write_queue_file(
        staged_path,
        "---\nidea_id: IDEA-STAGE-LOOP\ntitle: Stage Resume Goal\n---\n\n# Stage Resume Goal\n\nResume downstream.\n",
    )
    plane = ResearchPlane(config, paths)

    def _unexpected_goal_intake(*args: object, **kwargs: object) -> object:
        raise AssertionError("staging queue item unexpectedly re-entered goal_intake")

    def _expected_objective_profile_sync_failure(*args: object, **kwargs: object) -> object:
        raise research_plane_module.GoalSpecExecutionError("synthetic objective profile sync failure")

    monkeypatch.setattr(research_plane_module, "execute_goal_intake", _unexpected_goal_intake)
    monkeypatch.setattr(
        research_plane_module,
        "execute_objective_profile_sync",
        _expected_objective_profile_sync_failure,
    )

    with pytest.raises(research_plane_module.GoalSpecExecutionError, match="synthetic objective profile sync failure"):
        plane.run_ready_work(run_id="goalspec-stage-loop", resolve_assets=False)

    snapshot = plane.snapshot_state()
    assert snapshot.current_mode is ResearchRuntimeMode.GOALSPEC
    assert snapshot.retry_state is not None
    assert snapshot.retry_state.last_failure_reason == "synthetic objective profile sync failure"
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "objective_profile_sync"
    assert plane.status_store.read() is ResearchStatus.BLOCKED


def test_research_plane_run_ready_work_preserves_blocker_handoff_lineage_on_incident_queue_execution(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('mode = "stub"', 'mode = "auto"', 1),
        encoding="utf-8",
    )
    (workspace / "agents" / "tasks.md").write_text(
        "\n".join(
            [
                "# Active Task",
                "",
                "## 2026-03-21 - Preserve blocker lineage",
                "",
                "- **Goal:** Ensure incident execution keeps the originating blocker lineage.",
                "- **Acceptance:** Archive evidence retains the execution parent handoff and blocker ledger origin.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    queue = TaskQueue(paths)
    active_task = queue.active_task()
    assert active_task is not None

    diagnostics_dir = workspace / "agents" / "diagnostics" / "diag-incident-lineage"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    incident_rel_path = Path("agents/ideas/incidents/incoming/INC-HANDOFF-001.md")
    latch = queue.quarantine(
        active_task,
        "Consult exhausted the local path",
        incident_rel_path,
        diagnostics_dir=diagnostics_dir,
    )
    assert not (workspace / incident_rel_path).exists()
    handoff = ExecutionResearchHandoff(
        handoff_id=f"execution-run-incident:needs_research:{latch.batch_id}",
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id="execution-run-incident",
            snapshot_id="snapshot-execution-run-incident",
            frozen_plan_id="frozen-plan:incident123",
            frozen_plan_hash="incident123",
            transition_history_path=Path("agents/runs/execution-run-incident/transition_history.jsonl"),
        ),
        task_id=active_task.task_id,
        task_title=active_task.title,
        stage="Consult",
        reason="Consult exhausted the local path",
        incident_path=latch.incident_path,
        diagnostics_dir=diagnostics_dir,
        recovery_batch_id=latch.batch_id,
        failure_signature=latch.failure_signature,
        frozen_backlog_cards=latch.frozen_backlog_cards,
        retained_backlog_cards=latch.retained_backlog_cards,
    )
    paths.research_recovery_latch_file.write_text(
        latch.model_copy(update={"handoff": handoff}).model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    plane = ResearchPlane(loaded.config, paths)

    dispatch = plane.run_ready_work(run_id="incident-handoff-run", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.INCIDENT
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.INCIDENT
    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is None
    assert snapshot.deferred_requests == ()
    assert plane.status_store.read() is ResearchStatus.IDLE
    archived_path = workspace / "agents" / "ideas" / "incidents" / "archived" / "INC-HANDOFF-001.md"
    assert archived_path.exists()
    archived_text = archived_path.read_text(encoding="utf-8")
    assert "Consult returned `NEEDS_RESEARCH` during `Consult`." in archived_text
    assert "Consult exhausted the local path" in archived_text

    archive_record = json.loads(
        (paths.research_runtime_dir / "incidents" / "archive" / "incident-handoff-run.json").read_text(
            encoding="utf-8"
        )
    )
    lineage_record = json.loads(
        (paths.research_runtime_dir / "incidents" / "lineage" / "inc-handoff-001.json").read_text(
            encoding="utf-8"
        )
    )

    assert archive_record["parent_handoff_id"] == handoff.handoff_id
    assert archive_record["parent_run_id"] == "execution-run-incident"
    assert archive_record["archived_path"] == "agents/ideas/incidents/archived/INC-HANDOFF-001.md"
    assert lineage_record["parent_handoff_id"] == handoff.handoff_id
    assert lineage_record["parent_run_id"] == "execution-run-incident"
    assert lineage_record["source_task"] == f"agents/tasks.md :: {active_task.heading}"
    assert lineage_record["blocker_ledger_path"] == "agents/tasksblocker.md"
    assert lineage_record["blocker_item_key"].startswith("agents/tasksblocker.md#")
    assert lineage_record["source_path"] == "agents/ideas/incidents/incoming/INC-HANDOFF-001.md"
    persisted_latch = load_research_recovery_latch(paths.research_recovery_latch_file)
    assert persisted_latch is not None
    assert persisted_latch.handoff == handoff
    assert persisted_latch.remediation_decision is not None
    assert persisted_latch.remediation_decision.decision_type == "regenerated_backlog_work"
    assert persisted_latch.remediation_decision.remediation_spec_id == "SPEC-INC-HANDOFF-001"
    assert persisted_latch.remediation_decision.remediation_record_path == Path(
        "agents/.research_runtime/incidents/remediation/incident-handoff-run.json"
    )
    assert persisted_latch.remediation_decision.taskaudit_record_path == Path(
        "agents/.research_runtime/goalspec/taskaudit/incident-handoff-run.json"
    )
    assert persisted_latch.remediation_decision.task_provenance_path == Path("agents/task_provenance.json")
    assert persisted_latch.remediation_decision.lineage_path == Path(
        "agents/.research_runtime/incidents/lineage/inc-handoff-001.json"
    )


def test_research_report_marks_incident_recovery_as_stalled_when_regenerated_family_work_disappears(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('mode = "stub"', 'mode = "auto"', 1),
        encoding="utf-8",
    )
    (workspace / "agents" / "tasks.md").write_text(
        "\n".join(
            [
                "# Active Task",
                "",
                "## 2026-03-21 - Stalled incident recovery report",
                "",
                "- **Goal:** Show governance visibility when regenerated family work disappears.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    queue = TaskQueue(paths)
    active_task = queue.active_task()
    assert active_task is not None

    diagnostics_dir = workspace / "agents" / "diagnostics" / "diag-stalled-recovery"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    incident_rel_path = Path("agents/ideas/incidents/incoming/INC-STALLED-001.md")
    latch = queue.quarantine(
        active_task,
        "Consult exhausted the local path",
        incident_rel_path,
        diagnostics_dir=diagnostics_dir,
    )
    _write_incident_file(
        workspace / incident_rel_path,
        incident_id="INC-STALLED-001",
        title="Regenerated family disappeared",
        summary="Exercise progress watchdog visibility for missing regenerated family work.",
    )
    handoff = ExecutionResearchHandoff(
        handoff_id=f"execution-run-stalled:needs_research:{latch.batch_id}",
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id="execution-run-stalled",
            snapshot_id="snapshot-execution-run-stalled",
            frozen_plan_id="frozen-plan:stalled123",
            frozen_plan_hash="stalled123",
            transition_history_path=Path("agents/runs/execution-run-stalled/transition_history.jsonl"),
        ),
        task_id=active_task.task_id,
        task_title=active_task.title,
        stage="Consult",
        reason="Consult exhausted the local path",
        incident_path=latch.incident_path,
        diagnostics_dir=diagnostics_dir,
        recovery_batch_id=latch.batch_id,
        failure_signature=latch.failure_signature,
        frozen_backlog_cards=latch.frozen_backlog_cards,
        retained_backlog_cards=latch.retained_backlog_cards,
    )
    paths.research_recovery_latch_file.write_text(
        latch.model_copy(update={"handoff": handoff}).model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )

    plane = ResearchPlane(loaded.config, paths)
    dispatch = plane.run_ready_work(run_id="incident-stalled-run", resolve_assets=False)

    assert dispatch is not None
    persisted_latch = load_research_recovery_latch(paths.research_recovery_latch_file)
    assert persisted_latch is not None
    assert persisted_latch.remediation_decision is not None

    (workspace / "agents" / "tasksbacklog.md").write_text("# Task Backlog\n", encoding="utf-8")

    report = EngineControl(config_path).research_report()

    assert report.governance is not None
    assert report.governance.progress_watchdog.status == "stalled"
    assert report.governance.progress_watchdog.recovery_decision_type == "regenerated_backlog_work"
    assert report.governance.progress_watchdog.remediation_spec_id == "SPEC-INC-STALLED-001"
    assert report.governance.progress_watchdog.recovery_regeneration is not None
    assert report.governance.progress_watchdog.recovery_regeneration.status == "manual_only"
    assert (
        report.governance.progress_watchdog.recovery_regeneration.reason
        == "regenerated-family-work-missing"
    )


def test_research_plane_resumes_checkpoint_when_queue_input_disappears(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    incident_path = workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md"
    _write_queue_file(incident_path)
    plane = ResearchPlane(config, paths)

    plane.dispatch_ready_work(run_id="research-auto-run", resolve_assets=False)
    incident_path.unlink()

    dispatch = plane.dispatch_ready_work(resolve_assets=False)

    assert dispatch is not None
    assert dispatch.run_id == "research-auto-run"
    assert plane.active_dispatch() is not None

    snapshot = plane.snapshot_state()
    assert snapshot.current_mode is ResearchRuntimeMode.INCIDENT
    assert snapshot.mode_reason == "resume-from-checkpoint"
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.checkpoint_id == "research-auto-run"
    assert plane.status_store.read() is ResearchStatus.INCIDENT_INTAKE_RUNNING


def test_research_plane_resumes_goalspec_checkpoint_at_owned_downstream_stage(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    staged_path = workspace / "agents" / "ideas" / "staging" / "IDEA-RESUME-001__staged-goal.md"
    _write_queue_file(
        staged_path,
        "---\nidea_id: IDEA-RESUME-001\ntitle: Resume Staged Goal\n---\n\n# Resume Staged Goal\n\nStaged brief.\n",
    )
    plane = ResearchPlane(config, paths)
    emitted_at = _dt("2026-04-07T12:00:00Z")
    checkpoint = _goal_queue_checkpoint(
        run_id="goalspec-stage-resume",
        emitted_at=emitted_at,
        queue_path=paths.ideas_staging_dir,
        item_path=staged_path,
        status=ResearchStatus.OBJECTIVE_PROFILE_SYNC_RUNNING,
        node_id="objective_profile_sync",
        stage_kind_id="research.objective-profile-sync",
    )
    plane.state = plane.state.model_copy(
        update={
            "current_mode": ResearchRuntimeMode.GOALSPEC,
            "queue_snapshot": plane.state.queue_snapshot.model_copy(
                update={
                    "goalspec_ready": True,
                    "selected_family": ResearchQueueFamily.GOALSPEC,
                    "ownerships": checkpoint.owned_queues,
                }
            ),
            "checkpoint": checkpoint,
        }
    )

    dispatch = plane.dispatch_ready_work(resolve_assets=False)

    assert dispatch is not None
    assert dispatch.run_id == "goalspec-stage-resume"
    assert dispatch.selection.entry_node_id == "objective_profile_sync"
    assert dispatch.entry_stage.node_id == "objective_profile_sync"
    snapshot = plane.snapshot_state()
    assert snapshot.mode_reason == "resume-from-checkpoint"
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "objective_profile_sync"
    assert plane.status_store.read() is ResearchStatus.OBJECTIVE_PROFILE_SYNC_RUNNING


def test_research_plane_no_work_tracks_configured_auto_mode(tmp_path: Path) -> None:
    _, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    plane = ResearchPlane(config, paths)

    dispatch = plane.dispatch_ready_work(resolve_assets=False)

    assert dispatch is None
    snapshot = plane.snapshot_state()
    assert snapshot.current_mode is ResearchRuntimeMode.AUTO
    assert snapshot.last_mode is ResearchRuntimeMode.STUB
    assert snapshot.mode_reason == "no-dispatchable-research-work"
    assert snapshot.checkpoint is None
    assert plane.status_store.read() is ResearchStatus.IDLE


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        (ResearchMode.GOALSPEC, "forced-by-config; no-goalspec-queue-ready"),
        (ResearchMode.INCIDENT, "forced-by-config; no-incident-or-blocker-queue-ready"),
        (ResearchMode.AUDIT, "forced-by-config; no-audit-queue-ready"),
    ],
)
def test_research_plane_forced_mode_without_matching_queue_stays_idle(
    tmp_path: Path,
    mode: ResearchMode,
    reason: str,
) -> None:
    _, config, paths = _configured_runtime(tmp_path, mode=mode)
    plane = ResearchPlane(config, paths)

    dispatch = plane.dispatch_ready_work(run_id=f"forced-{mode.value}", resolve_assets=False)

    assert dispatch is None
    assert plane.active_dispatch() is None
    snapshot = plane.snapshot_state()
    assert snapshot.current_mode is ResearchRuntimeMode.from_value(mode)
    assert snapshot.last_mode is ResearchRuntimeMode.STUB
    assert snapshot.mode_reason == reason
    assert snapshot.queue_snapshot.selected_family is None
    assert snapshot.checkpoint is None
    assert plane.status_store.read() is ResearchStatus.IDLE


def test_execute_taskaudit_deferred_prepare_then_merge(tmp_path: Path) -> None:
    workspace, config, paths, _synthesis, reviewed_path = _prepare_reviewed_spec_for_taskmaster(
        tmp_path,
        run_id="goalspec-taskaudit-direct-041",
        emitted_at=_dt("2026-04-08T19:00:00Z"),
        title="Modernize Goal Intake",
        body=(
            "Create real GoalSpec intake and objective sync stages for the Millrace research runtime.\n\n"
            "## Capability Domains\n"
            "- Goal intake artifact capture\n"
            "- Objective profile sync persistence\n\n"
            "## Progression Lines\n"
            "- Progression from raw goal intake to staged product brief to synced objective profile.\n"
        ),
        decomposition_profile="simple",
    )
    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)
    assert selection is not None
    dispatch = compile_research_dispatch(
        paths,
        selection,
        run_id="goalspec-taskaudit-direct-041",
        queue_discovery=discovery,
        resolve_assets=False,
    )

    taskmaster_result = execute_taskmaster(
        paths,
        _goal_queue_checkpoint(
            run_id="goalspec-taskaudit-direct-041",
            emitted_at=_dt("2026-04-08T19:00:00Z"),
            queue_path=reviewed_path.parent,
            item_path=reviewed_path,
            status=ResearchStatus.TASKMASTER_RUNNING,
            node_id="taskmaster",
            stage_kind_id="research.taskmaster",
        ),
        dispatch=dispatch,
        run_id="goalspec-taskaudit-direct-041",
        emitted_at=_dt("2026-04-08T19:00:00Z"),
    )
    shard_path = workspace / taskmaster_result.shard_path
    taskaudit_record_path = workspace / "agents" / ".research_runtime" / "goalspec" / "taskaudit" / "goalspec-taskaudit-direct-041.json"

    prepared = execute_taskaudit(
        paths,
        run_id="goalspec-taskaudit-direct-041",
        emitted_at=_dt("2026-04-08T19:05:00Z"),
        defer_merge=True,
    )

    assert prepared.status == "prepared"
    assert prepared.provenance_path == ""
    assert taskaudit_record_path.exists()
    prepared_record = json.loads(taskaudit_record_path.read_text(encoding="utf-8"))
    assert prepared_record["status"] == "prepared"
    assert prepared_record["pending_card_count"] == 4
    assert parse_task_store(paths.backlog_file.read_text(encoding="utf-8"), source_file=paths.backlog_file).cards == []
    pending_document = parse_task_store(paths.taskspending_file.read_text(encoding="utf-8"), source_file=paths.taskspending_file)
    assert len(pending_document.cards) == 4
    assert shard_path.exists()
    assert not (workspace / "agents" / "task_provenance.json").exists()

    merged = execute_taskaudit(
        paths,
        run_id="goalspec-taskaudit-direct-041",
        emitted_at=_dt("2026-04-08T19:10:00Z"),
        defer_merge=True,
    )

    assert merged.status == "merged"
    assert merged.provenance_path == "agents/task_provenance.json"
    merged_record = json.loads(taskaudit_record_path.read_text(encoding="utf-8"))
    assert merged_record["status"] == "merged"
    assert merged_record["provenance_path"] == "agents/task_provenance.json"
    backlog = parse_task_store(paths.backlog_file.read_text(encoding="utf-8"), source_file=paths.backlog_file)
    assert len(backlog.cards) == 4
    assert paths.taskspending_file.read_text(encoding="utf-8") == "# Tasks Pending\n"
    assert not shard_path.exists()
    task_provenance = json.loads((workspace / "agents" / "task_provenance.json").read_text(encoding="utf-8"))
    assert task_provenance["taskaudit"]["record_path"] == "agents/.research_runtime/goalspec/taskaudit/goalspec-taskaudit-direct-041.json"
    assert task_provenance["taskaudit"]["merged_backlog_card_count"] == 4


def test_execute_taskaudit_marks_promotion_blocked_when_prepared_pending_snapshot_changes(
    tmp_path: Path,
) -> None:
    workspace, config, paths, _synthesis, reviewed_path = _prepare_reviewed_spec_for_taskmaster(
        tmp_path,
        run_id="goalspec-taskaudit-integrity-043",
        emitted_at=_dt("2026-04-08T20:00:00Z"),
        title="Preserve pending to live integrity",
        body=(
            "Exercise the Taskaudit merge conflict path without silently dropping pending work.\n\n"
            "## Capability Domains\n"
            "- Pending shard assembly\n"
            "- Live backlog promotion integrity\n"
        ),
        decomposition_profile="simple",
    )
    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)
    assert selection is not None
    dispatch = compile_research_dispatch(
        paths,
        selection,
        run_id="goalspec-taskaudit-integrity-043",
        queue_discovery=discovery,
        resolve_assets=False,
    )
    taskmaster_result = execute_taskmaster(
        paths,
        _goal_queue_checkpoint(
            run_id="goalspec-taskaudit-integrity-043",
            emitted_at=_dt("2026-04-08T20:00:00Z"),
            queue_path=reviewed_path.parent,
            item_path=reviewed_path,
            status=ResearchStatus.TASKMASTER_RUNNING,
            node_id="taskmaster",
            stage_kind_id="research.taskmaster",
        ),
        dispatch=dispatch,
        run_id="goalspec-taskaudit-integrity-043",
        emitted_at=_dt("2026-04-08T20:00:00Z"),
    )
    shard_path = workspace / taskmaster_result.shard_path
    taskaudit_record_path = (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "taskaudit"
        / "goalspec-taskaudit-integrity-043.json"
    )

    prepared = execute_taskaudit(
        paths,
        run_id="goalspec-taskaudit-integrity-043",
        emitted_at=_dt("2026-04-08T20:05:00Z"),
        defer_merge=True,
    )

    assert prepared.status == "prepared"
    paths.taskspending_file.write_text(
        paths.taskspending_file.read_text(encoding="utf-8") + "\n<!-- drift -->\n",
        encoding="utf-8",
    )

    with pytest.raises(TaskauditExecutionError, match="Taskaudit prepared pending family changed before final merge"):
        execute_taskaudit(
            paths,
            run_id="goalspec-taskaudit-integrity-043",
            emitted_at=_dt("2026-04-08T20:10:00Z"),
            defer_merge=True,
        )

    record = json.loads(taskaudit_record_path.read_text(encoding="utf-8"))
    assert record["status"] == "promotion_blocked"
    assert record["blocked_reason"] == "Taskaudit prepared pending family changed before final merge"
    assert record["blocked_failure_kind"] == "TaskauditExecutionError"
    assert record["blocked_at"] == "2026-04-08T20:10:00Z"
    assert parse_task_store(paths.backlog_file.read_text(encoding="utf-8"), source_file=paths.backlog_file).cards == []
    assert len(parse_task_store(paths.taskspending_file.read_text(encoding="utf-8"), source_file=paths.taskspending_file).cards) == 4
    assert shard_path.exists()
    assert not (workspace / "agents" / "task_provenance.json").exists()


def test_research_plane_defers_taskaudit_merge_into_later_goal_spec_pass(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    _write_queue_file(
        workspace / "agents" / "ideas" / "raw" / "goal.md",
        (
            "---\n"
            "idea_id: IDEA-TASKAUDIT-042\n"
            "title: Modernize Goal Intake\n"
            "decomposition_profile: simple\n"
            "---\n\n"
            "# Modernize Goal Intake\n\n"
            "Create real GoalSpec intake and objective sync stages.\n"
        ),
    )
    plane = ResearchPlane(config, paths)
    run_id = "goalspec-taskaudit-cadence-042"
    taskaudit_record_path = workspace / "agents" / ".research_runtime" / "goalspec" / "taskaudit" / f"{run_id}.json"

    for _ in range(6):
        dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=False)
        assert dispatch is not None

    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "taskmaster"

    dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=False)

    assert dispatch is not None
    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "taskaudit"
    assert plane.status_store.read() is ResearchStatus.TASKAUDIT_RUNNING
    assert not taskaudit_record_path.exists()
    assert parse_task_store(paths.backlog_file.read_text(encoding="utf-8"), source_file=paths.backlog_file).cards == []

    dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=False)

    assert dispatch is not None
    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "taskaudit"
    prepared_record = json.loads(taskaudit_record_path.read_text(encoding="utf-8"))
    assert prepared_record["status"] == "prepared"
    assert parse_task_store(paths.backlog_file.read_text(encoding="utf-8"), source_file=paths.backlog_file).cards == []
    assert len(
        parse_task_store(paths.taskspending_file.read_text(encoding="utf-8"), source_file=paths.taskspending_file).cards
    ) == 4

    dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=False)

    assert dispatch is not None
    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is None
    assert plane.status_store.read() is ResearchStatus.IDLE
    merged_record = json.loads(taskaudit_record_path.read_text(encoding="utf-8"))
    assert merged_record["status"] == "merged"
    assert len(parse_task_store(paths.backlog_file.read_text(encoding="utf-8"), source_file=paths.backlog_file).cards) == 4
    assert paths.taskspending_file.read_text(encoding="utf-8") == "# Tasks Pending\n"


def test_research_plane_surfaces_taskaudit_promotion_integrity_failure_as_blocked(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    _write_queue_file(
        workspace / "agents" / "ideas" / "raw" / "goal.md",
            (
                "---\n"
                "idea_id: IDEA-TASKAUDIT-044\n"
                "title: Preserve promotion integrity\n"
                "decomposition_profile: simple\n"
                "---\n\n"
                "# Preserve promotion integrity\n\n"
                "Keep pending delivery work truthful when backlog handoff is interrupted.\n"
            ),
        )
    captured: list[tuple[EventType, dict[str, object]]] = []
    plane = ResearchPlane(
        config,
        paths,
        emit_event=lambda event_type, payload: captured.append((event_type, dict(payload))),
    )
    run_id = "goalspec-taskaudit-integrity-044"
    taskaudit_record_path = workspace / "agents" / ".research_runtime" / "goalspec" / "taskaudit" / f"{run_id}.json"

    for _ in range(6):
        dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=False)
        assert dispatch is not None

    dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=False)
    assert dispatch is not None
    assert plane.snapshot_state().checkpoint is not None
    assert plane.snapshot_state().checkpoint.node_id == "taskaudit"

    dispatch = plane.run_ready_work(run_id=run_id, resolve_assets=False)
    assert dispatch is not None
    assert json.loads(taskaudit_record_path.read_text(encoding="utf-8"))["status"] == "prepared"
    prepared_pending_count = len(
        parse_task_store(
            paths.taskspending_file.read_text(encoding="utf-8"),
            source_file=paths.taskspending_file,
        ).cards
    )

    paths.taskspending_file.write_text(
        paths.taskspending_file.read_text(encoding="utf-8") + "\n<!-- drift -->\n",
        encoding="utf-8",
    )

    with pytest.raises(research_plane_module.GoalSpecExecutionError, match="Taskaudit prepared pending family changed before final merge"):
        plane.run_ready_work(run_id=run_id, resolve_assets=False)

    snapshot = plane.snapshot_state()
    assert plane.status_store.read() is ResearchStatus.BLOCKED
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "taskaudit"
    assert snapshot.retry_state is not None
    blocked_event = next(payload for event_type, payload in captured if event_type is EventType.RESEARCH_BLOCKED)
    assert blocked_event["failure_kind"] == "TaskauditExecutionError"
    assert blocked_event["reason"] == "Taskaudit prepared pending family changed before final merge"

    record = json.loads(taskaudit_record_path.read_text(encoding="utf-8"))
    assert record["status"] == "promotion_blocked"
    assert record["blocked_reason"] == "Taskaudit prepared pending family changed before final merge"
    assert (
        len(
            parse_task_store(
                paths.taskspending_file.read_text(encoding="utf-8"),
                source_file=paths.taskspending_file,
            ).cards
        )
        == prepared_pending_count
    )
    assert parse_task_store(paths.backlog_file.read_text(encoding="utf-8"), source_file=paths.backlog_file).cards == []


def test_taskaudit_pending_merge(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    raw_goal_text = (
        "---\n"
        "idea_id: IDEA-42\n"
        "title: Modernize Goal Intake\n"
        "decomposition_profile: simple\n"
        "---\n\n"
        "# Modernize Goal Intake\n\n"
        "Create real GoalSpec intake and objective sync stages.\n"
    )
    _write_queue_file(raw_goal_path, raw_goal_text)
    pending_scaffold = paths.taskspending_file.read_text(encoding="utf-8")
    plane = ResearchPlane(config, paths)

    dispatch = _run_research_until_settled(
        plane,
        run_id="goalspec-run-42",
        resolve_assets=False,
    )

    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is None
    assert snapshot.lock_state is None
    assert snapshot.queue_snapshot.selected_family is None
    assert snapshot.queue_snapshot.ownerships == ()
    assert plane.status_store.read() is ResearchStatus.IDLE

    staged_path = workspace / "agents" / "ideas" / "staging" / "IDEA-42__modernize-goal-intake.md"
    finished_source_path = workspace / "agents" / "ideas" / "finished" / "IDEA-42__modernize-goal-intake.md"
    archived_rel_path = (
        "agents/ideas/archive/raw/"
        f"goal__goalspec-run-42__{sha256(raw_goal_text.encode('utf-8')).hexdigest()[:12]}.md"
    )
    archived_path = workspace / archived_rel_path
    goal_intake_record_path = (
        workspace / "agents" / ".research_runtime" / "goalspec" / "goal_intake" / "goalspec-run-42.json"
    )
    objective_record_path = (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "objective_profile_sync"
        / "goalspec-run-42.json"
    )
    profile_state_path = workspace / "agents" / "objective" / "profile_sync_state.json"
    profile_json_path = (
        workspace / "agents" / "reports" / "acceptance_profiles" / "idea-42-profile.json"
    )
    profile_md_path = workspace / "agents" / "reports" / "acceptance_profiles" / "idea-42-profile.md"
    completion_manifest_path = workspace / "agents" / "audit" / "completion_manifest.json"
    completion_report_path = workspace / "agents" / "reports" / "completion_manifest_plan.md"
    completion_record_path = (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "completion_manifest"
        / "goalspec-run-42.json"
    )
    spec_record_path = (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "spec_synthesis"
        / "goalspec-run-42.json"
    )
    review_record_path = (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "spec_review"
        / "goalspec-run-42.json"
    )
    taskmaster_record_path = (
        workspace / "agents" / ".research_runtime" / "goalspec" / "taskmaster" / "goalspec-run-42.json"
    )
    taskaudit_record_path = (
        workspace / "agents" / ".research_runtime" / "goalspec" / "taskaudit" / "goalspec-run-42.json"
    )
    task_provenance_path = workspace / "agents" / "task_provenance.json"
    lineage_path = (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "lineage"
        / "SPEC-42.json"
    )
    queue_spec_path = workspace / "agents" / "ideas" / "specs" / "SPEC-42__modernize-goal-intake.md"
    reviewed_path = workspace / "agents" / "ideas" / "specs_reviewed" / "SPEC-42__modernize-goal-intake.md"
    archived_reviewed_path = workspace / "agents" / "ideas" / "archive" / "SPEC-42__modernize-goal-intake.md"
    shard_path = workspace / "agents" / "taskspending" / "SPEC-42.md"
    golden_spec_path = workspace / "agents" / "specs" / "stable" / "golden" / "SPEC-42__modernize-goal-intake.md"
    phase_spec_path = workspace / "agents" / "specs" / "stable" / "phase" / "SPEC-42__phase-01.md"
    decision_path = (
        workspace
        / "agents"
        / "specs"
        / "decisions"
        / "IDEA-42__modernize-goal-intake__spec-synthesis.md"
    )
    review_questions_path = (
        workspace
        / "agents"
        / "specs"
        / "questions"
        / "SPEC-42__modernize-goal-intake__spec-review.md"
    )
    review_decision_path = (
        workspace
        / "agents"
        / "specs"
        / "decisions"
        / "SPEC-42__modernize-goal-intake__spec-review.md"
    )
    stable_index_path = workspace / "agents" / "specs" / "index.json"
    family_state_path = workspace / "agents" / ".research_runtime" / "spec_family_state.json"

    assert not staged_path.exists()
    assert finished_source_path.exists()
    assert archived_path.exists()
    assert not raw_goal_path.exists()
    assert goal_intake_record_path.exists()
    assert objective_record_path.exists()
    assert profile_state_path.exists()
    assert profile_json_path.exists()
    assert profile_md_path.exists()
    assert completion_manifest_path.exists()
    assert completion_report_path.exists()
    assert completion_record_path.exists()
    assert spec_record_path.exists()
    assert review_record_path.exists()
    assert taskmaster_record_path.exists()
    assert taskaudit_record_path.exists()
    assert task_provenance_path.exists()
    assert lineage_path.exists()
    assert not queue_spec_path.exists()
    assert not reviewed_path.exists()
    assert archived_reviewed_path.exists()
    assert not shard_path.exists()
    assert golden_spec_path.exists()
    assert phase_spec_path.exists()
    assert decision_path.exists()
    assert review_questions_path.exists()
    assert review_decision_path.exists()
    assert stable_index_path.exists()
    assert family_state_path.exists()
    assert paths.taskspending_file.read_text(encoding="utf-8") == pending_scaffold
    backlog = parse_task_store(paths.backlog_file.read_text(encoding="utf-8"), source_file=paths.backlog_file)
    assert [card.spec_id for card in backlog.cards] == ["SPEC-42", "SPEC-42", "SPEC-42", "SPEC-42"]
    assert [card.title.split(" - ", 1)[0] for card in backlog.cards] == [
        "SPEC-42 PHASE_01.1",
        "SPEC-42 PHASE_01.2",
        "SPEC-42 PHASE_01.3",
        "SPEC-42 PHASE_01.4",
    ]
    assert [card.requirement_ids for card in backlog.cards] == [
        ("REQ-001", "REQ-002"),
        ("REQ-001", "REQ-002"),
        ("REQ-001", "REQ-002"),
        ("REQ-001", "REQ-002"),
    ]
    assert [card.acceptance_ids for card in backlog.cards] == [
        ("AC-001", "AC-002"),
        ("AC-001", "AC-002"),
        ("AC-001", "AC-002"),
        ("AC-001", "AC-002"),
    ]
    assert [_field_block_lines(card.body, "Files to touch") for card in backlog.cards] == [
        ("millrace_engine/research/goalspec_goal_intake.py",),
        (
            "millrace_engine/research/goalspec_objective_profile_sync.py",
            "millrace_engine/research/goalspec_stage_support.py",
        ),
        ("tests/test_research_dispatcher.py", "tests/test_goalspec_state.py"),
        ("tests/test_research_dispatcher.py", "tests/test_goalspec_state.py"),
    ]

    finished_text = finished_source_path.read_text(encoding="utf-8")
    golden_text = golden_spec_path.read_text(encoding="utf-8")
    decision_text = decision_path.read_text(encoding="utf-8")
    stale_deferred_text = "Spec synthesis and review are intentionally deferred to later Phase 05 runs."
    downstream_pending_text = "Implementation remains open for the profiled product"
    assert "status: finished" in finished_text
    assert "## Route Decision" in finished_text
    assert "agents/_goal_intake.md" in finished_text
    assert archived_reviewed_path.read_text(encoding="utf-8") == golden_text
    assert "agents/audit/completion_manifest.json" in archived_reviewed_path.read_text(encoding="utf-8")
    assert "SPEC-42" in decision_text
    assert "Deliver the product outcome captured in `IDEA-42`" in golden_text
    assert "Convert `IDEA-42` into a traceable GoalSpec draft package." not in golden_text
    assert "Persist completion-manifest drafting state before spec output" not in golden_text
    assert "smallest bounded spec slice" in decision_text
    assert "preserves GoalSpec traceability" not in decision_text
    assert stale_deferred_text not in profile_md_path.read_text(encoding="utf-8")
    assert stale_deferred_text not in completion_manifest_path.read_text(encoding="utf-8")
    assert stale_deferred_text not in completion_report_path.read_text(encoding="utf-8")
    assert stale_deferred_text not in archived_reviewed_path.read_text(encoding="utf-8")
    assert downstream_pending_text in profile_md_path.read_text(encoding="utf-8")
    assert downstream_pending_text in completion_manifest_path.read_text(encoding="utf-8")
    assert downstream_pending_text in completion_report_path.read_text(encoding="utf-8")
    assert downstream_pending_text in archived_reviewed_path.read_text(encoding="utf-8")
    assert "No blocking findings; the package is decomposition-ready as written." in review_questions_path.read_text(
        encoding="utf-8"
    )
    assert "`no_material_delta`" in review_decision_path.read_text(encoding="utf-8")
    assert "Approved for downstream decomposition without material spec edits in this run." in review_decision_path.read_text(
        encoding="utf-8"
    )

    goal_intake_record = json.loads(goal_intake_record_path.read_text(encoding="utf-8"))
    assert goal_intake_record["schema_version"] == "1.0"
    assert goal_intake_record["run_id"] == "goalspec-run-42"
    assert goal_intake_record["canonical_source_path"] == archived_rel_path
    assert goal_intake_record["current_artifact_path"] == "agents/ideas/staging/IDEA-42__modernize-goal-intake.md"
    assert goal_intake_record["source_path"] == "agents/ideas/raw/goal.md"
    assert goal_intake_record["archived_source_path"] == archived_rel_path
    assert goal_intake_record["research_brief_path"] == "agents/ideas/staging/IDEA-42__modernize-goal-intake.md"

    profile_state = json.loads(profile_state_path.read_text(encoding="utf-8"))
    assert profile_state["schema_version"] == "1.0"
    assert profile_state["run_id"] == "goalspec-run-42"
    assert profile_state["canonical_source_path"] == archived_rel_path
    assert profile_state["current_artifact_path"] == "agents/ideas/staging/IDEA-42__modernize-goal-intake.md"
    assert profile_state["source_path"] == archived_rel_path
    assert profile_state["goal_intake_record_path"] == (
        "agents/.research_runtime/goalspec/goal_intake/goalspec-run-42.json"
    )
    assert profile_state["profile_path"] == "agents/reports/acceptance_profiles/idea-42-profile.json"

    profile_json = json.loads(profile_json_path.read_text(encoding="utf-8"))
    assert profile_json["goal_id"] == "IDEA-42"
    assert profile_json["run_id"] == "goalspec-run-42"
    assert profile_json["canonical_source_path"] == archived_rel_path
    assert profile_json["current_artifact_path"] == "agents/ideas/staging/IDEA-42__modernize-goal-intake.md"
    assert profile_json["source_path"] == archived_rel_path
    assert profile_json["research_brief_path"] == "agents/ideas/staging/IDEA-42__modernize-goal-intake.md"
    assert any(downstream_pending_text in item for item in profile_json["hard_blockers"])

    completion_manifest = json.loads(completion_manifest_path.read_text(encoding="utf-8"))
    assert completion_manifest["artifact_type"] == "completion_manifest_draft"
    assert completion_manifest["goal_id"] == "IDEA-42"
    assert completion_manifest["canonical_source_path"] == archived_rel_path
    assert completion_manifest["current_artifact_path"] == "agents/ideas/staging/IDEA-42__modernize-goal-intake.md"
    assert completion_manifest["source_path"] == archived_rel_path
    assert completion_manifest["planning_profile"] == "framework_runtime"
    assert completion_manifest["objective_profile_path"] == "agents/reports/acceptance_profiles/idea-42-profile.json"
    assert any(downstream_pending_text in item for item in completion_manifest["open_questions"])
    assert [artifact["path"] for artifact in completion_manifest["required_artifacts"]] == [
        "agents/ideas/specs/SPEC-42__modernize-goal-intake.md",
        "agents/specs/stable/golden/SPEC-42__modernize-goal-intake.md",
        "agents/specs/stable/phase/SPEC-42__phase-01.md",
        "agents/specs/decisions/IDEA-42__modernize-goal-intake__spec-synthesis.md",
    ]
    assert [surface["path"] for surface in completion_manifest["implementation_surfaces"]] == [
        "millrace_engine/research/goalspec_goal_intake.py",
        "millrace_engine/research/goalspec_objective_profile_sync.py",
        "millrace_engine/research/goalspec_stage_support.py",
    ]
    assert [surface["path"] for surface in completion_manifest["verification_surfaces"]] == [
        "tests/test_research_dispatcher.py",
        "tests/test_goalspec_state.py",
    ]

    completion_record = json.loads(completion_record_path.read_text(encoding="utf-8"))
    assert completion_record["artifact_type"] == "completion_manifest_draft_record"
    assert completion_record["draft_path"] == "agents/audit/completion_manifest.json"
    assert completion_record["report_path"] == "agents/reports/completion_manifest_plan.md"

    spec_record = json.loads(spec_record_path.read_text(encoding="utf-8"))
    assert spec_record["artifact_type"] == "spec_synthesis"
    assert spec_record["spec_id"] == "SPEC-42"
    assert spec_record["canonical_source_path"] == archived_rel_path
    assert spec_record["current_artifact_path"] == "agents/ideas/staging/IDEA-42__modernize-goal-intake.md"
    assert spec_record["source_path"] == archived_rel_path
    assert spec_record["queue_spec_path"] == "agents/ideas/specs/SPEC-42__modernize-goal-intake.md"
    assert spec_record["decision_path"] == "agents/specs/decisions/IDEA-42__modernize-goal-intake__spec-synthesis.md"

    review_record = json.loads(review_record_path.read_text(encoding="utf-8"))
    assert review_record["spec_id"] == "SPEC-42"
    assert review_record["review_status"] == "no_material_delta"
    assert review_record["questions_path"] == "agents/specs/questions/SPEC-42__modernize-goal-intake__spec-review.md"
    assert review_record["decision_path"] == "agents/specs/decisions/SPEC-42__modernize-goal-intake__spec-review.md"
    assert review_record["reviewed_path"] == "agents/ideas/specs_reviewed/SPEC-42__modernize-goal-intake.md"
    assert review_record["findings"] == []

    lineage_record = json.loads(lineage_path.read_text(encoding="utf-8"))
    assert lineage_record["spec_id"] == "SPEC-42"
    assert lineage_record["goal_id"] == "IDEA-42"
    assert lineage_record["source_idea_path"] == "agents/ideas/staging/IDEA-42__modernize-goal-intake.md"
    assert lineage_record["queue_path"] == "agents/ideas/specs/SPEC-42__modernize-goal-intake.md"
    assert lineage_record["reviewed_path"] == ""
    assert lineage_record["archived_path"] == "agents/ideas/archive/SPEC-42__modernize-goal-intake.md"
    assert lineage_record["pending_shard_path"] == "agents/taskspending/SPEC-42.md"
    assert lineage_record["stable_spec_paths"] == [
        "agents/specs/stable/golden/SPEC-42__modernize-goal-intake.md",
        "agents/specs/stable/phase/SPEC-42__phase-01.md",
    ]

    stable_index = json.loads(stable_index_path.read_text(encoding="utf-8"))
    assert [entry["spec_path"] for entry in stable_index["stable_specs"]] == [
        "agents/specs/stable/golden/SPEC-42__modernize-goal-intake.md",
        "agents/specs/stable/phase/SPEC-42__phase-01.md",
    ]

    family_state = json.loads(family_state_path.read_text(encoding="utf-8"))
    assert family_state["goal_id"] == "IDEA-42"
    assert family_state["family_complete"] is True
    assert family_state["active_spec_id"] == "SPEC-42"
    assert family_state["spec_order"] == ["SPEC-42"]
    assert family_state["specs"]["SPEC-42"]["status"] == "decomposed"
    assert family_state["specs"]["SPEC-42"]["review_status"] == "no_material_delta"
    assert family_state["specs"]["SPEC-42"]["queue_path"] == "agents/ideas/specs/SPEC-42__modernize-goal-intake.md"
    assert family_state["specs"]["SPEC-42"]["reviewed_path"] == ""
    assert family_state["specs"]["SPEC-42"]["archived_path"] == "agents/ideas/archive/SPEC-42__modernize-goal-intake.md"
    assert family_state["specs"]["SPEC-42"]["pending_shard_path"] == "agents/taskspending/SPEC-42.md"
    assert family_state["specs"]["SPEC-42"]["stable_spec_paths"] == [
        "agents/specs/stable/golden/SPEC-42__modernize-goal-intake.md",
        "agents/specs/stable/phase/SPEC-42__phase-01.md",
    ]
    assert family_state["specs"]["SPEC-42"]["review_questions_path"] == (
        "agents/specs/questions/SPEC-42__modernize-goal-intake__spec-review.md"
    )
    assert family_state["specs"]["SPEC-42"]["review_decision_path"] == (
        "agents/specs/decisions/SPEC-42__modernize-goal-intake__spec-review.md"
    )
    assert family_state["initial_family_plan"]["spec_order"] == ["SPEC-42"]

    taskmaster_record = json.loads(taskmaster_record_path.read_text(encoding="utf-8"))
    assert taskmaster_record["artifact_type"] == "taskmaster_strict_shard"
    assert taskmaster_record["spec_id"] == "SPEC-42"
    assert taskmaster_record["reviewed_path"] == "agents/ideas/specs_reviewed/SPEC-42__modernize-goal-intake.md"
    assert taskmaster_record["archived_path"] == "agents/ideas/archive/SPEC-42__modernize-goal-intake.md"
    assert taskmaster_record["shard_path"] == "agents/taskspending/SPEC-42.md"
    assert taskmaster_record["finished_source_path"] == "agents/ideas/finished/IDEA-42__modernize-goal-intake.md"
    assert taskmaster_record["acceptance_id_source"] == "derived_from_requirements"
    assert taskmaster_record["card_count"] == 4
    assert taskmaster_record["profile_selection"]["selected_mode_ref"]["id"] == "mode.research_goalspec"
    assert taskmaster_record["profile_selection"]["task_authoring_profile_ref"]["id"] == "task_authoring.narrow"
    assert taskmaster_record["profile_selection"]["selection_path"] == "mode.task_authoring_profile_ref"
    assert taskmaster_record["profile_selection"]["lookup_path"] == "mode.task_authoring_profile_lookup_ref"
    assert taskmaster_record["profile_selection"]["selection_source"] == "mode"
    assert taskmaster_record["profile_selection"]["expected_min_cards"] == 3
    assert taskmaster_record["profile_selection"]["expected_max_cards"] == 5
    assert [title.split(" - ", 1)[0] for title in taskmaster_record["task_titles"]] == [
        "SPEC-42 PHASE_01.1",
        "SPEC-42 PHASE_01.2",
        "SPEC-42 PHASE_01.3",
        "SPEC-42 PHASE_01.4",
    ]

    taskaudit_record = json.loads(taskaudit_record_path.read_text(encoding="utf-8"))
    assert taskaudit_record["artifact_type"] == "taskaudit_final_family_merge"
    assert taskaudit_record["status"] == "merged"
    assert taskaudit_record["pending_path"] == "agents/taskspending.md"
    assert taskaudit_record["backlog_path"] == "agents/tasksbacklog.md"
    assert taskaudit_record["provenance_path"] == "agents/task_provenance.json"
    assert taskaudit_record["pending_card_count"] == 4
    assert taskaudit_record["backlog_card_count_before"] == 0
    assert taskaudit_record["backlog_card_count_after"] == 4
    assert taskaudit_record["merged_spec_ids"] == ["SPEC-42"]
    assert taskaudit_record["shard_paths"] == ["agents/taskspending/SPEC-42.md"]

    backlog_text = paths.backlog_file.read_text(encoding="utf-8")
    assert "agents/ideas/archive/SPEC-42__modernize-goal-intake.md" in backlog_text
    assert "agents/ideas/finished/IDEA-42__modernize-goal-intake.md" in backlog_text
    assert "agents/ideas/specs_reviewed/SPEC-42__modernize-goal-intake.md" not in backlog_text
    assert "agents/ideas/staging/IDEA-42__modernize-goal-intake.md" not in backlog_text
    assert "millrace_engine/research/goalspec_goal_intake.py" in backlog_text
    assert "tests/test_research_dispatcher.py" in backlog_text

    task_provenance = json.loads(task_provenance_path.read_text(encoding="utf-8"))
    assert [entry["source_file"] for entry in task_provenance["sources"]] == [
        "agents/tasks.md",
        "agents/tasksbacklog.md",
        "agents/tasksarchive.md",
    ]
    assert task_provenance["sources"][1]["card_count"] == 4
    assert task_provenance["taskaudit"]["record_path"] == "agents/.research_runtime/goalspec/taskaudit/goalspec-run-42.json"
    assert task_provenance["taskaudit"]["run_id"] == "goalspec-run-42"
    assert task_provenance["taskaudit"]["pending_path"] == "agents/taskspending.md"
    assert task_provenance["taskaudit"]["pending_shards"] == ["agents/taskspending/SPEC-42.md"]
    assert task_provenance["taskaudit"]["pending_card_count"] == 4
    assert task_provenance["taskaudit"]["merged_backlog_card_count"] == 4
    assert task_provenance["taskaudit"]["merged_spec_ids"] == ["SPEC-42"]
    assert [entry["title"].split(" - ", 1)[0] for entry in task_provenance["task_cards"]] == [
        "SPEC-42 PHASE_01.1",
        "SPEC-42 PHASE_01.2",
        "SPEC-42 PHASE_01.3",
        "SPEC-42 PHASE_01.4",
    ]


def test_research_plane_blocks_same_family_earlier_stage_recycling_after_spec_emission(
    tmp_path: Path,
) -> None:
    workspace, config, paths, _synthesis, queue_spec_path, _staged_path = _prepare_emitted_spec_family(
        tmp_path,
        run_id="goalspec-recycle-601",
        emitted_at=_dt("2026-04-07T17:00:00Z"),
        title="Team Workspace Vertical Slice",
        body="Build the first usable team workspace vertical slice for collaborative planning.",
        decomposition_profile="moderate",
        idea_id="IDEA-601",
    )
    assert queue_spec_path.exists()
    recycled_raw_path = workspace / "agents" / "ideas" / "raw" / "goal-recycled.md"
    _write_queue_file(
        recycled_raw_path,
        (
            "---\n"
            "idea_id: IDEA-601\n"
            "title: Team Workspace Vertical Slice\n"
            "decomposition_profile: moderate\n"
            "---\n\n"
            "# Team Workspace Vertical Slice\n\n"
            "Rediscovered raw goal after spec emission.\n"
        ),
    )
    plane = ResearchPlane(config, paths)

    with pytest.raises(
        research_plane_module.GoalSpecExecutionError,
        match="emitted specs recycled into goal_intake",
    ):
        plane.run_ready_work(run_id="goalspec-recycle-blocked", resolve_assets=False)

    assert plane.status_store.read() is ResearchStatus.BLOCKED
    integrity_report = json.loads(
        (workspace / "agents" / ".tmp" / "goalspec_delivery_integrity_report.json").read_text(encoding="utf-8")
    )
    assert integrity_report["status"] == "failed"
    assert integrity_report["reason"] == "same-family-earlier-stage-recycling-after-spec-emission"
    assert integrity_report["goal_id"] == "IDEA-601"
    assert integrity_report["queue_item_path"] == "agents/ideas/raw/goal-recycled.md"


def test_research_plane_blocks_silent_emitted_family_non_delivery_and_surfaces_it_in_governance(
    tmp_path: Path,
) -> None:
    workspace, config, paths, _synthesis, queue_spec_path, staged_path = _prepare_emitted_spec_family(
        tmp_path,
        run_id="goalspec-stalled-602",
        emitted_at=_dt("2026-04-07T17:05:00Z"),
        title="Support Ticket Service",
        body="Build the first usable support-ticket web app for a Python service.",
        decomposition_profile="moderate",
        idea_id="IDEA-602",
    )
    queue_spec_path.unlink()
    staged_path.unlink()
    plane = ResearchPlane(config, paths)

    with pytest.raises(
        research_plane_module.GoalSpecExecutionError,
        match="no active GoalSpec queue item, pending shard, or merged Taskaudit backlog handoff",
    ):
        plane.dispatch_ready_work(resolve_assets=False)

    assert plane.status_store.read() is ResearchStatus.BLOCKED
    integrity_report = json.loads(
        (workspace / "agents" / ".tmp" / "goalspec_delivery_integrity_report.json").read_text(encoding="utf-8")
    )
    assert integrity_report["status"] == "failed"
    assert integrity_report["reason"] == "emitted-specs-without-queue-or-handoff"
    governance = build_research_governance_report(paths)
    assert governance.goalspec_delivery_integrity.status == "failed"
    assert governance.goalspec_delivery_integrity.reason == "emitted-specs-without-queue-or-handoff"
    assert governance.goalspec_delivery_integrity.goal_id == "IDEA-602"


def test_research_plane_keeps_normal_downstream_goalspec_progression_healthy_after_spec_emission(
    tmp_path: Path,
) -> None:
    workspace, config, paths, _synthesis, queue_spec_path, staged_path = _prepare_emitted_spec_family(
        tmp_path,
        run_id="goalspec-healthy-603",
        emitted_at=_dt("2026-04-07T17:10:00Z"),
        title="Modernize Goal Intake",
        body="Create real GoalSpec intake and objective sync stages.",
        idea_id="IDEA-603",
    )
    assert queue_spec_path.exists()
    staged_path.unlink()
    plane = ResearchPlane(config, paths)

    dispatch = plane.dispatch_ready_work(resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.entry_node_id == "spec_review"
    assert plane.status_store.read() is ResearchStatus.SPEC_REVIEW_RUNNING
    integrity_report = json.loads(
        (workspace / "agents" / ".tmp" / "goalspec_delivery_integrity_report.json").read_text(encoding="utf-8")
    )
    assert integrity_report["status"] == "healthy"
    assert integrity_report["reason"] == "same-family-downstream-goalspec-queue-ready"
    plane.shutdown()


def test_sync_runtime_executes_goalspec_stages_from_supervisor_entrypoint(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    _write_queue_file(
        raw_goal_path,
        "---\n"
        "idea_id: IDEA-77\n"
        "title: Supervisor GoalSpec Path\n"
        "---\n\n"
        "# Supervisor GoalSpec Path\n\n"
        "Exercise the runtime supervisor entrypoint instead of the direct helper.\n",
    )
    plane = ResearchPlane(config, paths)

    dispatch = plane.sync_runtime(trigger="engine-start", run_id="goalspec-sync-77", resolve_assets=False)

    assert dispatch is not None
    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "objective_profile_sync"
    assert snapshot.queue_snapshot.selected_family is ResearchQueueFamily.GOALSPEC
    assert plane.status_store.read() is ResearchStatus.OBJECTIVE_PROFILE_SYNC_RUNNING
    assert not (workspace / "agents" / ".research_runtime" / "goalspec" / "spec_interview").exists()
    assert not list((workspace / "agents" / "specs" / "questions").glob("SPEC-77__interview-*.json"))
    assert not (workspace / "agents" / "audit" / "completion_manifest.json").exists()
    assert not (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "spec_synthesis"
        / "goalspec-sync-77.json"
    ).exists()
    assert not (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "spec_review"
        / "goalspec-sync-77.json"
    ).exists()
    assert not (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "taskmaster"
        / "goalspec-sync-77.json"
    ).exists()
    assert not (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "taskaudit"
        / "goalspec-sync-77.json"
    ).exists()
    assert not (workspace / "agents" / "taskspending" / "SPEC-77.md").exists()
    backlog = parse_task_store((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))
    assert backlog.cards == []

    dispatch = plane.run_ready_work(run_id="goalspec-sync-77", resolve_assets=False)

    assert dispatch is not None
    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "completion_manifest_draft"
    assert plane.status_store.read() is ResearchStatus.COMPLETION_MANIFEST_RUNNING
    assert not (workspace / "agents" / "audit" / "completion_manifest.json").exists()

    dispatch = plane.run_ready_work(run_id="goalspec-sync-77", resolve_assets=False)

    assert dispatch is not None
    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "spec_synthesis"
    assert plane.status_store.read() is ResearchStatus.SPEC_SYNTHESIS_RUNNING
    assert (workspace / "agents" / "audit" / "completion_manifest.json").exists()
    assert not (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "spec_synthesis"
        / "goalspec-sync-77.json"
    ).exists()


def test_research_plane_blocks_at_spec_interview_and_resumes_after_operator_answer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config, paths = _configured_runtime(
        tmp_path,
        mode=ResearchMode.GOALSPEC,
        interview_policy="always",
    )
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    _write_queue_file(
        raw_goal_path,
        "---\n"
        "idea_id: IDEA-88\n"
        "title: Support Ticket Escalation Console\n"
        "decomposition_profile: moderate\n"
        "---\n\n"
        "# Support Ticket Escalation Console\n\n"
        "Build the first operator console for a support-ticket service.\n\n"
        "## Capability Domains\n"
        "- Ticket escalation dashboard\n"
        "- Manager approval workflow\n\n"
        "## Progression Lines\n"
        "- Move from ticket intake to escalation approval to agent resolution confirmation.\n",
    )
    plane = ResearchPlane(config, paths)
    original_execute_spec_synthesis = research_plane_module.execute_spec_synthesis

    def _execute_spec_synthesis_with_ambiguity(*args: object, **kwargs: object):
        result = original_execute_spec_synthesis(*args, **kwargs)
        queue_spec_path = workspace / result.queue_spec_path
        queue_spec_path.write_text(
            queue_spec_path.read_text(encoding="utf-8")
            + "\n## Interview Override\n- TODO: Confirm whether VIP escalations require manager approval before assignment.\n",
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(
        research_plane_module,
        "execute_spec_synthesis",
        _execute_spec_synthesis_with_ambiguity,
    )

    _run_research_until_settled(
        plane,
        run_id="goalspec-interview-block-88",
        resolve_assets=False,
    )

    blocked_snapshot = plane.snapshot_state()
    questions = list_interview_questions(paths)
    assert plane.status_store.read() is ResearchStatus.BLOCKED
    assert blocked_snapshot.checkpoint is not None
    assert blocked_snapshot.checkpoint.node_id == "spec_interview"
    assert len(questions) == 1
    assert questions[0].status == "pending"
    assert not (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "spec_review"
        / "goalspec-interview-block-88.json"
    ).exists()

    answer_interview_question(
        paths,
        question_id=questions[0].question_id,
        text="VIP escalations require manager approval before the console can assign an agent.",
    )

    _resume_research_until_settled(
        plane,
        trigger="operator-answer",
        resolve_assets=False,
    )

    resumed_snapshot = plane.snapshot_state()
    resumed_questions = list_interview_questions(paths)
    assert plane.status_store.read() is ResearchStatus.IDLE
    assert resumed_snapshot.checkpoint is None
    assert resumed_questions[0].status == "answered"
    assert (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "spec_interview"
        / "goalspec-interview-block-88.json"
    ).exists()
    assert (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "spec_review"
        / "goalspec-interview-block-88.json"
    ).exists()


def test_execute_goal_intake_archives_same_queue_filename_to_distinct_execution_paths(tmp_path: Path) -> None:
    workspace, _, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    first_emitted_at = _dt("2026-03-21T12:00:00Z")
    second_emitted_at = _dt("2026-03-21T12:05:00Z")
    first_run_id = "goalspec-run-101"
    second_run_id = "goalspec-run-102"
    first_goal_text = "# First Goal\n\nPreserve the first snapshot.\n"
    second_goal_text = "# Second Goal\n\nPreserve the second snapshot.\n"

    def _checkpoint(run_id: str, emitted_at: datetime) -> ResearchCheckpoint:
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
                    queue_path=paths.ideas_raw_dir,
                    item_path=raw_goal_path,
                    owner_token=run_id,
                    acquired_at=emitted_at,
                ),
            ),
        )

    _write_queue_file(raw_goal_path, first_goal_text)
    first_result = execute_goal_intake(
        paths,
        _checkpoint(first_run_id, first_emitted_at),
        run_id=first_run_id,
        emitted_at=first_emitted_at,
    )
    first_record = json.loads((workspace / first_result.record_path).read_text(encoding="utf-8"))

    _write_queue_file(raw_goal_path, second_goal_text)
    second_result = execute_goal_intake(
        paths,
        _checkpoint(second_run_id, second_emitted_at),
        run_id=second_run_id,
        emitted_at=second_emitted_at,
    )
    second_record = json.loads((workspace / second_result.record_path).read_text(encoding="utf-8"))

    first_archived_rel = (
        "agents/ideas/archive/raw/"
        f"goal__goalspec-run-101__{sha256(first_goal_text.encode('utf-8')).hexdigest()[:12]}.md"
    )
    second_archived_rel = (
        "agents/ideas/archive/raw/"
        f"goal__goalspec-run-102__{sha256(second_goal_text.encode('utf-8')).hexdigest()[:12]}.md"
    )
    first_archived_path = workspace / first_archived_rel
    second_archived_path = workspace / second_archived_rel

    assert first_archived_path.exists()
    assert second_archived_path.exists()
    assert first_archived_path != second_archived_path
    assert first_archived_path.read_text(encoding="utf-8") == first_goal_text
    assert second_archived_path.read_text(encoding="utf-8") == second_goal_text
    assert first_record["archived_source_path"] == first_archived_rel
    assert second_record["archived_source_path"] == second_archived_rel
    assert first_result.archived_source_path == first_archived_rel
    assert second_result.archived_source_path == second_archived_rel


def test_execute_goal_intake_keeps_trace_metadata_out_of_semantic_body(tmp_path: Path) -> None:
    workspace, _, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    run_id = "goalspec-run-trace-201"
    emitted_at = _dt("2026-03-21T12:10:00Z")
    goal_text = (
        "---\n"
        "idea_id: IDEA-TRACE-201\n"
        "title: Goal Intake Trace Split\n"
        "---\n\n"
        "# Goal Intake Trace Split\n\n"
        "Keep staged idea semantics product-facing.\n"
    )

    _write_queue_file(raw_goal_path, goal_text)
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


def test_execute_objective_profile_sync_pins_frozen_family_policy_fields(tmp_path: Path) -> None:
    workspace, _, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    staged_path = workspace / "agents" / "ideas" / "staging" / "IDEA-301__pinned-policy-goal.md"
    run_id = "goalspec-policy-pin-301"
    emitted_at = _dt("2026-03-21T12:00:00Z")
    goal_text = (
        "---\n"
        "idea_id: IDEA-301\n"
        "title: Pinned Policy Goal\n"
        "---\n\n"
        "# Pinned Policy Goal\n\n"
        "Preserve frozen-family policy during objective refresh.\n"
    )

    _write_queue_file(raw_goal_path, goal_text)
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

    family_policy_path = workspace / "agents" / "objective" / "family_policy.json"
    family_policy_path.parent.mkdir(parents=True, exist_ok=True)
    family_policy_path.write_text(
        json.dumps({"family_cap_mode": "adaptive", "initial_family_max_specs": 3}) + "\n",
        encoding="utf-8",
    )
    state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-301",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-301",
            "spec_order": ["SPEC-301"],
            "specs": {
                "SPEC-301": {
                    "status": "emitted",
                    "title": "Pinned Policy Goal",
                    "decomposition_profile": "simple",
                }
            },
            "family_governor": {
                "policy_path": "agents/objective/family_policy.json",
                "initial_family_max_specs": 3,
                "applied_family_max_specs": 3,
            },
        }
    )
    frozen_state = state.model_copy(
        update={
            "initial_family_plan": build_initial_family_plan_snapshot(
                state,
                repo_root=workspace,
                goal_file=raw_goal_path,
                policy_path=family_policy_path,
                frozen_at=emitted_at,
            )
        }
    )
    paths.goal_spec_family_state_file.parent.mkdir(parents=True, exist_ok=True)
    paths.goal_spec_family_state_file.write_text(
        frozen_state.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )

    result = execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="objective_profile_sync",
            stage_kind_id="research.objective-profile-sync",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )

    policy_payload = json.loads(family_policy_path.read_text(encoding="utf-8"))
    profile_state_payload = json.loads((workspace / result.profile_state_path).read_text(encoding="utf-8"))
    queue_governor_payload = json.loads(paths.queue_governor_report_file.read_text(encoding="utf-8"))

    assert policy_payload["family_cap_mode"] == "adaptive"
    assert policy_payload["initial_family_max_specs"] == 3
    assert profile_state_payload["initial_family_policy_pin"]["active"] is True
    assert profile_state_payload["initial_family_policy_pin"]["reason"] == (
        "frozen-initial-family-policy-preserved"
    )
    assert profile_state_payload["initial_family_policy_pin"]["pinned_fields"] == [
        "family_cap_mode",
        "initial_family_max_specs",
    ]
    assert queue_governor_payload["status"] == "pinned"
    assert queue_governor_payload["initial_family_policy_pin"]["active"] is True


def test_execute_spec_synthesis_reuses_matching_artifacts_without_rewriting_family_state(tmp_path: Path) -> None:
    workspace, _, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    raw_goal_text = (
        "---\n"
        "idea_id: IDEA-201\n"
        "title: Support Ticket Escalation Console\n"
        "decomposition_profile: moderate\n"
        "---\n"
        "\n"
        "# Support Ticket Escalation Console\n\n"
        "Build the first operator console for a support-ticket service.\n\n"
        "## Capability Domains\n"
        "- Ticket intake API\n"
        "- Escalation approval dashboard\n\n"
        "## Progression Lines\n"
        "- Move from customer ticket intake to escalation approval to agent resolution confirmation.\n"
    )
    emitted_at = _dt("2026-03-21T12:00:00Z")
    run_id = "goalspec-idempotent-201"
    staged_path = workspace / "agents" / "ideas" / "staging" / "IDEA-201__support-ticket-escalation-console.md"

    _write_queue_file(raw_goal_path, raw_goal_text)
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

    first_result = execute_spec_synthesis(
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
    spec_record_path = workspace / first_result.record_path
    family_state_path = workspace / first_result.family_state_path
    queue_spec_path = workspace / first_result.queue_spec_path
    golden_spec_path = workspace / first_result.golden_spec_path
    phase_spec_path = workspace / first_result.phase_spec_path
    decision_path = workspace / first_result.decision_path

    first_record_text = spec_record_path.read_text(encoding="utf-8")
    first_family_state_text = family_state_path.read_text(encoding="utf-8")
    first_queue_text = queue_spec_path.read_text(encoding="utf-8")
    first_golden_text = golden_spec_path.read_text(encoding="utf-8")
    first_phase_text = phase_spec_path.read_text(encoding="utf-8")
    first_decision_text = decision_path.read_text(encoding="utf-8")

    second_result = execute_spec_synthesis(
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
        emitted_at=_dt("2026-03-21T12:30:00Z"),
    )

    assert second_result == first_result
    assert "Deliver the product outcome captured in `IDEA-201`" in first_queue_text
    assert "- Ticket intake API" in first_queue_text
    assert "Keep measurable validation and bounded output expectations attached to the synthesized slice" in first_queue_text
    assert "Convert `IDEA-201` into a traceable GoalSpec draft package." not in first_queue_text
    assert "Persist completion-manifest drafting state before spec output" not in first_queue_text
    assert "Carry the drafted GoalSpec package into a reviewable runtime implementation slice." not in first_phase_text
    assert "Deliver the first bounded product capability slice for `IDEA-201`" in first_phase_text
    assert "Implement the first bounded capability slice" not in first_phase_text
    _assert_product_grounded_stage_artifacts(first_queue_text, first_phase_text)
    assert "smallest bounded spec slice" in first_decision_text
    assert "GoalSpec traceability" not in first_decision_text
    assert "Planned later specs: none" in first_decision_text
    assert spec_record_path.read_text(encoding="utf-8") == first_record_text
    assert family_state_path.read_text(encoding="utf-8") == first_family_state_text
    assert queue_spec_path.read_text(encoding="utf-8") == first_queue_text
    assert golden_spec_path.read_text(encoding="utf-8") == first_golden_text
    assert phase_spec_path.read_text(encoding="utf-8") == first_phase_text
    assert decision_path.read_text(encoding="utf-8") == first_decision_text
    assert json.loads(first_record_text)["emitted_at"] == "2026-03-21T12:00:00Z"
    assert json.loads(first_family_state_text)["updated_at"] == "2026-03-21T12:00:00Z"


def test_execute_spec_synthesis_declares_bounded_later_specs_for_broad_goal(
    tmp_path: Path,
) -> None:
    workspace, _, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    raw_goal_text = (
        "---\n"
        "idea_id: IDEA-BROAD-201\n"
        "title: Team Workspace Expansion\n"
        "decomposition_profile: simple\n"
        "---\n"
        "\n"
        "# Team Workspace Expansion\n\n"
        "Build a broad but still early team workspace slice without widening the initial family too early.\n\n"
        "## Capability Domains\n"
        "- Workspace Intake\n"
        "- Shared Drafts\n"
        "- Review Queue\n"
        "- Activity Feed\n"
        "- Template Library\n"
        "- Insights Panel\n\n"
        "## Progression Lines\n"
        "- Progression from intake to drafting to review handoff to insight delivery.\n"
        "- Progression from individual planning to coordinated team publishing.\n"
    )
    emitted_at = _dt("2026-04-07T12:00:00Z")
    run_id = "goalspec-broad-family-201"
    staged_path = workspace / "agents" / "ideas" / "staging" / "IDEA-BROAD-201__team-workspace-expansion.md"

    _write_queue_file(raw_goal_path, raw_goal_text)
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

    assert family_policy["initial_family_max_specs"] == 2
    assert family_policy["adaptive_inputs"]["breadth_bonus"] == 0
    assert family_policy["adaptive_inputs"]["capability_domain_count"] == 6
    assert family_policy["adaptive_inputs"]["progression_line_count"] == 2
    assert family_state["family_complete"] is False
    assert family_state["active_spec_id"] == "SPEC-BROAD-201"
    assert family_state["spec_order"] == ["SPEC-BROAD-201", "SPEC-BROAD-201-02"]
    assert family_state["specs"]["SPEC-BROAD-201"]["status"] == "emitted"
    assert family_state["specs"]["SPEC-BROAD-201-02"]["status"] == "planned"
    assert family_state["specs"]["SPEC-BROAD-201-02"]["depends_on_specs"] == ["SPEC-BROAD-201"]
    assert family_state["initial_family_plan"]["spec_order"] == ["SPEC-BROAD-201", "SPEC-BROAD-201-02"]
    assert phase_text.count("Planned later initial-family specs:") == 1
    assert "- None." not in phase_text
    assert "`SPEC-BROAD-201-02`" in phase_text
    assert "Carry the drafted GoalSpec package" not in phase_text
    assert "Planned later specs: none" not in decision_text
    assert "`SPEC-BROAD-201-02`" in decision_text


def test_execute_spec_synthesis_respects_single_spec_broad_cap_for_broad_goal(tmp_path: Path) -> None:
    workspace, _, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    raw_goal_text = (
        "---\n"
        "idea_id: IDEA-BROAD-CAP-201\n"
        "title: Team Workspace Capped\n"
        "decomposition_profile: involved\n"
        "---\n"
        "\n"
        "# Team Workspace Capped\n\n"
        "Build an involved team workspace expansion with capped initial-family planning.\n\n"
        "## Capability Domains\n"
        "- Workspace Intake\n"
        "- Shared Drafts\n"
        "- Review Queue\n"
        "- Activity Feed\n"
        "- Template Library\n"
        "- Insights Panel\n\n"
        "## Progression Lines\n"
        "- Progression from intake to drafting to review handoff to insight delivery.\n"
        "- Progression from individual planning to coordinated team publishing.\n"
    )
    emitted_at = _dt("2026-04-07T12:20:00Z")
    run_id = "goalspec-broad-cap-201"
    staged_path = workspace / "agents" / "ideas" / "staging" / "IDEA-BROAD-CAP-201__team-workspace-capped.md"

    _write_queue_file(raw_goal_path, raw_goal_text)
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
    paths.objective_family_policy_file.parent.mkdir(parents=True, exist_ok=True)
    paths.objective_family_policy_file.write_text(
        json.dumps({"initial_family_max_specs": 1}) + "\n",
        encoding="utf-8",
    )

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
    decision_text = (workspace / result.decision_path).read_text(encoding="utf-8")

    assert family_state["family_complete"] is True
    assert family_state["active_spec_id"] == "SPEC-BROAD-CAP-201"
    assert family_state["spec_order"] == ["SPEC-BROAD-CAP-201"]
    assert family_state["initial_family_plan"]["spec_order"] == ["SPEC-BROAD-CAP-201"]
    assert "Planned later specs: none" in decision_text


def test_execute_spec_review_blocks_abstract_phase_plan_before_taskmaster(tmp_path: Path) -> None:
    workspace, _, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    run_id = "goalspec-review-block-301"
    emitted_at = _dt("2026-04-07T13:30:00Z")
    _write_queue_file(
        raw_goal_path,
        (
            "---\n"
            "idea_id: IDEA-301\n"
            "title: Team Workspace Vertical Slice\n"
            "decomposition_profile: moderate\n"
            "---\n\n"
            "# Team Workspace Vertical Slice\n\n"
            "Build the first usable team workspace vertical slice for collaborative planning.\n\n"
            "## Capability Domains\n"
            "- Workspace Intake\n"
            "- Shared Drafts\n\n"
            "## Progression Lines\n"
            "- Progression from intake to shared drafting to first usable proof.\n"
        ),
    )
    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=raw_goal_path.parent,
            item_path=raw_goal_path,
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    staged_path = workspace / goal_intake.research_brief_path
    execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="objective_profile_sync",
            stage_kind_id="research.objective-profile-sync",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
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
    )
    synthesis = execute_spec_synthesis(
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
        completion_manifest=completion_manifest.draft_state,
        emitted_at=emitted_at,
    )
    phase_path = workspace / synthesis.phase_spec_path
    phase_text = phase_path.read_text(encoding="utf-8")
    phase_text = _replace_markdown_section(
        phase_text,
        "Work Plan",
        "\n".join(
            [
                "## Work Plan",
                "1. Implement the first bounded capability slice.",
                "2. Add or update proof for the acceptance path.",
                "3. Close this phase with bounded handoff evidence.",
            ]
        ),
    )
    phase_path.write_text(phase_text, encoding="utf-8")
    queue_spec_path = workspace / synthesis.queue_spec_path

    with pytest.raises(
        research_plane_module.GoalSpecExecutionError,
        match="Spec Review blocked SPEC-301",
    ):
        execute_spec_review(
            paths,
            _goal_queue_checkpoint(
                run_id=run_id,
                emitted_at=emitted_at,
                queue_path=queue_spec_path.parent,
                item_path=queue_spec_path,
                status=ResearchStatus.SPEC_REVIEW_RUNNING,
                node_id="spec_review",
                stage_kind_id="research.spec-review",
            ),
            run_id=run_id,
            emitted_at=emitted_at,
        )

    review_record = json.loads(
        (
            workspace
            / "agents"
            / ".research_runtime"
            / "goalspec"
            / "spec_review"
            / f"{run_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert review_record["review_status"] == "blocked"
    assert any("Phase package defines 3 numbered Work Plan step" in item["summary"] for item in review_record["findings"])
    assert any("floor of 6" in item["summary"] for item in review_record["findings"])
    assert any("abstract or handoff-oriented work items" in item["summary"] for item in review_record["findings"])
    assert queue_spec_path.exists()
    assert not (workspace / "agents" / "ideas" / "specs_reviewed" / queue_spec_path.name).exists()


def test_execute_spec_review_blocks_whole_project_epic_phase_plan_before_taskmaster(
    tmp_path: Path,
) -> None:
    workspace, _, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    run_id = "goalspec-review-epic-304"
    emitted_at = _dt("2026-04-07T13:40:00Z")
    _write_queue_file(
        raw_goal_path,
        (
            "---\n"
            "idea_id: IDEA-304\n"
            "title: Team Workspace Campaign\n"
            "decomposition_profile: moderate\n"
            "---\n\n"
            "# Team Workspace Campaign\n\n"
            "Build the first bounded workspace workshop campaign slice for the product.\n\n"
            "## Capability Domains\n"
            "- Workspace Intake\n"
            "- Shared Drafts\n"
            "- Review Queue\n\n"
            "## Progression Lines\n"
            "- Progression from intake to shared drafting to review handoff.\n"
        ),
    )
    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=raw_goal_path.parent,
            item_path=raw_goal_path,
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    staged_path = workspace / goal_intake.research_brief_path
    execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="objective_profile_sync",
            stage_kind_id="research.objective-profile-sync",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
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
    )
    synthesis = execute_spec_synthesis(
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
        completion_manifest=completion_manifest.draft_state,
        emitted_at=emitted_at,
    )
    phase_path = workspace / synthesis.phase_spec_path
    phase_text = phase_path.read_text(encoding="utf-8")
    phase_text = _replace_markdown_section(
        phase_text,
        "Work Plan",
        "\n".join(
            [
                "## Work Plan",
                "1. Implement the entire campaign across `src/workspace/collector.py`, `src/workspace/conduit.py`, and `src/workspace/reservoir.py` in one phase.",
                "2. Consolidate the repo-wide product rollout in `src/workspace/runtime.py` and `src/workspace/app.py`.",
                "3. Run the whole project verification gate in `tests/test_workspace_flow.py` and `tests/test_workspace_balance.py`.",
                "4. Verify the full suite stays green in `tests/test_workspace_flow.py` and `tests/test_workspace_balance.py`.",
                "5. Close the entire acceptance sweep in `tests/test_workspace_e2e.py`.",
                "6. Deliver the end-to-end campaign across `src/workspace/runtime.py`, `src/workspace/app.py`, and `tests/test_workspace_e2e.py`.",
            ]
        ),
    )
    phase_path.write_text(phase_text, encoding="utf-8")
    queue_spec_path = workspace / synthesis.queue_spec_path

    with pytest.raises(
        research_plane_module.GoalSpecExecutionError,
        match="Spec Review blocked SPEC-304",
    ):
        execute_spec_review(
            paths,
            _goal_queue_checkpoint(
                run_id=run_id,
                emitted_at=emitted_at,
                queue_path=queue_spec_path.parent,
                item_path=queue_spec_path,
                status=ResearchStatus.SPEC_REVIEW_RUNNING,
                node_id="spec_review",
                stage_kind_id="research.spec-review",
            ),
            run_id=run_id,
            emitted_at=emitted_at,
        )

    review_record = json.loads(
        (
            workspace
            / "agents"
            / ".research_runtime"
            / "goalspec"
            / "spec_review"
            / f"{run_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert review_record["review_status"] == "blocked"
    assert any("execution-epic or whole-project/gate work items" in item["summary"] for item in review_record["findings"])
    assert any("whole project verification gate" in item["summary"] for item in review_record["findings"])
    assert queue_spec_path.exists()
    assert not (workspace / "agents" / "ideas" / "specs_reviewed" / queue_spec_path.name).exists()


def test_execute_spec_review_blocks_overcollapsed_phase_packages_before_taskmaster(
    tmp_path: Path,
) -> None:
    workspace, _, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    run_id = "goalspec-review-phase-count-302"
    emitted_at = _dt("2026-04-07T13:45:00Z")
    _write_queue_file(
        raw_goal_path,
        (
            "---\n"
            "idea_id: IDEA-302\n"
            "title: Team Workspace Operations Suite\n"
            "decomposition_profile: involved\n"
            "---\n\n"
            "# Team Workspace Operations Suite\n\n"
            "Build an involved workspace workshop product slice with multiple bounded follow-on phases.\n\n"
            "## Capability Domains\n"
            "- Workspace Intake\n"
            "- Shared Drafts\n"
            "- Review Queue\n"
            "- Activity Feed\n\n"
            "## Progression Lines\n"
            "- Progression from collection to routing to storage to infusion proof.\n"
        ),
    )
    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=raw_goal_path.parent,
            item_path=raw_goal_path,
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    staged_path = workspace / goal_intake.research_brief_path
    execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="objective_profile_sync",
            stage_kind_id="research.objective-profile-sync",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
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
    )
    synthesis = execute_spec_synthesis(
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
        completion_manifest=completion_manifest.draft_state,
        emitted_at=emitted_at,
    )
    phase_path = workspace / synthesis.phase_spec_path
    phase_text = phase_path.read_text(encoding="utf-8")

    assert "## Phase Packages" in phase_text
    assert "Phase key: `PHASE_01`" in phase_text
    assert "Phase key: `PHASE_02`" in phase_text
    assert "Phase key: `PHASE_03`" not in phase_text

    phase_text = _replace_markdown_section(
        phase_text,
        "Phase Packages",
        "\n".join(
            [
                "## Phase Packages",
                "- Render at least 1 bounded phase package for this involved campaign.",
                "### Phase Package 01",
                "- Phase key: `PHASE_01`",
                "- Phase priority: `P1`",
                "1. Keep the larger campaign collapsed into one giant phase package.",
                "2. Leave the remaining decomposition implied instead of explicit.",
            ]
        ),
    )
    phase_path.write_text(phase_text, encoding="utf-8")
    queue_spec_path = workspace / synthesis.queue_spec_path

    with pytest.raises(
        research_plane_module.GoalSpecExecutionError,
        match="Spec Review blocked SPEC-302",
    ):
        execute_spec_review(
            paths,
            _goal_queue_checkpoint(
                run_id=run_id,
                emitted_at=emitted_at,
                queue_path=queue_spec_path.parent,
                item_path=queue_spec_path,
                status=ResearchStatus.SPEC_REVIEW_RUNNING,
                node_id="spec_review",
                stage_kind_id="research.spec-review",
            ),
            run_id=run_id,
            emitted_at=emitted_at,
        )

    review_record = json.loads(
        (
            workspace
            / "agents"
            / ".research_runtime"
            / "goalspec"
            / "spec_review"
            / f"{run_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert review_record["review_status"] == "blocked"
    assert any("Phase package set defines 1 phase package" in item["summary"] for item in review_record["findings"])
    assert any("floor of 2" in item["summary"] for item in review_record["findings"])
    assert queue_spec_path.exists()
    assert not (workspace / "agents" / "ideas" / "specs_reviewed" / queue_spec_path.name).exists()


def test_minimum_phase_step_count_matches_bash_density_floors() -> None:
    assert minimum_phase_step_count("trivial") == 1
    assert minimum_phase_step_count("simple") == 3
    assert minimum_phase_step_count("moderate") == 6
    assert minimum_phase_step_count("involved") == 10
    assert minimum_phase_step_count("complex") == 14
    assert minimum_phase_step_count("massive") == 20
    assert minimum_phase_step_count("") == 3


def test_minimum_phase_package_count_matches_bash_phase_floors() -> None:
    assert minimum_phase_package_count("trivial") == 1
    assert minimum_phase_package_count("simple") == 1
    assert minimum_phase_package_count("moderate") == 1
    assert minimum_phase_package_count("involved") == 2
    assert minimum_phase_package_count("complex") == 2
    assert minimum_phase_package_count("massive") == 3
    assert minimum_phase_package_count("") == 1


@pytest.mark.parametrize(
    ("title", "body", "decomposition_profile", "expected_phase_keys"),
    [
        (
            "Modernize Goal Intake",
            (
                "Create real GoalSpec intake and objective sync stages.\n\n"
                "## Capability Domains\n"
                "- Goal Intake\n"
                "- Objective Profile Sync\n\n"
                "## Progression Lines\n"
                "- Progression from intake to objective sync to restart-safe completion.\n"
            ),
            "involved",
            ("PHASE_01", "PHASE_02"),
        ),
        (
            "Team Workspace Vertical Slice",
            (
                "Build the first usable team workspace vertical slice for collaborative planning.\n\n"
                "## Capability Domains\n"
                "- Workspace Intake\n"
                "- Shared Drafts\n"
                "- Review Queue\n\n"
                "## Progression Lines\n"
                "- Progression from intake to shared drafting to review handoff.\n"
            ),
            "moderate",
            ("PHASE_01",),
        ),
        (
            "Support Ticket Service",
            (
                "Build the first usable support-ticket web app for a Python service.\n\n"
                "## Capability Domains\n"
                "- Support Ticket Intake\n"
                "- Agent Workflow\n\n"
                "## Progression Lines\n"
                "- Progression from intake to triage to reply proof.\n"
            ),
            "involved",
            ("PHASE_01", "PHASE_02"),
        ),
        (
            "Neighborhood Events Hub",
            (
                "Build the first usable neighborhood events experience.\n\n"
                "## Capability Domains\n"
                "- Event Discovery\n"
                "- RSVP Tracking\n\n"
                "## Progression Lines\n"
                "- Progression from discovery to RSVP confirmation proof.\n"
            ),
            "moderate",
            ("PHASE_01",),
        ),
    ],
)
def test_execute_spec_review_accepts_generated_supported_planning_profile_phase_plans(
    tmp_path: Path,
    title: str,
    body: str,
    decomposition_profile: str,
    expected_phase_keys: tuple[str, ...],
) -> None:
    workspace, _config, _paths, synthesis, reviewed_path = _prepare_reviewed_spec_for_taskmaster(
        tmp_path,
        run_id=f"goalspec-review-supported-{sha256(title.encode('utf-8')).hexdigest()[:8]}",
        emitted_at=_dt("2026-04-09T18:00:00Z"),
        title=title,
        body=body,
        decomposition_profile=decomposition_profile,
    )

    assert reviewed_path.exists()
    phase_text = (workspace / synthesis.phase_spec_path).read_text(encoding="utf-8")
    _assert_product_grounded_stage_artifacts(
        reviewed_path.read_text(encoding="utf-8"),
        phase_text,
    )
    numbered_steps = tuple(
        line
        for line in phase_text.splitlines()
        if re.match(r"^\d+\.\s+\S", line.strip())
    )
    assert len(numbered_steps) >= minimum_phase_step_count(decomposition_profile)
    for phase_key in expected_phase_keys:
        assert f"- Phase key: `{phase_key}`" in phase_text


def test_execute_taskmaster_emits_product_first_shard_for_open_product_objective(tmp_path: Path) -> None:
    workspace, config, paths, _synthesis, reviewed_path = _prepare_reviewed_spec_for_taskmaster(
        tmp_path,
        run_id="goalspec-taskmaster-product-401",
        emitted_at=_dt("2026-04-07T14:00:00Z"),
        title="Team Workspace Vertical Slice",
        body=(
            "Build the first usable team workspace vertical slice for collaborative planning.\n\n"
            "## Capability Domains\n"
            "- Workspace Intake\n"
            "- Shared Drafts\n\n"
            "## Progression Lines\n"
            "- Progression from intake to shared drafting to first usable proof.\n"
        ),
        decomposition_profile="moderate",
    )
    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)
    assert selection is not None
    dispatch = compile_research_dispatch(
        paths,
        selection,
        run_id="goalspec-taskmaster-product-401",
        queue_discovery=discovery,
        resolve_assets=False,
    )

    result = execute_taskmaster(
        paths,
        _goal_queue_checkpoint(
            run_id="goalspec-taskmaster-product-401",
            emitted_at=_dt("2026-04-07T14:00:00Z"),
            queue_path=reviewed_path.parent,
            item_path=reviewed_path,
            status=ResearchStatus.TASKMASTER_RUNNING,
            node_id="taskmaster",
            stage_kind_id="research.taskmaster",
        ),
        dispatch=dispatch,
        run_id="goalspec-taskmaster-product-401",
        emitted_at=_dt("2026-04-07T14:00:00Z"),
    )

    shard = parse_task_store((workspace / result.shard_path).read_text(encoding="utf-8"), source_file=workspace / result.shard_path)
    record = json.loads((workspace / result.record_path).read_text(encoding="utf-8"))
    assert 6 <= result.card_count <= 10
    assert record["card_count"] == result.card_count
    assert record["profile_selection"]["expected_min_cards"] == 6
    assert record["profile_selection"]["expected_max_cards"] == 10
    assert any("src/team-workspace-vertical-slice/" in card.body for card in shard.cards)
    assert any("tests/team-workspace-vertical-slice/" in card.body for card in shard.cards)
    for card in shard.cards:
        files_to_touch = _field_block_lines(card.body, "Files to touch")
        assert files_to_touch
        assert any(not path.startswith("agents/") for path in files_to_touch)
        assert not all(path.startswith("agents/") for path in files_to_touch)

    assert not reviewed_path.exists()
    assert (workspace / result.archived_path).exists()
    family_state = json.loads(paths.goal_spec_family_state_file.read_text(encoding="utf-8"))
    assert family_state["specs"]["SPEC-401"]["status"] == "decomposed"


def test_taskmaster_card_envelope_matches_bash_profile_targets() -> None:
    assert taskmaster_card_envelope("trivial") == (1, 2)
    assert taskmaster_card_envelope("simple") == (3, 5)
    assert taskmaster_card_envelope("moderate") == (6, 10)
    assert taskmaster_card_envelope("involved") == (12, 16)
    assert taskmaster_card_envelope("complex") == (20, 28)
    assert taskmaster_card_envelope("massive") == (30, 45)
    assert taskmaster_card_envelope("") == (2, 6)
    assert taskmaster_card_envelope("unexpected-profile") == (2, 6)


def test_execute_taskmaster_accepts_open_product_objective_when_reviewed_spec_names_repo_paths(
    tmp_path: Path,
) -> None:
    workspace, config, paths, _synthesis, reviewed_path = _prepare_reviewed_spec_for_taskmaster(
        tmp_path,
        run_id="goalspec-taskmaster-product-402",
        emitted_at=_dt("2026-04-07T14:10:00Z"),
        title="Team Workspace Vertical Slice",
        body=(
            "Build the first usable team workspace vertical slice for collaborative planning.\n\n"
            "## Capability Domains\n"
            "- Workspace Intake\n"
            "- Shared Drafts\n\n"
            "## Progression Lines\n"
            "- Progression from intake to shared drafting to first usable proof.\n"
        ),
        decomposition_profile="moderate",
    )
    reviewed_text = reviewed_path.read_text(encoding="utf-8")
    reviewed_text = _replace_markdown_section(
        reviewed_text,
        "Dependencies",
        "\n".join(
            [
                "## Dependencies",
                "- Product implementation: `src/team-workspace-vertical-slice/workspace-intake`",
                "- Verification: `tests/team-workspace-vertical-slice/flow`",
            ]
        ),
    )
    reviewed_path.write_text(reviewed_text, encoding="utf-8")

    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)
    assert selection is not None
    dispatch = compile_research_dispatch(
        paths,
        selection,
        run_id="goalspec-taskmaster-product-402",
        queue_discovery=discovery,
        resolve_assets=False,
    )

    result = execute_taskmaster(
        paths,
        _goal_queue_checkpoint(
            run_id="goalspec-taskmaster-product-402",
            emitted_at=_dt("2026-04-07T14:10:00Z"),
            queue_path=reviewed_path.parent,
            item_path=reviewed_path,
            status=ResearchStatus.TASKMASTER_RUNNING,
            node_id="taskmaster",
            stage_kind_id="research.taskmaster",
        ),
        dispatch=dispatch,
        run_id="goalspec-taskmaster-product-402",
        emitted_at=_dt("2026-04-07T14:10:00Z"),
    )

    shard_text = (workspace / result.shard_path).read_text(encoding="utf-8")
    assert "src/team-workspace-vertical-slice/workspace-intake" in shard_text
    assert "tests/team-workspace-vertical-slice/flow" in shard_text
    assert "src/team-workspace-vertical-slice/" in shard_text
    assert not reviewed_path.exists()
    assert (workspace / result.archived_path).exists()


def test_execute_taskmaster_allows_honestly_internal_goal_without_repo_surface_paths(tmp_path: Path) -> None:
    workspace, config, paths, _synthesis, reviewed_path = _prepare_reviewed_spec_for_taskmaster(
        tmp_path,
        run_id="goalspec-taskmaster-internal-403",
        emitted_at=_dt("2026-04-07T14:20:00Z"),
        title="Modernize Goal Intake",
        body=(
            "Create real GoalSpec intake and objective sync stages for the Millrace research runtime.\n\n"
            "## Capability Domains\n"
            "- Goal intake artifact capture\n"
            "- Objective profile sync persistence\n\n"
            "## Progression Lines\n"
            "- Progression from raw goal intake to staged product brief to synced objective profile.\n"
        ),
        decomposition_profile="simple",
    )
    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)
    assert selection is not None
    dispatch = compile_research_dispatch(
        paths,
        selection,
        run_id="goalspec-taskmaster-internal-403",
        queue_discovery=discovery,
        resolve_assets=False,
    )

    result = execute_taskmaster(
        paths,
        _goal_queue_checkpoint(
            run_id="goalspec-taskmaster-internal-403",
            emitted_at=_dt("2026-04-07T14:20:00Z"),
            queue_path=reviewed_path.parent,
            item_path=reviewed_path,
            status=ResearchStatus.TASKMASTER_RUNNING,
            node_id="taskmaster",
            stage_kind_id="research.taskmaster",
        ),
        dispatch=dispatch,
        run_id="goalspec-taskmaster-internal-403",
        emitted_at=_dt("2026-04-07T14:20:00Z"),
    )

    shard_text = (workspace / result.shard_path).read_text(encoding="utf-8")
    record = json.loads((workspace / result.record_path).read_text(encoding="utf-8"))
    assert "millrace_engine/research/goalspec_goal_intake.py" in shard_text
    assert "tests/test_research_dispatcher.py" in shard_text
    assert "agents/specs/stable/golden/SPEC-403__modernize-goal-intake.md" in shard_text
    assert record["card_count"] == 4
    assert record["profile_selection"]["expected_min_cards"] == 3
    assert record["profile_selection"]["expected_max_cards"] == 5
    assert not reviewed_path.exists()
    assert (workspace / result.archived_path).exists()


def test_execute_taskmaster_splits_oversized_phase_step_into_traceable_suffix_cards(tmp_path: Path) -> None:
    workspace, config, paths, _synthesis, reviewed_path = _prepare_reviewed_spec_for_taskmaster(
        tmp_path,
        run_id="goalspec-taskmaster-split-404",
        emitted_at=_dt("2026-04-07T14:30:00Z"),
        title="Team Workspace Vertical Slice",
        body=(
            "Build the first usable team workspace vertical slice for collaborative planning.\n\n"
            "## Capability Domains\n"
            "- Workspace Intake\n"
            "- Shared Drafts\n\n"
            "## Progression Lines\n"
            "- Progression from intake to shared drafting to first usable proof.\n"
        ),
        decomposition_profile="moderate",
    )
    family_state = json.loads(paths.goal_spec_family_state_file.read_text(encoding="utf-8"))
    phase_path = workspace / family_state["specs"]["SPEC-404"]["stable_spec_paths"][1]
    phase_text = phase_path.read_text(encoding="utf-8")
    phase_text = _replace_markdown_section(
        phase_text,
        "Work Plan",
        "\n".join(
            [
                "## Work Plan",
                (
                    "1. Implement the broad launch slice across "
                    "`src/team-workspace-vertical-slice/entrypoint` and "
                    "`src/team-workspace-vertical-slice/workspace-intake` and "
                    "`src/team-workspace-vertical-slice/shared-drafts` and "
                    "`src/team-workspace-vertical-slice/workflow` and "
                    "`tests/team-workspace-vertical-slice/flow` and "
                    "`tests/team-workspace-vertical-slice/regression` "
                    "while preserving the same bounded product-slice contract."
                ),
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
        run_id="goalspec-taskmaster-split-404",
        queue_discovery=discovery,
        resolve_assets=False,
    )

    result = execute_taskmaster(
        paths,
        _goal_queue_checkpoint(
            run_id="goalspec-taskmaster-split-404",
            emitted_at=_dt("2026-04-07T14:30:00Z"),
            queue_path=reviewed_path.parent,
            item_path=reviewed_path,
            status=ResearchStatus.TASKMASTER_RUNNING,
            node_id="taskmaster",
            stage_kind_id="research.taskmaster",
        ),
        dispatch=dispatch,
        run_id="goalspec-taskmaster-split-404",
        emitted_at=_dt("2026-04-07T14:30:00Z"),
    )

    shard = parse_task_store((workspace / result.shard_path).read_text(encoding="utf-8"), source_file=workspace / result.shard_path)
    assert result.card_count == 6
    assert [card.title.split(" - ", 1)[0] for card in shard.cards] == [
        "SPEC-404 PHASE_01.1a",
        "SPEC-404 PHASE_01.1b",
        "SPEC-404 PHASE_01.1c",
        "SPEC-404 PHASE_01.1d",
        "SPEC-404 PHASE_01.1e",
        "SPEC-404 PHASE_01.1f",
    ]
    assert all(len(_field_block_lines(card.body, "Files to touch")) == 1 for card in shard.cards)


def test_execute_taskmaster_uses_reviewed_product_surfaces_when_phase_steps_are_text_sparse(
    tmp_path: Path,
) -> None:
    workspace, config, paths, _synthesis, reviewed_path = _prepare_reviewed_spec_for_taskmaster(
        tmp_path,
        run_id="goalspec-taskmaster-python-product-405",
        emitted_at=_dt("2026-04-09T19:00:00Z"),
        title="Support Ticket Service",
        body=(
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
        decomposition_profile="moderate",
    )
    family_state = json.loads(paths.goal_spec_family_state_file.read_text(encoding="utf-8"))
    phase_path = workspace / family_state["specs"]["SPEC-405"]["stable_spec_paths"][1]
    phase_text = _replace_markdown_section(
        phase_path.read_text(encoding="utf-8"),
        "Work Plan",
        "\n".join(
            [
                "## Work Plan",
                "1. Deliver the bounded ticket intake slice while preserving the reviewed service scope.",
                "2. Wire assignment behavior into the same product lane without widening the objective.",
                "3. Connect escalation handling to the launch path with the existing acceptance scope.",
                "4. Extend analytics reporting as a bounded continuation of the same service slice.",
                "5. Run the core service verification flow for the reviewed launch path.",
                "6. Close the release-ready support workflow verification sweep for this bounded objective.",
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
        run_id="goalspec-taskmaster-python-product-405",
        queue_discovery=discovery,
        resolve_assets=False,
    )

    result = execute_taskmaster(
        paths,
        _goal_queue_checkpoint(
            run_id="goalspec-taskmaster-python-product-405",
            emitted_at=_dt("2026-04-09T19:00:00Z"),
            queue_path=reviewed_path.parent,
            item_path=reviewed_path,
            status=ResearchStatus.TASKMASTER_RUNNING,
            node_id="taskmaster",
            stage_kind_id="research.taskmaster",
        ),
        dispatch=dispatch,
        run_id="goalspec-taskmaster-python-product-405",
        emitted_at=_dt("2026-04-09T19:00:00Z"),
    )

    shard = parse_task_store(
        (workspace / result.shard_path).read_text(encoding="utf-8"),
        source_file=workspace / result.shard_path,
    )
    assert result.card_count == 6
    assert any("src/support-ticket-service/" in card.body for card in shard.cards)
    assert any("tests/support-ticket-service/" in card.body for card in shard.cards)
    for card in shard.cards:
        files_to_touch = _field_block_lines(card.body, "Files to touch")
        assert files_to_touch
        assert len(files_to_touch) <= 2
        assert any(not path.startswith("agents/") for path in files_to_touch)


def test_execute_taskmaster_fails_closed_when_sparse_phase_plan_lacks_enough_surface_headroom(
    tmp_path: Path,
) -> None:
    workspace, config, paths, _synthesis, reviewed_path = _prepare_reviewed_spec_for_taskmaster(
        tmp_path,
        run_id="goalspec-taskmaster-python-product-406",
        emitted_at=_dt("2026-04-09T19:10:00Z"),
        title="Support Ticket Service",
        body=(
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
        decomposition_profile="moderate",
    )
    family_state = json.loads(paths.goal_spec_family_state_file.read_text(encoding="utf-8"))
    phase_path = workspace / family_state["specs"]["SPEC-406"]["stable_spec_paths"][1]
    phase_text = _replace_markdown_section(
        phase_path.read_text(encoding="utf-8"),
        "Work Plan",
        "\n".join(
            [
                "## Work Plan",
                "1. Deliver the whole bounded launch slice for the support workflow without widening scope.",
            ]
        ),
    )
    phase_path.write_text(phase_text, encoding="utf-8")

    completion_manifest_path = workspace / "agents" / "audit" / "completion_manifest.json"
    completion_manifest = json.loads(completion_manifest_path.read_text(encoding="utf-8"))
    completion_manifest["implementation_surfaces"] = [
        completion_manifest["implementation_surfaces"][0],
    ]
    completion_manifest["verification_surfaces"] = [
        completion_manifest["verification_surfaces"][0],
    ]
    completion_manifest_path.write_text(json.dumps(completion_manifest, indent=2) + "\n", encoding="utf-8")

    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)
    assert selection is not None
    dispatch = compile_research_dispatch(
        paths,
        selection,
        run_id="goalspec-taskmaster-python-product-406",
        queue_discovery=discovery,
        resolve_assets=False,
    )

    with pytest.raises(
        TaskmasterExecutionError,
        match=r"yields 2 task cards outside expected card range 6-10",
    ):
        execute_taskmaster(
            paths,
            _goal_queue_checkpoint(
                run_id="goalspec-taskmaster-python-product-406",
                emitted_at=_dt("2026-04-09T19:10:00Z"),
                queue_path=reviewed_path.parent,
                item_path=reviewed_path,
                status=ResearchStatus.TASKMASTER_RUNNING,
                node_id="taskmaster",
                stage_kind_id="research.taskmaster",
            ),
            dispatch=dispatch,
            run_id="goalspec-taskmaster-python-product-406",
            emitted_at=_dt("2026-04-09T19:10:00Z"),
        )


def test_end_to_end_product_goal_stays_product_scoped_through_taskmaster(tmp_path: Path) -> None:
    workspace, config, paths, synthesis, reviewed_path = _prepare_reviewed_spec_for_taskmaster(
        tmp_path,
        run_id="goalspec-e2e-product-701",
        emitted_at=_dt("2026-04-07T16:00:00Z"),
        title="Team Workspace Vertical Slice",
        body=(
            "Build the first usable team workspace vertical slice for collaborative planning.\n\n"
            "## Capability Domains\n"
            "- Workspace Intake\n"
            "- Shared Drafts\n"
            "- Review Queue\n"
            "- Activity Feed\n"
            "- published summary\n\n"
            "## Progression Lines\n"
            "- Progression from intake to shared drafting to review handoff.\n"
            "- Automated validation covers entry flow, collaboration state, handoff correctness, and the happy path.\n"
        ),
        decomposition_profile="moderate",
    )

    acceptance_profile_path = next((workspace / "agents" / "reports" / "acceptance_profiles").glob("*.json"))
    acceptance_profile = json.loads(acceptance_profile_path.read_text(encoding="utf-8"))
    assert acceptance_profile["semantic_profile"]["objective_summary"] == (
        "Build the first usable team workspace vertical slice for collaborative planning."
    )
    assert "Workspace Intake" in acceptance_profile["semantic_profile"]["capability_domains"]
    assert "GoalSpec" not in " ".join(acceptance_profile["milestones"])

    queue_spec_text = (workspace / synthesis.golden_spec_path).read_text(encoding="utf-8")
    phase_spec_text = (workspace / synthesis.phase_spec_path).read_text(encoding="utf-8")
    assert "Workspace Intake" in queue_spec_text
    assert "Activity Feed" in queue_spec_text
    assert "GoalSpec draft package" not in queue_spec_text
    assert "intake to shared drafting to review handoff" in phase_spec_text
    assert "reviewable runtime implementation slice" not in phase_spec_text

    reviewed_text = reviewed_path.read_text(encoding="utf-8")
    reviewed_text = _replace_markdown_section(
        reviewed_text,
        "Dependencies",
        "\n".join(
            [
                "## Dependencies",
                "- Product implementation: `src/team-workspace-vertical-slice/workspace-intake`",
                "- Product implementation: `src/team-workspace-vertical-slice/activity-feed`",
                "- Verification: `tests/team-workspace-vertical-slice/flow`",
            ]
        ),
    )
    reviewed_path.write_text(reviewed_text, encoding="utf-8")

    discovery = discover_research_queues(paths)
    selection = resolve_research_dispatch_selection(config.research.mode, discovery)
    assert selection is not None
    dispatch = compile_research_dispatch(
        paths,
        selection,
        run_id="goalspec-e2e-product-701",
        queue_discovery=discovery,
        resolve_assets=False,
    )

    result = execute_taskmaster(
        paths,
        _goal_queue_checkpoint(
            run_id="goalspec-e2e-product-701",
            emitted_at=_dt("2026-04-07T16:00:00Z"),
            queue_path=reviewed_path.parent,
            item_path=reviewed_path,
            status=ResearchStatus.TASKMASTER_RUNNING,
            node_id="taskmaster",
            stage_kind_id="research.taskmaster",
        ),
        dispatch=dispatch,
        run_id="goalspec-e2e-product-701",
        emitted_at=_dt("2026-04-07T16:00:00Z"),
    )

    shard_text = (workspace / result.shard_path).read_text(encoding="utf-8")
    assert "src/team-workspace-vertical-slice/workspace-intake" in shard_text
    assert "src/team-workspace-vertical-slice/activity-feed" in shard_text
    assert "tests/team-workspace-vertical-slice/flow" in shard_text
    assert "agents/specs/stable/golden/SPEC-701__team-workspace-vertical-slice.md" in shard_text
    assert "agents/ideas/archive/SPEC-701__team-workspace-vertical-slice.md" in shard_text
    assert "GoalSpec draft package" not in shard_text
    assert "task queue maintenance" not in shard_text


def test_research_plane_run_ready_work_keeps_workspace_seed_product_grounded_through_first_family_shard(
    tmp_path: Path,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    _write_queue_file(
        raw_goal_path,
        (
            "---\n"
            "idea_id: IDEA-WORKSPACE-710\n"
            "title: Team Workspace Vertical Slice\n"
            "decomposition_profile: moderate\n"
            "---\n\n"
            "# Team Workspace Vertical Slice\n\n"
            "Build the first usable team workspace vertical slice for collaborative planning.\n\n"
            "## Capability Domains\n"
            "- Workspace Intake\n"
            "- Shared Drafts\n"
            "- Review Queue\n"
            "- Activity Feed\n"
            "- published summary\n\n"
            "## Progression Lines\n"
            "- Progression from intake to shared drafting to review handoff.\n"
            "- Automated validation covers entry flow, collaboration state, handoff correctness, and the happy path.\n"
        ),
    )
    plane = ResearchPlane(config, paths)

    dispatch = _run_research_until_settled(
        plane,
        run_id="goalspec-workspace-auto-710",
        resolve_assets=False,
    )

    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
    assert plane.status_store.read() is ResearchStatus.IDLE
    assert plane.snapshot_state().queue_snapshot.selected_family is None

    completion_manifest = json.loads((workspace / "agents" / "audit" / "completion_manifest.json").read_text(encoding="utf-8"))
    assert completion_manifest["planning_profile"] == "generic_product"
    assert any(
        path.startswith("src/team-workspace-vertical-slice/")
        for path in (surface["path"] for surface in completion_manifest["implementation_surfaces"])
    )
    assert any(
        path.startswith("tests/team-workspace-vertical-slice/")
        for path in (surface["path"] for surface in completion_manifest["verification_surfaces"])
    )

    golden_spec_text = (
        workspace / "agents" / "specs" / "stable" / "golden" / "SPEC-WORKSPACE-710__team-workspace-vertical-slice.md"
    ).read_text(encoding="utf-8")
    phase_spec_text = (
        workspace / "agents" / "specs" / "stable" / "phase" / "SPEC-WORKSPACE-710__phase-01.md"
    ).read_text(encoding="utf-8")
    _assert_product_grounded_stage_artifacts(golden_spec_text, phase_spec_text)
    assert "src/team-workspace-vertical-slice/entrypoint" in golden_spec_text
    assert "tests/team-workspace-vertical-slice/flow" in phase_spec_text

    family_state = json.loads(paths.goal_spec_family_state_file.read_text(encoding="utf-8"))
    assert family_state["family_complete"] is True
    assert family_state["spec_order"] == ["SPEC-WORKSPACE-710"]
    assert family_state["specs"]["SPEC-WORKSPACE-710"]["status"] == "decomposed"

    taskmaster_record = json.loads(
        (
            workspace
            / "agents"
            / ".research_runtime"
            / "goalspec"
            / "taskmaster"
            / "goalspec-workspace-auto-710.json"
        ).read_text(encoding="utf-8")
    )
    assert taskmaster_record["card_count"] == 7
    taskaudit_path = (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "taskaudit"
        / "goalspec-workspace-auto-710.json"
    )
    assert taskaudit_path.exists()
    taskaudit_record = json.loads(taskaudit_path.read_text(encoding="utf-8"))
    assert taskaudit_record["status"] == "merged"
    assert taskaudit_record["pending_card_count"] == 7

    backlog = parse_task_store((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))
    assert len(backlog.cards) == 7
    _assert_product_first_task_cards(backlog.cards)
    assert any("tests/team-workspace-vertical-slice/regression" in card.body for card in backlog.cards)
    assert (workspace / "agents" / "taskspending.md").read_text(encoding="utf-8") == "# Tasks Pending\n"
    assert list((workspace / "agents" / "taskspending").glob("*")) == []


def test_research_plane_run_ready_work_merges_second_product_domain_seed_into_backlog(
    tmp_path: Path,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    _write_queue_file(
        raw_goal_path,
        (
            "---\n"
            "idea_id: IDEA-PY-711\n"
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
    )
    plane = ResearchPlane(config, paths)

    dispatch = _run_research_until_settled(
        plane,
        run_id="goalspec-python-auto-711",
        resolve_assets=False,
    )

    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
    assert plane.status_store.read() is ResearchStatus.IDLE

    completion_manifest = json.loads((workspace / "agents" / "audit" / "completion_manifest.json").read_text(encoding="utf-8"))
    assert completion_manifest["planning_profile"] == "generic_product"
    assert [surface["path"] for surface in completion_manifest["implementation_surfaces"]] == [
        "src/support-ticket-service/entrypoint",
        "src/support-ticket-service/ticket-creation-api",
        "src/support-ticket-service/agent-inbox-triage-dashboard",
        "src/support-ticket-service/escalation-notifications",
        "src/support-ticket-service/workflow",
    ]
    assert [surface["path"] for surface in completion_manifest["verification_surfaces"]] == [
        "tests/support-ticket-service/flow",
        "tests/support-ticket-service/regression",
    ]

    golden_spec_text = (
        workspace / "agents" / "specs" / "stable" / "golden" / "SPEC-PY-711__support-ticket-service.md"
    ).read_text(encoding="utf-8")
    phase_spec_text = (
        workspace / "agents" / "specs" / "stable" / "phase" / "SPEC-PY-711__phase-01.md"
    ).read_text(encoding="utf-8")
    _assert_product_grounded_stage_artifacts(golden_spec_text, phase_spec_text)
    assert "src/support-ticket-service/entrypoint" in golden_spec_text
    assert "tests/support-ticket-service/flow" in phase_spec_text

    family_state = json.loads(paths.goal_spec_family_state_file.read_text(encoding="utf-8"))
    assert family_state["family_complete"] is True
    assert family_state["spec_order"] == ["SPEC-PY-711"]
    assert family_state["specs"]["SPEC-PY-711"]["status"] == "decomposed"

    taskaudit_record = json.loads(
        (
            workspace
            / "agents"
            / ".research_runtime"
            / "goalspec"
            / "taskaudit"
            / "goalspec-python-auto-711.json"
        ).read_text(encoding="utf-8")
    )
    assert taskaudit_record["status"] == "merged"
    assert taskaudit_record["pending_card_count"] == 7
    assert taskaudit_record["backlog_card_count_after"] == 7

    backlog = parse_task_store((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))
    assert len(backlog.cards) == 7
    _assert_product_first_task_cards(backlog.cards)
    assert any("src/support-ticket-service/entrypoint" in card.body for card in backlog.cards)
    assert any("tests/support-ticket-service/regression" in card.body for card in backlog.cards)

    assert (workspace / "agents" / "taskspending.md").read_text(encoding="utf-8") == "# Tasks Pending\n"
    assert list((workspace / "agents" / "taskspending").glob("*")) == []


def test_completion_manifest_planning_guard_fails_closed_on_contaminated_semantic_labels(tmp_path: Path) -> None:
    workspace, _config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    run_id = "goalspec-plan-guard-712"
    emitted_at = _dt("2026-04-07T16:05:00Z")
    _write_queue_file(
        raw_goal_path,
        (
            "---\n"
            "idea_id: IDEA-PY-712\n"
            "title: Support Ticket Service\n"
            "decomposition_profile: moderate\n"
            "---\n\n"
            "# Support Ticket Service\n\n"
            "Build the first usable support-ticket web app for a Python service.\n\n"
            "## Capability Domains\n"
            "- Ticket creation API\n"
            "- Agent inbox triage dashboard\n\n"
            "## Progression Lines\n"
            "- Progression from ticket intake to assignment to resolution confirmation.\n"
        ),
    )
    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=raw_goal_path.parent,
            item_path=raw_goal_path,
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    staged_path = workspace / goal_intake.research_brief_path
    objective_sync = execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="objective_profile_sync",
            stage_kind_id="research.objective-profile-sync",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    profile_state = json.loads((workspace / objective_sync.profile_state_path).read_text(encoding="utf-8"))
    profile_path = workspace / profile_state["profile_path"]
    profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profile_payload["semantic_profile"]["objective_summary"] = "GoalSpec planning surface"
    profile_payload["semantic_profile"]["capability_domains"] = [
        "Stage contract",
        "agents/ideas/staging",
    ]
    profile_payload["semantic_profile"]["progression_lines"] = ["agents/_goal_intake.md"]
    profile_payload["semantic_profile"]["rejected_candidates"] = []
    profile_payload["milestones"] = ["Preserve traceability."]
    profile_path.write_text(json.dumps(profile_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(
        research_plane_module.GoalSpecExecutionError,
        match="Planner refused contaminated semantic labels for a product-scoped goal",
    ) as excinfo:
        execute_completion_manifest_draft(
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

    assert "Stage contract" in str(excinfo.value)
    assert "agents/ideas/staging" in str(excinfo.value)
    assert not paths.audit_completion_manifest_file.exists()
    assert not paths.completion_manifest_plan_file.exists()


def test_end_to_end_product_goal_meta_collapse_fails_closed_before_taskmaster_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    run_id = "goalspec-e2e-collapse-702"
    emitted_at = _dt("2026-04-07T16:10:00Z")
    _write_queue_file(
        raw_goal_path,
        (
            "---\n"
            "idea_id: IDEA-702\n"
            "title: Team Workspace Vertical Slice\n"
            "decomposition_profile: moderate\n"
            "---\n\n"
            "# Team Workspace Vertical Slice\n\n"
            "Build the first usable team workspace vertical slice for collaborative planning.\n\n"
            "## Capability Domains\n"
            "- Workspace Intake\n"
            "- Shared Drafts\n"
            "- Review Queue\n"
            "- Activity Feed\n"
        ),
    )
    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=raw_goal_path.parent,
            item_path=raw_goal_path,
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    staged_path = workspace / goal_intake.research_brief_path
    execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="objective_profile_sync",
            stage_kind_id="research.objective-profile-sync",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
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
    )

    monkeypatch.setattr(
        goalspec_spec_synthesis_module,
        "render_queue_spec",
        lambda **_: "\n".join(
            [
                "## Goals",
                "- Convert the goal into a traceable GoalSpec draft package.",
                "- Preserve completion manifest and objective profile traceability.",
                "- Prepare task generation and Spec Review handoff.",
            ]
        ),
    )
    monkeypatch.setattr(
        goalspec_spec_synthesis_module,
        "render_phase_spec",
        lambda **_: "\n".join(
            [
                "## Objective",
                "- Carry the GoalSpec package into a reviewable runtime implementation slice.",
                "",
                "## Work Plan",
                "1. Validate objective profile and completion manifest traceability.",
                "2. Preserve phase spec and queue spec alignment for task generation.",
                "3. Hand the package to Spec Review.",
            ]
        ),
    )
    monkeypatch.setattr(
        goalspec_spec_synthesis_module,
        "evaluate_scope_divergence",
        lambda **_: goalspec_scope_diagnostics_module.ScopeDivergenceRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            goal_id="IDEA-702",
            title="Team Workspace Vertical Slice",
            stage_name="spec_synthesis",
            source_path="agents/ideas/staging/IDEA-702__team-workspace-vertical-slice.md",
            expected_scope="product",
            decision="blocked",
            reason="severe_product_scope_divergence",
            summary="Deliberate meta collapse replaced product outputs with GoalSpec administration surfaces.",
            surfaces=(
                goalspec_scope_diagnostics_module.ScopeSurfaceDiagnostic(
                    surface_id="queue_spec",
                    coverage_ratio=0.0,
                    matched_goal_tokens=(),
                    missing_goal_tokens=("workspace", "collector", "conduit", "reservoir", "infuser"),
                    meta_scope_hits=("goalspec", "completion manifest", "task generation"),
                    severe=True,
                    excerpt="Convert the goal into a traceable GoalSpec draft package.",
                ),
                goalspec_scope_diagnostics_module.ScopeSurfaceDiagnostic(
                    surface_id="phase_spec",
                    coverage_ratio=0.0,
                    matched_goal_tokens=(),
                    missing_goal_tokens=("workspace", "collector", "conduit", "reservoir", "infuser"),
                    meta_scope_hits=("goalspec", "traceability", "phase spec"),
                    severe=True,
                    excerpt="Carry the GoalSpec package into a reviewable runtime implementation slice.",
                ),
            ),
        ),
    )

    with pytest.raises(
        research_plane_module.GoalSpecExecutionError,
        match="Scope divergence blocked SPEC-702 during spec_synthesis",
    ):
        execute_spec_synthesis(
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
            completion_manifest=completion_manifest.draft_state,
            emitted_at=emitted_at,
        )

    diagnostic_path = workspace / "agents" / ".research_runtime" / "goalspec" / "scope_divergence" / f"{run_id}__spec_synthesis.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["decision"] == "blocked"
    assert diagnostic["reason"] == "severe_product_scope_divergence"
    assert not any((workspace / "agents" / "taskspending").glob("SPEC-702*.md"))
    assert not any((workspace / "agents" / "ideas" / "archive").glob("SPEC-702*.md"))
    assert not any((workspace / "agents" / "ideas" / "specs").glob("SPEC-702*.md"))


def test_scope_divergence_helper_blocks_severe_meta_scope_divergence() -> None:
    anchor_tokens = goalspec_scope_diagnostics_module.build_goal_anchor_tokens(
        title="Team Workspace Vertical Slice",
        source_body=(
            "Build the first usable team workspace vertical slice for collaborative planning.\n"
            "Workspace intake gameplay.\n"
            "Shared drafts routing.\n"
            "Review queue storage.\n"
            "Progression from collection to conduit routing to infusion proof.\n"
        ),
        semantic_summary="Build the first usable team workspace vertical slice for collaborative planning.",
        capability_domains=(
            "Workspace intake gameplay",
            "Shared drafts routing",
            "Review queue storage",
        ),
        progression_lines=("Progression from collection to conduit routing to infusion proof.",),
    )
    record = goalspec_scope_diagnostics_module.evaluate_scope_divergence(
        run_id="goalspec-scope-drift-501",
        emitted_at=_dt("2026-04-07T15:00:00Z"),
        goal_id="IDEA-501",
        title="Team Workspace Vertical Slice",
        stage_name="spec_synthesis",
        source_path="agents/ideas/staging/IDEA-501.md",
        expected_scope="product",
        goal_anchor_tokens=anchor_tokens,
        surfaces=(
            (
                "queue_spec",
                "\n".join(
                    [
                        "## Goals",
                        "- Convert the goal into a traceable GoalSpec draft package.",
                        "- Preserve completion manifest and objective profile traceability.",
                        "- Prepare task generation and Spec Review handoff.",
                    ]
                ),
            ),
            (
                "phase_spec",
                "\n".join(
                    [
                        "## Objective",
                        "- Carry the GoalSpec package into a reviewable runtime implementation slice.",
                        "",
                        "## Work Plan",
                        "1. Validate objective profile and completion manifest traceability.",
                        "2. Preserve phase spec and queue spec alignment for task generation.",
                        "3. Hand the package to Spec Review.",
                    ]
                ),
            ),
        ),
    )

    assert record.decision == "blocked"
    assert record.reason == "severe_product_scope_divergence"
    severe_surfaces = {surface.surface_id for surface in record.surfaces if surface.severe}
    assert severe_surfaces == {"queue_spec", "phase_spec"}


def test_execute_spec_synthesis_fails_closed_when_scope_diagnostic_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    run_id = "goalspec-scope-drift-502"
    emitted_at = _dt("2026-04-07T15:05:00Z")
    _write_queue_file(
        raw_goal_path,
        (
            "---\n"
            "idea_id: IDEA-502\n"
            "title: Team Workspace Vertical Slice\n"
            "decomposition_profile: moderate\n"
            "---\n\n"
            "# Team Workspace Vertical Slice\n\n"
            "Build the first usable team workspace vertical slice for collaborative planning.\n"
        ),
    )
    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=raw_goal_path.parent,
            item_path=raw_goal_path,
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    staged_path = workspace / goal_intake.research_brief_path
    execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="objective_profile_sync",
            stage_kind_id="research.objective-profile-sync",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
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
    )

    monkeypatch.setattr(
        goalspec_spec_synthesis_module,
        "evaluate_scope_divergence",
        lambda **_: goalspec_scope_diagnostics_module.ScopeDivergenceRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            goal_id="IDEA-502",
            title="Team Workspace Vertical Slice",
            stage_name="spec_synthesis",
            source_path="agents/ideas/staging/IDEA-502.md",
            expected_scope="product",
            decision="blocked",
            reason="severe_product_scope_divergence",
            summary="Synthetic blocked diagnostic for fail-closed coverage.",
            surfaces=(
                goalspec_scope_diagnostics_module.ScopeSurfaceDiagnostic(
                    surface_id="queue_spec",
                    coverage_ratio=0.0,
                    matched_goal_tokens=(),
                    missing_goal_tokens=("workspace", "collector"),
                    meta_scope_hits=("goalspec", "completion manifest", "task generation"),
                    severe=True,
                    excerpt="Convert the goal into a traceable GoalSpec draft package.",
                ),
            ),
        ),
    )

    with pytest.raises(
        research_plane_module.GoalSpecExecutionError,
        match="Scope divergence blocked SPEC-502 during spec_synthesis",
    ):
        execute_spec_synthesis(
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
            completion_manifest=completion_manifest.draft_state,
            emitted_at=emitted_at,
        )

    diagnostic_path = workspace / "agents" / ".research_runtime" / "goalspec" / "scope_divergence" / f"{run_id}__spec_synthesis.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["decision"] == "blocked"
    assert diagnostic["reason"] == "severe_product_scope_divergence"


def test_execute_spec_interview_auto_resolves_repo_answerable_spec(tmp_path: Path) -> None:
    workspace, _, paths = _configured_runtime(
        tmp_path,
        mode=ResearchMode.GOALSPEC,
        interview_policy="always",
    )
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    run_id = "goalspec-interview-auto-42"
    emitted_at = _dt("2026-04-04T12:00:00Z")

    _write_queue_file(
        raw_goal_path,
        (
            "---\n"
            "idea_id: IDEA-42\n"
            "title: Support Ticket Agent Inbox\n"
            "decomposition_profile: moderate\n"
            "---\n\n"
            "# Support Ticket Agent Inbox\n\n"
            "Build the first service-agent inbox panel for a support-ticket web app.\n\n"
            "## Capability Domains\n"
            "- Ticket inbox list\n"
            "- Customer history sidebar\n\n"
            "## Progression Lines\n"
            "- Move from incoming ticket triage to customer-history review to assignment confirmation.\n"
        ),
    )
    goal_intake = execute_goal_intake(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=raw_goal_path.parent,
            item_path=raw_goal_path,
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    staged_path = workspace / goal_intake.research_brief_path
    objective = execute_objective_profile_sync(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="objective_profile_sync",
            stage_kind_id="research.objective-profile-sync",
        ),
        run_id=run_id,
        emitted_at=emitted_at,
    )
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
    )
    synthesis = execute_spec_synthesis(
        paths,
        _goal_active_request_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            path=staged_path,
            node_id="spec_synthesis",
            stage_kind_id="research.spec-synthesis",
        ),
        run_id=run_id,
        completion_manifest=completion_manifest.draft_state,
        emitted_at=emitted_at,
    )
    queue_spec_path = workspace / synthesis.queue_spec_path
    interview = execute_spec_interview(
        paths,
        _goal_queue_checkpoint(
            run_id=run_id,
            emitted_at=emitted_at,
            queue_path=queue_spec_path.parent,
            item_path=queue_spec_path,
        ).model_copy(
            update={
                "node_id": "spec_interview",
                "stage_kind_id": "research.spec-interview",
            }
        ),
        run_id=run_id,
        policy=SpecInterviewPolicy.ALWAYS,
        emitted_at=emitted_at,
    )

    assert interview.blocked is False
    assert interview.question_path == "agents/specs/questions/SPEC-42__interview-001.json"
    assert interview.decision_path == "agents/specs/decisions/SPEC-42__interview-001__decision.json"
    question_payload = json.loads((workspace / interview.question_path).read_text(encoding="utf-8"))
    decision_payload = json.loads((workspace / interview.decision_path).read_text(encoding="utf-8"))
    assert question_payload["status"] == "accepted"
    assert question_payload["answer_source"] == "repo"
    assert decision_payload["decision_source"] == "accepted_recommendation"
    assert decision_payload["question_id"] == "SPEC-42__interview-001"


def test_research_plane_records_goalspec_stage_execution_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    _write_queue_file(raw_goal_path, "# Broken Goal\n\nNeed deterministic failure coverage.\n")
    plane = ResearchPlane(config, paths)

    def _raise_goal_intake_failure(*args: object, **kwargs: object) -> object:
        raise research_plane_module.GoalSpecExecutionError("synthetic stage execution failure")

    monkeypatch.setattr(research_plane_module, "execute_goal_intake", _raise_goal_intake_failure)

    with pytest.raises(research_plane_module.GoalSpecExecutionError, match="synthetic stage execution failure"):
        plane.run_ready_work(run_id="goalspec-run-fail", resolve_assets=False)

    snapshot = plane.snapshot_state()
    assert plane.status_store.read() is ResearchStatus.BLOCKED
    assert snapshot.lock_state is None
    assert snapshot.retry_state is not None
    assert snapshot.retry_state.attempt == 1
    assert snapshot.retry_state.last_failure_reason == "synthetic stage execution failure"
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "goal_intake"


def test_research_plane_event_subscriber_records_selection_failure_without_raising(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "raw" / "goal.md")
    _write_queue_file(workspace / "agents" / "ideas" / "audit" / "incoming" / "audit.md")
    plane = ResearchPlane(config, paths)
    bus = EventBus([plane])

    bus.emit(
        EventType.IDEA_SUBMITTED,
        source=EventSource.ADAPTER,
        payload={"path": (workspace / "agents" / "ideas" / "raw" / "goal.md").as_posix()},
    )

    snapshot = plane.snapshot_state()
    assert plane.active_dispatch() is None
    assert snapshot.current_mode is ResearchRuntimeMode.AUTO
    assert snapshot.checkpoint is None
    assert snapshot.mode_reason.startswith("auto research dispatch does not support simultaneous ready queue groups")
    assert plane.status_store.read() is ResearchStatus.BLOCKED


def test_research_plane_event_subscriber_records_incident_execution_failure_without_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    incident_path = workspace / "agents" / "ideas" / "incidents" / "incoming" / "INC-EVENT-001.md"
    _write_incident_file(
        incident_path,
        incident_id="INC-EVENT-001",
        title="Subscriber incident failure coverage",
        summary="Exercise incident execution failure handling via the event subscriber.",
    )
    plane = ResearchPlane(config, paths)
    bus = EventBus([plane])

    def _raise_incident_failure(*args: object, **kwargs: object) -> object:
        raise research_plane_module.IncidentExecutionError("synthetic incident execution failure")

    monkeypatch.setattr(research_plane_module, "execute_incident_intake", _raise_incident_failure)

    bus.emit(
        EventType.NEEDS_RESEARCH,
        source=EventSource.EXECUTION,
        payload={"path": incident_path.as_posix()},
    )

    snapshot = plane.snapshot_state()
    assert plane.active_dispatch() is None
    assert snapshot.current_mode is ResearchRuntimeMode.INCIDENT
    assert snapshot.retry_state is not None
    assert snapshot.retry_state.attempt == 1
    assert snapshot.retry_state.last_failure_reason == "synthetic incident execution failure"
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.node_id == "incident_intake"
    assert plane.status_store.read() is ResearchStatus.BLOCKED


def test_research_plane_emits_runtime_visibility_events_for_successful_dispatch(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md")
    observed: list[tuple[EventType, dict[str, object]]] = []
    plane = ResearchPlane(
        config,
        paths,
        emit_event=lambda event_type, payload: observed.append((event_type, payload)),
    )

    try:
        plane.dispatch_ready_work(run_id="research-auto-run", resolve_assets=False)
        plane.shutdown()
    finally:
        plane.shutdown()

    observed_types = [event_type for event_type, _ in observed]
    assert observed_types == [
        EventType.RESEARCH_SCAN_COMPLETED,
        EventType.RESEARCH_MODE_SELECTED,
        EventType.RESEARCH_LOCK_ACQUIRED,
        EventType.RESEARCH_DISPATCH_COMPILED,
        EventType.RESEARCH_LOCK_RELEASED,
    ]
    dispatch_payload = next(payload for event_type, payload in observed if event_type is EventType.RESEARCH_DISPATCH_COMPILED)
    assert dispatch_payload["run_id"] == "research-auto-run"
    assert dispatch_payload["selected_family"] == ResearchQueueFamily.INCIDENT.value
    assert dispatch_payload["status"] == ResearchStatus.INCIDENT_INTAKE_RUNNING.value
    assert dispatch_payload["node_id"] == "incident_intake"
    assert dispatch_payload["stage_kind_id"] == "research.incident-intake"


def test_research_plane_prefers_incident_events_over_other_ready_families_in_auto_mode(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md")
    _write_queue_file(workspace / "agents" / "ideas" / "raw" / "goal.md")
    _write_queue_file(workspace / "agents" / "ideas" / "audit" / "incoming" / "audit.md")
    observed: list[tuple[EventType, dict[str, object]]] = []
    plane = ResearchPlane(
        config,
        paths,
        emit_event=lambda event_type, payload: observed.append((event_type, payload)),
    )

    dispatch = plane.dispatch_ready_work(run_id="research-auto-run", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.INCIDENT
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.INCIDENT
    observed_types = [event_type for event_type, _ in observed]
    assert observed_types == [
        EventType.RESEARCH_SCAN_COMPLETED,
        EventType.RESEARCH_MODE_SELECTED,
        EventType.RESEARCH_LOCK_ACQUIRED,
        EventType.RESEARCH_DISPATCH_COMPILED,
        EventType.RESEARCH_LOCK_RELEASED,
    ]
    dispatch_payload = next(payload for event_type, payload in observed if event_type is EventType.RESEARCH_DISPATCH_COMPILED)
    assert dispatch_payload["run_id"] == "research-auto-run"
    assert dispatch_payload["selected_family"] == ResearchQueueFamily.INCIDENT.value
    assert dispatch_payload["status"] == ResearchStatus.INCIDENT_INTAKE_RUNNING.value
    assert dispatch_payload["node_id"] == "incident_intake"


def test_research_plane_reconfigure_clears_cached_dispatch_snapshot(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md")
    plane = ResearchPlane(config, paths)

    plane.dispatch_ready_work(run_id="research-auto-run", resolve_assets=False)
    assert plane.active_dispatch() is not None

    plane.reconfigure(config, paths)

    assert plane.active_dispatch() is None
    assert plane.snapshot_state().checkpoint is not None


def test_research_plane_active_checkpoint_defers_new_requests_without_duplicate_dispatch(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md")
    plane = ResearchPlane(config, paths)

    dispatch = plane.dispatch_ready_work(run_id="research-auto-run", resolve_assets=False)
    goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    _write_queue_file(goal_path)
    plane.handle(
        EventRecord.model_validate(
            {
                "type": EventType.IDEA_SUBMITTED,
                "timestamp": "2026-03-19T12:30:00Z",
                "source": EventSource.ADAPTER,
                "payload": {"path": goal_path.as_posix()},
            }
        )
    )

    snapshot = plane.snapshot_state()
    assert dispatch is not None
    assert plane.active_dispatch() is not None
    assert plane.active_dispatch().run_id == "research-auto-run"
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.checkpoint_id == "research-auto-run"
    assert len(snapshot.deferred_requests) == 1
    assert snapshot.deferred_requests[0].event_type is EventType.IDEA_SUBMITTED


def test_research_plane_backlog_empty_event_fails_closed_when_completion_manifest_is_placeholder(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    plane = ResearchPlane(config, paths)

    plane.handle(
        EventRecord.model_validate(
            {
                "type": EventType.BACKLOG_EMPTY_AUDIT,
                "timestamp": "2026-03-21T12:30:00Z",
                "source": EventSource.EXECUTION,
                "payload": {"backlog_depth": 0},
            }
        )
    )

    failed_files = sorted((workspace / "agents" / "ideas" / "audit" / "failed").glob("*.md"))
    snapshot = plane.snapshot_state()
    discovery = discover_research_queues(paths)

    assert len(failed_files) == 1
    assert snapshot.checkpoint is None
    assert snapshot.queue_snapshot.selected_family is None
    assert plane.status_store.read() is ResearchStatus.AUDIT_FAIL
    assert snapshot.deferred_requests == ()
    assert discovery.family_scan(ResearchQueueFamily.AUDIT).items == ()

    failed_text = failed_files[0].read_text(encoding="utf-8")
    gatekeeper_files = sorted((workspace / "agents" / ".research_runtime" / "audit" / "gatekeeper").glob("*.json"))
    gatekeeper_record = json.loads(gatekeeper_files[0].read_text(encoding="utf-8"))
    gate_decision = json.loads((workspace / "agents" / "reports" / "audit_gate_decision.json").read_text(encoding="utf-8"))
    assert "trigger: queue_empty" in failed_text
    assert len(gatekeeper_files) == 1
    assert gatekeeper_record["decision"] == "audit_fail"
    assert gatekeeper_record["terminal_path"] == f"agents/ideas/audit/failed/{failed_files[0].name}"
    assert gate_decision["decision"] == "FAIL"
    assert "Completion manifest is not configured (`configured=false`)." in gate_decision["reasons"]


def test_research_plane_dispatches_explicit_audit_queue_in_forced_audit_mode(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    _write_audit_file(
        workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-700.md",
        audit_id="AUD-700",
        trigger="manual",
        status="incoming",
        scope="explicit-audit-work",
    )
    plane = ResearchPlane(config, paths)

    dispatch = plane.dispatch_ready_work(run_id="research-audit-run", resolve_assets=False)
    snapshot = plane.snapshot_state()

    assert dispatch is not None
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.AUDIT
    assert snapshot.current_mode is ResearchRuntimeMode.AUDIT
    assert snapshot.queue_snapshot.audit_ready is True
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.active_request is not None
    assert snapshot.checkpoint.active_request.event_type is EventType.AUDIT_REQUESTED
    assert snapshot.checkpoint.active_request.payload["path"].endswith("AUD-700.md")
    assert snapshot.checkpoint.active_request.audit_record is not None
    assert snapshot.checkpoint.active_request.audit_record.audit_id == "AUD-700"


def test_sync_runtime_executes_audit_stages_from_supervisor_entrypoint(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    incoming_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-701.md"
    required_command = "pytest -q tests/test_research_dispatcher.py"
    _write_audit_file(
        incoming_path,
        audit_id="AUD-701",
        trigger="manual",
        status="incoming",
        scope="compiled-audit-execution",
        commands=[required_command],
    )
    _write_completion_manifest(workspace, configured=True, commands=[required_command])
    _write_gaps_file(workspace, open_gap_count=0)
    plane = ResearchPlane(config, paths)

    dispatch = plane.sync_runtime(trigger="engine-start", run_id="audit-sync-701", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.AUDIT

    snapshot = plane.snapshot_state()
    intake_path = workspace / "agents" / ".research_runtime" / "audit" / "intake" / "audit-sync-701.json"
    execution_path = workspace / "agents" / ".research_runtime" / "audit" / "execution" / "audit-sync-701.json"
    validate_path = workspace / "agents" / ".research_runtime" / "audit" / "validate" / "audit-sync-701.json"
    gatekeeper_path = workspace / "agents" / ".research_runtime" / "audit" / "gatekeeper" / "audit-sync-701.json"
    passed_path = workspace / "agents" / "ideas" / "audit" / "passed" / "AUD-701.md"
    gate_decision_path = workspace / "agents" / "reports" / "audit_gate_decision.json"
    completion_decision_path = workspace / "agents" / "reports" / "completion_decision.json"

    assert snapshot.checkpoint is None
    assert snapshot.queue_snapshot.selected_family is None
    assert plane.status_store.read() is ResearchStatus.AUDIT_PASS
    assert intake_path.exists()
    assert execution_path.exists()
    assert validate_path.exists()
    assert gatekeeper_path.exists()
    assert passed_path.exists()
    assert gate_decision_path.exists()
    assert completion_decision_path.exists()
    assert not incoming_path.exists()

    execution_record = json.loads(execution_path.read_text(encoding="utf-8"))
    validate_record = json.loads(validate_path.read_text(encoding="utf-8"))
    gatekeeper_record = json.loads(gatekeeper_path.read_text(encoding="utf-8"))
    gate_decision = json.loads(gate_decision_path.read_text(encoding="utf-8"))
    completion_decision = json.loads(completion_decision_path.read_text(encoding="utf-8"))
    assert execution_record["artifact_type"] == "audit_execution_report"
    assert execution_record["passed"] is True
    assert execution_record["strict_contract_path"] == "packaged:agents/audit/strict_contract.json"
    assert validate_record["artifact_type"] == "audit_validate_report"
    assert validate_record["execution_report_path"] == "agents/.research_runtime/audit/execution/audit-sync-701.json"
    assert validate_record["recommended_decision"] == "pass"
    assert gatekeeper_record["artifact_type"] == "audit_gate_decision"
    assert gatekeeper_record["decision"] == "audit_pass"
    assert gatekeeper_record["terminal_path"] == "agents/ideas/audit/passed/AUD-701.md"
    assert gatekeeper_record["gate_decision_path"] == "agents/reports/audit_gate_decision.json"
    assert gatekeeper_record["completion_decision_path"] == "agents/reports/completion_decision.json"
    assert gate_decision["decision"] == "PASS"
    assert gate_decision["counts"]["completion_required"] == 1
    assert gate_decision["counts"]["completion_pass"] == 1
    assert completion_decision["decision"] == "PASS"
    assert completion_decision["gate_decision_path"] == "agents/reports/audit_gate_decision.json"


def test_sync_runtime_audit_passes_with_typed_workspace_objective_contract(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    incoming_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-701-LEGACY.md"
    required_command = "pytest -q tests/test_research_dispatcher.py"
    _write_audit_file(
        incoming_path,
        audit_id="AUD-701-LEGACY",
        trigger="manual",
        status="incoming",
        scope="legacy-workspace-objective-contract",
        commands=[required_command],
    )
    _write_completion_manifest(workspace, configured=True, commands=[required_command])
    _write_typed_objective_contract(
        workspace,
        profile_id="idea-legacy-profile",
        goal_id="IDEA-LEGACY-001",
        title="Legacy objective contract pass path",
    )
    _write_gaps_file(workspace, open_gap_count=0)
    plane = ResearchPlane(config, paths)

    dispatch = plane.sync_runtime(trigger="engine-start", run_id="audit-sync-701-legacy", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.AUDIT
    assert plane.status_store.read() is ResearchStatus.AUDIT_PASS
    assert not incoming_path.exists()

    gate_decision = json.loads((workspace / "agents" / "reports" / "audit_gate_decision.json").read_text(encoding="utf-8"))
    completion_decision = json.loads(
        (workspace / "agents" / "reports" / "completion_decision.json").read_text(encoding="utf-8")
    )
    assert gate_decision["decision"] == "PASS"
    assert gate_decision["objective_contract_path"] == "agents/objective/contract.yaml"
    assert completion_decision["decision"] == "PASS"
    assert completion_decision["objective_contract_path"] == "agents/objective/contract.yaml"


def test_sync_runtime_audit_fails_closed_for_malformed_typed_workspace_objective_contract(
    tmp_path: Path,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    incoming_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-701-BROKEN.md"
    required_command = "pytest -q tests/test_research_dispatcher.py"
    _write_audit_file(
        incoming_path,
        audit_id="AUD-701-BROKEN",
        trigger="manual",
        status="incoming",
        scope="malformed-typed-objective-contract",
        commands=[required_command],
    )
    _write_completion_manifest(workspace, configured=True, commands=[required_command])
    _write_malformed_typed_objective_contract(workspace)
    _write_gaps_file(workspace, open_gap_count=0)
    plane = ResearchPlane(config, paths)

    dispatch = plane.sync_runtime(trigger="engine-start", run_id="audit-sync-701-broken", resolve_assets=False)

    assert dispatch is not None
    assert plane.status_store.read() is ResearchStatus.AUDIT_FAIL

    gate_decision = json.loads((workspace / "agents" / "reports" / "audit_gate_decision.json").read_text(encoding="utf-8"))
    completion_decision = json.loads(
        (workspace / "agents" / "reports" / "completion_decision.json").read_text(encoding="utf-8")
    )

    assert gate_decision["decision"] == "FAIL"
    assert any(reason.startswith("Objective contract is invalid:") for reason in gate_decision["reasons"])
    assert completion_decision["decision"] == "FAIL"


def test_research_plane_handle_executes_explicit_audit_requested_event(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    incoming_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-702.md"
    required_command = "pytest -q tests/test_cli.py"
    _write_audit_file(
        incoming_path,
        audit_id="AUD-702",
        trigger="manual",
        status="incoming",
        scope="explicit-audit-event-regression",
        commands=[required_command],
    )
    _write_completion_manifest(workspace, configured=True, commands=[required_command])
    _write_gaps_file(workspace, open_gap_count=0)
    plane = ResearchPlane(config, paths)

    before_pending = plane.pending_count()
    plane.handle(
        EventRecord.model_validate(
            {
                "type": EventType.AUDIT_REQUESTED,
                "timestamp": "2026-03-21T12:30:00Z",
                "source": EventSource.EXECUTION,
                "payload": {"path": "agents/ideas/audit/incoming/AUD-702.md"},
            }
        )
    )

    snapshot = plane.snapshot_state()
    intake_files = sorted((workspace / "agents" / ".research_runtime" / "audit" / "intake").glob("*.json"))
    validate_files = sorted((workspace / "agents" / ".research_runtime" / "audit" / "validate").glob("*.json"))
    gatekeeper_files = sorted((workspace / "agents" / ".research_runtime" / "audit" / "gatekeeper").glob("*.json"))
    passed_path = workspace / "agents" / "ideas" / "audit" / "passed" / "AUD-702.md"
    gate_decision_path = workspace / "agents" / "reports" / "audit_gate_decision.json"
    completion_decision_path = workspace / "agents" / "reports" / "completion_decision.json"

    assert before_pending == 0
    assert plane.pending_count() == 0
    assert snapshot.checkpoint is None
    assert snapshot.deferred_requests == ()
    assert plane.status_store.read() is ResearchStatus.AUDIT_PASS
    assert len(intake_files) == 1
    assert len(validate_files) == 1
    assert len(gatekeeper_files) == 1
    assert passed_path.exists()
    assert gate_decision_path.exists()
    assert completion_decision_path.exists()
    assert not incoming_path.exists()

    intake_record = json.loads(intake_files[0].read_text(encoding="utf-8"))
    validate_record = json.loads(validate_files[0].read_text(encoding="utf-8"))
    gatekeeper_record = json.loads(gatekeeper_files[0].read_text(encoding="utf-8"))
    assert intake_record["audit_id"] == "AUD-702"
    assert intake_record["working_path"] == "agents/ideas/audit/working/AUD-702.md"
    assert validate_record["audit_id"] == "AUD-702"
    assert validate_record["working_path"] == "agents/ideas/audit/working/AUD-702.md"
    assert gatekeeper_record["decision"] == "audit_pass"
    assert gatekeeper_record["terminal_path"] == "agents/ideas/audit/passed/AUD-702.md"
    assert json.loads(gate_decision_path.read_text(encoding="utf-8"))["decision"] == "PASS"
    assert json.loads(completion_decision_path.read_text(encoding="utf-8"))["decision"] == "PASS"


def test_sync_runtime_audit_fail_remediation_enqueues_backlog_and_records_operator_story(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    incoming_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-703.md"
    required_command = "pytest -q tests/test_research_dispatcher.py --fast"
    _write_audit_file(
        incoming_path,
        audit_id="AUD-703",
        trigger="manual",
        status="incoming",
        scope="command-contract-guard-strict",
        commands=[required_command],
        summaries=["Open issues detected: 1"],
    )
    _write_completion_manifest(workspace, configured=True, commands=[required_command])
    _write_gaps_file(workspace, open_gap_count=0)
    (workspace / "agents" / "audit").mkdir(parents=True, exist_ok=True)
    (workspace / "agents" / "audit" / "strict_contract.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "contract_id": "strict-command-guard-test",
                "enabled": True,
                "description": "Fail closed when sampled commands or missing summaries are observed.",
                "required_command_substrings": [
                    "pytest -q tests/test_research_dispatcher.py",
                ],
                "forbidden_command_markers": ["--fast"],
                "required_summaries": ["Open issues detected: 0"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    plane = ResearchPlane(config, paths)

    dispatch = plane.sync_runtime(trigger="engine-start", run_id="audit-sync-703", resolve_assets=False)

    assert dispatch is not None
    execution_path = workspace / "agents" / ".research_runtime" / "audit" / "execution" / "audit-sync-703.json"
    validate_path = workspace / "agents" / ".research_runtime" / "audit" / "validate" / "audit-sync-703.json"
    gatekeeper_path = workspace / "agents" / ".research_runtime" / "audit" / "gatekeeper" / "audit-sync-703.json"
    remediation_path = workspace / "agents" / ".research_runtime" / "audit" / "remediation" / "audit-sync-703.json"
    failed_path = workspace / "agents" / "ideas" / "audit" / "failed" / "AUD-703.md"
    gate_decision_path = workspace / "agents" / "reports" / "audit_gate_decision.json"
    completion_decision_path = workspace / "agents" / "reports" / "completion_decision.json"
    audit_history_path = workspace / "agents" / "audit_history.md"
    audit_summary_path = workspace / "agents" / "audit_summary.json"

    assert plane.status_store.read() is ResearchStatus.AUDIT_FAIL
    assert execution_path.exists()
    assert validate_path.exists()
    assert gatekeeper_path.exists()
    assert remediation_path.exists()
    assert failed_path.exists()
    assert gate_decision_path.exists()
    assert completion_decision_path.exists()
    assert audit_history_path.exists()
    assert audit_summary_path.exists()
    assert not incoming_path.exists()

    execution_record = json.loads(execution_path.read_text(encoding="utf-8"))
    validate_record = json.loads(validate_path.read_text(encoding="utf-8"))
    gatekeeper_record = json.loads(gatekeeper_path.read_text(encoding="utf-8"))
    remediation_record = json.loads(remediation_path.read_text(encoding="utf-8"))
    gate_decision = json.loads(gate_decision_path.read_text(encoding="utf-8"))
    completion_decision = json.loads(completion_decision_path.read_text(encoding="utf-8"))
    audit_summary = json.loads(audit_summary_path.read_text(encoding="utf-8"))
    backlog_cards = parse_task_store(
        (workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"),
        source_file=workspace / "agents" / "tasksbacklog.md",
    ).cards

    assert execution_record["artifact_type"] == "audit_execution_report"
    assert execution_record["contract_id"] == "strict-command-guard-test"
    assert execution_record["strict_contract_path"] == "agents/audit/strict_contract.json"
    assert execution_record["passed"] is False
    assert execution_record["finding_count"] == 2
    assert [finding["kind"] for finding in execution_record["findings"]] == [
        "forbidden_command_marker",
        "missing_required_summary",
    ]
    assert execution_record["findings"][0]["expected"] == "--fast"
    assert execution_record["findings"][1]["expected"] == "Open issues detected: 0"
    assert validate_record["recommended_decision"] == "fail"
    assert validate_record["execution_report_path"] == "agents/.research_runtime/audit/execution/audit-sync-703.json"
    assert validate_record["finding_count"] == 2
    assert gatekeeper_record["decision"] == "audit_fail"
    assert gatekeeper_record["terminal_path"] == "agents/ideas/audit/failed/AUD-703.md"
    assert gatekeeper_record["remediation_record_path"] == "agents/.research_runtime/audit/remediation/audit-sync-703.json"
    assert gatekeeper_record["remediation_spec_id"] == "SPEC-AUD-703-REMEDIATION"
    assert gate_decision["decision"] == "FAIL"
    assert "Forbidden command marker `--fast` found in observed commands." in gate_decision["reasons"]
    assert completion_decision["decision"] == "FAIL"
    assert remediation_record["artifact_type"] == "audit_remediation"
    assert remediation_record["selected_action"] == "enqueue_backlog_task"
    assert remediation_record["remediation_spec_id"] == "SPEC-AUD-703-REMEDIATION"
    assert remediation_record["gate_decision_path"] == "agents/reports/audit_gate_decision.json"
    assert remediation_record["completion_decision_path"] == "agents/reports/completion_decision.json"
    assert remediation_record["remediation_task_title"] == "Remediate failed audit AUD-703"
    assert len(backlog_cards) == 1
    assert backlog_cards[0].spec_id == "SPEC-AUD-703-REMEDIATION"
    assert backlog_cards[0].title == "Remediate failed audit AUD-703"
    assert "Audit-Gate-Decision" in backlog_cards[0].body
    assert audit_summary["counts"] == {"total": 1, "pass": 0, "fail": 1}
    assert audit_summary["last_outcome"]["status"] == "AUDIT_FAIL"
    assert audit_summary["last_outcome"]["audit_id"] == "AUD-703"
    assert audit_summary["last_outcome"]["decision"] == "FAIL"
    assert audit_summary["last_outcome"]["remediation_spec_id"] == "SPEC-AUD-703-REMEDIATION"
    audit_history_text = audit_history_path.read_text(encoding="utf-8")
    assert "Audit: `AUD-703` :: Audit AUD-703" in audit_history_text
    assert "Remediation: `SPEC-AUD-703-REMEDIATION`" in audit_history_text


def test_sync_runtime_queue_empty_audit_goal_gap_review_fails_without_remediation_enqueue(
    tmp_path: Path,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    incoming_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-705.md"
    required_command = "pytest -q tests/test_research_dispatcher.py"
    _write_audit_file(
        incoming_path,
        audit_id="AUD-705",
        trigger="queue_empty",
        status="incoming",
        scope="marathon-goal-gap-review",
        commands=[required_command],
    )
    _write_completion_manifest(workspace, configured=True, commands=[required_command])
    _write_typed_objective_contract(
        workspace,
        profile_id="goal-gap-profile",
        goal_id="IDEA-GOAL-GAP-001",
        title="Restore marathon goal-gap review",
        source_path="agents/ideas/raw/goal-gap.md",
        require_open_gaps_zero=False,
        semantic_milestones=[
            {
                "id": "MILESTONE-GAP-001",
                "outcome": "Restore marathon goal gap review parity",
                "capability_scope": ["goal gap review", "marathon audit"],
            },
            {
                "id": "MILESTONE-GAP-002",
                "outcome": "Keep deterministic audit pass path intact",
                "capability_scope": ["audit gatekeeper"],
            },
        ],
    )
    _write_gaps_file(
        workspace,
        open_rows=[
            {
                "gap_id": "GAP-101",
                "title": "Restore marathon goal gap review parity",
                "area": "research",
                "owner": "qa",
                "severity": "S2",
                "notes": "MILESTONE-GAP-001 remains unresolved in queue-empty completion flow.",
            }
        ],
    )
    plane = ResearchPlane(config, paths)

    dispatch = plane.sync_runtime(trigger="engine-start", run_id="audit-sync-705", resolve_assets=False)

    gatekeeper_path = workspace / "agents" / ".research_runtime" / "audit" / "gatekeeper" / "audit-sync-705.json"
    goal_gap_review_path = workspace / "agents" / "reports" / "goal_gap_review.json"
    goal_gap_review_markdown_path = workspace / "agents" / "reports" / "goal_gap_review.md"
    gate_decision_path = workspace / "agents" / "reports" / "audit_gate_decision.json"
    completion_decision_path = workspace / "agents" / "reports" / "completion_decision.json"
    audit_summary_path = workspace / "agents" / "audit_summary.json"
    audit_history_path = workspace / "agents" / "audit_history.md"
    failed_path = workspace / "agents" / "ideas" / "audit" / "failed" / "AUD-705.md"

    assert dispatch is not None
    assert plane.status_store.read() is ResearchStatus.AUDIT_FAIL
    assert gatekeeper_path.exists()
    assert goal_gap_review_path.exists()
    assert goal_gap_review_markdown_path.exists()
    assert gate_decision_path.exists()
    assert completion_decision_path.exists()
    assert audit_summary_path.exists()
    assert audit_history_path.exists()
    assert failed_path.exists()
    assert not incoming_path.exists()

    gatekeeper_record = json.loads(gatekeeper_path.read_text(encoding="utf-8"))
    goal_gap_review = json.loads(goal_gap_review_path.read_text(encoding="utf-8"))
    gate_decision = json.loads(gate_decision_path.read_text(encoding="utf-8"))
    completion_decision = json.loads(completion_decision_path.read_text(encoding="utf-8"))
    audit_summary = json.loads(audit_summary_path.read_text(encoding="utf-8"))
    backlog_cards = parse_task_store(
        (workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"),
        source_file=workspace / "agents" / "tasksbacklog.md",
    ).cards

    assert gatekeeper_record["decision"] == "audit_fail"
    assert gatekeeper_record["deterministic_decision"] == "PASS"
    assert gatekeeper_record["goal_gap_review_path"] == "agents/reports/goal_gap_review.json"
    assert gatekeeper_record["goal_gap_review_status"] == "goal_gaps"
    assert gatekeeper_record["goal_gap_count"] == 1
    assert gatekeeper_record["goal_gap_remediation_selection_path"] is None
    assert gatekeeper_record["goal_gap_remediation_idea_path"] is None
    assert gatekeeper_record["remediation_record_path"] is None
    assert gatekeeper_record["remediation_spec_id"] is None
    assert gate_decision["decision"] == "PASS"
    assert completion_decision["decision"] == "PASS"
    assert goal_gap_review["artifact_type"] == "audit_goal_gap_review"
    assert goal_gap_review["overall_status"] == "goal_gaps"
    assert goal_gap_review["goal_gap_count"] == 1
    assert goal_gap_review["unresolved_milestone_ids"] == ["MILESTONE-GAP-001"]
    assert goal_gap_review["review_path"] == "agents/reports/goal_gap_review.json"
    assert goal_gap_review["markdown_path"] == "agents/reports/goal_gap_review.md"
    assert goal_gap_review["milestones"][0]["status"] == "goal_gap"
    assert goal_gap_review["milestones"][0]["matched_gaps"][0]["gap_id"] == "GAP-101"
    assert goal_gap_review["milestones"][1]["status"] == "satisfied"
    assert backlog_cards == []
    assert audit_summary["last_outcome"]["status"] == "AUDIT_FAIL"
    assert audit_summary["last_outcome"]["decision"] == "FAIL"
    assert audit_summary["last_outcome"]["deterministic_decision"] == "PASS"
    assert audit_summary["last_outcome"]["goal_gap_review_status"] == "goal_gaps"
    assert audit_summary["last_outcome"]["goal_gap_count"] == 1
    assert audit_summary["last_outcome"]["goal_gap_review_path"] == "agents/reports/goal_gap_review.json"
    audit_history_text = audit_history_path.read_text(encoding="utf-8")
    assert "Goal gap review: `goal_gaps` (1 unresolved milestone(s))" in audit_history_text
    assert "Goal gap review record: `agents/reports/goal_gap_review.json`" in audit_history_text


def test_sync_runtime_queue_empty_audit_stages_goal_gap_remediation_family_for_auto_follow_on(
    tmp_path: Path,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    goal_path = workspace / "agents" / "objective" / "goal-gap-source.md"
    goal_path.parent.mkdir(parents=True, exist_ok=True)
    goal_path.write_text(
        "\n".join(
            [
                "---",
                "idea_id: IDEA-GOAL-GAP-001",
                "title: Goal gap remediation source goal",
                "---",
                "",
                "# Goal gap remediation source goal",
                "",
                "Restore queue-empty marathon audit parity.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "agents" / "objective").mkdir(parents=True, exist_ok=True)
    (workspace / "agents" / "objective" / "family_policy.json").write_text(
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
    incoming_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-706.md"
    required_command = "pytest -q tests/test_research_dispatcher.py"
    _write_audit_file(
        incoming_path,
        audit_id="AUD-706",
        trigger="queue_empty",
        status="incoming",
        scope="goal-gap-remediation-family",
        commands=[required_command],
    )
    _write_completion_manifest(workspace, configured=True, commands=[required_command])
    _write_typed_objective_contract(
        workspace,
        profile_id="goal-gap-family-profile",
        goal_id="IDEA-GOAL-GAP-001",
        title="Goal gap remediation family objective",
        source_path="agents/objective/goal-gap-source.md",
        require_open_gaps_zero=False,
        semantic_milestones=[
            {
                "id": "MILESTONE-GAP-010",
                "outcome": "Restore marathon goal gap remediation family staging",
                "capability_scope": ["goal gap remediation", "marathon audit"],
            }
        ],
    )
    _write_gaps_file(
        workspace,
        open_rows=[
            {
                "gap_id": "GAP-201",
                "title": "Restore marathon goal gap remediation family staging",
                "area": "research",
                "owner": "qa",
                "severity": "S2",
                "notes": "MILESTONE-GAP-010 remains unresolved after the queue-empty completion pass.",
            }
        ],
    )
    plane = ResearchPlane(config, paths)

    dispatch = plane.sync_runtime(trigger="engine-start", run_id="audit-sync-706", resolve_assets=False)

    selection_path = workspace / "agents" / "reports" / "goal_gap_remediation_selection.json"
    selection_markdown_path = workspace / "agents" / "reports" / "goal_gap_remediation_selection.md"
    staged_idea_path = workspace / "agents" / "ideas" / "staging" / "IDEA-GOAL-GAP-001__goal-gap-remediation.md"
    family_state_path = paths.goal_spec_family_state_file
    gatekeeper_path = workspace / "agents" / ".research_runtime" / "audit" / "gatekeeper" / "audit-sync-706.json"

    assert dispatch is not None
    assert plane.status_store.read() is ResearchStatus.AUDIT_FAIL
    assert selection_path.exists()
    assert selection_markdown_path.exists()
    assert staged_idea_path.exists()
    assert family_state_path.exists()
    assert gatekeeper_path.exists()

    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    family_state = json.loads(family_state_path.read_text(encoding="utf-8"))
    gatekeeper_record = json.loads(gatekeeper_path.read_text(encoding="utf-8"))
    staged_text = staged_idea_path.read_text(encoding="utf-8")

    assert selection["artifact_type"] == "audit_goal_gap_remediation_selection"
    assert selection["goal_id"] == "IDEA-GOAL-GAP-001"
    assert selection["family_phase"] == "goal_gap_remediation"
    assert selection["total_remediation_items"] == 1
    assert selection["family_decomposition_profile"] == "trivial"
    assert selection["applied_family_max_specs"] == 1
    assert selection["synthesized_remediation_ids"] == ["REMED-MILESTONE-GAP-010"]
    assert selection["output_idea_path"] == "agents/ideas/staging/IDEA-GOAL-GAP-001__goal-gap-remediation.md"
    assert selection["deferred_milestone_ids"] == []
    assert family_state["goal_id"] == "IDEA-GOAL-GAP-001"
    assert family_state["source_idea_path"] == "agents/ideas/staging/IDEA-GOAL-GAP-001__goal-gap-remediation.md"
    assert family_state["family_phase"] == "goal_gap_remediation"
    assert family_state["family_complete"] is False
    assert family_state["spec_order"] == []
    assert family_state["family_governor"]["applied_family_max_specs"] == 1
    assert gatekeeper_record["goal_gap_remediation_selection_path"] == "agents/reports/goal_gap_remediation_selection.json"
    assert gatekeeper_record["goal_gap_remediation_idea_path"] == (
        "agents/ideas/staging/IDEA-GOAL-GAP-001__goal-gap-remediation.md"
    )
    assert "family_phase: goal_gap_remediation" in staged_text
    assert "canonical_source_path: agents/objective/goal-gap-source.md" in staged_text
    assert "decomposition_profile: trivial" in staged_text

    discovery = discover_research_queues(paths)
    assert discovery.family_scan(ResearchQueueFamily.GOALSPEC).ready is True

    next_dispatch = plane.dispatch_ready_work(run_id="goal-gap-remediation-follow-on", resolve_assets=False)

    assert next_dispatch is not None
    assert next_dispatch.selection.runtime_mode is ResearchRuntimeMode.GOALSPEC
    assert next_dispatch.entry_stage.node_id == "objective_profile_sync"


def test_sync_runtime_blocks_completion_when_tasks_pending_and_open_gaps_remain(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    incoming_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-704.md"
    required_command = "pytest -q tests/test_cli.py"
    _write_audit_file(
        incoming_path,
        audit_id="AUD-704",
        trigger="manual",
        status="incoming",
        scope="completion-blockers",
        commands=[required_command],
    )
    _write_completion_manifest(workspace, configured=True, commands=[required_command])
    _write_gaps_file(workspace, open_gap_count=2)
    (workspace / "agents" / "tasks.md").write_text(
        "\n".join(
            [
                "# Active Task",
                "",
                "## 2026-03-21 - Active blocker task",
                "",
                "- **Goal:** Keep completion blocked while active work remains.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "agents" / "tasksbacklog.md").write_text(
        "\n".join(
            [
                "# Backlog",
                "",
                "## 2026-03-21 - Backlog blocker task",
                "",
                "- **Goal:** Keep completion blocked while backlog work remains.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "agents" / "taskspending.md").write_text(
        "\n".join(
            [
                "# Pending Task Shards",
                "",
                "## 2026-03-21 - Pending blocker task",
                "",
                "- **Goal:** Keep completion blocked while pending work remains.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    plane = ResearchPlane(config, paths)

    dispatch = plane.sync_runtime(trigger="engine-start", run_id="audit-sync-704", resolve_assets=False)

    assert dispatch is not None
    assert plane.status_store.read() is ResearchStatus.AUDIT_FAIL
    gate_decision = json.loads((workspace / "agents" / "reports" / "audit_gate_decision.json").read_text(encoding="utf-8"))
    completion_decision = json.loads(
        (workspace / "agents" / "reports" / "completion_decision.json").read_text(encoding="utf-8")
    )
    assert gate_decision["decision"] == "FAIL"
    assert completion_decision["decision"] == "FAIL"
    assert gate_decision["counts"]["active_task_cards"] == 1
    assert gate_decision["counts"]["backlog_cards"] == 1
    assert gate_decision["counts"]["pending_task_cards"] == 1
    assert gate_decision["counts"]["task_store_cards"] == 3
    assert gate_decision["counts"]["open_gaps"] == 2
    assert "Active task store still has 1 task card(s)." in gate_decision["reasons"]
    assert "Backlog still has 1 task card(s)." in gate_decision["reasons"]
    assert "Pending task store still has 1 task card(s)." in gate_decision["reasons"]
    assert "2 actionable open gap row(s) remain in `agents/gaps.md`." in gate_decision["reasons"]


def test_research_plane_blocker_dispatch_restores_parent_handoff_from_recovery_latch(tmp_path: Path) -> None:
    workspace, config_path, loaded, paths, queue, active_task = _configure_auto_blocker_runtime(
        tmp_path,
        title="Restore parent handoff",
        goal="Exercise blocker dispatch from the recovery latch.",
        acceptance="Research restores the execution parent link without breadcrumbs.",
    )
    initial_breadcrumb_count = len([path for path in paths.deferred_dir.iterdir() if path.is_file()])

    _, latch_path, _, handoff = _quarantine_blocker_with_handoff(
        queue,
        active_task,
        workspace=workspace,
        incident_path="agents/ideas/incidents/incoming/INC-PARENT-001.md",
        diagnostics_name="diag-parent-handoff",
        handoff_id="execution-run-123:needs_research:20260319T120000Z",
        parent_run_id="execution-run-123",
        frozen_plan_id="frozen-plan:abc123",
        frozen_plan_hash="abc123",
    )

    plane = ResearchPlane(loaded.config, paths)
    dispatch = plane.dispatch_ready_work(run_id="research-blocker-run", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.BLOCKER
    snapshot = plane.snapshot_state()
    assert snapshot.deferred_requests == ()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.active_request is not None
    assert snapshot.checkpoint.active_request.event_type is EventType.NEEDS_RESEARCH
    assert snapshot.checkpoint.parent_handoff == handoff
    assert snapshot.checkpoint.active_request.handoff == handoff
    assert len([path for path in paths.deferred_dir.iterdir() if path.is_file()]) == initial_breadcrumb_count

    persisted_latch = load_research_recovery_latch(latch_path)
    assert persisted_latch is not None
    assert persisted_latch.handoff == handoff

    report = EngineControl(config_path).research_report()
    assert report.runtime.checkpoint is not None
    assert report.runtime.checkpoint.parent_handoff == handoff
    assert report.deferred_breadcrumb_count == initial_breadcrumb_count


@pytest.mark.parametrize(
    ("title", "goal", "acceptance", "incident_path", "diagnostics_name", "handoff_id_template", "parent_run_id",
     "frozen_plan_id", "frozen_plan_hash", "deferred_requests_factory", "remaining_task_id",
     "remaining_recovery_batch_id"),
    [
        (
            "Match blocker request",
            "Ensure blocker dispatch chooses the request that matches the latch handoff.",
            "Active blocker request payload matches the execution handoff task.",
            "agents/ideas/incidents/incoming/INC-MATCH-001.md",
            "diag-matching-blocker",
            "execution-run-456:needs_research:{batch_id}",
            "execution-run-456",
            "frozen-plan:def456",
            "def456",
            lambda task_id, task_title: [
                _blocker_deferred_request(task_id="2026-03-19__unrelated-blocker", received_at="2026-03-19T12:00:00Z"),
                _blocker_deferred_request(task_id=task_id, received_at="2026-03-19T12:01:00Z"),
            ],
            "2026-03-19__unrelated-blocker",
            None,
        ),
        (
            "Preserve unrelated blocker request",
            "Ensure latch restoration does not hijack a blocker request from another batch.",
            "Research synthesizes the active request from the latch and leaves the queued request intact.",
            "agents/ideas/incidents/incoming/INC-BATCH-MISMATCH-001.md",
            "diag-batch-mismatch",
            "execution-run-batch:needs_research:{batch_id}",
            "execution-run-batch",
            "frozen-plan:batch123",
            "batch123",
            lambda task_id, task_title: [
                _blocker_deferred_request(
                    task_id=task_id,
                    received_at="2026-03-19T12:00:00Z",
                    handoff={
                        "handoff_id": "execution-run-other:needs_research:other-batch",
                        "parent_run": {
                            "plane": "execution",
                            "run_id": "execution-run-other",
                        },
                        "task_id": task_id,
                        "task_title": task_title,
                        "stage": "Consult",
                        "reason": "Earlier blocker batch",
                        "status": ExecutionStatus.NEEDS_RESEARCH.value,
                        "recovery_batch_id": "other-batch",
                        "failure_signature": "other-signature",
                        "frozen_backlog_cards": 1,
                        "retained_backlog_cards": 0,
                    },
                )
            ],
            "__ACTIVE_TASK__",
            "other-batch",
        ),
    ],
    ids=["matching-latch-request", "batch-mismatched-request"],
)
def test_research_plane_blocker_dispatch_reconciles_deferred_requests_against_latch_handoff(
    tmp_path: Path,
    title: str,
    goal: str,
    acceptance: str,
    incident_path: str,
    diagnostics_name: str,
    handoff_id_template: str,
    parent_run_id: str,
    frozen_plan_id: str,
    frozen_plan_hash: str,
    deferred_requests_factory,
    remaining_task_id: str,
    remaining_recovery_batch_id: str | None,
) -> None:
    workspace, _, loaded, paths, queue, active_task = _configure_auto_blocker_runtime(
        tmp_path,
        title=title,
        goal=goal,
        acceptance=acceptance,
    )
    _, _, latch, handoff = _quarantine_blocker_with_handoff(
        queue,
        active_task,
        workspace=workspace,
        incident_path=incident_path,
        diagnostics_name=diagnostics_name,
        handoff_id=handoff_id_template.format(batch_id="{batch_id}"),
        parent_run_id=parent_run_id,
        frozen_plan_id=frozen_plan_id,
        frozen_plan_hash=frozen_plan_hash,
    )
    handoff = handoff.model_copy(update={"handoff_id": handoff_id_template.format(batch_id=latch.batch_id)})
    (workspace / "agents/.runtime/research_recovery_latch.json").write_text(
        latch.model_copy(update={"handoff": handoff}).model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    _write_research_runtime_state(
        workspace,
        deferred_requests=deferred_requests_factory(active_task.task_id, active_task.title),
    )

    plane = ResearchPlane(loaded.config, paths)
    dispatch = plane.dispatch_ready_work(run_id="research-blocker-run", resolve_assets=False)

    assert dispatch is not None
    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.active_request is not None
    assert snapshot.checkpoint.active_request.payload["task_id"] == active_task.task_id
    assert snapshot.checkpoint.active_request.handoff == handoff
    assert snapshot.checkpoint.parent_handoff == handoff
    assert len(snapshot.deferred_requests) == 1
    expected_remaining_task_id = active_task.task_id if remaining_task_id == "__ACTIVE_TASK__" else remaining_task_id
    assert snapshot.deferred_requests[0].payload["task_id"] == expected_remaining_task_id
    if remaining_recovery_batch_id is None:
        assert snapshot.deferred_requests[0].handoff is None
    else:
        assert snapshot.deferred_requests[0].handoff is not None
        assert snapshot.deferred_requests[0].handoff.recovery_batch_id == remaining_recovery_batch_id


def test_research_plane_resume_checkpoint_restores_parent_handoff_from_recovery_latch(
    tmp_path: Path,
) -> None:
    workspace, _, loaded, paths, queue, active_task = _configure_auto_blocker_runtime(
        tmp_path,
        title="Resume blocker handoff",
        goal="Restore parent linkage when a blocker checkpoint resumes after restart.",
        acceptance="Missing handoff fields are rehydrated from the recovery latch.",
    )
    _, _, _, handoff = _quarantine_blocker_with_handoff(
        queue,
        active_task,
        workspace=workspace,
        incident_path="agents/ideas/incidents/incoming/INC-RESUME-001.md",
        diagnostics_name="diag-resume-blocker",
        handoff_id="execution-run-789:needs_research:placeholder-batch",
        parent_run_id="execution-run-789",
        frozen_plan_id="frozen-plan:ghi789",
        frozen_plan_hash="ghi789",
    )
    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    latch = load_research_recovery_latch(latch_path)
    assert latch is not None
    handoff = handoff.model_copy(update={"handoff_id": f"execution-run-789:needs_research:{latch.batch_id}"})
    latch_path.write_text(
        latch.model_copy(update={"handoff": handoff}).model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )

    initial_plane = ResearchPlane(loaded.config, paths)
    initial_dispatch = initial_plane.dispatch_ready_work(run_id="research-blocker-run", resolve_assets=False)
    assert initial_dispatch is not None
    state_payload = initial_plane.snapshot_state().model_dump(mode="json")
    initial_plane.shutdown()

    checkpoint_payload = state_payload["checkpoint"]
    assert checkpoint_payload is not None
    checkpoint_payload.pop("parent_handoff", None)
    active_request_payload = checkpoint_payload["active_request"]
    assert active_request_payload is not None
    active_request_payload.pop("handoff", None)
    state_path = workspace / "agents/research_state.json"
    state_path.write_text(json.dumps(state_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    resumed_plane = ResearchPlane(loaded.config, paths)
    resumed_dispatch = resumed_plane.sync_runtime(trigger="engine-start", resolve_assets=False)

    assert resumed_dispatch is not None
    assert resumed_dispatch.run_id == "research-blocker-run"
    snapshot = resumed_plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.parent_handoff == handoff
    assert snapshot.checkpoint.active_request is not None
    assert snapshot.checkpoint.active_request.handoff == handoff


def test_research_plane_resume_checkpoint_preserves_existing_parent_handoff_when_latch_mismatches(
    tmp_path: Path,
) -> None:
    workspace, _, loaded, paths, queue, active_task = _configure_auto_blocker_runtime(
        tmp_path,
        title="Preserve resumed parent handoff",
        goal="Prevent restart-time latch drift from overwriting a checkpoint's existing parent handoff.",
        acceptance="Resume keeps the checkpoint's original execution parent linkage when the latch points at another batch.",
    )
    diagnostics_dir, _, latch, preserved_handoff = _quarantine_blocker_with_handoff(
        queue,
        active_task,
        workspace=workspace,
        incident_path="agents/ideas/incidents/incoming/INC-RESUME-PRESERVE-001.md",
        diagnostics_name="diag-resume-preserve",
        handoff_id="execution-run-preserved:needs_research:placeholder-batch",
        parent_run_id="execution-run-preserved",
        frozen_plan_id="frozen-plan:preserved123",
        frozen_plan_hash="preserved123",
    )
    preserved_handoff = preserved_handoff.model_copy(
        update={"handoff_id": f"execution-run-preserved:needs_research:{latch.batch_id}"}
    )
    mismatched_handoff = ExecutionResearchHandoff(
        handoff_id="execution-run-mismatch:needs_research:other-batch",
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id="execution-run-mismatch",
            snapshot_id="snapshot-execution-run-mismatch",
            frozen_plan_id="frozen-plan:mismatch456",
            frozen_plan_hash="mismatch456",
            transition_history_path=Path("agents/runs/execution-run-mismatch/transition_history.jsonl"),
        ),
        task_id="2026-03-19__other-task",
        task_title="Other task",
        stage="Consult",
        reason="Different blocker batch",
        incident_path=Path("agents/ideas/incidents/incoming/INC-OTHER-BATCH-001.md"),
        diagnostics_dir=diagnostics_dir,
        recovery_batch_id="other-batch",
        failure_signature="other-signature",
    )
    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    latch_path.write_text(
        latch.model_copy(update={"handoff": mismatched_handoff}).model_dump_json(indent=2, exclude_none=True)
        + "\n",
        encoding="utf-8",
    )

    initial_plane = ResearchPlane(loaded.config, paths)
    initial_dispatch = initial_plane.dispatch_ready_work(run_id="research-blocker-run", resolve_assets=False)
    assert initial_dispatch is not None
    state_payload = initial_plane.snapshot_state().model_dump(mode="json")
    initial_plane.shutdown()

    checkpoint_payload = state_payload["checkpoint"]
    assert checkpoint_payload is not None
    checkpoint_payload["parent_handoff"] = preserved_handoff.model_dump(mode="json")
    active_request_payload = checkpoint_payload["active_request"]
    assert active_request_payload is not None
    active_request_payload["handoff"] = preserved_handoff.model_dump(mode="json")
    state_path = workspace / "agents/research_state.json"
    state_path.write_text(json.dumps(state_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    resumed_plane = ResearchPlane(loaded.config, paths)
    resumed_dispatch = resumed_plane.sync_runtime(trigger="engine-start", resolve_assets=False)

    assert resumed_dispatch is not None
    snapshot = resumed_plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.parent_handoff == preserved_handoff
    assert snapshot.checkpoint.active_request is not None
    assert snapshot.checkpoint.active_request.handoff == preserved_handoff


def test_research_plane_lock_failures_block_closed_and_visible(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md")
    first_plane = ResearchPlane(config, paths)
    second_plane = ResearchPlane(config, paths)

    first_dispatch = first_plane.dispatch_ready_work(run_id="lock-owner", resolve_assets=False)

    with pytest.raises(ResearchLockUnavailableError, match="research loop lock is already held"):
        second_plane.dispatch_ready_work(resolve_assets=False)

    snapshot = second_plane.snapshot_state()
    assert first_dispatch is not None
    assert snapshot.checkpoint is None
    assert snapshot.mode_reason.startswith("research loop lock is already held")
    assert second_plane.status_store.read() is ResearchStatus.BLOCKED


def test_research_plane_compile_failure_releases_lock_for_retryable_reentry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md")
    first_plane = ResearchPlane(config, paths)
    second_plane = ResearchPlane(config, paths)
    original_compile = research_plane_module.compile_research_dispatch
    failed_once = False

    def flaky_compile(*args, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise CompiledResearchDispatchError("transient compile failure")
        return original_compile(*args, **kwargs)

    monkeypatch.setattr(research_plane_module, "compile_research_dispatch", flaky_compile)

    with pytest.raises(CompiledResearchDispatchError, match="transient compile failure"):
        first_plane.dispatch_ready_work(resolve_assets=False)

    failed_snapshot = first_plane.snapshot_state()
    assert failed_snapshot.lock_state is None
    assert failed_snapshot.retry_state is not None
    assert failed_snapshot.mode_reason == "transient compile failure"

    dispatch = second_plane.dispatch_ready_work(run_id="after-failure", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.run_id == "after-failure"
    assert second_plane.snapshot_state().checkpoint is not None


def test_research_resume_failure_preserves_owned_queue_snapshot_truthfully(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "research_retry_restart_resume")
    loaded = load_engine_config(config_path)
    plane = ResearchPlane(loaded.config, build_runtime_paths(loaded.config))

    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:05:01Z"))
    monkeypatch.setattr(
        research_plane_module,
        "compile_research_dispatch",
        lambda *args, **kwargs: (_ for _ in ()).throw(CompiledResearchDispatchError("transient compile failure")),
    )

    with pytest.raises(CompiledResearchDispatchError, match="transient compile failure"):
        plane.sync_runtime(trigger="engine-start", resolve_assets=False)

    snapshot = plane.snapshot_state()
    assert snapshot.lock_state is None
    assert snapshot.retry_state is not None
    assert snapshot.retry_state.attempt == 2
    assert snapshot.checkpoint is not None
    assert snapshot.queue_snapshot.selected_family is ResearchQueueFamily.INCIDENT
    assert snapshot.queue_snapshot.selected_family_authority is ResearchQueueSelectionAuthority.CHECKPOINT
    assert snapshot.queue_snapshot.ownerships == snapshot.checkpoint.owned_queues
    assert snapshot.deferred_requests[0].event_type is EventType.BACKLOG_EMPTY_AUDIT
    assert snapshot.checkpoint.deferred_follow_ons[0].event_type is EventType.IDEA_SUBMITTED


def test_research_plane_resume_checkpoint_uses_checkpoint_authority_when_family_is_no_longer_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    plane = ResearchPlane(config, paths)
    checkpoint = ResearchCheckpoint.model_validate(
        {
            "checkpoint_id": "research-goalspec-restart",
            "mode": "GOALSPEC",
            "status": "SPEC_REVIEW_RUNNING",
            "node_id": "spec_review",
            "stage_kind_id": "research.spec-review",
            "attempt": 1,
            "started_at": "2026-03-19T12:00:00Z",
            "updated_at": "2026-03-19T12:04:00Z",
            "owned_queues": [
                {
                    "family": "goalspec",
                    "queue_path": "agents/ideas/specs_reviewed",
                    "item_path": "agents/ideas/specs_reviewed/SPEC-100.md",
                    "owner_token": "research-goalspec-restart",
                    "acquired_at": "2026-03-19T12:00:00Z",
                }
            ],
        }
    )
    plane.state = plane.state.model_copy(update={"checkpoint": checkpoint})
    captured: dict[str, object] = {}

    def fake_compile(paths_arg, selection, **kwargs):
        captured["selection"] = selection
        captured["run_id"] = kwargs["run_id"]
        return object()

    monkeypatch.setattr(research_plane_module, "compile_research_dispatch", fake_compile)

    dispatch = plane._resume_checkpoint(
        trigger="engine-start",
        resolve_assets=False,
        observed_at=_dt("2026-03-19T12:05:00Z"),
    )

    selection = captured["selection"]
    assert dispatch is not None
    assert captured["run_id"] == "research-goalspec-restart"
    assert selection.queue_snapshot.goalspec_ready is False
    assert selection.queue_snapshot.selected_family is ResearchQueueFamily.GOALSPEC
    assert selection.queue_snapshot.selected_family_authority is ResearchQueueSelectionAuthority.CHECKPOINT
    assert selection.queue_snapshot.ownerships == checkpoint.owned_queues


def test_research_resume_failure_stops_retrying_after_budget_is_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "research_retry_restart_resume")
    loaded = load_engine_config(config_path)
    plane = ResearchPlane(loaded.config, build_runtime_paths(loaded.config))
    compile_attempts = 0

    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:05:01Z"))

    def fail_compile(*args, **kwargs):
        nonlocal compile_attempts
        compile_attempts += 1
        raise CompiledResearchDispatchError("transient compile failure")

    monkeypatch.setattr(research_plane_module, "compile_research_dispatch", fail_compile)

    with pytest.raises(CompiledResearchDispatchError, match="transient compile failure"):
        plane.sync_runtime(trigger="engine-start", resolve_assets=False)

    failed_snapshot = plane.snapshot_state()
    assert failed_snapshot.retry_state is not None
    assert failed_snapshot.retry_state.attempt == failed_snapshot.retry_state.max_attempts == 2
    assert failed_snapshot.retry_state.next_retry_at is None
    assert failed_snapshot.mode_reason == "transient compile failure"
    assert failed_snapshot.lock_state is None
    assert compile_attempts == 1

    assert plane.sync_runtime(trigger="engine-start", resolve_assets=False) is None

    resumed_snapshot = plane.snapshot_state()
    assert compile_attempts == 1
    assert resumed_snapshot == failed_snapshot
    assert resumed_snapshot.checkpoint is not None
    assert resumed_snapshot.queue_snapshot.ownerships == resumed_snapshot.checkpoint.owned_queues
    assert plane.status_store.read() is ResearchStatus.BLOCKED


def test_research_plane_poll_mode_only_rescans_when_due(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    config.research.idle_mode = "poll"
    config.research.idle_poll_seconds = 10
    plane = ResearchPlane(config, paths)

    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:00:00Z"))
    assert plane.sync_runtime(trigger="daemon-loop", resolve_assets=False) is None
    assert plane.snapshot_state().cycle_count == 1
    assert plane.snapshot_state().next_poll_at == _dt("2026-03-19T12:00:10Z")

    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:00:05Z"))
    assert plane.sync_runtime(trigger="daemon-loop", resolve_assets=False) is None
    assert plane.snapshot_state().cycle_count == 1

    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:00:11Z"))
    assert plane.sync_runtime(trigger="daemon-loop", resolve_assets=False) is None
    assert plane.snapshot_state().cycle_count == 2


def test_research_plane_watch_mode_scans_only_on_explicit_trigger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    config.research.idle_mode = "watch"
    plane = ResearchPlane(config, paths)

    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:00:00Z"))
    assert plane.sync_runtime(trigger="daemon-loop", resolve_assets=False) is None
    assert plane.snapshot_state().cycle_count == 0

    assert plane.sync_runtime(trigger="engine-start", resolve_assets=False) is None
    assert plane.snapshot_state().cycle_count == 1
    assert plane.snapshot_state().next_poll_at is None


def test_research_retry_restart_resume_waits_for_backoff_then_resumes_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "research_retry_restart_resume")
    loaded = load_engine_config(config_path)
    plane = ResearchPlane(loaded.config, build_runtime_paths(loaded.config))

    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:04:59Z"))
    assert plane.sync_runtime(trigger="engine-start", resolve_assets=False) is None
    assert plane.active_dispatch() is None
    assert plane.snapshot_state().lock_state is None
    assert plane.snapshot_state().retry_state is not None
    assert plane.snapshot_state().checkpoint is not None
    assert plane.snapshot_state().checkpoint.checkpoint_id == "research-restart-run"
    assert plane.snapshot_state().deferred_requests[0].event_type is EventType.BACKLOG_EMPTY_AUDIT

    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:05:01Z"))
    dispatch = plane.sync_runtime(trigger="engine-start", resolve_assets=False)

    snapshot = plane.snapshot_state()
    assert dispatch is not None
    assert dispatch.run_id == "research-restart-run"
    assert snapshot.retry_state is None
    assert snapshot.checkpoint is None
    assert snapshot.deferred_requests[0].event_type is EventType.BACKLOG_EMPTY_AUDIT
    assert snapshot.queue_snapshot.ownerships == ()
    assert snapshot.queue_snapshot.selected_family is None
    assert snapshot.current_mode is ResearchRuntimeMode.AUTO
    assert (workspace / "agents" / "research_status.md").read_text(encoding="utf-8") == "### IDLE\n"
    assert (workspace / "agents" / "ideas" / "incidents" / "archived" / "incident.md").exists()
    assert load_research_runtime_state(workspace / "agents" / "research_state.json") == snapshot


def test_engine_cycle_boundary_config_apply_dispatches_ready_research_queue(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    _write_queue_file(workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md")
    engine = MillraceEngine(config_path)
    reloaded = load_engine_config(config_path)
    reloaded.config.research.mode = ResearchMode.AUTO

    operation, restart_watcher = engine._queue_or_apply_reloaded_config(reloaded, command_id="cfg-1")
    restart_on_apply = engine._apply_pending_config_if_due(ConfigApplyBoundary.CYCLE_BOUNDARY)

    assert operation.applied is True
    assert restart_watcher is False
    assert restart_on_apply is False
    snapshot = engine.research_plane.snapshot_state()
    assert snapshot.current_mode is ResearchRuntimeMode.AUTO
    assert snapshot.checkpoint is None
    assert snapshot.queue_snapshot.selected_family is None
    assert engine.research_plane.status_store.read() is ResearchStatus.IDLE
    assert (workspace / "agents" / "ideas" / "incidents" / "archived" / "incident.md").exists()


def test_engine_start_syncs_ready_research_queue_before_runtime_loop(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    _persist_research_mode(config_path, ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md")
    engine = MillraceEngine(config_path)

    engine.start(once=True)

    state = load_research_runtime_state(workspace / "agents" / "research_state.json")
    assert state is not None
    assert state.current_mode is ResearchRuntimeMode.AUTO
    assert state.checkpoint is None
    assert state.queue_snapshot.selected_family is None
    assert (workspace / "agents" / "research_status.md").read_text(encoding="utf-8") == "### IDLE\n"
    assert (workspace / "agents" / "ideas" / "incidents" / "archived" / "incident.md").exists()


def test_engine_start_backlog_empty_enqueues_audit_and_fails_closed_completion(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "backlog_empty")
    _persist_research_mode(config_path, ResearchMode.AUDIT)
    update_driver = _write_update_stage_driver(tmp_path)
    engine = MillraceEngine(
        config_path,
        stage_commands={StageType.UPDATE: [sys.executable, str(update_driver)]},
    )

    engine.start(once=True)

    state = load_research_runtime_state(workspace / "agents" / "research_state.json")
    assert state is not None
    assert state.current_mode is ResearchRuntimeMode.AUDIT
    assert state.checkpoint is None
    assert state.queue_snapshot.selected_family is None
    assert (workspace / "agents" / "research_status.md").read_text(encoding="utf-8") == "### AUDIT_FAIL\n"

    failed_files = sorted((workspace / "agents" / "ideas" / "audit" / "failed").glob("*.md"))
    assert len(failed_files) == 1
    failed_text = failed_files[0].read_text(encoding="utf-8")
    assert "trigger: queue_empty" in failed_text

    gate_decision = json.loads((workspace / "agents" / "reports" / "audit_gate_decision.json").read_text(encoding="utf-8"))
    completion_decision = json.loads(
        (workspace / "agents" / "reports" / "completion_decision.json").read_text(encoding="utf-8")
    )
    assert gate_decision["decision"] == "FAIL"
    assert completion_decision["decision"] == "FAIL"
    assert "Completion manifest is not configured (`configured=false`)." in gate_decision["reasons"]

    event_types = [
        json.loads(line)["type"]
        for line in (workspace / "agents" / "engine_events.log").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert EventType.BACKLOG_EMPTY_AUDIT.value in event_types


def test_engine_start_records_truthful_auto_failure_state_without_crashing(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    _persist_research_mode(config_path, ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "raw" / "goal.md")
    _write_queue_file(workspace / "agents" / "ideas" / "audit" / "incoming" / "audit.md")
    engine = MillraceEngine(config_path)

    engine.start(once=True)

    state = load_research_runtime_state(workspace / "agents" / "research_state.json")
    assert state is not None
    assert state.current_mode is ResearchRuntimeMode.AUTO
    assert state.checkpoint is None
    assert state.mode_reason.startswith("auto research dispatch does not support simultaneous ready queue groups")
    assert (workspace / "agents" / "research_status.md").read_text(encoding="utf-8") == "### BLOCKED\n"


def test_engine_start_records_truthful_incident_execution_failure_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    _persist_research_mode(config_path, ResearchMode.AUTO)
    incident_path = workspace / "agents" / "ideas" / "incidents" / "incoming" / "INC-ENGINE-001.md"
    _write_incident_file(
        incident_path,
        incident_id="INC-ENGINE-001",
        title="Engine seam incident failure coverage",
        summary="Ensure engine sync preserves truthful blocked state on incident execution failure.",
    )
    engine = MillraceEngine(config_path)

    def _raise_incident_failure(*args: object, **kwargs: object) -> object:
        raise research_plane_module.IncidentExecutionError("synthetic incident execution failure")

    monkeypatch.setattr(research_plane_module, "execute_incident_intake", _raise_incident_failure)

    engine.start(once=True)

    state = load_research_runtime_state(workspace / "agents" / "research_state.json")
    assert state is not None
    assert state.current_mode is ResearchRuntimeMode.INCIDENT
    assert state.retry_state is not None
    assert state.retry_state.attempt == 1
    assert state.retry_state.last_failure_reason == "synthetic incident execution failure"
    assert state.checkpoint is not None
    assert state.checkpoint.node_id == "incident_intake"
    assert (workspace / "agents" / "research_status.md").read_text(encoding="utf-8") == "### BLOCKED\n"


def test_engine_start_records_truthful_audit_execution_failure_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    _persist_research_mode(config_path, ResearchMode.AUDIT)
    audit_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUDIT-ENGINE-001.md"
    _write_audit_file(
        audit_path,
        audit_id="AUDIT-ENGINE-001",
        scope="engine-seam-audit-failure",
        summaries=["Ensure engine sync preserves truthful blocked state on audit execution failure."],
    )
    engine = MillraceEngine(config_path)

    def _raise_audit_failure(*args: object, **kwargs: object) -> object:
        raise research_plane_module.AuditExecutionError("synthetic audit execution failure")

    monkeypatch.setattr(research_plane_module, "execute_audit_intake", _raise_audit_failure)

    engine.start(once=True)

    state = load_research_runtime_state(workspace / "agents" / "research_state.json")
    assert state is not None
    assert state.current_mode is ResearchRuntimeMode.AUDIT
    assert state.retry_state is not None
    assert state.retry_state.attempt == 1
    assert state.retry_state.last_failure_reason == "synthetic audit execution failure"
    assert state.checkpoint is not None
    assert state.checkpoint.node_id == "audit_intake"
    assert (workspace / "agents" / "research_status.md").read_text(encoding="utf-8") == "### BLOCKED\n"


def test_engine_start_resumes_checkpoint_from_restart_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "research_retry_restart_resume")
    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:05:01Z"))
    engine = MillraceEngine(config_path)

    engine.start(once=True)

    state = load_research_runtime_state(workspace / "agents" / "research_state.json")
    assert state is not None
    assert state.current_mode is ResearchRuntimeMode.AUTO
    assert state.lock_state is None
    assert state.retry_state is None
    assert state.checkpoint is None
    assert state.deferred_requests[0].event_type is EventType.BACKLOG_EMPTY_AUDIT
    assert (workspace / "agents" / "research_status.md").read_text(encoding="utf-8") == "### IDLE\n"
    assert (workspace / "agents" / "ideas" / "incidents" / "archived" / "incident.md").exists()


def test_engine_start_repeated_once_unwedges_restart_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "research_retry_restart_resume")
    engine = MillraceEngine(config_path)

    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:04:59Z"))
    engine.start(once=True)

    first_state = load_research_runtime_state(workspace / "agents" / "research_state.json")
    assert first_state is not None
    assert first_state.retry_state is not None
    assert first_state.checkpoint is not None
    assert first_state.checkpoint.node_id == "incident_intake"
    assert (workspace / "agents" / "research_status.md").read_text(encoding="utf-8") == "### INCIDENT_INTAKE_RUNNING\n"

    monkeypatch.setattr(research_plane_module, "_utcnow", lambda: _dt("2026-03-19T12:05:01Z"))
    engine.start(once=True)

    second_state = load_research_runtime_state(workspace / "agents" / "research_state.json")
    assert second_state is not None
    assert second_state.current_mode is ResearchRuntimeMode.AUTO
    assert second_state.retry_state is None
    assert second_state.checkpoint is None
    assert second_state.deferred_requests[0].event_type is EventType.BACKLOG_EMPTY_AUDIT
    assert (workspace / "agents" / "research_status.md").read_text(encoding="utf-8") == "### IDLE\n"
    assert (workspace / "agents" / "ideas" / "incidents" / "archived" / "incident.md").exists()


def test_research_package_re_exports_dispatcher_integration_surface() -> None:
    assert CompiledResearchDispatchError.__name__ == "CompiledResearchDispatchError"


def test_research_runtime_state_upgrade_migration_materializes_breadcrumbs_explicitly(tmp_path: Path) -> None:
    workspace, _config, paths = _configured_runtime(tmp_path, mode=ResearchMode.STUB)
    breadcrumb_path = paths.deferred_dir / "idea-submitted.json"
    _write_json_file(
        breadcrumb_path,
        {
            "event_type": EventType.IDEA_SUBMITTED.value,
            "received_at": "2026-04-04T12:05:00Z",
            "payload": {"idea_id": "IDEA-DISPATCHER-BREADCRUMB-001"},
        },
    )

    preview = preview_research_runtime_state_migration(
        paths.research_state_file,
        deferred_dir=paths.deferred_dir,
    )

    assert preview.action == "materialize_from_breadcrumbs"
    assert preview.would_write_state_file is True
    assert preview.breadcrumb_file_count == 1

    report = apply_research_runtime_state_migration(
        paths.research_state_file,
        deferred_dir=paths.deferred_dir,
    )

    assert report.action == "materialize_from_breadcrumbs"
    assert report.wrote_state_file is True
    assert breadcrumb_path.exists()
    snapshot = load_research_runtime_state(paths.research_state_file, deferred_dir=paths.deferred_dir)
    assert snapshot is not None
    assert snapshot.deferred_requests[0].event_type is EventType.IDEA_SUBMITTED
