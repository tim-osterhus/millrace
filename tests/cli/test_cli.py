from __future__ import annotations

import importlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from millrace_ai import cli
from millrace_ai.compiler import CompileOutcome
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ClosureTargetState,
    CompileDiagnostics,
    LearningRequestDocument,
    MailboxCommand,
    Plane,
    ReloadOutcome,
    ResultClass,
    RuntimeMode,
    TokenUsage,
)
from millrace_ai.control import ControlActionResult
from millrace_ai.mailbox import read_pending_mailbox_commands
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.run_inspection import InspectedRunSummary, InspectedStageResult
from millrace_ai.runtime.monitoring import RuntimeMonitorEvent
from millrace_ai.runtime.usage_governance import (
    SubscriptionQuotaStatus,
    UsageGovernanceBlocker,
    UsageGovernanceState,
    save_usage_governance_state,
)
from millrace_ai.runtime_lock import acquire_runtime_ownership_lock
from millrace_ai.state_store import load_snapshot, save_snapshot
from millrace_ai.workspace.arbiter_state import save_closure_target_state

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _copy_assets(tmp_path: Path) -> Path:
    source_assets = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    destination = tmp_path / "assets"
    shutil.copytree(source_assets, destination)
    return destination


def test_init_command_creates_workspace_baseline(tmp_path: Path) -> None:
    root = tmp_path / "workspace"

    runner = CliRunner()
    result = runner.invoke(cli.app, ["init", "--workspace", str(root)])

    paths = workspace_paths(root)

    assert result.exit_code == 0
    assert "workspace:" in result.output
    assert "initialized: true" in result.output
    assert paths.runtime_root.is_dir()
    assert paths.runtime_root.joinpath("millrace.toml").is_file()
    assert paths.runtime_snapshot_file.is_file()


@pytest.mark.parametrize(
    ("argv"),
    [
        ["run", "once"],
        ["compile", "validate"],
        ["queue", "ls"],
        ["status"],
        ["runs", "ls"],
        ["control", "pause"],
        ["skills", "ls"],
        ["doctor"],
    ],
)
def test_operational_commands_refuse_uninitialized_workspace(
    tmp_path: Path,
    argv: list[str],
) -> None:
    root = tmp_path / "workspace"

    runner = CliRunner()
    result = runner.invoke(cli.app, [*argv, "--workspace", str(root)])

    assert result.exit_code == 1
    assert "error: workspace is not initialized" in result.output
    assert "millrace init --workspace" in result.output
    assert not (root / "millrace-agents").exists()


def test_cli_import_surface_moves_to_package_directory() -> None:
    assert Path(cli.__file__).as_posix().endswith("/cli/__init__.py")


def test_cli_package_exposes_split_command_modules() -> None:
    run_module = importlib.import_module("millrace_ai.cli.commands.run")
    app_module = importlib.import_module("millrace_ai.cli.app")
    skills_module = importlib.import_module("millrace_ai.cli.commands.skills")

    assert hasattr(run_module, "run_once")
    assert hasattr(app_module, "app")
    assert hasattr(skills_module, "skills_app")


def test_cli_package_consumes_public_runtime_control_facade() -> None:
    control_module = importlib.import_module("millrace_ai.control")

    assert cli.RuntimeControl is control_module.RuntimeControl
    assert cli.ControlActionResult is control_module.ControlActionResult
    assert cli.RuntimeControl.__module__ == "millrace_ai.runtime.control"


def _task_payload(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "summary": "cli task",
        "target_paths": ["src/millrace_ai/runtime.py"],
        "acceptance": ["runtime loop runs"],
        "required_checks": ["uv run pytest tests/cli/test_cli.py -q"],
        "references": ["lab/specs/drafts/millrace-mvp-implementation-slice.md"],
        "risk": ["none"],
        "created_at": NOW.isoformat(),
        "created_by": "tests",
    }


def _spec_payload(spec_id: str) -> dict[str, object]:
    return {
        "spec_id": spec_id,
        "title": f"Spec {spec_id}",
        "summary": "cli spec",
        "source_type": "manual",
        "goals": ["ship CLI"],
        "constraints": ["MVP surface only"],
        "acceptance": ["command set works"],
        "references": ["lab/specs/drafts/millrace-runtime-module-and-cli-plan.md"],
        "created_at": NOW.isoformat(),
        "created_by": "tests",
    }


def _pending_commands(paths) -> set[MailboxCommand]:
    return {envelope.command for envelope in read_pending_mailbox_commands(paths)}


def _inspected_run_summary(
    run_id: str = "run-001",
    *,
    run_dir: str | None = None,
    status: str = "valid",
    failure_class: str | None = None,
    report_artifact: str | None = "troubleshoot_report.md",
    compiled_plan_id: str | None = "plan-001",
    mode_id: str | None = "default_codex",
    request_kind: str | None = None,
    closure_target_root_spec_id: str | None = None,
) -> InspectedRunSummary:
    artifact_paths = tuple(
        path for path in (report_artifact, "runner_stdout.txt") if path is not None
    )
    stage_result = InspectedStageResult(
        stage_result_path="stage_results/request-001.json",
        request_id="request-001",
        compiled_plan_id=compiled_plan_id,
        mode_id=mode_id,
        stage="checker",
        node_id="execution.checker.primary",
        stage_kind_id="checker",
        request_kind=request_kind,
        closure_target_root_spec_id=closure_target_root_spec_id,
        terminal_result="CHECKER_PASS",
        result_class="success",
        work_item_kind="task",
        work_item_id="task-001",
        failure_class=failure_class,
        stdout_path="runner_stdout.txt",
        stderr_path="runner_stderr.txt",
        report_artifact=report_artifact,
        artifact_paths=artifact_paths,
        runner_name="codex-cli",
        model_name="gpt-5.4",
        started_at=NOW.isoformat(),
        completed_at=NOW.isoformat(),
        duration_seconds=3.0,
        token_usage=TokenUsage(
            input_tokens=100,
            cached_input_tokens=30,
            output_tokens=12,
            thinking_tokens=5,
            total_tokens=112,
        ),
    )
    return InspectedRunSummary(
        run_id=run_id,
        run_dir=run_dir or f"/tmp/{run_id}",
        status=status,
        compiled_plan_id=compiled_plan_id,
        mode_id=mode_id,
        request_kind=request_kind,
        closure_target_root_spec_id=closure_target_root_spec_id,
        work_item_kind="task",
        work_item_id="task-001",
        failure_class=failure_class,
        troubleshoot_report_path=report_artifact,
        primary_stdout_path="runner_stdout.txt",
        primary_stderr_path="runner_stderr.txt",
        stage_results=(stage_result,),
        notes=(),
        started_at=NOW.isoformat(),
        completed_at=NOW.isoformat(),
        duration_seconds=3.0,
        token_usage=TokenUsage(
            input_tokens=100,
            cached_input_tokens=30,
            output_tokens=12,
            thinking_tokens=5,
            total_tokens=112,
        ),
    )


def test_run_once_invokes_runtime_engine_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    calls: dict[str, object] = {"startup": 0, "tick": 0, "close": 0, "mode": None, "stage_runner": None}
    sentinel_runner = object()

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
        ) -> None:
            del target, config_path, assets_root
            calls["mode"] = mode_id
            calls["stage_runner"] = stage_runner

        def startup(self):
            calls["startup"] = int(calls["startup"]) + 1
            return SimpleNamespace(
                active_mode_id="standard_plain",
                compiled_plan_id="plan-001",
            )

        def tick(self):
            calls["tick"] = int(calls["tick"]) + 1
            return SimpleNamespace(
                router_decision=SimpleNamespace(reason="no_work"),
                stage_result=SimpleNamespace(
                    metadata={"failure_class": None},
                    result_class=ResultClass.SUCCESS,
                ),
            )

        def close(self):
            calls["close"] = int(calls["close"]) + 1

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    monkeypatch.setattr(cli, "_build_stage_runner", lambda **kwargs: sentinel_runner)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "once", "--workspace", str(paths.root), "--mode", "standard_plain"],
    )

    assert result.exit_code == 0
    assert calls == {
        "startup": 1,
        "tick": 1,
        "close": 1,
        "mode": "standard_plain",
        "stage_runner": sentinel_runner,
    }
    assert "run_mode: once" in result.output


def test_run_daemon_respects_max_ticks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    calls: dict[str, object] = {"tick": 0, "stage_runner": None}
    sentinel_runner = object()

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, config_path, mode_id, assets_root, monitor
            calls["stage_runner"] = stage_runner
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=True)

        def startup(self):
            return SimpleNamespace(
                active_mode_id="standard_plain",
                compiled_plan_id="plan-001",
            )

        def tick(self):
            calls["tick"] += 1
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    monkeypatch.setattr(cli, "_build_stage_runner", lambda **kwargs: sentinel_runner)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "3"],
    )

    assert result.exit_code == 0
    assert calls["tick"] == 3
    assert calls["stage_runner"] is sentinel_runner
    assert "run_mode: daemon" in result.output
    assert "ticks: 3" in result.output


def test_run_daemon_with_monitor_basic_installs_monitor_and_prints_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, stage_runner, config_path, mode_id, assets_root
            self.monitor = monitor
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=False)

        def startup(self):
            assert self.monitor is not None
            self.monitor.emit(
                RuntimeMonitorEvent(
                    event_type="runtime_started",
                    occurred_at=NOW,
                    payload={
                        "mode_id": "standard_plain",
                        "compiled_plan_id": "plan-001",
                        "compiled_plan_currentness": "current",
                        "baseline_manifest_id": "baseline-001",
                        "baseline_seed_package_version": "0.15.5",
                        "loop_ids_by_plane": {
                            "execution": "execution.standard",
                            "planning": "planning.standard",
                        },
                        "concurrency_policy": None,
                        "status_markers_by_plane": {
                            "execution": "### IDLE",
                            "planning": "### IDLE",
                            "learning": "### IDLE",
                        },
                        "queue_depths_by_plane": {
                            "execution": 0,
                            "planning": 0,
                            "learning": 0,
                        },
                    },
                )
            )
            return SimpleNamespace(active_mode_id="standard_plain", compiled_plan_id="plan-001")

        def tick(self):
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    result = CliRunner().invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--monitor", "basic", "--max-ticks", "1"],
    )

    assert result.exit_code == 0
    assert "runtime started mode=standard_plain" in result.output


def test_run_daemon_without_monitor_stays_quiet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, stage_runner, config_path, mode_id, assets_root
            self.monitor = monitor
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=False)

        def startup(self):
            assert self.monitor is not None
            self.monitor.emit(
                RuntimeMonitorEvent(
                    event_type="runtime_started",
                    occurred_at=NOW,
                    payload={"mode_id": "standard_plain", "compiled_plan_id": "plan-001"},
                )
            )
            return SimpleNamespace(active_mode_id="standard_plain", compiled_plan_id="plan-001")

        def tick(self):
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    result = CliRunner().invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "1"],
    )

    assert result.exit_code == 0
    assert "runtime started" not in result.output


def test_skills_install_copies_local_skill_and_updates_workspace_index(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    source_skill = tmp_path / "source-skill"
    source_skill.mkdir()
    source_skill.joinpath("SKILL.md").write_text(
        "---\n"
        "name: source-skill\n"
        "description: A test skill\n"
        "---\n"
        "# Source Skill\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["skills", "install", str(source_skill), "--workspace", str(paths.root)],
    )

    installed_skill = paths.skills_dir / "source-skill" / "SKILL.md"
    index_text = paths.skills_dir.joinpath("skills_index.md").read_text(encoding="utf-8")

    assert result.exit_code == 0
    assert installed_skill.is_file()
    assert "installed_skill: source-skill" in result.output
    assert "- source-skill: source-skill/SKILL.md" in index_text


def test_skills_install_refuses_existing_skill_without_force(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    source_skill = tmp_path / "source-skill"
    source_skill.mkdir()
    source_skill.joinpath("SKILL.md").write_text("# Source Skill\n", encoding="utf-8")

    runner = CliRunner()
    first = runner.invoke(
        cli.app,
        ["skills", "install", str(source_skill), "--workspace", str(paths.root)],
    )
    second = runner.invoke(
        cli.app,
        ["skills", "install", str(source_skill), "--workspace", str(paths.root)],
    )

    assert first.exit_code == 0
    assert second.exit_code == 1
    assert "skill already exists" in second.output


def test_skills_create_refuses_when_learning_plane_is_not_enabled(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["skills", "create", "write a checker skill", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 1
    assert "current mode does not enable the learning plane" in result.output
    assert not any(paths.learning_requests_queue_dir.glob("*.md"))


def test_run_daemon_sleeps_between_ticks_when_unbounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    calls: dict[str, object] = {"tick": 0, "sleep": 0}
    sentinel_runner = object()

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, config_path, mode_id, assets_root, stage_runner, monitor
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=True)

        def startup(self):
            return SimpleNamespace(
                active_mode_id="standard_plain",
                compiled_plan_id="plan-001",
            )

        def tick(self):
            calls["tick"] = int(calls["tick"]) + 1
            if int(calls["tick"]) >= 2:
                self.snapshot.stop_requested = True
                self.snapshot.process_running = False
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    monkeypatch.setattr(cli, "_build_stage_runner", lambda **kwargs: sentinel_runner)
    monkeypatch.setattr(cli.time, "sleep", lambda _: calls.__setitem__("sleep", int(calls["sleep"]) + 1))

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert calls["tick"] == 2
    assert calls["sleep"] == 1


def test_run_daemon_fails_fast_when_workspace_daemon_lock_is_held(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="cli-lock-holder",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "1"],
    )

    assert result.exit_code == 1
    assert "error:" in result.output
    assert "workspace runtime ownership lock" in result.output


def test_run_once_fails_fast_when_workspace_lock_is_held(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text("[runtime]\nrun_style = 'once'\n", encoding="utf-8")
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="cli-lock-holder",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "once", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 1
    assert "error:" in result.output
    assert "workspace runtime ownership lock" in result.output


def test_run_once_returns_nonzero_on_runner_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    sentinel_runner = object()

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
        ) -> None:
            del target, config_path, mode_id, assets_root, stage_runner

        def startup(self):
            return SimpleNamespace(
                active_mode_id="standard_plain",
                compiled_plan_id="plan-001",
            )

        def tick(self):
            return SimpleNamespace(
                router_decision=SimpleNamespace(reason="builder_blocked"),
                stage_result=SimpleNamespace(
                    metadata={"failure_class": "runner_transport_failure"},
                    result_class=ResultClass.RECOVERABLE_FAILURE,
                ),
            )

        def close(self):
            return None

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)
    monkeypatch.setattr(cli, "_build_stage_runner", lambda **kwargs: sentinel_runner)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "once", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 1


def test_run_once_fails_fast_on_unknown_configured_stage_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[stages.builder]",
                'runner = "does_not_exist"',
            ]
        ),
        encoding="utf-8",
    )

    class FakeRuntimeEngine:
        def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - should not run
            raise AssertionError("RuntimeEngine should not be constructed")

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "once", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 1
    assert "Unknown configured stage runner" in result.output


def test_status_surfaces_active_mode_and_compiled_plan_id(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths).model_copy(
        update={
            "active_mode_id": "default_codex",
            "compiled_plan_id": "plan-status-123",
            "queue_depth_execution": 4,
            "queue_depth_planning": 2,
        }
    )
    save_snapshot(paths, snapshot)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "active_mode_id: default_codex" in result.output
    assert "compiled_plan_id: plan-status-123" in result.output


def test_status_surfaces_baseline_manifest_identity_and_compile_currentness(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    cli.compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
        assets_root=paths.runtime_root,
    )

    runner = CliRunner()
    current = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert current.exit_code == 0
    assert "baseline_manifest_id:" in current.output
    assert "compiled_plan_currentness: current" in current.output

    (paths.runtime_root / "entrypoints" / "execution" / "builder.md").write_text(
        "stale builder override\n",
        encoding="utf-8",
    )
    stale = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert stale.exit_code == 0
    assert "compiled_plan_currentness: stale" in stale.output


def test_status_surfaces_learning_plane_depth_and_status(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_learning_request(
        LearningRequestDocument(
            learning_request_id="learn-001",
            title="Learn from checker",
            requested_action="improve",
            created_at=NOW,
            created_by="tests",
        )
    )
    snapshot = load_snapshot(paths).model_copy(
        update={
            "active_mode_id": "learning_codex",
            "learning_loop_id": "learning.standard",
            "learning_status_marker": "### ANALYST_COMPLETE",
        }
    )
    save_snapshot(paths, snapshot)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "learning_queue_depth: 1" in result.output
    assert "learning_status_marker: ### ANALYST_COMPLETE" in result.output


def test_status_surfaces_usage_governance_pause_context(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    (paths.runtime_root / "millrace.toml").write_text(
        "\n".join(
            [
                "[runtime]",
                'default_mode = "default_codex"',
                "",
                "[usage_governance]",
                "enabled = true",
            ]
        ),
        encoding="utf-8",
    )
    snapshot = load_snapshot(paths).model_copy(
        update={
            "paused": True,
            "pause_sources": ("usage_governance",),
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, snapshot)
    save_usage_governance_state(
        paths,
        UsageGovernanceState(
            enabled=True,
            auto_resume=True,
            auto_resume_possible=True,
            last_evaluated_at=NOW,
            active_blockers=(
                UsageGovernanceBlocker(
                    source="runtime_token",
                    rule_id="test-rolling",
                    window="rolling_5h",
                    metric="total_tokens",
                    observed=125,
                    threshold=100,
                    next_auto_resume_at=NOW,
                ),
            ),
            paused_by_governance=True,
            next_auto_resume_at=NOW,
            subscription_quota_status=SubscriptionQuotaStatus(
                enabled=True,
                provider="codex_chatgpt_oauth",
                state="degraded",
                degraded_policy="fail_open",
                detail="quota_telemetry_unavailable",
                last_refreshed_at=NOW,
            ),
        ),
    )

    result = CliRunner().invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "pause_sources: usage_governance" in result.output
    assert "usage_governance_enabled: true" in result.output
    assert "usage_governance_paused: true" in result.output
    assert "usage_governance_blocker_count: 1" in result.output
    assert "usage_governance_subscription_status: degraded" in result.output
    assert "usage_governance_blocker: source=runtime_token rule=test-rolling" in result.output


def test_status_surfaces_failure_class_and_retry_counters(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths).model_copy(
        update={
            "current_failure_class": "missing_terminal_result",
            "troubleshoot_attempt_count": 2,
            "fix_cycle_count": 1,
            "consultant_invocations": 1,
        }
    )
    save_snapshot(paths, snapshot)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "current_failure_class: missing_terminal_result" in result.output
    assert "troubleshoot_attempt_count: 2" in result.output
    assert "fix_cycle_count: 1" in result.output
    assert "consultant_invocations: 1" in result.output


def test_status_surfaces_closure_target_state(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    save_closure_target_state(
        paths,
        ClosureTargetState(
            root_spec_id="spec-root-001",
            root_idea_id="idea-001",
            root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
            root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
            rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
            latest_verdict_path="millrace-agents/arbiter/verdicts/spec-root-001.json",
            latest_report_path="millrace-agents/arbiter/reports/run-001.md",
            closure_open=True,
            closure_blocked_by_lineage_work=False,
            blocking_work_ids=(),
            opened_at=NOW,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["status", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "closure_target_root_spec_id: spec-root-001" in result.output
    assert "closure_target_open: true" in result.output
    assert "closure_target_latest_verdict_path: millrace-agents/arbiter/verdicts/spec-root-001.json" in result.output
    assert "closure_target_latest_report_path: millrace-agents/arbiter/reports/run-001.md" in result.output


def test_runs_ls_uses_run_inspection_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    seen = []

    def fake_list_runs(target):
        seen.append(target)
        return (_inspected_run_summary(),)

    monkeypatch.setattr(cli, "list_runs", fake_list_runs)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "ls", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert seen
    assert "run_id: run-001" in result.output
    assert "status: valid" in result.output
    assert "work_item_id: task-001" in result.output


def test_runs_show_prints_stage_terminal_and_artifact_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    monkeypatch.setattr(cli, "inspect_run_id", lambda target, run_id: _inspected_run_summary(run_id))

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "show", "run-001", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "run_id: run-001" in result.output
    assert "compiled_plan_id: plan-001" in result.output
    assert "mode_id: default_codex" in result.output
    assert "request_id: request-001" in result.output
    assert "stage: checker" in result.output
    assert "node_id: execution.checker.primary" in result.output
    assert "stage_kind_id: checker" in result.output
    assert "terminal_result: CHECKER_PASS" in result.output
    assert "runner_name: codex-cli" in result.output
    assert "model_name: gpt-5.4" in result.output
    assert "duration_seconds: 3.0" in result.output
    assert "input_tokens: 100" in result.output
    assert "cached_input_tokens: 30" in result.output
    assert "output_tokens: 12" in result.output
    assert "thinking_tokens: 5" in result.output
    assert "report_artifact: troubleshoot_report.md" in result.output


def test_runs_show_surfaces_closure_target_request_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    monkeypatch.setattr(
        cli,
        "inspect_run_id",
        lambda target, run_id: _inspected_run_summary(
            run_id,
            request_kind="closure_target",
            closure_target_root_spec_id="spec-root-001",
        ),
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "show", "run-001", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "request_kind: closure_target" in result.output
    assert "closure_target_root_spec_id: spec-root-001" in result.output


def test_runs_tail_chooses_primary_artifact_by_documented_priority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = tmp_path / "run-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "troubleshoot_report.md").write_text("report wins\n", encoding="utf-8")
    (run_dir / "runner_stdout.txt").write_text("stdout fallback\n", encoding="utf-8")
    (run_dir / "runner_stderr.txt").write_text("stderr fallback\n", encoding="utf-8")

    monkeypatch.setattr(
        cli,
        "inspect_run_id",
        lambda target, run_id: _inspected_run_summary(run_id, run_dir=str(run_dir)),
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["runs", "tail", "run-001", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "report wins" in result.output
    assert "stdout fallback" not in result.output


def test_add_task_add_spec_and_queue_ls(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    task_doc = tmp_path / "task-import.json"
    spec_doc = tmp_path / "spec-import.json"
    task_doc.write_text(json.dumps(_task_payload("task-001")), encoding="utf-8")
    spec_doc.write_text(json.dumps(_spec_payload("spec-001")), encoding="utf-8")

    runner = CliRunner()

    add_task = runner.invoke(
        cli.app,
        ["add-task", str(task_doc), "--workspace", str(paths.root)],
    )
    add_spec = runner.invoke(
        cli.app,
        ["add-spec", str(spec_doc), "--workspace", str(paths.root)],
    )
    ls = runner.invoke(cli.app, ["queue", "ls", "--workspace", str(paths.root)])

    assert add_task.exit_code == 0
    assert add_spec.exit_code == 0
    assert ls.exit_code == 0
    assert (paths.tasks_queue_dir / "task-001.md").is_file()
    assert (paths.specs_queue_dir / "spec-001.md").is_file()
    assert "execution_queue_depth: 1" in ls.output
    assert "planning_queue_depth: 1" in ls.output


def test_queue_add_commands_and_show_are_available_under_namespaced_surface(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    task_doc = tmp_path / "task-import.json"
    spec_doc = tmp_path / "spec-import.json"
    task_doc.write_text(json.dumps(_task_payload("task-001")), encoding="utf-8")
    spec_doc.write_text(json.dumps(_spec_payload("spec-001")), encoding="utf-8")

    runner = CliRunner()
    add_task = runner.invoke(
        cli.app,
        ["queue", "add-task", str(task_doc), "--workspace", str(paths.root)],
    )
    add_spec = runner.invoke(
        cli.app,
        ["queue", "add-spec", str(spec_doc), "--workspace", str(paths.root)],
    )
    show = runner.invoke(
        cli.app,
        ["queue", "show", "task-001", "--workspace", str(paths.root)],
    )

    assert add_task.exit_code == 0
    assert add_spec.exit_code == 0
    assert show.exit_code == 0
    assert "work_item_id: task-001" in show.output
    assert "work_item_kind: task" in show.output
    assert "work_item_state: queue" in show.output


def test_queue_add_idea_stages_markdown_in_ideas_inbox(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    idea_doc = tmp_path / "idea-001.md"
    idea_doc.write_text("# Idea 001\n\nShip this\n", encoding="utf-8")

    runner = CliRunner()
    add_idea = runner.invoke(
        cli.app,
        ["queue", "add-idea", str(idea_doc), "--workspace", str(paths.root)],
    )

    assert add_idea.exit_code == 0
    staged = paths.root / "ideas" / "inbox" / "idea-001.md"
    assert staged.is_file()


def test_queue_add_commands_route_to_mailbox_when_daemon_owns_workspace(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": True,
                "updated_at": NOW,
            }
        ),
    )
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="cli-queue-mailbox",
    )

    task_doc = tmp_path / "task-import.json"
    spec_doc = tmp_path / "spec-import.json"
    idea_doc = tmp_path / "idea-queue-mailbox.md"
    task_doc.write_text(json.dumps(_task_payload("task-mailbox")), encoding="utf-8")
    spec_doc.write_text(json.dumps(_spec_payload("spec-mailbox")), encoding="utf-8")
    idea_doc.write_text("# Mailbox idea\n", encoding="utf-8")

    runner = CliRunner()
    add_task = runner.invoke(
        cli.app,
        ["queue", "add-task", str(task_doc), "--workspace", str(paths.root)],
    )
    add_spec = runner.invoke(
        cli.app,
        ["queue", "add-spec", str(spec_doc), "--workspace", str(paths.root)],
    )
    add_idea = runner.invoke(
        cli.app,
        ["queue", "add-idea", str(idea_doc), "--workspace", str(paths.root)],
    )

    assert add_task.exit_code == 0
    assert add_spec.exit_code == 0
    assert add_idea.exit_code == 0
    assert "mode: mailbox" in add_task.output
    assert "mode: mailbox" in add_spec.output
    assert "mode: mailbox" in add_idea.output

    pending = _pending_commands(paths)
    assert MailboxCommand.ADD_TASK in pending
    assert MailboxCommand.ADD_SPEC in pending
    assert MailboxCommand.ADD_IDEA in pending
    assert not (paths.tasks_queue_dir / "task-mailbox.md").exists()
    assert not (paths.specs_queue_dir / "spec-mailbox.md").exists()
    assert not (paths.root / "ideas" / "inbox" / "idea-queue-mailbox.md").exists()


def test_queue_add_task_rejects_unsafe_task_id(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    task_doc = tmp_path / "task-import-unsafe.json"
    payload = _task_payload("task-safe")
    payload["task_id"] = "../escape"
    task_doc.write_text(json.dumps(payload), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["queue", "add-task", str(task_doc), "--workspace", str(paths.root)],
    )

    assert result.exit_code == 1
    assert "failed to add task" in result.output
    assert not (paths.root / "escape.md").exists()


def test_queue_show_rejects_unsafe_work_item_id(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["queue", "show", "../../escape", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 1
    assert "invalid work item id" in result.output


@pytest.mark.parametrize(
    ("argv", "action"),
    (
        (["pause"], MailboxCommand.PAUSE),
        (["resume"], MailboxCommand.RESUME),
        (["stop"], MailboxCommand.STOP),
        (["retry-active"], MailboxCommand.RETRY_ACTIVE),
    ),
)
def test_control_commands_delegate_to_runtime_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    action: MailboxCommand,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            del target

        def pause_runtime(self, *, issuer: str = "operator"):
            seen.append("pause")
            del issuer
            return ControlActionResult(action=MailboxCommand.PAUSE, mode="direct", applied=True, detail="ok")

        def resume_runtime(self, *, issuer: str = "operator"):
            seen.append("resume")
            del issuer
            return ControlActionResult(action=MailboxCommand.RESUME, mode="direct", applied=True, detail="ok")

        def stop_runtime(self, *, issuer: str = "operator"):
            seen.append("stop")
            del issuer
            return ControlActionResult(action=MailboxCommand.STOP, mode="direct", applied=True, detail="ok")

        def retry_active(self, *, reason: str = "operator requested retry", issuer: str = "operator"):
            seen.append("retry-active")
            del reason, issuer
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=True,
                detail="ok",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    runner = CliRunner()
    result = runner.invoke(cli.app, [*argv, "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert seen
    assert f"action: {action.value}" in result.output


@pytest.mark.parametrize(
    ("argv", "action"),
    (
        (["control", "pause"], MailboxCommand.PAUSE),
        (["control", "resume"], MailboxCommand.RESUME),
        (["control", "stop"], MailboxCommand.STOP),
        (["control", "retry-active"], MailboxCommand.RETRY_ACTIVE),
        (["control", "clear-stale-state"], MailboxCommand.CLEAR_STALE_STATE),
        (["control", "reload-config"], MailboxCommand.RELOAD_CONFIG),
    ),
)
def test_namespaced_control_commands_delegate_to_runtime_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    action: MailboxCommand,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            del target

        def pause_runtime(self, *, issuer: str = "operator"):
            seen.append("pause")
            del issuer
            return ControlActionResult(action=MailboxCommand.PAUSE, mode="direct", applied=True, detail="ok")

        def resume_runtime(self, *, issuer: str = "operator"):
            seen.append("resume")
            del issuer
            return ControlActionResult(action=MailboxCommand.RESUME, mode="direct", applied=True, detail="ok")

        def stop_runtime(self, *, issuer: str = "operator"):
            seen.append("stop")
            del issuer
            return ControlActionResult(action=MailboxCommand.STOP, mode="direct", applied=True, detail="ok")

        def retry_active(self, *, reason: str = "operator requested retry", issuer: str = "operator"):
            seen.append("retry-active")
            del reason, issuer
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=True,
                detail="ok",
            )

        def clear_stale_state(
            self,
            *,
            reason: str = "operator requested stale-state clear",
            issuer: str = "operator",
        ):
            seen.append("clear-stale-state")
            del reason, issuer
            return ControlActionResult(
                action=MailboxCommand.CLEAR_STALE_STATE,
                mode="direct",
                applied=True,
                detail="ok",
            )

        def reload_config(self, *, issuer: str = "operator"):
            seen.append("reload-config")
            del issuer
            return ControlActionResult(
                action=MailboxCommand.RELOAD_CONFIG,
                mode="direct",
                applied=True,
                detail="ok",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    runner = CliRunner()
    result = runner.invoke(cli.app, [*argv, "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert seen
    assert f"action: {action.value}" in result.output


def test_planning_retry_active_command_delegates_to_runtime_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            del target

        def retry_active_planning(self, *, reason: str = "operator requested retry", issuer: str = "operator"):
            seen.append(f"{reason}|{issuer}")
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=True,
                detail="planning retry applied",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["planning", "retry-active", "--workspace", str(paths.root), "--reason", "planning retry"],
    )

    assert result.exit_code == 0
    assert seen == ["planning retry|operator"]
    assert "detail: planning retry applied" in result.output


def test_top_level_clear_stale_state_alias_delegates_to_runtime_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            del target

        def clear_stale_state(
            self,
            *,
            reason: str = "operator requested stale-state clear",
            issuer: str = "operator",
        ):
            seen.append("clear-stale-state")
            del reason, issuer
            return ControlActionResult(
                action=MailboxCommand.CLEAR_STALE_STATE,
                mode="direct",
                applied=True,
                detail="ok",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["clear-stale-state", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert seen == ["clear-stale-state"]
    assert "action: clear_stale_state" in result.output


def test_reload_config_routes_to_mailbox_when_workspace_has_daemon_owner(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": True,
                "updated_at": NOW,
            }
        ),
    )
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="cli-reload-mailbox",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["control", "reload-config", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert "mode: mailbox" in result.output
    assert MailboxCommand.RELOAD_CONFIG in _pending_commands(paths)


def test_config_show_renders_effective_runtime_and_reload_state(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'default_mode = "standard_plain"',
                'run_style = "daemon"',
                "",
                "[watchers]",
                "enabled = true",
            ]
        ),
        encoding="utf-8",
    )
    snapshot = load_snapshot(paths).model_copy(
        update={
            "config_version": "cfg-active-123",
            "last_reload_outcome": ReloadOutcome.FAILED_RETAINED_PREVIOUS_PLAN,
            "last_reload_error": "mode lookup failed",
        }
    )
    save_snapshot(paths, snapshot)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "show", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "default_mode: standard_plain" in result.output
    assert "run_style: daemon" in result.output
    assert "watchers.enabled: true" in result.output
    assert "config_version: cfg-active-123" in result.output
    assert "last_reload_outcome: failed_retained_previous_plan" in result.output
    assert "last_reload_error: mode lookup failed" in result.output


def test_config_validate_returns_nonzero_for_invalid_config(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config_path = paths.runtime_root / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[compile]",
                'default_execution_loop = "execution.standard"',
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "validate", "--workspace", str(paths.root)])

    assert result.exit_code == 1
    assert "error:" in result.output


def test_config_reload_command_delegates_to_runtime_control(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    seen: list[str] = []

    class FakeRuntimeControl:
        def __init__(self, target) -> None:
            del target

        def reload_config(self, *, issuer: str = "operator"):
            seen.append(issuer)
            return ControlActionResult(
                action=MailboxCommand.RELOAD_CONFIG,
                mode="direct",
                applied=True,
                detail="config reload applied",
            )

    monkeypatch.setattr(cli, "RuntimeControl", FakeRuntimeControl)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "reload", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert seen == ["operator"]
    assert "detail: config reload applied" in result.output


def test_modes_list_outputs_shipped_modes() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["modes", "list"])

    assert result.exit_code == 0
    assert "default_codex" in result.output
    assert "default_pi" in result.output
    assert "standard_plain -> default_codex" in result.output
    assert "standard_role_augmented" not in result.output


def test_modes_show_reports_alias_resolution_for_standard_plain() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["modes", "show", "standard_plain"])

    assert result.exit_code == 0
    assert "alias_of: default_codex" in result.output
    assert "mode_id: default_codex" in result.output


def test_compile_validate_returns_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    def fake_load_runtime_config(config_path=None, *, mailbox_overrides=None, cli_overrides=None):
        del config_path, mailbox_overrides, cli_overrides
        return RuntimeConfig()

    def fake_compile_and_persist_workspace_plan(
        target,
        *,
        config,
        requested_mode_id=None,
        assets_root=None,
        now=None,
    ):
        del target, config, requested_mode_id, assets_root, now
        diagnostics = CompileDiagnostics(
            ok=False,
            mode_id="broken-mode",
            errors=("mode lookup failed",),
            emitted_at=NOW,
        )
        return CompileOutcome(active_plan=None, diagnostics=diagnostics, used_last_known_good=False)

    monkeypatch.setattr(cli, "load_runtime_config", fake_load_runtime_config)
    monkeypatch.setattr(cli, "compile_and_persist_workspace_plan", fake_compile_and_persist_workspace_plan)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["compile", "validate", "--workspace", str(paths.root), "--mode", "broken-mode"],
    )

    assert result.exit_code == 1
    assert "ok: false" in result.output
    assert "mode lookup failed" in result.output


def test_compile_show_surfaces_compiled_plan_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    observed: dict[str, object] = {}

    def fake_load_runtime_config(config_path=None, *, mailbox_overrides=None, cli_overrides=None):
        del config_path, mailbox_overrides, cli_overrides
        return RuntimeConfig()

    def fake_compile_and_persist_workspace_plan(
        target,
        *,
        config,
        requested_mode_id=None,
        assets_root=None,
        now=None,
    ):
        del target, config, requested_mode_id, now
        observed["assets_root"] = assets_root
        diagnostics = CompileDiagnostics(
            ok=True,
            mode_id="standard_plain",
            errors=(),
            emitted_at=NOW,
        )
        active_plan = SimpleNamespace(
            compiled_plan_id="plan-001",
            mode_id="standard_plain",
            learning_graph=None,
            execution_loop_id="execution.standard",
            planning_loop_id="planning.standard",
            execution_graph=SimpleNamespace(
                nodes=(
                    SimpleNamespace(
                        plane=Plane.EXECUTION,
                        node_id="builder",
                        stage_kind_id="builder",
                        running_status_marker="BUILDER_RUNNING",
                        entrypoint_path="entrypoints/execution/builder.md",
                        entrypoint_contract_id="builder.contract.v1",
                        required_skill_paths=("skills/stage/execution/builder-core/SKILL.md",),
                        attached_skill_additions=(),
                        runner_name="codex_cli",
                        model_name=None,
                        timeout_seconds=3600,
                    ),
                ),
                compiled_entries=(
                    SimpleNamespace(
                        entry_key=SimpleNamespace(value="task"),
                        node_id="builder",
                    ),
                ),
                compiled_completion_entry=None,
            ),
            planning_graph=SimpleNamespace(
                nodes=(
                    SimpleNamespace(
                        plane=Plane.PLANNING,
                        node_id="arbiter",
                        stage_kind_id="arbiter",
                        running_status_marker="ARBITER_RUNNING",
                        entrypoint_path="entrypoints/planning/arbiter.md",
                        entrypoint_contract_id="arbiter.contract.v1",
                        required_skill_paths=("skills/stage/planning/arbiter-core/SKILL.md",),
                        attached_skill_additions=(),
                        runner_name="codex_cli",
                        model_name=None,
                        timeout_seconds=3600,
                    ),
                ),
                compiled_entries=(
                    SimpleNamespace(
                        entry_key=SimpleNamespace(value="spec"),
                        node_id="planner",
                    ),
                    SimpleNamespace(
                        entry_key=SimpleNamespace(value="incident"),
                        node_id="auditor",
                    ),
                ),
                compiled_completion_entry=SimpleNamespace(
                    entry_key=SimpleNamespace(value="closure_target"),
                    node_id="arbiter",
                ),
                completion_behavior=SimpleNamespace(
                    trigger="backlog_drained",
                    readiness_rule="no_open_lineage_work",
                    request_kind="closure_target",
                    target_selector="active_closure_target",
                    rubric_policy="reuse_or_create",
                    blocked_work_policy="suppress",
                    skip_if_already_closed=True,
                    on_pass_terminal_state_id="arbiter_complete",
                    on_gap_terminal_state_id="remediation_needed",
                    create_incident_on_gap=True,
                ),
            ),
        )
        return CompileOutcome(
            active_plan=active_plan,
            diagnostics=diagnostics,
            used_last_known_good=False,
            compile_input_fingerprint=SimpleNamespace(
                mode_id="standard_plain",
                config_fingerprint="cfg-001",
                assets_fingerprint="assets-001",
            ),
        )

    monkeypatch.setattr(cli, "load_runtime_config", fake_load_runtime_config)
    monkeypatch.setattr(cli, "compile_and_persist_workspace_plan", fake_compile_and_persist_workspace_plan)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["compile", "show", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert observed["assets_root"] == paths.runtime_root
    assert "graph_authoritative_for_runtime_execution:" not in result.output
    assert "graph_legacy_equivalence_ready_for_cutover:" not in result.output
    assert "graph_legacy_equivalence_issues:" not in result.output
    assert "entry: execution.task -> builder" in result.output
    assert "entry: planning.spec -> planner" in result.output
    assert "entry: planning.incident -> auditor" in result.output
    assert "completion: closure_target -> arbiter" in result.output
    assert "baseline_manifest_id:" in result.output
    assert "compiled_plan_currentness: current" in result.output
    assert "compile_input.mode_id: standard_plain" in result.output
    assert "compile_input.config_fingerprint: cfg-001" in result.output
    assert "compile_input.assets_fingerprint: assets-001" in result.output
    assert "compiled_plan_id: plan-001" in result.output
    assert "stage: execution.builder" in result.output
    assert "stage_kind_id: builder" in result.output
    assert "running_status_marker: BUILDER_RUNNING" in result.output
    assert "entrypoint_path: entrypoints/execution/builder.md" in result.output
    assert "entrypoint_contract_id: builder.contract.v1" in result.output
    assert "required_skills: skills/stage/execution/builder-core/SKILL.md" in result.output
    assert "attached_skills: none" in result.output
    assert "runner_name: codex_cli" in result.output
    assert "model_name: none" in result.output
    assert "timeout_seconds: 3600" in result.output
    assert "completion_behavior.trigger: backlog_drained" in result.output
    assert "completion_behavior.request_kind: closure_target" in result.output
    assert "completion_behavior.on_gap_terminal_state_id: remediation_needed" in result.output
    assert "role_overlays:" not in result.output


def test_doctor_command_surfaces_workspace_diagnostics(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["doctor", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "ok: true" in result.output


def test_upgrade_command_previews_three_way_classification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    assets_root = _copy_assets(tmp_path)
    (assets_root / "entrypoints" / "execution" / "builder.md").write_text(
        "candidate builder update\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "millrace_ai.workspace.baseline._resolve_asset_source_root",
        lambda _: assets_root,
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["upgrade", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "applied: false" in result.output
    assert "baseline_manifest_id:" in result.output
    assert "candidate_manifest_id:" in result.output
    assert "entry: entrypoints/execution/builder.md safe_package_update" in result.output


def test_upgrade_command_apply_refreshes_managed_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    assets_root = _copy_assets(tmp_path)
    (assets_root / "entrypoints" / "execution" / "builder.md").write_text(
        "candidate builder update\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "millrace_ai.workspace.baseline._resolve_asset_source_root",
        lambda _: assets_root,
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["upgrade", "--apply", "--workspace", str(paths.root)])

    assert result.exit_code == 0
    assert "applied: true" in result.output
    assert "result_manifest_id:" in result.output
    assert (
        paths.runtime_root / "entrypoints" / "execution" / "builder.md"
    ).read_text(encoding="utf-8") == "candidate builder update\n"


def test_status_watch_outputs_multiple_updates_with_bound(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "status",
            "watch",
            "--workspace",
            str(paths.root),
            "--max-updates",
            "2",
            "--interval-seconds",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert result.output.count("runtime_mode:") >= 2


def test_status_watch_can_observe_multiple_workspaces_in_one_session(tmp_path: Path) -> None:
    first_paths = _workspace(tmp_path / "first")
    second_paths = _workspace(tmp_path / "second")
    first_lock_path = first_paths.runtime_lock_file
    second_lock_path = second_paths.runtime_lock_file
    assert first_lock_path.exists() is False
    assert second_lock_path.exists() is False

    first_snapshot = load_snapshot(first_paths).model_copy(
        update={
            "active_mode_id": "standard_plain",
            "compiled_plan_id": "plan-first",
            "runtime_mode": RuntimeMode.ONCE,
        }
    )
    second_snapshot = load_snapshot(second_paths).model_copy(
        update={
            "active_mode_id": "standard_plain",
            "compiled_plan_id": "plan-second",
            "runtime_mode": RuntimeMode.DAEMON,
        }
    )
    save_snapshot(first_paths, first_snapshot)
    save_snapshot(second_paths, second_snapshot)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "status",
            "watch",
            "--workspace",
            str(first_paths.root),
            "--workspace",
            str(second_paths.root),
            "--max-updates",
            "1",
            "--interval-seconds",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert f"workspace: {first_paths.root}" in result.output
    assert f"workspace: {second_paths.root}" in result.output
    assert "compiled_plan_id: plan-first" in result.output
    assert "compiled_plan_id: plan-second" in result.output
    assert first_lock_path.exists() is False
    assert second_lock_path.exists() is False


def test_main_passes_provided_argv_through_to_typer_app(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_app(*, args=None, standalone_mode=False):
        observed["args"] = args
        observed["standalone_mode"] = standalone_mode

    monkeypatch.setattr(cli, "app", fake_app)
    argv = ["status", "--workspace", "/tmp/workspace"]

    exit_code = cli.main(argv)

    assert exit_code == 0
    assert observed["args"] is argv
    assert observed["standalone_mode"] is False


def test_main_with_none_argv_does_not_inject_fallback_args(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_app(*, args=None, standalone_mode=False):
        observed["args"] = args
        observed["standalone_mode"] = standalone_mode

    monkeypatch.setattr(cli, "app", fake_app)

    exit_code = cli.main()

    assert exit_code == 0
    assert observed["args"] is None
    assert observed["standalone_mode"] is False


def test_main_returns_nonzero_when_typer_app_returns_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_app(*, args=None, standalone_mode=False):
        del args, standalone_mode
        return 3

    monkeypatch.setattr(cli, "app", fake_app)

    exit_code = cli.main(["status"])

    assert exit_code == 3


def test_run_once_defaults_config_to_workspace_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = _workspace(tmp_path)
    observed: dict[str, object] = {}

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
        ) -> None:
            del target, stage_runner, mode_id, assets_root
            observed["config_path"] = config_path

        def startup(self):
            return SimpleNamespace(
                active_mode_id="standard_plain",
                compiled_plan_id="plan-001",
            )

        def tick(self):
            return SimpleNamespace(
                router_decision=SimpleNamespace(reason="no_work"),
                stage_result=SimpleNamespace(
                    metadata={"failure_class": None},
                    result_class=ResultClass.SUCCESS,
                ),
            )

        def close(self):
            return None

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "once", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert observed["config_path"] == paths.runtime_root / "millrace.toml"


def test_run_daemon_defaults_config_to_workspace_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = _workspace(tmp_path)
    observed: dict[str, object] = {}

    class FakeRuntimeEngine:
        def __init__(
            self,
            target,
            *,
            stage_runner,
            config_path=None,
            mode_id=None,
            assets_root=None,
            monitor=None,
        ) -> None:
            del target, stage_runner, mode_id, assets_root, monitor
            observed["config_path"] = config_path
            self.snapshot = SimpleNamespace(stop_requested=False, process_running=True)

        def startup(self):
            return SimpleNamespace(
                active_mode_id="standard_plain",
                compiled_plan_id="plan-001",
            )

        def tick(self):
            return SimpleNamespace(router_decision=SimpleNamespace(reason="loop"))

    monkeypatch.setattr(cli, "RuntimeEngine", FakeRuntimeEngine)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "daemon", "--workspace", str(paths.root), "--max-ticks", "1"],
    )

    assert result.exit_code == 0
    assert observed["config_path"] == paths.runtime_root / "millrace.toml"


def test_compile_validate_defaults_config_to_workspace_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = _workspace(tmp_path)
    observed: dict[str, object] = {}

    def fake_load_runtime_config(config_path=None, *, mailbox_overrides=None, cli_overrides=None):
        del mailbox_overrides, cli_overrides
        observed["config_path"] = config_path
        return RuntimeConfig()

    def fake_compile_and_persist_workspace_plan(
        target,
        *,
        config,
        requested_mode_id=None,
        assets_root=None,
        now=None,
    ):
        del target, config, requested_mode_id, assets_root, now
        diagnostics = CompileDiagnostics(
            ok=True,
            mode_id="standard_plain",
            errors=(),
            emitted_at=NOW,
        )
        return CompileOutcome(active_plan=None, diagnostics=diagnostics, used_last_known_good=False)

    monkeypatch.setattr(cli, "load_runtime_config", fake_load_runtime_config)
    monkeypatch.setattr(cli, "compile_and_persist_workspace_plan", fake_compile_and_persist_workspace_plan)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["compile", "validate", "--workspace", str(paths.root)],
    )

    assert result.exit_code == 0
    assert observed["config_path"] == paths.runtime_root / "millrace.toml"
