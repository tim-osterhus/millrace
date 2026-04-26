from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

import millrace_ai
from millrace_ai.workspace.baseline import (
    UpgradeDisposition,
    apply_baseline_upgrade,
    load_baseline_manifest,
    preview_baseline_upgrade,
)
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


def test_upgrade_preview_distinguishes_three_way_dispositions(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    assets_root = _copy_assets(tmp_path)

    (assets_root / "entrypoints" / "execution" / "builder.md").write_text(
        "candidate builder update\n",
        encoding="utf-8",
    )
    (paths.runtime_root / "entrypoints" / "planning" / "planner.md").write_text(
        "local planner edit\n",
        encoding="utf-8",
    )
    shared_checker = "shared checker update\n"
    (assets_root / "entrypoints" / "execution" / "checker.md").write_text(shared_checker, encoding="utf-8")
    (paths.runtime_root / "entrypoints" / "execution" / "checker.md").write_text(
        shared_checker,
        encoding="utf-8",
    )
    (assets_root / "entrypoints" / "planning" / "auditor.md").write_text(
        "candidate auditor update\n",
        encoding="utf-8",
    )
    (paths.runtime_root / "entrypoints" / "planning" / "auditor.md").write_text(
        "local auditor edit\n",
        encoding="utf-8",
    )
    (paths.runtime_root / "entrypoints" / "planning" / "arbiter.md").unlink()

    preview = preview_baseline_upgrade(paths, candidate_assets_root=assets_root)

    assert preview.classifications_by_path["graphs/planning/standard.json"] is UpgradeDisposition.UNCHANGED
    assert (
        preview.classifications_by_path["entrypoints/execution/builder.md"]
        is UpgradeDisposition.SAFE_PACKAGE_UPDATE
    )
    assert (
        preview.classifications_by_path["entrypoints/planning/planner.md"]
        is UpgradeDisposition.LOCAL_ONLY_MODIFICATION
    )
    assert (
        preview.classifications_by_path["entrypoints/execution/checker.md"]
        is UpgradeDisposition.ALREADY_CONVERGED
    )
    assert (
        preview.classifications_by_path["entrypoints/planning/auditor.md"]
        is UpgradeDisposition.CONFLICT
    )
    assert (
        preview.classifications_by_path["entrypoints/planning/arbiter.md"]
        is UpgradeDisposition.MISSING
    )


def test_upgrade_apply_preserves_runtime_state_and_operator_docs(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    assets_root = _copy_assets(tmp_path)
    runtime_snapshot_before = paths.runtime_snapshot_file.read_text(encoding="utf-8")
    notes_path = paths.runtime_root / "notes.md"
    notes_path.write_text("keep operator notes\n", encoding="utf-8")

    source_builder_path = assets_root / "entrypoints" / "execution" / "builder.md"
    source_builder_path.write_text("candidate builder apply\n", encoding="utf-8")
    missing_path = paths.runtime_root / "entrypoints" / "execution" / "checker.md"
    missing_path.unlink()

    outcome = apply_baseline_upgrade(paths, candidate_assets_root=assets_root)
    manifest = load_baseline_manifest(paths)

    assert outcome.applied is True
    assert (paths.runtime_root / "entrypoints" / "execution" / "builder.md").read_text(
        encoding="utf-8"
    ) == "candidate builder apply\n"
    assert missing_path.is_file()
    assert paths.runtime_snapshot_file.read_text(encoding="utf-8") == runtime_snapshot_before
    assert notes_path.read_text(encoding="utf-8") == "keep operator notes\n"
    assert (
        manifest.entry_for("entrypoints/execution/builder.md").original_sha256
        == hashlib.sha256(source_builder_path.read_bytes()).hexdigest()
    )


def test_upgrade_apply_aborts_before_mutation_on_conflict(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    assets_root = _copy_assets(tmp_path)
    builder_path = paths.runtime_root / "entrypoints" / "execution" / "builder.md"
    builder_before = builder_path.read_text(encoding="utf-8")

    (assets_root / "entrypoints" / "execution" / "builder.md").write_text(
        "candidate builder update\n",
        encoding="utf-8",
    )
    (assets_root / "entrypoints" / "planning" / "auditor.md").write_text(
        "candidate auditor update\n",
        encoding="utf-8",
    )
    (paths.runtime_root / "entrypoints" / "planning" / "auditor.md").write_text(
        "local auditor edit\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="conflict"):
        apply_baseline_upgrade(paths, candidate_assets_root=assets_root)

    assert builder_path.read_text(encoding="utf-8") == builder_before
