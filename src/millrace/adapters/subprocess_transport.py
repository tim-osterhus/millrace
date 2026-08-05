"""Bounded local subprocess transport helper.

This module is not a runner adapter and does not expose an adapter kind. It
only launches explicit local commands for selected adapters.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import IO, ClassVar, TypeAlias, cast

from millrace.adapters.runner_contract import (
    RedactionPolicy,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    canonicalize_redaction_policy,
    runner_cancellation_diagnostic_digest,
)
from millrace.contracts.compiled_plan import AuthorityValue

_ERROR_KINDS = frozenset(
    {
        "cancelled",
        "input_too_large",
        "invalid_cwd",
        "invocation_failed",
        "nonzero_exit",
        "output_too_large",
        "redaction_refused",
        "timeout",
    },
)
_REDACTION_REFUSED_DIAGNOSTIC = "redaction failed"


@dataclass(frozen=True, slots=True, repr=False)
class SubprocessTransportRequest:
    argv: tuple[str, ...]
    stdin_bytes: bytes
    cwd: Path
    env_allowlist: Mapping[str, str]
    timeout_seconds: float
    max_stdin_bytes: int
    max_stdout_bytes: int
    max_stderr_bytes: int
    redaction_policy: RedactionPolicy
    pre_cancelled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", _coerce_argv(self.argv))
        if not isinstance(self.stdin_bytes, bytes):
            raise TypeError("stdin_bytes must be bytes")
        if not isinstance(self.cwd, Path):
            raise TypeError("cwd must be Path")
        object.__setattr__(
            self,
            "env_allowlist",
            _coerce_env_allowlist(self.env_allowlist),
        )
        _require_positive_number(self.timeout_seconds, "timeout_seconds")
        _require_nonnegative_int(self.max_stdin_bytes, "max_stdin_bytes")
        _require_nonnegative_int(self.max_stdout_bytes, "max_stdout_bytes")
        _require_nonnegative_int(self.max_stderr_bytes, "max_stderr_bytes")
        if not isinstance(self.redaction_policy, RedactionPolicy):
            raise TypeError("redaction_policy must be RedactionPolicy")
        object.__setattr__(
            self,
            "redaction_policy",
            canonicalize_redaction_policy(self.redaction_policy),
        )
        if type(self.pre_cancelled) is not bool:
            raise TypeError("pre_cancelled must be a bool")

    def __repr__(self) -> str:
        policy_id = _redact_text(
            self.redaction_policy.policy_id,
            self.redaction_policy,
        )
        return (
            "SubprocessTransportRequest("
            f"argv_count={len(self.argv)}, "
            f"stdin_byte_count={len(self.stdin_bytes)}, "
            f"cwd={_redact_text(str(self.cwd), self.redaction_policy)!r}, "
            f"env_key_count={len(self.env_allowlist)}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"max_stdin_bytes={self.max_stdin_bytes!r}, "
            f"max_stdout_bytes={self.max_stdout_bytes!r}, "
            f"max_stderr_bytes={self.max_stderr_bytes!r}, "
            f"redaction_policy_id={policy_id!r}, "
            f"redaction_secret_token_count={len(self.redaction_policy.secret_tokens)}, "
            f"pre_cancelled={self.pre_cancelled!r}"
            ")"
        )


@dataclass(frozen=True, slots=True)
class SubprocessTransportSuccess:
    outcome_kind: ClassVar[str] = "success"

    exit_code: int
    stdout: str
    stderr: str
    stderr_truncated: bool = False

    def __post_init__(self) -> None:
        _require_int(self.exit_code, "exit_code")
        _require_string(self.stdout, "stdout")
        _require_string(self.stderr, "stderr")
        if type(self.stderr_truncated) is not bool:
            raise TypeError("stderr_truncated must be a bool")


@dataclass(frozen=True, slots=True)
class SubprocessTransportError:
    outcome_kind: ClassVar[str] = "error"

    error_kind: str
    stdout: str = ""
    stderr: str = ""
    diagnostics: str = ""
    exit_code: int | None = None
    stderr_truncated: bool = False

    def __post_init__(self) -> None:
        if self.error_kind not in _ERROR_KINDS:
            raise ValueError(
                f"unsupported subprocess transport error: {self.error_kind}",
            )
        _require_string(self.stdout, "stdout")
        _require_string(self.stderr, "stderr")
        _require_string(self.diagnostics, "diagnostics")
        if self.exit_code is not None:
            _require_int(self.exit_code, "exit_code")
        if type(self.stderr_truncated) is not bool:
            raise TypeError("stderr_truncated must be a bool")


SubprocessTransportOutcome: TypeAlias = (
    SubprocessTransportSuccess | SubprocessTransportError
)


class SubprocessTransport:
    """Launch explicit argv commands with bounded local process mechanics."""

    def invoke(
        self,
        request: SubprocessTransportRequest,
    ) -> SubprocessTransportOutcome:
        if not isinstance(request, SubprocessTransportRequest):
            raise TypeError("request must be SubprocessTransportRequest")
        started = self.start(request)
        if isinstance(started, SubprocessTransportError):
            return started
        try:
            started.process.wait(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            started.kill()
            started.cleanup()
            return SubprocessTransportError(error_kind="timeout")
        outcome = started.poll_completion()
        if outcome is None:
            started.kill()
            cleanup = started.cleanup()
            if cleanup.disposition != "complete":
                return SubprocessTransportError(
                    error_kind="invocation_failed",
                    diagnostics="owned subprocess cleanup was incomplete",
                )
            outcome = started.poll_completion()
        if outcome is None:
            return SubprocessTransportError(
                error_kind="invocation_failed",
                diagnostics="subprocess completion was unavailable",
            )
        return outcome

    def start(
        self,
        request: SubprocessTransportRequest,
    ) -> SubprocessTransportHandle | SubprocessTransportError:
        """Start a locally owned process and return its live bounded handle."""

        if not isinstance(request, SubprocessTransportRequest):
            raise TypeError("request must be SubprocessTransportRequest")
        if request.pre_cancelled:
            return SubprocessTransportError(error_kind="cancelled")
        if not _supports_process_group_ownership():
            return SubprocessTransportError(
                error_kind="invocation_failed",
                diagnostics="process-group ownership is unavailable",
            )
        if len(request.stdin_bytes) > request.max_stdin_bytes:
            return SubprocessTransportError(error_kind="input_too_large")
        if not request.cwd.is_absolute() or not request.cwd.is_dir():
            return SubprocessTransportError(error_kind="invalid_cwd")

        env = _build_environment(request.env_allowlist)
        try:
            process = subprocess.Popen(
                request.argv,
                cwd=request.cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
        except Exception as exc:
            diagnostics, redaction_failed = _try_redact_text(
                str(exc),
                redaction_policy=request.redaction_policy,
            )
            if redaction_failed:
                return SubprocessTransportError(
                    error_kind="redaction_refused",
                    diagnostics=diagnostics,
                )
            return SubprocessTransportError(
                error_kind="invocation_failed",
                diagnostics=diagnostics,
            )

        termination_lock = threading.Lock()

        def terminate_for_output_ceiling() -> None:
            with termination_lock:
                if process.poll() is None:
                    _terminate_process(process)

        if process.stdin is None or process.stdout is None or process.stderr is None:
            _terminate_process(process)
            return SubprocessTransportError(
                error_kind="invocation_failed",
                diagnostics="subprocess pipes were unavailable",
            )

        stdout_capture = _BoundedPipeCapture(
            process.stdout,
            maximum_bytes=request.max_stdout_bytes,
            on_exceeded=terminate_for_output_ceiling,
        )
        stderr_capture = _BoundedPipeCapture(
            process.stderr,
            maximum_bytes=request.max_stderr_bytes,
        )
        stdin_writer = _StdinWriter(process.stdin, request.stdin_bytes)
        stdout_capture.start()
        stderr_capture.start()
        stdin_writer.start()
        try:
            process_group_id = os.getpgid(process.pid)
        except (AttributeError, OSError):
            process_group_id = None
        return SubprocessTransportHandle(
            process=process,
            process_group_id=process_group_id,
            process_start_marker=_process_start_marker(process.pid),
            request=request,
            stdout_capture=stdout_capture,
            stderr_capture=stderr_capture,
            stdin_writer=stdin_writer,
            termination_lock=termination_lock,
        )


class SubprocessTransportHandle:
    """Live ownership of one process group and its bounded pipe workers."""

    def __init__(
        self,
        *,
        process: subprocess.Popen[bytes],
        process_group_id: int | None,
        process_start_marker: str | None,
        request: SubprocessTransportRequest,
        stdout_capture: _BoundedPipeCapture,
        stderr_capture: _BoundedPipeCapture,
        stdin_writer: _StdinWriter,
        termination_lock: threading.Lock,
    ) -> None:
        self.process = process
        self._process_group_id = process_group_id
        self._process_start_marker = process_start_marker
        self._request = request
        self._stdout_capture = stdout_capture
        self._stderr_capture = stderr_capture
        self._stdin_writer = stdin_writer
        self._termination_lock = termination_lock
        self._outcome: SubprocessTransportOutcome | None = None
        self._delivered = False

    @property
    def readers_joined(self) -> bool:
        return (
            self._stdout_capture.joined
            and self._stderr_capture.joined
            and self._stdin_writer.joined
        )

    def poll_completion(self) -> SubprocessTransportOutcome | None:
        if self._delivered:
            return None
        if self._outcome is None:
            if self.process.poll() is None:
                return None
            if self._process_group_exists():
                return None
            self._join_workers()
            self._outcome = self._completed_outcome()
        self._delivered = True
        return self._outcome

    def request_cancel(self) -> RunnerCancellationOperationResult:
        return _operation_result("cooperative_cancel", "unsupported")

    def terminate(self) -> RunnerCancellationOperationResult:
        return self._signal("terminate", signal.SIGTERM)

    def kill(self) -> RunnerCancellationOperationResult:
        return self._signal("kill", signal.SIGKILL)

    def cleanup(self) -> RunnerCleanupResult:
        started_at = time.time_ns()
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        if self.process.poll() is not None:
            self._join_workers()
            self._wait_for_process_group_exit(timeout_seconds=1.0)
        process_group_exists = self._process_group_exists()
        disposition = (
            "complete"
            if (
                self.process.poll() is not None
                and self.readers_joined
                and not process_group_exists
            )
            else "orphan_risk"
        )
        completed_at = max(started_at, time.time_ns())
        diagnostic: dict[str, AuthorityValue] = {
            "disposition": disposition,
            "readers_joined": self.readers_joined,
            "process_group_exists": process_group_exists,
        }
        return RunnerCleanupResult(
            disposition,
            started_at,
            completed_at,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )

    def _signal(
        self,
        operation: str,
        signum: int,
    ) -> RunnerCancellationOperationResult:
        started_at = time.time_ns()
        result = "succeeded"
        try:
            with self._termination_lock:
                if self._process_group_id is not None:
                    if self._process_group_identity_reused():
                        raise RuntimeError("owned process group identity was reused")
                    try:
                        os.killpg(self._process_group_id, signum)
                    except ProcessLookupError:
                        pass
                elif self.process.poll() is None:
                    if signum == signal.SIGTERM:
                        self.process.terminate()
                    else:
                        self.process.kill()
        except Exception:
            result = "failed"
        diagnostic = {"operation": operation, "result": result}
        return RunnerCancellationOperationResult(
            operation,
            result,
            started_at,
            max(started_at, time.time_ns()),
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )

    def _process_group_exists(self) -> bool:
        if self._process_group_id is None:
            return self.process.poll() is None
        try:
            os.killpg(self._process_group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _process_group_identity_reused(self) -> bool:
        if self._process_group_id != self.process.pid:
            return True
        current = _process_start_marker(self.process.pid)
        if self.process.poll() is None:
            return (
                current is not None
                and self._process_start_marker is not None
                and current != self._process_start_marker
            )
        if current is None:
            return _pid_exists(self.process.pid)
        return (
            self._process_start_marker is None
            or current != self._process_start_marker
        )

    def _wait_for_process_group_exit(self, *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while self._process_group_exists() and time.monotonic() < deadline:
            time.sleep(0.01)

    def _join_workers(self) -> None:
        self._stdout_capture.join()
        self._stderr_capture.join()
        self._stdin_writer.join()

    def _completed_outcome(self) -> SubprocessTransportOutcome:
        request = self._request
        if (
            self._stdout_capture.read_error is not None
            or self._stderr_capture.read_error is not None
            or not self.readers_joined
        ):
            return SubprocessTransportError(
                error_kind="invocation_failed",
                diagnostics="subprocess output capture failed",
            )
        if self._stdout_capture.exceeded:
            return SubprocessTransportError(error_kind="output_too_large")
        try:
            stdout = _redact_text(
                self._stdout_capture.data.decode("utf-8"),
                request.redaction_policy,
            )
            if len(stdout.encode("utf-8")) > request.max_stdout_bytes:
                return SubprocessTransportError(error_kind="output_too_large")
            if self._stderr_capture.exceeded:
                stderr = ""
                stderr_truncated = True
            else:
                stderr, stderr_truncated = _redact_and_bound_text(
                    self._stderr_capture.data.decode("utf-8", errors="replace"),
                    maximum_bytes=request.max_stderr_bytes,
                    redaction_policy=request.redaction_policy,
                )
        except UnicodeDecodeError:
            return SubprocessTransportError(
                error_kind="invocation_failed",
                diagnostics="subprocess stdout was not valid utf-8",
            )
        except Exception:
            return SubprocessTransportError(
                error_kind="redaction_refused",
                diagnostics=_REDACTION_REFUSED_DIAGNOSTIC,
            )
        returncode = self.process.returncode
        if returncode != 0:
            return SubprocessTransportError(
                error_kind=(
                    "cancelled"
                    if returncode is not None and returncode < 0
                    else "nonzero_exit"
                ),
                exit_code=returncode,
                stdout=stdout,
                stderr=stderr,
                stderr_truncated=stderr_truncated,
            )
        return SubprocessTransportSuccess(
            exit_code=cast(int, returncode),
            stdout=stdout,
            stderr=stderr,
            stderr_truncated=stderr_truncated,
        )


def _coerce_argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError("argv must be a tuple of strings")
    if not value:
        raise ValueError("argv cannot be empty")
    return _RedactedStringTuple(
        _require_nonblank_string(item, f"argv[{index}]")
        for index, item in enumerate(value)
    )


def _process_start_marker(pid: int) -> str | None:
    if os.name != "posix":
        return None
    try:
        result = subprocess.run(
            ("ps", "-o", "lstart=", "-p", str(pid)),
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    marker = result.stdout.strip()
    return marker or None


def _supports_process_group_ownership() -> bool:
    return os.name == "posix" and hasattr(os, "killpg") and hasattr(os, "getpgid")


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _coerce_env_allowlist(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("env_allowlist must be a mapping")
    env: dict[str, str] = {}
    for key, nested_value in cast(Mapping[object, object], value).items():
        env[_require_nonblank_string(key, "env_allowlist key")] = _require_string(
            nested_value, "env_allowlist value"
        )
    return _RedactedStringMapping(env)


def _build_environment(env_allowlist: Mapping[str, str]) -> dict[str, str]:
    env = {"PYTHONIOENCODING": "utf-8"}
    env.update(env_allowlist)
    return env


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    finally:
        try:
            process.wait(timeout=1)
        except Exception:
            pass


def _operation_result(
    operation: str,
    result: str,
) -> RunnerCancellationOperationResult:
    started_at = time.time_ns()
    diagnostic = {"operation": operation, "result": result}
    return RunnerCancellationOperationResult(
        operation,
        result,
        started_at,
        max(started_at, time.time_ns()),
        diagnostic,
        runner_cancellation_diagnostic_digest(diagnostic),
    )


def _redact_and_bound_text(
    value: str,
    *,
    maximum_bytes: int,
    redaction_policy: RedactionPolicy,
) -> tuple[str, bool]:
    redacted = _redact_text(value, redaction_policy)
    encoded = redacted.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return redacted, False
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore"), True


def _try_redact_text(
    value: str,
    *,
    redaction_policy: RedactionPolicy,
) -> tuple[str, bool]:
    try:
        return _redact_text(value, redaction_policy), False
    except Exception:
        return _REDACTION_REFUSED_DIAGNOSTIC, True


def _redact_text(value: str, policy: RedactionPolicy) -> str:
    return RedactionPolicy.redact_text(policy, value)


class _RedactedStringTuple(tuple[str, ...]):
    def __new__(cls, values: Iterable[str]) -> _RedactedStringTuple:
        return tuple.__new__(cls, tuple(values))

    def __repr__(self) -> str:
        return f"<redacted string tuple: {len(self)} item(s)>"

    __str__ = __repr__


class _RedactedStringMapping(Mapping[str, str]):
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = MappingProxyType(dict(values))

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"<redacted string mapping: {len(self)} item(s)>"

    __str__ = __repr__


class _BoundedPipeCapture:
    def __init__(
        self,
        stream: IO[bytes],
        *,
        maximum_bytes: int,
        on_exceeded: Callable[[], None] | None = None,
    ) -> None:
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self._on_exceeded = on_exceeded
        self._data = bytearray()
        self._exceeded = False
        self._read_error: Exception | None = None
        self._thread = threading.Thread(target=self._read_loop, daemon=True)

    @property
    def data(self) -> bytes:
        return bytes(self._data)

    @property
    def exceeded(self) -> bool:
        return self._exceeded

    @property
    def read_error(self) -> Exception | None:
        return self._read_error

    @property
    def joined(self) -> bool:
        return not self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def join(self) -> bool:
        self._thread.join(timeout=1)
        return self.joined

    def _read_loop(self) -> None:
        try:
            while True:
                chunk = self._stream.read(8192)
                if not chunk:
                    break
                remaining = self._maximum_bytes - len(self._data)
                if remaining <= 0:
                    self._mark_exceeded()
                    continue
                if len(chunk) > remaining:
                    self._data.extend(chunk[:remaining])
                    self._mark_exceeded()
                    continue
                self._data.extend(chunk)
        except Exception as exc:
            self._read_error = exc

    def _mark_exceeded(self) -> None:
        first_exceeded = not self._exceeded
        self._exceeded = True
        if first_exceeded and self._on_exceeded is not None:
            self._on_exceeded()


class _StdinWriter:
    def __init__(self, stream: IO[bytes], payload: bytes) -> None:
        self._stream = stream
        self._payload = payload
        self._thread = threading.Thread(target=self._write, daemon=True)

    def start(self) -> None:
        self._thread.start()

    @property
    def joined(self) -> bool:
        return not self._thread.is_alive()

    def join(self) -> bool:
        self._thread.join(timeout=1)
        return self.joined

    def _write(self) -> None:
        try:
            self._stream.write(self._payload)
            self._stream.flush()
        except BrokenPipeError:
            pass
        except OSError:
            pass
        finally:
            try:
                self._stream.close()
            except OSError:
                pass


def _require_nonblank_string(value: object, field_name: str) -> str:
    value_as_str = _require_string(value, field_name)
    if not value_as_str.strip():
        raise ValueError(f"{field_name} cannot be empty")
    return value_as_str


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _require_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    return value


def _require_nonnegative_int(value: object, field_name: str) -> int:
    value_as_int = _require_int(value, field_name)
    if value_as_int < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value_as_int


def _require_positive_number(value: object, field_name: str) -> float:
    if type(value) not in {int, float}:
        raise TypeError(f"{field_name} must be a number")
    value_as_float = float(cast(int | float, value))
    if value_as_float <= 0:
        raise ValueError(f"{field_name} must be positive")
    if not isfinite(value_as_float):
        raise ValueError(f"{field_name} must be finite")
    return value_as_float


__all__ = (
    "SubprocessTransport",
    "SubprocessTransportError",
    "SubprocessTransportHandle",
    "SubprocessTransportOutcome",
    "SubprocessTransportRequest",
    "SubprocessTransportSuccess",
)
