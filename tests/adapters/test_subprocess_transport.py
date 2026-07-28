from __future__ import annotations

import ast
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest

from millrace.adapters.runner_contract import RedactionPolicy
from millrace.contracts.compiled_plan import AuthorityValue


def test_subprocess_transport_invokes_explicit_argv_with_stdin_and_redaction(
    tmp_path: Path,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportRequest,
        SubprocessTransportSuccess,
    )

    transport = SubprocessTransport()
    request = SubprocessTransportRequest(
        argv=(
            sys.executable,
            "-c",
            "import sys; data=sys.stdin.read(); print('echo:' + data)",
        ),
        stdin_bytes=b"token-secret",
        cwd=tmp_path,
        env_allowlist={},
        timeout_seconds=5,
        max_stdin_bytes=64,
        max_stdout_bytes=128,
        max_stderr_bytes=128,
        redaction_policy=RedactionPolicy(
            policy_id="redact-default",
            secret_tokens=("token-secret",),
        ),
    )

    result = transport.invoke(request)

    assert isinstance(result, SubprocessTransportSuccess)
    assert result.exit_code == 0
    assert result.stdout == "echo:[REDACTED]\n"
    assert result.stderr == ""
    assert b"token-secret" not in repr(result).encode()


def test_subprocess_transport_refuses_shell_strings_and_oversized_input(
    tmp_path: Path,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportError,
        SubprocessTransportRequest,
    )

    transport = SubprocessTransport()
    policy = RedactionPolicy(policy_id="redact-default")

    with pytest.raises(TypeError):
        SubprocessTransportRequest(
            argv=cast(Any, f"{sys.executable} -c pass"),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=policy,
        )

    request = SubprocessTransportRequest(
        argv=(sys.executable, "-c", "raise SystemExit(7)"),
        stdin_bytes=b"too-large",
        cwd=tmp_path,
        env_allowlist={},
        timeout_seconds=5,
        max_stdin_bytes=3,
        max_stdout_bytes=128,
        max_stderr_bytes=128,
        redaction_policy=policy,
    )

    result = transport.invoke(request)

    assert isinstance(result, SubprocessTransportError)
    assert result.error_kind == "input_too_large"
    assert result.exit_code is None
    assert result.stdout == ""
    assert result.stderr == ""


def test_subprocess_transport_request_repr_hides_raw_config_surfaces(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.subprocess_transport import SubprocessTransportRequest

    secret = "CONFIG_SECRET"
    request = SubprocessTransportRequest(
        argv=(sys.executable, "-c", f"print({secret!r})"),
        stdin_bytes=secret.encode(),
        cwd=tmp_path / secret,
        env_allowlist={"TOKEN": secret},
        timeout_seconds=5,
        max_stdin_bytes=64,
        max_stdout_bytes=128,
        max_stderr_bytes=128,
        redaction_policy=RedactionPolicy(
            policy_id="redact-default",
            secret_tokens=(secret,),
        ),
    )
    logging.getLogger(__name__).warning(
        "%s %r %s %r",
        request,
        request,
        request.argv,
        request.env_allowlist,
    )

    assert secret not in repr(request)
    assert secret not in str(request)
    assert secret not in repr(request.argv)
    assert secret not in str(request.argv)
    assert secret not in repr(request.env_allowlist)
    assert secret not in str(request.env_allowlist)
    assert secret not in caplog.text


def test_subprocess_transport_uses_allowlisted_environment_and_controlled_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportRequest,
        SubprocessTransportSuccess,
    )

    monkeypatch.setenv("AMBIENT_SECRET", "ambient-secret")
    request = SubprocessTransportRequest(
        argv=(
            sys.executable,
            "-c",
            (
                "import os, pathlib; "
                "print(pathlib.Path.cwd().name); "
                "print(os.environ.get('VISIBLE_TOKEN', 'missing')); "
                "print(os.environ.get('AMBIENT_SECRET', 'missing')); "
                "print(os.environ.get('PATH', 'missing'))"
            ),
        ),
        stdin_bytes=b"",
        cwd=tmp_path,
        env_allowlist={"VISIBLE_TOKEN": "visible-value"},
        timeout_seconds=5,
        max_stdin_bytes=64,
        max_stdout_bytes=256,
        max_stderr_bytes=128,
        redaction_policy=RedactionPolicy(policy_id="redact-default"),
    )

    result = SubprocessTransport().invoke(request)

    assert isinstance(result, SubprocessTransportSuccess)
    assert result.stdout.splitlines() == [
        tmp_path.name,
        "visible-value",
        "missing",
        "missing",
    ]

    marker = tmp_path / "should-not-launch.txt"
    invalid_cwd = SubprocessTransport().invoke(
        SubprocessTransportRequest(
            argv=(
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('x')",
            ),
            stdin_bytes=b"",
            cwd=Path("."),
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        ),
    )

    assert invalid_cwd.error_kind == "invalid_cwd"
    assert not marker.exists()

    missing_cwd = SubprocessTransport().invoke(
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "pass"),
            stdin_bytes=b"",
            cwd=tmp_path / "missing",
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        ),
    )

    assert missing_cwd.error_kind == "invalid_cwd"

    with pytest.raises(ValueError):
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "pass"),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=float("inf"),
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )


def test_subprocess_transport_pre_cancel_and_timeout_do_not_expose_success(
    tmp_path: Path,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportError,
        SubprocessTransportRequest,
    )

    policy = RedactionPolicy(policy_id="redact-default")
    transport = SubprocessTransport()
    cancelled = transport.invoke(
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "print('must-not-run')"),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=policy,
            pre_cancelled=True,
        ),
    )

    assert isinstance(cancelled, SubprocessTransportError)
    assert cancelled.error_kind == "cancelled"
    assert cancelled.stdout == ""

    timed_out = transport.invoke(
        SubprocessTransportRequest(
            argv=(
                sys.executable,
                "-c",
                "import time; print('late'); time.sleep(5)",
            ),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=0.1,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=policy,
        ),
    )

    assert isinstance(timed_out, SubprocessTransportError)
    assert timed_out.error_kind == "timeout"
    assert timed_out.stdout == ""


def test_subprocess_transport_error_and_output_ceilings_are_fail_closed(
    tmp_path: Path,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportError,
        SubprocessTransportRequest,
        SubprocessTransportSuccess,
    )

    policy = RedactionPolicy(
        policy_id="redact-default",
        secret_tokens=("token-secret",),
    )
    transport = SubprocessTransport()
    nonzero = transport.invoke(
        SubprocessTransportRequest(
            argv=(
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "print('out token-secret'); "
                    "print('err token-secret', file=sys.stderr); "
                    "raise SystemExit(3)"
                ),
            ),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=policy,
        ),
    )

    assert isinstance(nonzero, SubprocessTransportError)
    assert nonzero.error_kind == "nonzero_exit"
    assert nonzero.exit_code == 3
    assert "token-secret" not in repr(nonzero)
    assert nonzero.stdout == "out [REDACTED]\n"
    assert nonzero.stderr == "err [REDACTED]\n"

    too_much_stdout = transport.invoke(
        SubprocessTransportRequest(
            argv=(
                sys.executable,
                "-c",
                (
                    "import sys, time; "
                    "sys.stdout.write('x' * 100000); "
                    "sys.stdout.flush(); "
                    "time.sleep(5)"
                ),
            ),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=4,
            max_stdin_bytes=64,
            max_stdout_bytes=8,
            max_stderr_bytes=128,
            redaction_policy=policy,
        ),
    )

    assert isinstance(too_much_stdout, SubprocessTransportError)
    assert too_much_stdout.error_kind == "output_too_large"
    assert too_much_stdout.stdout == ""

    bounded_stderr = transport.invoke(
        SubprocessTransportRequest(
            argv=(
                sys.executable,
                "-c",
                "import sys; print('token-secret' * 20, file=sys.stderr)",
            ),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=17,
            redaction_policy=policy,
        ),
    )

    assert isinstance(bounded_stderr, SubprocessTransportSuccess)
    assert "token-secret" not in repr(bounded_stderr)
    assert "token" not in bounded_stderr.stderr
    assert len(bounded_stderr.stderr.encode("utf-8")) <= 17

    expanded_stdout = transport.invoke(
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "print('s')"),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=2,
            max_stderr_bytes=128,
            redaction_policy=RedactionPolicy(
                policy_id="redact-default",
                secret_tokens=("s",),
            ),
        ),
    )

    assert isinstance(expanded_stdout, SubprocessTransportError)
    assert expanded_stdout.error_kind == "output_too_large"

    multibyte_stderr = transport.invoke(
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "import sys; sys.stderr.write('é' * 20)"),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=3,
            redaction_policy=policy,
        ),
    )

    assert isinstance(multibyte_stderr, SubprocessTransportSuccess)
    assert len(multibyte_stderr.stderr.encode("utf-8")) <= 3


def test_subprocess_transport_redacts_exception_repr_and_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportError,
        SubprocessTransportRequest,
    )

    secret = "token-secret"
    request = SubprocessTransportRequest(
        argv=(str(tmp_path / secret / "missing-command"),),
        stdin_bytes=b"",
        cwd=tmp_path,
        env_allowlist={},
        timeout_seconds=5,
        max_stdin_bytes=64,
        max_stdout_bytes=128,
        max_stderr_bytes=128,
        redaction_policy=RedactionPolicy(
            policy_id="redact-default",
            secret_tokens=(secret,),
        ),
    )

    result = SubprocessTransport().invoke(request)
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, SubprocessTransportError)
    assert result.error_kind == "invocation_failed"
    assert secret not in repr(result)
    assert secret not in caplog.text


def test_subprocess_transport_uses_base_redaction_not_policy_subclass(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportRequest,
        SubprocessTransportSuccess,
    )

    class NoOpPolicy(RedactionPolicy):
        def __getattribute__(self, name: str) -> object:
            if name == "secret_tokens":
                return ()
            return super().__getattribute__(name)

        def redact_text(self, value: str) -> str:
            return value

        def redact_authority_value(self, value: object) -> AuthorityValue:
            return cast(AuthorityValue, value)

    secret = "token-secret"
    result = SubprocessTransport().invoke(
        SubprocessTransportRequest(
            argv=(
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "print('token-secret'); "
                    "print('token-secret', file=sys.stderr)"
                ),
            ),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=NoOpPolicy(
                policy_id="redact-default",
                secret_tokens=(secret,),
            ),
        ),
    )
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, SubprocessTransportSuccess)
    assert result.stdout == "[REDACTED]\n"
    assert result.stderr == "[REDACTED]\n"
    assert secret not in repr(result)
    assert secret not in caplog.text


def test_subprocess_transport_invocation_error_diagnostics_are_redacted(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportError,
        SubprocessTransportRequest,
    )

    secret = "token-secret"
    result = SubprocessTransport().invoke(
        SubprocessTransportRequest(
            argv=(str(tmp_path / secret / "missing-command"),),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=RedactionPolicy(
                policy_id="redact-default",
                secret_tokens=(secret,),
            ),
        ),
    )
    logging.getLogger(__name__).warning("%s %r", result, result)

    assert isinstance(result, SubprocessTransportError)
    assert result.error_kind == "invocation_failed"
    assert result.stdout == ""
    assert result.stderr == ""
    assert "[REDACTED]" in result.diagnostics
    assert secret not in repr(result)
    assert secret not in caplog.text


def test_subprocess_transport_does_not_create_workspace_state_files(
    tmp_path: Path,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportRequest,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = RedactionPolicy(policy_id="redact-default")
    transport = SubprocessTransport()

    requests = [
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "print('ok')"),
            stdin_bytes=b"",
            cwd=workspace,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=policy,
        ),
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "raise SystemExit(4)"),
            stdin_bytes=b"",
            cwd=workspace,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=policy,
        ),
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "print('x' * 2000)"),
            stdin_bytes=b"",
            cwd=workspace,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=8,
            max_stderr_bytes=128,
            redaction_policy=policy,
        ),
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "import time; time.sleep(5)"),
            stdin_bytes=b"",
            cwd=workspace,
            env_allowlist={},
            timeout_seconds=0.1,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=policy,
        ),
    ]

    for request in requests:
        before = _snapshot_tree(workspace)
        transport.invoke(request)
        assert _snapshot_tree(workspace) == before


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="process-group cleanup is POSIX-specific",
)
def test_subprocess_transport_timeout_kills_process_group_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportError,
        SubprocessTransportHandle,
        SubprocessTransportRequest,
    )

    heartbeat = tmp_path / "heartbeat.txt"
    child_code = (
        "import pathlib, time; "
        f"path = pathlib.Path({str(heartbeat)!r}); "
        "\nwhile True:\n"
        "    path.write_text(str(time.time()))\n"
        "    time.sleep(0.05)\n"
    )
    parent_code = (
        "import pathlib, subprocess, time\n"
        f"heartbeat = pathlib.Path({str(heartbeat)!r})\n"
        f"subprocess.Popen([{sys.executable!r}, '-c', {child_code!r}])\n"
        "deadline = time.time() + 2\n"
        "while not heartbeat.exists() and time.time() < deadline:\n"
        "    time.sleep(0.01)\n"
        "time.sleep(30)\n"
    )

    transport = SubprocessTransport()
    request = SubprocessTransportRequest(
        argv=(sys.executable, "-c", parent_code),
        stdin_bytes=b"",
        cwd=tmp_path,
        env_allowlist={},
        timeout_seconds=0.3,
        max_stdin_bytes=64,
        max_stdout_bytes=128,
        max_stderr_bytes=128,
        redaction_policy=RedactionPolicy(policy_id="redact-default"),
    )
    started_handles: list[SubprocessTransportHandle] = []
    original_start = transport.start

    def capture_start(
        captured_request: SubprocessTransportRequest,
    ) -> SubprocessTransportHandle | SubprocessTransportError:
        started = original_start(captured_request)
        if isinstance(started, SubprocessTransportHandle):
            started_handles.append(started)
        return started

    monkeypatch.setattr(transport, "start", capture_start)

    try:
        result = transport.invoke(request)

        assert isinstance(result, SubprocessTransportError)
        assert result.error_kind == "timeout"
        assert heartbeat.exists()
        stable_value = heartbeat.read_text()
        time.sleep(0.3)
        assert heartbeat.read_text() == stable_value
    finally:
        for started in started_handles:
            started.kill()
            started.cleanup()


def test_live_subprocess_handle_terminates_group_and_joins_readers(
    tmp_path: Path,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportHandle,
        SubprocessTransportRequest,
    )

    handle = SubprocessTransport().start(
        SubprocessTransportRequest(
            argv=(
                sys.executable,
                "-c",
                "import sys,time; print('started', flush=True); time.sleep(30)",
            ),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=30,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )
    )

    assert isinstance(handle, SubprocessTransportHandle)
    try:
        assert handle.poll_completion() is None
        assert handle.terminate().operation == "terminate"
        cleanup = handle.cleanup()
        assert cleanup.disposition == "complete"
        assert handle.readers_joined
    finally:
        handle.kill()
        handle.cleanup()


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="process-group cleanup is POSIX-specific",
)
def test_live_handle_accounts_for_child_after_leader_exits(tmp_path: Path) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportHandle,
        SubprocessTransportRequest,
    )

    heartbeat = tmp_path / "leader-exit-child.txt"
    child = (
        "import pathlib,time\n"
        f"path=pathlib.Path({str(heartbeat)!r})\n"
        "while True:\n"
        " path.write_text(str(time.time()))\n"
        " time.sleep(0.03)\n"
    )
    parent = (
        "import subprocess,sys\n"
        f"subprocess.Popen([sys.executable,'-c',{child!r}],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
    )
    handle = SubprocessTransport().start(
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", parent),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )
    )
    assert isinstance(handle, SubprocessTransportHandle)
    try:
        deadline = time.time() + 2
        while handle.process.poll() is None and time.time() < deadline:
            time.sleep(0.01)
        assert handle.process.poll() is not None
        while not heartbeat.exists() and time.time() < deadline:
            time.sleep(0.01)

        handle.kill()
        cleanup = handle.cleanup()
        value = heartbeat.read_text()
        time.sleep(0.15)

        assert cleanup.disposition in {"complete", "orphan_risk"}
        if cleanup.disposition == "complete":
            assert heartbeat.read_text() == value
    finally:
        handle.kill()
        handle.cleanup()


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="process-group cleanup is POSIX-specific",
)
def test_terminal_leader_with_live_child_is_not_reported_complete(
    tmp_path: Path,
) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportHandle,
        SubprocessTransportRequest,
    )

    heartbeat = tmp_path / "terminal-leader-child.txt"
    child = (
        "import pathlib,time\n"
        f"path=pathlib.Path({str(heartbeat)!r})\n"
        "while True:\n"
        " path.write_text(str(time.time()))\n"
        " time.sleep(0.03)\n"
    )
    parent = (
        "import subprocess,sys\n"
        f"subprocess.Popen([sys.executable,'-c',{child!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL)\n"
    )
    handle = SubprocessTransport().start(
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", parent),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )
    )
    assert isinstance(handle, SubprocessTransportHandle)
    try:
        handle.process.wait(timeout=2)
        deadline = time.time() + 2
        while not heartbeat.exists() and time.time() < deadline:
            time.sleep(0.01)
        with pytest.raises(RuntimeError, match="owned process group remains"):
            handle.poll_completion()
    finally:
        handle.kill()
        handle.cleanup()


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="process-group cleanup is POSIX-specific",
)
def test_invoke_cleans_child_group_before_returning_success(tmp_path: Path) -> None:
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportRequest,
        SubprocessTransportSuccess,
    )

    heartbeat = tmp_path / "invoke-child.txt"
    child_pid = tmp_path / "invoke-child.pid"
    child = (
        "import pathlib,time\n"
        f"path=pathlib.Path({str(heartbeat)!r})\n"
        "while True:\n"
        " path.write_text(str(time.time()))\n"
        " time.sleep(0.03)\n"
    )
    parent = (
        "import pathlib,subprocess,sys,time\n"
        f"child_process=subprocess.Popen([sys.executable,'-c',{child!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL)\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child_process.pid))\n"
        "time.sleep(0.1)\n"
    )
    try:
        result = SubprocessTransport().invoke(
            SubprocessTransportRequest(
                argv=(sys.executable, "-c", parent),
                stdin_bytes=b"",
                cwd=tmp_path,
                env_allowlist={},
                timeout_seconds=5,
                max_stdin_bytes=64,
                max_stdout_bytes=128,
                max_stderr_bytes=128,
                redaction_policy=RedactionPolicy(policy_id="redact-default"),
            )
        )

        assert isinstance(result, SubprocessTransportSuccess)
        stable = heartbeat.read_text()
        time.sleep(0.15)
        assert heartbeat.read_text() == stable
    finally:
        if child_pid.exists():
            try:
                os.kill(int(child_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_subprocess_start_refuses_without_process_group_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters import subprocess_transport
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportError,
        SubprocessTransportRequest,
    )

    monkeypatch.setattr(
        subprocess_transport,
        "_supports_process_group_ownership",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        subprocess.Popen,
        "__init__",
        lambda *_args, **_kwargs: pytest.fail("external work was launched"),
    )
    result = SubprocessTransport().start(
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "pass"),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )
    )

    assert isinstance(result, SubprocessTransportError)
    assert result.error_kind == "invocation_failed"


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="process-group cleanup is POSIX-specific",
)
@pytest.mark.parametrize("current_marker", (None, "different process generation"))
def test_live_handle_refuses_possibly_reused_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current_marker: str | None,
) -> None:
    from millrace.adapters import subprocess_transport
    from millrace.adapters.subprocess_transport import (
        SubprocessTransport,
        SubprocessTransportHandle,
        SubprocessTransportRequest,
    )

    handle = SubprocessTransport().start(
        SubprocessTransportRequest(
            argv=(sys.executable, "-c", "pass"),
            stdin_bytes=b"",
            cwd=tmp_path,
            env_allowlist={},
            timeout_seconds=5,
            max_stdin_bytes=64,
            max_stdout_bytes=128,
            max_stderr_bytes=128,
            redaction_policy=RedactionPolicy(policy_id="redact-default"),
        )
    )
    assert isinstance(handle, SubprocessTransportHandle)
    handle.process.wait(timeout=2)
    signal_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        subprocess_transport,
        "_process_start_marker",
        lambda _pid: current_marker,
    )
    monkeypatch.setattr(
        subprocess_transport,
        "_pid_exists",
        lambda _pid: True,
        raising=False,
    )
    monkeypatch.setattr(
        os,
        "killpg",
        lambda process_group_id, signum: signal_calls.append(
            (process_group_id, signum)
        ),
    )

    result = handle.kill()
    cleanup = handle.cleanup()

    assert result.result == "failed"
    assert all(signum == 0 for _, signum in signal_calls)
    assert cleanup.disposition == "orphan_risk"


def test_subprocess_transport_production_imports_stay_below_runtime_authority() -> None:
    module_path = Path("src/millrace/adapters/subprocess_transport.py")
    tree = ast.parse(module_path.read_text())
    forbidden_prefixes = (
        "millrace.compiler",
        "millrace.kernel",
        "millrace.operator",
        "millrace.substrate",
        "millrace.workflows",
    )
    imports: list[str] = []
    shell_true_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if (
                    keyword.arg == "shell"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    shell_true_calls.append(node)

    assert not [
        imported for imported in imports if imported.startswith(forbidden_prefixes)
    ]
    assert shell_true_calls == []


def _snapshot_tree(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            item.relative_to(root).as_posix()
            for item in root.rglob("*")
            if item.exists()
        ),
    )
