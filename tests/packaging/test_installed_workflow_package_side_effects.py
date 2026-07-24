from __future__ import annotations

import sys
from pathlib import Path

from support.installed_workflow_packages import (
    SENTINEL_DISTRIBUTION_NAME,
    SENTINEL_PACKAGE_NAME,
    write_installed_workflow_package,
)


def test_installed_discovery_does_not_execute_package_init(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    fixture = write_installed_workflow_package(tmp_path / "site")
    monkeypatch.syspath_prepend(str(fixture.site_root))
    sys.modules.pop(SENTINEL_PACKAGE_NAME, None)

    source = read_installed_workflow_package_source(SENTINEL_DISTRIBUTION_NAME)

    assert source.diagnostics == ()
    assert SENTINEL_PACKAGE_NAME not in sys.modules


def test_installed_discovery_does_not_import_package_module(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    fixture = write_installed_workflow_package(tmp_path / "site")
    monkeypatch.syspath_prepend(str(fixture.site_root))
    sys.modules.pop(f"{SENTINEL_PACKAGE_NAME}.trap", None)

    source = read_installed_workflow_package_source(SENTINEL_DISTRIBUTION_NAME)

    assert source.diagnostics == ()
    assert f"{SENTINEL_PACKAGE_NAME}.trap" not in sys.modules


def test_installed_discovery_does_not_load_entry_points(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import importlib.metadata

    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    fixture = write_installed_workflow_package(tmp_path / "site")
    monkeypatch.syspath_prepend(str(fixture.site_root))

    def forbidden_entry_points(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("installed discovery must not load entry points")

    monkeypatch.setattr(importlib.metadata, "entry_points", forbidden_entry_points)

    source = read_installed_workflow_package_source(SENTINEL_DISTRIBUTION_NAME)

    assert source.diagnostics == ()


def test_installed_discovery_does_not_use_importlib_resources_package_import(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import importlib.resources

    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    fixture = write_installed_workflow_package(tmp_path / "site")
    monkeypatch.syspath_prepend(str(fixture.site_root))

    def forbidden_files(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("installed discovery must not use importlib.resources")

    monkeypatch.setattr(importlib.resources, "files", forbidden_files)

    source = read_installed_workflow_package_source(SENTINEL_DISTRIBUTION_NAME)

    assert source.diagnostics == ()


def test_installed_fixture_setup_does_not_preimport_sentinel_package(
    tmp_path: Path,
) -> None:
    sys.modules.pop(SENTINEL_PACKAGE_NAME, None)

    write_installed_workflow_package(tmp_path / "site")

    assert SENTINEL_PACKAGE_NAME not in sys.modules
