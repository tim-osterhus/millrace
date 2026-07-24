from __future__ import annotations

from pathlib import Path

import pytest

from millrace.operator.packages import (
    PackageDoctorCommand,
    execute_package_doctor_command,
    project_current_workflow_package,
)
from millrace.operator.status import operator_status
from millrace.substrate.errors import StorageIntegrityError
from support.workflow_package_active_pinning import (
    archive_active_harness,
    installed_active_harness,
    mutate_package_status,
    mutate_selected_plan_cas,
    reopened_runtime_state,
    selected_plan_cas_path,
    selected_plan_digest,
    update_archive_package,
    workflow_package_pin,
)


def _doctor(harness, *, command_id: str = "doctor-active"):
    return execute_package_doctor_command(
        harness.store,
        harness.cas_store,
        PackageDoctorCommand(
            command_id=command_id,
            actor_id="operator:local",
            package_id=harness.package_id,
            package_version=harness.package_version,
            workflow_id=harness.workflow_id,
            workflow_version=harness.workflow_version,
        ),
    )


def _finding_categories(result) -> set[str]:
    return {finding.category for finding in result.findings}


def test_status_distinguishes_active_pin_retention_from_selectable_package(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_package_status(
        harness,
        operation_id="package.disable",
        command_id="disable-active",
    )
    result = _doctor(harness)

    assert result.package is not None
    assert result.package.selectable is False
    assert result.package.unselectable_reason == "package_status_disabled"
    assert result.active_pin_aftermath_category == (
        "active_pin_retained_after_package_disable"
    )
    assert "package_disabled" in _finding_categories(result)


def test_operator_status_reports_active_package_pin_without_registry_authority(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_package_status(
        harness,
        operation_id="package.disable",
        command_id="disable-active",
    )
    status = operator_status(reopened_runtime_state(harness))

    assert len(status.active_package_pins) == 1
    pin_status = status.active_package_pins[0]
    assert pin_status.authority_fingerprint == harness.fingerprint
    assert pin_status.package_id == harness.package_id
    assert pin_status.package_version == harness.package_version
    assert pin_status.workflow_id == harness.workflow_id
    assert pin_status.workflow_version == harness.workflow_version
    asset_pin = harness.plan.workflow_package_pin.selected_asset_pins[0]
    assert pin_status.selected_asset_pins == (
        ("asset.prompt", asset_pin.content_digest),
    )


def test_doctor_reports_active_pin_retained_after_disable(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_package_status(
        harness,
        operation_id="package.disable",
        command_id="disable-active",
    )
    result = _doctor(harness)

    assert result.active_pin_aftermath_category == (
        "active_pin_retained_after_package_disable"
    )
    assert "active_pin_retained" in _finding_categories(result)
    assert "active_pin_selected_plan_corrupt" not in _finding_categories(result)


def test_doctor_reports_active_pin_retained_after_remove(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_package_status(
        harness,
        operation_id="package.remove",
        command_id="remove-active",
    )
    result = _doctor(harness)

    assert result.active_pin_aftermath_category == (
        "active_pin_retained_after_package_remove"
    )
    assert "active_pin_retained" in _finding_categories(result)
    assert "package_removed" in _finding_categories(result)


def test_doctor_reports_corrupt_active_pin_as_restart_blocker(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)
    mutate_selected_plan_cas(
        harness,
        lambda payload: workflow_package_pin(payload).__setitem__("entrypoint", ""),
    )

    result = _doctor(harness)

    assert result.active_pin_aftermath_category == "active_pin_selected_plan_corrupt"
    assert "active_pin_selected_plan_corrupt" in _finding_categories(result)
    assert result.overall_status == "unhealthy"


def test_doctor_reports_corrupt_active_pin_without_repairing_selected_plan_or_registry(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)
    registry_before = harness.store.load_workflow_package_registry(harness.cas_store)
    mutate_selected_plan_cas(
        harness,
        lambda payload: workflow_package_pin(payload).__setitem__("entrypoint", ""),
    )

    result = _doctor(harness)

    assert result.active_pin_aftermath_category == "active_pin_selected_plan_corrupt"
    assert harness.store.load_workflow_package_registry(harness.cas_store) == (
        registry_before
    )
    with pytest.raises(StorageIntegrityError):
        reopened_runtime_state(harness)


def test_package_projection_does_not_mutate_active_run_after_update(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)
    state_before = reopened_runtime_state(harness)

    update_archive_package(harness)
    result = _doctor(harness)
    state_after = reopened_runtime_state(harness)

    assert result.active_pin_aftermath_category == (
        "active_pin_retained_after_package_update"
    )
    assert state_after == state_before


def test_status_reports_installed_source_kind_as_provenance_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = installed_active_harness(tmp_path, monkeypatch)
    projection = project_current_workflow_package(
        harness.store,
        harness.cas_store,
        harness.package_id,
        harness.package_version,
    )

    assert projection is not None
    assert projection.source_kind == "installed_python_package"
    assert projection.provenance.source_kind == "installed_python_package"
    assert harness.plan.workflow_package_pin is not None
    assert not hasattr(harness.plan.workflow_package_pin, "source_kind")


def test_doctor_reports_installed_active_pin_retained_after_disable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = installed_active_harness(tmp_path, monkeypatch)

    mutate_package_status(
        harness,
        operation_id="package.disable",
        command_id="disable-installed-active",
    )
    result = _doctor(harness)

    assert result.package is not None
    assert result.package.source_kind == "installed_python_package"
    assert result.active_pin_aftermath_category == (
        "active_pin_retained_after_package_disable"
    )


def test_doctor_reports_installed_registry_damage_with_intact_selected_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = installed_active_harness(tmp_path, monkeypatch)
    snapshot = harness.store.load_workflow_package_registry(harness.cas_store)
    record = snapshot.current_package(harness.package_id, harness.package_version)

    selected_digest = selected_plan_digest(harness.db_path)
    selected_plan_cas_path(harness.cas_root, selected_digest).read_bytes()
    mutate_package_status(
        harness,
        operation_id="package.disable",
        command_id="disable-installed-active",
    )
    selected_plan_cas_path(harness.cas_root, record.assets[0].cas_digest).unlink()
    result = _doctor(harness)
    loaded = reopened_runtime_state(harness)

    assert result.active_pin_aftermath_category == (
        "active_pin_retained_after_package_disable"
    )
    assert loaded.admitted_plans[harness.fingerprint].selected_plan == harness.plan
