"""Public synchronous byte transport with ownership of one live POSIX group."""

from __future__ import annotations

import hashlib
import math
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import IO, BinaryIO, cast

OWNED_PROCESS_CONTRACT = "owned-process-v1"

__all__ = (
    "OWNED_PROCESS_CONTRACT",
    "ProcessRequest",
    "ProcessResult",
    "OwnedProcessError",
    "run_owned_process",
)


@dataclass(frozen=True)
class ProcessRequest:
    """Explicit launch inputs and finite byte policies; deadline is wall time."""

    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    stdin: bytes | None
    deadline: float
    stdin_limit: int
    stdout_capture_limit: int
    stderr_capture_limit: int
    stdout_fail_limit: int | None
    stderr_fail_limit: int | None
    cleanup_on_root_exit: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.argv, tuple)
            or not self.argv
            or not self.argv[0]
            or any(not isinstance(v, str) or "\x00" in v for v in self.argv)
            or not isinstance(self.cwd, Path)
            or "\x00" in str(self.cwd)
            or not isinstance(self.env, Mapping)
            or any(
                not isinstance(k, str)
                or not k
                or "=" in k
                or "\x00" in k
                or not isinstance(v, str)
                or "\x00" in v
                for k, v in self.env.items()
            )
        ):
            raise ValueError("invalid_process_launch")
        limits: tuple[int, ...] = (
            self.stdin_limit, self.stdout_capture_limit, self.stderr_capture_limit
        )
        limits += tuple(
            n for n in (self.stdout_fail_limit, self.stderr_fail_limit) if n is not None
        )
        if any(type(n) is not int or n <= 0 for n in limits):
            raise ValueError("invalid_process_limits")
        if self.stdin is not None and (
            not isinstance(self.stdin, bytes) or len(self.stdin) > self.stdin_limit
        ):
            raise ValueError("invalid_process_stdin")
        try:
            valid_deadline = (
                not isinstance(self.deadline, bool)
                and isinstance(self.deadline, (int, float))
                and math.isfinite(self.deadline)
                and self.deadline > 0
            )
        except OverflowError:
            valid_deadline = False
        if not valid_deadline or type(self.cleanup_on_root_exit) is not bool:
            raise ValueError("invalid_process_deadline_or_cleanup")
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


@dataclass(frozen=True)
class ProcessResult:
    """Observed facts, never a native success or graph-routing decision.

    No stop cause means no causal stop was observed. In particular, an opted-in
    root-exit cleanup can be forced without assigning a stop cause.
    """

    pid: int | None
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stop_cause: str | None
    stdout_bytes: int
    stderr_bytes: int
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_overflow: bool
    stderr_overflow: bool
    stdin_delivered: bool
    stdin_closed: bool
    stdout_eof: bool
    stderr_eof: bool
    root_reaped: bool
    cleanup_forced: bool
    group_state: str
    cleanup_confirmed: bool


class OwnedProcessError(RuntimeError):
    """A caller callback failed after bounded cleanup; cause holds the exception."""

    def __init__(self, result: ProcessResult) -> None:
        super().__init__("owned_process_callback_failed")
        self.result = result


class _Channel:
    def __init__(self, capture: int, fail: int | None) -> None:
        self.capture = capture
        self.fail = fail
        self.raw = bytearray()
        self.count = 0
        self.hash = hashlib.sha256()
        self.eof = False
        self.overflow = False

    def observe(self, raw: bytes) -> None:
        self.count += len(raw)
        self.hash.update(raw)
        self.raw.extend(raw[: max(0, self.capture - len(self.raw))])
        self.overflow = self.fail is not None and self.count > self.fail


class _Run:
    def __init__(
        self,
        request: ProcessRequest,
        poll: Callable[[], bool],
        on_started: Callable[[int], None],
        on_stdout: Callable[[bytes], None] | None,
        on_stderr: Callable[[bytes], None] | None,
        on_drained: Callable[[int], None] | None,
        on_stop: Callable[[str], None] | None,
    ) -> None:
        self.request = request
        self.poll = poll
        self.on_started = on_started
        self.callbacks = {"stdout": on_stdout, "stderr": on_stderr}
        self.on_drained = on_drained
        self.on_stop = on_stop
        self.end = time.monotonic() + max(0, request.deadline - time.time())
        self.child: subprocess.Popen[bytes] | None = None
        self.selector: selectors.BaseSelector | None = None
        self.channels = {
            "stdout": _Channel(request.stdout_capture_limit, request.stdout_fail_limit),
            "stderr": _Channel(request.stderr_capture_limit, request.stderr_fail_limit),
        }
        self.offset = 0
        self.stdin_closed = request.stdin is None
        self.cause: str | None = None
        self.cleanup: float | None = None
        self.term_at: float | None = None
        self.killed = False
        self.forced = False
        self.drained_called = False
        self.code: int | None = None
        self.group = "closed"
        self.group_closed = False
        self.setup_failed = False
        self.callback_error: BaseException | None = None

    def result(self) -> ProcessResult:
        out, err = self.channels["stdout"], self.channels["stderr"]
        reaped = self.child is not None and self.code is not None
        confirmed = self.child is None or (
            reaped and out.eof and err.eof
            and self.stdin_closed and self.group == "closed"
        )
        return ProcessResult(
            self.child.pid if self.child else None,
            self.code,
            bytes(out.raw),
            bytes(err.raw),
            self.cause,
            out.count,
            err.count,
            out.hash.hexdigest(),
            err.hash.hexdigest(),
            out.count > len(out.raw),
            err.count > len(err.raw),
            out.overflow,
            err.overflow,
            self.request.stdin is None or self.offset == len(self.request.stdin),
            self.stdin_closed,
            out.eof,
            err.eof,
            reaped,
            self.forced,
            self.group,
            confirmed,
        )

    def commit_cause(self, cause: str) -> None:
        if self.cause is not None:
            return
        self.cause = cause
        if self.cleanup is None:
            self.cleanup = time.monotonic()
        if self.on_stop is not None:
            try:
                self.on_stop(cause)
            except BaseException as exc:
                # A secondary observer error cannot replace a failing callback.
                if self.callback_error is None:
                    self.callback_error = exc
                    raise

    def stop(self, cause: str) -> None:
        self.commit_cause(cause)
        self.close_stdin()

    def close_stdin(self) -> None:
        if self.child is not None and self.child.stdin is not None:
            self.unregister(self.child.stdin)
            try:
                self.child.stdin.close()
            except OSError:
                self.commit_cause("io_failed")
        self.stdin_closed = True

    def unregister(self, pipe: IO[bytes]) -> None:
        if self.selector is not None:
            try:
                self.selector.unregister(pipe)
            except (KeyError, ValueError, OSError):
                pass

    def check(self) -> None:
        if self.callback_error is not None:
            return
        try:
            cancelled = self.poll()
        except BaseException as exc:
            self.callback_error = exc
            raise
        if cancelled:
            self.stop("cancelled")
        elif self.channels["stdout"].overflow:
            self.stop("stdout_overflow")
        elif self.channels["stderr"].overflow:
            self.stop("stderr_overflow")
        elif time.monotonic() >= self.end:
            self.stop("timeout")

    def group_state(self) -> str:
        assert self.child is not None
        if self.group_closed:
            return "closed"
        try:
            os.killpg(self.child.pid, 0)
            return "present"
        except ProcessLookupError:
            if self.code is not None:
                self.group_closed = True
            return "closed"
        except OSError:
            return "unknown"

    def signal(self, sig: int) -> None:
        assert self.child is not None
        if self.group_closed:
            return
        self.forced = True
        try:
            os.killpg(self.child.pid, sig)
        except OSError:
            pass

    def cleanup_step(self) -> None:
        if self.cleanup is None or self.group == "closed":
            return
        now = time.monotonic()
        if self.term_at is None:
            self.term_at = now
            self.signal(signal.SIGTERM)
        if not self.killed and self.term_at is not None and (
            now - self.term_at >= 2 or now - self.cleanup >= 5
        ):
            self.killed = True
            self.signal(signal.SIGKILL)

    def ready(self, wait: float) -> bool:
        """At most one chunk per ready channel, with controls between chunks."""
        assert self.selector is not None
        progress = False
        try:
            ready = self.selector.select(wait)
        except OSError:
            self.stop("io_failed")
            return False
        for key, _ in ready:
            pipe = cast(BinaryIO, key.fileobj)
            name = str(key.data)
            if name == "stdin":
                data = self.request.stdin
                assert data is not None
                if self.cause is not None or self.offset == len(data):
                    self.close_stdin()
                    progress = True
                    continue
                try:
                    chunk = data[self.offset : self.offset + 8192]
                    self.offset += os.write(key.fd, chunk)
                    progress = True
                    if self.offset == len(data):
                        self.close_stdin()
                except BlockingIOError:
                    pass
                except OSError:
                    self.stop("stdin_failed")
                self.check()
                continue
            try:
                raw = os.read(key.fd, 8192)
            except BlockingIOError:
                continue
            except OSError:
                self.stop("io_failed")
                self.unregister(pipe)
                continue
            progress = True
            channel = self.channels[name]
            if not raw:
                channel.eof = True
                self.unregister(pipe)
                continue
            channel.observe(raw)
            callback = self.callbacks[name]
            if callback is not None and self.callback_error is None:
                try:
                    callback(raw)
                except BaseException as exc:
                    self.callback_error = exc
                    raise
            self.check()
            if self.cleanup is not None and time.monotonic() - self.cleanup >= 5:
                break
        return progress

    def root_boundary(self) -> None:
        assert self.child is not None
        self.code = self.child.poll()
        if self.code is None:
            return
        if (
            self.request.cleanup_on_root_exit or self.code != 0
        ) and self.cleanup is None:
            self.cleanup = time.monotonic()
        # Consume queued tails fairly through EOF/EAGAIN before root-policy TERM.
        while self.ready(0):
            if self.cause is not None or self.forced or (
                # Reserve the existing TERM interval inside the same total bound.
                self.cleanup is not None and time.monotonic() - self.cleanup >= 5 - 2
            ):
                break
        if not self.stdin_closed:
            self.close_stdin()
            if self.request.stdin is not None and self.offset < len(self.request.stdin):
                self.stop("stdin_failed")
        if (
            not self.drained_called
            and not self.forced
            and self.cause is None
            and all(channel.eof for channel in self.channels.values())
        ):
            self.drained_called = True
            if self.on_drained is not None:
                self.check()
                if self.cause is None:
                    try:
                        self.on_drained(self.code)
                    except BaseException as exc:
                        self.callback_error = exc
                        raise
                    self.check()

    def loop(self) -> ProcessResult:
        assert self.child is not None
        while True:
            self.check()
            self.ready(0.02)
            self.root_boundary()
            self.check()
            self.group = self.group_state()
            if self.result().cleanup_confirmed:
                return self.result()
            self.cleanup_step()
            if self.cleanup is not None and time.monotonic() - self.cleanup >= 5:
                # A finite callback can return after the bound. Do not start a
                # fresh grace interval or omit the final owned-group KILL attempt.
                self.cleanup_step()
                return self.result()

    def cleanup_exception(self) -> None:
        """Drain/reap without recalling a failing caller callback."""
        self.stop("callback_failed")
        if self.child is None:
            return
        while True:
            self.code = self.child.poll()
            self.group = self.group_state()
            self.cleanup_step()
            if self.selector is not None:
                self.ready(0.02)
            else:
                time.sleep(0.02)
            if self.result().cleanup_confirmed:
                return
            if self.setup_failed and self.code is not None and self.group == "closed":
                # Failed setup may leave no way to observe EOF. Do not invent
                # those facts or wait for observations that cannot arrive.
                return
            assert self.cleanup is not None
            if time.monotonic() - self.cleanup >= 5:
                self.cleanup_step()
                return

    def execute(self) -> ProcessResult:
        try:
            if os.name != "posix":
                self.stop("unsupported_platform")
                return self.result()
            self.check()
            if self.cause is not None:
                return self.result()
            try:
                self.child = subprocess.Popen(
                    self.request.argv,
                    cwd=self.request.cwd,
                    env=self.request.env,
                    stdin=(
                        subprocess.DEVNULL
                        if self.request.stdin is None else subprocess.PIPE
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            except (OSError, ValueError):
                self.stop("start_failed")
                return self.result()
            self.group = "present"
            try:
                self.selector = selectors.DefaultSelector()
                for name, pipe in (
                    ("stdout", self.child.stdout),
                    ("stderr", self.child.stderr),
                    ("stdin", self.child.stdin),
                ):
                    if pipe is not None:
                        os.set_blocking(pipe.fileno(), False)
                        event = (
                            selectors.EVENT_WRITE
                            if name == "stdin" else selectors.EVENT_READ
                        )
                        self.selector.register(pipe, event, name)
            except (OSError, ValueError):
                self.setup_failed = True
                self.stop("io_failed")
                self.cleanup_exception()
                return self.result()
            try:
                self.on_started(self.child.pid)
            except BaseException as exc:
                self.callback_error = exc
                raise
            self.check()
            return self.loop()
        except BaseException as exc:
            self.callback_error = exc
            self.cleanup_exception()
            if isinstance(exc, Exception):
                raise OwnedProcessError(self.result()) from exc
            raise
        finally:
            if self.selector is not None:
                self.selector.close()
            if self.child is not None:
                for pipe in (self.child.stdin, self.child.stdout, self.child.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except OSError:
                            pass  # Descriptor cleanup cannot replace a caller error.


def run_owned_process(
    request: ProcessRequest,
    *,
    poll: Callable[[], bool],
    on_started: Callable[[int], None],
    on_stdout: Callable[[bytes], None] | None = None,
    on_stderr: Callable[[bytes], None] | None = None,
    on_drained: Callable[[int], None] | None = None,
    on_stop: Callable[[str], None] | None = None,
) -> ProcessResult:
    """Own a child, pipes and group until closure or one bounded cleanup limit.

    Stream callbacks do bounded synchronous work. A callback cannot be preempted;
    cancellation/deadline are checked immediately when it returns. Only live-call
    ownership permits signaling; this function never accepts a recovered PID.

    Raises:
        OwnedProcessError: A caller callback raised an ordinary exception. Its
            result retains cleanup facts and its cause is the original error.
        BaseException: An interruption is re-raised unchanged after cleanup.
    """
    return _Run(
        request, poll, on_started, on_stdout, on_stderr, on_drained, on_stop
    ).execute()
