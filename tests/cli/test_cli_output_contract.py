from __future__ import annotations

import io
import json
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
PROJECT_METADATA = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))[
    "project"
]


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    from millrace.adapters.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _single_json_object(raw: str) -> dict[str, Any]:
    assert raw.endswith("\n")
    assert len(raw.strip().splitlines()) == 1
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def test_daemon_uninitialized_store_returns_stable_error(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "store.sqlite3"
    cas_path = tmp_path / "cas"

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "--db",
            str(db_path),
            "--cas",
            str(cas_path),
            "run",
            "daemon",
            "--max-ticks",
            "1",
        ]
    )

    assert exit_code == 4
    assert stdout == ""
    error = _single_json_object(stderr)
    assert error["ok"] is False
    assert error["command"] == "run.daemon"
    assert error["code"] == "daemon_state_open_failed"
    assert error["message"] == "Daemon could not open or validate runtime state."
    details = error["details"]
    assert details["iterations"] == 0
    assert details["stopped_reason"] == "state_open_failed"
    assert details["workspace"] == str(workspace.resolve())
    assert details["db_path"] == str(db_path.resolve())
    assert details["cas_path"] == str(cas_path.resolve())
    assert details["diagnostics"][0]["code"] == "cas_root_not_initialized"
    assert not (workspace / ".millrace" / "daemon.lock").exists()
    assert not db_path.exists()
    assert not cas_path.exists()


def test_daemon_invalid_adapter_config_reports_daemon_command_before_state_access(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    bad_config = tmp_path / "bad-adapter-config.json"
    bad_config.write_text("{", encoding="utf-8")

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "run",
            "daemon",
            "--max-ticks",
            "1",
            "--adapter-config-json",
            str(bad_config),
        ]
    )

    assert exit_code == 2
    assert stdout == ""
    error = _single_json_object(stderr)
    assert error["command"] == "run.daemon"
    assert error["code"] == "invalid_adapter_config"
    assert not (workspace / ".millrace" / "daemon.lock").exists()
    assert not (workspace / ".millrace" / "runtime.sqlite3").exists()


def test_json_argument_parse_errors_are_single_error_objects() -> None:
    exit_code, stdout, stderr = _invoke(["--json", "--not-a-real-option"])

    assert exit_code == 2
    assert stdout == ""
    assert "usage:" not in stderr.lower()
    assert "traceback" not in stderr.lower()
    error = _single_json_object(stderr)
    assert error["ok"] is False
    assert error["command"] == "cli"
    assert error["code"] == "argument_parse_error"
    assert isinstance(error["message"], str)
    assert error["details"] == {}


def test_json_success_and_error_contract_are_single_objects() -> None:
    success_code, success_stdout, success_stderr = _invoke(["--json", "--version"])

    assert success_code == 0
    assert success_stderr == ""
    success = _single_json_object(success_stdout)
    assert list(success) == ["ok", "command", "code", "message", "data"]
    assert success["ok"] is True
    assert success["command"] == "version"
    assert success["code"] == "ok"
    assert isinstance(success["message"], str)
    assert isinstance(success["data"], dict)

    error_code, error_stdout, error_stderr = _invoke(["--json", "run"])

    assert error_code == 3
    assert error_stdout == ""
    error = _single_json_object(error_stderr)
    assert list(error) == ["ok", "command", "code", "message", "details"]
    assert error["ok"] is False
    assert error["command"] == "run"
    assert error["code"] == "command_not_implemented"
    assert isinstance(error["message"], str)
    assert isinstance(error["details"], dict)


def test_substrate_failures_are_bounded_json_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.cli import workspace as workspace_module
    from millrace.substrate.errors import StorageIntegrityError

    def refuse_open(_namespace: object) -> None:
        raise StorageIntegrityError("corrupt persisted authority")

    monkeypatch.setattr(workspace_module, "handle_workspace_command", refuse_open)

    exit_code, stdout, stderr = _invoke(["--json", "workspace", "check"])

    assert exit_code == 4
    assert stdout == ""
    assert "traceback" not in stderr.lower()
    error = _single_json_object(stderr)
    assert error["ok"] is False
    assert error["command"] == "workspace.check"
    assert error["code"] == "substrate_error"


def test_actor_id_validation_rejects_blank_without_state_access(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "store.sqlite3"
    cas_path = tmp_path / "cas"

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--actor-id",
            " ",
            "--workspace",
            str(workspace),
            "--db",
            str(db_path),
            "--cas",
            str(cas_path),
            "run",
            "daemon",
        ]
    )

    assert exit_code == 2
    assert stdout == ""
    assert _single_json_object(stderr) == {
        "ok": False,
        "command": "run.daemon",
        "code": "invalid_actor_id",
        "message": "--actor-id must be nonblank.",
        "details": {},
    }
    assert not workspace.exists()
    assert not db_path.exists()
    assert not cas_path.exists()


def test_actor_id_validation_precedes_version_success() -> None:
    exit_code, stdout, stderr = _invoke(
        ["--json", "--actor-id", " ", "--version"]
    )

    assert exit_code == 2
    assert stdout == ""
    assert _single_json_object(stderr) == {
        "ok": False,
        "command": "version",
        "code": "invalid_actor_id",
        "message": "--actor-id must be nonblank.",
        "details": {},
    }


def test_json_help_renders_single_success_objects() -> None:
    no_command_code, no_command_stdout, no_command_stderr = _invoke(["--json"])

    assert no_command_code == 0
    assert no_command_stderr == ""
    no_command = _single_json_object(no_command_stdout)
    assert no_command["ok"] is True
    assert no_command["command"] == "cli"
    assert no_command["code"] == "help"
    assert isinstance(no_command["data"], dict)
    assert isinstance(no_command["data"]["help"], str)
    assert "workspace" in no_command["data"]["help"]

    root_code, root_stdout, root_stderr = _invoke(["--json", "--help"])

    assert root_code == 0
    assert root_stderr == ""
    root = _single_json_object(root_stdout)
    assert root["ok"] is True
    assert root["command"] == "cli"
    assert root["code"] == "help"
    assert isinstance(root["data"], dict)
    assert isinstance(root["data"]["help"], str)
    assert "workspace" in root["data"]["help"]

    run_code, run_stdout, run_stderr = _invoke(["--json", "run", "--help"])

    assert run_code == 0
    assert run_stderr == ""
    run = _single_json_object(run_stdout)
    assert run["ok"] is True
    assert run["command"] == "run"
    assert run["code"] == "help"
    assert isinstance(run["data"], dict)
    assert isinstance(run["data"]["help"], str)
    assert "daemon" in run["data"]["help"]

    daemon_code, daemon_stdout, daemon_stderr = _invoke(
        ["--json", "run", "daemon", "--help"]
    )

    assert daemon_code == 0
    assert daemon_stderr == ""
    daemon = _single_json_object(daemon_stdout)
    assert daemon["ok"] is True
    assert daemon["command"] == "run.daemon"
    assert daemon["code"] == "help"
    assert isinstance(daemon["data"], dict)
    assert isinstance(daemon["data"]["help"], str)


def test_cli_has_no_runtime_dependency_added_without_review() -> None:
    runtime_dependencies = PROJECT_METADATA.get("dependencies", [])
    normalized = {
        dependency.split("[", 1)[0].split("=", 1)[0].split("<", 1)[0].lower()
        for dependency in runtime_dependencies
    }

    assert "click" not in normalized
    assert "typer" not in normalized
