"""Synchronous owned command lifecycle with bounded multiplexed pipe reads."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..process import ProcessRequest, run_owned_process
from ..records import CommandResult


def _diagnostics(
    raw: bytearray, env: dict[str, str], truncated: bool
) -> tuple[bytes, bool]:
    retained = bytes(raw)
    for name, value in env.items():
        if name != "PATH" and value:
            redacted = retained.replace(value.encode(), b"[redacted]")
            truncated = truncated or len(redacted) > 65536
            retained = redacted[:65536]
    return retained, truncated


def invoke(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    deadline: float,
    cancelled: Callable[[], bool],
    on_started: Callable[[int], None],
) -> CommandResult:
    """Invoke foreground work; a saved PID is never accepted for restart cleanup."""
    try:
        request = ProcessRequest(
            tuple(argv), cwd, env, None, deadline,
            stdin_limit=1,
            stdout_capture_limit=65536,
            stderr_capture_limit=65536,
            stdout_fail_limit=65536,
            stderr_fail_limit=None,
        )
    except ValueError:
        return CommandResult(
            b"", b"", "execution_failure", "stopped", "invalid_process_request"
        )
    result = run_owned_process(
        request,
        poll=cancelled,
        on_started=on_started,
    )
    classification = {
        "cancelled": "cancelled",
        "timeout": "timeout",
        "stdout_overflow": "protocol_failure",
    }.get(result.stop_cause or "", "execution_failure")
    if result.stop_cause is None:
        classification = "completed" if result.exit_code == 0 else "execution_failure"
    if (
        result.cleanup_confirmed and result.stdout_overflow
        and result.stop_cause != "cancelled"
    ):
        classification = "protocol_failure"
    stderr, truncated = _diagnostics(
        bytearray(result.stderr), env, result.stderr_truncated
    )
    if result.pid is None:
        diagnostic = (
            "deadline_before_launch" if result.stop_cause == "timeout"
            else result.stop_cause or "start_failed"
        )
    elif not result.cleanup_confirmed:
        diagnostic = "cleanup_unconfirmed" + (",stderr_truncated" if truncated else "")
    else:
        diagnostic = ",".join(
            name for name, present in (
                ("stdout_truncated", result.stdout_truncated),
                ("stderr_truncated", truncated),
            ) if present
        )
    return CommandResult(
        result.stdout, stderr, classification,
        "stopped" if result.cleanup_confirmed else "may_live",
        diagnostic, result.exit_code,
    )
