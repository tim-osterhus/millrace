"""Private bounded status/control channel for the Linux Pi subreaper."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


def _object(raw: bytes, expected: set[str]) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate supervisor field")
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("supervisor report shape")
    return value


class PiSupervisor:
    """Owns the outer helper and validates its two ordered private reports."""

    def __init__(
        self,
        supervisor: Path,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        bridge_fd: int,
        deadline: float,
    ) -> None:
        status_read, status_write = os.pipe()
        control_read, control_write = os.pipe()
        self.status = os.fdopen(status_read, "rb", buffering=0)
        self.control_fd = control_write
        self.lock = threading.Lock()
        self.buffer = bytearray()
        self.result: bool | None = None
        self.report: dict[str, Any] | None = None
        try:
            self.process = subprocess.Popen(
                (
                    sys.executable,
                    "-I",
                    "-B",
                    str(supervisor),
                    str(status_write),
                    str(control_read),
                    str(os.getpid()),
                    str(bridge_fd),
                    *argv,
                ),
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                start_new_session=True,
                pass_fds=(
                    (status_write, control_read, bridge_fd)
                    if bridge_fd >= 0
                    else (status_write, control_read)
                ),
            )
        except BaseException:
            self.status.close()
            os.close(control_write)
            raise
        finally:
            os.close(status_write)
            os.close(control_read)
        self.pi_pid: int | None = None
        self.startup_failed = False

    def startup(self, deadline: float) -> int:
        try:
            ready = _object(self._line(deadline), {"kind", "pi_pid"})
            if ready["kind"] != "ready" or type(ready["pi_pid"]) is not int:
                raise ValueError("supervisor startup report")
            if ready["pi_pid"] <= 0 or ready["pi_pid"] == self.process.pid:
                raise ValueError("supervisor Pi identity")
            self.pi_pid = ready["pi_pid"]
            return self.pi_pid
        except (OSError, ValueError):
            self.startup_failed = True
            os.close(self.control_fd)
            self.control_fd = -1
            raise

    def _line(self, deadline: float) -> bytes:
        while b"\n" not in self.buffer:
            if time.monotonic() >= deadline:
                raise ValueError("supervisor report timeout")
            remaining = deadline - time.monotonic()
            if not select.select([self.status], [], [], min(0.05, remaining))[0]:
                continue
            chunk = os.read(self.status.fileno(), 1024)
            if not chunk:
                raise ValueError("supervisor report EOF")
            self.buffer.extend(chunk)
            if len(self.buffer) > 1024:
                raise ValueError("supervisor report bound")
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        return bytes(line)

    def signal_pi(self, operation: str) -> None:
        command = {"terminate": b"T", "kill": b"K"}[operation]
        with self.lock:
            if self.control_fd < 0:
                if self.result is True:
                    return
                raise ValueError("supervisor control closed")
            os.write(self.control_fd, command)

    def finish(self) -> bool:
        with self.lock:
            if self.result is not None:
                return self.result
            if self.process.poll() is None:
                return False
            try:
                if self.startup_failed or self.pi_pid is None:
                    raise ValueError("supervisor startup unverified")
                report = _object(
                    self._line(time.monotonic() + 1),
                    {
                        "kind",
                        "pi_returncode",
                        "disposed",
                        "reaped_children",
                        "term_count",
                        "kill_count",
                        "remaining_children",
                    },
                )
                if (
                    report["kind"] != "complete"
                    or type(report["pi_returncode"]) is not int
                    or type(report["disposed"]) is not bool
                    or any(
                        type(report[key]) is not int or report[key] < 0
                        for key in (
                            "reaped_children",
                            "term_count",
                            "kill_count",
                            "remaining_children",
                        )
                    )
                    or report["remaining_children"] != 0
                    or self.buffer
                    or os.read(self.status.fileno(), 1)
                    or self.process.returncode
                    != (0 if report["disposed"] and report["pi_returncode"] == 0 else 1)
                ):
                    raise ValueError("supervisor completion report")
                self.report = report
                self.result = report["disposed"]
            except (OSError, ValueError):
                self.result = False
            finally:
                if self.control_fd >= 0:
                    os.close(self.control_fd)
                    self.control_fd = -1
                self.status.close()
            return bool(self.result)
