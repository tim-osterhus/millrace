from __future__ import annotations

import hashlib
from pathlib import Path

import millrace_ai
from millrace_ai.workspace.baseline import load_baseline_manifest
from millrace_ai.workspace.initialization import initialize_workspace


def test_initialized_workspace_writes_baseline_manifest(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")

    manifest_path = paths.state_dir / "baseline_manifest.json"
    manifest = load_baseline_manifest(paths)

    assert manifest_path.is_file()
    assert manifest.schema_version == "1.0"
    assert manifest.seed_package_version == millrace_ai.__version__
    assert manifest.manifest_id
    assert manifest.entries
    assert [entry.relative_path for entry in manifest.entries] == sorted(
        entry.relative_path for entry in manifest.entries
    )


def test_manifest_records_original_hashes_for_managed_assets(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    manifest = load_baseline_manifest(paths)

    entry = manifest.entry_for("entrypoints/execution/builder.md")
    expected_hash = hashlib.sha256(
        (paths.runtime_root / "entrypoints" / "execution" / "builder.md").read_bytes()
    ).hexdigest()

    assert entry.asset_family == "entrypoints"
    assert entry.original_sha256 == expected_hash
