"""Recovered file-tool errors remain feedback inside a qualified Pi session."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace.adapters.pi_rpc_client import (
    PiLifecycle,
    PiRpcClient,
    PiRpcError,
    parse_candidate,
)
from millrace.adapters.pi_rpc_groups import GroupProfile, PiGroups, environment_digest
from tests.support.pi_rpc import CANDIDATE, MODEL

PROMPT = "Inspect and update the local project."
FINAL_TEXT = json.dumps(CANDIDATE)
ERRORS = [
    (
        "read",
        {"path": "missing.txt"},
        "ENOENT: no such file or directory",
    ),
    (
        "edit",
        {"path": "stale.txt", "edits": [{"oldText": "old", "newText": "new"}]},
        "Could not find the exact text",
    ),
    (
        "write",
        {"path": "locked.txt", "content": "new"},
        "EACCES: permission denied",
    ),
]


def _assistant(content, stop_reason, timestamp):
    return {
        "role": "assistant",
        "content": content,
        "stopReason": stop_reason,
        "timestamp": timestamp,
        "model": MODEL["id"],
        "provider": MODEL["provider"],
        "api": MODEL["api"],
        "usage": {
            "input": 100,
            "output": 10,
            "cacheRead": 20,
            "cacheWrite": 0,
            "totalTokens": 130,
        },
    }


def _events(operations, *, final_text=FINAL_TEXT, final_stop="stop"):
    user = {
        "role": "user",
        "content": [{"type": "text", "text": PROMPT}],
        "timestamp": 1,
    }
    messages = [user]
    events = [
        {"type": "agent_start"},
        {"type": "turn_start"},
        {"type": "message_start", "message": user},
        {"type": "message_end", "message": user},
    ]

    for index, (name, args, content, is_error) in enumerate(operations, start=1):
        call_id = f"call-{index}"
        assistant = _assistant(
            [{"type": "toolCall", "id": call_id, "name": name, "arguments": args}],
            "toolUse",
            index * 4,
        )
        result_content = copy.deepcopy(content)
        result = {
            "role": "toolResult",
            "content": result_content,
            "timestamp": index * 4 + 1,
            "toolCallId": call_id,
            "toolName": name,
            "isError": is_error,
        }
        messages.extend([assistant, result])
        events.extend(
            [
                {"type": "message_start", "message": assistant},
                {"type": "message_end", "message": assistant},
                {
                    "type": "tool_execution_start",
                    "toolCallId": call_id,
                    "toolName": name,
                    "args": copy.deepcopy(args),
                },
                {
                    "type": "tool_execution_end",
                    "toolCallId": call_id,
                    "toolName": name,
                    "isError": is_error,
                    "result": {"content": copy.deepcopy(content)},
                },
                {"type": "message_start", "message": result},
                {"type": "message_end", "message": result},
                {"type": "turn_end", "message": assistant, "toolResults": [result]},
                {"type": "turn_start"},
            ]
        )

    final = _assistant([{"type": "text", "text": final_text}], final_stop, 100)
    messages.append(final)
    events.extend(
        [
            {"type": "message_start", "message": final},
            {"type": "message_end", "message": final},
            {"type": "turn_end", "message": final, "toolResults": []},
            {"type": "agent_end", "messages": messages, "willRetry": False},
            {"type": "agent_settled"},
        ]
    )
    return events


def _file_error_then_recovery(error):
    name, args, text = error
    return [
        (name, args, [{"type": "text", "text": text}], True),
        (
            "write",
            {"path": "fallback.txt", "content": "recovered"},
            [{"type": "text", "text": "Successfully wrote to fallback.txt"}],
            False,
        ),
    ]


def _client(events, tmp_path, *, supervisor_ok=True):
    lifecycle = PiLifecycle(MODEL, PROMPT, group_accounting=True)
    profile = GroupProfile(
        Path("/bridge.mjs"),
        "b" * 64,
        Path("/package"),
        "c" * 64,
        platform="linux",
        supervised=True,
    )
    groups = PiGroups(profile, tmp_path, {}, MODEL, 1234, lifecycle.uncertain)
    sequence = 0

    def record(kind, **payload):
        nonlocal sequence
        groups.record(
            {
                "v": "pi-group-observer.v1",
                "seq": sequence,
                "kind": kind,
                "payload": payload,
            }
        )
        sequence += 1

    record(
        "hello",
        pid=1234,
        cwd=str(tmp_path),
        bridge_sha256="b" * 64,
        profile_sha256="c" * 64,
        env_sha256=environment_digest({}),
        platform="linux",
        node="v24.18.0",
    )
    for event in events:
        lifecycle.event(event)
        raw = (json.dumps(event, separators=(",", ":")) + "\n").encode()
        groups.rpc(raw, event)
        record(
            "rpc",
            index=len(groups.observed_hashes),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    record(
        "bye",
        code=0,
        rpc_count=len(groups.observed_hashes),
        spawn_count=0,
    )
    groups.supervisor_disposed = supervisor_ok

    client = PiRpcClient.__new__(PiRpcClient)
    client.lifecycle = lifecycle
    client.groups = groups
    client.supervisor = SimpleNamespace(finish=lambda: supervisor_ok)
    client.process = SimpleNamespace(returncode=0, poll=lambda: 0)
    client.closed = True
    client.close_failed = False
    client.deadline = time.monotonic() + 60
    client.cancel_requested = threading.Event()
    client.errors = []
    client.pending = {}
    client.threads = []
    client.eof = {"stdout": True, "stderr": True, "bridge": True}
    client.prompt = PROMPT
    client.pi_session_id = "session"
    client.usage = None
    client.responses = {"prompt-id": {}}
    client.send = lambda *_args, **_kwargs: "prompt-id"
    client.response = lambda *_args, **_kwargs: {}
    client.state = lambda: None
    client.query = lambda command: (
        {"text": lifecycle.final_text()}
        if command == "get_last_assistant_text"
        else {
            "sessionId": "session",
            "tokens": {
                "input": 300,
                "cacheRead": 60,
                "cacheWrite": 0,
                "output": 30,
                "total": 390,
            },
        }
    )
    return client, lifecycle, groups


@pytest.mark.parametrize(
    "error", ERRORS, ids=["missing-read", "edit-mismatch", "write-permission"]
)
def test_group_accounting_accepts_recovered_file_error_feedback(error, tmp_path):
    operations = _file_error_then_recovery(error)
    events = _events(operations)
    client, lifecycle, groups = _client(events, tmp_path)

    final = client._run()

    assert parse_candidate(final, 8192) == CANDIDATE
    assert lifecycle.settled == 1
    assert lifecycle.uncertain == set()
    assert lifecycle.results == {"call-1", "call-2"}
    name, args, text = error
    assert lifecycle.declared["call-1"] == (name, args)
    assert lifecycle.completed["call-1"]["isError"] is True
    assert lifecycle.completed["call-1"]["result"]["content"] == [
        {"type": "text", "text": text}
    ]
    assert groups.disposed()
    assert client.close()


@pytest.mark.parametrize(
    "defect",
    [
        "malformed_error_bit",
        "multiple_text_blocks",
        "image_content",
        "unsupported_tool",
        "stale_argument_snapshot",
        "missing_result",
        "contradictory_result_snapshot",
    ],
)
def test_invalid_error_feedback_or_snapshots_still_refuse(defect, tmp_path):
    operation = (
        "read",
        {"path": "missing.txt"},
        [{"type": "text", "text": "ENOENT: no such file or directory"}],
        True,
    )
    events = _events([operation])
    if defect == "malformed_error_bit":
        events[7]["isError"] = 1
        events[8]["isError"] = 1
    elif defect == "multiple_text_blocks":
        events[7]["result"]["content"].append({"type": "text", "text": "extra"})
        events[8]["message"]["content"].append({"type": "text", "text": "extra"})
        events[9]["message"]["content"].append({"type": "text", "text": "extra"})
        events[10]["toolResults"][0]["content"].append(
            {"type": "text", "text": "extra"}
        )
    elif defect == "image_content":
        image = {"type": "image", "mimeType": "image/png", "data": "AAAA"}
        events[7]["result"]["content"] = [image]
        events[8]["message"]["content"] = [image]
        events[9]["message"]["content"] = [image]
        events[10]["toolResults"][0]["content"] = [image]
    elif defect == "unsupported_tool":
        events[4]["message"]["content"][0]["name"] = "search"
        events[5]["message"]["content"][0]["name"] = "search"
        events[6]["toolName"] = "search"
        events[7]["toolName"] = "search"
        events[8]["message"]["toolName"] = "search"
        events[9]["message"]["toolName"] = "search"
        events[10]["toolResults"][0]["toolName"] = "search"
    elif defect == "stale_argument_snapshot":
        events[6]["args"] = {"path": "different.txt"}
    elif defect == "missing_result":
        events[8:10] = []
    else:
        events[8]["message"]["content"][0]["text"] = "contradictory result"
        events[9]["message"]["content"][0]["text"] = "contradictory result"
        events[10]["toolResults"][0]["content"][0]["text"] = "contradictory result"

    with pytest.raises(PiRpcError):
        client, _, _ = _client(events, tmp_path)
        client._run()


def test_invalid_final_candidate_is_not_accepted_after_closed_tool_lifecycle(tmp_path):
    events = _events(
        [
            (
                "read",
                {"path": "missing.txt"},
                [{"type": "text", "text": "ENOENT: no such file or directory"}],
                True,
            ),
            (
                "write",
                {"path": "fallback.txt", "content": "recovered"},
                [{"type": "text", "text": "Successfully wrote to fallback.txt"}],
                False,
            ),
        ],
        final_text="not a candidate",
    )
    client, _, _ = _client(events, tmp_path)

    with pytest.raises(json.JSONDecodeError):
        parse_candidate(client._run(), 8192)


@pytest.mark.parametrize("failure", ["cancelled", "deadline", "supervisor"])
def test_recovered_file_error_does_not_override_runtime_failure_gates(
    failure, tmp_path
):
    events = _events(_file_error_then_recovery(ERRORS[0]))
    client, _, _ = _client(events, tmp_path, supervisor_ok=failure != "supervisor")
    if failure == "cancelled":
        client.cancel_requested.set()
    elif failure == "deadline":
        client.deadline = time.monotonic() - 1

    with pytest.raises(PiRpcError, match="incomplete process closure"):
        client._run()
