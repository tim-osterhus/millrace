from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from support.workflow_package_active_pinning import package_manifest
from support.workflow_packages import (
    workflow_package_archive_bytes,
    write_workflow_package_path,
)

ASSET_BYTES = b"CLI package prompt\n"


def _invoke(argv: list[str]) -> tuple[int, str, str]:
    from millrace.adapters.cli.main import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def _json(raw: str) -> dict[str, Any]:
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    return parsed


def _init_workspace(workspace: Path) -> None:
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "workspace",
            "init",
            "--input-id",
            "init-workspace",
        ]
    )
    assert exit_code == 0, (stdout, stderr)


def _open_state(workspace: Path):
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    store = SQLiteRuntimeStore.open(workspace / ".millrace" / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(workspace / ".millrace" / "cas")
    return store, cas_store, store.load_runtime_state(cas_store)


def _valid_manifest(*, source_kind: str = "archive") -> dict[str, object]:
    return package_manifest(
        package_id="pkg.example.cli",
        package_version="1.0.0",
        workflow_id="wf.cli",
        workflow_version="1",
        source_kind=source_kind,
        asset_bytes=ASSET_BYTES,
    )


def _write_archive(path: Path) -> None:
    path.write_bytes(
        workflow_package_archive_bytes(
            manifest=_valid_manifest(source_kind="archive"),
            asset_bytes=ASSET_BYTES,
        )
    )


def _import_archive(workspace: Path, archive_path: Path, *, command_id: str) -> None:
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "import-archive",
            str(archive_path),
            "--command-id",
            command_id,
        ]
    )
    assert exit_code == 0, (stdout, stderr)


def _enable_package(workspace: Path, *, command_id: str = "cmd-enable") -> None:
    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "enable",
            "pkg.example.cli",
            "1.0.0",
            "--command-id",
            command_id,
        ]
    )
    assert exit_code == 0, (stdout, stderr)


def test_package_import_path_delegates_to_operator_api_and_records_command_audit(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    package_root = tmp_path / "package-root"
    package_root.mkdir()
    write_workflow_package_path(
        package_root,
        manifest=_valid_manifest(source_kind="path"),
        asset_bytes=ASSET_BYTES,
    )
    _init_workspace(workspace)
    store, cas_store, before_state = _open_state(workspace)

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "import-path",
            str(package_root),
            "--command-id",
            "cmd-import-path",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    payload = _json(stdout)
    assert payload["command"] == "package.import-path"
    assert payload["code"] == "package_command_succeeded"
    data = payload["data"]
    assert list(data) == ["audit", "package"]
    assert data["audit"]["operation_id"] == "package.import_path"
    assert data["package"]["package_id"] == "pkg.example.cli"
    assert data["package"]["status"] == "imported"
    audits = store.load_workflow_package_command_audit_events()
    assert [event.command_id for event in audits] == ["cmd-import-path"]
    assert audits[0].operation_id == "package.import_path"
    assert store.load_runtime_state(cas_store) == before_state


def test_package_lifecycle_commands_do_not_touch_runtime_state_and_do_not_collide(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    archive_path = tmp_path / "package.mrpkg.tar"
    _write_archive(archive_path)
    _init_workspace(workspace)
    _import_archive(workspace, archive_path, command_id="cmd-import")
    _enable_package(workspace, command_id="cmd-enable-1")
    store, cas_store, before_lifecycle = _open_state(workspace)

    for command, command_id in (
        ("disable", "cmd-disable-1"),
        ("enable", "cmd-enable-2"),
        ("disable", "cmd-disable-2"),
    ):
        exit_code, stdout, stderr = _invoke(
            [
                "--json",
                "--workspace",
                str(workspace),
                "package",
                command,
                "pkg.example.cli",
                "1.0.0",
                "--command-id",
                command_id,
            ]
        )
        assert exit_code == 0, (stdout, stderr)

    assert store.load_runtime_state(cas_store) == before_lifecycle
    lifecycle_events = [
        event
        for event in store.load_workflow_package_command_audit_events()
        if event.operation_id in {"package.enable", "package.disable"}
    ]
    assert [(event.command_id, event.status) for event in lifecycle_events] == [
        ("cmd-enable-1", "enabled"),
        ("cmd-disable-1", "disabled"),
        ("cmd-enable-2", "enabled"),
        ("cmd-disable-2", "disabled"),
    ]


def test_package_verify_and_doctor_do_not_admit_or_select_plan(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    archive_path = tmp_path / "package.mrpkg.tar"
    _write_archive(archive_path)
    _init_workspace(workspace)
    _import_archive(workspace, archive_path, command_id="cmd-import")
    _enable_package(workspace)
    store, cas_store, before_state = _open_state(workspace)

    verify_code, verify_stdout, verify_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "verify",
            "pkg.example.cli",
            "1.0.0",
            "--workflow-id",
            "wf.cli",
            "--workflow-version",
            "1",
            "--entrypoint",
            "default",
            "--command-id",
            "cmd-verify",
        ]
    )
    doctor_code, doctor_stdout, doctor_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "doctor",
            "pkg.example.cli",
            "1.0.0",
            "--command-id",
            "cmd-doctor",
        ]
    )

    assert verify_code == 0, (verify_stdout, verify_stderr)
    assert doctor_code == 0, (doctor_stdout, doctor_stderr)
    verify_payload = _json(verify_stdout)
    assert verify_payload["data"]["plan_ready"] is True
    diagnostics = verify_payload["data"]["diagnostics"]
    assert diagnostics == []
    assert _json(doctor_stdout)["data"]["overall_status"] == "healthy"
    after_state = store.load_runtime_state(cas_store)
    assert after_state == before_state
    assert after_state.admitted_plans == {}
    assert after_state.default_plan_ref is None


def test_package_select_workflow_returns_plan_without_hidden_default_plan(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    archive_path = tmp_path / "package.mrpkg.tar"
    _write_archive(archive_path)
    _init_workspace(workspace)
    _import_archive(workspace, archive_path, command_id="cmd-import")
    _enable_package(workspace)
    store, cas_store, before_state = _open_state(workspace)

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "select-workflow",
            "pkg.example.cli",
            "1.0.0",
            "--workflow-id",
            "wf.cli",
            "--workflow-version",
            "1",
            "--entrypoint",
            "default",
            "--command-id",
            "cmd-select",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    payload = _json(stdout)
    assert payload["command"] == "package.select-workflow"
    data = payload["data"]
    assert list(data) == ["plan", "diagnostics", "audit"]
    assert data["audit"]["operation_id"] == "package.select_workflow"
    assert data["plan"]["workflow_id"] == "wf.cli"
    assert data["plan"]["workflow_version"] == "1"
    assert data["plan"]["authority_fingerprint"].startswith("sha256:")
    assert data["diagnostics"] == []
    assert store.load_runtime_state(cas_store) == before_state


def test_package_json_outputs_have_stable_keys_and_deterministic_ordering(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    archive_path = tmp_path / "package.mrpkg.tar"
    _write_archive(archive_path)
    _init_workspace(workspace)
    _import_archive(workspace, archive_path, command_id="cmd-import")

    first_code, first_stdout, first_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "list",
            "--command-id",
            "cmd-list-1",
        ]
    )
    second_code, second_stdout, second_stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "list",
            "--command-id",
            "cmd-list-2",
        ]
    )

    assert first_code == second_code == 0
    assert first_stderr == second_stderr == ""
    first = _json(first_stdout)
    second = _json(second_stdout)
    assert list(first) == ["ok", "command", "code", "message", "data"]
    assert list(first["data"]) == ["audit", "packages"]
    assert first["data"]["audit"]["operation_id"] == "package.list"
    first_packages = first["data"]["packages"]
    second_packages = second["data"]["packages"]
    assert len(first_packages) == len(second_packages) == 1
    assert list(first_packages[0]) == [
        "identity",
        "package_id",
        "package_version",
        "package_generation",
        "status",
        "status_generation",
        "package_format_version",
        "manifest_digest",
        "package_digest",
        "source_kind",
        "assets",
        "dependencies",
        "provenance",
        "selectable",
        "unselectable_reason",
    ]
    assert list(first_packages[0]["assets"][0]) == [
        "asset_id",
        "package_path",
        "content_digest",
        "byte_length",
    ]
    assert list(first_packages[0]["provenance"]) == [
        "manifest_digest",
        "package_digest",
        "source_kind",
        "source_digest",
        "source_provenance_digest",
        "latest_registry_audit_id",
        "import_record_digest",
    ]
    assert first_packages == second_packages


def test_package_export_archive_writes_nested_relative_output_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    archive_path = tmp_path / "package.mrpkg.tar"
    output_dir = tmp_path / "exports"
    output_dir.mkdir()
    _write_archive(archive_path)
    _init_workspace(workspace)
    _import_archive(workspace, archive_path, command_id="cmd-import")
    monkeypatch.chdir(tmp_path)

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "--workspace",
            str(workspace),
            "package",
            "export-archive",
            "pkg.example.cli",
            "1.0.0",
            "--output",
            "exports/pkg.example.cli.mrpkg.tar",
            "--command-id",
            "cmd-export",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    payload = _json(stdout)
    assert payload["command"] == "package.export-archive"
    assert payload["data"]["audit"]["operation_id"] == "package.export_path"
    assert payload["data"]["archive_path"] == str(
        output_dir / "pkg.example.cli.mrpkg.tar"
    )
    assert (output_dir / "pkg.example.cli.mrpkg.tar").is_file()
