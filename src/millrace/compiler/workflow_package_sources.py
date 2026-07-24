"""Data-only workflow package archive and path source readers."""

from __future__ import annotations

import importlib.metadata
import io
import json
import os
import stat
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import cast

from millrace.compiler.diagnostics import compiler_error
from millrace.compiler.workflow_package_manifest import (
    validate_importable_workflow_package_manifest,
)
from millrace.contracts import Diagnostic
from millrace.contracts.workflow_package import (
    WorkflowPackageManifest,
    asset_digest_for_bytes,
)
from millrace.contracts.workflow_package_paths import (
    WorkflowPackagePathPolicyError,
    is_ignored_package_path,
    is_regular_package_tar_member,
    normalized_package_path_key,
    validate_package_path,
)


@dataclass(frozen=True, slots=True)
class WorkflowPackageSourceRead:
    source_kind: str
    source_uri: str
    source_digest: str
    manifest_source: Mapping[str, object]
    manifest: WorkflowPackageManifest | None
    diagnostics: tuple[Diagnostic, ...]
    manifest_bytes: bytes
    asset_bytes_by_path: Mapping[str, bytes]
    member_paths: tuple[str, ...]


_MANIFEST_PATH = "manifest.json"
_SOURCE_DIGEST_DOMAIN = b"millrace.wpkg.source.v1\0"
_READABLE_DIAGNOSTICS = {
    "asset_digest_mismatch": "asset digest mismatch",
    "asset_byte_length_mismatch": "asset byte length mismatch",
    "duplicate_package_path": "duplicate package path",
    "hardlink_package_file": "hardlink package file",
    "hidden_system_authority_entry": "hidden system authority entry",
    "invalid_manifest_json": "invalid manifest JSON",
    "invalid_installed_resource_root": "invalid installed resource root",
    "missing_distribution": "missing installed distribution",
    "missing_distribution_files": "missing installed distribution files",
    "missing_distribution_file": "missing installed distribution file",
    "missing_declared_asset": "missing declared asset",
    "missing_manifest": "missing manifest",
    "non_regular_package_file": "non-regular package file",
    "non_regular_package_member": "non-regular package member",
    "noncanonical_tar_metadata": "noncanonical tar metadata",
    "non_nfc_package_path": "non-NFC package path",
    "uncompressed_posix_tar_required": "uncompressed POSIX tar required",
    "undeclared_package_member": "undeclared package member",
    "unsafe_package_path": "unsafe package path",
    "unreadable_package_file": "unreadable package file",
}


def read_path_workflow_package_source(
    package_root: str | Path,
) -> WorkflowPackageSourceRead:
    root = Path(package_root)
    source_uri = str(root.resolve())
    diagnostics: list[Diagnostic] = []
    manifest_bytes = _read_path_bytes(root, _MANIFEST_PATH, diagnostics)
    manifest_source = _manifest_source_from_bytes(manifest_bytes, diagnostics)
    validation = validate_importable_workflow_package_manifest(manifest_source)
    diagnostics.extend(validation.diagnostics)
    _scan_path_root(root, diagnostics)
    asset_bytes_by_path = _read_declared_path_assets(
        root,
        manifest_source,
        diagnostics,
    )
    _validate_declared_assets(manifest_source, asset_bytes_by_path, diagnostics)
    member_paths = tuple(sorted((_MANIFEST_PATH, *asset_bytes_by_path)))
    source_members = (
        (_MANIFEST_PATH, manifest_bytes),
        *tuple(asset_bytes_by_path.items()),
    )
    return WorkflowPackageSourceRead(
        source_kind="path",
        source_uri=source_uri,
        source_digest=_source_digest("path", source_members),
        manifest_source=manifest_source,
        manifest=validation.manifest if not diagnostics else None,
        diagnostics=tuple(diagnostics),
        manifest_bytes=manifest_bytes,
        asset_bytes_by_path=asset_bytes_by_path,
        member_paths=member_paths,
    )


def read_archive_workflow_package_source(
    archive_bytes: bytes,
    *,
    source_uri: str = "memory://workflow-package.mrpkg.tar",
) -> WorkflowPackageSourceRead:
    diagnostics: list[Diagnostic] = []
    member_bytes = _read_archive_members(archive_bytes, diagnostics)
    manifest_bytes = member_bytes.get(_MANIFEST_PATH, b"")
    if _MANIFEST_PATH not in member_bytes:
        diagnostics.append(_source_error("missing_manifest", _MANIFEST_PATH))
    manifest_source = _manifest_source_from_bytes(manifest_bytes, diagnostics)
    validation = validate_importable_workflow_package_manifest(manifest_source)
    diagnostics.extend(validation.diagnostics)
    asset_bytes_by_path = {
        package_path: payload
        for package_path, payload in sorted(member_bytes.items())
        if package_path != _MANIFEST_PATH
    }
    _validate_declared_assets(manifest_source, asset_bytes_by_path, diagnostics)
    member_paths = tuple(sorted(member_bytes))
    return WorkflowPackageSourceRead(
        source_kind="archive",
        source_uri=source_uri,
        source_digest=_bytes_digest(archive_bytes),
        manifest_source=manifest_source,
        manifest=validation.manifest if not diagnostics else None,
        diagnostics=tuple(diagnostics),
        manifest_bytes=manifest_bytes,
        asset_bytes_by_path=asset_bytes_by_path,
        member_paths=member_paths,
    )


def read_installed_workflow_package_source(
    installed_distribution_name: str,
    *,
    installed_resource_root: str = "millrace_workflow_package",
) -> WorkflowPackageSourceRead:
    diagnostics: list[Diagnostic] = []
    resource_root = _validate_package_path(installed_resource_root, diagnostics)
    if resource_root is None:
        diagnostics.append(
            _source_error("invalid_installed_resource_root", installed_resource_root)
        )
        return _installed_read_result(
            distribution_name=installed_distribution_name,
            resource_root=installed_resource_root,
            member_bytes={},
            diagnostics=diagnostics,
        )
    try:
        distribution = importlib.metadata.distribution(installed_distribution_name)
    except importlib.metadata.PackageNotFoundError:
        diagnostics.append(
            _source_error("missing_distribution", installed_distribution_name)
        )
        return _installed_read_result(
            distribution_name=installed_distribution_name,
            resource_root=resource_root,
            member_bytes={},
            diagnostics=diagnostics,
        )
    distribution_files = distribution.files
    if distribution_files is None:
        diagnostics.append(
            _source_error("missing_distribution_files", installed_distribution_name)
        )
        return _installed_read_result(
            distribution_name=installed_distribution_name,
            resource_root=resource_root,
            member_bytes={},
            diagnostics=diagnostics,
        )
    distribution_root = Path(str(distribution.locate_file("")))
    package_files: dict[str, Path] = {}
    seen_normalized_paths: set[str] = set()
    for distribution_file in sorted(
        distribution_files,
        key=lambda item: item.as_posix(),
    ):
        relative_path = _installed_member_path(
            distribution_file.as_posix(),
            resource_root,
        )
        if relative_path is None:
            continue
        normalized_path = normalized_package_path_key(relative_path)
        if normalized_path in seen_normalized_paths:
            diagnostics.append(_source_error("duplicate_package_path", normalized_path))
            continue
        seen_normalized_paths.add(normalized_path)
        validated = _validate_package_path(relative_path, diagnostics)
        if validated is None:
            continue
        if _is_ignored_path(validated):
            diagnostics.append(
                _source_error("hidden_system_authority_entry", validated)
            )
            continue
        if validated in package_files:
            diagnostics.append(_source_error("duplicate_package_path", validated))
            continue
        package_files[validated] = Path(
            str(distribution.locate_file(distribution_file))
        )
    member_bytes: dict[str, bytes] = {}
    manifest_file = package_files.get(_MANIFEST_PATH)
    if manifest_file is not None:
        payload = _read_installed_file_bytes(
            manifest_file,
            _MANIFEST_PATH,
            diagnostics,
            distribution_root=distribution_root,
            resource_root=resource_root,
        )
        if payload is None:
            package_files.pop(_MANIFEST_PATH, None)
        else:
            member_bytes[_MANIFEST_PATH] = payload
    if _MANIFEST_PATH in member_bytes:
        manifest_source = _manifest_source_from_bytes(
            member_bytes[_MANIFEST_PATH],
            diagnostics,
        )
        declared_paths = set(_declared_asset_paths(manifest_source))
        for package_path in sorted(
            set(package_files) - declared_paths - {_MANIFEST_PATH}
        ):
            diagnostics.append(_source_error("undeclared_package_member", package_path))
        for package_path in sorted(declared_paths):
            file_path = package_files.get(package_path)
            if file_path is None:
                continue
            payload = _read_installed_file_bytes(
                file_path,
                package_path,
                diagnostics,
                distribution_root=distribution_root,
                resource_root=resource_root,
            )
            if payload is not None:
                member_bytes[package_path] = payload
    return _installed_read_result(
        distribution_name=installed_distribution_name,
        resource_root=resource_root,
        member_bytes=member_bytes,
        diagnostics=diagnostics,
    )


def _read_archive_members(
    archive_bytes: bytes,
    diagnostics: list[Diagnostic],
) -> Mapping[str, bytes]:
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            for member in archive.getmembers():
                normalized_name = normalized_package_path_key(member.name)
                if normalized_name != member.name and normalized_name in members:
                    diagnostics.append(
                        _source_error("duplicate_package_path", normalized_name)
                    )
                    continue
                package_path = _validate_member(member, diagnostics)
                if package_path is None:
                    continue
                if package_path in members:
                    diagnostics.append(
                        _source_error("duplicate_package_path", package_path)
                    )
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    diagnostics.append(
                        _source_error("missing_package_member", package_path)
                    )
                    continue
                members[package_path] = extracted.read()
    except tarfile.TarError:
        diagnostics.append(
            _source_error("uncompressed_posix_tar_required", "archive")
        )
    return members


def _installed_read_result(
    *,
    distribution_name: str,
    resource_root: str,
    member_bytes: Mapping[str, bytes],
    diagnostics: list[Diagnostic],
) -> WorkflowPackageSourceRead:
    manifest_bytes = member_bytes.get(_MANIFEST_PATH, b"")
    if _MANIFEST_PATH not in member_bytes:
        diagnostics.append(_source_error("missing_manifest", _MANIFEST_PATH))
    manifest_source = _manifest_source_from_bytes(manifest_bytes, diagnostics)
    validation = validate_importable_workflow_package_manifest(manifest_source)
    diagnostics.extend(validation.diagnostics)
    asset_bytes_by_path = {
        package_path: payload
        for package_path, payload in sorted(member_bytes.items())
        if package_path != _MANIFEST_PATH
    }
    _validate_declared_assets(manifest_source, asset_bytes_by_path, diagnostics)
    member_paths = tuple(sorted(member_bytes))
    source_members = (
        (_MANIFEST_PATH, manifest_bytes),
        *tuple(asset_bytes_by_path.items()),
    )
    return WorkflowPackageSourceRead(
        source_kind="installed_python_package",
        source_uri=f"python-dist://{distribution_name}/{resource_root}",
        source_digest=_source_digest("installed_python_package", source_members),
        manifest_source=manifest_source,
        manifest=validation.manifest if not diagnostics else None,
        diagnostics=tuple(diagnostics),
        manifest_bytes=manifest_bytes,
        asset_bytes_by_path=asset_bytes_by_path,
        member_paths=member_paths,
    )


def _installed_member_path(
    distribution_path: str,
    resource_root: str,
) -> str | None:
    if distribution_path == resource_root:
        return None
    prefix = f"{resource_root}/"
    if not distribution_path.startswith(prefix):
        return None
    return distribution_path.removeprefix(prefix)


def _read_installed_file_bytes(
    file_path: Path,
    package_path: str,
    diagnostics: list[Diagnostic],
    *,
    distribution_root: Path,
    resource_root: str,
) -> bytes | None:
    try:
        root_resolved = distribution_root.resolve(strict=True)
        file_resolved = file_path.resolve(strict=True)
    except OSError:
        diagnostics.append(_source_error("missing_distribution_file", package_path))
        return None
    if not _path_is_relative_to(file_resolved, root_resolved) or (
        _has_symlink_parent(file_path, distribution_root / resource_root)
    ):
        diagnostics.append(_source_error("unsafe_package_path", package_path))
        return None
    try:
        file_stat = file_path.lstat()
    except OSError:
        diagnostics.append(_source_error("missing_distribution_file", package_path))
        return None
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        diagnostics.append(_source_error("non_regular_package_file", package_path))
        return None
    if file_stat.st_nlink > 1:
        diagnostics.append(_source_error("hardlink_package_file", package_path))
        return None
    if _has_no_read_bits(file_stat):
        diagnostics.append(_source_error("unreadable_package_file", package_path))
        return None
    try:
        return file_path.read_bytes()
    except OSError:
        diagnostics.append(_source_error("unreadable_package_file", package_path))
        return None


def _has_symlink_parent(file_path: Path, package_root: Path) -> bool:
    try:
        if package_root.is_symlink():
            return True
    except OSError:
        return True
    parent = file_path.parent
    while True:
        if parent == package_root or parent == parent.parent:
            return False
        try:
            if parent.is_symlink():
                return True
        except OSError:
            return True
        parent = parent.parent


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_member(
    member: tarfile.TarInfo,
    diagnostics: list[Diagnostic],
) -> str | None:
    package_path = _validate_package_path(member.name, diagnostics)
    if package_path is None:
        return None
    if not is_regular_package_tar_member(member.type):
        diagnostics.append(_source_error("non_regular_package_member", package_path))
        return None
    if member.pax_headers or (
        member.uid != 0
        or member.gid != 0
        or member.uname != ""
        or member.gname != ""
        or member.mtime != 0
        or member.mode != 0o644
    ):
        diagnostics.append(_source_error("noncanonical_tar_metadata", package_path))
        return None
    if _is_ignored_path(package_path):
        diagnostics.append(_source_error("hidden_system_authority_entry", package_path))
        return None
    return package_path


def _read_path_bytes(
    root: Path,
    package_path: str,
    diagnostics: list[Diagnostic],
) -> bytes:
    validated = _validate_package_path(package_path, diagnostics)
    if validated is None:
        return b""
    if _is_ignored_path(validated):
        diagnostics.append(_source_error("hidden_system_authority_entry", validated))
        return b""
    file_path = root.joinpath(*PurePosixPath(validated).parts)
    try:
        file_stat = file_path.lstat()
    except OSError:
        diagnostics.append(_source_error("missing_declared_asset", validated))
        return b""
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        diagnostics.append(_source_error("non_regular_package_file", validated))
        return b""
    if file_stat.st_nlink > 1:
        diagnostics.append(_source_error("hardlink_package_file", validated))
        return b""
    if _has_no_read_bits(file_stat):
        diagnostics.append(_source_error("unreadable_package_file", validated))
        return b""
    try:
        return file_path.read_bytes()
    except OSError:
        diagnostics.append(_source_error("unreadable_package_file", validated))
        return b""


def _scan_path_root(root: Path, diagnostics: list[Diagnostic]) -> None:
    for path in sorted(root.rglob("*")):
        package_path = path.relative_to(root).as_posix()
        if _is_ignored_path(package_path):
            continue
        validated = _validate_package_path(package_path, diagnostics)
        if validated is None:
            continue
        try:
            file_stat = path.lstat()
        except OSError:
            diagnostics.append(_source_error("unreadable_package_file", validated))
            continue
        if stat.S_ISDIR(file_stat.st_mode):
            continue
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            diagnostics.append(_source_error("non_regular_package_file", validated))
            continue
        if file_stat.st_nlink > 1:
            diagnostics.append(_source_error("hardlink_package_file", validated))
            continue
        if _has_no_read_bits(file_stat):
            diagnostics.append(_source_error("unreadable_package_file", validated))


def _has_no_read_bits(file_stat: os.stat_result) -> bool:
    return file_stat.st_mode & 0o444 == 0


def _read_declared_path_assets(
    root: Path,
    manifest_source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> Mapping[str, bytes]:
    asset_bytes: dict[str, bytes] = {}
    seen_paths: set[str] = set()
    for package_path in _declared_asset_paths(manifest_source):
        normalized_path = normalized_package_path_key(package_path)
        if normalized_path in seen_paths:
            diagnostics.append(_source_error("duplicate_package_path", normalized_path))
            continue
        validated = _validate_package_path(package_path, diagnostics)
        if validated is None:
            continue
        if _is_ignored_path(validated):
            diagnostics.append(
                _source_error("hidden_system_authority_entry", validated)
            )
            continue
        if validated in seen_paths:
            diagnostics.append(_source_error("duplicate_package_path", validated))
            continue
        seen_paths.add(validated)
        asset_bytes[validated] = _read_path_bytes(root, validated, diagnostics)
    return asset_bytes


def _manifest_source_from_bytes(
    manifest_bytes: bytes,
    diagnostics: list[Diagnostic],
) -> Mapping[str, object]:
    if not manifest_bytes:
        return {}
    try:
        parsed = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        diagnostics.append(_source_error("invalid_manifest_json", _MANIFEST_PATH))
        return {}
    if not isinstance(parsed, dict):
        diagnostics.append(_source_error("invalid_manifest_json", _MANIFEST_PATH))
        return {}
    return cast(Mapping[str, object], parsed)


def _declared_asset_paths(manifest_source: Mapping[str, object]) -> tuple[str, ...]:
    raw_assets = manifest_source.get("assets")
    if not isinstance(raw_assets, list):
        return ()
    paths: list[str] = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            continue
        raw_path = raw_asset.get("package_path")
        if isinstance(raw_path, str):
            paths.append(raw_path)
    return tuple(paths)


def _validate_declared_assets(
    manifest_source: Mapping[str, object],
    asset_bytes_by_path: Mapping[str, bytes],
    diagnostics: list[Diagnostic],
) -> None:
    declared_paths = _declared_asset_paths(manifest_source)
    normalized_declared: set[str] = set()
    for package_path in declared_paths:
        normalized_path = normalized_package_path_key(package_path)
        if normalized_path in normalized_declared:
            diagnostics.append(_source_error("duplicate_package_path", normalized_path))
            continue
        validated = _validate_package_path(package_path, diagnostics)
        if validated is None:
            continue
        if validated in normalized_declared:
            diagnostics.append(_source_error("duplicate_package_path", validated))
            continue
        normalized_declared.add(validated)
    for package_path in sorted(set(asset_bytes_by_path) - normalized_declared):
        diagnostics.append(_source_error("undeclared_package_member", package_path))
    for package_path in sorted(normalized_declared - set(asset_bytes_by_path)):
        diagnostics.append(_source_error("missing_declared_asset", package_path))

    raw_assets = manifest_source.get("assets")
    if not isinstance(raw_assets, list):
        return
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            continue
        raw_package_path = raw_asset.get("package_path")
        if not isinstance(raw_package_path, str):
            continue
        validated = _validate_package_path(raw_package_path, diagnostics)
        if validated is None or validated not in asset_bytes_by_path:
            continue
        payload = asset_bytes_by_path[validated]
        if raw_asset.get("content_digest") != asset_digest_for_bytes(payload):
            diagnostics.append(_source_error("asset_digest_mismatch", validated))
        if raw_asset.get("byte_length") != len(payload):
            diagnostics.append(_source_error("asset_byte_length_mismatch", validated))


def _validate_package_path(
    package_path: str,
    diagnostics: list[Diagnostic],
) -> str | None:
    try:
        return validate_package_path(package_path)
    except WorkflowPackagePathPolicyError as exc:
        diagnostics.append(_source_error(exc.code, exc.package_path))
        return None


def _source_digest(source_kind: str, members: tuple[tuple[str, bytes], ...]) -> str:
    payload = json.dumps(
        {
            "source_kind": source_kind,
            "members": [
                {"path": path, "storage_digest": _bytes_digest(payload)}
                for path, payload in sorted(members)
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _bytes_digest(payload)


def _bytes_digest(payload: bytes) -> str:
    return f"sha256:{sha256(_SOURCE_DIGEST_DOMAIN + payload).hexdigest()}"


def _is_ignored_path(package_path: str) -> bool:
    return is_ignored_package_path(package_path)


def _source_error(code: str, path: str) -> Diagnostic:
    readable = _READABLE_DIAGNOSTICS.get(code, code.replace("_", " "))
    return compiler_error(
        code=code,
        declaration_path=path,
        message=f"Workflow package source error: {readable}.",
        context={"path": path},
        hint="Use a deterministic data-only workflow package source.",
    )


__all__ = (
    "WorkflowPackageSourceRead",
    "read_archive_workflow_package_source",
    "read_installed_workflow_package_source",
    "read_path_workflow_package_source",
)
