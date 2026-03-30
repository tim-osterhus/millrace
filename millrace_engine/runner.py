"""Runner abstraction and artifact capture."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
import os
import re
import signal
import subprocess
import time

from .contracts import CodexUsageSummary, RunnerKind, RunnerResult, StageContext, StageType
from .diagnostics import allocate_run_directory, build_stage_artifact_paths
from .markdown import write_text_atomic
from .paths import RuntimePaths
from .telemetry import extract_codex_exec_usage, format_usage_summary


MARKER_RE = re.compile(r"^###\s+([A-Z0-9_]+)\s*$", re.MULTILINE)

STAGE_LABELS: dict[StageType, str] = {
    StageType.BUILDER: "Builder",
    StageType.INTEGRATION: "Integration",
    StageType.QA: "QA",
    StageType.HOTFIX: "Hotfix",
    StageType.DOUBLECHECK: "Doublecheck",
    StageType.TROUBLESHOOT: "Troubleshoot",
    StageType.CONSULT: "Consult",
    StageType.UPDATE: "Update",
    StageType.GOAL_INTAKE: "GoalIntake",
    StageType.OBJECTIVE_PROFILE_SYNC: "ObjectiveProfileSync",
    StageType.SPEC_SYNTHESIS: "SpecSynthesis",
    StageType.SPEC_REVIEW: "SpecReview",
    StageType.TASKMASTER: "Taskmaster",
    StageType.TASKAUDIT: "Taskaudit",
    StageType.CLARIFY: "Clarify",
    StageType.CRITIC: "Critic",
    StageType.DESIGNER: "Designer",
    StageType.PHASESPLIT: "Phasesplit",
    StageType.INCIDENT_INTAKE: "IncidentIntake",
    StageType.INCIDENT_RESOLVE: "IncidentResolve",
    StageType.INCIDENT_ARCHIVE: "IncidentArchive",
    StageType.AUDIT_INTAKE: "AuditIntake",
    StageType.AUDIT_VALIDATE: "AuditValidate",
    StageType.AUDIT_GATEKEEPER: "AuditGatekeeper",
    StageType.MECHANIC: "Mechanic",
}


def detect_last_marker(text: str) -> tuple[str | None, str | None]:
    """Extract the last valid `### MARKER` line from free text."""

    matches = list(MARKER_RE.finditer(text))
    if not matches:
        return None, None
    match = matches[-1]
    return match.group(1), match.group(0)


def _display_path(root: Path, path: Path | None) -> str:
    if path is None:
        return "n/a"
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _stage_label(stage: StageType) -> str:
    return STAGE_LABELS.get(stage, stage.value.replace("_", " ").title().replace(" ", ""))


def _format_stage_result_summary(result: RunnerResult, root: Path) -> str:
    marker = f"### {result.detected_marker}" if result.detected_marker else "missing"
    return (
        f"Stage result: stage={_stage_label(result.stage)} runner={result.runner.value} model={result.model} "
        f"exit={result.exit_code} marker={marker} "
        f"stdout={_display_path(root, result.stdout_path)} "
        f"stderr={_display_path(root, result.stderr_path)} "
        f"last={_display_path(root, result.last_response_path)}"
    )


def _append_runner_notes(runner_notes_path: Path, run_id: str, lines: Sequence[str]) -> None:
    existing = runner_notes_path.read_text(encoding="utf-8") if runner_notes_path.exists() else ""
    rendered_lines: list[str] = []
    if not existing.strip():
        rendered_lines.append(f"Run: {run_id}")
    rendered_lines.extend(line.rstrip("\n") for line in lines)
    appended = "\n".join(rendered_lines).rstrip("\n") + "\n"
    if existing.strip():
        appended = existing.rstrip("\n") + "\n" + appended
    write_text_atomic(runner_notes_path, appended)


class BaseRunner:
    """Subprocess-backed stage runner base class."""

    runner_kind: RunnerKind
    emits_jsonl_stdout: bool = False

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths

    def build_command(self, context: StageContext, last_response_path: Path) -> tuple[str, ...]:
        """Return the command used to invoke the runner."""

        raise NotImplementedError

    def telemetry(self, context: StageContext, stdout_path: Path) -> CodexUsageSummary | None:
        """Return optional runner-specific telemetry."""

        return None

    def _base_environment(self, context: StageContext, run_dir: Path, stdout_path: Path, stderr_path: Path, last_response_path: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(context.env)
        env.update(
            {
                "MILLRACE_PROMPT": context.prompt,
                "MILLRACE_STAGE": context.stage.value,
                "MILLRACE_MODEL": context.model,
                "MILLRACE_RUN_DIR": str(run_dir),
                "MILLRACE_STDOUT_PATH": str(stdout_path),
                "MILLRACE_STDERR_PATH": str(stderr_path),
                "MILLRACE_LAST_RESPONSE_PATH": str(last_response_path),
                "MILLRACE_ALLOW_SEARCH": "1" if context.allow_search else "0",
                "MILLRACE_ALLOW_NETWORK": "1" if context.allow_network else "0",
                "MILLRACE_REASONING_EFFORT": context.effort.value if context.effort else "",
            }
        )
        return env

    def execute(self, context: StageContext) -> RunnerResult:
        """Invoke the stage runner and capture normalized artifacts."""

        if context.runner is not self.runner_kind:
            raise ValueError(
                f"{self.__class__.__name__} cannot execute runner kind {context.runner.value}"
            )

        run_dir = allocate_run_directory(self.paths, stage=context.stage, run_id=context.run_id)
        stdout_path, stderr_path, last_response_path, runner_notes_path = build_stage_artifact_paths(
            run_dir,
            context.stage,
        )
        command = self.build_command(context, last_response_path)
        if not command:
            raise ValueError("runner command may not be empty")

        env = self._base_environment(context, run_dir, stdout_path, stderr_path, last_response_path)
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        write_text_atomic(stdout_path, "")
        write_text_atomic(stderr_path, "")
        exit_code = 127

        try:
            with (
                stdout_path.open("w", encoding="utf-8") as stdout_handle,
                stderr_path.open("w", encoding="utf-8") as stderr_handle,
            ):
                process = subprocess.Popen(
                    command,
                    cwd=context.working_dir,
                    env=env,
                    stdin=subprocess.PIPE if context.prompt_to_stdin else subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    start_new_session=os.name != "nt",
                )
                try:
                    process.communicate(
                        input=context.prompt if context.prompt_to_stdin else None,
                        timeout=context.timeout_seconds,
                    )
                    exit_code = process.returncode
                except subprocess.TimeoutExpired:
                    self._terminate_process(process)
                    timeout_note = f"RUNNER_TIMEOUT after {context.timeout_seconds}s"
                    existing_stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
                    if existing_stderr and not existing_stderr.endswith("\n"):
                        existing_stderr += "\n"
                    write_text_atomic(stderr_path, existing_stderr + timeout_note + "\n")
                    exit_code = 124
        except OSError as exc:
            write_text_atomic(stderr_path, f"{exc}\n")

        duration_seconds = time.monotonic() - started_monotonic
        completed_at = datetime.now(timezone.utc)

        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")

        if not last_response_path.exists() and stdout and not self.emits_jsonl_stdout:
            write_text_atomic(last_response_path, stdout)

        last_response_text = (
            last_response_path.read_text(encoding="utf-8", errors="replace")
            if last_response_path.exists()
            else ""
        )
        detected_marker, raw_marker_line = detect_last_marker(stdout)
        if detected_marker is None and last_response_text:
            detected_marker, raw_marker_line = detect_last_marker(last_response_text)

        usage_summary = self.telemetry(context, stdout_path)
        if usage_summary is not None:
            usage_summary = usage_summary.model_copy(
                update={"source": Path(_display_path(self.paths.root, usage_summary.source))}
            )

        result = RunnerResult.model_validate(
            {
                "stage": context.stage,
                "runner": context.runner,
                "model": context.model,
                "command": command,
                "exit_code": exit_code,
                "duration_seconds": duration_seconds,
                "stdout": stdout,
                "stderr": stderr,
                "detected_marker": detected_marker,
                "raw_marker_line": raw_marker_line,
                "stdout_path": stdout_path,
                "stderr_path": stderr_path,
                "last_response_path": last_response_path if last_response_path.exists() else None,
                "runner_notes_path": runner_notes_path,
                "run_dir": run_dir,
                "started_at": started_at,
                "completed_at": completed_at,
                "usage_summary": usage_summary,
            }
        )

        note_lines = [_format_stage_result_summary(result, self.paths.root)]
        if usage_summary is not None:
            note_lines.append(format_usage_summary(usage_summary))
        _append_runner_notes(runner_notes_path, run_dir.name, note_lines)

        return result

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=5)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass

        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass


class SubprocessRunner(BaseRunner):
    """Generic runner for deterministic local subprocess fixtures."""

    runner_kind = RunnerKind.SUBPROCESS

    def build_command(self, context: StageContext, last_response_path: Path) -> tuple[str, ...]:
        del last_response_path
        if not context.command:
            raise ValueError("SubprocessRunner requires an explicit command")
        return context.command


class CodexRunner(BaseRunner):
    """Codex exec wrapper with JSONL telemetry extraction."""

    runner_kind = RunnerKind.CODEX
    emits_jsonl_stdout = True

    def build_command(self, context: StageContext, last_response_path: Path) -> tuple[str, ...]:
        if context.command:
            return context.command
        command: list[str] = ["codex"]
        if context.allow_search:
            command.append("--search")
        command.extend(
            [
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--model",
                context.model,
                "--full-auto",
            ]
        )
        if context.effort is not None:
            command.extend(["-c", f'model_reasoning_effort="{context.effort.value}"'])
        command.extend(["-o", str(last_response_path), context.prompt])
        return tuple(command)

    def telemetry(self, context: StageContext, stdout_path: Path) -> CodexUsageSummary | None:
        return extract_codex_exec_usage(
            stdout_path,
            loop="execution",
            stage=_stage_label(context.stage),
            model=context.model,
            runner=self.runner_kind.value,
        )


class ClaudeRunner(BaseRunner):
    """Claude subprocess wrapper."""

    runner_kind = RunnerKind.CLAUDE

    def build_command(self, context: StageContext, last_response_path: Path) -> tuple[str, ...]:
        del last_response_path
        if context.command:
            return context.command
        return ("claude", context.prompt)
