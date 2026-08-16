from __future__ import annotations

import io
import json
import re
import sys
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
PROJECT_METADATA = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))[
    "project"
]
PROJECT_VERSION = PROJECT_METADATA["version"]
LOCKED_GROUPS = (
    "workspace",
    "package",
    "plan",
    "queue",
    "status",
    "runs",
    "trace",
    "waits",
    "interventions",
    "dispatch",
    "run",
)
RUNTIME_AUTHORITY_PREFIXES = (
    "millrace.compiler",
    "millrace.kernel",
    "millrace.operator",
    "millrace.substrate",
    "millrace.testing",
    "millrace.workflows",
)


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    from millrace.adapters.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def test_pyproject_exposes_millrace_console_script() -> None:
    scripts = PROJECT_METADATA.get("scripts", {})

    assert PROJECT_METADATA["name"] == "millrace-ai"
    assert PROJECT_VERSION == "0.22.2"
    assert scripts["millrace"] == "millrace.adapters.cli.main:cli"


def test_console_script_entrypoint_import_has_no_side_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    for module_name in list(sys.modules):
        if module_name == "millrace.adapters.cli" or module_name.startswith(
            "millrace.adapters.cli."
        ):
            sys.modules.pop(module_name)
    before_runtime_modules = {
        module_name
        for module_name in sys.modules
        if module_name.startswith(RUNTIME_AUTHORITY_PREFIXES)
    }

    imported = import_module("millrace.adapters.cli.main")

    after_runtime_modules = {
        module_name
        for module_name in sys.modules
        if module_name.startswith(RUNTIME_AUTHORITY_PREFIXES)
    }
    assert imported.__name__ == "millrace.adapters.cli.main"
    assert after_runtime_modules == before_runtime_modules
    assert list(tmp_path.iterdir()) == []


def test_root_help_lists_locked_command_groups() -> None:
    exit_code, stdout, stderr = _invoke(["--help"])

    assert exit_code == 0
    assert stderr == ""
    for group in LOCKED_GROUPS:
        assert re.search(rf"^\s+{re.escape(group)}\b", stdout, re.MULTILINE), stdout
    assert not re.search(r"^\s+daemon\b", stdout, re.MULTILINE)
    for forbidden in ("tick", "observe", "once", "invoke"):
        assert forbidden not in stdout
    for forbidden in ("Reserved", "later Millrace packet", "later commands"):
        assert forbidden not in stdout

    exit_code, stdout, stderr = _invoke(["run", "--help"])

    assert exit_code == 0
    assert stderr == ""
    assert re.search(r"^\s+daemon\b", stdout, re.MULTILINE), stdout
    for forbidden in ("tick", "observe", "once"):
        assert forbidden not in stdout

    exit_code, stdout, stderr = _invoke(["dispatch", "--help"])

    assert exit_code == 0
    assert stderr == ""
    assert "invoke" not in stdout


def test_registered_group_help_is_current_product_language() -> None:
    for group in LOCKED_GROUPS:
        exit_code, stdout, stderr = _invoke([group, "--help"])

        assert exit_code == 0
        assert stderr == ""
        assert "Reserved" not in stdout
        assert "later Millrace packet" not in stdout
        assert "later commands" not in stdout
        assert "later audited commands" not in stdout


def test_public_document_command_families_parse_under_current_help() -> None:
    documented_argv = (
        ("workspace", "init"),
        ("workspace", "check"),
        ("package", "import-installed"),
        ("package", "enable"),
        ("package", "list"),
        ("package", "verify"),
        ("plan", "admit-package"),
        ("plan", "select-default"),
        ("plan", "show"),
        ("queue", "enqueue"),
        ("queue", "list"),
        ("status",),
        ("runs", "list"),
        ("runs", "show"),
        ("trace", "show"),
        ("waits", "list"),
        ("waits", "resume"),
        ("interventions", "list"),
        ("interventions", "resume-lineage"),
        ("dispatch", "claim"),
        ("dispatch", "show"),
        ("doctor",),
        ("run", "daemon"),
    )

    for argv in documented_argv:
        exit_code, stdout, stderr = _invoke([*argv, "--help"])

        assert exit_code == 0, argv
        assert stderr == "", argv
        assert stdout.startswith("usage: millrace"), argv


def test_all_registered_leaf_commands_route_to_concrete_dispatchers(
    monkeypatch,
) -> None:
    from millrace.adapters.cli import main as cli_main

    command_cases = (
        (("workspace", "init", "--input-id", "input"), "_dispatch_workspace"),
        (("workspace", "check"), "_dispatch_workspace"),
        (
            ("package", "import-path", "/tmp/package", "--command-id", "command"),
            "_dispatch_package",
        ),
        (
            ("package", "import-archive", "/tmp/package", "--command-id", "command"),
            "_dispatch_package",
        ),
        (
            (
                "package",
                "import-installed",
                "millrace-plus",
                "--resource-root",
                "millrace_plus",
                "--command-id",
                "command",
            ),
            "_dispatch_package",
        ),
        (
            (
                "package",
                "update",
                "package.id",
                "1",
                "--from-path",
                "/tmp/package",
                "--command-id",
                "command",
            ),
            "_dispatch_package",
        ),
        (("package", "list", "--command-id", "command"), "_dispatch_package"),
        (
            ("package", "inspect", "package.id", "1", "--command-id", "command"),
            "_dispatch_package",
        ),
        (
            (
                "package",
                "export-archive",
                "package.id",
                "1",
                "--output",
                "/tmp/package.millrace",
                "--command-id",
                "command",
            ),
            "_dispatch_package",
        ),
        (
            ("package", "enable", "package.id", "1", "--command-id", "command"),
            "_dispatch_package",
        ),
        (
            ("package", "disable", "package.id", "1", "--command-id", "command"),
            "_dispatch_package",
        ),
        (
            ("package", "remove", "package.id", "1", "--command-id", "command"),
            "_dispatch_package",
        ),
        (
            (
                "package",
                "verify",
                "package.id",
                "1",
                "--workflow-id",
                "workflow.id",
                "--workflow-version",
                "1",
                "--entrypoint",
                "default",
                "--command-id",
                "command",
            ),
            "_dispatch_package",
        ),
        (
            (
                "package",
                "select-workflow",
                "package.id",
                "1",
                "--workflow-id",
                "workflow.id",
                "--workflow-version",
                "1",
                "--entrypoint",
                "default",
                "--command-id",
                "command",
            ),
            "_dispatch_package",
        ),
        (
            ("package", "doctor", "package.id", "1", "--command-id", "command"),
            "_dispatch_package",
        ),
        (
            (
                "plan",
                "admit",
                "--compiled-plan-json",
                "/tmp/plan.json",
                "--input-id",
                "input",
            ),
            "_dispatch_plan",
        ),
        (
            (
                "plan",
                "admit-package",
                "package.id",
                "1",
                "--workflow-id",
                "workflow.id",
                "--workflow-version",
                "1",
                "--entrypoint",
                "default",
                "--command-id",
                "command",
                "--input-id",
                "input",
            ),
            "_dispatch_plan",
        ),
        (
            ("plan", "select-default", "sha256:abc", "--input-id", "input"),
            "_dispatch_plan",
        ),
        (("plan", "show"), "_dispatch_plan"),
        (
            ("queue", "enqueue", "work", "--payload-json", "{}"),
            "_dispatch_queue",
        ),
        (
            (
                "queue",
                "cancel",
                "work.id",
                "--plan-fingerprint",
                "sha256:abc",
                "--input-id",
                "cancel.id",
                "--reason",
                "obsolete",
            ),
            "_dispatch_queue",
        ),
        (
            (
                "queue",
                "cancel-lineage",
                "lineage.id",
                "--plan-fingerprint",
                "sha256:abc",
                "--input-id",
                "cancel-lineage.id",
                "--reason",
                "obsolete",
            ),
            "_dispatch_queue",
        ),
        (("queue", "list"), "_dispatch_queue"),
        (("status",), "_dispatch_status"),
            (("runs", "list"), "_dispatch_status"),
            (("runs", "show", "run.id"), "_dispatch_status"),
            (
                ("runs", "cancel", "run.id", "--input-id", "cancel.id"),
                "_dispatch_status",
            ),
            (
                ("runs", "follow", "run.id", "--after-sequence", "0"),
                "_dispatch_status",
            ),
        (("trace", "show"), "_dispatch_status"),
        (("waits", "list"), "_dispatch_status"),
        (("waits", "resume", "wait.id"), "_dispatch_intervention"),
        (("waits", "close", "wait.id"), "_dispatch_intervention"),
        (("waits", "revise", "wait.id"), "_dispatch_intervention"),
        (("interventions", "list"), "_dispatch_status"),
        (
            (
                "interventions",
                "resume-lineage",
                "option.id",
                "--reason",
                "resume",
            ),
            "_dispatch_intervention",
        ),
        (
            (
                "interventions",
                "close-lineage",
                "option.id",
                "--reason",
                "close",
            ),
            "_dispatch_intervention",
        ),
        (
            (
                "interventions",
                "revise-lineage",
                "option.id",
                "--reason",
                "revise",
            ),
            "_dispatch_intervention",
        ),
        (("dispatch", "claim", "activation.id"), "_dispatch_dispatch"),
        (
            (
                "dispatch",
                "suspend",
                "--plan-fingerprint",
                "sha256:abc",
                "--input-id",
                "suspend.id",
                "--reason",
                "maintenance",
            ),
            "_dispatch_dispatch",
        ),
        (
            (
                "dispatch",
                "resume",
                "--plan-fingerprint",
                "sha256:abc",
                "--suspension-id",
                "suspension.id",
                "--input-id",
                "resume.id",
                "--reason",
                "done",
            ),
            "_dispatch_dispatch",
        ),
        (("dispatch", "show", "run.id"), "_dispatch_dispatch"),
        (("doctor",), "_dispatch_doctor"),
        (("run", "daemon"), "_dispatch_run"),
        (("run", "budget-stop", "--budget-id", "budget.id"), "_dispatch_run"),
    )
    called: list[str] = []

    def dispatcher(name: str):
        def dispatch(_namespace) -> int:
            called.append(name)
            return 0

        return dispatch

    dispatcher_names = {expected for _argv, expected in command_cases}
    for name in dispatcher_names:
        monkeypatch.setattr(cli_main, name, dispatcher(name))

    for argv, expected in command_cases:
        called.clear()
        assert cli_main.main(argv) == 0, argv
        assert called == [expected], argv

    _parser, help_parsers = cli_main._build_parser()
    registered_leaves = {
        command
        for command in help_parsers
        if command in {"status", "doctor"} or "." in command
    }
    assert len(command_cases) == 44
    assert registered_leaves == {
        ".".join(argv[:2]) if argv[0] not in {"status", "doctor"} else argv[0]
        for argv, _expected in command_cases
    }


def test_version_output_is_deterministic_in_text_and_json() -> None:
    first_code, first_stdout, first_stderr = _invoke(["--version"])
    second_code, second_stdout, second_stderr = _invoke(["--version"])

    assert first_code == second_code == 0
    assert first_stderr == second_stderr == ""
    assert first_stdout == second_stdout == f"millrace {PROJECT_VERSION}\n"

    first_code, first_stdout, first_stderr = _invoke(["--json", "--version"])
    second_code, second_stdout, second_stderr = _invoke(["--json", "--version"])

    assert first_code == second_code == 0
    assert first_stderr == second_stderr == ""
    assert first_stdout == second_stdout
    assert len(first_stdout.strip().splitlines()) == 1
    assert json.loads(first_stdout) == {
        "ok": True,
        "command": "version",
        "code": "ok",
        "message": f"millrace {PROJECT_VERSION}",
        "data": {"version": PROJECT_VERSION},
    }
