"""Synchronous owned command lifecycle with bounded multiplexed pipe reads."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ..records import CommandResult


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Denied probes do not prove writer closure.


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
    if os.name != "posix":
        return CommandResult(
            b"", b"", "execution_failure", "stopped", "unsupported_platform"
        )
    if deadline <= time.time():
        return CommandResult(b"", b"", "timeout", "stopped", "deadline_before_launch")
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        return CommandResult(
            b"", b"", "execution_failure", "stopped", type(exc).__name__
        )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = False
    stderr_truncated = False
    reason: str | None = None
    cleanup_started: float | None = None
    killed = False
    completed = False
    monotonic_deadline = time.monotonic() + max(0, deadline - time.time())
    try:
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        on_started(process.pid)
        while True:
            now = time.monotonic()
            if reason is None:
                stop_requested = cancelled()
                now = time.monotonic()
                if stop_requested:
                    reason = "cancelled"
                elif overflow:
                    reason = "protocol_failure"
                elif now >= monotonic_deadline:
                    reason = "timeout"
                if reason is not None:
                    cleanup_started = time.monotonic()
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
            if (
                cleanup_started is not None
                and now - cleanup_started >= 2
                and not killed
            ):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                killed = True
            for key, _ in selector.select(0.02):
                try:
                    chunk = os.read(key.fd, 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                name = str(key.data)
                room = 65536 - len(buffers[name])
                buffers[name].extend(chunk[:room])
                if len(chunk) > room:
                    if name == "stdout":
                        overflow = True
                    else:
                        stderr_truncated = True
            code = process.poll()
            closed = not selector.get_map()
            group_gone = not _group_exists(process.pid)
            if code is not None and closed and group_gone:
                completed = True
                classification = reason or (
                    "completed" if code == 0 else "execution_failure"
                )
                if overflow:
                    classification = (
                        "protocol_failure" if reason != "cancelled" else reason
                    )
                stderr, truncated = _diagnostics(
                    buffers["stderr"], env, stderr_truncated
                )
                diagnostic = ",".join(
                    name
                    for name, present in (
                        ("stdout_truncated", overflow),
                        ("stderr_truncated", truncated),
                    )
                    if present
                )
                return CommandResult(
                    bytes(buffers["stdout"]),
                    stderr,
                    classification,
                    "stopped",
                    diagnostic,
                    code,
                )
            if cleanup_started is not None and now - cleanup_started >= 5:
                completed = True
                stderr, truncated = _diagnostics(
                    buffers["stderr"], env, stderr_truncated
                )
                diagnostic = (
                    "cleanup_unconfirmed,stderr_truncated"
                    if truncated
                    else "cleanup_unconfirmed"
                )
                return CommandResult(
                    bytes(buffers["stdout"]),
                    stderr,
                    reason or "execution_failure",
                    "may_live",
                    diagnostic,
                    code,
                )
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
        if not completed:
            # An in-process callback failure still owns this live invocation.
            # Abrupt controller death bypasses this block and is reconciled by Core.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
