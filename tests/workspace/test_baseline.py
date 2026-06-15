from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

import millrace_ai
from millrace_ai.workspace.baseline import (
    SHARED_INSTRUCTION_RELATIVE_PATH,
    UpgradeDisposition,
    WorkspaceFileOwnership,
    apply_baseline_upgrade,
    build_baseline_manifest,
    classify_workspace_relative_path,
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
    assert [entry.relative_path for entry in manifest.entries] == sorted(entry.relative_path for entry in manifest.entries)


def test_manifest_records_original_hashes_for_managed_assets(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    manifest = load_baseline_manifest(paths)

    entry = manifest.entry_for("entrypoints/execution/lad_builder.md")
    expected_hash = hashlib.sha256((paths.runtime_root / "entrypoints" / "execution" / "lad_builder.md").read_bytes()).hexdigest()

    assert entry.asset_family == "entrypoints"
    assert entry.original_sha256 == expected_hash


def test_manifest_records_shared_instruction_as_operator_owned_seed(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    manifest = load_baseline_manifest(paths)

    entry = manifest.entry_for(SHARED_INSTRUCTION_RELATIVE_PATH)

    assert entry.asset_family == "shared_instruction"
    assert paths.shared_instruction_file.is_file()


@pytest.mark.parametrize(
    ("relative_path", "expected_ownership"),
    [
        ("MILLRACE.md", WorkspaceFileOwnership.OPERATOR_OWNED_SEED),
        ("templates/MILLRACE.md.candidate", WorkspaceFileOwnership.PACKAGED_CANDIDATE),
        ("workspace-map/index.md", WorkspaceFileOwnership.OPERATOR_OWNED_SEED),
        ("workspace-map/manifest.json", WorkspaceFileOwnership.GENERATED_RUNTIME_OWNED),
        ("workspace-map/generated/file-tree.md", WorkspaceFileOwnership.GENERATED_RUNTIME_OWNED),
        ("workspace-map/wiki/index.md", WorkspaceFileOwnership.CURATED_LOCAL),
        ("history-log/latest.md", WorkspaceFileOwnership.RUNTIME_HISTORY),
        ("outline.md", WorkspaceFileOwnership.DEPRECATED_COMPATIBILITY),
        ("historylog.md", WorkspaceFileOwnership.DEPRECATED_COMPATIBILITY),
        ("entrypoints/execution/lad_builder.md", WorkspaceFileOwnership.PACKAGED_MANAGED),
    ],
)
def test_workspace_ownership_classification_covers_workspace_surfaces(
    relative_path: str,
    expected_ownership: WorkspaceFileOwnership,
) -> None:
    assert classify_workspace_relative_path(relative_path) is expected_ownership


def test_manifest_ignores_runtime_cache_artifacts(tmp_path: Path) -> None:
    assets_root = _copy_assets(tmp_path)
    cache_dir = assets_root / "skills" / "stage" / "execution" / "builder-core" / "__pycache__"
    cache_dir.mkdir(parents=True)
    cache_dir.joinpath("helper.cpython-311.pyc").write_bytes(b"cache")
    assets_root.joinpath("modes", ".DS_Store").write_text("metadata\n", encoding="utf-8")

    manifest = build_baseline_manifest(tmp_path / "workspace", assets_root=assets_root)

    relative_paths = {entry.relative_path for entry in manifest.entries}
    assert not any("__pycache__" in path for path in relative_paths)
    assert not any(path.endswith(".pyc") for path in relative_paths)
    assert "modes/.DS_Store" not in relative_paths


def test_rerun_rebuilds_missing_manifest_from_seed_asset_hashes(tmp_path: Path) -> None:
    assets_root = _copy_assets(tmp_path)
    source_builder_path = assets_root / "entrypoints" / "execution" / "lad_builder.md"
    source_builder_path.write_text("seeded builder from custom assets\n", encoding="utf-8")
    seeded_hash = hashlib.sha256(source_builder_path.read_bytes()).hexdigest()

    paths = initialize_workspace(tmp_path / "workspace", assets_root=assets_root)
    builder_path = paths.runtime_root / "entrypoints" / "execution" / "lad_builder.md"

    paths.baseline_manifest_file.unlink()
    builder_path.write_text("locally edited builder\n", encoding="utf-8")

    initialize_workspace(paths, assets_root=assets_root)
    manifest = load_baseline_manifest(paths)
    manifest_entry = manifest.entry_for("entrypoints/execution/lad_builder.md")
    edited_hash = hashlib.sha256(builder_path.read_bytes()).hexdigest()

    assert manifest_entry.original_sha256 == seeded_hash
    assert manifest_entry.original_sha256 != edited_hash


def test_upgrade_preview_distinguishes_three_way_dispositions(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    assets_root = _copy_assets(tmp_path)

    (assets_root / "entrypoints" / "execution" / "lad_builder.md").write_text(
        "candidate builder update\n",
        encoding="utf-8",
    )
    (paths.runtime_root / "entrypoints" / "planning" / "lad_planner.md").write_text(
        "local planner edit\n",
        encoding="utf-8",
    )
    shared_checker = "shared checker update\n"
    (assets_root / "entrypoints" / "execution" / "lad_checker.md").write_text(shared_checker, encoding="utf-8")
    (paths.runtime_root / "entrypoints" / "execution" / "lad_checker.md").write_text(
        shared_checker,
        encoding="utf-8",
    )
    (assets_root / "entrypoints" / "planning" / "lad_auditor.md").write_text(
        "candidate auditor update\n",
        encoding="utf-8",
    )
    (paths.runtime_root / "entrypoints" / "planning" / "lad_auditor.md").write_text(
        "local auditor edit\n",
        encoding="utf-8",
    )
    (paths.runtime_root / "entrypoints" / "planning" / "lad_arbiter.md").unlink()

    preview = preview_baseline_upgrade(paths, candidate_assets_root=assets_root)

    assert preview.classifications_by_path["graphs/planning/lad.json"] is UpgradeDisposition.UNCHANGED
    assert preview.classifications_by_path["entrypoints/execution/lad_builder.md"] is UpgradeDisposition.SAFE_PACKAGE_UPDATE
    assert preview.classifications_by_path["entrypoints/planning/lad_planner.md"] is UpgradeDisposition.LOCAL_ONLY_MODIFICATION
    assert preview.classifications_by_path["entrypoints/execution/lad_checker.md"] is UpgradeDisposition.ALREADY_CONVERGED
    assert preview.classifications_by_path["entrypoints/planning/lad_auditor.md"] is UpgradeDisposition.CONFLICT
    assert preview.classifications_by_path["entrypoints/planning/lad_arbiter.md"] is UpgradeDisposition.MISSING


def test_upgrade_apply_preserves_runtime_state_and_operator_docs(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    assets_root = _copy_assets(tmp_path)
    runtime_snapshot_before = paths.runtime_snapshot_file.read_text(encoding="utf-8")
    notes_path = paths.runtime_root / "notes.md"
    notes_path.write_text("keep operator notes\n", encoding="utf-8")
    generated_index_before = "# local generated map index\n"
    generated_manifest_before = '{"status": "local"}\n'
    generated_file_tree_before = "# local file tree\n"
    curated_wiki_before = "# local wiki\n"
    history_latest_before = "# local history latest\n"
    outline_before = "# local outline\n"
    historylog_before = "# local historylog\n"
    paths.workspace_map_index_file.write_text(generated_index_before, encoding="utf-8")
    paths.workspace_map_manifest_file.write_text(generated_manifest_before, encoding="utf-8")
    paths.workspace_map_generated_file_tree_file.write_text(generated_file_tree_before, encoding="utf-8")
    paths.workspace_map_wiki_index_file.write_text(curated_wiki_before, encoding="utf-8")
    paths.history_log_latest_file.write_text(history_latest_before, encoding="utf-8")
    paths.outline_file.write_text(outline_before, encoding="utf-8")
    paths.historylog_file.write_text(historylog_before, encoding="utf-8")

    source_builder_path = assets_root / "entrypoints" / "execution" / "lad_builder.md"
    source_builder_path.write_text("candidate builder apply\n", encoding="utf-8")
    missing_path = paths.runtime_root / "entrypoints" / "execution" / "lad_checker.md"
    missing_path.unlink()

    outcome = apply_baseline_upgrade(paths, candidate_assets_root=assets_root)
    manifest = load_baseline_manifest(paths)

    assert outcome.applied is True
    assert (paths.runtime_root / "entrypoints" / "execution" / "lad_builder.md").read_text(
        encoding="utf-8"
    ) == "candidate builder apply\n"
    assert missing_path.is_file()
    assert paths.runtime_snapshot_file.read_text(encoding="utf-8") == runtime_snapshot_before
    assert notes_path.read_text(encoding="utf-8") == "keep operator notes\n"
    assert paths.workspace_map_index_file.read_text(encoding="utf-8") == generated_index_before
    assert paths.workspace_map_manifest_file.read_text(encoding="utf-8") == generated_manifest_before
    assert paths.workspace_map_generated_file_tree_file.read_text(encoding="utf-8") == generated_file_tree_before
    assert paths.workspace_map_wiki_index_file.read_text(encoding="utf-8") == curated_wiki_before
    assert paths.history_log_latest_file.read_text(encoding="utf-8") == history_latest_before
    assert paths.outline_file.read_text(encoding="utf-8") == outline_before
    assert paths.historylog_file.read_text(encoding="utf-8") == historylog_before
    assert (
        manifest.entry_for("entrypoints/execution/lad_builder.md").original_sha256
        == hashlib.sha256(source_builder_path.read_bytes()).hexdigest()
    )


def test_upgrade_apply_never_overwrites_modified_shared_instructions(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    local_text = "# Local MILLRACE\n\nOperator rules.\n"
    paths.shared_instruction_file.write_text(local_text, encoding="utf-8")

    preview = preview_baseline_upgrade(paths)

    assert preview.classifications_by_path[SHARED_INSTRUCTION_RELATIVE_PATH] is UpgradeDisposition.LOCAL_ONLY_MODIFICATION

    outcome = apply_baseline_upgrade(paths)

    assert outcome.applied is True
    assert paths.shared_instruction_file.read_text(encoding="utf-8") == local_text
    assert not paths.shared_instruction_candidate_file.exists()


def test_upgrade_apply_creates_missing_curated_wiki_starter_pages(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    missing_pages = (
        paths.workspace_map_wiki_runtime_file,
        paths.workspace_map_wiki_compiler_file,
        paths.workspace_map_wiki_workspace_file,
        paths.workspace_map_wiki_runners_file,
        paths.workspace_map_wiki_assets_file,
        paths.workspace_map_wiki_cli_file,
        paths.workspace_map_wiki_contracts_file,
        paths.workspace_map_wiki_invariants_file,
        paths.workspace_map_wiki_glossary_file,
        paths.workspace_map_wiki_maintenance_notes_file,
    )
    for page in missing_pages:
        page.unlink()

    preview = preview_baseline_upgrade(paths)

    for page in missing_pages:
        relative_path = page.relative_to(paths.runtime_root).as_posix()
        assert preview.classifications_by_path[relative_path] is UpgradeDisposition.MISSING

    outcome = apply_baseline_upgrade(paths)

    assert outcome.applied is True
    for page in missing_pages:
        assert page.is_file()
        assert page.read_text(encoding="utf-8").startswith("# ")


def test_upgrade_apply_preserves_existing_curated_wiki_starter_page_content(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    local_text = "# Runtime\n\nOperator-maintained runtime notes.\n"
    paths.workspace_map_wiki_runtime_file.write_text(local_text, encoding="utf-8")

    preview = preview_baseline_upgrade(paths)

    assert (
        preview.classifications_by_path["workspace-map/wiki/domains/runtime.md"]
        is UpgradeDisposition.LOCAL_ONLY_MODIFICATION
    )

    outcome = apply_baseline_upgrade(paths)

    assert outcome.applied is True
    assert paths.workspace_map_wiki_runtime_file.read_text(encoding="utf-8") == local_text


def test_upgrade_writes_candidate_for_packaged_shared_instruction_template_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    updated_template = "# MILLRACE.md\n\nUpdated packaged template.\n"

    monkeypatch.setattr(
        "millrace_ai.workspace.baseline.MILLRACE_SHARED_INSTRUCTIONS_TEMPLATE",
        updated_template,
    )

    preview = preview_baseline_upgrade(paths)

    assert (
        preview.classifications_by_path[SHARED_INSTRUCTION_RELATIVE_PATH]
        is UpgradeDisposition.SHARED_INSTRUCTION_TEMPLATE_UPDATE_AVAILABLE
    )

    outcome = apply_baseline_upgrade(paths)
    manifest = load_baseline_manifest(paths)

    assert outcome.applied is True
    assert paths.shared_instruction_file.read_text(encoding="utf-8") != updated_template
    assert paths.shared_instruction_candidate_file.read_text(encoding="utf-8") == updated_template
    assert (
        manifest.entry_for(SHARED_INSTRUCTION_RELATIVE_PATH).original_sha256
        == hashlib.sha256(updated_template.encode("utf-8")).hexdigest()
    )


def test_upgrade_can_localize_removed_managed_asset(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    assets_root = _copy_assets(tmp_path)
    removed_relative_path = "entrypoints/execution/lad_builder.md"
    localized_path = paths.runtime_root / removed_relative_path
    localized_path.write_text("local replacement kept outside package ownership\n", encoding="utf-8")
    (assets_root / removed_relative_path).unlink()

    preview = preview_baseline_upgrade(
        paths,
        candidate_assets_root=assets_root,
        localize_removed_paths=(removed_relative_path,),
    )

    assert preview.classifications_by_path[removed_relative_path] is UpgradeDisposition.LOCALIZED_REMOVED

    outcome = apply_baseline_upgrade(
        paths,
        candidate_assets_root=assets_root,
        localize_removed_paths=(removed_relative_path,),
    )
    manifest = load_baseline_manifest(paths)

    assert outcome.applied is True
    assert localized_path.read_text(encoding="utf-8") == "local replacement kept outside package ownership\n"
    assert removed_relative_path not in {entry.relative_path for entry in manifest.entries}


def test_upgrade_apply_aborts_before_mutation_on_conflict(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    assets_root = _copy_assets(tmp_path)
    builder_path = paths.runtime_root / "entrypoints" / "execution" / "lad_builder.md"
    builder_before = builder_path.read_text(encoding="utf-8")

    (assets_root / "entrypoints" / "execution" / "lad_builder.md").write_text(
        "candidate builder update\n",
        encoding="utf-8",
    )
    (assets_root / "entrypoints" / "planning" / "lad_auditor.md").write_text(
        "candidate auditor update\n",
        encoding="utf-8",
    )
    (paths.runtime_root / "entrypoints" / "planning" / "lad_auditor.md").write_text(
        "local auditor edit\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="conflict"):
        apply_baseline_upgrade(paths, candidate_assets_root=assets_root)

    assert builder_path.read_text(encoding="utf-8") == builder_before
