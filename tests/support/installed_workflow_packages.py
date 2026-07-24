from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import sys
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)

DEFAULT_DISTRIBUTION_NAME = "millrace-wpkg-no-import-sentinel"
DEFAULT_IMPORT_PACKAGE_NAME = "millrace_wpkg_no_import_sentinel"
DEFAULT_RESOURCE_ROOT = "millrace_workflow_package"
DEFAULT_ASSET_PATH = "assets/prompt.md"
DEFAULT_ASSET_BYTES = b"operator prompt\n"

SENTINEL_DISTRIBUTION_NAME = DEFAULT_DISTRIBUTION_NAME
SENTINEL_PACKAGE_NAME = DEFAULT_IMPORT_PACKAGE_NAME

ManifestSource = dict[str, object]
Record = dict[str, object]


@dataclass(frozen=True, slots=True)
class InstalledWorkflowPackageFixture:
    site_packages: Path
    distribution_name: str
    import_package_name: str
    resource_root: str
    manifest: ManifestSource
    manifest_bytes: bytes
    asset_bytes_by_path: Mapping[str, bytes]
    marker_path: Path

    @property
    def site_root(self) -> Path:
        return self.site_packages

    @property
    def package_name(self) -> str:
        return self.import_package_name

    @property
    def asset_bytes(self) -> bytes:
        asset_bytes = tuple(self.asset_bytes_by_path.values())
        return b"" if not asset_bytes else asset_bytes[0]

    @property
    def dist_info(self) -> Path:
        return (
            self.site_packages
            / f"{_normalized_distribution_stem(self.distribution_name)}-1.0.0.dist-info"
        )

    @property
    def import_package_root(self) -> Path:
        return self.site_packages / self.import_package_name

    @property
    def resource_root_path(self) -> Path:
        return self.site_packages / self.resource_root

    def assert_not_imported(self) -> None:
        assert self.import_package_name not in sys.modules
        assert f"{self.import_package_name}.trap" not in sys.modules
        assert f"{self.import_package_name}.ordinary" not in sys.modules
        assert not self.marker_path.exists()


def installed_workflow_package_manifest(
    *,
    package_id: str = "pkg.example.installed",
    package_version: str = "1.0.0",
    workflow_id: str = "wf.installed",
    workflow_version: str = "1",
    asset_path: str = DEFAULT_ASSET_PATH,
    asset_bytes: bytes = DEFAULT_ASSET_BYTES,
    source_kind: str | None = None,
) -> ManifestSource:
    asset_digest = asset_digest_for_bytes(asset_bytes)
    package: Record = {
        "package_id": package_id,
        "package_version": package_version,
        "package_format_version": "1",
        "package_role": "workflow_package",
        "publisher": "Example",
        "base_millrace_compatibility": ">=0.22,<0.23",
    }
    if source_kind is not None:
        package["source_kind"] = source_kind
    manifest: ManifestSource = {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": package,
        "workflows": [
            {
                "workflow_id": workflow_id,
                "workflow_version": workflow_version,
                "visibility": "test_only",
                "entrypoints": ["default"],
                "selected_authority": {
                    "graphs": ["graph.installed"],
                    "stage_kinds": ["stage.installed"],
                    "terminal_outcomes": ["outcome.accepted"],
                    "terminal_actions": ["action.close"],
                },
                "required_assets": [
                    {"asset_id": "asset.prompt", "content_digest": asset_digest}
                ],
            }
        ],
        "assets": [
            {
                "asset_id": "asset.prompt",
                "asset_kind": "entrypoint_prompt",
                "media_type": "text/markdown; charset=utf-8",
                "encoding": "utf-8",
                "content_digest": asset_digest,
                "byte_length": len(asset_bytes),
                "package_path": asset_path,
                "selection": "required",
                "selected_authority_participation": "yes",
            }
        ],
        "dependencies": [],
        "compatibility": {"base_millrace": ">=0.22,<0.23"},
        "canonicalization": {"algorithm": "millrace-json-v1", "hash": "sha256"},
        "manifest_digest": None,
        "non_authoritative_metadata": {},
    }
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    return manifest


def manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")


def write_installed_workflow_package(
    site_packages: Path,
    *,
    distribution_name: str = DEFAULT_DISTRIBUTION_NAME,
    version: str = "1.0.0",
    import_package_name: str = DEFAULT_IMPORT_PACKAGE_NAME,
    package_name: str | None = None,
    resource_root: str = DEFAULT_RESOURCE_ROOT,
    manifest: ManifestSource | None = None,
    asset_bytes: bytes = DEFAULT_ASSET_BYTES,
    write_manifest: bool = True,
    write_declared_assets: bool = True,
    include_record: bool = True,
    extra_resource_files: tuple[tuple[str, bytes], ...] = (),
    extra_record_files: tuple[tuple[str, bytes], ...] = (),
) -> InstalledWorkflowPackageFixture:
    site_packages.mkdir(parents=True, exist_ok=True)
    import_package_name = package_name or import_package_name
    marker_path = site_packages / "_sentinel_import_marker.txt"
    manifest = (
        installed_workflow_package_manifest(asset_bytes=asset_bytes)
        if manifest is None
        else manifest
    )
    payloads = _fixture_payloads(
        distribution_name=distribution_name,
        version=version,
        import_package_name=import_package_name,
        resource_root=resource_root,
        manifest=manifest,
        asset_bytes=asset_bytes,
        marker_path=marker_path,
        write_manifest=write_manifest,
        write_declared_assets=write_declared_assets,
        extra_resource_files=extra_resource_files,
        extra_record_files=extra_record_files,
    )
    if include_record:
        record_path = _dist_info_dir(distribution_name, version) + "/RECORD"
        payloads[record_path] = _record_payload(payloads)
    for relative_path, payload in payloads.items():
        _write_relative(site_packages, relative_path, payload)
    return InstalledWorkflowPackageFixture(
        site_packages=site_packages,
        distribution_name=distribution_name,
        import_package_name=import_package_name,
        resource_root=resource_root,
        manifest=manifest,
        manifest_bytes=manifest_bytes(manifest),
        asset_bytes_by_path=_asset_bytes_by_path(manifest, asset_bytes),
        marker_path=marker_path,
    )


def build_installed_workflow_package_wheel(
    tmp_path: Path,
    *,
    distribution_name: str = DEFAULT_DISTRIBUTION_NAME,
    version: str = "1.0.0",
    import_package_name: str = DEFAULT_IMPORT_PACKAGE_NAME,
    resource_root: str = DEFAULT_RESOURCE_ROOT,
    manifest: ManifestSource | None = None,
    asset_bytes: bytes = DEFAULT_ASSET_BYTES,
) -> tuple[Path, InstalledWorkflowPackageFixture]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    marker_path = tmp_path / "_wheel_sentinel_import_marker.txt"
    manifest = (
        installed_workflow_package_manifest(asset_bytes=asset_bytes)
        if manifest is None
        else manifest
    )
    payloads = _fixture_payloads(
        distribution_name=distribution_name,
        version=version,
        import_package_name=import_package_name,
        resource_root=resource_root,
        manifest=manifest,
        asset_bytes=asset_bytes,
        marker_path=marker_path,
        write_manifest=True,
        write_declared_assets=True,
        extra_resource_files=(),
        extra_record_files=(),
    )
    record_path = _dist_info_dir(distribution_name, version) + "/RECORD"
    payloads[record_path] = _record_payload(payloads)
    wheel_path = tmp_path / (
        f"{_normalized_distribution_stem(distribution_name)}-{version}-"
        "py3-none-any.whl"
    )
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for relative_path, payload in sorted(payloads.items()):
            wheel.writestr(relative_path, payload)
    fixture = InstalledWorkflowPackageFixture(
        site_packages=tmp_path,
        distribution_name=distribution_name,
        import_package_name=import_package_name,
        resource_root=resource_root,
        manifest=manifest,
        manifest_bytes=manifest_bytes(manifest),
        asset_bytes_by_path=_asset_bytes_by_path(manifest, asset_bytes),
        marker_path=marker_path,
    )
    return wheel_path, fixture


def install_wheel_to_target(wheel_path: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel_path) as wheel:
        wheel.extractall(target)


def _fixture_payloads(
    *,
    distribution_name: str,
    version: str,
    import_package_name: str,
    resource_root: str,
    manifest: ManifestSource,
    asset_bytes: bytes,
    marker_path: Path,
    write_manifest: bool,
    write_declared_assets: bool,
    extra_resource_files: tuple[tuple[str, bytes], ...],
    extra_record_files: tuple[tuple[str, bytes], ...],
) -> dict[str, bytes]:
    dist_info = _dist_info_dir(distribution_name, version)
    payloads: dict[str, bytes] = {
        f"{import_package_name}/__init__.py": _hostile_module(
            marker_path,
            "__init__ imported",
        ),
        f"{import_package_name}/ordinary.py": _hostile_module(
            marker_path,
            "ordinary module imported",
        ),
        f"{import_package_name}/trap.py": _hostile_module(
            marker_path,
            "entry point trap imported",
        ),
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            f"Name: {distribution_name}\n"
            f"Version: {version}\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\n"
            b"Generator: millrace-test-fixture\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        ),
        f"{dist_info}/entry_points.txt": (
            "[millrace.workflow_packages]\n"
            f"trap = {import_package_name}.trap:main\n"
        ).encode(),
    }
    if write_manifest:
        payloads[f"{resource_root}/manifest.json"] = manifest_bytes(manifest)
    if write_declared_assets:
        for package_path, payload in _asset_bytes_by_path(
            manifest,
            asset_bytes,
        ).items():
            payloads[f"{resource_root}/{package_path}"] = payload
    for relative_path, payload in extra_resource_files:
        payloads[f"{resource_root}/{relative_path}"] = payload
    for relative_path, payload in extra_record_files:
        payloads[relative_path] = payload
    return payloads


def _asset_bytes_by_path(
    manifest: Mapping[str, object],
    asset_bytes: bytes,
) -> Mapping[str, bytes]:
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list):
        return {}
    result: dict[str, bytes] = {}
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            continue
        package_path = raw_asset.get("package_path")
        if isinstance(package_path, str):
            result[package_path] = asset_bytes
    return result


def _hostile_module(marker_path: Path, marker_text: str) -> bytes:
    return (
        "from pathlib import Path\n"
        f"Path({str(marker_path)!r}).write_text({marker_text!r}, encoding='utf-8')\n"
        f"raise RuntimeError({marker_text!r})\n"
    ).encode()


def _record_payload(payloads: Mapping[str, bytes]) -> bytes:
    rows: list[tuple[str, str, str]] = []
    for relative_path, payload in sorted(payloads.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
        hash_text = f"sha256={digest.rstrip(b'=').decode('ascii')}"
        rows.append((relative_path, hash_text, str(len(payload))))
    rows.append(("", "", ""))
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _write_relative(root: Path, relative_path: str, payload: bytes) -> None:
    target = root.joinpath(*PurePosixPath(relative_path).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _dist_info_dir(distribution_name: str, version: str) -> str:
    return f"{_normalized_distribution_stem(distribution_name)}-{version}.dist-info"


def _normalized_distribution_stem(distribution_name: str) -> str:
    return distribution_name.replace("-", "_").replace(".", "_")


__all__ = (
    "DEFAULT_ASSET_BYTES",
    "DEFAULT_ASSET_PATH",
    "DEFAULT_DISTRIBUTION_NAME",
    "DEFAULT_IMPORT_PACKAGE_NAME",
    "DEFAULT_RESOURCE_ROOT",
    "SENTINEL_DISTRIBUTION_NAME",
    "SENTINEL_PACKAGE_NAME",
    "InstalledWorkflowPackageFixture",
    "build_installed_workflow_package_wheel",
    "install_wheel_to_target",
    "installed_workflow_package_manifest",
    "manifest_bytes",
    "write_installed_workflow_package",
)
