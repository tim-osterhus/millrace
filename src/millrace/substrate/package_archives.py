"""Deterministic workflow package tar archive helpers."""

from __future__ import annotations

import io
import json
import os
import stat
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from millrace.contracts.workflow_package_paths import (
    WorkflowPackagePathPolicyError,
    is_ignored_package_path,
    is_regular_package_tar_member,
    validate_package_path,
)


class WorkflowPackageArchiveError(ValueError):
    """Raised when workflow package archive bytes are unsafe or noncanonical."""


@dataclass(frozen=True, slots=True)
class WorkflowPackageArchiveBytes:
    manifest_bytes: bytes
    asset_bytes_by_path: Mapping[str, bytes]
    member_paths: tuple[str, ...]


_MANIFEST_PATH = "manifest.json"


def export_workflow_package_directory(package_root: str | Path) -> bytes:
    root = Path(package_root)
    manifest_bytes = _read_declared_file(root, _MANIFEST_PATH)
    manifest = _parse_manifest(manifest_bytes)
    asset_paths = _declared_asset_paths(manifest)
    _validate_declared_paths((_MANIFEST_PATH, *asset_paths))
    _scan_for_forbidden_files(root)
    members = [(_MANIFEST_PATH, manifest_bytes)]
    for package_path in sorted(asset_paths):
        members.append((package_path, _read_declared_file(root, package_path)))
    return archive_bytes_for_members(tuple(members))


def archive_bytes_for_members(members: tuple[tuple[str, bytes], ...]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(
        fileobj=stream,
        mode="w",
        format=tarfile.USTAR_FORMAT,
    ) as archive:
        for package_path, payload in sorted(members, key=lambda item: item[0]):
            _validate_package_path(package_path)
            info = tarfile.TarInfo(package_path)
            info.size = len(payload)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def read_workflow_package_archive_bytes(
    archive_bytes: bytes,
) -> WorkflowPackageArchiveBytes:
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            for member in archive.getmembers():
                _validate_member(member)
                package_path = _validate_package_path(member.name)
                if package_path in members:
                    raise WorkflowPackageArchiveError(
                        f"duplicate package path: {package_path}"
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise WorkflowPackageArchiveError(
                        f"missing package member bytes: {package_path}"
                    )
                members[package_path] = extracted.read()
    except tarfile.TarError as exc:
        raise WorkflowPackageArchiveError(
            "workflow package archives must be uncompressed POSIX tar"
        ) from exc

    if _MANIFEST_PATH not in members:
        raise WorkflowPackageArchiveError("missing manifest.json")
    member_paths = tuple(sorted(members))
    asset_bytes = {
        package_path: payload
        for package_path, payload in sorted(members.items())
        if package_path != _MANIFEST_PATH
    }
    return WorkflowPackageArchiveBytes(
        manifest_bytes=members[_MANIFEST_PATH],
        asset_bytes_by_path=asset_bytes,
        member_paths=member_paths,
    )


def _parse_manifest(manifest_bytes: bytes) -> Mapping[str, object]:
    try:
        parsed = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowPackageArchiveError("manifest.json must be UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise WorkflowPackageArchiveError("manifest.json must be a JSON object")
    return cast(Mapping[str, object], parsed)


def _declared_asset_paths(manifest: Mapping[str, object]) -> tuple[str, ...]:
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list):
        raise WorkflowPackageArchiveError("manifest assets must be a list")
    paths: list[str] = []
    for index, raw_asset in enumerate(raw_assets):
        if not isinstance(raw_asset, dict):
            raise WorkflowPackageArchiveError(f"assets[{index}] must be an object")
        raw_path = raw_asset.get("package_path")
        if not isinstance(raw_path, str):
            raise WorkflowPackageArchiveError(
                f"assets[{index}].package_path must be text"
            )
        paths.append(raw_path)
    return tuple(paths)


def _validate_declared_paths(package_paths: tuple[str, ...]) -> None:
    normalized: set[str] = set()
    for package_path in package_paths:
        validated = _validate_package_path(package_path)
        if _is_ignored_path(validated):
            raise WorkflowPackageArchiveError(
                f"hidden system authority entry: {validated}"
            )
        if validated in normalized:
            raise WorkflowPackageArchiveError(f"duplicate package path: {validated}")
        normalized.add(validated)


def _validate_package_path(package_path: str) -> str:
    try:
        return validate_package_path(package_path)
    except WorkflowPackagePathPolicyError as exc:
        raise WorkflowPackageArchiveError(str(exc)) from exc


def _validate_member(member: tarfile.TarInfo) -> None:
    if not is_regular_package_tar_member(member.type):
        raise WorkflowPackageArchiveError(
            f"non-regular package member: {member.name}"
        )
    if member.pax_headers:
        raise WorkflowPackageArchiveError(
            f"noncanonical tar metadata: {member.name}"
        )
    if (
        member.uid != 0
        or member.gid != 0
        or member.uname != ""
        or member.gname != ""
        or member.mtime != 0
        or member.mode != 0o644
    ):
        raise WorkflowPackageArchiveError(
            f"noncanonical tar metadata: {member.name}"
        )
    package_path = _validate_package_path(member.name)
    if _is_ignored_path(package_path):
        raise WorkflowPackageArchiveError(
            f"hidden system authority entry: {package_path}"
        )


def _scan_for_forbidden_files(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        relative_path = path.relative_to(root).as_posix()
        if _is_ignored_path(relative_path):
            continue
        validated = _validate_package_path(relative_path)
        file_stat = path.lstat()
        if stat.S_ISDIR(file_stat.st_mode):
            continue
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise WorkflowPackageArchiveError(
                f"non-regular package file: {validated}"
            )
        if file_stat.st_nlink > 1:
            raise WorkflowPackageArchiveError(f"hardlink package file: {validated}")
        if _has_no_read_bits(file_stat):
            raise WorkflowPackageArchiveError(
                f"unreadable package file: {validated}"
            )


def _read_declared_file(root: Path, package_path: str) -> bytes:
    validated = _validate_package_path(package_path)
    file_path = root.joinpath(*PurePosixPath(validated).parts)
    if _is_ignored_path(validated):
        raise WorkflowPackageArchiveError(f"hidden system authority entry: {validated}")
    try:
        file_stat = file_path.lstat()
    except OSError as exc:
        raise WorkflowPackageArchiveError(
            f"missing declared package file: {validated}"
        ) from exc
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise WorkflowPackageArchiveError(f"non-regular package file: {validated}")
    if file_stat.st_nlink > 1:
        raise WorkflowPackageArchiveError(f"hardlink package file: {validated}")
    if _has_no_read_bits(file_stat):
        raise WorkflowPackageArchiveError(f"unreadable package file: {validated}")
    try:
        return file_path.read_bytes()
    except OSError as exc:
        raise WorkflowPackageArchiveError(
            f"unreadable package file: {validated}"
        ) from exc


def _is_ignored_path(package_path: str) -> bool:
    return is_ignored_package_path(package_path)


def _has_no_read_bits(file_stat: os.stat_result) -> bool:
    return file_stat.st_mode & 0o444 == 0


__all__ = (
    "WorkflowPackageArchiveBytes",
    "WorkflowPackageArchiveError",
    "archive_bytes_for_members",
    "export_workflow_package_directory",
    "read_workflow_package_archive_bytes",
)
