"""Pi 0.87.0 tool-result shapes at the lifecycle qualification boundary.

These events are derived from the frozen Pi 0.87.0 implementation retained in
the C20 repair packet. They are not a replay of C20's unretained raw events.
"""

import json

import pytest

from millrace.adapters.pi_rpc_client import PiLifecycle, PiRpcError

MODEL = {"id": "bonsai2-27b-pq2", "provider": "local", "api": "openai-completions"}
PROMPT = "Inspect and update the local project."
FINAL_TEXT = json.dumps(
    {
        "marker": "OPAQUE",
        "artifact_payload_candidate": None,
        "observation_payload_candidate": {"runner_report": "updated"},
    }
)


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
            "input": 10,
            "output": 2,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": 12,
        },
    }


def _events_for_edit(args, result_text, *, is_error=False):
    user = {
        "role": "user",
        "content": [{"type": "text", "text": PROMPT}],
        "timestamp": 1,
    }
    tool_call = _assistant(
        [{"type": "toolCall", "id": "edit-1", "name": "edit", "arguments": args}],
        "toolUse",
        2,
    )
    result_content = [{"type": "text", "text": result_text}]
    tool_result = {
        "role": "toolResult",
        "content": result_content,
        "timestamp": 3,
        "toolCallId": "edit-1",
        "toolName": "edit",
        "isError": is_error,
    }
    final = _assistant([{"type": "text", "text": FINAL_TEXT}], "stop", 4)
    messages = [user, tool_call, tool_result, final]
    return [
        {"type": "agent_start"},
        {"type": "turn_start"},
        {"type": "message_start", "message": user},
        {"type": "message_end", "message": user},
        {"type": "message_start", "message": tool_call},
        {"type": "message_end", "message": tool_call},
        {
            "type": "tool_execution_start",
            "toolCallId": "edit-1",
            "toolName": "edit",
            "args": args,
        },
        {
            "type": "tool_execution_end",
            "toolCallId": "edit-1",
            "toolName": "edit",
            "isError": is_error,
            "result": {"content": result_content},
        },
        {"type": "message_start", "message": tool_result},
        {"type": "message_end", "message": tool_result},
        {"type": "turn_end", "message": tool_call, "toolResults": [tool_result]},
        {"type": "turn_start"},
        {"type": "message_start", "message": final},
        {"type": "message_end", "message": final},
        {"type": "turn_end", "message": final, "toolResults": []},
        {"type": "agent_end", "messages": messages, "willRetry": False},
        {"type": "agent_settled"},
    ]


def _parse(events, *, group_accounting=True):
    parser = PiLifecycle(MODEL, PROMPT, group_accounting=group_accounting)
    for event in events:
        parser.event(event)
    return parser


def test_ordinary_edit_array_result_closes_the_tool_and_agent():
    args = {
        "path": "src/example.py",
        "edits": [{"oldText": "before", "newText": "after"}],
    }
    parser = _parse(
        _events_for_edit(args, "Successfully replaced 1 block(s) in src/example.py.")
    )

    assert parser.settled == 1
    assert parser.results == {"edit-1"}
    assert parser.uncertain == set()
    assert parser.final_text() == FINAL_TEXT


def test_pinned_pi_mixed_legacy_edit_pair_qualifies_its_successful_closure():
    # Pi's frozen edit.js appends the top-level legacy pair to `edits` and
    # returns a new arguments object. Its agent loop emits tool_execution_start
    # with the original arguments before that preparation, while the successful
    # result counts the prepared array. Therefore this source-derived event has
    # one original edit and a two-block success result.
    args = {
        "path": "src/example.py",
        "edits": [{"oldText": "before", "newText": "after"}],
        "oldText": "old value",
        "newText": "new value",
    }
    parser = _parse(
        _events_for_edit(args, "Successfully replaced 2 block(s) in src/example.py.")
    )

    assert parser.settled == 1
    assert parser.results == {"edit-1"}
    assert parser.uncertain == set()
    assert parser.final_text() == FINAL_TEXT


def test_legacy_failed_edit_result_remains_uncertain_after_agent_settlement():
    args = {
        "path": "src/example.py",
        "edits": [{"oldText": "stale", "newText": "new value"}],
    }
    parser = _parse(
        _events_for_edit(args, "Could not apply the requested edit.", is_error=True),
        group_accounting=False,
    )

    assert parser.settled == 1
    assert parser.results == {"edit-1"}
    assert parser.final_text() == FINAL_TEXT
    assert parser.uncertain == {"unqualified tool result"}


@pytest.mark.parametrize("defect", ["stale_result_id", "missing_result_message"])
def test_stale_or_incomplete_edit_result_cannot_close_the_turn(defect):
    args = {
        "path": "src/example.py",
        "edits": [{"oldText": "before", "newText": "after"}],
    }
    events = _events_for_edit(
        args, "Successfully replaced 1 block(s) in src/example.py."
    )
    if defect == "stale_result_id":
        events[9]["message"]["toolCallId"] = "stale-edit"
    else:
        events.pop(9)

    with pytest.raises(PiRpcError):
        _parse(events)
