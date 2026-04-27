"""Pi RPC runner adapter."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from millrace_ai.config import PiEventLogPolicy, RuntimeConfig
from millrace_ai.runners.adapters._prompting import build_stage_prompt
from millrace_ai.runners.adapters.pi_rpc_client import PiRpcClient, PiRpcSessionResult
from millrace_ai.runners.contracts import (
    completion_artifact_from_raw_result,
    invocation_artifact_from_request,
    write_runner_completion,
    write_runner_invocation,
)
from millrace_ai.runners.errors import RunnerBinaryNotFoundError
from millrace_ai.runners.requests import RunnerRawResult, StageRunRequest


class PiRpcRunnerAdapter:
    """In-process adapter that invokes Pi RPC as the stage executor."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        workspace_root: Path,
        client_factory: Callable[..., PiRpcClient] = PiRpcClient,
    ) -> None:
        self.config = config
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.client_factory = client_factory

    @property
    def name(self) -> str:
        return "pi_rpc"

    def run(self, request: StageRunRequest) -> RunnerRawResult:
        now = datetime.now(timezone.utc)
        run_dir = Path(request.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = run_dir / f"runner_prompt.{request.request_id}.md"
        stdout_path = run_dir / f"runner_stdout.{request.request_id}.txt"
        stderr_path = run_dir / f"runner_stderr.{request.request_id}.txt"
        event_log_path = run_dir / f"runner_events.{request.request_id}.jsonl"
        invocation_path = run_dir / f"runner_invocation.{request.request_id}.json"
        completion_path = run_dir / f"runner_completion.{request.request_id}.json"

        prompt = build_stage_prompt(request)
        prompt_path.write_text(prompt, encoding="utf-8")

        command = self._build_command(request=request)
        env = dict(os.environ)
        env.update(self.config.runners.pi.env)

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
            client = self.client_factory(
                command=command,
                cwd=self.workspace_root,
                env=env,
            )
            session_result = client.run_prompt(
                prompt=prompt,
                timeout_seconds=request.timeout_seconds or 3600,
            )
        except RunnerBinaryNotFoundError as exc:
            stderr_path.write_text(f"runner binary not found: {exc}\n", encoding="utf-8")
            result = RunnerRawResult(
                request_id=request.request_id,
                run_id=request.run_id,
                stage=request.stage,
                runner_name=self.name,
                model_name=request.model_name,
                model_reasoning_effort=request.model_reasoning_effort,
                exit_kind="runner_error",
                exit_code=127,
                stdout_path=None,
                stderr_path=str(stderr_path),
                terminal_result_path=None,
                event_log_path=None,
                token_usage=None,
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
        except (OSError, ValueError) as exc:
            stderr_path.write_text(f"runner process error: {exc}\n", encoding="utf-8")
            result = RunnerRawResult(
                request_id=request.request_id,
                run_id=request.run_id,
                stage=request.stage,
                runner_name=self.name,
                model_name=request.model_name,
                model_reasoning_effort=request.model_reasoning_effort,
                exit_kind="runner_error",
                exit_code=1,
                stdout_path=None,
                stderr_path=str(stderr_path),
                terminal_result_path=None,
                event_log_path=None,
                token_usage=None,
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
                    failure_class="runner_transport_failure",
                    notes=(str(exc),),
                ),
            )
            return result

        persisted_event_lines = self._persistable_event_lines(session_result.event_lines)
        if self._should_persist_event_log(
            session_result=session_result,
            persisted_event_lines=persisted_event_lines,
        ):
            event_log_path.write_text(
                "\n".join(persisted_event_lines) + "\n",
                encoding="utf-8",
            )
        if session_result.assistant_text is not None:
            stdout_path.write_text(session_result.assistant_text, encoding="utf-8")
        stderr_path.write_text(session_result.stderr_text, encoding="utf-8")

        result = RunnerRawResult(
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            runner_name=self.name,
            model_name=request.model_name,
            model_reasoning_effort=request.model_reasoning_effort,
            exit_kind=session_result.exit_kind,
            exit_code=session_result.exit_code,
            observed_exit_kind=session_result.observed_exit_kind,
            observed_exit_code=session_result.observed_exit_code,
            stdout_path=str(stdout_path) if stdout_path.exists() else None,
            stderr_path=str(stderr_path),
            terminal_result_path=None,
            event_log_path=str(event_log_path) if event_log_path.exists() else None,
            token_usage=session_result.token_usage,
            started_at=session_result.started_at,
            ended_at=session_result.ended_at,
        )

        write_runner_completion(
            completion_path,
            completion_artifact_from_raw_result(
                request=request,
                runner_name=self.name,
                raw_result=result,
                command=command,
                emitted_at=datetime.now(timezone.utc),
                failure_class=session_result.failure_class,
                notes=session_result.notes,
            ),
        )
        return result

    def _build_command(self, *, request: StageRunRequest) -> tuple[str, ...]:
        pi = self.config.runners.pi
        command: list[str] = [pi.command, *pi.args, "--mode", "rpc", "--no-session"]

        if pi.provider is not None:
            command.extend(["--provider", pi.provider])
        if request.model_name is not None:
            command.extend(["--model", request.model_name])
        if pi.thinking is not None:
            command.extend(["--thinking", pi.thinking])
        if pi.disable_context_files:
            command.append("--no-context-files")
        if pi.disable_skills:
            command.append("--no-skills")

        return tuple(command)

    def _should_persist_event_log(
        self,
        *,
        session_result: PiRpcSessionResult,
        persisted_event_lines: tuple[str, ...],
    ) -> bool:
        if not persisted_event_lines:
            return False

        policy = self.config.runners.pi.event_log_policy
        if policy is PiEventLogPolicy.FULL:
            return True
        return session_result.exit_kind != "completed"

    def _persistable_event_lines(self, event_lines: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(line for line in event_lines if not _is_message_update_event(line))


def _is_message_update_event(raw_line: str) -> bool:
    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("type") == "message_update"


__all__ = ["PiRpcRunnerAdapter", "PiRpcSessionResult"]
