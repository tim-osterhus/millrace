from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import ExecutionStageName, Plane, TokenUsage, WorkItemKind
from millrace_ai.runner import StageRunRequest
from millrace_ai.runners.adapters.pi_rpc import PiRpcRunnerAdapter, PiRpcSessionResult


def _request(tmp_path: Path) -> StageRunRequest:
    run_dir = tmp_path / "runs" / "run-001"
    run_dir.mkdir(parents=True, exist_ok=True)
    entrypoint_path = tmp_path / "entrypoint.md"
    entrypoint_path.write_text("# Builder\n", encoding="utf-8")
    return StageRunRequest(
        request_id="req-001",
        run_id="run-001",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
        mode_id="default_pi",
        compiled_plan_id="plan-001",
        entrypoint_path=str(entrypoint_path),
        entrypoint_contract_id="builder.contract.v1",
        active_work_item_kind=WorkItemKind.TASK,
        active_work_item_id="task-001",
        active_work_item_path=str(tmp_path / "task-001.md"),
        run_dir=str(run_dir),
        summary_status_path=str(tmp_path / "execution_status.md"),
        runtime_snapshot_path=str(tmp_path / "runtime_snapshot.json"),
        recovery_counters_path=str(tmp_path / "recovery_counters.json"),
        runner_name="pi_rpc",
        model_name="openai/gpt-5.4",
        timeout_seconds=120,
    )


def _completed_session_result(*, event_lines: tuple[str, ...]) -> PiRpcSessionResult:
    now = datetime.now(timezone.utc)
    return PiRpcSessionResult(
        exit_kind="completed",
        exit_code=0,
        started_at=now,
        ended_at=now,
        event_lines=event_lines,
        assistant_text="\n### BUILDER_COMPLETE\n\n",
        token_usage=TokenUsage(
            input_tokens=120,
            cached_input_tokens=20,
            output_tokens=14,
            thinking_tokens=0,
            total_tokens=134,
        ),
        failure_class=None,
        notes=(),
        stderr_text="",
    )


def test_pi_adapter_omits_event_log_for_success_by_default(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    observed_command: list[tuple[str, ...]] = []
    observed_prompt: list[str] = []

    def fake_client_factory(*, command, cwd, env):
        del cwd, env
        observed_command.append(command)

        class _FakeClient:
            def run_prompt(self, *, prompt, timeout_seconds):
                del timeout_seconds
                observed_prompt.append(prompt)
                return _completed_session_result(
                    event_lines=(
                        '{"type":"agent_start"}',
                        '{"type":"agent_end"}',
                    ),
                )

        return _FakeClient()

    adapter = PiRpcRunnerAdapter(
        config=RuntimeConfig(),
        workspace_root=tmp_path,
        client_factory=fake_client_factory,
    )

    result = adapter.run(request)

    assert result.exit_kind == "completed"
    assert result.runner_name == "pi_rpc"
    assert result.stdout_path is not None
    assert Path(result.stdout_path).read_text(encoding="utf-8") == "\n### BUILDER_COMPLETE\n\n"
    assert result.event_log_path is None
    assert not (Path(request.run_dir) / "runner_events.req-001.jsonl").exists()
    assert result.token_usage == TokenUsage(
        input_tokens=120,
        cached_input_tokens=20,
        output_tokens=14,
        thinking_tokens=0,
        total_tokens=134,
    )
    assert observed_prompt
    assert observed_command == [
        (
            "pi",
            "--mode",
            "rpc",
            "--no-session",
            "--model",
            "openai/gpt-5.4",
            "--no-context-files",
            "--no-skills",
        )
    ]


def test_pi_request_carries_compiled_identity_defaults(tmp_path: Path) -> None:
    request = _request(tmp_path)

    assert request.node_id == "builder"
    assert request.stage_kind_id == "builder"
    assert request.running_status_marker == "BUILDER_RUNNING"
    assert request.legal_terminal_markers == (
        "### BUILDER_COMPLETE",
        "### BLOCKED",
    )


def test_pi_adapter_persists_event_log_when_full_policy_is_enabled(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    def fake_client_factory(*, command, cwd, env):
        del command, cwd, env

        class _FakeClient:
            def run_prompt(self, *, prompt, timeout_seconds):
                del prompt, timeout_seconds
                return _completed_session_result(
                    event_lines=(
                        '{"type":"agent_start"}',
                        '{"type":"message_update"}',
                        '{"type":"agent_end"}',
                    ),
                )

        return _FakeClient()

    adapter = PiRpcRunnerAdapter(
        config=RuntimeConfig(runners={"pi": {"event_log_policy": "full"}}),
        workspace_root=tmp_path,
        client_factory=fake_client_factory,
    )

    result = adapter.run(request)

    assert result.event_log_path is not None
    assert Path(result.event_log_path).read_text(encoding="utf-8").splitlines() == [
        '{"type":"agent_start"}',
        '{"type":"agent_end"}',
    ]


def test_pi_adapter_persists_event_log_for_failures_by_default(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    def fake_client_factory(*, command, cwd, env):
        del command, cwd, env

        class _FakeClient:
            def run_prompt(self, *, prompt, timeout_seconds):
                del prompt, timeout_seconds
                now = datetime.now(timezone.utc)
                return PiRpcSessionResult(
                    exit_kind="timeout",
                    exit_code=124,
                    started_at=now,
                    ended_at=now,
                    event_lines=(
                        '{"type":"agent_start"}',
                        '{"type":"message_update"}',
                        '{"type":"agent_end","stopReason":"error"}',
                    ),
                    assistant_text=None,
                    token_usage=None,
                    failure_class="runner_timeout",
                    notes=("runner process exceeded timeout",),
                    stderr_text="timed out",
                )

        return _FakeClient()

    adapter = PiRpcRunnerAdapter(
        config=RuntimeConfig(),
        workspace_root=tmp_path,
        client_factory=fake_client_factory,
    )

    result = adapter.run(request)

    assert result.exit_kind == "timeout"
    assert result.event_log_path is not None
    assert Path(result.event_log_path).read_text(encoding="utf-8").splitlines() == [
        '{"type":"agent_start"}',
        '{"type":"agent_end","stopReason":"error"}',
    ]


def test_pi_adapter_omits_event_log_when_only_message_updates_would_remain(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    def fake_client_factory(*, command, cwd, env):
        del command, cwd, env

        class _FakeClient:
            def run_prompt(self, *, prompt, timeout_seconds):
                del prompt, timeout_seconds
                return _completed_session_result(
                    event_lines=(
                        '{"type":"message_update"}',
                        '{"type":"message_update"}',
                    ),
                )

        return _FakeClient()

    adapter = PiRpcRunnerAdapter(
        config=RuntimeConfig(runners={"pi": {"event_log_policy": "full"}}),
        workspace_root=tmp_path,
        client_factory=fake_client_factory,
    )

    result = adapter.run(request)

    assert result.event_log_path is None
    assert not (Path(request.run_dir) / "runner_events.req-001.jsonl").exists()
