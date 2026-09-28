"""Exercise the packaged preload through real pipes, not synthetic receipts."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from millrace.adapters import pi_rpc_groups as groups


def node():
    path = os.environ.get("MILLRACE_TEST_PI_NODE") or shutil.which("node")
    if path is None:
        pytest.skip("Node unavailable")
    return path


def invoke(tmp_path, script, *, maximum=1000, broken=False):
    bridge = Path(groups.bridge_identity()["path"])
    assert bridge.exists(), "packaged bridge is missing"
    driver = tmp_path / "driver.mjs"
    driver.write_text(script)
    r, w = os.pipe()
    raw = bytearray()
    if broken:
        os.close(r)

    def drain():
        with os.fdopen(r, "rb") as stream:
            raw.extend(stream.read())

    thread = threading.Thread(target=drain) if not broken else None
    if thread:
        thread.start()
    profile = groups.GroupProfile(
        bridge, hashlib.sha256(bridge.read_bytes()).hexdigest(), tmp_path, "a" * 64
    )
    args = profile.argv(
        (node(), str(driver)),
        w,
        {
            "max_event_count": maximum,
            "max_stdout_bytes": 1000000,
            "max_event_bytes": 65536,
        },
    )
    try:
        result = subprocess.run(
            args,
            cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin"},
            pass_fds=(w,),
            capture_output=True,
            timeout=10,
        )
    finally:
        os.close(w)
        if thread:
            thread.join(2)
    assert thread is None or not thread.is_alive()
    return result, [json.loads(x) for x in raw.splitlines()]


def test_packaged_observer_genuine_status_and_private_channel(tmp_path):
    script = """import {spawn} from 'node:child_process';
const command='exit 7';
await new Promise(r=>process.stdout.write(JSON.stringify({
  type:'tool_execution_start',toolCallId:'x',toolName:'bash',args:{command}
})+'\\n',r));
const child=spawn('/bin/bash',['-c',command],{
  cwd:process.cwd(),env:process.env,detached:true,
  stdio:['ignore','pipe','pipe'],windowsHide:true
});
child.stdout.resume();child.stderr.resume();
await new Promise(r=>child.on('close',r));
await new Promise(r=>process.stdout.write(JSON.stringify({
  type:'tool_execution_end',toolCallId:'x',toolName:'bash'
})+'\\n',r));
"""
    result, records = invoke(tmp_path, script)
    assert result.returncode == 0, result.stderr
    assert records[0]["kind"] == "hello" and records[-1]["kind"] == "bye"
    root = next(e["payload"] for e in records if e["kind"] == "root_exit")
    assert (root["code"], root["signal"], root["group"]) == (7, None, "absent")
    assert [e["seq"] for e in records] == list(range(len(records)))
    assert len([e for e in records if e["kind"] == "stream_end"]) == 2


@pytest.mark.parametrize("mode", ["broken", "bound", "crash"])
def test_channel_failure_never_has_complete_positive_tail(tmp_path, mode):
    result, records = invoke(
        tmp_path,
        "if (process.argv) { console.log('{}'); }"
        if mode != "crash"
        else "process.kill(process.pid,'SIGKILL');",
        maximum=1 if mode == "bound" else 1000,
        broken=mode == "broken",
    )
    assert result.returncode != 0
    assert (
        not records
        or records[-1]["kind"] != "bye"
        or records[-1]["payload"]["code"] != 0
    )


def test_group_dispose_drains_exit_witness_without_claiming_execution(tmp_path):
    from millrace.adapters.pi_rpc_client import PiRpcClient
    from tests.support.pi_rpc import LIMITS, MODEL

    executable = node()
    if subprocess.check_output([executable, "--version"]).strip() != b"v24.18.0":
        pytest.skip("client profile requires qualified Node v24.18.0")
    script = tmp_path / "wait.mjs"
    script.write_text(
        "import fs from 'node:fs';process.on('SIGTERM',()=>process.exit(0));"
        "fs.writeFileSync('ready','');setInterval(()=>{},1000);"
    )
    identity = groups.bridge_identity()
    profile = groups.GroupProfile(
        Path(identity["path"]), identity["sha256"], tmp_path, "a" * 64,
        sys.platform,
    )
    client = PiRpcClient(
        (executable, str(script)),
        tmp_path,
        {"PATH": "/usr/bin:/bin"},
        LIMITS,
        MODEL,
        "prompt",
        5,
        group_profile=profile,
    )
    try:
        while not client.groups.hello:
            client.consume()
        until = time.monotonic() + 2
        while not (tmp_path / "ready").exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert (tmp_path / "ready").exists()
        # A failed/cancelled invocation may still prove resource disposal.
        client.lifecycle.uncertain.add("cancelled")
        assert client.dispose()
        assert client.groups.bye and client.closed
        assert "cancelled" in client.lifecycle.uncertain
        assert not client.groups.finish({}, {}, set())
    finally:
        client.dispose()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper")
def test_supervisor_forced_pi_kill_proves_disposal_without_observer_bye(tmp_path):
    from millrace.adapters.pi_rpc_client import PiRpcClient
    from tests.support.pi_rpc import LIMITS, MODEL

    script = tmp_path / "stuck.mjs"
    script.write_text("setInterval(()=>{},1000);")
    identity = groups.bridge_identity()
    supervisor = (
        Path(__file__).parents[2] / "src/millrace/adapters/pi_rpc_supervisor.py"
    )
    profile = groups.GroupProfile(
        Path(identity["path"]),
        identity["sha256"],
        tmp_path,
        "a" * 64,
        "linux",
        supervised=True,
        supervisor=supervisor,
        supervisor_sha256=hashlib.sha256(supervisor.read_bytes()).hexdigest(),
    )
    client = PiRpcClient(
        (node(), str(script)),
        tmp_path,
        {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        LIMITS,
        MODEL,
        "prompt",
        5,
        group_profile=profile,
    )
    try:
        client._start_supervised()
        while not client.groups.hello:
            client.consume()
        client.lifecycle.uncertain.add("cancelled")
        close_outcomes = []

        def close_concurrently():
            try:
                close_outcomes.append(client.close())
            except ValueError as exc:
                close_outcomes.append(exc)

        closer = threading.Thread(target=close_concurrently)
        closer.start()
        client.signal_pi("kill")
        assert client.dispose()
        closer.join(1)
        assert not closer.is_alive()
        assert len(close_outcomes) == 1
        assert client.groups.supervisor_disposed
        assert not client.groups.bye
        assert not client.groups.finish({}, {}, set())
        assert "cancelled" in client.lifecycle.uncertain
        assert client.dispose()
    finally:
        client.dispose()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper")
def test_supervised_observer_accepts_service_after_owned_disposal(tmp_path):
    from millrace.adapters.pi_rpc_supervision import PiSupervisor
    from tests.support.pi_rpc import LIMITS, MODEL

    script = tmp_path / "service.mjs"
    command = "sleep 8 >/dev/null 2>&1 & echo $!"
    script.write_text(
        "import {spawn} from 'node:child_process';"
        f"const command={json.dumps(command)};"
        "const start={type:'tool_execution_start',toolCallId:'x',"
        "toolName:'bash',args:{command}};"
        "await new Promise(r=>process.stdout.write(JSON.stringify(start)+'\\n',r));"
        "const child=spawn('/bin/bash',['-c',command],{"
        "cwd:process.cwd(),env:process.env,detached:true,"
        "stdio:['ignore','pipe','pipe'],windowsHide:true});"
        "child.stdout.resume();child.stderr.resume();"
        "await new Promise(r=>child.on('close',r));"
        "const end={type:'tool_execution_end',toolCallId:'x',"
        "toolName:'bash',isError:false,"
        "result:{content:[{type:'text',text:'ok'}]}};"
        "await new Promise(r=>process.stdout.write(JSON.stringify(end)+'\\n',r));"
    )
    bridge = groups.bridge_identity()
    supervisor_path = (
        Path(__file__).parents[2] / "src/millrace/adapters/pi_rpc_supervisor.py"
    )
    profile = groups.GroupProfile(
        Path(bridge["path"]), bridge["sha256"], tmp_path, "a" * 64,
        "linux", supervised=True, supervisor=supervisor_path,
    )
    env = {
        "PATH": str(tmp_path / "bin") + ":/usr/bin:/bin",
        "LANG": "C", "LC_ALL": "C",
        "PI_CODING_AGENT_DIR": str(tmp_path),
        "PI_CODING_AGENT": "true", "AI_AGENT": "pi",
        "PI_SESSION_ID": "session", "PI_PROVIDER": MODEL["provider"],
        "PI_MODEL": MODEL["id"], "PI_REASONING_LEVEL": "xhigh",
    }
    read_fd, write_fd = os.pipe()
    argv = profile.argv((node(), str(script)), write_fd, LIMITS)
    owner = PiSupervisor(
        supervisor_path, argv, tmp_path, env, write_fd, time.monotonic() + 5
    )
    os.close(write_fd)
    try:
        pi_pid = owner.startup(time.monotonic() + 5)
        gate = groups.PiGroups(profile, tmp_path, env, MODEL, pi_pid, set())
        gate.session_id = "session"
        assert owner.process.wait(timeout=4) == 0
        assert owner.finish()
        assert owner.report is not None
        assert owner.report["reaped_children"] >= 1
        assert owner.process.stdout is not None
        with owner.process.stdout as stdout:
            rpc_lines = stdout.readlines()
        assert owner.process.stderr is not None
        with owner.process.stderr as stderr:
            assert stderr.read() == b""
        with os.fdopen(read_fd, "rb") as bridge_stream:
            observer_lines = bridge_stream.readlines()
        rpc = [json.loads(line) for line in rpc_lines]
        for line, event in zip(rpc_lines, rpc, strict=True):
            gate.rpc(line, event)
        for line in observer_lines:
            gate.record(json.loads(line))
        roots = [json.loads(line)["payload"] for line in observer_lines
                 if json.loads(line)["kind"] == "root_exit"]
        assert len(roots) == 1 and roots[0]["group"] == "present"
        assert not gate.uncertain
        gate.supervisor_disposed = True
        assert gate.finish(
            {"x": ("bash", {"command": command})},
            {"x": rpc[1]},
            {"x"},
        )
    finally:
        if owner.process.poll() is None:
            owner.signal_pi("kill")
            owner.process.wait(timeout=4)
        owner.finish()
        try:
            os.close(read_fd)
        except OSError:
            pass
