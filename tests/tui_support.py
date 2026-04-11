from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

from textual.app import App
from textual.widgets import Static

from millrace_engine.markdown import parse_task_cards
from millrace_engine.paths import RuntimePaths
from millrace_engine.research.interview import create_manual_interview_question
from millrace_engine.tui.app import MillraceTUIApplication
from millrace_engine.tui.models import (
    RunCompoundingView,
    RunContextFactSelectionSummaryView,
    RunCreatedProcedureSummaryView,
    RunDetailView,
    RunIntegrationSummaryView,
    RunPolicyEvidenceView,
    RunProcedureSelectionSummaryView,
    RunTransitionView,
    SelectionDecisionView,
    SelectionSummaryView,
)
from millrace_engine.tui.screens.run_detail_modal import RunDetailModal
from millrace_engine.tui.workers import WorkerSettings
from tests.support import SAMPLE_AGENTS_ROOT, load_workspace_fixture


SNAPSHOT_WORKER_SETTINGS = WorkerSettings(
    refresh_interval_seconds=3600.0,
    event_stream_poll_interval_seconds=3600.0,
    event_stream_idle_timeout_seconds=3600.0,
    event_batch_window_seconds=3600.0,
    event_retry_delay_seconds=3600.0,
)


def run_app_scenario(
    config_path,
    scenario,
    *,
    worker_settings: WorkerSettings | None = None,
    size=(120, 40),
    offer_startup_daemon_launch: bool = False,
) -> None:
    async def runner() -> None:
        app = MillraceTUIApplication.from_config_path(
            config_path,
            worker_settings=worker_settings,
            offer_startup_daemon_launch=offer_startup_daemon_launch,
        )
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await scenario(app, pilot)

    asyncio.run(runner())


def run_modal_scenario(modal: RunDetailModal, scenario, *, size=(120, 40)) -> None:
    class ModalHost(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.modal = modal

        async def on_mount(self) -> None:
            self.push_screen(self.modal)

    async def runner() -> None:
        app = ModalHost()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await scenario(app, pilot)

    asyncio.run(runner())


async def wait_for_condition(pilot, predicate, *, attempts: int = 40) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError("condition not met before timeout")


def static_text(widget: Static) -> str:
    return str(widget.render())


def write_backlog(workspace: Path, cards: list[tuple[str, str, str | None]]) -> None:
    lines = ["# Task Backlog", ""]
    for date, title, spec_id in cards:
        lines.append(f"## {date} - {title}")
        lines.append("")
        lines.append(f"- **Goal:** {title}.")
        if spec_id is not None:
            lines.append(f"- **Spec-ID:** {spec_id}")
        lines.append("")
    (workspace / "agents" / "tasksbacklog.md").write_text("\n".join(lines), encoding="utf-8")


def write_runtime_state_snapshot(
    workspace: Path,
    *,
    process_running: bool,
    backlog_depth: int,
    mode: str = "daemon",
    active_task_id: str | None = None,
    execution_status: str = "IDLE",
    research_status: str = "IDLE",
) -> None:
    state_path = workspace / "agents" / ".runtime" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "process_running": process_running,
        "process_id": os.getpid() if process_running else None,
        "paused": False,
        "pause_reason": None,
        "pause_run_id": None,
        "execution_status": execution_status,
        "research_status": research_status,
        "active_task_id": active_task_id,
        "backlog_depth": backlog_depth,
        "deferred_queue_size": 0,
        "uptime_seconds": 1.0 if process_running else 0.0,
        "config_hash": "test-config-hash",
        "asset_bundle_version": "test-bundle",
        "pending_config_hash": None,
        "previous_config_hash": None,
        "pending_config_boundary": None,
        "pending_config_fields": [],
        "rollback_armed": False,
        "started_at": datetime(2026, 3, 25, tzinfo=timezone.utc).isoformat() if process_running else None,
        "updated_at": datetime(2026, 3, 25, tzinfo=timezone.utc).isoformat(),
        "mode": mode,
    }
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def ensure_small_workspace(workspace: Path) -> None:
    (workspace / "agents" / "size_status.md").write_text("### SMALL\n", encoding="utf-8")


def copy_runtime_event_log(workspace: Path) -> None:
    source = SAMPLE_AGENTS_ROOT / "engine_events.log"
    shutil.copy2(source, workspace / "agents" / "engine_events.log")


def copy_sample_run_artifacts(workspace: Path, *, run_id: str = "smoke-standard") -> None:
    source = SAMPLE_AGENTS_ROOT / "runs" / run_id
    destination = workspace / "agents" / "runs" / run_id
    shutil.copytree(source, destination, dirs_exist_ok=True)
    snapshot_path = destination / "resolved_snapshot.json"
    if not snapshot_path.exists():
        return
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    stages = payload.get("content", {}).get("execution_plan", {}).get("stages", ())
    updated = False
    for stage in stages:
        if "handler_ref" in stage:
            continue
        stage["handler_ref"] = f"placeholder:{stage.get('kind_id', stage.get('node_id', 'stage'))}"
        updated = True
    if updated:
        snapshot_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_operator_workspace(
    tmp_path: Path,
    *,
    fixture_name: str = "golden_path",
    process_running: bool = True,
    mode: str = "daemon",
) -> tuple[Path, Path]:
    workspace, config_path = load_workspace_fixture(tmp_path, fixture_name)
    ensure_small_workspace(workspace)
    copy_runtime_event_log(workspace)
    copy_sample_run_artifacts(workspace)
    backlog_depth = len(parse_task_cards((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8")))
    write_runtime_state_snapshot(
        workspace,
        process_running=process_running,
        backlog_depth=backlog_depth,
        mode=mode,
    )
    return workspace, config_path


def seed_pending_interview_question(
    workspace: Path,
    *,
    relative_source_path: str = "agents/specs/staging/SPEC-TUI-001__operator-interview.md",
    title: str = "Operator interview spec",
    question: str = "Should queue reorder approvals stay operator-confirmed in the TUI?",
    why_this_matters: str = "This determines whether queue mutation remains governed or becomes one-step.",
    recommended_answer: str = "Keep confirmation so daemon and foreground flows stay behaviorally aligned.",
) -> str:
    source_path = workspace / relative_source_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "\n".join(
            (
                "---",
                'spec_id = "SPEC-TUI-001"',
                f'title = "{title}"',
                "---",
                "",
                f"# {title}",
                "",
                "A staged synthesized spec used by the TUI interview fixtures.",
                "",
            )
        ),
        encoding="utf-8",
    )
    result = create_manual_interview_question(
        RuntimePaths.from_workspace(workspace, workspace / "agents"),
        source_path=source_path,
        question=question,
        why_this_matters=why_this_matters,
        recommended_answer=recommended_answer,
        answer_source="operator",
        blocking=True,
        evidence=("tests/tui_support.py fixture",),
    )
    return result.question.question_id


def create_snapshot_workspace(
    *,
    fixture_name: str = "golden_path",
    process_running: bool = True,
    mode: str = "daemon",
) -> tuple[Path, Path]:
    temp_root = Path(tempfile.mkdtemp(prefix="millrace-tui-snapshot-"))
    return load_operator_workspace(
        temp_root,
        fixture_name=fixture_name,
        process_running=process_running,
        mode=mode,
    )


def sample_selection_summary(*, run_id: str | None = None) -> SelectionSummaryView:
    return SelectionSummaryView(
        scope="run",
        selection_ref="mode:mode.standard@1.0.0",
        mode_ref="mode:mode.standard@1.0.0",
        execution_loop_ref="loop:execution.standard@1.0.0",
        frozen_plan_id="frozen-plan:smoke-standard",
        frozen_plan_hash="smoke-standard-hash",
        run_id=run_id,
        research_participation="stub",
        stage_labels=("builder:builder", "qa:qa"),
    )


def sample_selection_decision() -> SelectionDecisionView:
    return SelectionDecisionView(
        selected_size="SMALL",
        route_decision="default",
        route_reason="Backlog stayed within the SMALL route.",
        large_profile_decision="standard",
    )


def sample_run_detail(*, run_id: str = "smoke-standard", observed_at: datetime | None = None) -> RunDetailView:
    moment = observed_at or datetime(2026, 3, 25, tzinfo=timezone.utc)
    return RunDetailView(
        run_id=run_id,
        compiled_at=moment,
        frozen_plan_id=f"frozen-plan:{run_id}",
        frozen_plan_hash=f"{run_id}-hash",
        stage_count=2,
        selection=sample_selection_summary(run_id=run_id),
        selection_decision=sample_selection_decision(),
        current_preview=sample_selection_summary(run_id=None),
        current_preview_decision=sample_selection_decision(),
        current_preview_error=None,
        routing_modes=("small",),
        snapshot_path=f"/tmp/{run_id}/resolved_snapshot.json",
        transition_history_path=f"/tmp/{run_id}/transition_history.jsonl",
        policy_hook_count=2,
        latest_policy_decision="PASS",
        latest_policy_evidence=RunPolicyEvidenceView(
            hook="pre_stage",
            evaluator="execution_integration_policy",
            decision="PASS",
            timestamp=moment,
            event_name="execution.stage.completed",
            node_id="builder",
            routing_mode="small",
            notes=("builder success path is allowed",),
            evidence_summaries=("task gate did not require integration", "builder routes to qa"),
        ),
        integration_policy=RunIntegrationSummaryView(
            effective_mode="large_only",
            builder_success_target="qa",
            should_run_integration=False,
            task_gate_required=False,
            task_integration_preference="inherit",
            requested_sequence=("builder", "qa"),
            effective_sequence=("builder", "qa"),
            available_execution_nodes=("builder", "qa"),
            reason="Builder routes to qa.",
        ),
        compounding=RunCompoundingView(
            created_count=1,
            procedure_selection_count=1,
            context_fact_selection_count=1,
            injected_procedure_count=1,
            injected_context_fact_count=1,
            created_procedures=(
                RunCreatedProcedureSummaryView(
                    procedure_id=f"proc.run.{run_id}.builder",
                    scope="run",
                    source_stage="builder",
                    title="Builder repair procedure",
                ),
            ),
            procedure_selections=(
                RunProcedureSelectionSummaryView(
                    stage="builder",
                    node_id="builder",
                    considered_count=1,
                    injected_count=1,
                    injected_ids=("proc.workspace.builder.reviewed",),
                ),
            ),
            context_fact_selections=(
                RunContextFactSelectionSummaryView(
                    stage="builder",
                    node_id="builder",
                    considered_count=1,
                    injected_count=1,
                    injected_ids=("fact.workspace.builder.audit",),
                ),
            ),
        ),
        transitions=(
            RunTransitionView(
                event_id="evt-1",
                timestamp=moment,
                observed_timestamp=moment,
                event_name="stage.completed",
                source="engine",
                plane="execution",
                node_id="builder",
                kind_id="builder",
                outcome="success",
                status_before="IDLE",
                status_after="QA_PENDING",
                active_task_before=None,
                active_task_after="2026-03-19__ship-the-happy-path",
                routing_mode="small",
                queue_mutations_applied=("promoted",),
                artifacts_emitted=("artifact.md",),
            ),
        ),
    )
