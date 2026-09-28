"""Audited standard-library fake subprocess. It executes no child commands."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from pi_rpc import CANDIDATE, MODEL, lifecycle

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
prompted = False
if "PI_FAKE_CANDIDATE" in os.environ:
    CANDIDATE.clear()
    CANDIDATE.update(json.loads(os.environ["PI_FAKE_CANDIDATE"]))


def emit(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    command = request["type"]
    data = {}
    if command == "get_state":
        data = dict(
            sessionId="fake-session",
            model=dict(
                id=MODEL["id"],
                provider=MODEL["provider"],
                api=MODEL["api"],
                baseUrl=MODEL["endpoint"],
                contextWindow=MODEL["context_window"],
                maxTokens=MODEL["max_output_tokens"],
            ),
            thinkingLevel="xhigh",
            isStreaming=False,
            isCompacting=False,
            pendingMessageCount=0,
        )
    elif command == "get_session_stats":
        data = dict(
            sessionId="fake-session",
            userMessages=int(prompted),
            assistantMessages=int(prompted),
            toolCalls=0,
            toolResults=0,
            tokens=dict(
                input=100 if prompted else 0,
                output=10 if prompted else 0,
                cacheRead=20 if prompted else 0,
                cacheWrite=0,
                total=130 if prompted else 0,
            ),
        )
    elif command == "get_last_assistant_text":
        data = dict(text=json.dumps(CANDIDATE))
    elif command == "prompt":
        prompted = True
        if mode == "hang":
            continue
        if mode == "flood":
            os.write(1, b"x" * 100000)
            continue
        if mode == "event_count":
            for _ in range(10001):
                emit(dict(type="queue_update", steering=[], followUp=[]))
            continue
        if mode == "event_count_large":
            for _ in range(100001):
                emit(dict(type="queue_update", steering=[], followUp=[]))
        if mode == "bad_utf8":
            os.write(1, b"\xff\n")
            continue
        if mode == "partial":
            os.write(1, b"{")
            break
        if mode == "stderr":
            os.write(2, b"diagnostic\n")
        for event in lifecycle(request["message"]):
            emit(event)
        if mode == "duplicate_settled":
            emit(dict(type="agent_settled"))
    elif command == "abort":
        if mode == "hang":
            continue
    emit(
        dict(
            type="response", id=request["id"], command=command, success=True, data=data
        )
    )
    if mode == "duplicate_response" and command == "get_state":
        emit(
            dict(
                type="response",
                id=request["id"],
                command=command,
                success=True,
                data=data,
            )
        )
if mode == "hold_exit":
    time.sleep(30)
