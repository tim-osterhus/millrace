"""Codex CLI command construction."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.config import CodexPermissionLevel, RuntimeConfig
from millrace_ai.runners.requests import StageRunRequest


def build_codex_cli_command(
    *,
    config: RuntimeConfig,
    workspace_root: Path,
    request: StageRunRequest,
    prompt: str,
    output_last_message_path: Path,
) -> tuple[str, ...]:
    codex = config.runners.codex
    command: list[str] = [codex.command, *codex.args]

    if codex.profile is not None:
        command.extend(["--profile", codex.profile])
    if codex.skip_git_repo_check:
        command.append("--skip-git-repo-check")
    if request.model_name is not None:
        command.extend(["--model", request.model_name])

    permission_level = resolve_permission_level(config=config, request=request)
    command.extend(permission_flags(permission_level))

    for item in codex.extra_config:
        command.extend(["-c", item])

    model_reasoning_effort = request.model_reasoning_effort
    if model_reasoning_effort is None and codex.model_reasoning_effort is not None:
        model_reasoning_effort = codex.model_reasoning_effort.value
    if model_reasoning_effort is not None:
        command.extend(["-c", f'model_reasoning_effort="{model_reasoning_effort}"'])

    command.extend(["--cd", str(workspace_root)])
    command.append("--json")
    command.extend(["--output-last-message", str(output_last_message_path)])
    command.append(prompt)
    return tuple(command)


def resolve_permission_level(
    *,
    config: RuntimeConfig,
    request: StageRunRequest,
) -> CodexPermissionLevel:
    codex = config.runners.codex

    stage_override = codex.permission_by_stage.get(request.stage.value)
    if stage_override is not None:
        return stage_override

    if request.model_name is not None:
        model_override = codex.permission_by_model.get(request.model_name)
        if model_override is not None:
            return model_override

    return codex.permission_default


def permission_flags(level: CodexPermissionLevel) -> tuple[str, ...]:
    if level is CodexPermissionLevel.BASIC:
        return ("--full-auto",)
    if level is CodexPermissionLevel.ELEVATED:
        return ("-c", 'approval_policy="never"', "--sandbox", "danger-full-access")
    return ("--dangerously-bypass-approvals-and-sandbox",)


__all__ = ["build_codex_cli_command", "permission_flags", "resolve_permission_level"]
