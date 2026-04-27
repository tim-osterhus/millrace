from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ExecutionTerminalResult,
    TaskDocument,
    TokenUsage,
)
from millrace_ai.mailbox import write_mailbox_command
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.usage_governance import (
    SubscriptionQuotaStatus,
    SubscriptionQuotaWindowReading,
    evaluate_usage_governance,
    load_usage_governance_state,
)
from millrace_ai.state_store import load_snapshot

NOW = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)


def test_usage_governance_public_exports_remain_importable() -> None:
    import millrace_ai.runtime.usage_governance as usage_governance

    for name in usage_governance.__all__:
        assert hasattr(usage_governance, name), name


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="usage governance runtime task",
        target_paths=["src/millrace_ai/runtime/usage_governance.py"],
        acceptance=["usage governance enforces between-stage pauses"],
        required_checks=["uv run --extra dev python -m pytest tests/runtime/test_usage_governance.py -q"],
        references=[
            "lab/specs/review/2026-04-26-millrace-usage-governance-auto-pause-resume-spec.md"
        ],
        risk=["runtime governance drift"],
        created_at=NOW,
        created_by="tests",
    )


def _write_governance_config(paths, *, threshold: int = 100, auto_resume: bool = True) -> None:
    paths.runtime_root.joinpath("millrace.toml").write_text(
        "\n".join(
            [
                "[runtime]",
                'default_mode = "default_codex"',
                'run_style = "daemon"',
                "",
                "[usage_governance]",
                "enabled = true",
                f"auto_resume = {'true' if auto_resume else 'false'}",
                "",
                "[usage_governance.runtime_token_rules]",
                "enabled = true",
                "",
                "[[usage_governance.runtime_token_rules.rules]]",
                'rule_id = "test-rolling"',
                'window = "rolling_5h"',
                'metric = "total_tokens"',
                f"threshold = {threshold}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _runner_result(
    request: StageRunRequest,
    *,
    terminal: str,
    token_usage: TokenUsage | None,
    now: datetime = NOW,
) -> RunnerRawResult:
    run_dir = Path(request.run_dir)
    stdout_path = run_dir / "runner_stdout.txt"
    stdout_path.write_text(f"### {terminal}\n", encoding="utf-8")
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name=request.runner_name or "test-runner",
        model_name=request.model_name,
        exit_kind="completed",
        exit_code=0,
        stdout_path=str(stdout_path),
        stderr_path=None,
        terminal_result_path=None,
        observed_exit_kind=None,
        observed_exit_code=None,
        started_at=now,
        ended_at=now + timedelta(seconds=1),
        token_usage=token_usage,
    )


def test_usage_governance_is_fully_inert_by_default(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            token_usage=TokenUsage(total_tokens=500_000),
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    outcome = engine.tick()

    assert outcome.stage.value == "builder"
    snapshot = load_snapshot(paths)
    assert snapshot.paused is False
    assert snapshot.pause_sources == ()
    assert not paths.usage_governance_state_file.exists()
    assert not paths.usage_governance_ledger_file.exists()


def test_runtime_token_rule_pauses_between_stages_and_counts_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _write_governance_config(paths, threshold=100)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))
    calls: list[str] = []
    monkeypatch.setattr("millrace_ai.runtime.stage_requests.now", lambda: NOW)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        calls.append(request.stage.value)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            token_usage=TokenUsage(input_tokens=50, output_tokens=75, total_tokens=125),
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    first = engine.tick()
    second = engine.tick()

    assert first.stage.value == "builder"
    assert second.router_decision.reason == "paused"
    assert calls == ["builder"]

    snapshot = load_snapshot(paths)
    assert snapshot.paused is True
    assert snapshot.pause_sources == ("usage_governance",)

    state = load_usage_governance_state(paths)
    assert state.enabled is True
    assert state.paused_by_governance is True
    assert [blocker.rule_id for blocker in state.active_blockers] == ["test-rolling"]
    assert state.active_blockers[0].observed == 125

    ledger_lines = paths.usage_governance_ledger_file.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 1
    assert json.loads(ledger_lines[0])["stage_result_path"] == first.stage_result_path.relative_to(
        paths.root
    ).as_posix()


def test_missing_ledger_entry_is_reconciled_from_stage_result_after_restart(
    monkeypatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _write_governance_config(paths, threshold=100)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))
    monkeypatch.setattr("millrace_ai.runtime.stage_requests.now", lambda: NOW)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            token_usage=TokenUsage(total_tokens=125),
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    engine.tick()
    engine.close()
    paths.usage_governance_ledger_file.unlink()

    restarted = RuntimeEngine(
        paths,
        stage_runner=lambda request: (_ for _ in ()).throw(
            AssertionError("stage runner should not launch while governance is paused")
        ),
    )
    outcome = restarted.tick()

    assert outcome.router_decision.reason == "paused"
    ledger_lines = paths.usage_governance_ledger_file.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 1
    assert load_usage_governance_state(paths).active_blockers[0].rule_id == "test-rolling"


def test_manual_resume_cannot_bypass_active_governance_blocker(
    monkeypatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _write_governance_config(paths, threshold=100)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))
    calls: list[str] = []
    monkeypatch.setattr("millrace_ai.runtime.stage_requests.now", lambda: NOW)

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        calls.append(request.stage.value)
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
            token_usage=TokenUsage(total_tokens=125),
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    monkeypatch.setattr("millrace_ai.runtime.stage_requests.now", lambda: NOW)
    engine.tick()
    write_mailbox_command(
        paths,
        {
            "command_id": "resume-under-governance",
            "command": "resume",
            "issued_at": NOW,
            "issuer": "tests",
            "payload": {},
        },
    )

    outcome = engine.tick()

    assert outcome.router_decision.reason == "paused"
    assert calls == ["builder"]
    snapshot = load_snapshot(paths)
    assert snapshot.paused is True
    assert snapshot.pause_sources == ("usage_governance",)


def test_auto_resume_clears_governance_pause_when_rolling_window_expires(
    monkeypatch,
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _write_governance_config(paths, threshold=100)
    QueueStore(paths).enqueue_task(_task_doc("task-001"))
    calls: list[str] = []

    def stage_runner(request: StageRunRequest) -> RunnerRawResult:
        calls.append(request.stage.value)
        if request.stage.value == "builder":
            return _runner_result(
                request,
                terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
                token_usage=TokenUsage(total_tokens=125),
                now=NOW,
            )
        return _runner_result(
            request,
            terminal=ExecutionTerminalResult.CHECKER_PASS.value,
            token_usage=TokenUsage(total_tokens=1),
            now=NOW + timedelta(hours=6),
        )

    engine = RuntimeEngine(paths, stage_runner=stage_runner)
    monkeypatch.setattr("millrace_ai.runtime.stage_requests.now", lambda: NOW)
    engine.tick()
    assert load_snapshot(paths).paused is True

    monkeypatch.setattr(
        "millrace_ai.runtime.stage_requests.now",
        lambda: NOW + timedelta(hours=6),
    )
    outcome = engine.tick()

    assert outcome.stage.value == "checker"
    assert calls == ["builder", "checker"]
    snapshot = load_snapshot(paths)
    assert snapshot.paused is False
    assert snapshot.pause_sources == ()


def test_operator_pause_never_auto_resumes(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    _write_governance_config(paths, threshold=100)
    snapshot = load_snapshot(paths)
    from millrace_ai.state_store import save_snapshot

    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "paused": True,
                "pause_sources": ("operator",),
                "updated_at": NOW,
            }
        ),
    )

    engine = RuntimeEngine(paths, stage_runner=lambda request: _runner_result(
        request,
        terminal=ExecutionTerminalResult.BUILDER_COMPLETE.value,
        token_usage=None,
    ))
    outcome = engine.tick()

    assert outcome.router_decision.reason == "paused"
    snapshot = load_snapshot(paths)
    assert snapshot.paused is True
    assert snapshot.pause_sources == ("operator",)
    assert load_usage_governance_state(paths).active_blockers == ()


def test_subscription_quota_degraded_defaults_fail_open(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config = RuntimeConfig(
        usage_governance={
            "enabled": True,
            "subscription_quota_rules": {"enabled": True},
            "runtime_token_rules": {"enabled": False},
        }
    )

    class DegradedProvider:
        def read(self, *, now: datetime) -> SubscriptionQuotaStatus:
            return SubscriptionQuotaStatus(
                enabled=True,
                provider="codex_chatgpt_oauth",
                state="degraded",
                detail="quota_telemetry_unavailable",
                last_refreshed_at=now,
            )

    state = evaluate_usage_governance(
        paths,
        config=config,
        now=NOW,
        daemon_session_id="session-001",
        paused_by_governance=False,
        subscription_provider=DegradedProvider(),
    )

    assert state.subscription_quota_status.state == "degraded"
    assert state.active_blockers == ()


def test_subscription_quota_fail_closed_blocks_when_telemetry_degraded(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config = RuntimeConfig(
        usage_governance={
            "enabled": True,
            "subscription_quota_rules": {
                "enabled": True,
                "degraded_policy": "fail_closed",
            },
            "runtime_token_rules": {"enabled": False},
        }
    )

    class DegradedProvider:
        def read(self, *, now: datetime) -> SubscriptionQuotaStatus:
            return SubscriptionQuotaStatus(
                enabled=True,
                provider="codex_chatgpt_oauth",
                state="degraded",
                detail="quota_telemetry_unavailable",
                last_refreshed_at=now,
            )

    state = evaluate_usage_governance(
        paths,
        config=config,
        now=NOW,
        daemon_session_id="session-001",
        paused_by_governance=False,
        subscription_provider=DegradedProvider(),
    )

    assert [blocker.rule_id for blocker in state.active_blockers] == [
        "subscription-quota-degraded-fail-closed"
    ]
    assert state.auto_resume_possible is False


def test_subscription_quota_reading_blocks_at_configured_percent(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    config = RuntimeConfig(
        usage_governance={
            "enabled": True,
            "subscription_quota_rules": {
                "enabled": True,
                "rules": [
                    {
                        "rule_id": "quota-five-hour-test",
                        "window": "five_hour",
                        "pause_at_percent_used": 95,
                    }
                ],
            },
            "runtime_token_rules": {"enabled": False},
        }
    )

    class HealthyProvider:
        def read(self, *, now: datetime) -> SubscriptionQuotaStatus:
            return SubscriptionQuotaStatus(
                enabled=True,
                provider="codex_chatgpt_oauth",
                state="healthy",
                last_refreshed_at=now,
                windows={
                    "five_hour": SubscriptionQuotaWindowReading(
                        window="five_hour",
                        percent_used=96,
                        resets_at=now + timedelta(hours=1),
                        read_at=now,
                    )
                },
            )

    state = evaluate_usage_governance(
        paths,
        config=config,
        now=NOW,
        daemon_session_id="session-001",
        paused_by_governance=False,
        subscription_provider=HealthyProvider(),
    )

    assert [blocker.rule_id for blocker in state.active_blockers] == ["quota-five-hour-test"]
    assert state.next_auto_resume_at == NOW + timedelta(hours=1)
