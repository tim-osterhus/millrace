"""Private Linux Pi subreaper. Only its own direct children can be signalled.

The parent supplies private status/control pipes and the exact Pi argv. This
process is single-threaded, owns the only waitpid calls, and never signals a
saved process-group number. A report of disposal requires ECHILD after Pi and
all adopted descendants have been reaped.
"""

from __future__ import annotations

import ctypes
import json
import os
import select
import signal
import sys
import time
from collections.abc import Callable, Sequence
from typing import cast

_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36
_TERM_GRACE = 0.2
_KILL_GRACE = 0.6
_MAX_CHILDREN = 256
_MAX_REPORT = 512


def _prctl(option: int, value: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, value, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl")


def _report(fd: int, value: dict[str, object]) -> None:
    data = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    if len(data) > _MAX_REPORT:
        raise ValueError("supervisor report bound")
    while data:
        data = data[os.write(fd, data) :]


def _children() -> list[int]:
    pid = os.getpid()
    with open(f"/proc/{pid}/task/{pid}/children", encoding="ascii") as stream:
        raw = stream.read(4096)
        if stream.read(1) and raw and not raw[-1].isspace():
            # The final token may be truncated. Signal only complete IDs;
            # later passes expose the next chunk as earlier children retire.
            raw = raw.rpartition(" ")[0]
    children = [int(value) for value in raw.split()[:_MAX_CHILDREN]]
    if any(value <= 0 for value in children):
        raise ValueError("child list shape")
    return children


def _direct_child(pid: int) -> bool:
    with open(f"/proc/{pid}/status", encoding="ascii") as stream:
        for line in stream:
            if line.startswith("PPid:"):
                return int(line.split()[1]) == os.getpid()
    return False


def _signal_direct(pid: int, sig: signal.Signals) -> None:
    # SIGCHLD is SIG_DFL and this process is the sole reaper. A direct child
    # cannot have its PID recycled between enumeration and pidfd_open.
    pidfd_open = cast(Callable[[int], int], getattr(os, "pidfd_open"))
    pidfd_send_signal = cast(
        Callable[[int, signal.Signals], None], getattr(signal, "pidfd_send_signal")
    )
    fd = pidfd_open(pid)
    try:
        if not _direct_child(pid):
            raise ValueError("child ownership changed")
        pidfd_send_signal(fd, sig)
    finally:
        os.close(fd)


def _require_pidfd() -> None:
    """Refuse before Pi exec if this host cannot use the disposal primitive."""
    try:
        pidfd_open = cast(Callable[[int], int], getattr(os, "pidfd_open"))
        pidfd_send_signal = cast(
            Callable[[int, int], None], getattr(signal, "pidfd_send_signal")
        )
    except AttributeError as exc:
        raise OSError("pidfd unsupported") from exc
    fd = pidfd_open(os.getpid())
    try:
        pidfd_send_signal(fd, 0)
    finally:
        os.close(fd)


def _reap_adopted() -> tuple[int, bool]:
    count = 0
    for _ in range(_MAX_CHILDREN):
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return count, True
        if pid == 0:
            return count, False
        count += 1
    return count, False


def _dispose_descendants() -> tuple[bool, int, int, int, int]:
    started = time.monotonic()
    reaped = term_count = kill_count = 0
    while time.monotonic() - started < _TERM_GRACE + _KILL_GRACE:
        count, empty = _reap_adopted()
        reaped += count
        if empty:
            return True, reaped, term_count, kill_count, 0
        sig = (
            signal.SIGTERM
            if time.monotonic() - started < _TERM_GRACE
            else signal.SIGKILL
        )
        for pid in _children():
            try:
                _signal_direct(pid, sig)
            except ProcessLookupError:
                # Only this process can reap its children. ESRCH is expected
                # for a child which just became a zombie; the next wait owns it.
                continue
            if sig == signal.SIGTERM:
                term_count += 1
            else:
                kill_count += 1
        time.sleep(0.01)
    count, empty = _reap_adopted()
    reaped += count
    if empty:
        return True, reaped, term_count, kill_count, 0
    return False, reaped, term_count, kill_count, len(_children())


def _run(
    status_fd: int,
    control_fd: int,
    parent_pid: int,
    bridge_fd: int,
    argv: Sequence[str],
) -> int:
    requested = 0

    def term_handler(_sig: int, _frame: object) -> None:
        nonlocal requested
        requested = max(requested, 1)

    def kill_handler(_sig: int, _frame: object) -> None:
        nonlocal requested
        requested = 2

    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, term_handler)
    signal.signal(signal.SIGUSR2, kill_handler)
    _prctl(_PR_SET_CHILD_SUBREAPER, 1)
    _prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
    if os.getppid() != parent_pid:
        raise ValueError("supervisor parent changed")
    _require_pidfd()
    pid = os.fork()
    if pid == 0:
        os.close(status_fd)
        os.close(control_fd)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGUSR2, signal.SIG_DFL)
        # Match Popen(restore_signals=True) for the inner Node process.
        for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
            inherited = getattr(signal, name, None)
            if inherited is not None:
                signal.signal(inherited, signal.SIG_DFL)
        os.execvpe(argv[0], list(argv), os.environ)
    pi_status: int | None = None
    pi_lost = False
    try:
        null_fd = os.open(os.devnull, os.O_RDWR)
        try:
            for fd in (0, 1, 2):
                os.dup2(null_fd, fd)
        finally:
            if null_fd > 2:
                os.close(null_fd)
        if bridge_fd >= 0:
            os.close(bridge_fd)
        _report(status_fd, {"kind": "ready", "pi_pid": pid})
        sent = 0
        valid_control = True
        while pi_status is None:
            try:
                seen, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError as exc:
                pi_lost = True
                raise ValueError("Pi child ownership lost") from exc
            if seen:
                pi_status = status
                break
            if select.select([control_fd], [], [], 0.02)[0]:
                command = os.read(control_fd, 1)
                if command == b"T":
                    requested = max(requested, 1)
                elif command in (b"K", b""):
                    requested = 2
                else:
                    valid_control = False
                    requested = 2
            if requested > sent:
                # Pi is our unreaped direct child; numeric reuse is impossible.
                os.kill(pid, signal.SIGTERM if requested == 1 else signal.SIGKILL)
                sent = requested
        disposed, reaped, terms, kills, remaining = _dispose_descendants()
        pi_returncode = os.waitstatus_to_exitcode(pi_status)
        _report(
            status_fd,
            {
                "kind": "complete",
                "pi_returncode": pi_returncode,
                "disposed": disposed and valid_control,
                "reaped_children": reaped,
                "term_count": terms,
                "kill_count": kills,
                "remaining_children": remaining,
            },
        )
        return 0 if disposed and valid_control and pi_returncode == 0 else 1
    except (OSError, ValueError):
        if pi_status is None and not pi_lost:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            until = time.monotonic() + 0.5
            while time.monotonic() < until:
                try:
                    seen, status = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    break
                if seen:
                    pi_status = status
                    break
                time.sleep(0.01)
        if pi_status is not None:
            try:
                _dispose_descendants()
            except (OSError, ValueError):
                pass
        try:
            _report(status_fd, {"kind": "failed", "error": "supervisor_failure"})
        except OSError:
            pass
        return 2
    finally:
        os.close(control_fd)
        os.close(status_fd)


def main() -> int:
    if sys.platform != "linux" or len(sys.argv) < 6:
        return 2
    status_fd = -1
    try:
        status_fd, control_fd, parent_pid, bridge_fd = map(int, sys.argv[1:5])
        if status_fd < 3 or control_fd < 3 or parent_pid <= 1 or bridge_fd < -1:
            return 2
        return _run(status_fd, control_fd, parent_pid, bridge_fd, sys.argv[5:])
    except (OSError, ValueError):
        if status_fd >= 3:
            try:
                _report(status_fd, {"kind": "failed", "error": "startup_failure"})
            except OSError:
                pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
