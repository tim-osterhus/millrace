"""Only a selected guarded v3 timeout may qualify a signalled stock Bash call."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.adapters.pi_rpc_groups import (
    BUNDLE,
    GroupProfile,
    PiGroups,
    command_digest,
    environment_digest,
)
from tests.support.pi_rpc import MODEL


def transcript(tmp_path, defect=None, requested=10):
    profile = GroupProfile(
        Path("/bridge"), "b" * 64, Path("/package"), "c" * 64, "linux", supervised=True
    )
    # Exercise the new selected capability against the original frozen gate, too.
    object.__setattr__(profile, "bash_timeout", True)
    if defect == "unguarded":
        object.__setattr__(profile, "bash_timeout", False)
    if defect == "v2":
        profile = replace(profile, supervised=False)
        object.__setattr__(profile, "bash_timeout", True)
    gate = PiGroups(profile, tmp_path, {}, MODEL, 1234, set())
    gate.session_id = "session"
    gate.supervisor_disposed = defect != "supervisor_incomplete"
    index = 0

    def emit(kind, **payload):
        nonlocal index
        gate.record(
            dict(v="pi-group-observer.v1", seq=index, kind=kind, payload=payload)
        )
        index += 1

    def rpc(event):
        raw = (json.dumps(event, separators=(",", ":")) + "\n").encode()
        gate.rpc(raw, event)
        emit(
            "rpc",
            index=len(gate.observed_hashes),
            sha256=hashlib.sha256(raw).hexdigest(),
        )

    emit(
        "hello",
        pid=1234,
        cwd=str(tmp_path),
        bridge_sha256="b" * 64,
        profile_sha256="c" * 64,
        env_sha256=environment_digest({}),
        platform="linux",
        node="v24.18.0",
    )
    args = {"command": "sleep 600"}
    if requested is not None:
        args["timeout"] = requested
    start = dict(
        type="tool_execution_start", toolCallId="call", toolName="bash", args=args
    )
    rpc(start)
    emit(
        "start",
        call="call",
        args_sha256=command_digest(args["command"]),
        timeout=requested,
    )
    emit(
        "spawn",
        call="call",
        pid=4321,
        command_sha256=command_digest(args["command"]),
        cwd=str(tmp_path),
        env_sha256=gate.expected_environment_digest(),
        shell="/bin/bash",
        args=["-c"],
        detached=True,
        stdio=["ignore", "pipe", "pipe"],
        windows_hide=True,
    )
    frame = dict(file=(profile.package / BUNDLE).as_uri(), line=611, column=1791)
    caller = dict(file=frame["file"], line=1107, column=615)
    if defect == "abort":
        caller["column"] = 509
    if defect == "unknown_caller":
        caller["column"] = 616
    control = dict(pid=-4321, signal="SIGKILL", frames=[frame, caller])
    if defect == "positive_pid":
        control["pid"] = 4321
    if defect == "wrong_pid":
        control["pid"] = -9999
    if defect == "wrong_signal":
        control["signal"] = "SIGTERM"
    if defect == "control_after_end":
        emit("end", call="call")
    if defect != "missing_control":
        emit("control", **control)
    if defect == "duplicate_control":
        emit("control", **control)
    emit(
        "root_exit",
        pid=4321,
        code=0 if defect == "numeric_exit" else None,
        signal=None if defect == "numeric_exit" else "SIGKILL",
        group="unknown" if defect == "unknown_group" else "present",
    )
    for name in ("stdout", "stderr"):
        if defect == "premature_destroy" and name == "stdout":
            emit("stream_destroy", pid=4321, name=name, ended=False)
        if defect == "missing_stream" and name == "stdout":
            continue
        emit("stream_end", pid=4321, name=name)
        emit("stream_destroy", pid=4321, name=name, ended=True)
    if defect != "missing_close":
        emit(
            "child_close",
            pid=4321,
            code=0 if defect == "numeric_exit" else None,
            signal=None if defect == "numeric_exit" else "SIGKILL",
        )
    seconds = min(requested, 1800) if requested is not None else 1800
    if defect == "wrong_seconds":
        seconds += 1
    end = dict(
        type="tool_execution_end",
        toolCallId="call",
        toolName="bash",
        isError=True,
        result={
            "content": [
                {"type": "text", "text": f"Command timed out after {seconds} seconds"}
            ]
        },
    )
    if defect == "wrong_error":
        end["isError"] = False
    if defect == "wrong_text":
        end["result"]["content"][0]["text"] = "Command aborted"
    rpc(end)
    if defect != "control_after_end":
        emit("end", call="call")
    declared, completed, results = {"call": ("bash", args)}, {"call": end}, {"call"}
    if defect == "unknown_control_other_call":
        other = {"command": "true"}
        rpc(
            dict(
                type="tool_execution_start",
                toolCallId="other",
                toolName="bash",
                args=other,
            )
        )
        emit("start", call="other", args_sha256=command_digest("true"), timeout=None)
        emit(
            "spawn",
            call="other",
            pid=8888,
            command_sha256=command_digest("true"),
            cwd=str(tmp_path),
            env_sha256=gate.expected_environment_digest(),
            shell="/bin/bash",
            args=["-c"],
            detached=True,
            stdio=["ignore", "pipe", "pipe"],
            windows_hide=True,
        )
        emit("control", pid=-8888, signal="SIGKILL", frames=[])
        emit("root_exit", pid=8888, code=0, signal=None, group="absent")
        for name in ("stdout", "stderr"):
            emit("stream_end", pid=8888, name=name)
        emit("child_close", pid=8888, code=0, signal=None)
        other_end = dict(
            type="tool_execution_end",
            toolCallId="other",
            toolName="bash",
            isError=False,
            result={"content": [{"type": "text", "text": "(no output)"}]},
        )
        rpc(other_end)
        emit("end", call="other")
        declared["other"], completed["other"] = ("bash", other), other_end
        results.add("other")
    if defect != "missing_bye":
        emit(
            "bye",
            code=0,
            rpc_count=len(gate.observed_hashes),
            spawn_count=len(gate.spawns),
        )
    if defect == "rpc_mismatch":
        gate.rpc_hashes.reverse()
    if defect == "unrelated_uncertainty":
        gate.uncertain.add("unqualified unrelated call")
    return gate, (declared, completed, results)


@pytest.mark.parametrize(
    "requested", [10, 10.0, 1800, None, 7200, 0.5, 1.123456789, 1e-7]
)
def test_selected_guarded_v3_timeout_is_feedback(tmp_path, requested):
    gate, facts = transcript(tmp_path, requested=requested)
    assert gate.finish(*facts)
    assert gate.disposed() and not gate.uncertain


@pytest.mark.parametrize(
    "defect",
    [
        "unguarded",
        "v2",
        "positive_pid",
        "wrong_pid",
        "wrong_signal",
        "abort",
        "unknown_caller",
        "duplicate_control",
        "control_after_end",
        "missing_control",
        "numeric_exit",
        "wrong_seconds",
        "missing_stream",
        "missing_close",
        "premature_destroy",
        "unknown_group",
        "missing_bye",
        "rpc_mismatch",
        "supervisor_incomplete",
        "unrelated_uncertainty",
        "unknown_control_other_call",
        "wrong_error",
        "wrong_text",
    ],
)
def test_guard_never_qualifies_incomplete_or_hostile_timeout(tmp_path, defect):
    gate, facts = transcript(tmp_path, defect)
    assert not gate.finish(*facts)
    assert gate.uncertain


def test_lifecycle_refuses_nontext_timeout_result_before_group_qualification():
    from millrace.adapters.pi_rpc_client import PiLifecycle
    from tests.support.pi_rpc import lifecycle

    events = lifecycle(
        "prompt",
        tool=("bash", {"command": "sleep 600", "timeout": 10}),
        result="Command timed out after 10 seconds",
    )
    end = next(e for e in events if e["type"] == "tool_execution_end")
    end["result"]["content"][0].update(
        type="image", data="opaque", mimeType="image/png"
    )
    parser = PiLifecycle(MODEL, "prompt", group_accounting=True)
    for event in events:
        parser.event(event)
    assert "unqualified tool result" in parser.uncertain
