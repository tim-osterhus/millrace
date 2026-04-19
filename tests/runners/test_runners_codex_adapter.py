from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.config import CodexPermissionLevel, RuntimeConfig
from millrace_ai.contracts import ExecutionStageName, Plane, TokenUsage, WorkItemKind
from millrace_ai.runner import StageRunRequest
from millrace_ai.runners.adapters.codex_cli import CodexCliRunnerAdapter
from millrace_ai.runners.errors import RunnerBinaryNotFoundError
from millrace_ai.runners.process import ProcessExecutionResult


def _request(
    tmp_path: Path,
    *,
    stage: ExecutionStageName = ExecutionStageName.BUILDER,
    model_name: str = "gpt-5",
    request_id: str = "req-001",
    run_id: str = "run-001",
) -> StageRunRequest:
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    entrypoint_path = tmp_path / "entrypoint.md"
    entrypoint_path.write_text("# Builder\n", encoding="utf-8")
    return StageRunRequest(
        request_id=request_id,
        run_id=run_id,
        plane=Plane.EXECUTION,
        stage=stage,
        mode_id="standard_plain",
        compiled_plan_id="plan-001",
        entrypoint_path=str(entrypoint_path),
        entrypoint_contract_id="builder.contract.v1",
        active_work_item_kind=WorkItemKind.TASK,
        active_work_item_id="task-001",
        active_work_item_path=str(tmp_path / "task-001.json"),
        run_dir=str(run_dir),
        summary_status_path=str(tmp_path / "execution_status.md"),
        runtime_snapshot_path=str(tmp_path / "runtime_snapshot.json"),
        recovery_counters_path=str(tmp_path / "recovery_counters.json"),
        runner_name="codex_cli",
        model_name=model_name,
        timeout_seconds=120,
    )


def _command_option_value(command: tuple[str, ...], flag: str) -> str:
    index = command.index(flag)
    return command[index + 1]


def test_codex_adapter_writes_invocation_and_completion_artifacts(tmp_path: Path) -> None:
    request = _request(tmp_path)

    def fake_execute(*, command, cwd, env, timeout_seconds, stdout_path, stderr_path):
        del cwd, env, timeout_seconds
        Path(stdout_path).write_text(
            "\n".join(
                (
                    '{"type":"thread.started","thread_id":"thread-001"}',
                    (
                        '{"type":"event_msg","payload":{"type":"token_count","info":'
                        '{"total_token_usage":{"input_tokens":120,"cached_input_tokens":40,'
                        '"output_tokens":12,"reasoning_output_tokens":5,"total_tokens":132}}}}'
                    ),
                )
            )
            + "\n",
            encoding="utf-8",
        )
        Path(_command_option_value(command, "--output-last-message")).write_text(
            "### BUILDER_COMPLETE\n",
            encoding="utf-8",
        )
        Path(stderr_path).write_text("", encoding="utf-8")
        now = datetime.now(timezone.utc)
        return ProcessExecutionResult(
            exit_code=0,
            timed_out=False,
            started_at=now,
            ended_at=now,
            error=None,
        )

    adapter = CodexCliRunnerAdapter(
        config=RuntimeConfig(),
        workspace_root=tmp_path,
        process_executor=fake_execute,
    )
    result = adapter.run(request)

    assert result.exit_kind == "completed"
    assert result.stdout_path is not None
    assert Path(result.stdout_path).is_file()
    assert result.event_log_path is not None
    assert Path(result.event_log_path).is_file()
    assert result.token_usage == TokenUsage(
        input_tokens=120,
        cached_input_tokens=40,
        output_tokens=12,
        thinking_tokens=5,
        total_tokens=132,
    )
    assert Path(result.stdout_path).read_text(encoding="utf-8") == "### BUILDER_COMPLETE\n"

    run_dir = Path(request.run_dir)
    invocation_path = run_dir / "runner_invocation.req-001.json"
    completion_path = run_dir / "runner_completion.req-001.json"
    event_log_path = run_dir / "runner_events.req-001.jsonl"
    assert invocation_path.is_file()
    assert completion_path.is_file()
    assert event_log_path.is_file()

    invocation_payload = json.loads(invocation_path.read_text(encoding="utf-8"))
    completion_payload = json.loads(completion_path.read_text(encoding="utf-8"))
    assert invocation_payload["runner_name"] == "codex_cli"
    assert completion_payload["runner_name"] == "codex_cli"
    assert completion_payload["exit_kind"] == "completed"
    assert completion_payload["event_log_path"] == str(event_log_path)
    assert completion_payload["token_usage"] == {
        "input_tokens": 120,
        "cached_input_tokens": 40,
        "output_tokens": 12,
        "thinking_tokens": 5,
        "total_tokens": 132,
    }


def test_codex_adapter_uses_one_hour_fallback_timeout_when_request_timeout_missing(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path).model_copy(update={"timeout_seconds": 0})
    observed_timeout_seconds: list[int] = []

    def fake_execute(*, command, cwd, env, timeout_seconds, stdout_path, stderr_path):
        del command, cwd, env
        observed_timeout_seconds.append(timeout_seconds)
        Path(stdout_path).write_text("### BUILDER_COMPLETE\n", encoding="utf-8")
        Path(stderr_path).write_text("", encoding="utf-8")
        now = datetime.now(timezone.utc)
        return ProcessExecutionResult(
            exit_code=0,
            timed_out=False,
            started_at=now,
            ended_at=now,
            error=None,
        )

    adapter = CodexCliRunnerAdapter(
        config=RuntimeConfig(),
        workspace_root=tmp_path,
        process_executor=fake_execute,
    )

    adapter.run(request)

    assert observed_timeout_seconds == [3600]


def test_codex_adapter_uses_maximum_permissions_by_default(tmp_path: Path) -> None:
    seen_command: dict[str, tuple[str, ...]] = {}

    def fake_execute(*, command, cwd, env, timeout_seconds, stdout_path, stderr_path):
        del cwd, env, timeout_seconds
        seen_command["value"] = tuple(command)
        Path(stdout_path).write_text("### BUILDER_COMPLETE\n", encoding="utf-8")
        Path(stderr_path).write_text("", encoding="utf-8")
        now = datetime.now(timezone.utc)
        return ProcessExecutionResult(
            exit_code=0,
            timed_out=False,
            started_at=now,
            ended_at=now,
            error=None,
        )

    adapter = CodexCliRunnerAdapter(
        config=RuntimeConfig(),
        workspace_root=tmp_path,
        process_executor=fake_execute,
    )
    adapter.run(_request(tmp_path))

    command = seen_command["value"]
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert "--full-auto" not in command
    assert 'approval_policy="never"' not in command
    assert "--sandbox" not in command
    assert "danger-full-access" not in command


def test_codex_adapter_resolves_permission_precedence_and_command_mapping(
    tmp_path: Path,
) -> None:
    config = RuntimeConfig(
        runners={
            "codex": {
                "permission_default": CodexPermissionLevel.BASIC,
                "permission_by_model": {
                    "gpt-5": CodexPermissionLevel.ELEVATED,
                },
                "permission_by_stage": {
                    "builder": CodexPermissionLevel.MAXIMUM,
                },
            }
        }
    )

    commands: dict[str, tuple[str, ...]] = {}
    terminal_by_request_id = {
        "builder-req": "### BUILDER_COMPLETE\n",
        "checker-req": "### CHECKER_PASS\n",
        "updater-req": "### UPDATE_COMPLETE\n",
    }

    def fake_execute(*, command, cwd, env, timeout_seconds, stdout_path, stderr_path):
        del cwd, env, timeout_seconds
        key = Path(stdout_path).stem.removeprefix("runner_stdout.")
        commands[key] = tuple(command)
        Path(stdout_path).write_text(terminal_by_request_id[key], encoding="utf-8")
        Path(stderr_path).write_text("", encoding="utf-8")
        now = datetime.now(timezone.utc)
        return ProcessExecutionResult(
            exit_code=0,
            timed_out=False,
            started_at=now,
            ended_at=now,
            error=None,
        )

    adapter = CodexCliRunnerAdapter(
        config=config,
        workspace_root=tmp_path,
        process_executor=fake_execute,
    )

    builder_request = _request(
        tmp_path,
        stage=ExecutionStageName.BUILDER,
        model_name="gpt-5",
        request_id="builder-req",
        run_id="builder-run",
    )
    checker_request = _request(
        tmp_path,
        stage=ExecutionStageName.CHECKER,
        model_name="gpt-5",
        request_id="checker-req",
        run_id="checker-run",
    )
    updater_request = _request(
        tmp_path,
        stage=ExecutionStageName.UPDATER,
        model_name="gpt-4",
        request_id="updater-req",
        run_id="updater-run",
    )

    adapter.run(builder_request)
    adapter.run(checker_request)
    adapter.run(updater_request)

    builder_command = commands["builder-req"]
    checker_command = commands["checker-req"]
    updater_command = commands["updater-req"]

    assert "--json" in builder_command
    assert "--dangerously-bypass-approvals-and-sandbox" in builder_command
    assert "--full-auto" not in builder_command
    assert 'approval_policy="never"' not in builder_command
    assert "--sandbox" not in builder_command
    assert "danger-full-access" not in builder_command

    assert "-c" in checker_command
    assert 'approval_policy="never"' in checker_command
    assert "--sandbox" in checker_command
    assert "danger-full-access" in checker_command
    assert "--full-auto" not in checker_command
    assert "--dangerously-bypass-approvals-and-sandbox" not in checker_command

    assert "--full-auto" in updater_command
    assert 'approval_policy="never"' not in updater_command
    assert "--sandbox" not in updater_command
    assert "danger-full-access" not in updater_command
    assert "--dangerously-bypass-approvals-and-sandbox" not in updater_command


@pytest.mark.parametrize(
    ("permission_level", "expected_flag_checks", "unexpected_flag_checks"),
    [
        (
            CodexPermissionLevel.BASIC,
            (("--full-auto", True),),
            (
                ('approval_policy="never"', False),
                ("--sandbox", False),
                ("danger-full-access", False),
                ("--dangerously-bypass-approvals-and-sandbox", False),
            ),
        ),
        (
            CodexPermissionLevel.ELEVATED,
            (
                ("-c", True),
                ('approval_policy="never"', True),
                ("--sandbox", True),
                ("danger-full-access", True),
            ),
            (
                ("--full-auto", False),
                ("--dangerously-bypass-approvals-and-sandbox", False),
            ),
        ),
        (
            CodexPermissionLevel.MAXIMUM,
            (("--dangerously-bypass-approvals-and-sandbox", True),),
            (
                ("--full-auto", False),
                ('approval_policy="never"', False),
                ("--sandbox", False),
                ("danger-full-access", False),
            ),
        ),
    ],
)
def test_codex_adapter_maps_permission_levels_to_flags(
    tmp_path: Path,
    permission_level: CodexPermissionLevel,
    expected_flag_checks: tuple[tuple[str, bool], ...],
    unexpected_flag_checks: tuple[tuple[str, bool], ...],
) -> None:
    config = RuntimeConfig(
        runners={
            "codex": {
                "permission_default": permission_level,
            }
        }
    )

    seen_command: dict[str, tuple[str, ...]] = {}

    def fake_execute(*, command, cwd, env, timeout_seconds, stdout_path, stderr_path):
        del cwd, env, timeout_seconds
        seen_command["value"] = tuple(command)
        Path(stdout_path).write_text("### BUILDER_COMPLETE\n", encoding="utf-8")
        Path(stderr_path).write_text("", encoding="utf-8")
        now = datetime.now(timezone.utc)
        return ProcessExecutionResult(
            exit_code=0,
            timed_out=False,
            started_at=now,
            ended_at=now,
            error=None,
        )

    adapter = CodexCliRunnerAdapter(
        config=config,
        workspace_root=tmp_path,
        process_executor=fake_execute,
    )
    adapter.run(_request(tmp_path))

    command = seen_command["value"]
    for flag, expected in expected_flag_checks:
        assert (flag in command) is expected
    for flag, expected in unexpected_flag_checks:
        assert (flag in command) is expected


def test_codex_adapter_maps_missing_binary_to_runner_error(tmp_path: Path) -> None:
    request = _request(tmp_path)

    def fake_execute(*, command, cwd, env, timeout_seconds, stdout_path, stderr_path):
        del command, cwd, env, timeout_seconds, stdout_path, stderr_path
        raise RunnerBinaryNotFoundError("codex")

    adapter = CodexCliRunnerAdapter(
        config=RuntimeConfig(),
        workspace_root=tmp_path,
        process_executor=fake_execute,
    )
    result = adapter.run(request)

    assert result.exit_kind == "runner_error"
    assert result.exit_code == 127
    assert result.stderr_path is not None
    assert Path(result.stderr_path).is_file()


def test_codex_adapter_maps_transport_error_to_runner_error_even_with_zero_exit_code(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    def fake_execute(*, command, cwd, env, timeout_seconds, stdout_path, stderr_path):
        del command, cwd, env, timeout_seconds
        Path(stdout_path).write_text("### BUILDER_COMPLETE\n", encoding="utf-8")
        Path(stderr_path).write_text("transport warning\n", encoding="utf-8")
        now = datetime.now(timezone.utc)
        return ProcessExecutionResult(
            exit_code=0,
            timed_out=False,
            started_at=now,
            ended_at=now,
            error="transport_error",
        )

    adapter = CodexCliRunnerAdapter(
        config=RuntimeConfig(),
        workspace_root=tmp_path,
        process_executor=fake_execute,
    )
    result = adapter.run(request)

    assert result.exit_kind == "runner_error"
    completion_path = Path(request.run_dir) / f"runner_completion.{request.request_id}.json"
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    assert payload["failure_class"] == "runner_transport_failure"


def test_codex_adapter_prompt_includes_stage_request_context_fields(tmp_path: Path) -> None:
    request = _request(tmp_path).model_copy(
        update={
            "entrypoint_contract_id": "builder.contract.v1",
            "required_skill_paths": (
                "millrace-agents/skills/requesting-code-review/SKILL.md",
            ),
            "attached_skill_paths": (
                "millrace-agents/skills/test-driven-development/SKILL.md",
            ),
            "active_work_item_path": str(tmp_path / "tasks" / "task-001.md"),
        }
    )

    def fake_execute(*, command, cwd, env, timeout_seconds, stdout_path, stderr_path):
        del command, cwd, env, timeout_seconds
        Path(stdout_path).write_text("### BUILDER_COMPLETE\n", encoding="utf-8")
        Path(stderr_path).write_text("", encoding="utf-8")
        now = datetime.now(timezone.utc)
        return ProcessExecutionResult(
            exit_code=0,
            timed_out=False,
            started_at=now,
            ended_at=now,
            error=None,
        )

    adapter = CodexCliRunnerAdapter(
        config=RuntimeConfig(),
        workspace_root=tmp_path,
        process_executor=fake_execute,
    )
    adapter.run(request)

    prompt_path = Path(request.run_dir) / f"runner_prompt.{request.request_id}.md"
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "Stage Request Context:" in prompt
    assert "Entrypoint Contract ID: builder.contract.v1" in prompt
    assert f"Active Work Item Path: {request.active_work_item_path}" in prompt
    assert "Required Skill Paths:" in prompt
    assert "- millrace-agents/skills/requesting-code-review/SKILL.md" in prompt
    assert "Attached Skill Paths:" in prompt
    assert "- millrace-agents/skills/test-driven-development/SKILL.md" in prompt


def test_codex_adapter_prompt_uses_none_for_absent_optional_context(tmp_path: Path) -> None:
    request = _request(tmp_path).model_copy(
        update={
            "entrypoint_contract_id": None,
            "required_skill_paths": (),
            "attached_skill_paths": (),
            "active_work_item_path": None,
            "runner_name": None,
            "model_name": None,
        }
    )

    def fake_execute(*, command, cwd, env, timeout_seconds, stdout_path, stderr_path):
        del command, cwd, env, timeout_seconds
        Path(stdout_path).write_text("### BUILDER_COMPLETE\n", encoding="utf-8")
        Path(stderr_path).write_text("", encoding="utf-8")
        now = datetime.now(timezone.utc)
        return ProcessExecutionResult(
            exit_code=0,
            timed_out=False,
            started_at=now,
            ended_at=now,
            error=None,
        )

    adapter = CodexCliRunnerAdapter(
        config=RuntimeConfig(),
        workspace_root=tmp_path,
        process_executor=fake_execute,
    )
    adapter.run(request)

    prompt_path = Path(request.run_dir) / f"runner_prompt.{request.request_id}.md"
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "Entrypoint Contract ID: none" in prompt
    assert "Active Work Item Path: none" in prompt
    assert "Required Skill Paths: none" in prompt
    assert "Attached Skill Paths: none" in prompt
    assert "Runner Name: none" in prompt
    assert "Model Name: none" in prompt
