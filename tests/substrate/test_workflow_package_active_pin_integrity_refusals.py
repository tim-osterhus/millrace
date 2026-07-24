from __future__ import annotations

from pathlib import Path

import pytest

from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.workflow_package_active_pinning import (
    archive_active_harness,
    delete_selected_plan_cas,
    mutate_selected_plan_cas,
    reopened_runtime_state,
    workflow_package_pin,
)


def _assert_restart_refuses(harness, *, match: str | None = None) -> None:
    with pytest.raises(StorageIntegrityError, match=match):
        reopened_runtime_state(harness)


def test_restart_refuses_missing_selected_package_pin_fields(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_selected_plan_cas(
        harness,
        lambda payload: workflow_package_pin(payload).pop("package_id"),
    )

    _assert_restart_refuses(harness)


def test_restart_refuses_malformed_selected_package_pin_fields(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_selected_plan_cas(
        harness,
        lambda payload: workflow_package_pin(payload).__setitem__("entrypoint", ""),
    )

    _assert_restart_refuses(harness)


def test_restart_refuses_mismatched_selected_package_id_or_version(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_selected_plan_cas(
        harness,
        lambda payload: workflow_package_pin(payload).__setitem__(
            "package_version",
            "2.0.0",
        ),
    )

    _assert_restart_refuses(harness)


def test_restart_refuses_mismatched_pinned_workflow_id_or_version(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_selected_plan_cas(
        harness,
        lambda payload: workflow_package_pin(payload).__setitem__(
            "workflow_id",
            "wf.other",
        ),
    )

    _assert_restart_refuses(harness)


def test_restart_refuses_corrupt_selected_asset_pin(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)

    def corrupt_asset(payload: dict[str, object]) -> None:
        pin = workflow_package_pin(payload)
        assets = pin["selected_asset_pins"]
        assert isinstance(assets, list)
        asset = assets[0]
        assert isinstance(asset, dict)
        asset["content_digest"] = "sha256:not-a-valid-digest"

    mutate_selected_plan_cas(harness, corrupt_asset)

    _assert_restart_refuses(harness)


def test_restart_refuses_corrupt_selected_dependency_pin(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)

    def corrupt_dependency(payload: dict[str, object]) -> None:
        workflow_package_pin(payload)["selected_dependency_pins"] = [
            {
                "record_kind": "selected_workflow_package_dependency_pin",
                "schema_version": 1,
                "package_id": "",
                "package_version": "1.0.0",
                "package_format_version": "1",
            }
        ]

    mutate_selected_plan_cas(harness, corrupt_dependency)

    _assert_restart_refuses(harness)


def test_restart_refuses_selected_plan_with_registry_source_or_audit_pin_fields(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)

    def add_forbidden_fields(payload: dict[str, object]) -> None:
        pin = workflow_package_pin(payload)
        pin["source_kind"] = "archive"
        pin["manifest_digest"] = "sha256:" + ("1" * 64)
        pin["package_digest"] = "sha256:" + ("2" * 64)
        pin["import_record_digest"] = "sha256:" + ("3" * 64)
        pin["command_audit_id"] = "workflow-package-command-audit:forbidden"

    mutate_selected_plan_cas(harness, add_forbidden_fields)

    _assert_restart_refuses(harness)


def test_restart_refuses_missing_selected_plan_cas_object(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)

    delete_selected_plan_cas(harness)

    _assert_restart_refuses(harness, match="missing CAS object")


def test_restart_refuses_selected_plan_authority_fingerprint_mismatch(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_selected_plan_cas(
        harness,
        lambda payload: payload["workflow"].__setitem__("workflow_name", "Drifted"),
    )

    _assert_restart_refuses(harness)


def test_restart_refuses_installed_source_kind_inside_selected_package_pin(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_selected_plan_cas(
        harness,
        lambda payload: workflow_package_pin(payload).__setitem__(
            "source_kind",
            "installed_python_package",
        ),
    )

    _assert_restart_refuses(harness)


def test_open_store_after_refusal_does_not_repair_selected_plan(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)
    mutate_selected_plan_cas(
        harness,
        lambda payload: workflow_package_pin(payload).__setitem__("entrypoint", ""),
    )

    store = SQLiteRuntimeStore.open(harness.db_path)
    try:
        with pytest.raises(StorageIntegrityError):
            store.load_runtime_state(harness.cas_store)
        with pytest.raises(StorageIntegrityError):
            store.load_runtime_state(harness.cas_store)
    finally:
        store.close()
