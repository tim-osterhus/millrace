"""Shared data-only workflow package path safety policy."""

from __future__ import annotations

import tarfile
import unicodedata
from pathlib import PurePosixPath


class WorkflowPackagePathPolicyError(ValueError):
    """Raised when package path/member data violates shared safety policy."""

    def __init__(self, code: str, package_path: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.package_path = package_path


IGNORED_PACKAGE_PATH_NAMES = frozenset(
    {
        ".DS_Store",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
)
IGNORED_PACKAGE_PATH_SUFFIXES = (".pyc", ".pyo", ".swp", ".tmp")
REGULAR_PACKAGE_TAR_MEMBER_TYPES = (tarfile.REGTYPE, tarfile.AREGTYPE)


def normalized_package_path_key(package_path: str) -> str:
    return unicodedata.normalize("NFC", package_path)


def validate_package_path(package_path: str) -> str:
    if package_path == "" or package_path != normalized_package_path_key(package_path):
        raise WorkflowPackagePathPolicyError(
            "non_nfc_package_path",
            package_path,
            f"non-NFC package path: {package_path}",
        )
    if "\\" in package_path:
        raise WorkflowPackagePathPolicyError(
            "unsafe_package_path",
            package_path,
            f"unsafe package path: {package_path}",
        )
    raw_parts = package_path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise WorkflowPackagePathPolicyError(
            "unsafe_package_path",
            package_path,
            f"unsafe package path: {package_path}",
        )
    if any(part.startswith(".") for part in raw_parts):
        raise WorkflowPackagePathPolicyError(
            "hidden_system_authority_entry",
            package_path,
            f"hidden system authority entry: {package_path}",
        )
    path = PurePosixPath(package_path)
    if path.is_absolute() or str(path) in {"", "."}:
        raise WorkflowPackagePathPolicyError(
            "unsafe_package_path",
            package_path,
            f"unsafe package path: {package_path}",
        )
    if any(part in {"", ".", ".."} for part in path.parts):
        raise WorkflowPackagePathPolicyError(
            "unsafe_package_path",
            package_path,
            f"unsafe package path: {package_path}",
        )
    return path.as_posix()


def is_ignored_package_path(package_path: str) -> bool:
    path = PurePosixPath(package_path)
    return any(part in IGNORED_PACKAGE_PATH_NAMES for part in path.parts) or (
        path.name.endswith(IGNORED_PACKAGE_PATH_SUFFIXES)
    )


def is_regular_package_tar_member(member_type: bytes) -> bool:
    return member_type in REGULAR_PACKAGE_TAR_MEMBER_TYPES


__all__ = (
    "IGNORED_PACKAGE_PATH_NAMES",
    "IGNORED_PACKAGE_PATH_SUFFIXES",
    "REGULAR_PACKAGE_TAR_MEMBER_TYPES",
    "WorkflowPackagePathPolicyError",
    "is_ignored_package_path",
    "is_regular_package_tar_member",
    "normalized_package_path_key",
    "validate_package_path",
)
