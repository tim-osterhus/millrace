from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import cast

from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)

ManifestSource = dict[str, object]
Record = dict[str, object]


def workflow_package_manifest(
    *,
    package_id: str = "pkg.example.operator",
    package_version: str = "1.0.0",
    workflow_id: str = "wf.operator",
    workflow_version: str = "1",
    asset_bytes: bytes = b"operator prompt\n",
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
                    "graphs": ["graph.operator"],
                    "stage_kinds": ["stage.operator"],
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
                "package_path": "prompts/operator.md",
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


def workflow_package_archive_bytes(
    *,
    manifest: ManifestSource | None = None,
    asset_bytes: bytes = b"operator prompt\n",
) -> bytes:
    manifest = (
        workflow_package_manifest(asset_bytes=asset_bytes)
        if manifest is None
        else manifest
    )
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    stream = io.BytesIO()
    with tarfile.open(
        fileobj=stream,
        mode="w",
        format=tarfile.USTAR_FORMAT,
    ) as archive:
        for name, payload in (
            (
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode(
                    "utf-8"
                ),
            ),
            (cast(str, asset["package_path"]), asset_bytes),
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
    return stream.getvalue()


def write_workflow_package_path(
    root: Path,
    *,
    manifest: ManifestSource | None = None,
    asset_bytes: bytes = b"operator prompt\n",
) -> None:
    manifest = (
        workflow_package_manifest(asset_bytes=asset_bytes)
        if manifest is None
        else manifest
    )
    asset = cast(Record, cast(list[object], manifest["assets"])[0])
    asset_path = root / cast(str, asset["package_path"])
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(asset_bytes)
    (root / "manifest.json").write_bytes(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
