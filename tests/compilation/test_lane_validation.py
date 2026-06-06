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


# ---------------------------------------------------------------------------
# Scheduler policy compile validation - unknown planes
# ---------------------------------------------------------------------------


def test_compile_rejects_scheduler_policy_with_unknown_plane(tmp_path: Path) -> None:
    """A scheduler policy referencing a plane not recognized by the model
    is rejected at asset load time."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policy_path = (
        assets_root / "registry" / "scheduler_policies" / "default_two_plane.json"
    )
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["definitions"][0]["plane_order"].append("ghost_plane")
    _write_json(policy_path, payload)

    outcome = _compile(tmp_path, assets_root=assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "ghost_plane" in _diagnostic_text(outcome) or (
        "Invalid scheduler policy" in _diagnostic_text(outcome)
    )


def test_compile_rejects_scheduler_policy_with_wrong_default_for_mode(tmp_path: Path) -> None:
    """When the default two-plane scheduler policy is mutated to include
    learning plane/lane, it gets past Pydantic validation but the
    scheduler policy compiler catches the extra planes."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policy_path = (
        assets_root / "registry" / "scheduler_policies" / "default_two_plane.json"
    )
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["definitions"][0]["plane_order"].append("learning")
    payload["definitions"][0]["lanes"].append(
        {
            "schema_version": "1.0",
            "kind": "workflow_lane",
            "lane_id": "learning.main",
            "plane": "learning",
            "allowed_family_ids": ["learning_request"],
            "claim_policy_id": "learning.default",
            "max_active_runs": 1,
            "one_active_scope": "plane",
        }
    )
    _write_json(policy_path, payload)

    outcome = _compile(tmp_path, assets_root=assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    diagnostic = _diagnostic_text(outcome)
    assert ("learning" in diagnostic
            or "scheduler policy" in diagnostic.lower())


# ---------------------------------------------------------------------------
# Scheduler policy compile validation - duplicate lanes
# ---------------------------------------------------------------------------


def test_compile_rejects_scheduler_policy_with_duplicate_lane_ids(tmp_path: Path) -> None:
    """A scheduler policy with two lanes sharing the same lane_id is rejected."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policy_path = (
        assets_root / "registry" / "scheduler_policies" / "default_two_plane.json"
    )
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    # Duplicate the execution lane.
    dup_lane = dict(payload["definitions"][0]["lanes"][0])
    dup_lane["lane_id"] = "execution.main"  # same as existing
    payload["definitions"][0]["lanes"].append(dup_lane)
    _write_json(policy_path, payload)

    outcome = _compile(tmp_path, assets_root=assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None


def test_compile_rejects_scheduler_policy_with_multi_lane_per_plane_without_experimental(
    tmp_path: Path,
) -> None:
    """A scheduler policy with multiple lanes for the same plane is rejected
    when experimental_multi_lane is False."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policy_path = (
        assets_root / "registry" / "scheduler_policies" / "default_two_plane.json"
    )
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["definitions"][0]["lanes"].append(
        {
            "schema_version": "1.0",
            "kind": "workflow_lane",
            "lane_id": "execution.secondary",
            "plane": "execution",
            "allowed_family_ids": ["task"],
            "claim_policy_id": "execution.default",
            "max_active_runs": 1,
            "one_active_scope": "plane",
        }
    )
    _write_json(policy_path, payload)

    outcome = _compile(tmp_path, assets_root=assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "multi-lane" in _diagnostic_text(outcome).lower() or (
        "2 lanes" in _diagnostic_text(outcome)
        and "execution" in _diagnostic_text(outcome)
    )


# ---------------------------------------------------------------------------
# Scheduler policy compile validation - invalid claim policy references
# ---------------------------------------------------------------------------


def test_compile_rejects_scheduler_policy_with_invalid_claim_policy_reference(
    tmp_path: Path,
) -> None:
    """A lane referencing a claim_policy_id that doesn't match the expected
    queue claim policy for its plane is rejected."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policy_path = (
        assets_root / "registry" / "scheduler_policies" / "default_two_plane.json"
    )
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["definitions"][0]["lanes"][0]["claim_policy_id"] = "ghost.claim.policy"
    _write_json(policy_path, payload)

    outcome = _compile(tmp_path, assets_root=assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "claim policy" in _diagnostic_text(outcome).lower()


# ---------------------------------------------------------------------------
# Scheduler policy compile validation - invalid family order references
# ---------------------------------------------------------------------------


def test_compile_rejects_scheduler_policy_with_unknown_family_in_lane(
    tmp_path: Path,
) -> None:
    """A lane that allows a family id unknown to the system is rejected."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    policy_path = (
        assets_root / "registry" / "scheduler_policies" / "default_two_plane.json"
    )
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["definitions"][0]["lanes"][0]["allowed_family_ids"].append("ghost_family")
    _write_json(policy_path, payload)

    outcome = _compile(tmp_path, assets_root=assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    diagnostic = _diagnostic_text(outcome)
    assert ("ghost_family" in diagnostic
            or "unknown" in diagnostic.lower()
            or "not in" in diagnostic.lower()
            or "included in its claim policy" in diagnostic)


# ---------------------------------------------------------------------------
# Scheduler policy compile validation - explicit policy ID selection
# ---------------------------------------------------------------------------


def test_compile_rejects_mode_with_unknown_scheduler_policy_id(tmp_path: Path) -> None:
    """A mode with an explicit scheduler_policy_id not found in assets is rejected."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["scheduler_policy_id"] = "nonexistent.policy"
    _write_json(mode_path, payload)

    outcome = _compile(tmp_path, assets_root=assets_root)

    assert outcome.diagnostics.ok is False
    assert outcome.active_plan is None
    assert "scheduler policy" in _diagnostic_text(outcome).lower()


def test_compile_accepts_mode_with_explicit_valid_scheduler_policy_id(
    tmp_path: Path,
) -> None:
    """A mode with an explicit scheduler_policy_id pointing to an existing
    policy compiles successfully."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")
    mode_path = assets_root / "modes" / "default_codex.json"
    payload = json.loads(mode_path.read_text(encoding="utf-8"))
    payload["scheduler_policy_id"] = "default.two_plane"
    _write_json(mode_path, payload)

    outcome = _compile(tmp_path, assets_root=assets_root)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.scheduler_policy is not None
    assert outcome.active_plan.scheduler_policy.policy_id == "default.two_plane"


# ---------------------------------------------------------------------------
# Scheduling policy - foreground order and closure priority are compiled
# ---------------------------------------------------------------------------


def test_compiled_scheduler_policy_has_foreground_order(tmp_path: Path) -> None:
    """The compiled scheduler policy includes foreground_order matching
    the shipped default: planning before execution."""
    outcome = _compile(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    scheduler = outcome.active_plan.scheduler_policy
    assert scheduler is not None
    assert scheduler.foreground_order
    planning_idx = scheduler.foreground_order.index("planning")
    execution_idx = scheduler.foreground_order.index("execution")
    assert planning_idx < execution_idx  # planning before execution


def test_compiled_scheduler_policy_closure_priority_positive(tmp_path: Path) -> None:
    """The compiled scheduler policy has closure_priority > 0 so closure
    inverts foreground order."""
    outcome = _compile(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    scheduler = outcome.active_plan.scheduler_policy
    assert scheduler is not None
    assert scheduler.closure_priority > 0


def test_compiled_scheduler_policy_has_claim_policies_by_plane(tmp_path: Path) -> None:
    """The compiled scheduler policy carries claim_policies_by_plane that
    match the queue claim policies."""
    outcome = _compile(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    scheduler = outcome.active_plan.scheduler_policy
    assert scheduler is not None
    assert "execution" in scheduler.claim_policies_by_plane
    assert "planning" in scheduler.claim_policies_by_plane
    assert scheduler.claim_policies_by_plane["execution"].family_order == ("task",)
    assert scheduler.claim_policies_by_plane["planning"].family_order[:2] == (
        "incident",
        "blueprint_draft",
    )
