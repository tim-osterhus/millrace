import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tests.router.support import (
    command_graph_json,
    create_instance,
    inspect_run,
    recover_run,
    start_run,
)

CLI = sys.executable


def test_pilot_cli_scope_and_scripted_journey(flow: tuple[Path, Path, Path]) -> None:
    workflow, project, home = flow

    def call(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [CLI, "-m", "millrace.router.cli", "--state-home", str(home), *args],
            text=True,
            capture_output=True,
            timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    created = call(
        "instance", "create", "--workflow", str(workflow), "--project", str(project)
    )
    assert created.returncode == 0, created.stderr
    instance = json.loads(created.stdout.splitlines()[-1])["instance_id"]
    started = call("start", "--instance", instance)
    assert started.returncode == 0, started.stderr
    transmitted, first, receipt = [
        json.loads(line) for line in started.stdout.splitlines()
    ]
    assert transmitted["request_envelope"]["request_id"] == receipt["request_id"]
    assert first["instance_id"] == instance
    assert first["run_id"] == receipt["run_id"]
    status = call("status", "--instance", instance, "--run", receipt["run_id"])
    final = json.loads(status.stdout)
    assert final["status"] == "succeeded"
    assert final["count"] == 2
    refused = call("resume", "--instance", instance, "--run", final["id"])
    assert json.loads(refused.stderr)["code"] == "run_terminal"
    for command in ("pause", "cancel"):
        stopped = call(command, "--instance", instance, "--run", final["id"])
        assert (
            json.loads(stopped.stdout.splitlines()[-1])["action"] == "already_terminal"
        )
    live = call("start", "--instance", instance, "--edit-mode", "live")
    assert live.returncode == 0, live.stderr
    duplicate = call(
        "instance", "create", "--workflow", str(workflow), "--project", str(project)
    )
    assert json.loads(duplicate.stderr)["code"] == "workflow_registered"


def _active(instance: str, run: str, home: Path) -> dict[str, object]:
    until = time.monotonic() + 5
    while time.monotonic() < until:
        status = inspect_run(instance, run, state_home=home)
        if status["invocations"] and status["invocations"][-1]["status"] == "active":
            return status
        time.sleep(0.01)
    raise AssertionError("owned child did not become active")


@pytest.mark.parametrize("interrupts", [1, 2])
def test_foreground_interrupts_and_concurrent_status(
    single_flow: tuple[Path, Path, Path], interrupts: int
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    data["planes"]["main"]["nodes"]["draft"]["invoke"].update(
        args=["wait"], timeout_seconds=0.5
    )
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    process = subprocess.Popen(
        [
            CLI,
            "-m",
            "millrace.router.cli",
            "--state-home",
            str(home),
            "start",
            "--instance",
            instance,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        assert process.stdout is not None
        transmitted = json.loads(process.stdout.readline())
        assert "request_envelope" in transmitted
        ids = json.loads(process.stdout.readline())  # IDs flush before command ends
        active = _active(instance, ids["run_id"], home)
        assert active["status"] == "running"
        for _ in range(interrupts):
            process.send_signal(signal.SIGINT)
            time.sleep(0.03)
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == 0, stderr
        receipt = json.loads(stdout)
        assert receipt["status"] == "applied"
        result = inspect_run(instance, ids["run_id"], state_home=home)
        assert result["status"] == ("paused" if interrupts == 1 else "cancelled")
        assert result["count"] == 1
        assert result["completions"][0]["writer"] == "stopped"
        assert (project / ".millrace-router-owner/owner.json").exists() is (
            interrupts == 1
        )
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()


def test_interrupt_during_started_receipt_is_owned(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    try:
        result = start_run(
            instance,
            state_home=home,
            on_started=lambda ids: os.kill(os.getpid(), signal.SIGINT),
        )
    except KeyboardInterrupt:
        result = None
    assert result is not None
    assert result["status"] == "paused"
    assert result["count"] == 0


def test_controller_death_never_signals_saved_group(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = single_flow
    data = json.loads(workflow.read_text())
    data["planes"]["main"]["nodes"]["draft"]["invoke"].update(
        args=["wait"], timeout_seconds=5
    )
    workflow.write_text(command_graph_json(data))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    process = subprocess.Popen(
        [
            CLI,
            "-m",
            "millrace.router.cli",
            "--state-home",
            str(home),
            "start",
            "--instance",
            instance,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    child: int | None = None
    try:
        assert process.stdout is not None
        assert "request_envelope" in json.loads(process.stdout.readline())
        ids = json.loads(process.stdout.readline())
        active = _active(instance, ids["run_id"], home)
        child = active["invocations"][0]["pid"]
        process.kill()
        process.wait(timeout=3)
        os.kill(child, 0)  # actual command outlived its controller
        with monkeypatch.context() as patch:

            def forbidden(*args: object) -> None:
                raise AssertionError("restart must not signal a saved group")

            patch.setattr(os, "killpg", forbidden)
            result = recover_run(instance, ids["run_id"], state_home=home)
            assert result["status"] == "paused"
            assert result["pause_reason"] == "writer_unknown"
            assert result["count"] == 1
            assert result["completions"] == []
            assert recover_run(instance, ids["run_id"], state_home=home) == result
        os.kill(child, 0)
        assert (project / ".millrace-router-owner/owner.json").exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if child is not None:
            try:
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            until = time.monotonic() + 2
            while time.monotonic() < until:
                state = subprocess.run(
                    ["/bin/ps", "-p", str(child), "-o", "stat="],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if not state.stdout.strip() or state.stdout.strip().startswith("Z"):
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("test-owned command survived final cleanup")
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()


def test_invalid_instance_path_refuses_before_registration(
    flow: tuple[Path, Path, Path],
) -> None:
    from millrace.router.records import RouterError

    workflow, project, home = flow
    with pytest.raises(RouterError):
        create_instance(workflow, project, state_home=home, path=(".",))
    assert not (home / "instances").exists()
