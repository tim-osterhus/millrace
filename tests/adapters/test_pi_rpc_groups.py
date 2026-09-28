"""Boundary tests: incomplete observer facts must never qualify bash feedback."""

import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from tests.support.pi_rpc import MODEL


def module():
    assert importlib.util.find_spec("millrace.adapters.pi_rpc_groups"), (
        "private group witness gate is missing"
    )
    return importlib.import_module("millrace.adapters.pi_rpc_groups")


def fixture(monkeypatch, tmp_path):
    m = module()
    monkeypatch.setattr(m, "_group_absent", lambda pid: True)
    profile = m.GroupProfile(
        Path("/package/bridge.mjs"), "b" * 64, Path("/package"), "c" * 64
    )
    uncertain = set()
    gate = m.PiGroups(profile, tmp_path, {}, MODEL, 1234, uncertain)
    gate.session_id = "session"
    records = []

    def emit(kind, **payload):
        event = {
            "v": "pi-group-observer.v1",
            "seq": len(records),
            "kind": kind,
            "payload": payload,
        }
        records.append(event)
        gate.record(event)

    emit(
        "hello",
        pid=1234,
        cwd=str(tmp_path),
        bridge_sha256="b" * 64,
        profile_sha256="c" * 64,
        env_sha256=m.environment_digest(
            {"__CF_USER_TEXT_ENCODING": f"0x{os.getuid():X}:0x0:0x0"}
        ),
        platform="darwin",
        node="v24.18.0",
    )
    args = {"command": "exit 1"}
    start = {
        "type": "tool_execution_start",
        "toolCallId": "call",
        "toolName": "bash",
        "args": args,
    }
    raw = (json.dumps(start, separators=(",", ":")) + "\n").encode()
    gate.rpc(raw, start)
    emit("rpc", index=0, sha256=hashlib.sha256(raw).hexdigest())
    emit(
        "start",
        call="call",
        args_sha256=m.command_digest(args["command"]),
        timeout=None,
    )
    emit(
        "spawn",
        call="call",
        pid=4321,
        command_sha256=m.command_digest("exit 1"),
        cwd=str(tmp_path),
        env_sha256=gate.expected_environment_digest(),
        shell="/bin/bash",
        args=["-c"],
        detached=True,
        stdio=["ignore", "pipe", "pipe"],
        windows_hide=True,
    )
    return gate, emit, records, uncertain, args


def complete(gate, emit, args, *, group="absent", code=1, signal=None):
    emit("root_exit", pid=4321, code=code, signal=signal, group=group)
    for name in ["stdout", "stderr"]:
        emit("stream_end", pid=4321, name=name)
        emit("stream_destroy", pid=4321, name=name, ended=True)
    emit("child_close", pid=4321, code=code, signal=signal)
    end = {
        "type": "tool_execution_end",
        "toolCallId": "call",
        "toolName": "bash",
        "isError": code != 0,
        "result": {"content": [{"type": "text", "text": "ordinary feedback"}]},
    }
    raw = (json.dumps(end, separators=(",", ":")) + "\n").encode()
    gate.rpc(raw, end)
    emit("rpc", index=1, sha256=hashlib.sha256(raw).hexdigest())
    emit("end", call="call")
    emit("bye", code=0, rpc_count=2, spawn_count=1)
    return {"call": ("bash", args)}, {"call": end}, {"call"}


def test_settled_numeric_nonzero_is_feedback(monkeypatch, tmp_path):
    gate, emit, _, uncertain, args = fixture(monkeypatch, tmp_path)
    before = gate.diagnostic()
    assert before["observer_hello"] is True
    assert before["observer_bye"] is False
    assert before["call_sets_match"] is False
    assert before["unretired_spawn_count"] == 1
    assert gate.finish(*complete(gate, emit, args))
    assert gate.disposed() and not uncertain
    after = gate.diagnostic()
    assert after["observer_integrity"] is True
    assert after["observer_bye"] is True
    assert after["rpc_hashes_match"] is True
    assert after["call_sets_match"] is True
    assert after["unretired_spawn_count"] == 0
    monkeypatch.setattr(module(), "_group_absent", lambda pid: False)
    assert gate.diagnostic()["unretired_present_group_count"] == 0
    assert gate.spawns[4321].retired is True


@pytest.mark.parametrize(
    "defect",
    [
        "survivor",
        "signal",
        "destroy",
        "missing_end",
        "duplicate_exit",
        "wrong_call",
        "env",
        "schema",
        "sequence",
        "unknown_control",
    ],
)
def test_corrupt_or_uncertain_witness_never_qualifies(monkeypatch, tmp_path, defect):
    gate, emit, records, uncertain, args = fixture(monkeypatch, tmp_path)
    if defect in ["schema", "sequence", "wrong_call", "env", "duplicate_exit"]:
        if defect == "schema":
            bad = {
                "v": "pi-group-observer.v1",
                "seq": len(records),
                "kind": "root_exit",
                "payload": {"pid": True},
            }
        elif defect == "sequence":
            bad = {**records[-1], "seq": 100}
        elif defect == "wrong_call":
            bad = {
                **records[-1],
                "seq": len(records),
                "payload": {**records[-1]["payload"], "call": "stale"},
            }
        elif defect == "env":
            bad = {
                **records[-1],
                "seq": len(records),
                "payload": {**records[-1]["payload"], "env_sha256": "f" * 64},
            }
        else:
            emit("root_exit", pid=4321, code=0, signal=None, group="absent")
            bad = {**records[-1], "seq": len(records)}
        with pytest.raises(ValueError):
            gate.record(bad)
        assert not gate.disposed()
        return
    if defect == "destroy":
        emit("stream_destroy", pid=4321, name="stdout", ended=False)
    if defect == "unknown_control":
        emit("control", pid=-4321, signal="SIGKILL", frames=[])
    result = complete(
        gate,
        emit,
        args,
        group="present" if defect == "survivor" else "absent",
        code=None if defect == "signal" else 1,
        signal="SIGTERM" if defect == "signal" else None,
    )
    if defect == "missing_end":
        result[2].clear()
    assert not gate.finish(*result)
    assert uncertain


def test_rpc_digest_comparison_is_ordered_and_one_to_one(monkeypatch, tmp_path):
    gate, emit, _, _, args = fixture(monkeypatch, tmp_path)
    result = complete(gate, emit, args)
    raw = b'{"type":"response"}\n'
    gate.rpc(raw, {"type": "response"})
    assert not gate.finish(*result)


def test_survivor_cleanup_retry_never_clears_uncertainty_or_signals(
    monkeypatch, tmp_path
):
    m = module()
    gate, emit, _, uncertain, args = fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "_group_absent", lambda pid: False)
    result = complete(gate, emit, args, group="present")
    assert not gate.finish(*result) and not gate.disposed()
    monkeypatch.setattr(m, "_group_absent", lambda pid: True)
    assert gate.disposed() and uncertain
    monkeypatch.setattr(
        m,
        "_group_absent",
        lambda pid: (_ for _ in ()).throw(AssertionError("retired group probed again")),
    )
    assert gate.disposed()


def test_supervised_survivor_is_accepted_only_after_owned_disposal(
    monkeypatch, tmp_path
):
    gate, emit, _, uncertain, args = fixture(monkeypatch, tmp_path)
    gate.profile = module().GroupProfile(
        gate.profile.bridge,
        gate.profile.bridge_sha256,
        gate.profile.package,
        gate.profile.profile_sha256,
        supervised=True,
    )
    monkeypatch.setattr(module(), "_group_absent", lambda pid: False)
    result = complete(gate, emit, args, group="present")
    assert not gate.disposed()
    assert not uncertain
    gate.supervisor_disposed = True
    assert gate.finish(*result)


def test_supervised_root_signal_remains_execution_uncertainty(monkeypatch, tmp_path):
    gate, emit, _, uncertain, args = fixture(monkeypatch, tmp_path)
    gate.profile = module().GroupProfile(
        gate.profile.bridge,
        gate.profile.bridge_sha256,
        gate.profile.package,
        gate.profile.profile_sha256,
        supervised=True,
    )
    result = complete(gate, emit, args, group="present", code=None, signal="SIGTERM")
    gate.supervisor_disposed = True
    assert not gate.finish(*result)
    assert "signal or surviving group" in uncertain


def test_supervised_unknown_group_probe_remains_uncertain(monkeypatch, tmp_path):
    gate, emit, _, uncertain, args = fixture(monkeypatch, tmp_path)
    gate.profile = module().GroupProfile(
        gate.profile.bridge,
        gate.profile.bridge_sha256,
        gate.profile.package,
        gate.profile.profile_sha256,
        supervised=True,
    )
    gate.supervisor_disposed = True
    assert not gate.finish(*complete(gate, emit, args, group="unknown"))
    assert "signal or surviving group" in uncertain


def test_supervised_zero_spawn_still_requires_supervisor_report(tmp_path):
    m = module()
    profile = m.GroupProfile(
        Path("/package/bridge.mjs"), "b" * 64, Path("/package"), "c" * 64,
        "linux", supervised=True,
    )
    gate = m.PiGroups(profile, tmp_path, {}, MODEL, 1234, set())
    gate.record({
        "v": m.PROTOCOL, "seq": 0, "kind": "hello",
        "payload": {
            "pid": 1234, "cwd": str(tmp_path), "bridge_sha256": "b" * 64,
            "profile_sha256": "c" * 64,
            "env_sha256": m.environment_digest({}),
            "platform": "linux", "node": "v24.18.0",
        },
    })
    gate.record({
        "v": m.PROTOCOL, "seq": 1, "kind": "bye",
        "payload": {"code": 0, "rpc_count": 0, "spawn_count": 0},
    })
    assert not gate.disposed()
    assert not gate.finish({}, {}, set())


def test_darwin_injected_corefoundation_environment_is_verified(monkeypatch, tmp_path):
    import os

    from millrace.adapters.pi_rpc_groups import (
        GroupProfile,
        PiGroups,
        environment_digest,
    )

    profile = GroupProfile(Path("/bridge.mjs"), "b" * 64, Path("/package"), "c" * 64)
    gate = PiGroups(profile, tmp_path, {}, MODEL, 1234, set())
    gate.record(
        {
            "v": "pi-group-observer.v1",
            "seq": 0,
            "kind": "hello",
            "payload": {
                "pid": 1234,
                "cwd": str(tmp_path),
                "bridge_sha256": "b" * 64,
                "profile_sha256": "c" * 64,
                "env_sha256": environment_digest(
                    {"__CF_USER_TEXT_ENCODING": f"0x{os.getuid():X}:0x0:0x0"}
                ),
                "platform": "darwin",
                "node": "v24.18.0",
            },
        }
    )
    assert gate.hello


def test_linux_hello_uses_linux_identity_without_darwin_environment(tmp_path):
    from millrace.adapters.pi_rpc_groups import (
        GroupProfile,
        PiGroups,
        environment_digest,
    )

    profile = GroupProfile(
        Path("/bridge.mjs"), "b" * 64, Path("/package"), "c" * 64, "linux"
    )
    gate = PiGroups(profile, tmp_path, {}, MODEL, 1234, set())
    assert "__CF_USER_TEXT_ENCODING" not in gate.env
    gate.record(
        {
            "v": "pi-group-observer.v1",
            "seq": 0,
            "kind": "hello",
            "payload": {
                "pid": 1234,
                "cwd": str(tmp_path),
                "bridge_sha256": "b" * 64,
                "profile_sha256": "c" * 64,
                "env_sha256": environment_digest({}),
                "platform": "linux",
                "node": "v24.18.0",
            },
        }
    )
    assert gate.hello
