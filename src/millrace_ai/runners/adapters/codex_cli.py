"""Codex CLI runner adapter."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from millrace_ai.config import CodexPermissionLevel, RuntimeConfig
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    PlanningStageName,
    PlanningTerminalResult,
    TokenUsage,
)
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
    render_stage_request_context_lines,
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

        prompt_path = run_dir / f"runner_prompt.{request.request_id}.md"
        stdout_path = run_dir / f"runner_stdout.{request.request_id}.txt"
        stderr_path = run_dir / f"runner_stderr.{request.request_id}.txt"
        event_log_path = run_dir / f"runner_events.{request.request_id}.jsonl"
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
                timeout_seconds=request.timeout_seconds or 3600,
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

        persisted_event_log_path = _persist_event_log(stdout_path, event_log_path)
        token_usage = _extract_token_usage(persisted_event_log_path)
        materialized_stdout_path = _materialize_stdout_artifact(
            stdout_path=stdout_path,
            output_last_message_path=output_last_message_path,
            event_log_path=persisted_event_log_path,
        )

        reconciled_timeout_marker = _reconciled_timeout_terminal_marker(
            request.stage,
            output_last_message_path=output_last_message_path,
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
            exit_kind=canonical_exit_kind,
            exit_code=canonical_exit_code,
            observed_exit_kind=observed_exit_kind,
            observed_exit_code=observed_exit_code,
            stdout_path=str(materialized_stdout_path) if materialized_stdout_path else None,
            stderr_path=str(stderr_path),
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
        command.append("--json")
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
        legal_markers = ", ".join(f"`### {marker}`" for marker in _legal_terminal_markers(request.stage))
        return "\n".join(
            (
                "You are executing one Millrace runtime stage request.",
                f"Open `{request.entrypoint_path}` and follow instructions exactly.",
                "",
                "Stage Request Context:",
                *request_context,
                "",
                (
                    "When done, print exactly one legal terminal marker defined by the opened "
                    "entrypoint contract."
                ),
                f"Legal markers for this stage: {legal_markers}.",
                "Do not invent or rename terminal markers.",
                "Do not print multiple terminal markers.",
            )
        )


def _legal_terminal_markers(stage: ExecutionStageName | PlanningStageName) -> tuple[str, ...]:
    if stage is ExecutionStageName.BUILDER:
        return (
            ExecutionTerminalResult.BUILDER_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.CHECKER:
        return (
            ExecutionTerminalResult.CHECKER_PASS.value,
            ExecutionTerminalResult.FIX_NEEDED.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.FIXER:
        return (
            ExecutionTerminalResult.FIXER_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.DOUBLECHECKER:
        return (
            ExecutionTerminalResult.DOUBLECHECK_PASS.value,
            ExecutionTerminalResult.FIX_NEEDED.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.UPDATER:
        return (
            ExecutionTerminalResult.UPDATE_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.TROUBLESHOOTER:
        return (
            ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is ExecutionStageName.CONSULTANT:
        return (
            ExecutionTerminalResult.CONSULT_COMPLETE.value,
            ExecutionTerminalResult.NEEDS_PLANNING.value,
            ExecutionTerminalResult.BLOCKED.value,
        )
    if stage is PlanningStageName.PLANNER:
        return (
            PlanningTerminalResult.PLANNER_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        )
    if stage is PlanningStageName.MANAGER:
        return (
            PlanningTerminalResult.MANAGER_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        )
    if stage is PlanningStageName.MECHANIC:
        return (
            PlanningTerminalResult.MECHANIC_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        )
    if stage is PlanningStageName.AUDITOR:
        return (
            PlanningTerminalResult.AUDITOR_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        )
    return (
        PlanningTerminalResult.ARBITER_COMPLETE.value,
        PlanningTerminalResult.REMEDIATION_NEEDED.value,
        PlanningTerminalResult.BLOCKED.value,
    )


def _persist_event_log(stdout_path: Path, event_log_path: Path) -> Path | None:
    if not stdout_path.exists():
        return None
    event_log_path.write_bytes(stdout_path.read_bytes())
    stdout_path.unlink()
    return event_log_path


def _materialize_stdout_artifact(
    *,
    stdout_path: Path,
    output_last_message_path: Path,
    event_log_path: Path | None,
) -> Path | None:
    if output_last_message_path.exists():
        stdout_path.write_text(output_last_message_path.read_text(encoding="utf-8"), encoding="utf-8")
        return stdout_path
    if event_log_path is not None and event_log_path.exists():
        stdout_path.write_text(event_log_path.read_text(encoding="utf-8"), encoding="utf-8")
        return stdout_path
    return None


_TERMINAL_MARKER_PATTERN = re.compile(r"^###\s+([A-Z_]+)\s*$")


def _reconciled_timeout_terminal_marker(
    stage: ExecutionStageName | PlanningStageName,
    *,
    output_last_message_path: Path,
) -> str | None:
    if not output_last_message_path.exists():
        return None

    try:
        lines = output_last_message_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    stripped_nonempty = [line.strip() for line in lines if line.strip()]
    if not stripped_nonempty:
        return None

    legal_markers = set(_legal_terminal_markers(stage))
    observed_markers: list[str] = []
    for line in lines:
        match = _TERMINAL_MARKER_PATTERN.match(line.strip())
        if match is None:
            continue
        marker = match.group(1)
        if marker in legal_markers:
            observed_markers.append(marker)

    if len(observed_markers) != 1:
        return None

    marker = observed_markers[0]
    if stripped_nonempty[-1] != f"### {marker}":
        return None

    return marker


def _extract_token_usage(event_log_path: Path | None) -> TokenUsage | None:
    if event_log_path is None or not event_log_path.exists():
        return None

    best: TokenUsage | None = None
    try:
        lines = event_log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        candidate = _token_usage_from_line(line)
        if candidate is None:
            continue
        if best is None or candidate.total_tokens >= best.total_tokens:
            best = candidate
    return best


def _token_usage_from_line(line: str) -> TokenUsage | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return _token_usage_from_payload(payload)


def _token_usage_from_payload(payload: object) -> TokenUsage | None:
    if not isinstance(payload, dict):
        return None

    payload_type = payload.get("type")
    nested_payload = payload.get("payload")
    if payload_type == "event_msg" and isinstance(nested_payload, dict):
        return _token_usage_from_payload(nested_payload)

    if payload_type != "token_count":
        return None

    info = payload.get("info")
    if not isinstance(info, dict):
        return None

    usage_payload = info.get("total_token_usage")
    if not isinstance(usage_payload, dict):
        usage_payload = info.get("last_token_usage")
    if not isinstance(usage_payload, dict):
        return None
    return _token_usage_from_dict(usage_payload)


def _token_usage_from_dict(payload: dict[str, object]) -> TokenUsage | None:
    input_tokens = _int_from_payload(payload, "input_tokens")
    output_tokens = _int_from_payload(payload, "output_tokens")
    if input_tokens is None or output_tokens is None:
        return None

    cached_input_tokens = _int_from_payload(payload, "cached_input_tokens", default=0) or 0
    thinking_tokens = (
        _int_from_payload(
            payload,
            "reasoning_output_tokens",
            "thinking_tokens",
            "reasoning_tokens",
            default=0,
        )
        or 0
    )
    total_tokens = _int_from_payload(payload, "total_tokens", default=input_tokens + output_tokens)
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens

    return TokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        total_tokens=total_tokens,
    )


def _int_from_payload(
    payload: dict[str, object],
    *keys: str,
    default: int | None = None,
) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return default
    return default


__all__ = ["CodexCliRunnerAdapter"]
