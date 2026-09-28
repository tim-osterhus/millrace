"""Deterministic Pi wire fixtures: no Node, provider, shell, or credentials."""

import copy
import json

MODEL = {
    "id": "opaque-model",
    "provider": "local",
    "api": "openai-completions",
    "endpoint": "http://127.0.0.1:43187/v1",
    "context_window": 32000,
    "max_output_tokens": 256,
}
CANDIDATE = {
    "marker": "OPAQUE",
    "artifact_payload_candidate": None,
    "observation_payload_candidate": {"runner_report": "checked secret-value"},
}
LIMITS = dict(
    max_event_count=1000,
    max_event_bytes=8192,
    max_stdout_bytes=65536,
    max_stderr_bytes=8192,
    max_stderr_diagnostic_bytes=1024,
)


def lifecycle(prompt, *, tool=None, result=None, final=None):
    user = dict(role="user", content=[dict(type="text", text=prompt)], timestamp=1)
    messages = [user]
    events = [
        dict(type="agent_start"),
        dict(type="turn_start"),
        dict(type="message_start", message=user),
        dict(type="message_end", message=user),
    ]

    def assistant(content, stop, timestamp):
        return dict(
            role="assistant",
            content=content,
            stopReason=stop,
            timestamp=timestamp,
            model=MODEL["id"],
            provider=MODEL["provider"],
            api=MODEL["api"],
            usage=dict(
                input=100, output=10, cacheRead=20, cacheWrite=0, totalTokens=130
            ),
        )

    if tool:
        name, args = tool
        a = assistant(
            [dict(type="toolCall", id="call-1", name=name, arguments=args)],
            "toolUse",
            2,
        )
        t = dict(
            role="toolResult",
            content=[dict(type="text", text=result)],
            timestamp=3,
            toolCallId="call-1",
            toolName=name,
            isError=False,
        )
        events += [
            dict(type="message_start", message=a),
            dict(type="message_end", message=a),
            dict(
                type="tool_execution_start",
                toolCallId="call-1",
                toolName=name,
                args=args,
            ),
            dict(
                type="tool_execution_end",
                toolCallId="call-1",
                toolName=name,
                isError=False,
                result=dict(content=t["content"]),
            ),
            dict(type="message_start", message=t),
            dict(type="message_end", message=t),
            dict(type="turn_end", message=a, toolResults=[t]),
            dict(type="turn_start"),
        ]
        messages.extend([a, t])
    a = assistant(
        [dict(type="text", text=json.dumps(CANDIDATE) if final is None else final)],
        "stop",
        4,
    )
    messages.append(a)
    events += [
        dict(type="message_start", message=a),
        dict(type="message_end", message=a),
        dict(type="turn_end", message=a, toolResults=[]),
        dict(type="agent_end", messages=messages, willRetry=False),
        dict(type="agent_settled"),
    ]
    return copy.deepcopy(events)
