"""The Linux Pi supervisor owns and reaps detached Bash descendants."""

import json
import os
import select
import signal
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper")


def _launch(command: str, *, ignore_sigchld: bool = False):
    status_read, status_write = os.pipe()
    control_read, control_write = os.pipe()
    command = command.replace("{status_fd}", str(status_write)).replace(
        "{control_fd}", str(control_read)
    ).replace("{status_inode}", str(os.fstat(status_write).st_ino)).replace(
        "{control_inode}", str(os.fstat(control_read).st_ino)
    )

    def inherited_ignore() -> None:
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "millrace.adapters.pi_rpc_supervisor",
            str(status_write),
            str(control_read),
            str(os.getpid()),
            "-1",
            "/bin/bash",
            "-c",
            command,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(status_write, control_read),
        preexec_fn=inherited_ignore if ignore_sigchld else None,
        start_new_session=True,
    )
    os.close(status_write)
    os.close(control_read)
    return process, os.fdopen(status_read, "r"), control_write


def _finish(process, status, control, *, timeout: float = 4):
    try:
        assert process.stdin is not None
        process.stdin.close()
        assert process.wait(timeout=timeout) == 0
        reports = [json.loads(line) for line in status]
        assert [report["kind"] for report in reports] == ["ready", "complete"]
        assert reports[1]["pi_returncode"] == 0
        assert reports[1]["disposed"] is True
        return reports
    finally:
        os.close(control)
        status.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


@pytest.mark.parametrize("command", ["exit 0", "sleep 0.05 & wait"])
def test_supervisor_normal_completion_and_reaped_children(command):
    process, status, control = _launch(command)
    reports = _finish(process, status, control)
    assert reports[0]["pi_pid"] > 0
    assert reports[1]["remaining_children"] == 0


def test_supervisor_reaps_service_that_outlives_shell():
    process, status, control = _launch("sleep 8 >/dev/null 2>&1 & echo $!")
    assert process.stdout is not None
    service = int(process.stdout.readline().strip())
    reports = _finish(process, status, control)
    assert reports[1]["reaped_children"] >= 1
    with pytest.raises(ProcessLookupError):
        os.kill(service, 0)


def test_supervisor_escalates_term_immune_service_and_leaves_unrelated_process():
    unrelated = subprocess.Popen(["sleep", "8"], start_new_session=True)
    process, status, control = _launch(
        "( trap '' TERM; end=$((SECONDS+8)); "
        "while ((SECONDS<end)); do sleep 0.1; done ) >/dev/null 2>&1 & echo $!"
    )
    try:
        assert process.stdout is not None
        service = int(process.stdout.readline().strip())
        reports = _finish(process, status, control)
        assert reports[1]["kill_count"] >= 1
        assert unrelated.poll() is None
        with pytest.raises(ProcessLookupError):
            os.kill(service, 0)
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=2)


def test_supervisor_resets_inherited_sigchld_ignore():
    process, status, control = _launch(
        "sleep 8 >/dev/null 2>&1 & echo $!", ignore_sigchld=True
    )
    assert process.stdout is not None
    service = int(process.stdout.readline().strip())
    reports = _finish(process, status, control)
    assert reports[1]["reaped_children"] >= 1
    with pytest.raises(ProcessLookupError):
        os.kill(service, 0)


def test_supervisor_control_eof_cancels_inner_process():
    process, status, control = _launch("sleep 8")
    try:
        ready = json.loads(status.readline())
        assert ready["kind"] == "ready"
        os.close(control)
        assert process.wait(timeout=4) != 0
        complete = json.loads(status.readline())
        assert complete["kind"] == "complete"
        assert complete["disposed"] is True
        assert complete["pi_returncode"] != 0
    finally:
        status.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_broken_ready_report_drains_inner_process():
    process, status, control = _launch("sleep 8")
    status.close()
    os.close(control)
    try:
        assert process.wait(timeout=4) == 2
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 1)[0]
        assert process.stdout.read() == b""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_private_report_and_control_fds_do_not_reach_inner_shell():
    process, status, control = _launch(
        "for item in '{status_fd}:{status_inode}' '{control_fd}:{control_inode}'; "
        "do fd=${item%%:*}; inode=${item#*:}; "
        "if [ \"$(readlink /proc/self/fd/$fd 2>/dev/null)\" = "
        "\"pipe:[$inode]\" ]; then exit 42; fi; done"
    )
    reports = _finish(process, status, control)
    assert reports[1]["pi_returncode"] == 0


def test_supervisor_refuses_missing_pidfd_before_fork():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from millrace.adapters import pi_rpc_supervisor as m;"
            "import os;"
            "m._require_pidfd=lambda: (_ for _ in ()).throw(OSError('unsupported'));"
            "m.os.fork=lambda: (_ for _ in ()).throw(AssertionError('forked'));"
            "fd=os.open(os.devnull,os.O_RDWR);"
            "\ntry:\n m._run(fd,fd,os.getppid(),-1,('/bin/true',))"
            "\nexcept OSError: pass"
            "\nelse: raise AssertionError('admitted')",
        ],
        capture_output=True,
        timeout=3,
    )
    assert probe.returncode == 0, probe.stderr


def test_supervisor_never_signals_pi_after_child_ownership_lost():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from millrace.adapters import pi_rpc_supervisor as m;"
            "import os;"
            "m._require_pidfd=lambda: None;"
            "m.os.fork=lambda: 1234567;"
            "m.os.waitpid=lambda *a: (_ for _ in ()).throw(ChildProcessError());"
            "m.os.kill=lambda *a: (_ for _ in ()).throw(AssertionError('signalled'));"
            "a=os.open(os.devnull,os.O_RDWR);"
            "b=os.open(os.devnull,os.O_RDWR);"
            "assert m._run(a,b,os.getppid(),-1,('/bin/true',)) == 2",
        ],
        capture_output=True,
        timeout=3,
    )
    assert probe.returncode == 0, probe.stderr


def test_child_listing_overflow_returns_bounded_complete_chunk(monkeypatch):
    import io

    from millrace.adapters import pi_rpc_supervisor as helper

    children = list(range(100000, 100700))
    raw = " ".join(map(str, children))
    monkeypatch.setattr("builtins.open", lambda *a, **k: io.StringIO(raw))
    assert helper._children() == children[:256]
