import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from millrace.router.adapters.command import invoke

CASES = Path(__file__).parents[1] / "fixtures/command_cases.py"


def test_stdout_exit_timeout_and_writer_states(tmp_path: Path) -> None:
    result = invoke(
        [sys.executable, str(CASES), "good"],
        tmp_path,
        {},
        time.time() + 2,
        lambda: False,
        lambda pid: None,
    )
    assert result.classification == "completed"
    assert result.writer_state == "stopped"
    assert b'"READY"' in result.stdout


def test_redaction_cannot_expand_retained_diagnostics(tmp_path: Path) -> None:
    result = invoke(
        [sys.executable, str(CASES), "hugeerr"],
        tmp_path,
        {"SYNTHETIC_SECRET": "x"},
        time.time() + 2,
        lambda: False,
        lambda pid: None,
    )
    assert result.classification == "completed"
    assert len(result.stderr) <= 65536
    assert b"x" not in result.stderr
    assert result.diagnostic == "stderr_truncated"


@pytest.mark.parametrize("mode", ["child", "linger", "detached"])
def test_descendant_stream_and_cleanup_aftermath(tmp_path: Path, mode: str) -> None:
    roots: list[int] = []
    started = time.monotonic()
    try:
        result = invoke(
            [sys.executable, str(CASES), mode],
            tmp_path,
            {"SYNTHETIC_SECRET": "x"},
            time.time() + (1 if mode == "child" else 0.15),
            lambda: False,
            roots.append,
        )
        if mode == "child":
            assert result.classification == "completed"
            assert result.writer_state == "stopped"
            assert time.monotonic() - started >= 0.15
        else:
            assert result.classification == "timeout"
            if mode == "detached":
                assert result.writer_state == "may_live"
                assert "cleanup_unconfirmed" in result.diagnostic
                assert len(result.stderr) <= 65536
                assert b"x" not in result.stderr
                assert "stderr_truncated" in result.diagnostic
        with pytest.raises(ChildProcessError):
            os.waitpid(roots[0], os.WNOHANG)
    finally:
        pid_file = tmp_path / "child.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text())
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            until = time.monotonic() + 2
            while time.monotonic() < until:
                state = subprocess.run(
                    ["/bin/ps", "-p", str(pid), "-o", "stat="],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if not state.stdout.strip() or state.stdout.strip().startswith("Z"):
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("test-owned descendant still writes after cleanup")


def test_deadline_before_launch_has_no_child(tmp_path: Path) -> None:
    roots: list[int] = []
    result = invoke(
        [sys.executable, str(CASES), "wait"],
        tmp_path,
        {},
        time.time() - 1,
        lambda: False,
        roots.append,
    )
    assert roots == []
    assert result.classification == "timeout"
    assert result.writer_state == "stopped"


def test_transient_denied_group_probe_does_not_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = os.killpg
    denied = False

    def probe(pgid: int, sig: int) -> None:
        nonlocal denied
        if sig == 0 and not denied:
            denied = True
            raise PermissionError("fixture denied group probe")
        original(pgid, sig)

    monkeypatch.setattr(os, "killpg", probe)
    result = invoke(
        [sys.executable, str(CASES), "good"],
        tmp_path,
        {},
        time.time() + 2,
        lambda: False,
        lambda pid: None,
    )
    assert denied
    assert result.classification == "completed"
    assert result.writer_state == "stopped"


def test_denied_probe_and_cleanup_preserve_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots: list[int] = []
    original = os.killpg
    signals: list[int] = []

    def denied(pgid: int, sig: int) -> None:
        signals.append(sig)
        raise PermissionError("fixture denies group access")

    started = time.monotonic()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "killpg", denied)
            result = invoke(
                [sys.executable, str(CASES), "wait"],
                tmp_path,
                {},
                time.time() + 0.1,
                lambda: False,
                roots.append,
            )
        assert result.classification == "timeout"
        assert result.writer_state == "may_live"
        assert result.diagnostic == "cleanup_unconfirmed"
        assert signal.SIGTERM in signals and signal.SIGKILL in signals
        assert 5 <= time.monotonic() - started < 6.5
        os.kill(roots[0], 0)  # denial must not claim that this actual writer stopped
    finally:
        if roots:
            try:
                original(roots[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(roots[0], 0)


def test_both_channel_truncations_are_retained(tmp_path):
    roots = []
    result = invoke(
        [sys.executable, str(CASES), "both"],
        tmp_path,
        {},
        time.time() + 2,
        lambda: False,
        roots.append,
    )
    assert result.writer_state == "stopped"
    assert set(result.diagnostic.split(",")) == {"stdout_truncated", "stderr_truncated"}
    with pytest.raises(ChildProcessError):
        os.waitpid(roots[0], os.WNOHANG)


@pytest.mark.parametrize("denied", [False, True])
def test_postpoll_clock_preserves_cleanup_grace(tmp_path, monkeypatch, denied):
    ready = tmp_path / "ready"
    code = (
        "import signal,time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM,lambda *args:None)\n"
        f"Path({str(ready)!r}).write_text('ready')\n"
        "while True: time.sleep(.02)\n"
    )
    roots, attempts = [], []
    killpg = os.killpg
    slow = False

    def started(pid):
        roots.append(pid)
        until = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert ready.exists()

    def callback():
        nonlocal slow
        if not slow:
            slow = True
            time.sleep(2.1)
        return True

    def signal_group(pid, sig):
        if sig in {signal.SIGTERM, signal.SIGKILL}:
            attempts.append((sig, time.monotonic()))
            if denied:
                raise PermissionError("fixture denied signal")
        return killpg(pid, sig)

    try:
        monkeypatch.setattr(os, "killpg", signal_group)
        result = invoke(
            [sys.executable, "-c", code],
            tmp_path,
            {},
            time.time() + 30,
            callback,
            started,
        )
        term = next(at for sig, at in attempts if sig == signal.SIGTERM)
        killed = next(at for sig, at in attempts if sig == signal.SIGKILL)
        assert 1.9 <= killed - term < 3
        assert result.classification == "cancelled"
        if denied:
            assert 4.9 <= time.monotonic() - term < 6.5
            assert (
                result.writer_state == "may_live"
                and result.diagnostic == "cleanup_unconfirmed"
            )
        else:
            assert result.writer_state == "stopped"
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


@pytest.mark.parametrize("output", [False, True])
def test_postpoll_deadline_is_rechecked(tmp_path, output):
    ready = tmp_path / "ready"
    code = (
        "import time\nfrom pathlib import Path\n"
        f"Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(.4)\n"
    )
    if output:
        code += 'print(\'{"outcome":"READY"}\',flush=True)\n'
    roots = []

    def started(pid):
        roots.append(pid)
        until = time.monotonic() + 2
        while not ready.exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert ready.exists()

    def callback():
        time.sleep(0.6)
        return False

    try:
        result = invoke(
            [sys.executable, "-c", code],
            tmp_path,
            {},
            time.time() + 0.3,
            callback,
            started,
        )
        assert result.classification == "timeout" and result.writer_state == "stopped"
    finally:
        if roots:
            try:
                os.killpg(roots[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(roots[0], 0)
            except ChildProcessError:
                pass
