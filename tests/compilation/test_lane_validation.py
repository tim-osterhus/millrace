from __future__ import annotations

import json
import shutil
from pathlib import Path

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import bootstrap_workspace


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _compile(tmp_path: Path, *, mode_id: str = "default_codex", assets_root: Path | None = None):
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root, assets_root=assets_root)
    return compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id=mode_id,
        assets_root=assets_root,
    )


def _diagnostic_text(outcome) -> str:
    return "\n".join(outcome.diagnostics.errors)


def test_builtin_default_mode_compiles_one_main_lane_per_plane(tmp_path: Path) -> None:
    outcome = _compile(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    scheduler = outcome.active_plan.scheduler_policy
    assert scheduler is not None
    assert scheduler.experimental_multi_lane is False
    assert {lane.lane_id for lane in scheduler.lanes} == {
        "execution.main",
        "planning.main",
    }
    assert all(lane.max_active_runs == 1 for lane in scheduler.lanes)


def test_builtin_learning_mode_declares_conflict_policy_for_overlap(tmp_path: Path) -> None:
    outcome = _compile(tmp_path, mode_id="learning_codex")

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    scheduler = outcome.active_plan.scheduler_policy
    assert scheduler is not None
    pairs = {policy.lane_pair for policy in scheduler.lane_conflict_policies}
    assert ("execution.main", "learning.main") in pairs
    assert ("learning.main", "planning.main") in pairs


def test_compile_rejects_concurrency_overlap_without_lane_conflict_policy(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    mode_path = assets_root / "modes" / "learning_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["lane_conflict_policies"] = []
    _write_json(mode_path, payload)

    outcome = _compile(tmp_path, mode_id="learning_codex", assets_root=assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "lane conflict policy" in _diagnostic_text(outcome)


def test_compile_rejects_concurrency_overlap_with_invalid_plane_arity(tmp_path: Path) -> None:
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    mode_path = assets_root / "modes" / "learning_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["concurrency_policy"]["may_run_concurrently"].append(
        ["execution", "planning", "learning"]
    )
    _write_json(mode_path, payload)

    outcome = _compile(tmp_path, mode_id="learning_codex", assets_root=assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "may_run_concurrently entries must name exactly two planes" in _diagnostic_text(outcome)
