"""Focused Pi RPC client for one prompt lifecycle."""

from __future__ import annotations

import codecs
import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from millrace_ai.contracts import TokenUsage
from millrace_ai.runners.errors import RunnerBinaryNotFoundError
from millrace_ai.runners.requests import RunnerExitKind

_QUEUE_EOF = object()


@dataclass(frozen=True, slots=True)
class PiRpcSessionResult:
    exit_kind: RunnerExitKind
    exit_code: int | None
    started_at: datetime
    ended_at: datetime
    event_lines: tuple[str, ...]
    assistant_text: str | None
    token_usage: TokenUsage | None
    failure_class: str | None
    notes: tuple[str, ...]
    stderr_text: str
    observed_exit_kind: RunnerExitKind | None = None
    observed_exit_code: int | None = None


class PiRpcClient:
    """Thin JSONL RPC client that owns one Pi subprocess session."""

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        process_factory: Callable[..., subprocess.Popen[bytes]] | None = None,
        abort_grace_seconds: float = 2.0,
    ) -> None:
        self.command = command
        self.cwd = Path(cwd)
        self.env = env
        self.process_factory = process_factory or _spawn_pi_process
        self.abort_grace_seconds = abort_grace_seconds

    def run_prompt(self, *, prompt: str, timeout_seconds: int) -> PiRpcSessionResult:
        started_at = datetime.now(timezone.utc)
        process = self._spawn_process()
        stdout_queue: queue.Queue[str | object] = queue.Queue()
        stderr_chunks: list[str] = []
        event_lines: list[str] = []
        notes: list[str] = []
        provider_error_detected = False

        stdout_thread = threading.Thread(
            target=_read_jsonl_stdout_lines,
            args=(process.stdout, stdout_queue),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_read_stderr_text,
            args=(process.stderr, stderr_chunks),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        prompt_id = "prompt-1"
        deadline = time.monotonic() + max(1, timeout_seconds)

        try:
            self._send_command(
                process,
                {
                    "id": prompt_id,
                    "type": "prompt",
                    "message": prompt,
                },
            )
            prompt_response = self._wait_for_response(
                stdout_queue=stdout_queue,
                response_id=prompt_id,
                event_lines=event_lines,
                deadline=deadline,
            )
            if prompt_response is None:
                return self._timeout_result(
                    process=process,
                    stdout_queue=stdout_queue,
                    stderr_chunks=stderr_chunks,
                    started_at=started_at,
                    event_lines=event_lines,
                )
            if not prompt_response.get("success", False):
                return PiRpcSessionResult(
                    exit_kind="runner_error",
                    exit_code=1,
                    started_at=started_at,
                    ended_at=datetime.now(timezone.utc),
                    event_lines=tuple(event_lines),
                    assistant_text=None,
                    token_usage=None,
                    failure_class="runner_rpc_rejected",
                    notes=(str(prompt_response.get("error") or "prompt rejected"),),
                    stderr_text="".join(stderr_chunks),
                )

            while True:
                record = self._read_record(stdout_queue=stdout_queue, deadline=deadline)
                if record is None:
                    return self._timeout_result(
                        process=process,
                        stdout_queue=stdout_queue,
                        stderr_chunks=stderr_chunks,
                        started_at=started_at,
                        event_lines=event_lines,
                    )
                raw_line, payload = record
                if payload is None:
                    return PiRpcSessionResult(
                        exit_kind="runner_error",
                        exit_code=1,
                        started_at=started_at,
                        ended_at=datetime.now(timezone.utc),
                        event_lines=tuple(event_lines),
                        assistant_text=None,
                        token_usage=None,
                        failure_class="runner_incomplete_rpc_stream",
                        notes=("pi rpc stream ended before agent_end",),
                        stderr_text="".join(stderr_chunks),
                    )
                if payload.get("type") == "response":
                    continue

                event_lines.append(raw_line)
                if _event_stop_reason(payload) == "error":
                    provider_error_detected = True

                if payload.get("type") == "agent_end":
                    break

            assistant_text = self._request_last_assistant_text(process, stdout_queue, notes)
            token_usage = self._request_session_stats(process, stdout_queue, notes)
            final_exit_code = 0
            failure_class: str | None = None
            exit_kind: RunnerExitKind = "completed"

            if provider_error_detected and not _has_nonempty_text(assistant_text):
                exit_kind = "provider_error"
                final_exit_code = 1
                failure_class = "runner_provider_failure"
            elif not _has_nonempty_text(assistant_text):
                exit_kind = "runner_error"
                final_exit_code = 1
                failure_class = "runner_empty_assistant_text"

            return PiRpcSessionResult(
                exit_kind=exit_kind,
                exit_code=final_exit_code,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc),
                event_lines=tuple(event_lines),
                assistant_text=assistant_text,
                token_usage=token_usage,
                failure_class=failure_class,
                notes=tuple(notes),
                stderr_text="".join(stderr_chunks),
            )
        finally:
            _shutdown_process(process)

    def _spawn_process(self) -> subprocess.Popen[bytes]:
        try:
            return self.process_factory(
                self.command,
                cwd=self.cwd,
                env=self.env,
            )
        except FileNotFoundError as exc:
            raise RunnerBinaryNotFoundError(self.command[0]) from exc

    def _send_command(self, process: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
        if process.stdin is None:
            raise OSError("pi rpc stdin is unavailable")
        process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        process.stdin.flush()

    def _wait_for_response(
        self,
        *,
        stdout_queue: queue.Queue[str | object],
        response_id: str,
        event_lines: list[str],
        deadline: float,
    ) -> dict[str, Any] | None:
        while True:
            record = self._read_record(stdout_queue=stdout_queue, deadline=deadline)
            if record is None:
                return None
            raw_line, payload = record
            if payload is None:
                raise ValueError("pi rpc stream ended before response")
            if payload.get("type") != "response":
                event_lines.append(raw_line)
                continue
            if payload.get("id") == response_id:
                return payload

    def _request_last_assistant_text(
        self,
        process: subprocess.Popen[bytes],
        stdout_queue: queue.Queue[str | object],
        notes: list[str],
    ) -> str | None:
        response = self._request_response(
            process=process,
            stdout_queue=stdout_queue,
            response_id="last-assistant-1",
            command_type="get_last_assistant_text",
        )
        if response is None or not response.get("success", False):
            notes.append("pi rpc get_last_assistant_text failed")
            return None
        data = response.get("data")
        if not isinstance(data, dict):
            return None
        text = data.get("text")
        return text if isinstance(text, str) else None

    def _request_session_stats(
        self,
        process: subprocess.Popen[bytes],
        stdout_queue: queue.Queue[str | object],
        notes: list[str],
    ) -> TokenUsage | None:
        response = self._request_response(
            process=process,
            stdout_queue=stdout_queue,
            response_id="session-stats-1",
            command_type="get_session_stats",
        )
        if response is None or not response.get("success", False):
            notes.append("pi rpc get_session_stats unavailable")
            return None
        data = response.get("data")
        return _token_usage_from_stats_payload(data)

    def _request_response(
        self,
        *,
        process: subprocess.Popen[bytes],
        stdout_queue: queue.Queue[str | object],
        response_id: str,
        command_type: str,
    ) -> dict[str, Any] | None:
        try:
            self._send_command(
                process,
                {
                    "id": response_id,
                    "type": command_type,
                },
            )
        except OSError:
            return None
        return self._wait_for_response(
            stdout_queue=stdout_queue,
            response_id=response_id,
            event_lines=[],
            deadline=time.monotonic() + 5,
        )

    def _timeout_result(
        self,
        *,
        process: subprocess.Popen[bytes],
        stdout_queue: queue.Queue[str | object],
        stderr_chunks: list[str],
        started_at: datetime,
        event_lines: list[str],
    ) -> PiRpcSessionResult:
        notes = ["runner process exceeded timeout"]
        try:
            self._send_command(process, {"id": "abort-1", "type": "abort"})
            notes.append("sent pi rpc abort command")
        except OSError:
            notes.append("failed to send pi rpc abort command")

        grace_deadline = time.monotonic() + self.abort_grace_seconds
        while time.monotonic() < grace_deadline:
            record = self._read_record(stdout_queue=stdout_queue, deadline=grace_deadline)
            if record is None:
                break
            raw_line, payload = record
            if payload is None:
                break
            if payload.get("type") != "response":
                event_lines.append(raw_line)
            if process.poll() is not None:
                break

        if process.poll() is None:
            notes.append("pi rpc process required hard kill after abort grace period")
        return PiRpcSessionResult(
            exit_kind="timeout",
            exit_code=124,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            event_lines=tuple(event_lines),
            assistant_text=None,
            token_usage=None,
            failure_class="runner_timeout",
            notes=tuple(notes),
            stderr_text="".join(stderr_chunks),
        )

    def _read_record(
        self,
        *,
        stdout_queue: queue.Queue[str | object],
        deadline: float,
    ) -> tuple[str, dict[str, Any] | None] | None:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            return None

        try:
            item = stdout_queue.get(timeout=timeout)
        except queue.Empty:
            return None

        if item is _QUEUE_EOF:
            return ("", None)

        raw_line = item
        if not isinstance(raw_line, str):
            return ("", None)

        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in pi rpc stream: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("pi rpc stream record must be a JSON object")
        return raw_line, payload


def _spawn_pi_process(
    command: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
    )


def _read_jsonl_stdout_lines(stream: Any, output_queue: queue.Queue[str | object]) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            buffer += decoder.decode(chunk)
            while True:
                newline_index = buffer.find("\n")
                if newline_index == -1:
                    break
                line = buffer[:newline_index]
                buffer = buffer[newline_index + 1 :]
                if line.endswith("\r"):
                    line = line[:-1]
                output_queue.put(line)
        buffer += decoder.decode(b"", final=True)
        if buffer:
            if buffer.endswith("\r"):
                buffer = buffer[:-1]
            output_queue.put(buffer)
    finally:
        output_queue.put(_QUEUE_EOF)


def _read_stderr_text(stream: Any, chunks: list[str]) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")()
    while True:
        chunk = stream.read(4096)
        if not chunk:
            break
        chunks.append(decoder.decode(chunk))
    chunks.append(decoder.decode(b"", final=True))


def _shutdown_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
    except OSError:
        pass

    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
            return
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                return


def _has_nonempty_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _event_stop_reason(payload: dict[str, Any]) -> str | None:
    message = payload.get("message")
    if isinstance(message, dict):
        stop_reason = message.get("stopReason")
        if isinstance(stop_reason, str):
            return stop_reason

    messages = payload.get("messages")
    if isinstance(messages, list):
        for item in reversed(messages):
            if isinstance(item, dict) and item.get("role") == "assistant":
                stop_reason = item.get("stopReason")
                if isinstance(stop_reason, str):
                    return stop_reason
    return None


def _token_usage_from_stats_payload(payload: Any) -> TokenUsage | None:
    if not isinstance(payload, dict):
        return None
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None

    input_tokens = _coerce_non_negative_int(tokens.get("input"))
    output_tokens = _coerce_non_negative_int(tokens.get("output"))
    cached_input_tokens = _coerce_non_negative_int(tokens.get("cacheRead"))
    total_tokens = _coerce_non_negative_int(tokens.get("total"))
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens

    return TokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=0,
        total_tokens=total_tokens,
    )


def _coerce_non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


__all__ = ["PiRpcClient", "PiRpcSessionResult"]
