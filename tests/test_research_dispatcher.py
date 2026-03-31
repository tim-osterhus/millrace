from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import sys

import pytest

import millrace_engine.planes.research as research_plane_module
from millrace_engine.config import ConfigApplyBoundary, build_runtime_paths, load_engine_config
from millrace_engine.contracts import (
    CrossPlaneParentRun,
    ExecutionResearchHandoff,
    ExecutionStatus,
    ResearchMode,
    ResearchStatus,
    StageType,
)
from millrace_engine.control import EngineControl
from millrace_engine.engine import MillraceEngine
from millrace_engine.events import EventBus, EventRecord, EventSource, EventType
from millrace_engine.markdown import parse_task_store
from millrace_engine.planes.research import ResearchLockUnavailableError, ResearchPlane
from millrace_engine.queue import TaskQueue, load_research_recovery_latch
from millrace_engine.research import CompiledResearchDispatchError, entry_stage_type_for_dispatch
from millrace_engine.research.dispatcher import (
    UnsupportedResearchQueueCombinationError,
    compile_research_dispatch,
    resolve_research_dispatch_selection,
)
from millrace_engine.research.goalspec import (
    execute_completion_manifest_draft,
    execute_goal_intake,
    execute_objective_profile_sync,
    execute_spec_review,
    execute_spec_synthesis,
)
from millrace_engine.research.queues import discover_research_queues
from millrace_engine.research.specs import GoalSpecFamilyState, build_initial_family_plan_snapshot
from millrace_engine.research.state import (
    ResearchCheckpoint,
    ResearchQueueFamily,
    ResearchQueueOwnership,
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
) -> tuple[Path, object, object]:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    loaded = load_engine_config(config_path)
    loaded.config.research.mode = mode
    return workspace, loaded.config, build_runtime_paths(loaded.config)


def _write_queue_file(path: Path, body: str = "# queued\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


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
                    "require_open_gaps_zero": True,
                },
                "objective_profile": {
                    "profile_id": profile_id,
                    "title": title,
                    "source_path": source_path,
                    "updated_at": updated_at,
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


def _write_gaps_file(workspace: Path, *, open_gap_count: int = 0) -> None:
    gaps_path = workspace / "agents" / "gaps.md"
    lines = [
        "# Gaps",
        "",
        "## Open Gaps",
        "",
        "| Gap ID | Title | Area | Owner | Severity | Status | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
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


def test_auto_selection_rejects_unsupported_ready_queue_combo(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "raw" / "goal.md")
    _write_queue_file(workspace / "agents" / "ideas" / "audit" / "incoming" / "audit.md")

    plane = ResearchPlane(config, paths)

    with pytest.raises(UnsupportedResearchQueueCombinationError, match="simultaneous ready queue groups"):
        plane.dispatch_ready_work(resolve_assets=False)

    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is None
    assert snapshot.queue_snapshot.goalspec_ready is True
    assert snapshot.queue_snapshot.audit_ready is True
    assert snapshot.mode_reason.startswith("auto research dispatch does not support simultaneous ready queue groups")


def test_research_plane_dispatches_compiled_auto_plan(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md")
    plane = ResearchPlane(config, paths)

    dispatch = plane.dispatch_ready_work(run_id="research-auto-run", resolve_assets=False)

    assert dispatch is not None
    assert dispatch.selection.runtime_mode is ResearchRuntimeMode.INCIDENT
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.BLOCKER
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
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.BLOCKER
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


def test_research_plane_run_ready_work_executes_auto_goalspec_stages_through_taskaudit(tmp_path: Path) -> None:
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
    assert snapshot.current_mode is ResearchRuntimeMode.AUTO
    assert snapshot.checkpoint is None
    assert snapshot.lock_state is None
    assert snapshot.queue_snapshot.selected_family is None
    assert snapshot.queue_snapshot.ownerships == ()
    assert plane.status_store.read() is ResearchStatus.IDLE

    archived_rel_path = (
        "agents/ideas/archive/raw/"
        f"goal__goalspec-auto-run__{sha256(raw_goal_text.encode('utf-8')).hexdigest()[:12]}.md"
    )
    assert not raw_goal_path.exists()
    assert (workspace / archived_rel_path).exists()
    assert (
        workspace / "agents" / ".research_runtime" / "goalspec" / "goal_intake" / "goalspec-auto-run.json"
    ).exists()
    assert (
        workspace / "agents" / ".research_runtime" / "goalspec" / "taskmaster" / "goalspec-auto-run.json"
    ).exists()
    assert (
        workspace / "agents" / ".research_runtime" / "goalspec" / "taskaudit" / "goalspec-auto-run.json"
    ).exists()
    backlog = parse_task_store((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))
    assert len(backlog.cards) == 3
    assert all(card.spec_id == "SPEC-AUTO-42" for card in backlog.cards)


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
    assert dispatch.selection.queue_snapshot.selected_family is ResearchQueueFamily.BLOCKER
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


def test_taskaudit_pending_merge(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.GOALSPEC)
    raw_goal_path = workspace / "agents" / "ideas" / "raw" / "goal.md"
    raw_goal_text = (
        "---\n"
        "idea_id: IDEA-42\n"
        "title: Modernize Goal Intake\n"
        "decomposition_profile: moderate\n"
        "---\n\n"
        "# Modernize Goal Intake\n\n"
        "Create real GoalSpec intake and objective sync stages.\n"
    )
    _write_queue_file(raw_goal_path, raw_goal_text)
    pending_scaffold = paths.taskspending_file.read_text(encoding="utf-8")
    plane = ResearchPlane(config, paths)

    dispatch = plane.run_ready_work(run_id="goalspec-run-42", resolve_assets=False)

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
    assert [card.spec_id for card in backlog.cards] == ["SPEC-42", "SPEC-42", "SPEC-42"]
    assert [card.title.split(" - ", 1)[0] for card in backlog.cards] == [
        "SPEC-42 PHASE_01.1",
        "SPEC-42 PHASE_01.2",
        "SPEC-42 PHASE_01.3",
    ]
    assert [card.requirement_ids for card in backlog.cards] == [
        ("REQ-001", "REQ-002"),
        ("REQ-001", "REQ-002"),
        ("REQ-001", "REQ-002"),
    ]
    assert [card.acceptance_ids for card in backlog.cards] == [
        ("AC-001", "AC-002"),
        ("AC-001", "AC-002"),
        ("AC-001", "AC-002"),
    ]

    finished_text = finished_source_path.read_text(encoding="utf-8")
    stale_deferred_text = "Spec synthesis and review are intentionally deferred to later Phase 05 runs."
    downstream_pending_text = "Spec Review and task generation remain downstream after this draft synthesis pass."
    assert "status: finished" in finished_text
    assert "## Route Decision" in finished_text
    assert "agents/_goal_intake.md" in finished_text
    assert archived_reviewed_path.read_text(encoding="utf-8") == golden_spec_path.read_text(encoding="utf-8")
    assert "agents/audit/completion_manifest.json" in archived_reviewed_path.read_text(encoding="utf-8")
    assert "SPEC-42" in decision_path.read_text(encoding="utf-8")
    assert stale_deferred_text not in profile_md_path.read_text(encoding="utf-8")
    assert stale_deferred_text not in completion_manifest_path.read_text(encoding="utf-8")
    assert stale_deferred_text not in completion_report_path.read_text(encoding="utf-8")
    assert stale_deferred_text not in archived_reviewed_path.read_text(encoding="utf-8")
    assert downstream_pending_text in profile_md_path.read_text(encoding="utf-8")
    assert downstream_pending_text in completion_manifest_path.read_text(encoding="utf-8")
    assert downstream_pending_text in completion_report_path.read_text(encoding="utf-8")
    assert downstream_pending_text in archived_reviewed_path.read_text(encoding="utf-8")
    assert "No material delta" in review_questions_path.read_text(encoding="utf-8")
    assert "`no_material_delta`" in review_decision_path.read_text(encoding="utf-8")

    goal_intake_record = json.loads(goal_intake_record_path.read_text(encoding="utf-8"))
    assert goal_intake_record["schema_version"] == "1.0"
    assert goal_intake_record["run_id"] == "goalspec-run-42"
    assert goal_intake_record["source_path"] == "agents/ideas/raw/goal.md"
    assert goal_intake_record["archived_source_path"] == archived_rel_path
    assert goal_intake_record["research_brief_path"] == "agents/ideas/staging/IDEA-42__modernize-goal-intake.md"

    profile_state = json.loads(profile_state_path.read_text(encoding="utf-8"))
    assert profile_state["schema_version"] == "1.0"
    assert profile_state["run_id"] == "goalspec-run-42"
    assert profile_state["goal_intake_record_path"] == (
        "agents/.research_runtime/goalspec/goal_intake/goalspec-run-42.json"
    )
    assert profile_state["profile_path"] == "agents/reports/acceptance_profiles/idea-42-profile.json"

    profile_json = json.loads(profile_json_path.read_text(encoding="utf-8"))
    assert profile_json["goal_id"] == "IDEA-42"
    assert profile_json["run_id"] == "goalspec-run-42"
    assert profile_json["research_brief_path"] == "agents/ideas/staging/IDEA-42__modernize-goal-intake.md"
    assert profile_json["hard_blockers"] == [downstream_pending_text]

    completion_manifest = json.loads(completion_manifest_path.read_text(encoding="utf-8"))
    assert completion_manifest["artifact_type"] == "completion_manifest_draft"
    assert completion_manifest["goal_id"] == "IDEA-42"
    assert completion_manifest["objective_profile_path"] == "agents/reports/acceptance_profiles/idea-42-profile.json"
    assert completion_manifest["open_questions"] == [downstream_pending_text]
    assert completion_manifest["required_outputs"][0]["path"] == "agents/ideas/specs/SPEC-42__modernize-goal-intake.md"

    completion_record = json.loads(completion_record_path.read_text(encoding="utf-8"))
    assert completion_record["artifact_type"] == "completion_manifest_draft_record"
    assert completion_record["draft_path"] == "agents/audit/completion_manifest.json"
    assert completion_record["report_path"] == "agents/reports/completion_manifest_plan.md"

    spec_record = json.loads(spec_record_path.read_text(encoding="utf-8"))
    assert spec_record["artifact_type"] == "spec_synthesis"
    assert spec_record["spec_id"] == "SPEC-42"
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
    assert taskmaster_record["card_count"] == 3
    assert taskmaster_record["profile_selection"]["selected_mode_ref"]["id"] == "mode.research_goalspec"
    assert taskmaster_record["profile_selection"]["task_authoring_profile_ref"]["id"] == "task_authoring.narrow"
    assert taskmaster_record["profile_selection"]["selection_path"] == "mode.task_authoring_profile_ref"
    assert taskmaster_record["profile_selection"]["lookup_path"] == "mode.task_authoring_profile_lookup_ref"
    assert taskmaster_record["profile_selection"]["selection_source"] == "mode"

    taskaudit_record = json.loads(taskaudit_record_path.read_text(encoding="utf-8"))
    assert taskaudit_record["artifact_type"] == "taskaudit_final_family_merge"
    assert taskaudit_record["status"] == "merged"
    assert taskaudit_record["pending_path"] == "agents/taskspending.md"
    assert taskaudit_record["backlog_path"] == "agents/tasksbacklog.md"
    assert taskaudit_record["provenance_path"] == "agents/task_provenance.json"
    assert taskaudit_record["pending_card_count"] == 3
    assert taskaudit_record["backlog_card_count_before"] == 0
    assert taskaudit_record["backlog_card_count_after"] == 3
    assert taskaudit_record["merged_spec_ids"] == ["SPEC-42"]
    assert taskaudit_record["shard_paths"] == ["agents/taskspending/SPEC-42.md"]

    backlog_text = paths.backlog_file.read_text(encoding="utf-8")
    assert "agents/ideas/archive/SPEC-42__modernize-goal-intake.md" in backlog_text
    assert "agents/ideas/finished/IDEA-42__modernize-goal-intake.md" in backlog_text
    assert "agents/ideas/specs_reviewed/SPEC-42__modernize-goal-intake.md" not in backlog_text
    assert "agents/ideas/staging/IDEA-42__modernize-goal-intake.md" not in backlog_text

    task_provenance = json.loads(task_provenance_path.read_text(encoding="utf-8"))
    assert [entry["source_file"] for entry in task_provenance["sources"]] == [
        "agents/tasks.md",
        "agents/tasksbacklog.md",
        "agents/tasksarchive.md",
    ]
    assert task_provenance["taskaudit"]["record_path"] == "agents/.research_runtime/goalspec/taskaudit/goalspec-run-42.json"
    assert task_provenance["taskaudit"]["run_id"] == "goalspec-run-42"
    assert task_provenance["taskaudit"]["pending_path"] == "agents/taskspending.md"
    assert task_provenance["taskaudit"]["pending_shards"] == ["agents/taskspending/SPEC-42.md"]
    assert task_provenance["taskaudit"]["pending_card_count"] == 3
    assert task_provenance["taskaudit"]["merged_backlog_card_count"] == 3
    assert task_provenance["taskaudit"]["merged_spec_ids"] == ["SPEC-42"]
    assert [entry["title"].split(" - ", 1)[0] for entry in task_provenance["task_cards"]] == [
        "SPEC-42 PHASE_01.1",
        "SPEC-42 PHASE_01.2",
        "SPEC-42 PHASE_01.3",
    ]


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
    assert snapshot.checkpoint is None
    assert snapshot.queue_snapshot.selected_family is None
    assert plane.status_store.read() is ResearchStatus.IDLE
    assert (workspace / "agents" / "audit" / "completion_manifest.json").exists()
    assert (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "spec_synthesis"
        / "goalspec-sync-77.json"
    ).exists()
    assert (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "spec_review"
        / "goalspec-sync-77.json"
    ).exists()
    assert (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "taskmaster"
        / "goalspec-sync-77.json"
    ).exists()
    assert (
        workspace
        / "agents"
        / ".research_runtime"
        / "goalspec"
        / "taskaudit"
        / "goalspec-sync-77.json"
    ).exists()
    assert not (workspace / "agents" / "taskspending" / "SPEC-77.md").exists()
    backlog = parse_task_store((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"))
    assert len(backlog.cards) == 3


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

    def _queue_checkpoint(path: Path) -> ResearchCheckpoint:
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
                    item_path=path,
                    owner_token=run_id,
                    acquired_at=emitted_at,
                ),
            ),
        )

    def _active_request_checkpoint(path: Path) -> ResearchCheckpoint:
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

    _write_queue_file(raw_goal_path, goal_text)
    execute_goal_intake(
        paths,
        _queue_checkpoint(raw_goal_path),
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
        _active_request_checkpoint(staged_path),
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
        "title: Preserve GoalSpec Idempotency\n"
        "decomposition_profile: moderate\n"
        "---\n\n"
        "# Preserve GoalSpec Idempotency\n\n"
        "Exercise restart-safe synthesis reuse.\n"
    )
    emitted_at = _dt("2026-03-21T12:00:00Z")
    run_id = "goalspec-idempotent-201"
    staged_path = workspace / "agents" / "ideas" / "staging" / "IDEA-201__preserve-goalspec-idempotency.md"

    def _queue_checkpoint(path: Path) -> ResearchCheckpoint:
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
                    item_path=path,
                    owner_token=run_id,
                    acquired_at=emitted_at,
                ),
            ),
        )

    def _active_request_checkpoint(path: Path) -> ResearchCheckpoint:
        return ResearchCheckpoint(
            checkpoint_id=run_id,
            mode=ResearchRuntimeMode.GOALSPEC,
            status=ResearchStatus.SPEC_SYNTHESIS_RUNNING,
            node_id="spec_synthesis",
            stage_kind_id="research.spec-synthesis",
            started_at=emitted_at,
            updated_at=emitted_at,
            active_request={
                "event_type": EventType.IDEA_SUBMITTED,
                "received_at": emitted_at,
                "payload": {"path": path.as_posix()},
                "queue_family": ResearchQueueFamily.GOALSPEC,
            },
        )

    _write_queue_file(raw_goal_path, raw_goal_text)
    execute_goal_intake(
        paths,
        _queue_checkpoint(raw_goal_path),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    execute_objective_profile_sync(
        paths,
        _active_request_checkpoint(staged_path),
        run_id=run_id,
        emitted_at=emitted_at,
    )
    completion_manifest = execute_completion_manifest_draft(
        paths,
        _active_request_checkpoint(staged_path),
        run_id=run_id,
        emitted_at=emitted_at,
    ).draft_state

    first_result = execute_spec_synthesis(
        paths,
        _active_request_checkpoint(staged_path),
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
        _active_request_checkpoint(staged_path),
        run_id=run_id,
        completion_manifest=completion_manifest,
        emitted_at=_dt("2026-03-21T12:30:00Z"),
    )

    assert second_result == first_result
    assert spec_record_path.read_text(encoding="utf-8") == first_record_text
    assert family_state_path.read_text(encoding="utf-8") == first_family_state_text
    assert queue_spec_path.read_text(encoding="utf-8") == first_queue_text
    assert golden_spec_path.read_text(encoding="utf-8") == first_golden_text
    assert phase_spec_path.read_text(encoding="utf-8") == first_phase_text
    assert decision_path.read_text(encoding="utf-8") == first_decision_text
    assert json.loads(first_record_text)["emitted_at"] == "2026-03-21T12:00:00Z"
    assert json.loads(first_family_state_text)["updated_at"] == "2026-03-21T12:00:00Z"


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


def test_research_plane_emits_blocked_and_retry_visibility_events_on_dispatch_failure(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUTO)
    _write_queue_file(workspace / "agents" / "ideas" / "raw" / "goal.md")
    _write_queue_file(workspace / "agents" / "ideas" / "audit" / "incoming" / "audit.md")
    observed: list[tuple[EventType, dict[str, object]]] = []
    plane = ResearchPlane(
        config,
        paths,
        emit_event=lambda event_type, payload: observed.append((event_type, payload)),
    )

    with pytest.raises(UnsupportedResearchQueueCombinationError, match="simultaneous ready queue groups"):
        plane.dispatch_ready_work(resolve_assets=False)

    observed_types = [event_type for event_type, _ in observed]
    assert observed_types == [
        EventType.RESEARCH_SCAN_COMPLETED,
        EventType.RESEARCH_BLOCKED,
        EventType.RESEARCH_RETRY_SCHEDULED,
    ]
    retry_payload = next(payload for event_type, payload in observed if event_type is EventType.RESEARCH_RETRY_SCHEDULED)
    assert retry_payload["attempt"] == 1
    assert retry_payload["exhausted"] is False
    assert "simultaneous ready queue groups" in str(retry_payload["failure_signature"])


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
                "## 2026-03-19 - Restore parent handoff",
                "",
                "- **Goal:** Exercise blocker dispatch from the recovery latch.",
                "- **Acceptance:** Research restores the execution parent link without breadcrumbs.",
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
    initial_breadcrumb_count = len([path for path in paths.deferred_dir.iterdir() if path.is_file()])

    diagnostics_dir = workspace / "agents" / "diagnostics" / "diag-parent-handoff"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    latch = queue.quarantine(
        active_task,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-PARENT-001.md"),
        diagnostics_dir=diagnostics_dir,
    )
    handoff = ExecutionResearchHandoff(
        handoff_id="execution-run-123:needs_research:20260319T120000Z",
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id="execution-run-123",
            snapshot_id="snapshot-execution-run-123",
            frozen_plan_id="frozen-plan:abc123",
            frozen_plan_hash="abc123",
            transition_history_path=Path("agents/runs/execution-run-123/transition_history.jsonl"),
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
    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    latch_path.write_text(
        latch.model_copy(update={"handoff": handoff}).model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
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


def test_research_plane_blocker_dispatch_prefers_deferred_request_matching_latch_handoff(
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
                "## 2026-03-19 - Match blocker request",
                "",
                "- **Goal:** Ensure blocker dispatch chooses the request that matches the latch handoff.",
                "- **Acceptance:** Active blocker request payload matches the execution handoff task.",
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

    diagnostics_dir = workspace / "agents" / "diagnostics" / "diag-matching-blocker"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    latch = queue.quarantine(
        active_task,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-MATCH-001.md"),
        diagnostics_dir=diagnostics_dir,
    )
    handoff = ExecutionResearchHandoff(
        handoff_id=f"execution-run-456:needs_research:{latch.batch_id}",
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id="execution-run-456",
            snapshot_id="snapshot-execution-run-456",
            frozen_plan_id="frozen-plan:def456",
            frozen_plan_hash="def456",
            transition_history_path=Path("agents/runs/execution-run-456/transition_history.jsonl"),
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
    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    latch_path.write_text(
        latch.model_copy(update={"handoff": handoff}).model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    state_path = workspace / "agents/research_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "updated_at": "2026-03-19T12:00:00Z",
                "current_mode": "AUTO",
                "last_mode": "STUB",
                "mode_reason": "research-plane-initialized",
                "cycle_count": 0,
                "transition_count": 0,
                "deferred_requests": [
                    {
                        "event_type": EventType.NEEDS_RESEARCH.value,
                        "received_at": "2026-03-19T12:00:00Z",
                        "payload": {"task_id": "2026-03-19__unrelated-blocker"},
                        "queue_family": ResearchQueueFamily.BLOCKER.value,
                    },
                    {
                        "event_type": EventType.NEEDS_RESEARCH.value,
                        "received_at": "2026-03-19T12:01:00Z",
                        "payload": {"task_id": active_task.task_id},
                        "queue_family": ResearchQueueFamily.BLOCKER.value,
                    },
                ],
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
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
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
    assert snapshot.deferred_requests[0].payload["task_id"] == "2026-03-19__unrelated-blocker"


def test_research_plane_blocker_dispatch_synthesizes_latch_request_when_only_batch_mismatched_request_exists(
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
                "## 2026-03-19 - Preserve unrelated blocker request",
                "",
                "- **Goal:** Ensure latch restoration does not hijack a blocker request from another batch.",
                "- **Acceptance:** Research synthesizes the active request from the latch and leaves the queued request intact.",
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

    diagnostics_dir = workspace / "agents" / "diagnostics" / "diag-batch-mismatch"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    latch = queue.quarantine(
        active_task,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-BATCH-MISMATCH-001.md"),
        diagnostics_dir=diagnostics_dir,
    )
    handoff = ExecutionResearchHandoff(
        handoff_id=f"execution-run-batch:needs_research:{latch.batch_id}",
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id="execution-run-batch",
            snapshot_id="snapshot-execution-run-batch",
            frozen_plan_id="frozen-plan:batch123",
            frozen_plan_hash="batch123",
            transition_history_path=Path("agents/runs/execution-run-batch/transition_history.jsonl"),
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
    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    latch_path.write_text(
        latch.model_copy(update={"handoff": handoff}).model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    state_path = workspace / "agents/research_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "updated_at": "2026-03-19T12:00:00Z",
                "current_mode": "AUTO",
                "last_mode": "STUB",
                "mode_reason": "research-plane-initialized",
                "cycle_count": 0,
                "transition_count": 0,
                "deferred_requests": [
                    {
                        "event_type": EventType.NEEDS_RESEARCH.value,
                        "received_at": "2026-03-19T12:00:00Z",
                        "payload": {"task_id": active_task.task_id},
                        "queue_family": ResearchQueueFamily.BLOCKER.value,
                        "handoff": {
                            "handoff_id": "execution-run-other:needs_research:other-batch",
                            "parent_run": {
                                "plane": "execution",
                                "run_id": "execution-run-other",
                            },
                            "task_id": active_task.task_id,
                            "task_title": active_task.title,
                            "stage": "Consult",
                            "reason": "Earlier blocker batch",
                            "status": ExecutionStatus.NEEDS_RESEARCH.value,
                            "recovery_batch_id": "other-batch",
                            "failure_signature": "other-signature",
                            "frozen_backlog_cards": 1,
                            "retained_backlog_cards": 0,
                        },
                    },
                ],
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
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    plane = ResearchPlane(loaded.config, paths)
    dispatch = plane.dispatch_ready_work(run_id="research-blocker-run", resolve_assets=False)

    assert dispatch is not None
    snapshot = plane.snapshot_state()
    assert snapshot.checkpoint is not None
    assert snapshot.checkpoint.active_request is not None
    assert snapshot.checkpoint.active_request.handoff == handoff
    assert snapshot.checkpoint.parent_handoff == handoff
    assert snapshot.checkpoint.active_request.payload["task_id"] == active_task.task_id
    assert len(snapshot.deferred_requests) == 1
    assert snapshot.deferred_requests[0].handoff is not None
    assert snapshot.deferred_requests[0].handoff.recovery_batch_id == "other-batch"


def test_research_plane_resume_checkpoint_restores_parent_handoff_from_recovery_latch(
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
                "## 2026-03-19 - Resume blocker handoff",
                "",
                "- **Goal:** Restore parent linkage when a blocker checkpoint resumes after restart.",
                "- **Acceptance:** Missing handoff fields are rehydrated from the recovery latch.",
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

    diagnostics_dir = workspace / "agents" / "diagnostics" / "diag-resume-blocker"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    latch = queue.quarantine(
        active_task,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-RESUME-001.md"),
        diagnostics_dir=diagnostics_dir,
    )
    handoff = ExecutionResearchHandoff(
        handoff_id=f"execution-run-789:needs_research:{latch.batch_id}",
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id="execution-run-789",
            snapshot_id="snapshot-execution-run-789",
            frozen_plan_id="frozen-plan:ghi789",
            frozen_plan_hash="ghi789",
            transition_history_path=Path("agents/runs/execution-run-789/transition_history.jsonl"),
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
    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
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
                "## 2026-03-19 - Preserve resumed parent handoff",
                "",
                "- **Goal:** Prevent restart-time latch drift from overwriting a checkpoint's existing parent handoff.",
                "- **Acceptance:** Resume keeps the checkpoint's original execution parent linkage when the latch points at another batch.",
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

    diagnostics_dir = workspace / "agents" / "diagnostics" / "diag-resume-preserve"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    latch = queue.quarantine(
        active_task,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-RESUME-PRESERVE-001.md"),
        diagnostics_dir=diagnostics_dir,
    )
    preserved_handoff = ExecutionResearchHandoff(
        handoff_id=f"execution-run-preserved:needs_research:{latch.batch_id}",
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id="execution-run-preserved",
            snapshot_id="snapshot-execution-run-preserved",
            frozen_plan_id="frozen-plan:preserved123",
            frozen_plan_hash="preserved123",
            transition_history_path=Path("agents/runs/execution-run-preserved/transition_history.jsonl"),
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
    assert snapshot.queue_snapshot.ownerships == snapshot.checkpoint.owned_queues
    assert snapshot.deferred_requests[0].event_type is EventType.BACKLOG_EMPTY_AUDIT
    assert snapshot.checkpoint.deferred_follow_ons[0].event_type is EventType.IDEA_SUBMITTED


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
