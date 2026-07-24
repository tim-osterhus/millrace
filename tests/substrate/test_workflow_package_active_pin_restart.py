from __future__ import annotations

from pathlib import Path

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from support.workflow_package_active_pinning import (
    archive_active_harness,
    delete_selected_plan_cas,
    installed_active_harness,
    mutate_package_status,
    reopened_runtime_state,
    selected_plan_cas_path,
    selected_plan_digest,
    update_archive_package,
)


def test_restart_preserves_selected_package_pins(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)
    loaded = reopened_runtime_state(harness)
    loaded_plan = loaded.admitted_plans[harness.fingerprint].selected_plan
    loaded_pin = loaded_plan.workflow_package_pin

    assert loaded_pin == harness.plan.workflow_package_pin
    assert loaded_pin is not None
    assert loaded_pin.package_id == harness.package_id
    assert loaded_pin.package_version == harness.package_version


def test_restart_preserves_selected_package_asset_pins(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)
    loaded = reopened_runtime_state(harness)
    loaded_plan = loaded.admitted_plans[harness.fingerprint].selected_plan
    loaded_pin = loaded_plan.workflow_package_pin

    assert loaded_pin is not None
    assert loaded_pin.selected_asset_pins == (
        harness.plan.workflow_package_pin.selected_asset_pins
    )
    assert loaded_pin.selected_asset_pins[0].asset_id == "asset.prompt"
    assert loaded_pin.selected_asset_pins[0].content_digest.startswith("sha256:")


def test_restart_after_package_update_uses_admitted_compiled_plan(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)
    original_plan = harness.plan

    update_archive_package(harness)
    loaded = reopened_runtime_state(harness)

    assert loaded.admitted_plans[harness.fingerprint].selected_plan == original_plan
    assert loaded.runs["run-active"].run_ref.plan_ref.authority_fingerprint == (
        harness.fingerprint
    )


def test_restart_after_package_disable_remove_keeps_active_plan_unmodified(
    tmp_path: Path,
) -> None:
    disabled = archive_active_harness(tmp_path / "disabled")
    removed = archive_active_harness(tmp_path / "removed")

    mutate_package_status(
        disabled,
        operation_id="package.disable",
        command_id="disable-active",
    )
    mutate_package_status(
        removed,
        operation_id="package.remove",
        command_id="remove-active",
    )

    assert (
        reopened_runtime_state(disabled)
        .admitted_plans[disabled.fingerprint]
        .selected_plan
        == disabled.plan
    )
    assert (
        reopened_runtime_state(removed)
        .admitted_plans[removed.fingerprint]
        .selected_plan
        == removed.plan
    )


def test_restart_after_installed_package_disable_remove_keeps_active_plan_unmodified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled = installed_active_harness(tmp_path / "disabled", monkeypatch)
    removed = installed_active_harness(tmp_path / "removed", monkeypatch)

    mutate_package_status(
        disabled,
        operation_id="package.disable",
        command_id="disable-installed-active",
    )
    mutate_package_status(
        removed,
        operation_id="package.remove",
        command_id="remove-installed-active",
    )

    assert (
        reopened_runtime_state(disabled)
        .admitted_plans[disabled.fingerprint]
        .selected_plan
        == disabled.plan
    )
    assert (
        reopened_runtime_state(removed)
        .admitted_plans[removed.fingerprint]
        .selected_plan
        == removed.plan
    )


def test_restart_ignores_installed_registry_cas_damage_when_selected_plan_cas_is_intact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = installed_active_harness(tmp_path, monkeypatch)
    snapshot = harness.store.load_workflow_package_registry(harness.cas_store)
    record = snapshot.current_package(harness.package_id, harness.package_version)
    asset_digest = record.assets[0].cas_digest

    selected_digest = selected_plan_digest(harness.db_path)
    selected_plan_cas_path(harness.cas_root, selected_digest).read_bytes()
    selected_plan_cas_path(harness.cas_root, asset_digest).unlink()
    loaded = reopened_runtime_state(harness)

    assert loaded.admitted_plans[harness.fingerprint].selected_plan == harness.plan
    assert loaded.runs["run-active"].run_ref.plan_ref.authority_fingerprint == (
        harness.fingerprint
    )


def test_export_reload_preserves_package_backed_authority_fingerprint(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)
    loaded = reopened_runtime_state(harness)
    selected = loaded.admitted_plans[harness.fingerprint].selected_plan

    assert authority_fingerprint(selected) == harness.fingerprint
    assert selected.workflow_package_pin == harness.plan.workflow_package_pin


def test_missing_selected_plan_cas_is_restart_refusal(tmp_path: Path) -> None:
    harness = archive_active_harness(tmp_path)

    delete_selected_plan_cas(harness)

    store = SQLiteRuntimeStore.open(harness.db_path)
    try:
        with pytest.raises(Exception, match="selected_plan_digest"):
            store.load_runtime_state(ContentAddressedByteStore(harness.cas_root))
    finally:
        store.close()
