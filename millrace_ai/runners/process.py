"""Process execution helpers shared by runner adapters."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.runners.errors import RunnerBinaryNotFoundError


@dataclass(frozen=True, slots=True)
class ProcessExecutionResult:
    exit_code: int
    timed_out: bool
    started_at: datetime
    ended_at: datetime
    error: str | None = None


def run_process(
    *,
    command: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    stdout_path: Path,
    stderr_path: Path,
) -> ProcessExecutionResult:
    started_at = datetime.now(timezone.utc)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
            "w",
            encoding="utf-8",
        ) as stderr_file:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                timeout=max(1, timeout_seconds),
                check=False,
            )
        return ProcessExecutionResult(
            exit_code=completed.returncode,
            timed_out=False,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            error=None,
        )
    except FileNotFoundError as exc:
        raise RunnerBinaryNotFoundError(command[0]) from exc
    except subprocess.TimeoutExpired:
        stderr_path.write_text(
            f"runner timed out after {timeout_seconds} seconds\n",
            encoding="utf-8",
        )
        return ProcessExecutionResult(
            exit_code=124,
            timed_out=True,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            error="timeout",
        )
    except OSError as exc:
        stderr_path.write_text(f"runner process error: {exc}\n", encoding="utf-8")
        return ProcessExecutionResult(
            exit_code=1,
            timed_out=False,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            error=str(exc),
        )


__all__ = [
    "ProcessExecutionResult",
    "run_process",
]
