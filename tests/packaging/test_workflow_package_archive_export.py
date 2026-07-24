from __future__ import annotations

import io
import json
import os
import tarfile
import unicodedata
from pathlib import Path
from typing import cast

import pytest

from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)

ManifestSource = dict[str, object]
Record = dict[str, object]


def _fail_if_read(monkeypatch: pytest.MonkeyPatch, forbidden_path: Path) -> None:
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == forbidden_path:
            raise AssertionError(f"read attempted for {forbidden_path}")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)


def _manifest(asset_bytes: bytes = b"echo prompt\n") -> ManifestSource:
    digest = asset_digest_for_bytes(asset_bytes)
    manifest: ManifestSource = {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": {
            "package_id": "pkg.example.archive",
            "package_version": "1.0.0",
            "package_format_version": "1",
            "package_role": "workflow_package",
            "publisher": "Example",
            "base_millrace_compatibility": ">=0.22,<0.23",
        },
        "workflows": [
            {
                "workflow_id": "wf.echo_check",
                "workflow_version": "1",
                "visibility": "test_only",
                "entrypoints": ["default"],
                "selected_authority": {
                    "graphs": ["graph.echo"],
                    "stage_kinds": ["stage.receive", "stage.respond"],
                    "terminal_outcomes": ["outcome.accepted"],
                    "terminal_actions": ["action.close"],
                },
                "required_assets": [
                    {"asset_id": "asset.echo_prompt", "content_digest": digest}
                ],
            }
        ],
        "assets": [
            {
                "asset_id": "asset.echo_prompt",
                "asset_kind": "entrypoint_prompt",
                "media_type": "text/markdown; charset=utf-8",
                "encoding": "utf-8",
                "content_digest": digest,
                "byte_length": len(asset_bytes),
                "package_path": "prompts/echo.md",
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


def _write_package(root: Path, manifest: ManifestSource | None = None) -> None:
    manifest = _manifest() if manifest is None else manifest
    assets = cast(list[object], manifest["assets"])
    asset = cast(Record, assets[0])
    package_path = cast(str, asset["package_path"])
    asset_path = root / package_path
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b"echo prompt\n")
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _member_names(archive_bytes: bytes) -> tuple[str, ...]:
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        return tuple(member.name for member in archive.getmembers())


def _single_member(archive_bytes: bytes, name: str) -> tarfile.TarInfo:
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        member = archive.getmember(name)
        return member


def test_package_directory_export_produces_deterministic_archive_bytes(
    tmp_path: Path,
) -> None:
    from millrace.substrate.package_archives import (
        export_workflow_package_directory,
    )

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_package(first)
    _write_package(second)

    first_bytes = export_workflow_package_directory(first)
    second_bytes = export_workflow_package_directory(second)

    assert first_bytes == second_bytes
    assert _member_names(first_bytes) == ("manifest.json", "prompts/echo.md")
    for member_name in _member_names(first_bytes):
        member = _single_member(first_bytes, member_name)
        assert member.isfile()
        assert member.uid == 0
        assert member.gid == 0
        assert member.uname == ""
        assert member.gname == ""
        assert member.mtime == 0
        assert member.mode == 0o644
        assert member.pax_headers == {}


def test_package_directory_export_excludes_generated_cache_artifacts(
    tmp_path: Path,
) -> None:
    from millrace.substrate.package_archives import (
        export_workflow_package_directory,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_package(package_root)
    (package_root / "__pycache__").mkdir()
    (package_root / "__pycache__" / "ignored.pyc").write_bytes(b"cache")
    (package_root / ".pytest_cache").mkdir()
    (package_root / ".pytest_cache" / "ignored").write_text(
        "cache",
        encoding="utf-8",
    )
    (package_root / ".DS_Store").write_bytes(b"metadata")

    archive_bytes = export_workflow_package_directory(package_root)

    assert _member_names(archive_bytes) == ("manifest.json", "prompts/echo.md")


@pytest.mark.parametrize(
    ("package_path", "match"),
    [
        ("/absolute.md", "unsafe package path"),
        ("../escape.md", "unsafe package path"),
        ("prompts/./echo.md", "unsafe package path"),
        (".env", "hidden system"),
        (".git/config", "hidden system"),
        (unicodedata.normalize("NFD", "prompts/écho.md"), "non-NFC"),
    ],
)
def test_package_directory_export_refuses_path_escape_and_non_nfc_members(
    tmp_path: Path,
    package_path: str,
    match: str,
) -> None:
    from millrace.substrate.package_archives import (
        WorkflowPackageArchiveError,
        export_workflow_package_directory,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    manifest = _manifest()
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    asset["package_path"] = package_path
    (package_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowPackageArchiveError, match=match):
        export_workflow_package_directory(package_root)


def test_package_directory_export_refuses_undeclared_dotfile_authority_entries(
    tmp_path: Path,
) -> None:
    from millrace.substrate.package_archives import (
        WorkflowPackageArchiveError,
        export_workflow_package_directory,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_package(package_root)
    (package_root / ".git").mkdir()
    (package_root / ".git" / "config").write_text("secret", encoding="utf-8")

    with pytest.raises(WorkflowPackageArchiveError, match="hidden system"):
        export_workflow_package_directory(package_root)


def test_package_directory_export_refuses_no_read_bit_manifest_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.substrate.package_archives import (
        WorkflowPackageArchiveError,
        export_workflow_package_directory,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_package(package_root)
    manifest_path = package_root / "manifest.json"
    manifest_path.chmod(0)
    _fail_if_read(monkeypatch, manifest_path)
    try:
        with pytest.raises(WorkflowPackageArchiveError, match="unreadable"):
            export_workflow_package_directory(package_root)
    finally:
        manifest_path.chmod(0o644)


def test_package_directory_export_refuses_no_read_bit_asset_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.substrate.package_archives import (
        WorkflowPackageArchiveError,
        export_workflow_package_directory,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_package(package_root)
    asset_path = package_root / "prompts" / "echo.md"
    asset_path.chmod(0)
    _fail_if_read(monkeypatch, asset_path)
    try:
        with pytest.raises(WorkflowPackageArchiveError, match="unreadable"):
            export_workflow_package_directory(package_root)
    finally:
        asset_path.chmod(0o644)


def test_package_directory_export_refuses_symlink_hardlink_device_and_fifo(
    tmp_path: Path,
) -> None:
    from millrace.substrate.package_archives import (
        WorkflowPackageArchiveError,
        export_workflow_package_directory,
    )

    symlink_root = tmp_path / "symlink"
    symlink_root.mkdir()
    _write_package(symlink_root)
    (symlink_root / "prompts" / "echo.md").unlink()
    (symlink_root / "target.md").write_text("target", encoding="utf-8")
    (symlink_root / "prompts" / "echo.md").symlink_to("../target.md")
    with pytest.raises(WorkflowPackageArchiveError, match="non-regular package file"):
        export_workflow_package_directory(symlink_root)

    hardlink_root = tmp_path / "hardlink"
    hardlink_root.mkdir()
    _write_package(hardlink_root)
    os.link(
        hardlink_root / "prompts" / "echo.md",
        hardlink_root / "prompts" / "linked.md",
    )
    with pytest.raises(WorkflowPackageArchiveError, match="hardlink"):
        export_workflow_package_directory(hardlink_root)

    fifo_root = tmp_path / "fifo"
    fifo_root.mkdir()
    _write_package(fifo_root)
    (fifo_root / "prompts" / "echo.md").unlink()
    os.mkfifo(fifo_root / "prompts" / "echo.md")
    with pytest.raises(WorkflowPackageArchiveError, match="non-regular package file"):
        export_workflow_package_directory(fifo_root)


def test_package_archive_import_uses_canonical_member_order(tmp_path: Path) -> None:
    from millrace.substrate.package_archives import (
        read_workflow_package_archive_bytes,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_package(package_root)
    manifest_bytes = (package_root / "manifest.json").read_bytes()
    asset_bytes = (package_root / "prompts" / "echo.md").read_bytes()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, payload in (
            ("prompts/echo.md", asset_bytes),
            ("manifest.json", manifest_bytes),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))

    package_bytes = read_workflow_package_archive_bytes(stream.getvalue())

    assert package_bytes.member_paths == ("manifest.json", "prompts/echo.md")
    assert package_bytes.manifest_bytes == manifest_bytes
    assert package_bytes.asset_bytes_by_path == {"prompts/echo.md": asset_bytes}
