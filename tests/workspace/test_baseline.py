from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import millrace_ai
from millrace_ai.workspace.baseline import load_baseline_manifest
from millrace_ai.workspace.initialization import initialize_workspace


def _copy_assets(tmp_path: Path) -> Path:
    source_assets = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    destination = tmp_path / "assets"
    shutil.copytree(source_assets, destination)
    return destination


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


def test_rerun_rebuilds_missing_manifest_from_seed_asset_hashes(tmp_path: Path) -> None:
    assets_root = _copy_assets(tmp_path)
    source_builder_path = assets_root / "entrypoints" / "execution" / "builder.md"
    source_builder_path.write_text("seeded builder from custom assets\n", encoding="utf-8")
    seeded_hash = hashlib.sha256(source_builder_path.read_bytes()).hexdigest()

    paths = initialize_workspace(tmp_path / "workspace", assets_root=assets_root)
    builder_path = paths.runtime_root / "entrypoints" / "execution" / "builder.md"

    paths.baseline_manifest_file.unlink()
    builder_path.write_text("locally edited builder\n", encoding="utf-8")

    initialize_workspace(paths, assets_root=assets_root)
    manifest = load_baseline_manifest(paths)
    manifest_entry = manifest.entry_for("entrypoints/execution/builder.md")
    edited_hash = hashlib.sha256(builder_path.read_bytes()).hexdigest()

    assert manifest_entry.original_sha256 == seeded_hash
    assert manifest_entry.original_sha256 != edited_hash
