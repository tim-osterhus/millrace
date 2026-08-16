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


def test_budget_stop_help_and_missing_id_are_bounded_json_contracts() -> None:
    help_code, help_stdout, help_stderr = _invoke(
        ["--json", "run", "budget-stop", "--help"]
    )

    assert help_code == 0
    assert help_stderr == ""
    help_result = _single_json_object(help_stdout)
    assert help_result["ok"] is True
    assert help_result["command"] == "run.budget-stop"
    assert "--budget-id" in help_result["data"]["help"]

    missing_code, missing_stdout, missing_stderr = _invoke(
        ["--json", "run", "budget-stop"]
    )

    assert missing_code == 2
    assert missing_stdout == ""
    missing = _single_json_object(missing_stderr)
    assert missing["ok"] is False
    assert missing["command"] == "run.budget-stop"
    assert missing["code"] == "invalid_budget_id"
    assert missing["details"] == {"budget_id": ""}

    blank_code, blank_stdout, blank_stderr = _invoke(
        ["--json", "run", "budget-stop", "--budget-id", "  "]
    )

    assert blank_code == 2
    assert blank_stdout == ""
    blank = _single_json_object(blank_stderr)
    assert blank["command"] == "run.budget-stop"
    assert blank["code"] == "invalid_budget_id"
    assert blank["details"] == {"budget_id": "  "}


def test_budget_stop_rejects_oversized_id_without_echoing_raw_input(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    init_code, init_stdout, init_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-oversized-budget-id",
        ]
    )
    assert init_code == 0, (init_stdout, init_stderr)

    oversized_id = "x" * 1_000_000
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "run",
            "budget-stop",
            "--budget-id",
            oversized_id,
        ]
    )

    assert exit_code == 2
    assert stdout == ""
    assert len(stderr.encode("utf-8")) <= 1024
    assert oversized_id not in stderr
    error = _single_json_object(stderr)
    assert error["ok"] is False
    assert error["command"] == "run.budget-stop"
    assert error["code"] == "invalid_budget_id"
    assert error["details"] == {}


def test_budget_stop_rejects_one_byte_over_derived_id_limit_before_lookup_or_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.contracts.state import RUNNER_SESSION_TEXT_MAX_BYTES
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    workspace = tmp_path / "workspace"
    init_code, init_stdout, init_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-budget-stop-boundary",
        ]
    )
    assert init_code == 0, (init_stdout, init_stderr)

    fixed_suspension_input_id = "daemon-budget::suspend"
    budget_id = "x" * (
        RUNNER_SESSION_TEXT_MAX_BYTES
        - len(fixed_suspension_input_id.encode("utf-8"))
        + 1
    )
    assert len(budget_id.encode("utf-8")) == 4075
    lookups: list[str] = []
    mutations: list[object] = []
    original_load = SQLiteRuntimeStore.load_daemon_budget_epoch
    original_persist = SQLiteRuntimeStore.persist_runtime_state

    def record_lookup(store: object, value: str) -> object:
        lookups.append(value)
        return original_load(store, value)  # type: ignore[arg-type]

    def record_mutation(store: object, *args: object, **kwargs: object) -> object:
        mutations.append((args, kwargs))
        return original_persist(store, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        SQLiteRuntimeStore,
        "load_daemon_budget_epoch",
        record_lookup,
    )
    monkeypatch.setattr(
        SQLiteRuntimeStore,
        "persist_runtime_state",
        record_mutation,
    )

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "run",
            "budget-stop",
            "--budget-id",
            budget_id,
        ]
    )

    assert exit_code == 2
    assert stdout == ""
    assert len(stderr.encode("utf-8")) <= 1024
    assert budget_id not in stderr
    error = _single_json_object(stderr)
    assert error["command"] == "run.budget-stop"
    assert error["code"] == "invalid_budget_id"
    assert error["details"] == {}
    assert lookups == []
    assert mutations == []


def test_cli_has_no_runtime_dependency_added_without_review() -> None:
    runtime_dependencies = PROJECT_METADATA.get("dependencies", [])
    normalized = {
        dependency.split("[", 1)[0].split("=", 1)[0].split("<", 1)[0].lower()
        for dependency in runtime_dependencies
    }

    assert "click" not in normalized
    assert "typer" not in normalized
