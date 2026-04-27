"""Codex CLI runner adapter."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from millrace_ai.config import RuntimeConfig
from millrace_ai.runners.adapters._prompting import build_stage_prompt
from millrace_ai.runners.adapters.codex_cli_artifacts import (
    codex_cli_artifact_paths,
    materialize_stdout_artifact,
    persist_event_log,
    reconciled_timeout_terminal_marker,
)
from millrace_ai.runners.adapters.codex_cli_command import build_codex_cli_command
from millrace_ai.runners.adapters.codex_cli_tokens import extract_token_usage
from millrace_ai.runners.contracts import (
    completion_artifact_from_raw_result,
    invocation_artifact_from_request,
    write_runner_completion,
    write_runner_invocation,
)
from millrace_ai.runners.errors import RunnerBinaryNotFoundError
from millrace_ai.runners.process import ProcessExecutionResult, run_process
from millrace_ai.runners.requests import (
    RunnerRawResult,
    StageRunRequest,
)


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
        artifact_paths = codex_cli_artifact_paths(run_dir, request.request_id)

        prompt = self._build_prompt(request)
        artifact_paths.prompt_path.write_text(prompt, encoding="utf-8")

        command = build_codex_cli_command(
            config=self.config,
            workspace_root=self.workspace_root,
            request=request,
            prompt=prompt,
            output_last_message_path=artifact_paths.output_last_message_path,
        )
        env = dict(os.environ)
        env.update(self.config.runners.codex.env)

        write_runner_invocation(
            artifact_paths.invocation_path,
            invocation_artifact_from_request(
                request=request,
                runner_name=self.name,
                command=command,
                prompt_path=str(artifact_paths.prompt_path),
                emitted_at=now,
            ),
        )

        try:
            process_result = self.process_executor(
                command=command,
                cwd=self.workspace_root,
                env=env,
                timeout_seconds=request.timeout_seconds or 3600,
                stdout_path=artifact_paths.stdout_path,
                stderr_path=artifact_paths.stderr_path,
            )
        except RunnerBinaryNotFoundError as exc:
            artifact_paths.stderr_path.write_text(
                f"runner binary not found: {exc}\n",
                encoding="utf-8",
            )
            result = RunnerRawResult(
                request_id=request.request_id,
                run_id=request.run_id,
                stage=request.stage,
                runner_name=self.name,
                model_name=request.model_name,
                model_reasoning_effort=request.model_reasoning_effort,
                exit_kind="runner_error",
                exit_code=127,
                stdout_path=str(artifact_paths.stdout_path),
                stderr_path=str(artifact_paths.stderr_path),
                terminal_result_path=None,
                event_log_path=None,
                token_usage=None,
                started_at=now,
                ended_at=datetime.now(timezone.utc),
            )
            write_runner_completion(
                artifact_paths.completion_path,
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

        persisted_event_log_path = persist_event_log(
            artifact_paths.stdout_path,
            artifact_paths.event_log_path,
        )
        token_usage = extract_token_usage(persisted_event_log_path)
        materialized_stdout_path = materialize_stdout_artifact(
            stdout_path=artifact_paths.stdout_path,
            output_last_message_path=artifact_paths.output_last_message_path,
            event_log_path=persisted_event_log_path,
        )

        reconciled_timeout_marker = reconciled_timeout_terminal_marker(
            request,
            output_last_message_path=artifact_paths.output_last_message_path,
        )
        observed_exit_kind = exit_kind if reconciled_timeout_marker is not None else None
        observed_exit_code = (
            process_result.exit_code if reconciled_timeout_marker is not None else None
        )
        canonical_exit_kind = "completed" if reconciled_timeout_marker is not None else exit_kind
        canonical_exit_code = 0 if reconciled_timeout_marker is not None else process_result.exit_code

        result = RunnerRawResult(
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            runner_name=self.name,
            model_name=request.model_name,
            model_reasoning_effort=request.model_reasoning_effort,
            exit_kind=canonical_exit_kind,
            exit_code=canonical_exit_code,
            observed_exit_kind=observed_exit_kind,
            observed_exit_code=observed_exit_code,
            stdout_path=str(materialized_stdout_path) if materialized_stdout_path else None,
            stderr_path=str(artifact_paths.stderr_path),
            terminal_result_path=None,
            event_log_path=(
                str(persisted_event_log_path) if persisted_event_log_path is not None else None
            ),
            token_usage=token_usage,
            started_at=process_result.started_at,
            ended_at=process_result.ended_at,
        )

        failure_class = None
        notes: tuple[str, ...] = ()
        if reconciled_timeout_marker is not None:
            notes = (
                "runner timeout reconciled after final terminal marker "
                f"### {reconciled_timeout_marker}",
            )
        elif process_result.timed_out:
            failure_class = "runner_timeout"
            notes = ("runner process exceeded timeout",)
        elif process_result.error is not None:
            failure_class = "runner_transport_failure"
            notes = (process_result.error,)
        elif process_result.exit_code != 0:
            failure_class = "runner_non_zero_exit"
            notes = ("runner exited with non-zero status",)

        write_runner_completion(
            artifact_paths.completion_path,
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

    def _build_prompt(self, request: StageRunRequest) -> str:
        return build_stage_prompt(request)

__all__ = ["CodexCliRunnerAdapter"]
