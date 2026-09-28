"""Bounded interactive client for the pinned Pi JSONL protocol.

The lifecycle parser validates evidence before deriving the conservative
filesystem-only cleanup predicate. It never interprets terminal markers.
"""

from __future__ import annotations

import os
import queue
import select
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, cast

from millrace.adapters.pi_rpc_config import strict_json
from millrace.adapters.pi_rpc_groups import GroupProfile, PiGroups
from millrace.adapters.pi_rpc_supervision import PiSupervisor
from millrace.adapters.runner_contract import AdapterTokenUsage


class PiRpcError(ValueError):
    def __init__(self, reason: str, kind: str = "invocation_failed") -> None:
        super().__init__(reason)
        self.kind = kind


class PiRpcNotStarted(PiRpcError):
    """A constructor refusal before Popen is called."""


def parse_candidate(text: str, maximum: int) -> dict[str, Any]:
    if len(text.encode("utf-8")) > maximum:
        raise PiRpcError("result bound", "output_too_large")
    value = strict_json(text)
    if not isinstance(value, dict) or set(value) != {
        "marker",
        "artifact_payload_candidate",
        "observation_payload_candidate",
    }:
        raise ValueError("candidate envelope")
    if not isinstance(value["marker"], str) or not value["marker"].strip():
        raise ValueError("candidate marker")
    for key in ("artifact_payload_candidate", "observation_payload_candidate"):
        if value[key] is not None and not isinstance(value[key], dict):
            raise ValueError("candidate payload")
    observation = value["observation_payload_candidate"]
    if (
        observation is not None
        and "runner_report" in observation
        and not isinstance(observation["runner_report"], str)
    ):
        raise ValueError("runner_report must be text")
    return value


def token_usage(
    value: Any, *, total_key: str = "total", allow_zero: bool = False
) -> AdapterTokenUsage | None:
    if not isinstance(value, dict):
        return None
    values = [
        value.get(k) for k in ("input", "output", "cacheRead", "cacheWrite", total_key)
    ]
    if any(type(x) is not int or not 0 <= x <= 2**53 - 1 for x in values):
        return None
    inp, out, read, write, total = cast(list[int], values)
    if (
        total != inp + out + read + write
        or total > 2**63 - 1
        or (not total and not allow_zero)
    ):
        return None
    return AdapterTokenUsage(inp + read + write, out, total)


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PiRpcError("expected protocol object")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise PiRpcError("expected protocol identifier")
    return value


def _boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise PiRpcError("expected protocol boolean")
    return bool(value)


def _content(value: Any, *, assistant: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise PiRpcError("content must be an array")
    result = []
    for raw in value:
        item = _object(raw)
        kind = item.get("type")
        if kind in ("text", "thinking"):
            if not isinstance(item.get("text" if kind == "text" else "thinking"), str):
                raise PiRpcError("content text")
        elif kind == "toolCall" and assistant:
            _text(item.get("id"))
            _text(item.get("name"))
            _object(item.get("arguments"))
        elif kind == "image" and not assistant:
            _text(item.get("data"))
            _text(item.get("mimeType"))
        else:
            raise PiRpcError("unknown content block")
        result.append(item)
    return result


class PiLifecycle:
    """One prompt; low-level runs may continue only through intrinsic recovery."""

    def __init__(
        self, model: dict[str, Any], prompt: str, *, group_accounting: bool = False
    ) -> None:
        self.group_accounting = group_accounting
        self.model = model
        self.prompt = prompt
        self.run = self.turn = False
        self.message: dict[str, Any] | None = None
        self.runs = self.settled = self.users = 0
        self.continuation = self.will_retry = False
        self.auxiliary = False
        self.messages: list[dict[str, Any]] = []
        self.last: dict[str, Any] | None = None
        self.turn_results: list[dict[str, Any]] = []
        self.declared: dict[str, tuple[str, dict[str, Any]]] = {}
        self.tools: dict[str, tuple[str, dict[str, Any]]] = {}
        self.completed: dict[str, dict[str, Any]] = {}
        self.results: set[str] = set()
        self.uncertain: set[str] = set()
        self.usages: list[AdapterTokenUsage] = []
        self.usage_complete = True
        self.turn_assistant = False
        self.recovery: str | None = None

    def _message(self, value: Any, *, final: bool) -> dict[str, Any]:
        m = _object(value)
        role = m.get("role")
        if role not in ("assistant", "toolResult", "user", "system"):
            raise PiRpcError("message role")
        if role == "system":
            if not isinstance(m.get("content"), str) or not isinstance(
                m.get("sections"), dict
            ):
                raise PiRpcError("system message")
        else:
            _content(m.get("content"), assistant=role == "assistant")
            if type(m.get("timestamp")) is not int or m["timestamp"] < 0:
                raise PiRpcError("message timestamp")
        if role == "assistant":
            for key, expected in [
                ("model", self.model["id"]),
                ("provider", self.model["provider"]),
                ("api", self.model["api"]),
            ]:
                if m.get(key) != expected:
                    raise PiRpcError("model drift")
            if m.get("stopReason") not in (
                ("stop", "toolUse", "length", "error", "aborted")
                if final
                else ("pending", "stop", "toolUse", "length", "error", "aborted")
            ):
                raise PiRpcError("assistant stop reason")
        if role == "toolResult":
            _text(m.get("toolCallId"))
            _text(m.get("toolName"))
            _boolean(m.get("isError"))
        return m

    def event(self, event: dict[str, Any]) -> None:
        kind = _text(event.get("type"))
        if self.settled:
            raise PiRpcError("event after settlement")
        if kind == "agent_start":
            if (
                self.run
                or self.turn
                or self.message
                or (self.runs and not self.continuation)
            ):
                raise PiRpcError("agent start boundary")
            self.run = True
            self.runs += 1
            self.messages = []
            self.continuation = False
        elif kind == "turn_start":
            if not self.run or self.turn or self.message:
                raise PiRpcError("turn start boundary")
            self.turn = True
            self.turn_results = []
            self.turn_assistant = False
        elif kind == "message_start":
            if not self.turn or self.message is not None:
                raise PiRpcError("message start boundary")
            self.message = self._message(event.get("message"), final=False)
        elif kind == "message_update":
            if self.message is None or self.message["role"] != "assistant":
                raise PiRpcError("message update boundary")
            delta = _object(event.get("assistantMessageEvent"))
            dt = _text(delta.get("type"))
            if dt not in {
                "text_start",
                "text_delta",
                "text_end",
                "thinking_start",
                "thinking_delta",
                "thinking_end",
                "toolcall_start",
                "toolcall_delta",
                "toolcall_end",
                "start",
                "done",
                "error",
            }:
                raise PiRpcError("unknown assistant update")
            if dt.endswith(("_start", "_delta", "_end")):
                if (
                    type(delta.get("contentIndex")) is not int
                    or delta["contentIndex"] < 0
                ):
                    raise PiRpcError("delta index")
            if dt.endswith("_delta") and not isinstance(delta.get("delta"), str):
                raise PiRpcError("delta text")
            if dt in ("done", "error"):
                self.uncertain.add("unqualified update")
        elif kind == "message_end":
            m = self._message(event.get("message"), final=True)
            start = self.message
            if (
                start is None
                or start["role"] != m["role"]
                or start.get("timestamp") != m.get("timestamp")
            ):
                raise PiRpcError("message end mismatch")
            if m["role"] != "assistant" and start != m:
                raise PiRpcError("nonassistant message conflict")
            self.message = None
            self.messages.append(m)
            if m["role"] == "user":
                self.users += 1
                if self.users != 1 or m["content"] != [
                    {"type": "text", "text": self.prompt}
                ]:
                    raise PiRpcError("unowned prompt")
            elif m["role"] == "assistant":
                if self.turn_assistant:
                    raise PiRpcError("multiple assistants in turn")
                self.turn_assistant = True
                self.last = m
                usage = token_usage(
                    m.get("usage"), total_key="totalTokens", allow_zero=True
                )
                if usage is None:
                    self.usage_complete = False
                else:
                    self.usages.append(usage)
                if m["stopReason"] not in ("stop", "toolUse"):
                    self.uncertain.add("assistant error/abort")
                for block in m["content"]:
                    if block["type"] == "toolCall":
                        ident, name = block["id"], block["name"]
                        if ident in self.declared:
                            raise PiRpcError("duplicate tool declaration")
                        self.declared[ident] = (name, block["arguments"])
                        if name not in ("read", "edit", "write") and not (
                            self.group_accounting and name == "bash"
                        ):
                            self.uncertain.add("unsupported tool")
            elif m["role"] == "toolResult":
                ident = m["toolCallId"]
                end = self.completed.get(ident)
                if (
                    ident in self.results
                    or end is None
                    or (m["toolName"], m["content"], m["isError"])
                    != (end["toolName"], end["result"]["content"], end["isError"])
                ):
                    raise PiRpcError("tool result conflict")
                self.results.add(ident)
                self.turn_results.append(m)
        elif kind == "tool_execution_start":
            ident, name, args = (
                _text(event.get("toolCallId")),
                _text(event.get("toolName")),
                _object(event.get("args")),
            )
            if (
                not self.turn
                or self.message
                or ident in self.tools
                or ident in self.completed
                or self.declared.get(ident) != (name, args)
            ):
                raise PiRpcError("tool start mismatch")
            self.tools[ident] = name, args
        elif kind == "tool_execution_end":
            ident, name = _text(event.get("toolCallId")), _text(event.get("toolName"))
            tool_start = self.tools.pop(ident, None)
            if tool_start is None or tool_start[0] != name or ident in self.completed:
                raise PiRpcError("tool end mismatch")
            error = _boolean(event.get("isError"))
            content = _content(_object(event.get("result")).get("content"))
            self.completed[ident] = event
            args = tool_start[1]
            safe = not error and len(content) == 1 and content[0]["type"] == "text"
            text = content[0].get("text", "") if safe else ""
            if name == "read":
                safe = (
                    safe
                    and isinstance(args.get("path"), str)
                    and not text.startswith("Read image file [")
                )
            elif name == "write":
                safe = (
                    safe
                    and isinstance(args.get("path"), str)
                    and isinstance(args.get("content"), str)
                    and text == "Successfully wrote to " + args["path"]
                )
            elif name == "edit":
                edits = args.get("edits")
                if self.group_accounting and set(args) == {
                    "path",
                    "oldText",
                    "newText",
                }:
                    edits = [{"oldText": args["oldText"], "newText": args["newText"]}]
                safe = (
                    safe
                    and isinstance(args.get("path"), str)
                    and isinstance(edits, list)
                    and bool(edits)
                    and all(
                        isinstance(e, dict)
                        and set(e) == {"oldText", "newText"}
                        and all(isinstance(v, str) for v in e.values())
                        for e in edits
                    )
                )
                safe = safe and text == (
                    f"Successfully replaced {len(cast(list[Any], edits))} "
                    f"block(s) in {args['path']}."
                )
            elif name == "bash" and self.group_accounting:
                # Actual status and settlement are mandatory in PiGroups.finish.
                safe = len(content) == 1 and content[0]["type"] == "text"
            else:
                safe = False
            if not safe:
                self.uncertain.add("unqualified tool result")
        elif kind == "tool_execution_update":
            ident = _text(event.get("toolCallId"))
            if ident not in self.tools or event.get("toolName") != self.tools[ident][0]:
                raise PiRpcError("tool update mismatch")
            if self.group_accounting and self.tools[ident][0] == "bash":
                if event.get("args") != self.tools[ident][1] or any(
                    c["type"] != "text"
                    for c in _content(
                        _object(event.get("partialResult")).get("content")
                    )
                ):
                    raise PiRpcError("bash update shape")
            else:
                self.uncertain.add("tool update")
        elif kind == "turn_end":
            if (
                not self.turn_assistant
                or not self.turn
                or self.message
                or self.tools
                or self.last is None
                or event.get("message") != self.last
                or event.get("toolResults") != self.turn_results
            ):
                raise PiRpcError("turn end snapshot")
            self.turn = False
        elif kind == "agent_end":
            if (
                not self.run
                or self.turn
                or self.message
                or self.tools
                or event.get("messages") != self.messages
            ):
                raise PiRpcError("agent end snapshot")
            self.will_retry = _boolean(event.get("willRetry"))
            self.run = False
        elif kind == "agent_settled":
            if (
                self.run
                or self.turn
                or self.message
                or self.tools
                or not self.runs
                or self.will_retry
                or self.users != 1
                or set(self.declared) != self.results
            ):
                raise PiRpcError("incomplete settlement")
            self.settled = 1
        elif kind in {
            "auto_retry_start",
            "auto_retry_end",
            "compaction_start",
            "compaction_end",
            "summarization_retry_scheduled",
            "summarization_retry_attempt_start",
            "summarization_retry_finished",
        }:
            self.auxiliary = True
            self.uncertain.add("intrinsic recovery")
            if kind in ("auto_retry_start", "summarization_retry_scheduled"):
                for key in ("attempt", "maxAttempts", "delayMs"):
                    if type(event.get(key)) is not int or event[key] < 0:
                        raise PiRpcError("retry numeric field")
                if not isinstance(event.get("errorMessage"), str):
                    raise PiRpcError("retry error message")
            if kind == "auto_retry_end":
                _boolean(event.get("success"))
                if type(event.get("attempt")) is not int or event["attempt"] < 0:
                    raise PiRpcError("retry attempt")
            if kind.startswith("compaction_"):
                if event.get("reason") not in ("manual", "threshold", "overflow"):
                    raise PiRpcError("compaction reason")
                if kind == "compaction_end":
                    _boolean(event.get("aborted"))
                    _boolean(event.get("willRetry"))
            if kind in ("auto_retry_start", "compaction_start"):
                if self.run or self.turn or self.message:
                    raise PiRpcError("recovery boundary")
                self.continuation = True
            if kind == "summarization_retry_attempt_start" and event.get(
                "source"
            ) not in ("branchSummary", "compaction"):
                raise PiRpcError("summary source")
        elif kind == "queue_update":
            self.uncertain.add("unqualified queue event")
            if event.get("steering") != [] or event.get("followUp") != []:
                raise PiRpcError("queued prompts")
        elif kind == "thinking_level_changed":
            self.uncertain.add("unqualified thinking event")
            if event.get("level") != "xhigh":
                raise PiRpcError("thinking drift")
        elif kind in {"entry_appended", "session_info_changed"}:
            if kind == "entry_appended":
                _object(event.get("entry"))
            elif event.get("name") is not None and not isinstance(event["name"], str):
                raise PiRpcError("session name")
            self.uncertain.add("unqualified session event")
        else:
            raise PiRpcError("unknown or forbidden event")

    def final_text(self) -> str:
        if not self.settled or self.last is None or self.last["stopReason"] != "stop":
            raise PiRpcError("no successful final assistant")
        if any(c["type"] == "toolCall" for c in self.last["content"]):
            raise PiRpcError("final assistant still declares tools")
        return "".join(
            c["text"] for c in self.last["content"] if c["type"] == "text"
        ).strip()

    def fallback_usage(self) -> AdapterTokenUsage | None:
        if (
            not self.settled
            or self.auxiliary
            or not self.usage_complete
            or not self.usages
            or self.message is not None
        ):
            return None
        inp = sum(u.input_tokens for u in self.usages)
        out = sum(u.output_tokens for u in self.usages)
        if not inp + out or inp + out > 2**63 - 1:
            return None
        return AdapterTokenUsage(inp, out, inp + out)


class PiRpcClient:
    def __init__(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        limits: dict[str, Any],
        model: dict[str, Any],
        prompt: str,
        timeout: float,
        *,
        deadline: float | None = None,
        group_profile: GroupProfile | None = None,
    ) -> None:
        self.limits, self.model, self.prompt = limits, model, prompt
        self.deadline = time.monotonic() + timeout if deadline is None else deadline
        self.lifecycle = PiLifecycle(
            model, prompt, group_accounting=group_profile is not None
        )
        self.groups: PiGroups | None = None
        self.supervisor: PiSupervisor | None = None
        self.group_profile = group_profile
        self.cwd, self.env = cwd, env
        self.bridge_stream: BinaryIO | None = None
        self.pending: dict[str, str] = {}
        self.responses: dict[str, dict[str, Any]] = {}
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(
            maxsize=limits["max_event_count"]
        )
        self.errors: list[PiRpcError] = []
        self.eof = {"stdout": False, "stderr": False}
        self.stream_bytes = {"stdout": 0, "stderr": 0, "bridge": 0}
        self.stream_events = {"stdout": 0, "stderr": 0, "bridge": 0}
        self.stream_error_codes = {
            "stdout": "none",
            "stderr": "none",
            "bridge": "none",
        }
        self.stop = threading.Event()
        self.cancel_requested = threading.Event()
        self.consume_lock = threading.Lock()
        self.abort_sent = False
        self.abort_ack = threading.Event()
        self.sent_prompt = False
        self.usage: AdapterTokenUsage | None = None
        self.stderr = bytearray()
        self.closed = False
        self.cleanup_draining = False
        self.pi_session_id: str | None = None
        if time.monotonic() >= self.deadline:
            raise PiRpcNotStarted("startup deadline")
        read_fd = write_fd = None
        if group_profile is not None:
            read_fd, write_fd = os.pipe()
        try:
            if group_profile is not None and write_fd is not None:
                argv = group_profile.argv(argv, write_fd, limits)
            if group_profile is not None and group_profile.supervised:
                if write_fd is None or group_profile.supervisor is None:
                    raise ValueError("missing supervisor")
                self.supervisor = PiSupervisor(
                    group_profile.supervisor, argv, cwd, env, write_fd, self.deadline
                )
                self.process = self.supervisor.process
            else:
                self.process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    start_new_session=True,
                    pass_fds=() if write_fd is None else (write_fd,),
                )
        except BaseException:
            if read_fd is not None:
                os.close(read_fd)
            raise
        finally:
            if write_fd is not None:
                os.close(write_fd)
        if group_profile is not None and read_fd is not None:
            self.bridge_stream = os.fdopen(read_fd, "rb", buffering=0)
            self.eof["bridge"] = False
            if self.supervisor is None:
                self.groups = PiGroups(
                    group_profile,
                    cwd,
                    env,
                    model,
                    self.process.pid,
                    self.lifecycle.uncertain,
                )
        self.threads: list[threading.Thread] = []
        if self.supervisor is None:
            self._start_readers()

    def _start_readers(self) -> None:
        for name, stream in [
            ("stdout", self.process.stdout),
            ("stderr", self.process.stderr),
            *(([("bridge", self.bridge_stream)]) if self.bridge_stream else []),
        ]:
            assert stream is not None
            thread = threading.Thread(
                target=self._read, args=(stream, name), daemon=True
            )
            thread.start()
            self.threads.append(thread)

    def _start_supervised(self) -> None:
        if self.supervisor is None or self.group_profile is None:
            return
        pi_pid = self.supervisor.startup(self.deadline)
        self.groups = PiGroups(
            self.group_profile,
            self.cwd,
            self.env,
            self.model,
            pi_pid,
            self.lifecycle.uncertain,
        )
        self._start_readers()

    def _read(self, stream: BinaryIO, name: str) -> None:
        total = count = 0
        buffer = bytearray()
        try:
            while not self.stop.is_set():
                if not select.select([stream], [], [], 0.05)[0]:
                    continue
                raw = os.read(stream.fileno(), 65536)
                if not raw:
                    if buffer and name != "stderr":
                        raise PiRpcError("partial final line")
                    self.eof[name] = True
                    return
                total += len(raw)
                self.stream_bytes[name] = total
                if (
                    total
                    > self.limits[
                        "max_" + ("stdout" if name == "bridge" else name) + "_bytes"
                    ]
                ):
                    raise PiRpcError("stream bound", "output_too_large")
                if name == "stderr":
                    # Validate all bytes, retain only bounded diagnostic text.
                    buffer.extend(raw)
                    continue
                buffer.extend(raw)
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    if len(line) > self.limits["max_event_bytes"]:
                        raise PiRpcError("event bound", "output_too_large")
                    event = _object(strict_json(line.decode("utf-8")))
                    count += 1
                    self.stream_events[name] = count
                    if count > self.limits["max_event_count"]:
                        raise PiRpcError("event count", "output_too_large")
                    if name == "stdout" and self.groups is not None:
                        self.groups.rpc(bytes(line) + b"\n", event)
                    self.events.put_nowait((name, event))
                if len(buffer) > self.limits["max_event_bytes"]:
                    raise PiRpcError("unterminated event bound", "output_too_large")
        except (ValueError, UnicodeError, OSError, queue.Full) as exc:
            reason = str(exc) if isinstance(exc, PiRpcError) else ""
            self.stream_error_codes[name] = {
                "partial final line": "partial_final_line",
                "stream bound": "stream_bound",
                "event bound": "event_bound",
                "event count": "event_count",
                "unterminated event bound": "unterminated_event_bound",
            }.get(
                reason,
                "event_queue_full" if isinstance(exc, queue.Full) else "invalid_stream",
            )
            if self.groups is not None:
                self.groups.integrity = False
                self.lifecycle.uncertain.add("invalid observer channel")
            self.errors.append(
                exc if isinstance(exc, PiRpcError) else PiRpcError("invalid stream")
            )
        finally:
            if name == "stderr":
                try:
                    buffer.decode("utf-8")
                    self.stderr.extend(
                        buffer[: self.limits["max_stderr_diagnostic_bytes"]]
                    )
                except UnicodeError:
                    self.stream_error_codes[name] = "invalid_stderr_utf8"
                    self.errors.append(PiRpcError("invalid stderr UTF-8"))

    def send(self, command: str, **fields: Any) -> str:
        import json

        ident = str(len(self.responses) + len(self.pending)) + ":" + command
        if ident in self.responses or ident in self.pending:
            raise PiRpcError("duplicate request")
        self.pending[ident] = command
        data = (json.dumps({"id": ident, "type": command, **fields}) + "\n").encode()
        if command == "prompt":
            if self.sent_prompt:
                raise PiRpcError("second prompt")
            self.sent_prompt = True
        stream = self.process.stdin
        if stream is None or stream.closed:
            raise PiRpcError("closed stdin")
        # Nonblocking writes obey the same deadline even if Pi stops reading.
        os.set_blocking(stream.fileno(), False)
        while data:
            self.check()
            if select.select([], [stream], [], 0.05)[1]:
                count = os.write(stream.fileno(), data)
                data = data[count:]
        return ident

    def check(self) -> None:
        if self.errors:
            raise self.errors[0]
        if not self.cleanup_draining and time.monotonic() >= self.deadline:
            raise PiRpcError("deadline", "timeout")

    def signal_pi(self, operation: str) -> None:
        if self.supervisor is not None:
            self.supervisor.signal_pi(operation)
        elif self.process.poll() is None:
            if operation == "terminate":
                self.process.terminate()
            else:
                self.process.kill()

    def consume(self) -> None:
        with self.consume_lock:
            self._consume()

    def _consume(self) -> None:
        self.check()
        if self.cancel_requested.is_set() and not self.abort_sent:
            self.abort_sent = True
            self.send("abort")
        try:
            source, event = self.events.get(timeout=0.05)
        except queue.Empty:
            if self.eof["stdout"]:
                raise PiRpcError("premature EOF") from None
            return
        if source == "bridge":
            assert self.groups is not None
            self.groups.record(event)
            return
        if event.get("type") == "response":
            ident = _text(event.get("id"))
            command = self.pending.pop(ident, None)
            if (
                command is None
                or event.get("command") != command
                or ident in self.responses
            ):
                raise PiRpcError("response correlation")
            success = _boolean(event.get("success"))
            if (success and "error" in event) or (
                not success
                and ("data" in event or not isinstance(event.get("error"), str))
            ):
                raise PiRpcError("contradictory response")
            self.responses[ident] = event
            if command == "abort" and event["success"] is True:
                self.abort_ack.set()
        else:
            if not self.sent_prompt:
                raise PiRpcError("event before owned prompt")
            self.lifecycle.event(event)

    def query(self, command: str) -> dict[str, Any]:
        ident = self.send(command)
        return self.response(ident)

    def response(self, ident: str) -> dict[str, Any]:
        while ident not in self.responses:
            self.consume()
        result = self.responses[ident]
        if result["success"] is not True:
            raise PiRpcError("RPC rejected")
        return _object(result.get("data", {}))

    def state(self) -> None:
        state = self.query("get_state")
        identity = _text(state.get("sessionId"))
        if self.pi_session_id is not None and identity != self.pi_session_id:
            raise PiRpcError("Pi session identity drift")
        self.pi_session_id = identity
        if self.groups is not None:
            self.groups.session_id = identity
        model = _object(state.get("model"))
        if any(
            model.get(k) != self.model[v]
            for k, v in {
                "id": "id",
                "provider": "provider",
                "api": "api",
                "baseUrl": "endpoint",
                "contextWindow": "context_window",
                "maxTokens": "max_output_tokens",
            }.items()
        ):
            raise PiRpcError("readiness model drift")
        if (
            state.get("thinkingLevel") != "xhigh"
            or state.get("isStreaming") is not False
            or state.get("isCompacting") is not False
            or type(state.get("pendingMessageCount")) is not int
            or state["pendingMessageCount"] != 0
            or state.get("sessionFile") is not None
        ):
            raise PiRpcError("readiness state")

    def ready(self) -> None:
        self._start_supervised()
        self.state()
        stats = self.query("get_session_stats")
        if stats.get("sessionId") != self.pi_session_id:
            raise PiRpcError("Pi statistics session drift")
        if any(
            type(stats.get(k)) is not int or stats[k] != 0
            for k in ("userMessages", "assistantMessages", "toolCalls", "toolResults")
        ) or token_usage(stats.get("tokens"), allow_zero=True) != AdapterTokenUsage(
            0, 0, 0
        ):
            raise PiRpcError("nonfresh session")

    def run(self) -> str:
        ident = self.send("prompt", message=self.prompt)
        while not self.lifecycle.settled or ident not in self.responses:
            self.consume()
        self.response(ident)
        self.state()
        text = self.query("get_last_assistant_text").get("text")
        stats = self.query("get_session_stats")
        if stats.get("sessionId") != self.pi_session_id:
            raise PiRpcError("Pi statistics session drift")
        self.usage = token_usage(stats.get("tokens"))
        fallback = self.lifecycle.fallback_usage()
        observed_total = sum(u.total_tokens for u in self.lifecycle.usages)
        if self.usage is not None and (
            self.usage.total_tokens < observed_total
            or (fallback is not None and self.usage != fallback)
        ):
            self.usage = None
            raise PiRpcError("usage snapshot conflict")
        final = self.lifecycle.final_text()
        if text != final:
            raise PiRpcError("final text conflict")
        if not self.close():
            raise PiRpcError("incomplete process closure")
        return final

    def close(self) -> bool:
        if self.closed:
            return (
                self.process.returncode == 0
                and all(self.eof.values())
                and not self.errors
                and (self.supervisor is None or self.supervisor.finish())
                and (
                    self.groups is None
                    or self.groups.finish(
                        self.lifecycle.declared,
                        self.lifecycle.completed,
                        self.lifecycle.results,
                    )
                )
            )
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        until = (
            time.monotonic() + 2
            if self.supervisor is not None
            else min(self.deadline, time.monotonic() + 2)
        )
        self.cleanup_draining = self.supervisor is not None
        try:
            while time.monotonic() < until and (
                self.process.poll() is None or not all(self.eof.values())
            ):
                if not self.events.empty():
                    self.consume()
                time.sleep(0.01)
            while not self.events.empty():
                self.consume()
        finally:
            self.cleanup_draining = False
        if self.process.poll() is None or not all(self.eof.values()):
            return False
        self.stop.set()
        for thread in self.threads:
            thread.join(0.2)
        if any(thread.is_alive() for thread in self.threads):
            return False
        for stream in (
            self.process.stdin,
            self.process.stdout,
            self.process.stderr,
            self.bridge_stream,
        ):
            if stream:
                stream.close()
        self.closed = True
        supervisor_complete = self.supervisor is None or self.supervisor.finish()
        if self.supervisor is not None and self.groups is not None:
            self.groups.supervisor_disposed = supervisor_complete
        group_complete = self.groups is None or self.groups.finish(
            self.lifecycle.declared, self.lifecycle.completed, self.lifecycle.results
        )
        return (
            self.process.returncode == 0
            and supervisor_complete
            and not self.errors
            and not self.pending
            and group_complete
        )

    def dispose(self) -> bool:
        """Finite owned parent/reader disposal; never a descendant assertion."""
        if self.process.poll() is None:
            if self.supervisor is None or not self.supervisor.startup_failed:
                try:
                    self.signal_pi("terminate")
                except BrokenPipeError:
                    # The helper may have closed control just before poll. Only
                    # its terminal report, checked below, can prove disposal.
                    pass
            try:
                self.process.wait(0.3 if self.supervisor is not None else 0.2)
            except subprocess.TimeoutExpired:
                if self.supervisor is None or not self.supervisor.startup_failed:
                    try:
                        self.signal_pi("kill")
                    except BrokenPipeError:
                        pass
                try:
                    self.process.wait(1.5 if self.supervisor is not None else 0.5)
                except subprocess.TimeoutExpired:
                    return False
        if self.groups is not None:
            # Drain the finite exit tail even after the invocation deadline. This
            # is disposal evidence, never a continuation of the failed prompt.
            for thread in self.threads:
                thread.join(0.2)
        self.stop.set()
        for thread in self.threads:
            thread.join(0.2)
        if any(thread.is_alive() for thread in self.threads):
            return False
        for stream in (
            self.process.stdin,
            self.process.stdout,
            self.process.stderr,
            self.bridge_stream,
        ):
            if stream and not stream.closed:
                stream.close()
        self.closed = True
        if self.supervisor is not None:
            disposed = self.supervisor.finish()
            if self.groups is not None:
                self.groups.supervisor_disposed = disposed
            return disposed
        if self.groups is not None:
            with self.consume_lock:
                while not self.events.empty():
                    source, event = self.events.get_nowait()
                    if source == "bridge":
                        try:
                            self.groups.record(event)
                        except ValueError:
                            return False
            if not all(self.eof.values()) or self.errors:
                self.groups.integrity = False
            return self.groups.disposed()
        return True
