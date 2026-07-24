from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path
from typing import cast

from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)

ManifestSource = dict[str, object]
Record = dict[str, object]


def _fail_if_read(monkeypatch, forbidden_path: Path) -> None:
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == forbidden_path:
            raise AssertionError(f"read attempted for {forbidden_path}")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)


def _diagnostic_codes(source) -> set[str]:
    return {diagnostic.code for diagnostic in source.diagnostics}


def _manifest(
    *,
    asset_path: str = "evil_package/__init__.py",
    asset_bytes: bytes = b"raise RuntimeError('package code executed')\n",
) -> ManifestSource:
    digest = asset_digest_for_bytes(asset_bytes)
    manifest: ManifestSource = {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": {
            "package_id": "pkg.example.noexec",
            "package_version": "1.0.0",
            "package_format_version": "1",
            "package_role": "workflow_package",
            "publisher": "Example",
            "base_millrace_compatibility": ">=0.22,<0.23",
            "source_kind": "path",
        },
        "workflows": [
            {
                "workflow_id": "wf.noexec",
                "workflow_version": "1",
                "visibility": "test_only",
                "entrypoints": ["evil_package:entrypoint"],
                "source_refs": [asset_path],
                "selected_authority": {
                    "graphs": ["graph.noexec"],
                    "stage_kinds": ["stage.noexec"],
                    "terminal_outcomes": ["outcome.accepted"],
                    "terminal_actions": ["action.close"],
                },
                "required_assets": [
                    {"asset_id": "asset.entrypoint", "content_digest": digest}
                ],
            }
        ],
        "assets": [
            {
                "asset_id": "asset.entrypoint",
                "asset_kind": "stage_skill",
                "media_type": "text/x-python; charset=utf-8",
                "encoding": "utf-8",
                "content_digest": digest,
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


def _write_path_package(
    root: Path,
    *,
    manifest: ManifestSource | None = None,
    asset_bytes: bytes = b"raise RuntimeError('package code executed')\n",
) -> None:
    manifest = _manifest(asset_bytes=asset_bytes) if manifest is None else manifest
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    package_file = root / cast(str, asset["package_path"])
    package_file.parent.mkdir(parents=True, exist_ok=True)
    package_file.write_bytes(asset_bytes)
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _archive_bytes(root: Path) -> bytes:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    asset_path = cast(str, asset["package_path"])
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path in (root / "manifest.json", root / asset_path):
            payload = path.read_bytes()
            name = path.relative_to(root).as_posix()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
    return stream.getvalue()


def test_path_package_source_reader_reads_bytes_without_importing_package_modules(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_path_workflow_package_source,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_path_package(package_root)
    monkeypatch.syspath_prepend(str(package_root))
    sys.modules.pop("evil_package", None)

    source = read_path_workflow_package_source(package_root)

    assert "evil_package" not in sys.modules
    assert source.manifest is not None
    assert source.diagnostics == ()
    asset = cast(Record, cast(list[object], source.manifest_source["assets"])[0])
    assert source.asset_bytes_by_path[cast(str, asset["package_path"])].startswith(
        b"raise RuntimeError"
    )


def test_archive_package_source_reader_reads_bytes_without_importing_package_modules_or_entrypoints(  # noqa: E501
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_archive_workflow_package_source,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_path_package(package_root)
    monkeypatch.syspath_prepend(str(package_root))
    sys.modules.pop("evil_package", None)

    source = read_archive_workflow_package_source(_archive_bytes(package_root))

    assert "evil_package" not in sys.modules
    assert source.manifest is not None
    assert source.diagnostics == ()
    assert tuple(source.asset_bytes_by_path) == ("evil_package/__init__.py",)


def test_path_source_reader_refuses_dot_prefixed_authority_paths(
    tmp_path: Path,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_path_workflow_package_source,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_path_package(
        package_root,
        manifest=_manifest(asset_path=".git/config", asset_bytes=b"secret\n"),
        asset_bytes=b"secret\n",
    )

    source = read_path_workflow_package_source(package_root)

    assert "hidden_system_authority_entry" in {
        diagnostic.code for diagnostic in source.diagnostics
    }
    assert source.manifest is None


def test_archive_source_reader_refuses_dot_prefixed_authority_paths(
    tmp_path: Path,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_archive_workflow_package_source,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_path_package(
        package_root,
        manifest=_manifest(asset_path=".env", asset_bytes=b"secret\n"),
        asset_bytes=b"secret\n",
    )

    source = read_archive_workflow_package_source(_archive_bytes(package_root))

    assert "hidden_system_authority_entry" in {
        diagnostic.code for diagnostic in source.diagnostics
    }
    assert source.manifest is None


def test_path_source_reader_reports_unreadable_no_read_bit_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_path_workflow_package_source,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_path_package(package_root)
    manifest_path = package_root / "manifest.json"
    manifest_path.chmod(0)
    _fail_if_read(monkeypatch, manifest_path)
    try:
        source = read_path_workflow_package_source(package_root)
    finally:
        manifest_path.chmod(0o644)

    assert "unreadable_package_file" in _diagnostic_codes(source)
    assert source.manifest is None


def test_path_source_reader_reports_invalid_manifest_json(tmp_path: Path) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_path_workflow_package_source,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    (package_root / "manifest.json").write_text("{", encoding="utf-8")

    source = read_path_workflow_package_source(package_root)

    assert "invalid_manifest_json" in _diagnostic_codes(source)
    assert source.manifest is None


def test_path_source_reader_reports_asset_byte_length_mismatch(
    tmp_path: Path,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_path_workflow_package_source,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    asset_bytes = b"selected package asset\n"
    manifest = _manifest(asset_bytes=asset_bytes)
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    asset["byte_length"] = len(asset_bytes) + 1
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    _write_path_package(
        package_root,
        manifest=manifest,
        asset_bytes=asset_bytes,
    )

    source = read_path_workflow_package_source(package_root)

    assert _diagnostic_codes(source) == {"asset_byte_length_mismatch"}
    assert source.manifest is None


def test_path_source_reader_reports_unreadable_no_read_bit_declared_asset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_path_workflow_package_source,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_path_package(package_root)
    asset_path = package_root / "evil_package" / "__init__.py"
    asset_path.chmod(0)
    _fail_if_read(monkeypatch, asset_path)
    try:
        source = read_path_workflow_package_source(package_root)
    finally:
        asset_path.chmod(0o644)

    assert "unreadable_package_file" in _diagnostic_codes(source)
    assert source.manifest is None


def test_path_source_root_scan_reports_unreadable_no_read_bit_extra_member(
    tmp_path: Path,
) -> None:
    from millrace.compiler.workflow_package_sources import (
        read_path_workflow_package_source,
    )

    package_root = tmp_path / "pkg"
    package_root.mkdir()
    _write_path_package(package_root)
    extra_path = package_root / "extra.txt"
    extra_path.write_text("extra\n", encoding="utf-8")
    extra_path.chmod(0)
    try:
        source = read_path_workflow_package_source(package_root)
    finally:
        extra_path.chmod(0o644)

    assert "unreadable_package_file" in _diagnostic_codes(source)
    assert source.manifest is None
