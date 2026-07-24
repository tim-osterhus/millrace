from __future__ import annotations

import sys
from pathlib import Path

from support.installed_workflow_packages import (
    SENTINEL_DISTRIBUTION_NAME,
    SENTINEL_PACKAGE_NAME,
    InstalledWorkflowPackageFixture,
    build_installed_workflow_package_wheel,
    install_wheel_to_target,
)
from support.workflow_packages import workflow_package_archive_bytes


def _install_wheel_fixture(
    tmp_path: Path,
) -> tuple[Path, InstalledWorkflowPackageFixture]:
    wheel_path, fixture = build_installed_workflow_package_wheel(
        tmp_path / "wheel-build"
    )
    target_install = tmp_path / "target-install"
    install_wheel_to_target(wheel_path, target_install)
    return target_install, fixture


def test_installed_wheel_discovery_reads_package_bytes_without_importing_modules(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    target_install, _fixture = _install_wheel_fixture(tmp_path)
    monkeypatch.syspath_prepend(str(target_install))
    sys.modules.pop(SENTINEL_PACKAGE_NAME, None)

    source = read_installed_workflow_package_source(SENTINEL_DISTRIBUTION_NAME)

    assert source.manifest is not None
    assert source.diagnostics == ()
    assert SENTINEL_PACKAGE_NAME not in sys.modules


def test_installed_wheel_import_installed_commits_same_manifest_digest_as_archive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_archive_workflow_package_source,
        read_installed_workflow_package_source,
    )
    from millrace.substrate.cas import ContentAddressedByteStore
    from millrace.substrate.sqlite import SQLiteRuntimeStore

    target_install, fixture = _install_wheel_fixture(tmp_path)
    monkeypatch.syspath_prepend(str(target_install))
    archive_source = read_archive_workflow_package_source(
        workflow_package_archive_bytes(manifest=fixture.manifest)
    )
    installed_source = read_installed_workflow_package_source(
        SENTINEL_DISTRIBUTION_NAME,
    )
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(tmp_path / "cas")

    installed = store.import_workflow_package_source(
        cas_store,
        installed_source,
        actor_id="operator:local",
    )

    assert archive_source.manifest is not None
    assert installed.manifest_digest == archive_source.manifest.manifest_digest
