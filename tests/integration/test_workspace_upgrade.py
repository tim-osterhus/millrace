from __future__ import annotations

import json
import shutil
from pathlib import Path

from millrace_ai.compiler import compile_and_persist_workspace_plan, inspect_workspace_plan_currentness
from millrace_ai.config import RuntimeConfig
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.state_store import load_snapshot
from millrace_ai.workspace.baseline import (
    UpgradeDisposition,
    apply_baseline_upgrade,
    load_baseline_manifest,
    preview_baseline_upgrade,
    write_baseline_manifest,
)
from millrace_ai.workspace.initialization import initialize_workspace


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _idle_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(f"stage runner should not be called for idle proof tick: {request.stage.value}")


def test_workspace_lifecycle_end_to_end(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    assert paths.baseline_manifest_file.is_file()

    compiled = compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
    )
    assert compiled.diagnostics.ok is True
    assert compiled.active_plan is not None

    current = inspect_workspace_plan_currentness(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
    )
    assert current.state == "current"

    engine = RuntimeEngine(paths, stage_runner=_idle_runner)
    engine.startup()
    outcome = engine.tick()
    engine.close()
    assert outcome.router_decision.reason == "no_work"
    assert load_snapshot(paths).process_running is False

    candidate_assets_root = _copy_builtin_assets(tmp_path)
    (candidate_assets_root / "entrypoints" / "execution" / "lad_builder.md").write_text(
        "candidate builder update\n",
        encoding="utf-8",
    )
    preview = preview_baseline_upgrade(paths, candidate_assets_root=candidate_assets_root)
    assert preview.baseline_manifest_id != preview.candidate_manifest_id
    assert (
        preview.classifications_by_path["entrypoints/execution/lad_builder.md"].value
        == "safe_package_update"
    )

    applied = apply_baseline_upgrade(paths, candidate_assets_root=candidate_assets_root)
    assert applied.applied is True
    assert applied.candidate_manifest_id == preview.candidate_manifest_id

    stale = inspect_workspace_plan_currentness(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
    )
    assert stale.state == "stale"

    refreshed = compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
    )
    assert refreshed.diagnostics.ok is True
    assert refreshed.active_plan is not None

    mode_path = paths.runtime_root / "modes" / "lad_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["loop_ids_by_plane"]["planning"] = "planning.unknown"
    mode_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    failed = compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        assets_root=paths.runtime_root,
        refuse_stale_last_known_good=True,
    )
    assert failed.diagnostics.ok is False
    assert failed.active_plan is None
    assert failed.used_last_known_good is False


def test_workspace_upgrade_backfills_missing_wiki_starters_without_overwriting_local_content(tmp_path: Path) -> None:
    paths = initialize_workspace(tmp_path / "workspace")
    manifest = load_baseline_manifest(paths)
    legacy_entries = tuple(
        entry for entry in manifest.entries if not entry.relative_path.startswith("workspace-map/wiki/")
    )
    write_baseline_manifest(paths, manifest.model_copy(update={"entries": legacy_entries}))

    local_runtime_notes = "# Runtime\n\nLocal operator notes.\n"
    paths.workspace_map_wiki_runtime_file.write_text(local_runtime_notes, encoding="utf-8")
    paths.workspace_map_wiki_contracts_file.unlink()

    preview = preview_baseline_upgrade(paths)

    assert (
        preview.classifications_by_path["workspace-map/wiki/domains/runtime.md"]
        is UpgradeDisposition.LOCAL_ONLY_MODIFICATION
    )
    assert preview.classifications_by_path["workspace-map/wiki/domains/contracts.md"] is UpgradeDisposition.MISSING

    outcome = apply_baseline_upgrade(paths)

    assert outcome.applied is True
    assert paths.workspace_map_wiki_runtime_file.read_text(encoding="utf-8") == local_runtime_notes
    assert paths.workspace_map_wiki_contracts_file.is_file()
    assert not (paths.runtime_root / "AGENTS.md").exists()
