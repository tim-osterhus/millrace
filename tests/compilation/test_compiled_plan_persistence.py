from __future__ import annotations

import json
import shutil
from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.compilation.plan_authority import has_required_workflow_authority
from millrace_ai.compiler import compile_and_persist_workspace_plan, inspect_workspace_plan_currentness
from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runtime.effects.primitives import default_primitive_executor_registry


def test_load_existing_plan_rejects_plan_missing_runtime_stage(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "codex",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    for graph_key in ("planning_graph",):
        for node in payload[graph_key]["nodes"]:
            if node["stage_kind_id"] == "manager_blueprint":
                node.pop("runtime_stage", None)
    for node in payload["graphs_by_plane"]["planning"]["nodes"]:
        if node["stage_kind_id"] == "manager_blueprint":
            node.pop("runtime_stage", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    assert load_existing_plan(compiled_plan_path) is None

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="blueprint_" "codex",
    )
    assert currentness.state == "missing"
    assert currentness.persisted_plan_id is None


def test_load_existing_plan_rejects_plan_missing_terminal_action_id(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload["execution_graph"]["terminal_states"][0].pop("terminal_action_id", None)
    payload["graphs_by_plane"]["execution"]["terminal_states"][0].pop("terminal_action_id", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    assert load_existing_plan(compiled_plan_path) is None

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert currentness.state == "missing"
    assert currentness.persisted_plan_id is None


# ---------------------------------------------------------------------------
# Persistence round-trip tests for runtime_operations_by_id
# ---------------------------------------------------------------------------


def _copy_builtin_assets(tmp_path: Path) -> Path:
    assets_root = Path(__file__).resolve().parents[2] / "src" / "millrace_ai" / "assets"
    copied_root = tmp_path / "assets"
    shutil.copytree(assets_root, copied_root)
    return copied_root


def _compile_with_assets(
    tmp_path: Path,
    *,
    mode_id: str = "standard_plain",
    assets_root: Path | None = None,
):
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)
    return compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id=mode_id,
        assets_root=assets_root,
    )


def _diagnostic_text(outcome) -> str:
    return "\n".join(outcome.diagnostics.errors)


def test_runtime_operations_survive_persistence_round_trip(tmp_path: Path) -> None:
    """runtime_operations_by_id is present after compile and survives a
    serialize-deserialize round-trip through CompiledRunPlan."""
    outcome = _compile_with_assets(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    original_ops = outcome.active_plan.runtime_operations_by_id
    assert len(original_ops) >= 4  # recon operations + lifecycle operations
    assert "recon.enqueue_task" in original_ops
    assert "recon.enqueue_spec" in original_ops
    assert "recon.noop" in original_ops
    assert "recon.block_work_item" in original_ops
    assert "lifecycle.complete_work_item" in original_ops
    assert "lifecycle.block_work_item" in original_ops

    for op_id, op in original_ops.items():
        assert op.operation_id == op_id
        assert "terminal_action" in op.allowed_contexts

    # Round-trip through JSON serialization.
    dumped = outcome.active_plan.model_dump(mode="json")
    loaded = CompiledRunPlan.model_validate(dumped)

    assert loaded.runtime_operations_by_id == original_ops


def test_runtime_operations_survive_disk_persistence(tmp_path: Path) -> None:
    """runtime_operations_by_id survives writing to compiled_plan.json and
    loading back."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    loaded = load_existing_plan(compiled_plan_path)
    assert loaded is not None

    assert loaded.runtime_operations_by_id == outcome.active_plan.runtime_operations_by_id


def test_plan_without_runtime_operations_loads_with_empty_default(tmp_path: Path) -> None:
    """A compiled plan serialized without runtime_operations_by_id loads
    with an empty dict default (backward compatibility)."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("runtime_operations_by_id", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    loaded = load_existing_plan(compiled_plan_path)
    assert loaded is not None
    assert loaded.runtime_operations_by_id == {}


# ---------------------------------------------------------------------------
# Persistence round-trip tests for scheduler_policy
# ---------------------------------------------------------------------------


def test_scheduler_policy_survives_persistence_round_trip(tmp_path: Path) -> None:
    """scheduler_policy is present after compile and survives a
    serialize-deserialize round-trip through CompiledRunPlan."""
    outcome = _compile_with_assets(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    original = outcome.active_plan.scheduler_policy
    assert original is not None
    assert original.plane_order
    assert original.lanes
    assert not original.experimental_multi_lane

    dumped = outcome.active_plan.model_dump(mode="json")
    loaded = CompiledRunPlan.model_validate(dumped)

    assert loaded.scheduler_policy is not None
    assert loaded.scheduler_policy == original


def test_scheduler_policy_survives_disk_persistence(tmp_path: Path) -> None:
    """scheduler_policy survives writing to compiled_plan.json and loading back."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    loaded = load_existing_plan(compiled_plan_path)
    assert loaded is not None

    assert loaded.scheduler_policy is not None
    assert loaded.scheduler_policy == outcome.active_plan.scheduler_policy


def test_plan_without_scheduler_policy_loads_with_none_default(tmp_path: Path) -> None:
    """A historical plan missing scheduler_policy loads for diagnostics only."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("scheduler_policy", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    loaded = load_existing_plan(compiled_plan_path)
    assert loaded is not None
    assert loaded.scheduler_policy is None
    assert not has_required_workflow_authority(loaded)


def test_plan_without_scheduler_authority_metadata_loads_with_none_default(
    tmp_path: Path,
) -> None:
    """Historical plans missing scheduler authority metadata load but are stale."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
    )
    assert outcome.diagnostics.ok is True

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("scheduler_policy_authority_kind", None)
    payload.pop("selected_scheduler_policy_asset_id", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    loaded = load_existing_plan(compiled_plan_path)
    assert loaded is not None
    assert loaded.scheduler_policy_authority_kind is None
    assert loaded.selected_scheduler_policy_asset_id is None
    assert not has_required_workflow_authority(loaded)


def test_plan_without_selected_recovery_policy_ids_loads_with_none_default(
    tmp_path: Path,
) -> None:
    """Historical plans missing selected recovery ids load but are stale."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="recovery_heavy_millrace",
    )
    assert outcome.diagnostics.ok is True

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("selected_workflow_recovery_policy_ids", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    loaded = load_existing_plan(compiled_plan_path)
    assert loaded is not None
    assert loaded.selected_workflow_recovery_policy_ids is None
    assert not has_required_workflow_authority(loaded)


def test_currentness_marks_plan_without_scheduler_policy_stale(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("scheduler_policy", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
    )

    assert currentness.state == "stale"
    assert currentness.persisted_fingerprint == currentness.expected_fingerprint


def test_currentness_marks_plan_without_scheduler_authority_metadata_stale(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("scheduler_policy_authority_kind", None)
    payload.pop("selected_scheduler_policy_asset_id", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
    )

    assert currentness.state == "stale"
    assert currentness.persisted_fingerprint == currentness.expected_fingerprint


def test_currentness_marks_plan_without_selected_recovery_policies_stale(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="recovery_heavy_millrace",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.selected_workflow_recovery_policy_ids
    assert outcome.active_plan.workflow_recovery_policies_by_id

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload["workflow_recovery_policies_by_id"] = {}
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="recovery_heavy_millrace",
    )

    assert currentness.state == "stale"
    assert currentness.persisted_fingerprint == currentness.expected_fingerprint


def test_currentness_marks_plan_without_selected_recovery_policy_ids_stale(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="recovery_heavy_millrace",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.selected_workflow_recovery_policy_ids

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("selected_workflow_recovery_policy_ids", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    currentness = inspect_workspace_plan_currentness(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="recovery_heavy_millrace",
    )

    assert currentness.state == "stale"
    assert currentness.persisted_fingerprint == currentness.expected_fingerprint


def test_compile_if_needed_recompiles_plan_without_scheduler_policy(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    first = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
    )
    assert first.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("scheduler_policy", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    second = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="lad_codex",
        compile_if_needed=True,
    )

    assert second.active_plan is not None
    assert second.active_plan.scheduler_policy is not None
    loaded = load_existing_plan(compiled_plan_path)
    assert loaded is not None
    assert loaded.scheduler_policy is not None


# ---------------------------------------------------------------------------
# Fingerprint tests
# ---------------------------------------------------------------------------


def test_runtime_operations_assets_are_in_resolved_assets(tmp_path: Path) -> None:
    """Runtime operation registry assets appear in the compiled plan's
    resolved_assets list."""
    outcome = _compile_with_assets(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    resolved_logical_ids = {ref.logical_id for ref in outcome.active_plan.resolved_assets}
    assert "runtime_operation:recon.enqueue_task" in resolved_logical_ids
    assert "runtime_operation:recon.enqueue_spec" in resolved_logical_ids
    assert "runtime_operation:recon.noop" in resolved_logical_ids
    assert "runtime_operation:recon.block_work_item" in resolved_logical_ids


def test_scheduler_policy_assets_are_in_resolved_assets(tmp_path: Path) -> None:
    """Scheduler policy registry assets appear in the compiled plan's
    resolved_assets list."""
    outcome = _compile_with_assets(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    resolved_logical_ids = {ref.logical_id for ref in outcome.active_plan.resolved_assets}
    assert "scheduler_policy:default.two_plane" in resolved_logical_ids


def test_fingerprint_changes_when_runtime_operations_asset_changes(
    tmp_path: Path,
) -> None:
    """Changing a runtime operations asset changes the compile input fingerprint."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")

    baseline = _compile_with_assets(tmp_path, assets_root=assets_root)
    assert baseline.diagnostics.ok is True
    assert baseline.active_plan is not None

    # Mutate the runtime operations asset.
    ops_path = (
        assets_root / "registry" / "runtime_operations" / "default_runtime_operations.json"
    )
    payload = json.loads(ops_path.read_text(encoding="utf-8"))
    payload["definitions"].append(
        {
            "schema_version": "1.0",
            "kind": "runtime_operation",
            "operation_id": "recon.custom_fingerprint_op",
            "allowed_contexts": ["terminal_action"],
            "required_capabilities": ["recon.custom_fingerprint_op"],
            "mutation_phase": "unknown",
            "idempotency": {
                "duplicate_policy": "idempotent",
                "replay_policy": "resume_idempotently",
            },
        }
    )
    ops_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    mutated = _compile_with_assets(tmp_path, assets_root=assets_root)
    assert mutated.diagnostics.ok is True
    assert mutated.active_plan is not None

    assert (
        baseline.active_plan.compile_input_fingerprint.assets_fingerprint
        != mutated.active_plan.compile_input_fingerprint.assets_fingerprint
    )
    assert (
        "recon.custom_fingerprint_op"
        in mutated.active_plan.runtime_operations_by_id
    )


def test_fingerprint_changes_when_scheduler_policy_asset_changes(
    tmp_path: Path,
) -> None:
    """Changing a scheduler policy asset changes the compile input fingerprint."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")

    baseline = _compile_with_assets(tmp_path, assets_root=assets_root)
    assert baseline.diagnostics.ok is True
    assert baseline.active_plan is not None

    # Mutate the scheduler policy asset.
    policy_path = (
        assets_root / "registry" / "scheduler_policies" / "default_two_plane.json"
    )
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["definitions"][0]["closure_priority"] = 999
    policy_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    mutated = _compile_with_assets(tmp_path, assets_root=assets_root)
    assert mutated.diagnostics.ok is True
    assert mutated.active_plan is not None

    assert (
        baseline.active_plan.compile_input_fingerprint.assets_fingerprint
        != mutated.active_plan.compile_input_fingerprint.assets_fingerprint
    )
    assert mutated.active_plan.scheduler_policy is not None
    assert mutated.active_plan.scheduler_policy.closure_priority == 999


def test_compile_input_fingerprint_includes_runtime_operations_asset_content(
    tmp_path: Path,
) -> None:
    """The compile input fingerprint reflects the actual content of the
    runtime operations asset file, not just its presence."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")

    baseline = _compile_with_assets(tmp_path, assets_root=assets_root)
    assert baseline.diagnostics.ok is True

    # Change an operation's mutation_phase.
    ops_path = (
        assets_root / "registry" / "runtime_operations" / "default_runtime_operations.json"
    )
    payload = json.loads(ops_path.read_text(encoding="utf-8"))
    payload["definitions"][0]["mutation_phase"] = "pre_mutation"
    ops_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    mutated = _compile_with_assets(tmp_path, assets_root=assets_root)
    assert mutated.diagnostics.ok is True

    assert (
        baseline.active_plan.compile_input_fingerprint.assets_fingerprint
        != mutated.active_plan.compile_input_fingerprint.assets_fingerprint
    )


def test_learning_mode_compiles_three_plane_scheduler_policy(
    tmp_path: Path,
) -> None:
    """A learning-enabled mode compiles with the three-plane scheduler policy
    that includes a learning lane and lane conflict policies."""
    outcome = _compile_with_assets(tmp_path, mode_id="learning_codex")

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    scheduler = outcome.active_plan.scheduler_policy
    assert scheduler is not None
    assert set(scheduler.plane_order) == {"execution", "planning", "learning"}
    lane_ids = {lane.lane_id for lane in scheduler.lanes}
    assert lane_ids == {"execution.main", "planning.main", "learning.main"}
    assert len(scheduler.lane_conflict_policies) >= 2

    resolved_logical_ids = {ref.logical_id for ref in outcome.active_plan.resolved_assets}
    assert "scheduler_policy:default.three_plane" in resolved_logical_ids


def test_learning_pi_mode_compiles_with_both_registries_active(
    tmp_path: Path,
) -> None:
    """The shipped learning_pi mode compiles successfully and includes both
    runtime_operations_by_id and scheduler_policy."""
    outcome = _compile_with_assets(tmp_path, mode_id="learning_pi")

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None
    assert outcome.active_plan.runtime_operations_by_id
    assert outcome.active_plan.scheduler_policy is not None
    assert "recon.enqueue_task" in outcome.active_plan.runtime_operations_by_id


def test_shipped_modes_compile_with_both_registries(tmp_path: Path) -> None:
    """All shipped mode IDs compile successfully and include both registries."""
    from millrace_ai.modes import SHIPPED_MODE_IDS

    for mode_id in SHIPPED_MODE_IDS:
        outcome = _compile_with_assets(tmp_path, mode_id=mode_id)
        assert outcome.diagnostics.ok is True, (
            f"mode {mode_id} failed: {outcome.diagnostics.errors}"
        )
        assert outcome.active_plan is not None
        assert outcome.active_plan.runtime_operations_by_id, (
            f"mode {mode_id} missing runtime_operations_by_id"
        )
        assert outcome.active_plan.scheduler_policy is not None, (
            f"mode {mode_id} missing scheduler_policy"
        )
        assert outcome.active_plan.runtime_effect_primitives_by_id, (
            f"mode {mode_id} missing runtime_effect_primitives_by_id"
        )


# ---------------------------------------------------------------------------
# Persistence round-trip tests for runtime_effect_primitives_by_id
# ---------------------------------------------------------------------------


def test_runtime_effect_primitives_survive_persistence_round_trip(
    tmp_path: Path,
) -> None:
    """runtime_effect_primitives_by_id is present after compile and survives a
    serialize-deserialize round-trip through CompiledRunPlan."""
    outcome = _compile_with_assets(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    original_primitives = outcome.active_plan.runtime_effect_primitives_by_id
    assert len(original_primitives) >= 17
    assert "legacy_python_handler" in original_primitives
    assert "artifact_presence" in original_primitives
    assert "mutation_journal_append" in original_primitives
    assert "source_lifecycle" in original_primitives

    interpreted_ids: set[str] = set()
    non_interpreted_ids: set[str] = set()
    for primitive_id, primitive in original_primitives.items():
        assert primitive.primitive_id == primitive_id
        if primitive.non_interpreted_compatibility:
            non_interpreted_ids.add(primitive_id)
        else:
            interpreted_ids.add(primitive_id)

    # Shipped interpreted primitives must match their registered executors.
    assert "artifact_presence" in interpreted_ids
    assert "artifact_model_parse" in interpreted_ids
    assert "persist_record" in interpreted_ids
    assert "enqueue_work_items" in interpreted_ids
    assert "emit_event" in interpreted_ids

    # Non-interpreted primitives remain the bulk of shipped definitions.
    assert "legacy_python_handler" in non_interpreted_ids
    assert "mutation_journal_append" in non_interpreted_ids
    assert "source_lifecycle" in non_interpreted_ids
    assert "copy_artifact" in non_interpreted_ids
    assert len(non_interpreted_ids) >= 12

    # Round-trip through JSON serialization.
    dumped = outcome.active_plan.model_dump(mode="json")
    loaded = CompiledRunPlan.model_validate(dumped)

    assert loaded.runtime_effect_primitives_by_id == original_primitives


def test_runtime_effect_primitives_survive_disk_persistence(
    tmp_path: Path,
) -> None:
    """runtime_effect_primitives_by_id survives writing to compiled_plan.json and
    loading back."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    loaded = load_existing_plan(compiled_plan_path)
    assert loaded is not None

    assert (
        loaded.runtime_effect_primitives_by_id
        == outcome.active_plan.runtime_effect_primitives_by_id
    )


def test_plan_without_runtime_effect_primitives_loads_with_empty_default(
    tmp_path: Path,
) -> None:
    """A compiled plan serialized without runtime_effect_primitives_by_id loads
    with an empty dict default (backward compatibility)."""
    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_root)

    outcome = compile_and_persist_workspace_plan(
        workspace_root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.diagnostics.ok is True

    compiled_plan_path = workspace_paths(workspace_root).state_dir / "compiled_plan.json"
    payload = json.loads(compiled_plan_path.read_text(encoding="utf-8"))
    payload.pop("runtime_effect_primitives_by_id", None)
    compiled_plan_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    loaded = load_existing_plan(compiled_plan_path)
    assert loaded is not None
    assert loaded.runtime_effect_primitives_by_id == {}


def test_runtime_effect_primitive_assets_are_in_resolved_assets(
    tmp_path: Path,
) -> None:
    """Runtime effect primitive registry assets appear in the compiled plan's
    resolved_assets list."""
    outcome = _compile_with_assets(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    resolved_logical_ids = {ref.logical_id for ref in outcome.active_plan.resolved_assets}
    assert "runtime_effect_primitive:artifact_presence" in resolved_logical_ids
    assert "runtime_effect_primitive:legacy_python_handler" in resolved_logical_ids
    assert "runtime_effect_primitive:mutation_journal_append" in resolved_logical_ids
    assert "runtime_effect_primitive:source_lifecycle" in resolved_logical_ids


def test_fingerprint_changes_when_runtime_effect_primitives_asset_changes(
    tmp_path: Path,
) -> None:
    """Changing a runtime effect primitive asset changes the compile input
    fingerprint."""
    assets_root = _copy_builtin_assets(tmp_path / "assets")

    baseline = _compile_with_assets(tmp_path, assets_root=assets_root)
    assert baseline.diagnostics.ok is True
    assert baseline.active_plan is not None

    # Mutate the runtime effect primitives asset.
    primitives_path = (
        assets_root
        / "registry"
        / "runtime_effect_primitives"
        / "default_runtime_effect_primitives.json"
    )
    payload = json.loads(primitives_path.read_text(encoding="utf-8"))
    payload["definitions"][0]["primitive_id"] = "artifact_presence_v2"
    primitives_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    mutated = _compile_with_assets(tmp_path, assets_root=assets_root)
    assert mutated.diagnostics.ok is False  # unknown primitive referenced
    assert "references unknown primitive" in _diagnostic_text(mutated)

    assert (
        baseline.active_plan.compile_input_fingerprint.assets_fingerprint
        != mutated.compile_input_fingerprint.assets_fingerprint
    )


# ---------------------------------------------------------------------------
# Cross-validation: executor registry ↔ primitive definitions
# ---------------------------------------------------------------------------


def test_executor_registry_consistent_with_primitive_definitions(
    tmp_path: Path,
) -> None:
    """Every registered interpreted executor has a matching primitive definition
    with non_interpreted_compatibility=False, and every definition marked
    interpreted has a registered executor."""
    outcome = _compile_with_assets(tmp_path)

    assert outcome.diagnostics.ok is True
    assert outcome.active_plan is not None

    primitives_by_id = outcome.active_plan.runtime_effect_primitives_by_id
    registry = default_primitive_executor_registry()

    executor_ids = set(registry.executors_by_id.keys())
    interpreted_def_ids = {
        pid
        for pid, p in primitives_by_id.items()
        if not p.non_interpreted_compatibility
    }

    # Every primitive with a registered executor must be declared interpreted.
    extra_in_registry = executor_ids - interpreted_def_ids
    assert not extra_in_registry, (
        f"executor registry contains primitives not declared interpreted: "
        f"{sorted(extra_in_registry)}"
    )

    # Every interpreted primitive must have a registered executor.
    extra_in_definitions = interpreted_def_ids - executor_ids
    assert not extra_in_definitions, (
        f"interpreted primitive definitions without registered executors: "
        f"{sorted(extra_in_definitions)}"
    )
