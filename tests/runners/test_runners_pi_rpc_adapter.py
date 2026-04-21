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


def test_pi_adapter_builds_reserved_transport_flags_and_materializes_events(
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
                now = datetime.now(timezone.utc)
                return PiRpcSessionResult(
                    exit_kind="completed",
                    exit_code=0,
                    started_at=now,
                    ended_at=now,
                    event_lines=(
                        '{"type":"agent_start"}',
                        '{"type":"agent_end"}',
                    ),
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
    assert result.event_log_path is not None
    assert Path(result.event_log_path).read_text(encoding="utf-8").splitlines() == [
        '{"type":"agent_start"}',
        '{"type":"agent_end"}',
    ]
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
