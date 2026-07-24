from __future__ import annotations

from pathlib import Path
from typing import get_type_hints

import pytest

from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.transition import AdmitPlan
from millrace.operator.packages import (
    PackageWorkflowSelectionCommand,
    execute_package_workflow_selection_command,
)
from support.workflow_package_active_pinning import (
    ARCHIVE_PACKAGE_ID,
    ARCHIVE_PACKAGE_VERSION,
    ARCHIVE_WORKFLOW_ID,
    archive_active_harness,
    assert_source_kind_not_selected_authority,
    close_active_run,
    installed_active_harness,
    mutate_package_status,
    update_archive_package,
)


def _selection_failure_codes(harness) -> set[str]:
    result = execute_package_workflow_selection_command(
        harness.store,
        harness.cas_store,
        PackageWorkflowSelectionCommand(
            command_id=f"select-after-{harness.package_id}-{harness.source_kind}",
            actor_id="operator:local",
            package_id=harness.package_id,
            package_version=harness.package_version,
            workflow_id=harness.workflow_id,
            workflow_version=harness.workflow_version,
        ),
    )
    assert result.outcome == "failed"
    return {diagnostic.code for diagnostic in result.diagnostics}


def _kernel_source_text() -> str:
    root = Path("src/millrace/kernel")
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
    )


def test_active_run_continues_with_pinned_plan_after_package_update(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)
    original_ref = harness.state.runs["run-active"].run_ref.plan_ref

    update_archive_package(harness)
    closed = close_active_run(harness.state, fingerprint=harness.fingerprint)

    assert "work-active" in closed.closed_work_items
    assert closed.runs["run-active"].run_ref.plan_ref == original_ref
    assert closed.admitted_plans[harness.fingerprint].selected_plan == harness.plan


def test_active_run_continues_with_pinned_plan_after_package_disable(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_package_status(
        harness,
        operation_id="package.disable",
        command_id="disable-active",
    )
    closed = close_active_run(harness.state, fingerprint=harness.fingerprint)

    assert "work-active" in closed.closed_work_items
    assert closed.runs["run-active"].run_ref.plan_ref.authority_fingerprint == (
        harness.fingerprint
    )


def test_active_run_continues_with_pinned_plan_after_package_remove(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)

    mutate_package_status(
        harness,
        operation_id="package.remove",
        command_id="remove-active",
    )
    closed = close_active_run(harness.state, fingerprint=harness.fingerprint)

    assert "work-active" in closed.closed_work_items
    assert closed.runs["run-active"].run_ref.plan_ref.authority_fingerprint == (
        harness.fingerprint
    )


def test_new_selection_refuses_disabled_or_removed_package_while_active_pin_continues(
    tmp_path: Path,
) -> None:
    disabled = archive_active_harness(tmp_path / "disabled")
    mutate_package_status(
        disabled,
        operation_id="package.disable",
        command_id="disable-active",
    )

    removed = archive_active_harness(tmp_path / "removed")
    mutate_package_status(
        removed,
        operation_id="package.remove",
        command_id="remove-active",
    )

    assert _selection_failure_codes(disabled) == {
        "package_selection_package_status_refused"
    }
    assert _selection_failure_codes(removed) == {
        "package_selection_package_status_refused"
    }
    assert "work-active" in close_active_run(
        disabled.state,
        fingerprint=disabled.fingerprint,
        input_id="observe-disabled-active",
    ).closed_work_items
    assert "work-active" in close_active_run(
        removed.state,
        fingerprint=removed.fingerprint,
        input_id="observe-removed-active",
    ).closed_work_items


def test_installed_active_run_continues_with_pinned_plan_after_package_disable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = installed_active_harness(tmp_path, monkeypatch)

    mutate_package_status(
        harness,
        operation_id="package.disable",
        command_id="disable-installed-active",
    )
    closed = close_active_run(harness.state, fingerprint=harness.fingerprint)

    assert "work-active" in closed.closed_work_items
    assert closed.runs["run-active"].run_ref.plan_ref.authority_fingerprint == (
        harness.fingerprint
    )


def test_installed_active_run_continues_with_pinned_plan_after_package_remove(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = installed_active_harness(tmp_path, monkeypatch)

    mutate_package_status(
        harness,
        operation_id="package.remove",
        command_id="remove-installed-active",
    )
    closed = close_active_run(harness.state, fingerprint=harness.fingerprint)

    assert "work-active" in closed.closed_work_items
    assert closed.runs["run-active"].run_ref.plan_ref.authority_fingerprint == (
        harness.fingerprint
    )


def test_installed_active_run_does_not_treat_source_kind_as_runtime_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = installed_active_harness(tmp_path, monkeypatch)

    assert harness.plan.workflow_package_pin is not None
    assert_source_kind_not_selected_authority(harness.plan)
    closed = close_active_run(harness.state, fingerprint=harness.fingerprint)

    assert "work-active" in closed.closed_work_items


def test_kernel_transition_does_not_reresolve_package_registry_state(
    tmp_path: Path,
) -> None:
    harness = archive_active_harness(tmp_path)
    snapshot_before = harness.store.load_workflow_package_registry(harness.cas_store)

    update_archive_package(harness)
    closed = close_active_run(harness.state, fingerprint=harness.fingerprint)

    assert "work-active" in closed.closed_work_items
    assert snapshot_before.records[0].package_generation == 1
    current = harness.store.load_workflow_package_registry(
        harness.cas_store
    ).current_package(
        harness.package_id,
        harness.package_version,
    )
    assert current.package_generation == 2
    assert closed.admitted_plans[harness.fingerprint].selected_plan == harness.plan


def test_kernel_transition_does_not_branch_on_package_source_kind() -> None:
    source = _kernel_source_text()

    assert "installed_python_package" not in source
    assert "workflow_package_sources" not in source
    assert "importlib.metadata" not in source
    assert "importlib.resources" not in source


def test_kernel_transition_does_not_branch_on_package_or_workflow_name() -> None:
    source = _kernel_source_text()

    assert ARCHIVE_PACKAGE_ID not in source
    assert ARCHIVE_PACKAGE_VERSION not in source
    assert ARCHIVE_WORKFLOW_ID not in source


def test_admit_plan_accepts_typed_package_backed_selected_plan_without_registry_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = archive_active_harness(tmp_path)

    def forbidden_registry_load(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("kernel admission must not read package registry")

    monkeypatch.setattr(
        harness.store,
        "load_workflow_package_registry",
        forbidden_registry_load,
    )
    state = close_active_run(harness.state, fingerprint=harness.fingerprint)

    assert "work-active" in state.closed_work_items
    assert state.admitted_plans[harness.fingerprint].selected_plan == harness.plan


def test_admission_has_no_raw_serialized_package_pin_entrypoint() -> None:
    hints = get_type_hints(AdmitPlan)

    assert hints["selected_plan"] is SelectedCompiledPlan
