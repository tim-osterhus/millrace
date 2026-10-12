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
recovery_tail = None
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
        recovery = mode in {"retry", "compaction", "retry_hang", "compaction_hang"}
        factor = 2 if prompted and recovery else int(prompted)
        data = dict(
            sessionId="fake-session",
            userMessages=int(prompted),
            assistantMessages=int(prompted),
            toolCalls=0,
            toolResults=0,
            tokens=dict(
                input=100 * factor,
                output=10 * factor,
                cacheRead=20 * factor,
                cacheWrite=0,
                total=130 * factor,
            ),
        )
    elif command == "get_last_assistant_text":
        data = dict(text=json.dumps(CANDIDATE))
    elif command == "prompt":
        prompted = True
        if mode in {"retry", "compaction", "retry_hang", "compaction_hang"}:
            # Scripted source-order fixtures, not a native-provider observation.
            emit(dict(type="response", id=request["id"], command=command, success=True))
            if mode.startswith("compaction"):
                events = lifecycle(
                    request["message"], tool=("read", {"path": "opaque"}), result="text"
                )
                for event in events[:11]:
                    emit(event)
                recovery_tail = events[11:]
                emit(dict(type="compaction_start", reason="threshold"))
                if mode.endswith("hang"):
                    continue
                emit(
                    dict(
                        type="compaction_end",
                        reason="threshold",
                        aborted=False,
                        willRetry=False,
                        result={"summary": "scripted summary"},
                    )
                )
                for event in events[11:]:
                    emit(event)
            else:
                first = lifecycle(request["message"])[:-1]
                first[-1]["willRetry"] = True
                for event in first:
                    if (
                        event["type"] == "message_end"
                        and event["message"]["role"] == "assistant"
                    ):
                        event["message"]["stopReason"] = "error"
                    emit(event)
                emit(
                    dict(
                        type="auto_retry_start",
                        attempt=1,
                        maxAttempts=3,
                        delayMs=1,
                        errorMessage="scripted provider error",
                    )
                )
                if mode.endswith("hang"):
                    continue
                second = lifecycle(request["message"])
                second = second[:2] + second[4:]
                second[-2]["messages"] = second[-2]["messages"][1:]
                second.insert(4, dict(type="auto_retry_end", success=True, attempt=1))
                for event in second:
                    emit(event)
            continue
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
        if mode == "compaction_hang":
            emit(
                dict(
                    type="compaction_end",
                    reason="threshold",
                    aborted=True,
                    willRetry=False,
                )
            )
            # Cancellation during prepareNextTurn still opens the next turn;
            # its aborted assistant ends that turn and the existing agent run.
            recovery_tail[-4]["message"]["stopReason"] = "aborted"
            for event in recovery_tail:
                emit(event)
        elif mode == "retry_hang":
            emit(
                dict(
                    type="auto_retry_end",
                    success=False,
                    attempt=1,
                    finalError="Retry cancelled",
                )
            )
            emit(dict(type="agent_settled"))
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
