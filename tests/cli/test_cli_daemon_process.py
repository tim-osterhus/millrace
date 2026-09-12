from __future__ import annotations

import os
import socket
import subprocess
import sys
from copy import deepcopy

import pytest

from millrace.adapters.cli import daemon_process

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="exact native process/lifecycle qualification requires macOS",
)

def test_real_macos_exact_birth_and_reaped_owned_child():
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys;sys.stdin.read(1)"], stdin=subprocess.PIPE
    )
    try:
        identity = daemon_process.process_identity(child.pid)
        assert identity["status"] == "live"
        assert identity["uid"] == os.geteuid()
        assert daemon_process.process_identity(child.pid) == identity
        assert daemon_process.observe_process(identity) == "live"
        child.communicate(b"x", timeout=3)
        assert daemon_process.observe_process(identity) == "not_live"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_birth_mismatch_fixture_is_absence_of_old_identity_not_actual_pid_reuse():
    identity = daemon_process.process_identity(os.getpid())
    old = deepcopy(identity)
    old["birth"][1] += 1
    assert daemon_process.observe_process(old) == "not_live"
    assert daemon_process.observe_process(identity) == "live"


def test_real_same_uid_peer_and_unsupported_observation(monkeypatch):
    left, right = socket.socketpair()
    try:
        assert daemon_process.same_uid_peer(left)
        assert daemon_process.same_uid_peer(right)
        monkeypatch.setattr(daemon_process.sys, "platform", "unsupported")
        assert not daemon_process.same_uid_peer(left)
        assert daemon_process.process_identity(os.getpid()) == {"status": "unknown"}
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("pid", [0, -1, True])
def test_invalid_pid_never_uses_raw_signal_fallback(pid):
    assert daemon_process.process_identity(pid) == {"status": "unknown"}
