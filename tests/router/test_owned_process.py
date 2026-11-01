"""Public byte-process contract: timing, fair delivery and owned aftermath."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from millrace.router.process import (
    OWNED_PROCESS_CONTRACT,
    OwnedProcessError,
    ProcessRequest,
    run_owned_process,
)

PEER = Path(__file__).parent / "fixtures/owned_process_peer.py"


def request(tmp: Path, code: str = "pass", **kwargs) -> ProcessRequest:
    values = dict(
        argv=(sys.executable, "-c", code), cwd=tmp, env={}, stdin=None,
        deadline=time.time() + 2, stdin_limit=1048576,
        stdout_capture_limit=65536, stderr_capture_limit=65536,
        stdout_fail_limit=None, stderr_fail_limit=None,
    )
    values.update(kwargs)
    return ProcessRequest(**values)


def run(req: ProcessRequest, **kwargs):
    return run_owned_process(
        req, poll=kwargs.pop("poll", lambda: False),
        on_started=kwargs.pop("on_started", lambda _: None), **kwargs,
    )


def test_request_validation_and_frozen_environment(tmp_path) -> None:
    assert OWNED_PROCESS_CONTRACT == "owned-process-v1"
    env = {"VALUE": "captured"}
    req = request(tmp_path, env=env)
    env["VALUE"] = "changed"
    assert req.env["VALUE"] == "captured"
    assert request(tmp_path, argv=("executable", "")).argv[-1] == ""
    for change in (
        {"argv": ()}, {"argv": ("",)}, {"argv": ("a\x00b",)},
        {"env": {"a=b": "c"}},
        {"stdin_limit": 0}, {"stdout_capture_limit": True},
        {"stderr_fail_limit": float("inf")}, {"deadline": float("nan")},
        {"stdin": b"xx", "stdin_limit": 1}, {"cleanup_on_root_exit": 1},
    ):
        with pytest.raises(ValueError):
            request(tmp_path, **change)


def test_single_budget_postcallback_and_repeated_cancel(tmp_path) -> None:
    roots = []
    expired = run(request(tmp_path, deadline=time.time() - 1), on_started=roots.append)
    assert expired.stop_cause == "timeout" and roots == []
    cancelled = run(request(tmp_path), poll=lambda: True, on_started=roots.append)
    assert cancelled.stop_cause == "cancelled" and roots == []

    def slow_started(_):
        time.sleep(0.12)

    result = run(request(tmp_path, "import time;time.sleep(30)",
                         deadline=time.time() + 0.05), on_started=slow_started)
    assert result.stop_cause == "timeout" and result.cleanup_confirmed


def test_stdin_pressure_and_callback_failure_cleanup(tmp_path) -> None:
    out, err = bytearray(), bytearray()
    req = request(tmp_path, argv=(sys.executable, str(PEER), "pressure"),
                  stdin=b"p" * 1048576, deadline=time.time() + 5)
    result = run(req, on_stdout=out.extend, on_stderr=err.extend)
    assert result.stop_cause is None and result.cleanup_confirmed
    assert result.stdin_delivered and result.stdin_closed
    assert result.stdout_bytes == len(out) and result.stderr_bytes == len(err)
    assert out.endswith(b"1048576")
    assert result.stdout_sha256 == hashlib.sha256(out).hexdigest()
    assert result.stderr_sha256 == hashlib.sha256(err).hexdigest()
    assert len(result.stdout) == len(result.stderr) == 65536
    error = RuntimeError("inert callback error")

    def failed(_):
        raise error

    with pytest.raises(OwnedProcessError) as caught:
        run(request(tmp_path, "import time;time.sleep(30)"), on_started=failed)
    assert caught.value.__cause__ is error
    assert caught.value.result.stop_cause == "callback_failed"
    assert caught.value.result.cleanup_confirmed
    with pytest.raises(KeyboardInterrupt) as interrupted:
        run(request(tmp_path, "import time;time.sleep(30)"),
            on_started=lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert type(interrupted.value) is KeyboardInterrupt


def test_root_eof_group_and_cleanup_uncertainty(tmp_path, monkeypatch) -> None:
    roots = []
    killpg = os.killpg

    def denied(pid, sig):
        raise PermissionError("test-owned denied probe")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "killpg", denied)
            result = run(request(tmp_path, "import time;time.sleep(30)",
                                 deadline=time.time() + 0.05), on_started=roots.append)
        assert result.stop_cause == "timeout"
        assert result.group_state == "unknown" and not result.cleanup_confirmed
        assert result.cleanup_forced
    finally:
        if roots:
            try:
                killpg(roots[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(roots[0], 0)


def root_cleanup(
    tmp_path, monkeypatch, mode="root-tail", poll=lambda: False, on_stop=None,
):
    order, raw = [], bytearray()
    killpg = os.killpg

    def signal_group(pid, sig):
        if sig:
            order.append(("signal", sig, time.monotonic()))
        return killpg(pid, sig)

    def drained(code):
        assert code == 0
        order.append(("snapshot", len(raw), time.monotonic()))

    monkeypatch.setattr(os, "killpg", signal_group)
    try:
        result = run(request(tmp_path, argv=(sys.executable, str(PEER), mode),
                             cleanup_on_root_exit=True, deadline=time.time() + 10),
                     on_stdout=raw.extend, on_drained=drained, poll=poll,
                     on_stop=on_stop)
        return result, order, raw
    finally:
        pidfile = tmp_path / "child.pid"
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_on_drained_buffered_tail_precedes_root_cleanup(tmp_path, monkeypatch) -> None:
    stops = []
    result, order, raw = root_cleanup(tmp_path, monkeypatch, on_stop=stops.append)
    assert len(raw) > 8192
    assert raw.count(b"\n") == 64
    assert order[0][:2] == ("snapshot", len(raw))
    assert sum(item[0] == "snapshot" for item in order) == 1
    assert any(item[:2] == ("signal", signal.SIGTERM) for item in order)
    assert result.cleanup_forced and result.stop_cause is None
    assert stops == []


def test_on_drained_requires_root_and_eof(tmp_path) -> None:
    started = time.monotonic()
    called = []
    result = run(request(tmp_path, argv=(sys.executable, str(PEER), "eof-first")),
                 on_drained=lambda code: called.append((code, time.monotonic())))
    assert result.cleanup_confirmed
    assert len(called) == 1 and called[0][1] - started >= 0.1


def test_on_drained_never_runs_after_forced_cleanup(tmp_path, monkeypatch) -> None:
    result, order, _ = root_cleanup(tmp_path, monkeypatch, "root-pipe")
    assert result.cleanup_forced
    assert all(item[0] != "snapshot" for item in order)


def test_root_exit_and_failure_share_cleanup_clock(tmp_path, monkeypatch) -> None:
    drained = []

    def denied(*_):
        raise PermissionError("test-owned denied closure and signals")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "killpg", denied)
            result = run(request(
                tmp_path, argv=(sys.executable, str(PEER), "root-tail"),
                cleanup_on_root_exit=True, deadline=time.time() + 15,
            ), on_drained=lambda _: drained.append(time.monotonic()),
                poll=lambda: bool(drained) and time.monotonic() - drained[0] >= 4)
        assert len(drained) == 1 and result.stop_cause == "cancelled"
        assert result.group_state == "unknown" and not result.cleanup_confirmed
        # Resetting the clock at the later cancel would return about 9s later.
        assert 4.5 <= time.monotonic() - drained[0] < 6.5
    finally:
        pidfile = tmp_path / "child.pid"
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_on_drained_callback_deadline_and_error_cleanup(tmp_path) -> None:
    called = []

    def slow(_):
        called.append(True)
        time.sleep(0.6)

    result = run(request(tmp_path, deadline=time.time() + 0.5), on_drained=slow)
    assert called == [True] and result.stop_cause == "timeout"
    assert result.root_reaped and result.stdout_eof and result.stderr_eof
    with pytest.raises(OwnedProcessError) as caught:
        run(request(tmp_path), on_drained=lambda _: (_ for _ in ()).throw(ValueError()))
    assert caught.value.result.cleanup_confirmed
    interrupt = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as caught:
        run(request(tmp_path), on_drained=lambda _: (_ for _ in ()).throw(interrupt))
    assert caught.value is interrupt


def test_root_before_natural_eof_notifies_without_force(tmp_path) -> None:
    roots, snapshots, chunks = [], [], []

    def stdout(raw):
        with pytest.raises(ProcessLookupError):
            os.kill(roots[0], 0)
        chunks.append(raw)

    result = run(request(
        tmp_path, argv=(sys.executable, str(PEER), "root-late-eof"),
    ), on_started=roots.append, on_stdout=stdout, on_drained=snapshots.append)
    assert snapshots == [0] and b"".join(chunks) == b"tail"
    assert result.cleanup_confirmed and not result.cleanup_forced


def test_closed_reaped_group_is_never_probed_or_signalled_again(tmp_path, monkeypatch):
    killpg = os.killpg
    closed = []

    def probe(pid, sig):
        assert not closed, "reused numeric group was accessed after closure"
        try:
            return killpg(pid, sig)
        except ProcessLookupError:
            closed.append(pid)
            raise

    monkeypatch.setattr(os, "killpg", probe)
    result = run(request(
        tmp_path, argv=(sys.executable, str(PEER), "detached-tail"),
    ), poll=lambda: bool(closed))
    assert closed == [result.pid] and result.stop_cause == "cancelled"
    assert result.stdout == b"tail" and result.cleanup_confirmed
    assert not result.cleanup_forced


def test_command_capture_policy_preserved(tmp_path) -> None:
    from millrace.router.adapters.command import invoke

    result = invoke(
        [sys.executable, "-c", "import os;os.write(2,b'x'*70000)"], tmp_path, {},
        time.time() + 2, lambda: False, lambda _: None,
    )
    assert result.classification == "completed"
    assert len(result.stderr) == 65536 and result.diagnostic == "stderr_truncated"
    req = request(tmp_path, "import os;os.write(1,b'x'*70000)", stdout_fail_limit=65536)
    result = run(req)
    assert result.stdout_overflow and result.stop_cause == "stdout_overflow"


def test_stream_callback_and_partial_setup_failures_close_owned_resources(
    tmp_path, monkeypatch
) -> None:
    error = ValueError("test-owned parser failure")

    def fail(_):
        raise error

    with pytest.raises(OwnedProcessError) as caught:
        run(request(tmp_path, "import os,time;os.write(1,b'bytes');time.sleep(30)"),
            on_stdout=fail)
    assert caught.value.__cause__ is error
    assert caught.value.result.cleanup_confirmed
    assert caught.value.result.stdout_bytes == 5

    def setup_failure(*_):
        raise OSError("test-owned partial pipe setup")

    with monkeypatch.context() as patch:
        patch.setattr(os, "set_blocking", setup_failure)
        started = time.monotonic()
        result = run(request(tmp_path, "import time;time.sleep(30)"))
        assert time.monotonic() - started < 1
    assert result.stop_cause == "io_failed"
    assert result.root_reaped and result.group_state == "closed"
    with pytest.raises(ProcessLookupError):
        os.kill(result.pid, 0)
    with monkeypatch.context() as patch:
        patch.setattr(
            "millrace.router.process.selectors.DefaultSelector", setup_failure
        )
        started = time.monotonic()
        result = run(request(tmp_path, "import time;time.sleep(30)"))
        assert time.monotonic() - started < 1
    assert result.root_reaped and result.group_state == "closed"
    assert not result.cleanup_confirmed  # EOF was not observed after failed setup.


def test_broken_and_cancelled_stdin_never_attest_exact_delivery(tmp_path) -> None:
    result = run(request(
        tmp_path, "import os,time;os.close(0);time.sleep(30)", stdin=b"x" * 1048576,
    ))
    assert result.stop_cause == "stdin_failed"
    assert result.stdin_closed and not result.stdin_delivered
    assert result.cleanup_confirmed
    roots = []
    result = run(request(
        tmp_path, "import time;time.sleep(30)", stdin=b"x" * 1048576,
    ), on_started=roots.append, poll=lambda: bool(roots))
    assert result.stop_cause == "cancelled"
    assert result.stdin_closed and not result.stdin_delivered


def test_cancel_plus_overflow_preserves_first_cause_and_observed_flags(
    tmp_path,
) -> None:
    seen = []
    result = run(request(
        tmp_path, "import os;os.write(1,b'x'*1000)", stdout_fail_limit=8,
    ), on_stdout=seen.append, poll=lambda: bool(seen))
    assert result.stop_cause == "cancelled" and result.stdout_overflow
    assert result.stdout_bytes == sum(map(len, seen))


@pytest.mark.parametrize("slow_snapshot", [False, True])
def test_root_cleanup_escalates_before_return_despite_busy_output_or_late_callback(
    tmp_path, monkeypatch, slow_snapshot,
) -> None:
    killpg = os.killpg
    signals, snapshots = [], []
    observed = 0

    def signal_group(pid, sig):
        if sig:
            signals.append((sig, time.monotonic()))
        return killpg(pid, sig)

    def stdout(raw):
        nonlocal observed
        observed += len(raw)
        if not slow_snapshot:
            # Finite parser work keeps the pipe busy without retaining output.
            time.sleep(0.002)

    def snapshot(code):
        snapshots.append(code)
        time.sleep(5.1)  # Cannot be preempted; must not get another cleanup grace.

    monkeypatch.setattr(os, "killpg", signal_group)
    started = time.monotonic()
    try:
        result = run(request(
            tmp_path,
            argv=(
                sys.executable, str(PEER),
                "root-tail" if slow_snapshot else "root-flood",
            ),
            cleanup_on_root_exit=True, deadline=time.time() + 15,
            stdout_capture_limit=1,
        ), on_stdout=stdout, on_drained=snapshot)
        assert any(sig == signal.SIGTERM for sig, _ in signals)
        assert any(sig == signal.SIGKILL for sig, _ in signals)
        term_at = next(at for sig, at in signals if sig == signal.SIGTERM)
        kill_at = next(at for sig, at in signals if sig == signal.SIGKILL)
        if slow_snapshot:
            assert kill_at - term_at < 0.3
        else:
            assert term_at - started < 4
            assert 1.8 <= kill_at - term_at < 2.6
        assert time.monotonic() - started < 6.5
        assert snapshots == ([0] if slow_snapshot else [])
        assert result.root_reaped and result.cleanup_forced
        assert result.stdout_bytes == observed and len(result.stdout) <= 1
        # The callback overrun remains honest uncertainty, not clean transport.
        if slow_snapshot:
            assert not result.cleanup_confirmed
    finally:
        pidfile = tmp_path / "child.pid"
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_first_stop_observed_once_before_cleanup_bytes(tmp_path) -> None:
    marker = tmp_path / "ready"
    code = (
        "import os,signal,sys,time;from pathlib import Path\n"
        "def term(*_):\n"
        " os.write(1,b'cleanup');os.write(2,b'diagnostic');sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,term)\n"
        f"Path({str(marker)!r}).touch()\n"
        "while True: time.sleep(.01)\n"
    )
    stops, chunks = [], []

    def stdout(raw):
        assert stops == ["cancelled"]
        chunks.append(raw)

    result = run(
        request(tmp_path, code), poll=marker.exists, on_stop=stops.append,
        on_stdout=stdout,
    )
    assert stops == ["cancelled"] and result.stop_cause == "cancelled"
    assert b"".join(chunks) == result.stdout == b"cleanup"
    assert result.stderr == b"diagnostic" and result.cleanup_confirmed
    assert result.stdout_sha256 == hashlib.sha256(b"cleanup").hexdigest()


@pytest.mark.parametrize("interrupt", [False, True])
def test_stop_observer_error_preserves_cause_and_exception(tmp_path, interrupt) -> None:
    error = KeyboardInterrupt() if interrupt else ValueError("observer failure")
    roots, stops = [], []

    def stop(cause):
        stops.append(cause)
        raise error

    expected_error = KeyboardInterrupt if interrupt else OwnedProcessError
    with pytest.raises(expected_error) as caught:
        run(
            request(tmp_path, "import time;time.sleep(30)"),
            on_started=roots.append, poll=lambda: bool(roots), on_stop=stop,
        )
    if interrupt:
        assert caught.value is error
    else:
        assert caught.value.__cause__ is error
        assert caught.value.result.stop_cause == "cancelled"
        assert caught.value.result.cleanup_confirmed
    assert stops == ["cancelled"] and len(roots) == 1
    with pytest.raises(ProcessLookupError):
        os.killpg(roots[0], 0)


def test_secondary_stop_observer_error_cannot_replace_original_callback(
    tmp_path,
) -> None:
    original = ValueError("stream failure")
    stops = []

    def stdout(_):
        raise original

    def stop(cause):
        stops.append(cause)
        raise RuntimeError("secondary observer failure")

    with pytest.raises(OwnedProcessError) as caught:
        run(request(tmp_path, "import os,time;os.write(1,b'x');time.sleep(30)"),
            on_stdout=stdout, on_stop=stop)
    assert caught.value.__cause__ is original
    assert caught.value.result.stop_cause == "callback_failed"
    assert caught.value.result.cleanup_confirmed and stops == ["callback_failed"]


@pytest.mark.parametrize("failure", ["exit", "signal"])
@pytest.mark.parametrize("pipes", ["inherited", "private"])
def test_failed_root_cleans_ready_owned_child(tmp_path, monkeypatch, failure, pipes):
    roots, signals, drained, stops = [], [], [], []
    killpg = os.killpg

    def started(pid):
        roots.append(pid)
        until = time.monotonic() + 3
        while not (tmp_path / "root-ready").exists() and time.monotonic() < until:
            time.sleep(0.002)
        assert (tmp_path / "root-ready").exists()
        ready = json.loads((tmp_path / "root-ready").read_text())
        assert ready["pid"] == ready["pgid"] == pid
        assert ready["child_pid"] == ready["child"]["pid"]
        assert ready["child"]["pgid"] == pid
        assert (ready["failure"], ready["pipes"]) == (failure, pipes)
        (tmp_path / "root-release").touch()

    def signal_group(pgid, sig):
        if sig:
            signals.append((sig, time.monotonic()))
        return killpg(pgid, sig)

    deadline = time.time() + 15
    began = time.monotonic()
    try:
        monkeypatch.setattr(os, "killpg", signal_group)
        result = run(request(
            tmp_path, argv=(sys.executable, str(PEER), "failed-root", failure, pipes),
            deadline=deadline,
        ), on_started=started, on_drained=drained.append, on_stop=stops.append)
        expected = -signal.SIGKILL if failure == "signal" else 7
        assert result.exit_code == expected and result.root_reaped
        assert result.stop_cause is None and stops == []
        assert result.cleanup_forced and result.cleanup_confirmed
        assert result.group_state == "closed" and result.stdin_closed
        assert result.stdout_eof and result.stderr_eof
        assert b'{"outcome":"READY"}' in result.stdout
        assert drained == ([expected] if pipes == "private" else [])
        term = next(at for sig, at in signals if sig == signal.SIGTERM)
        killed = next(at for sig, at in signals if sig == signal.SIGKILL)
        assert 1.8 <= killed - term < 3
        assert time.monotonic() - began < 8 and time.time() < deadline
        with pytest.raises(ProcessLookupError):
            killpg(roots[0], 0)
        ticks = (tmp_path / "child.ticks").read_bytes()
        assert ticks
        time.sleep(0.1)  # Observe stopped writes after proven group closure.
        assert (tmp_path / "child.ticks").read_bytes() == ticks
    finally:
        if roots:
            try:
                killpg(roots[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(roots[0], 0)
            except ChildProcessError:
                pass


@pytest.mark.parametrize("late_cancel", [False, True])
def test_failed_root_denied_cleanup_keeps_one_clock_and_unknown(
    tmp_path, monkeypatch, late_cancel,
):
    roots, signals, stops = [], [], []
    killpg = os.killpg

    def started(pid):
        roots.append(pid)
        until = time.monotonic() + 3
        while not (tmp_path / "root-ready").exists() and time.monotonic() < until:
            time.sleep(0.002)
        assert (tmp_path / "root-ready").exists()
        ready = json.loads((tmp_path / "root-ready").read_text())
        assert ready["pid"] == ready["pgid"] == ready["child"]["pgid"] == pid
        (tmp_path / "root-release").touch()

    def denied(pgid, sig):
        if sig:
            signals.append((sig, time.monotonic()))
        raise PermissionError("test-owned unknown failed-root group")

    def poll():
        return late_cancel and bool(signals) and (
            time.monotonic() - signals[0][1] >= 3
        )

    began = time.monotonic()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "killpg", denied)
            result = run(request(
                tmp_path,
                argv=(sys.executable, str(PEER), "failed-root", "exit", "private"),
                deadline=time.time() + 15,
            ), on_started=started, poll=poll, on_stop=stops.append)
        term = next(at for sig, at in signals if sig == signal.SIGTERM)
        killed = next(at for sig, at in signals if sig == signal.SIGKILL)
        assert term - began < 3 and 1.8 <= killed - term < 3
        assert 4.9 <= time.monotonic() - term < 6.5
        assert result.exit_code == 7 and result.root_reaped
        assert result.stop_cause == ("cancelled" if late_cancel else None)
        assert stops == (["cancelled"] if late_cancel else [])
        assert result.group_state == "unknown" and not result.cleanup_confirmed
        assert result.cleanup_forced and result.stdout_eof and result.stderr_eof
        child = json.loads((tmp_path / "child-ready").read_text())["pid"]
        os.kill(child, 0)  # Unknown must not claim this actual writer stopped.
    finally:
        if roots:
            try:
                killpg(roots[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(roots[0], 0)
            except ChildProcessError:
                pass
