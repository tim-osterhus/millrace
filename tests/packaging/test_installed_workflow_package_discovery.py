from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from millrace.contracts.workflow_package import manifest_digest_for_manifest
from support.installed_workflow_packages import (
    DEFAULT_ASSET_BYTES,
    DEFAULT_ASSET_PATH,
    DEFAULT_RESOURCE_ROOT,
    SENTINEL_DISTRIBUTION_NAME,
    SENTINEL_PACKAGE_NAME,
    installed_workflow_package_manifest,
    write_installed_workflow_package,
)


def _discover(site_root: Path, monkeypatch):
    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    monkeypatch.syspath_prepend(str(site_root))
    return read_installed_workflow_package_source(SENTINEL_DISTRIBUTION_NAME)


def _diagnostic_codes(source) -> set[str]:
    return {diagnostic.code for diagnostic in source.diagnostics}


def _fail_if_read(monkeypatch: pytest.MonkeyPatch, forbidden_path: Path) -> None:
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == forbidden_path:
            raise AssertionError(f"read attempted for {forbidden_path}")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)


def test_installed_distribution_discovery_reads_manifest_and_assets_as_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = write_installed_workflow_package(tmp_path / "site")
    sys.modules.pop(SENTINEL_PACKAGE_NAME, None)

    source = _discover(fixture.site_root, monkeypatch)

    assert SENTINEL_PACKAGE_NAME not in sys.modules
    assert source.source_kind == "installed_python_package"
    assert source.source_uri == (
        f"python-dist://{SENTINEL_DISTRIBUTION_NAME}/{DEFAULT_RESOURCE_ROOT}"
    )
    assert source.manifest is not None
    assert source.diagnostics == ()
    assert source.asset_bytes_by_path == {
        DEFAULT_ASSET_PATH: DEFAULT_ASSET_BYTES,
    }
    assert source.member_paths == (DEFAULT_ASSET_PATH, "manifest.json")


def test_installed_distribution_discovery_uses_declared_resource_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    fixture = write_installed_workflow_package(
        tmp_path / "site",
        resource_root="custom_workflow_package",
    )
    monkeypatch.syspath_prepend(str(fixture.site_root))

    source = read_installed_workflow_package_source(
        SENTINEL_DISTRIBUTION_NAME,
        installed_resource_root="custom_workflow_package",
    )

    assert source.source_uri == (
        f"python-dist://{SENTINEL_DISTRIBUTION_NAME}/custom_workflow_package"
    )
    assert source.manifest is not None
    assert source.diagnostics == ()


def test_installed_distribution_discovery_refuses_invalid_resource_root_without_reading(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_installed_workflow_package_source,
    )

    fixture = write_installed_workflow_package(tmp_path / "site")
    outside = tmp_path / "escape"
    outside.mkdir()
    (outside / "manifest.json").write_bytes(fixture.manifest_bytes)
    monkeypatch.syspath_prepend(str(fixture.site_root))

    source = read_installed_workflow_package_source(
        SENTINEL_DISTRIBUTION_NAME,
        installed_resource_root="../escape",
    )

    assert source.manifest is None
    assert source.manifest_bytes == b""
    assert source.asset_bytes_by_path == {}
    assert source.member_paths == ()
    assert "invalid_installed_resource_root" in _diagnostic_codes(source)


def test_installed_distribution_discovery_refuses_missing_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = write_installed_workflow_package(tmp_path / "site")
    (fixture.resource_root_path / "manifest.json").unlink()

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "missing_manifest" in _diagnostic_codes(source)


def test_installed_distribution_discovery_refuses_missing_declared_asset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = write_installed_workflow_package(tmp_path / "site")
    (fixture.resource_root_path / DEFAULT_ASSET_PATH).unlink()

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "missing_declared_asset" in _diagnostic_codes(source)


def test_installed_discovery_refuses_no_read_bit_manifest_without_importing_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = write_installed_workflow_package(tmp_path / "site")
    manifest_path = fixture.resource_root_path / "manifest.json"
    manifest_path.chmod(0)
    _fail_if_read(monkeypatch, manifest_path)
    try:
        source = _discover(fixture.site_root, monkeypatch)
    finally:
        manifest_path.chmod(0o644)

    fixture.assert_not_imported()
    assert source.manifest is None
    assert "unreadable_package_file" in _diagnostic_codes(source)


def test_installed_discovery_refuses_no_read_bit_asset_without_importing_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = write_installed_workflow_package(tmp_path / "site")
    asset_path = fixture.resource_root_path / DEFAULT_ASSET_PATH
    asset_path.chmod(0)
    _fail_if_read(monkeypatch, asset_path)
    try:
        source = _discover(fixture.site_root, monkeypatch)
    finally:
        asset_path.chmod(0o644)

    fixture.assert_not_imported()
    assert source.manifest is None
    assert "unreadable_package_file" in _diagnostic_codes(source)


def test_installed_distribution_discovery_refuses_mismatched_manifest_digest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = installed_workflow_package_manifest()
    manifest["manifest_digest"] = "sha256:" + ("0" * 64)
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        manifest=manifest,
    )

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "manifest_digest_mismatch" in _diagnostic_codes(source)


def test_installed_distribution_discovery_refuses_mismatched_asset_digest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = write_installed_workflow_package(tmp_path / "site")
    (fixture.resource_root_path / DEFAULT_ASSET_PATH).write_bytes(b"changed\n")

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "asset_digest_mismatch" in _diagnostic_codes(source)


def test_installed_distribution_discovery_refuses_path_escape(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = installed_workflow_package_manifest(asset_path="../escape.md")
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        manifest=manifest,
        write_declared_assets=False,
        extra_resource_files=(("../escape.md", DEFAULT_ASSET_BYTES),),
    )

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "unsafe_package_path" in _diagnostic_codes(source)


def test_installed_distribution_discovery_refuses_missing_distribution_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = write_installed_workflow_package(tmp_path / "site")
    (fixture.dist_info / "RECORD").unlink()

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "missing_distribution_files" in _diagnostic_codes(source)


def test_installed_distribution_discovery_refuses_duplicate_normalized_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    normalized_asset_path = "assets/caf\u00e9.md"
    duplicate_asset_path = "assets/cafe\u0301.md"
    manifest = installed_workflow_package_manifest(asset_path=normalized_asset_path)
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        manifest=manifest,
        extra_resource_files=((duplicate_asset_path, DEFAULT_ASSET_BYTES),),
    )

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "duplicate_package_path" in _diagnostic_codes(source)


def test_installed_distribution_discovery_refuses_hidden_system_authority_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        extra_resource_files=((".hidden/secret.txt", b"ignored\n"),),
    )

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "hidden_system_authority_entry" in _diagnostic_codes(source)


def test_installed_distribution_discovery_refuses_undeclared_members_without_reading(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        extra_resource_files=(("secrets.txt", b"secret\n"),),
    )

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "undeclared_package_member" in _diagnostic_codes(source)
    assert "secrets.txt" not in source.asset_bytes_by_path
    assert "secrets.txt" not in source.member_paths


def test_installed_distribution_discovery_refuses_symlink_resource_root_escape(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = write_installed_workflow_package(tmp_path / "site")
    outside = tmp_path / "outside-package-root"
    outside.mkdir()
    (outside / "manifest.json").write_bytes(fixture.manifest_bytes)
    asset_path = outside / DEFAULT_ASSET_PATH
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(DEFAULT_ASSET_BYTES)
    shutil.rmtree(fixture.resource_root_path)
    fixture.resource_root_path.symlink_to(outside, target_is_directory=True)

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "unsafe_package_path" in _diagnostic_codes(source)


def test_installed_distribution_discovery_refuses_unsupported_manifest_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = installed_workflow_package_manifest()
    manifest["manifest_format_version"] = "999"
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    fixture = write_installed_workflow_package(
        tmp_path / "site",
        manifest=manifest,
    )

    source = _discover(fixture.site_root, monkeypatch)

    assert source.manifest is None
    assert "unsupported_manifest_format_version" in _diagnostic_codes(source)


def test_installed_distribution_discovery_source_digest_is_deterministic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = write_installed_workflow_package(tmp_path / "site")

    first = _discover(fixture.site_root, monkeypatch)
    second = _discover(fixture.site_root, monkeypatch)

    assert first.source_digest == second.source_digest
