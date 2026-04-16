"""Codex CLI runner adapter."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from millrace_ai.config import CodexPermissionLevel, RuntimeConfig
from millrace_ai.runner import (
    RunnerRawResult,
    StageRunRequest,
    render_stage_request_context_lines,
)
from millrace_ai.runners.contracts import (
    completion_artifact_from_raw_result,
    invocation_artifact_from_request,
    write_runner_completion,
    write_runner_invocation,
)
from millrace_ai.runners.errors import RunnerBinaryNotFoundError
from millrace_ai.runners.process import ProcessExecutionResult, run_process


class CodexCliRunnerAdapter:
    """In-process adapter that invokes Codex CLI as the stage executor."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        workspace_root: Path,
        process_executor: Callable[..., ProcessExecutionResult] = run_process,
    ) -> None:
        self.config = config
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.process_executor = process_executor

    @property
    def name(self) -> str:
        return "codex_cli"

    def run(self, request: StageRunRequest) -> RunnerRawResult:
        now = datetime.now(timezone.utc)
        run_dir = Path(request.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = run_dir / f"runner_prompt.{request.request_id}.md"
        stdout_path = run_dir / f"runner_stdout.{request.request_id}.txt"
        stderr_path = run_dir / f"runner_stderr.{request.request_id}.txt"
        output_last_message_path = run_dir / f"runner_last_message.{request.request_id}.txt"
        invocation_path = run_dir / f"runner_invocation.{request.request_id}.json"
        completion_path = run_dir / f"runner_completion.{request.request_id}.json"

        prompt = self._build_prompt(request)
        prompt_path.write_text(prompt, encoding="utf-8")

        command = self._build_command(
            request=request,
            prompt=prompt,
            output_last_message_path=output_last_message_path,
        )
        env = dict(os.environ)
        env.update(self.config.runners.codex.env)

        write_runner_invocation(
            invocation_path,
            invocation_artifact_from_request(
                request=request,
                runner_name=self.name,
                command=command,
                prompt_path=str(prompt_path),
                emitted_at=now,
            ),
        )

        try:
            process_result = self.process_executor(
                command=command,
                cwd=self.workspace_root,
                env=env,
                timeout_seconds=request.timeout_seconds or 300,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
        except RunnerBinaryNotFoundError as exc:
            stderr_path.write_text(f"runner binary not found: {exc}\n", encoding="utf-8")
            result = RunnerRawResult(
                request_id=request.request_id,
                run_id=request.run_id,
                stage=request.stage,
                runner_name=self.name,
                model_name=request.model_name,
                exit_kind="runner_error",
                exit_code=127,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                terminal_result_path=None,
                started_at=now,
                ended_at=datetime.now(timezone.utc),
            )
            write_runner_completion(
                completion_path,
                completion_artifact_from_raw_result(
                    request=request,
                    runner_name=self.name,
                    raw_result=result,
                    command=command,
                    emitted_at=datetime.now(timezone.utc),
                    failure_class="runner_binary_not_found",
                    notes=("runner executable missing",),
                ),
            )
            return result

        exit_kind: Literal[
            "completed",
            "timeout",
            "runner_error",
            "provider_error",
            "interrupted",
        ] = "completed"
        if process_result.timed_out:
            exit_kind = "timeout"
        elif process_result.error is not None:
            exit_kind = "runner_error"
        elif process_result.exit_code != 0:
            exit_kind = "runner_error"

        result = RunnerRawResult(
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            runner_name=self.name,
            model_name=request.model_name,
            exit_kind=exit_kind,
            exit_code=process_result.exit_code,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            terminal_result_path=None,
            started_at=process_result.started_at,
            ended_at=process_result.ended_at,
        )

        failure_class = None
        notes: tuple[str, ...] = ()
        if process_result.timed_out:
            failure_class = "runner_timeout"
            notes = ("runner process exceeded timeout",)
        elif process_result.error is not None:
            failure_class = "runner_transport_failure"
            notes = (process_result.error,)
        elif process_result.exit_code != 0:
            failure_class = "runner_non_zero_exit"
            notes = ("runner exited with non-zero status",)

        write_runner_completion(
            completion_path,
            completion_artifact_from_raw_result(
                request=request,
                runner_name=self.name,
                raw_result=result,
                command=command,
                emitted_at=datetime.now(timezone.utc),
                failure_class=failure_class,
                notes=notes,
            ),
        )
        return result

    def _build_command(
        self,
        *,
        request: StageRunRequest,
        prompt: str,
        output_last_message_path: Path,
    ) -> tuple[str, ...]:
        codex = self.config.runners.codex
        command: list[str] = [codex.command, *codex.args]

        if codex.profile is not None:
            command.extend(["--profile", codex.profile])
        if codex.skip_git_repo_check:
            command.append("--skip-git-repo-check")
        if request.model_name is not None:
            command.extend(["--model", request.model_name])

        permission_level = self._resolve_permission_level(request)
        command.extend(self._permission_flags(permission_level))

        for item in codex.extra_config:
            command.extend(["-c", item])

        command.extend(["--cd", str(self.workspace_root)])
        command.extend(["--output-last-message", str(output_last_message_path)])
        command.append(prompt)
        return tuple(command)

    def _resolve_permission_level(self, request: StageRunRequest) -> CodexPermissionLevel:
        codex = self.config.runners.codex

        stage_override = codex.permission_by_stage.get(request.stage.value)
        if stage_override is not None:
            return stage_override

        if request.model_name is not None:
            model_override = codex.permission_by_model.get(request.model_name)
            if model_override is not None:
                return model_override

        return codex.permission_default

    def _permission_flags(self, level: CodexPermissionLevel) -> tuple[str, ...]:
        if level is CodexPermissionLevel.BASIC:
            return ("--full-auto",)
        if level is CodexPermissionLevel.ELEVATED:
            return ("-c", 'approval_policy="never"', "--sandbox", "danger-full-access")
        return ("--dangerously-bypass-approvals-and-sandbox",)

    def _build_prompt(self, request: StageRunRequest) -> str:
        request_context = render_stage_request_context_lines(request)
        return "\n".join(
            (
                "You are executing one Millrace runtime stage request.",
                f"Open `{request.entrypoint_path}` and follow instructions exactly.",
                "",
                "Stage Request Context:",
                *request_context,
                "",
                "When done, print exactly one terminal marker line in format `### TOKEN`.",
                "Do not print multiple terminal markers.",
            )
        )


__all__ = ["CodexCliRunnerAdapter"]
