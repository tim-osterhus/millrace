from __future__ import annotations

from pathlib import Path
import json

import pytest
from pydantic import ValidationError

import millrace_engine.research as research
from millrace_engine.paths import RuntimePaths
from millrace_engine.markdown import parse_task_store
from millrace_engine.research.governance import (
    DEFAULT_PINNED_FAMILY_POLICY_FIELDS,
    apply_initial_family_policy_pin,
    evaluate_initial_family_plan_guard,
    evaluate_family_policy_drift,
    evaluate_governance_canary,
    evaluate_progress_watchdog,
    resolve_family_governor_state,
    sync_progress_watchdog,
)
from millrace_engine.research.provenance import (
    TaskauditProvenance,
    load_task_provenance_registry,
    refresh_task_provenance_registry,
    task_provenance_source_paths,
)
from millrace_engine.research.specs import (
    FrozenInitialFamilySpecPlan,
    GoalSpecFamilySpecState,
    GoalSpecFamilyState,
    GoalSpecReviewStatus,
    INITIAL_FAMILY_FREEZE_MODE,
    INITIAL_FAMILY_PLAN_SCHEMA_VERSION,
    build_initial_family_plan_snapshot,
    load_goal_spec_family_state,
    load_stable_spec_registry,
    refresh_stable_spec_registry,
    stable_spec_metadata_from_file,
    write_goal_spec_family_state,
)
from millrace_engine.research.goalspec import (
    CompletionManifestDraftStateRecord,
    CompletionManifestDraftSurface,
    GoalSource,
    SpecSynthesisRecord,
)
from millrace_engine.research.goalspec_helpers import GoalSpecExecutionError
from millrace_engine.research.goalspec_persistence import _updated_goal_spec_family_state
from millrace_engine.research.taskmaster import (
    TASKMASTER_ARTIFACT_SCHEMA_VERSION,
    TaskAuthoringProfileSelection,
    TaskmasterExecutionResult,
    TaskmasterRecord,
)
from millrace_engine.research.taskaudit import (
    TASKAUDIT_ARTIFACT_SCHEMA_VERSION,
    TaskauditExecutionResult,
    TaskauditRecord,
)

@pytest.mark.parametrize(
    ("exported", "expected"),
    [
        ("FrozenInitialFamilySpecPlan", FrozenInitialFamilySpecPlan),
        ("GoalSpecReviewStatus", GoalSpecReviewStatus),
        ("INITIAL_FAMILY_FREEZE_MODE", INITIAL_FAMILY_FREEZE_MODE),
        ("INITIAL_FAMILY_PLAN_SCHEMA_VERSION", INITIAL_FAMILY_PLAN_SCHEMA_VERSION),
        ("DEFAULT_PINNED_FAMILY_POLICY_FIELDS", DEFAULT_PINNED_FAMILY_POLICY_FIELDS),
        ("apply_initial_family_policy_pin", apply_initial_family_policy_pin),
        ("evaluate_family_policy_drift", evaluate_family_policy_drift),
        ("evaluate_governance_canary", evaluate_governance_canary),
        ("evaluate_initial_family_plan_guard", evaluate_initial_family_plan_guard),
        ("resolve_family_governor_state", resolve_family_governor_state),
        ("CompletionManifestDraftStateRecord", CompletionManifestDraftStateRecord),
        ("CompletionManifestDraftSurface", CompletionManifestDraftSurface),
        ("SpecSynthesisRecord", SpecSynthesisRecord),
        ("TASKMASTER_ARTIFACT_SCHEMA_VERSION", TASKMASTER_ARTIFACT_SCHEMA_VERSION),
        ("TaskAuthoringProfileSelection", TaskAuthoringProfileSelection),
        ("TaskmasterExecutionResult", TaskmasterExecutionResult),
        ("TaskmasterRecord", TaskmasterRecord),
        ("TASKAUDIT_ARTIFACT_SCHEMA_VERSION", TASKAUDIT_ARTIFACT_SCHEMA_VERSION),
        ("TaskauditExecutionResult", TaskauditExecutionResult),
        ("TaskauditProvenance", TaskauditProvenance),
        ("TaskauditRecord", TaskauditRecord),
    ],
)
def test_research_package_re_exports_contracts(exported: str, expected: object) -> None:
    assert getattr(research, exported) is expected


def test_refresh_stable_spec_registry_bootstraps_and_writes_deterministically(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    stable_root = workspace / "agents" / "specs" / "stable"
    frozen_dir = stable_root / ".frozen"
    index_path = workspace / "agents" / "specs" / "index.json"

    golden_spec = stable_root / "golden" / "SPEC-ALPHA__alpha.md"
    phase_spec = stable_root / "phase" / "SPEC-BETA__phase-01.md"
    plain_spec = stable_root / "misc" / "SPEC-GAMMA__notes.md"
    golden_spec.parent.mkdir(parents=True, exist_ok=True)
    phase_spec.parent.mkdir(parents=True, exist_ok=True)
    plain_spec.parent.mkdir(parents=True, exist_ok=True)
    frozen_dir.mkdir(parents=True, exist_ok=True)

    golden_spec.write_text("# Golden\n", encoding="utf-8")
    phase_spec.write_text("# Phase\n", encoding="utf-8")
    plain_spec.write_text("# Plain\n", encoding="utf-8")
    stale_marker = frozen_dir / "obsolete.frozen"
    stale_marker.write_text("stale\n", encoding="utf-8")

    registry = refresh_stable_spec_registry(
        stable_root,
        frozen_dir,
        index_path,
        relative_to=workspace,
        updated_at="2026-03-21T10:00:00Z",
    )

    assert index_path.exists()
    assert not stale_marker.exists()
    assert [entry.spec_path for entry in registry.stable_specs] == [
        "agents/specs/stable/golden/SPEC-ALPHA__alpha.md",
        "agents/specs/stable/misc/SPEC-GAMMA__notes.md",
        "agents/specs/stable/phase/SPEC-BETA__phase-01.md",
    ]
    assert [entry.frozen_tier for entry in registry.stable_specs] == ["golden", "", "phase"]
    assert registry.stable_specs[1].freeze_marker == ""
    assert registry.stable_specs[0].freeze_marker.endswith(".frozen")
    assert registry.stable_specs[2].checksum_marker.endswith(".sha256")

    first_text = index_path.read_text(encoding="utf-8")
    refreshed = refresh_stable_spec_registry(
        stable_root,
        frozen_dir,
        index_path,
        relative_to=workspace,
        updated_at="2026-03-21T10:00:00Z",
    )

    assert index_path.read_text(encoding="utf-8") == first_text
    assert load_stable_spec_registry(index_path) == refreshed


def test_goal_spec_family_state_validates_and_round_trips_with_frozen_initial_plan(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    goal_file = workspace / "agents" / "ideas" / "raw" / "goal.md"
    policy_file = workspace / "agents" / "policies" / "family.json"
    state_path = workspace / "agents" / ".research_runtime" / "spec_family_state.json"
    goal_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.parent.mkdir(parents=True, exist_ok=True)

    goal_file.write_text("---\nidea_id: IDEA-42\n---\n# Goal\n", encoding="utf-8")
    policy_file.write_text(
        json.dumps(
            {
                "family_cap_mode": "adaptive",
                "initial_family_max_specs": 3,
            }
        ),
        encoding="utf-8",
    )

    state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-42",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "active_spec_id": "SPEC-ALPHA",
            "spec_order": ["SPEC-ALPHA", "SPEC-BETA"],
            "specs": {
                "SPEC-ALPHA": {
                    "status": "reviewed",
                    "title": "Alpha spec",
                    "decomposition_profile": "moderate",
                    "queue_path": "agents/ideas/specs/SPEC-ALPHA__alpha.md",
                },
                "SPEC-BETA": {
                    "status": "planned",
                    "title": "Beta follow-on",
                    "decomposition_profile": "simple",
                    "depends_on_specs": ["SPEC-ALPHA"],
                },
            },
            "family_governor": {
                "policy_path": "agents/policies/family.json",
                "initial_family_max_specs": 2,
                "applied_family_max_specs": 2,
            },
        }
    )

    plan = build_initial_family_plan_snapshot(
        state,
        repo_root=workspace,
        goal_file=goal_file,
        policy_path=policy_file,
        frozen_at="2026-03-21T10:05:00Z",
    )
    persisted = write_goal_spec_family_state(
        state_path,
        state.model_copy(update={"initial_family_plan": plan}),
        updated_at="2026-03-21T10:06:00Z",
    )

    assert plan.freeze_trigger_spec_id == "SPEC-ALPHA"
    assert plan.initial_family_max_specs == 3
    assert plan.applied_family_max_specs == 2
    assert plan.goal_sha256
    assert plan.family_policy_sha256
    assert json.loads(plan.model_dump_json())["completed_at"] == ""
    assert load_goal_spec_family_state(state_path) == persisted


def test_completion_manifest_state_tracks_artifacts_and_product_surfaces_separately() -> None:
    state = CompletionManifestDraftStateRecord.model_validate(
        {
            "draft_id": "idea-42-completion-manifest",
            "goal_id": "IDEA-42",
            "title": "Neighborhood Events Hub",
            "run_id": "goalspec-run-42",
            "updated_at": "2026-04-07T12:00:00Z",
            "canonical_source_path": "agents/ideas/archive/raw/goal__goalspec-run-42__abc123def456.md",
            "current_artifact_path": "agents/ideas/staging/IDEA-42__neighborhood-events-hub.md",
            "source_path": "agents/ideas/archive/raw/goal__goalspec-run-42__abc123def456.md",
            "research_brief_path": "agents/ideas/staging/IDEA-42__neighborhood-events-hub.md",
            "objective_profile_state_path": "agents/objective/profile_sync_state.json",
            "objective_profile_path": "agents/reports/acceptance_profiles/idea-42-profile.json",
            "completion_manifest_plan_path": "agents/reports/completion_manifest_plan.md",
            "goal_intake_record_path": "agents/.research_runtime/goalspec/goal_intake/goalspec-run-42.json",
            "planning_profile": "generic_product",
            "acceptance_focus": ["Collector works", "Flow validates"],
            "open_questions": ["Implementation remains open."],
            "required_artifacts": [
                {
                    "artifact_kind": "queue_spec",
                    "path": "agents/ideas/specs/SPEC-42__neighborhood-events-hub.md",
                    "purpose": "Primary queue spec for downstream review.",
                }
            ],
            "implementation_surfaces": [
                {
                    "surface_kind": "entrypoint",
                    "path": "src/neighborhood-events-hub/entrypoint",
                    "purpose": "Expose the bounded product entry surface.",
                }
            ],
            "verification_surfaces": [
                {
                    "surface_kind": "flow_verification",
                    "path": "tests/neighborhood-events-hub/flow",
                    "purpose": "Lock the bounded product flow.",
                }
            ],
        }
    )

    assert state.canonical_source_path.startswith("agents/ideas/archive/")
    assert state.current_artifact_path.startswith("agents/ideas/staging/")
    assert state.required_artifacts[0].path.startswith("agents/")
    assert state.implementation_surfaces[0].path.startswith("src/")
    assert state.verification_surfaces[0].path.startswith("tests/")


def test_goal_spec_family_state_rejects_specs_without_matching_spec_order() -> None:
    with pytest.raises(ValidationError):
        GoalSpecFamilyState.model_validate(
            {
                "specs": {
                    "SPEC-ALPHA": {
                        "status": "planned",
                    }
                }
            }
        )


def test_goal_spec_family_spec_state_lineage_projects_review_and_stable_paths() -> None:
    spec_state = GoalSpecFamilySpecState.model_validate(
        {
            "status": "reviewed",
            "review_status": "no_material_delta",
            "queue_path": "agents/ideas/specs/SPEC-ALPHA__alpha.md",
            "reviewed_path": "agents/ideas/specs_reviewed/SPEC-ALPHA__alpha.md",
            "stable_spec_paths": [
                "agents/specs/stable/golden/SPEC-ALPHA__alpha.md",
                "agents/specs/stable/phase/SPEC-ALPHA__phase-01.md",
            ],
        }
    )

    lineage = spec_state.lineage(
        spec_id="SPEC-ALPHA",
        goal_id="IDEA-42",
        source_idea_path="agents/ideas/raw/goal.md",
    )

    assert lineage.spec_id == "SPEC-ALPHA"
    assert lineage.goal_id == "IDEA-42"
    assert lineage.source_idea_path == "agents/ideas/raw/goal.md"
    assert lineage.queue_path == "agents/ideas/specs/SPEC-ALPHA__alpha.md"
    assert lineage.reviewed_path == "agents/ideas/specs_reviewed/SPEC-ALPHA__alpha.md"
    assert lineage.stable_spec_paths == (
        "agents/specs/stable/golden/SPEC-ALPHA__alpha.md",
        "agents/specs/stable/phase/SPEC-ALPHA__phase-01.md",
    )


def test_family_governor_cap_blocks_growth_beyond_policy_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paths = RuntimePaths.from_workspace(workspace, Path("agents"))
    policy_path = workspace / "agents" / "objective" / "family_policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        json.dumps(
            {
                "initial_family_max_specs": 2,
                "remediation_family_max_specs": 5,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    current_state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-42",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-ALPHA",
            "spec_order": ["SPEC-ALPHA", "SPEC-BETA"],
            "specs": {
                "SPEC-ALPHA": {
                    "status": "emitted",
                    "title": "Alpha",
                    "decomposition_profile": "moderate",
                },
                "SPEC-BETA": {
                    "status": "planned",
                    "title": "Beta",
                    "decomposition_profile": "moderate",
                    "depends_on_specs": ["SPEC-ALPHA"],
                },
            },
        }
    )
    governor = resolve_family_governor_state(
        paths=paths,
        current_state=current_state,
        policy_payload={
            "initial_family_max_specs": 2,
            "remediation_family_max_specs": 5,
        },
    )
    guarded_state = current_state.model_copy(update={"family_governor": governor})
    proposed_specs = dict(guarded_state.specs)
    proposed_specs["SPEC-GAMMA"] = GoalSpecFamilySpecState(
        status="planned",
        title="Gamma",
        decomposition_profile="simple",
        depends_on_specs=("SPEC-BETA",),
    )

    decision = evaluate_initial_family_plan_guard(
        current_state=guarded_state,
        candidate_spec_id="SPEC-GAMMA",
        proposed_spec_order=("SPEC-ALPHA", "SPEC-BETA", "SPEC-GAMMA"),
        proposed_specs=proposed_specs,
    )

    assert governor.policy_path == "agents/objective/family_policy.json"
    assert governor.initial_family_max_specs == 2
    assert governor.applied_family_max_specs == 2
    assert decision.action == "block"
    assert decision.reason == "family-governor-cap-exceeded"
    assert decision.applied_family_max_specs == 2
    assert decision.proposed_spec_count == 3
    assert decision.added_spec_ids == ("SPEC-GAMMA",)
    assert decision.violation_codes == ("family-cap-exceeded",)


def test_initial_family_plan_guard_blocks_added_specs_after_freeze(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    goal_file = workspace / "agents" / "ideas" / "raw" / "goal.md"
    policy_file = workspace / "agents" / "objective" / "family_policy.json"
    goal_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text("# Goal\n", encoding="utf-8")
    policy_file.write_text(json.dumps({"initial_family_max_specs": 2}) + "\n", encoding="utf-8")

    state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-42",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-ALPHA",
            "spec_order": ["SPEC-ALPHA"],
            "specs": {
                "SPEC-ALPHA": {
                    "status": "emitted",
                    "title": "Alpha",
                    "decomposition_profile": "moderate",
                }
            },
            "family_governor": {
                "policy_path": "agents/objective/family_policy.json",
                "initial_family_max_specs": 2,
                "applied_family_max_specs": 2,
            },
        }
    )
    frozen_state = state.model_copy(
        update={
            "initial_family_plan": build_initial_family_plan_snapshot(
                state,
                repo_root=workspace,
                goal_file=goal_file,
                policy_path=policy_file,
                frozen_at="2026-03-21T10:05:00Z",
            )
        }
    )
    proposed_specs = dict(frozen_state.specs)
    proposed_specs["SPEC-BETA"] = GoalSpecFamilySpecState(
        status="planned",
        title="Beta",
        decomposition_profile="simple",
        depends_on_specs=("SPEC-ALPHA",),
    )

    decision = evaluate_initial_family_plan_guard(
        current_state=frozen_state,
        candidate_spec_id="SPEC-BETA",
        proposed_spec_order=("SPEC-ALPHA", "SPEC-BETA"),
        proposed_specs=proposed_specs,
    )

    assert decision.action == "block"
    assert decision.reason == "frozen-initial-family-plan-drift"
    assert decision.frozen is True
    assert decision.added_spec_ids == ("SPEC-BETA",)
    assert "added-spec-ids" in decision.violation_codes


def test_initial_family_plan_guard_blocks_reordered_broad_frozen_plan(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    goal_file = workspace / "agents" / "ideas" / "raw" / "goal.md"
    policy_file = workspace / "agents" / "objective" / "family_policy.json"
    goal_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text("# Goal\n", encoding="utf-8")
    policy_file.write_text(json.dumps({"initial_family_max_specs": 4}) + "\n", encoding="utf-8")

    state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-42",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-ALPHA",
            "spec_order": ["SPEC-ALPHA", "SPEC-BETA", "SPEC-GAMMA", "SPEC-DELTA"],
            "specs": {
                "SPEC-ALPHA": {
                    "status": "emitted",
                    "title": "Alpha",
                    "decomposition_profile": "moderate",
                },
                "SPEC-BETA": {
                    "status": "planned",
                    "title": "Beta",
                    "decomposition_profile": "moderate",
                    "depends_on_specs": ["SPEC-ALPHA"],
                },
                "SPEC-GAMMA": {
                    "status": "planned",
                    "title": "Gamma",
                    "decomposition_profile": "moderate",
                    "depends_on_specs": ["SPEC-BETA"],
                },
                "SPEC-DELTA": {
                    "status": "planned",
                    "title": "Delta",
                    "decomposition_profile": "moderate",
                    "depends_on_specs": ["SPEC-GAMMA"],
                },
            },
            "family_governor": {
                "policy_path": "agents/objective/family_policy.json",
                "initial_family_max_specs": 4,
                "applied_family_max_specs": 4,
            },
        }
    )
    frozen_state = state.model_copy(
        update={
            "initial_family_plan": build_initial_family_plan_snapshot(
                state,
                repo_root=workspace,
                goal_file=goal_file,
                policy_path=policy_file,
                frozen_at="2026-03-21T10:05:00Z",
            )
        }
    )

    decision = evaluate_initial_family_plan_guard(
        current_state=frozen_state,
        candidate_spec_id="SPEC-ALPHA",
        proposed_spec_order=("SPEC-ALPHA", "SPEC-GAMMA", "SPEC-BETA", "SPEC-DELTA"),
        proposed_specs=dict(frozen_state.specs),
    )

    assert decision.action == "block"
    assert decision.reason == "frozen-initial-family-plan-drift"
    assert decision.frozen is True
    assert "spec-order-changed" in decision.violation_codes


def test_apply_initial_family_policy_pin_preserves_frozen_family_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paths = RuntimePaths.from_workspace(workspace, Path("agents"))
    goal_file = workspace / "agents" / "ideas" / "raw" / "goal.md"
    policy_file = workspace / "agents" / "objective" / "family_policy.json"
    goal_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text("# Goal\n", encoding="utf-8")
    policy_file.write_text(
        json.dumps({"family_cap_mode": "adaptive", "initial_family_max_specs": 3}) + "\n",
        encoding="utf-8",
    )

    state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-42",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-ALPHA",
            "spec_order": ["SPEC-ALPHA"],
            "specs": {
                "SPEC-ALPHA": {
                    "status": "emitted",
                    "title": "Alpha",
                    "decomposition_profile": "moderate",
                }
            },
            "family_governor": {
                "policy_path": "agents/objective/family_policy.json",
                "initial_family_max_specs": 3,
                "applied_family_max_specs": 3,
            },
        }
    )
    frozen_state = state.model_copy(
        update={
            "initial_family_plan": build_initial_family_plan_snapshot(
                state,
                repo_root=workspace,
                goal_file=goal_file,
                policy_path=policy_file,
                frozen_at="2026-03-21T10:05:00Z",
            )
        }
    )

    next_payload, decision = apply_initial_family_policy_pin(
        paths=paths,
        current_policy_payload={
            "family_cap_mode": "deterministic",
            "initial_family_max_specs": 1,
            "source_goal_id": "IDEA-42",
        },
        current_family_state=frozen_state,
    )

    assert next_payload["family_cap_mode"] == "adaptive"
    assert next_payload["initial_family_max_specs"] == 3
    assert decision.active is True
    assert decision.action == "pin"
    assert decision.reason == "frozen-initial-family-policy-preserved"
    assert decision.pinned_fields == ("family_cap_mode", "initial_family_max_specs")


def test_governance_canary_and_drift_reports_remain_explainable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paths = RuntimePaths.from_workspace(workspace, Path("agents"))
    goal_file = workspace / "agents" / "ideas" / "raw" / "goal.md"
    family_policy_file = workspace / "agents" / "objective" / "family_policy.json"
    baseline_policy_file = paths.governance_canary_baseline_policy_file
    drift_policy_file = paths.drift_control_policy_file
    goal_file.parent.mkdir(parents=True, exist_ok=True)
    family_policy_file.parent.mkdir(parents=True, exist_ok=True)
    baseline_policy_file.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text("# Goal\n", encoding="utf-8")
    family_policy_file.write_text(
        json.dumps({"family_cap_mode": "adaptive", "initial_family_max_specs": 3}) + "\n",
        encoding="utf-8",
    )
    baseline_policy_file.write_text(
        json.dumps({"watched_family_policy_fields": list(DEFAULT_PINNED_FAMILY_POLICY_FIELDS)}) + "\n",
        encoding="utf-8",
    )
    drift_policy_file.write_text(
        json.dumps(
            {
                "watched_family_policy_fields": list(DEFAULT_PINNED_FAMILY_POLICY_FIELDS),
                "hard_latch_on_policy_drift": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-42",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-ALPHA",
            "spec_order": ["SPEC-ALPHA"],
            "specs": {
                "SPEC-ALPHA": {
                    "status": "emitted",
                    "title": "Alpha",
                    "decomposition_profile": "moderate",
                }
            },
            "family_governor": {
                "policy_path": "agents/objective/family_policy.json",
                "initial_family_max_specs": 3,
                "applied_family_max_specs": 3,
            },
        }
    )
    frozen_state = state.model_copy(
        update={
            "initial_family_plan": build_initial_family_plan_snapshot(
                state,
                repo_root=workspace,
                goal_file=goal_file,
                policy_path=family_policy_file,
                frozen_at="2026-03-21T10:05:00Z",
            )
        }
    )
    family_policy_file.write_text(
        json.dumps({"family_cap_mode": "adaptive", "initial_family_max_specs": 4}) + "\n",
        encoding="utf-8",
    )
    drift_report = evaluate_family_policy_drift(paths=paths, current_family_state=frozen_state)
    canary_report = evaluate_governance_canary(paths=paths)

    assert drift_report.status == "hard_latch"
    assert drift_report.reason == "frozen-family-policy-drift-detected"
    assert drift_report.drift_fields == ("initial_family_max_specs",)
    assert drift_report.hard_latch_active is True
    assert canary_report.status == "drifted"
    assert canary_report.reason == "governance-canary-policy-drift"
    assert canary_report.changed_fields == ("hard_latch_on_policy_drift",)


def test_updated_goal_spec_family_state_preserves_goal_gap_remediation_phase(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paths = RuntimePaths.from_workspace(workspace, Path("agents"))
    queue_spec_path = workspace / "agents" / "ideas" / "specs" / "SPEC-REM-001__remediation.md"
    policy_path = workspace / "agents" / "objective" / "family_policy.json"
    queue_spec_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        json.dumps({"initial_family_max_specs": 4, "remediation_family_max_specs": 1}) + "\n",
        encoding="utf-8",
    )
    write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        GoalSpecFamilyState.model_validate(
            {
                "goal_id": "IDEA-REM-001",
                "source_idea_path": "agents/ideas/staging/IDEA-REM-001__goal-gap-remediation.md",
                "family_phase": "goal_gap_remediation",
                "family_complete": False,
                "active_spec_id": "",
                "spec_order": [],
                "specs": {},
                "family_governor": {
                    "policy_path": "agents/objective/family_policy.json",
                    "initial_family_max_specs": 4,
                    "remediation_family_max_specs": 1,
                    "applied_family_max_specs": 1,
                },
            }
        ),
        updated_at="2026-03-21T10:05:00Z",
    )

    next_state = _updated_goal_spec_family_state(
        paths=paths,
        source=GoalSource.model_validate(
            {
                "current_artifact_path": (workspace / "agents" / "ideas" / "staging" / "IDEA-REM-001__goal-gap-remediation.md").as_posix(),
                "current_artifact_relative_path": "agents/ideas/staging/IDEA-REM-001__goal-gap-remediation.md",
                "canonical_source_path": (workspace / "agents" / "ideas" / "raw" / "goal.md").as_posix(),
                "canonical_relative_source_path": "agents/ideas/raw/goal.md",
                "source_path": (workspace / "agents" / "ideas" / "staging" / "IDEA-REM-001__goal-gap-remediation.md").as_posix(),
                "relative_source_path": "agents/ideas/staging/IDEA-REM-001__goal-gap-remediation.md",
                "idea_id": "IDEA-REM-001",
                "title": "Goal gap remediation source",
                "decomposition_profile": "simple",
                "frontmatter": {},
                "body": "Goal gap remediation source",
                "canonical_body": "Canonical goal",
                "checksum_sha256": "a" * 64,
            }
        ),
        spec_id="SPEC-REM-001",
        title="Goal gap remediation spec",
        decomposition_profile="simple",
        queue_spec_path=queue_spec_path,
        emitted_at="2026-03-21T10:10:00Z",
    )

    assert next_state.family_phase == "goal_gap_remediation"
    assert next_state.family_governor is not None
    assert next_state.family_governor.applied_family_max_specs == 1
    assert next_state.spec_order == ("SPEC-REM-001",)


def test_updated_goal_spec_family_state_preserves_promoted_sibling_dependencies(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paths = RuntimePaths.from_workspace(workspace, Path("agents"))
    goal_file = workspace / "agents" / "ideas" / "raw" / "goal.md"
    queue_spec_path = workspace / "agents" / "ideas" / "specs" / "SPEC-BETA__beta.md"
    policy_path = workspace / "agents" / "objective" / "family_policy.json"
    for path in (goal_file, queue_spec_path, policy_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text("# Goal\n", encoding="utf-8")
    queue_spec_path.write_text("# Beta\n", encoding="utf-8")
    policy_path.write_text(json.dumps({"initial_family_max_specs": 2}) + "\n", encoding="utf-8")

    state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-42",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-ALPHA",
            "spec_order": ["SPEC-ALPHA", "SPEC-BETA"],
            "specs": {
                "SPEC-ALPHA": {
                    "status": "emitted",
                    "title": "Alpha",
                    "decomposition_profile": "moderate",
                    "queue_path": "agents/ideas/specs/SPEC-ALPHA__alpha.md",
                },
                "SPEC-BETA": {
                    "status": "planned",
                    "title": "Beta",
                    "decomposition_profile": "moderate",
                    "depends_on_specs": ["SPEC-ALPHA"],
                },
            },
            "family_governor": {
                "policy_path": "agents/objective/family_policy.json",
                "initial_family_max_specs": 2,
                "applied_family_max_specs": 2,
            },
        }
    )
    frozen_state = state.model_copy(
        update={
            "initial_family_plan": build_initial_family_plan_snapshot(
                state,
                repo_root=workspace,
                goal_file=goal_file,
                policy_path=policy_path,
                frozen_at="2026-03-21T10:05:00Z",
            )
        }
    )
    write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        frozen_state,
        updated_at="2026-03-21T10:05:00Z",
    )

    next_state = _updated_goal_spec_family_state(
        paths=paths,
        source=GoalSource.model_validate(
            {
                "current_artifact_path": goal_file.as_posix(),
                "current_artifact_relative_path": "agents/ideas/raw/goal.md",
                "canonical_source_path": goal_file.as_posix(),
                "canonical_relative_source_path": "agents/ideas/raw/goal.md",
                "source_path": goal_file.as_posix(),
                "relative_source_path": "agents/ideas/raw/goal.md",
                "idea_id": "IDEA-42",
                "title": "Goal source",
                "decomposition_profile": "moderate",
                "frontmatter": {},
                "body": "Goal source",
                "canonical_body": "Goal source",
                "checksum_sha256": "a" * 64,
            }
        ),
        spec_id="SPEC-BETA",
        title="Beta",
        decomposition_profile="moderate",
        depends_on_specs=("SPEC-ALPHA",),
        queue_spec_path=queue_spec_path,
        emitted_at="2026-03-21T10:10:00Z",
        family_complete=False,
    )

    assert next_state.active_spec_id == "SPEC-BETA"
    assert next_state.specs["SPEC-BETA"].status == "emitted"
    assert next_state.specs["SPEC-BETA"].depends_on_specs == ("SPEC-ALPHA",)
    assert load_goal_spec_family_state(paths.goal_spec_family_state_file).specs["SPEC-BETA"].depends_on_specs == (
        "SPEC-ALPHA",
    )


def test_updated_goal_spec_family_state_still_blocks_mutated_promoted_sibling_dependencies(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    paths = RuntimePaths.from_workspace(workspace, Path("agents"))
    goal_file = workspace / "agents" / "ideas" / "raw" / "goal.md"
    queue_spec_path = workspace / "agents" / "ideas" / "specs" / "SPEC-BETA__beta.md"
    policy_path = workspace / "agents" / "objective" / "family_policy.json"
    for path in (goal_file, queue_spec_path, policy_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text("# Goal\n", encoding="utf-8")
    queue_spec_path.write_text("# Beta\n", encoding="utf-8")
    policy_path.write_text(json.dumps({"initial_family_max_specs": 2}) + "\n", encoding="utf-8")

    state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-42",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-ALPHA",
            "spec_order": ["SPEC-ALPHA", "SPEC-BETA"],
            "specs": {
                "SPEC-ALPHA": {
                    "status": "emitted",
                    "title": "Alpha",
                    "decomposition_profile": "moderate",
                    "queue_path": "agents/ideas/specs/SPEC-ALPHA__alpha.md",
                },
                "SPEC-BETA": {
                    "status": "planned",
                    "title": "Beta",
                    "decomposition_profile": "moderate",
                    "depends_on_specs": ["SPEC-ALPHA"],
                },
            },
            "family_governor": {
                "policy_path": "agents/objective/family_policy.json",
                "initial_family_max_specs": 2,
                "applied_family_max_specs": 2,
            },
        }
    )
    frozen_state = state.model_copy(
        update={
            "initial_family_plan": build_initial_family_plan_snapshot(
                state,
                repo_root=workspace,
                goal_file=goal_file,
                policy_path=policy_path,
                frozen_at="2026-03-21T10:05:00Z",
            )
        }
    )
    write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        frozen_state,
        updated_at="2026-03-21T10:05:00Z",
    )

    with pytest.raises(GoalSpecExecutionError, match="frozen-initial-family-plan-drift"):
        _updated_goal_spec_family_state(
            paths=paths,
            source=GoalSource.model_validate(
                {
                    "current_artifact_path": goal_file.as_posix(),
                    "current_artifact_relative_path": "agents/ideas/raw/goal.md",
                    "canonical_source_path": goal_file.as_posix(),
                    "canonical_relative_source_path": "agents/ideas/raw/goal.md",
                    "source_path": goal_file.as_posix(),
                    "relative_source_path": "agents/ideas/raw/goal.md",
                    "idea_id": "IDEA-42",
                    "title": "Goal source",
                    "decomposition_profile": "moderate",
                    "frontmatter": {},
                    "body": "Goal source",
                    "canonical_body": "Goal source",
                    "checksum_sha256": "a" * 64,
                }
            ),
            spec_id="SPEC-BETA",
            title="Beta",
            decomposition_profile="moderate",
            depends_on_specs=(),
            queue_spec_path=queue_spec_path,
            emitted_at="2026-03-21T10:10:00Z",
            family_complete=False,
        )


def test_progress_watchdog_regenerates_missing_audit_recovery_task_without_rewriting_family_policy_history(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    paths = RuntimePaths.from_workspace(workspace, Path("agents"))
    goal_file = workspace / "agents" / "ideas" / "raw" / "goal.md"
    family_policy_file = workspace / "agents" / "objective" / "family_policy.json"
    backlog_file = workspace / "agents" / "tasksbacklog.md"
    for path in (
        goal_file,
        family_policy_file,
        backlog_file,
        workspace / "agents" / "tasks.md",
        workspace / "agents" / "taskspending.md",
        workspace / "agents" / "tasksbackburner.md",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text("# Goal\n", encoding="utf-8")
    family_policy_file.write_text(
        json.dumps({"family_cap_mode": "adaptive", "initial_family_max_specs": 3}) + "\n",
        encoding="utf-8",
    )
    backlog_file.write_text("# Task Backlog\n", encoding="utf-8")
    (workspace / "agents" / "tasks.md").write_text("# Active Task\n", encoding="utf-8")
    (workspace / "agents" / "taskspending.md").write_text("# Tasks Pending\n", encoding="utf-8")
    (workspace / "agents" / "tasksbackburner.md").write_text("# Task Backburner\n", encoding="utf-8")

    state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-99",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-ALPHA",
            "spec_order": ["SPEC-ALPHA"],
            "specs": {
                "SPEC-ALPHA": {
                    "status": "reviewed",
                    "title": "Alpha",
                    "decomposition_profile": "moderate",
                }
            },
            "family_governor": {
                "policy_path": "agents/objective/family_policy.json",
                "initial_family_max_specs": 3,
                "applied_family_max_specs": 3,
            },
        }
    )
    frozen_state = state.model_copy(
        update={
            "initial_family_plan": build_initial_family_plan_snapshot(
                state,
                repo_root=workspace,
                goal_file=goal_file,
                policy_path=family_policy_file,
                frozen_at="2026-03-21T10:05:00Z",
            )
        }
    )
    write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        frozen_state,
        updated_at="2026-03-21T10:06:00Z",
    )

    remediation_record_path = paths.research_runtime_dir / "audit" / "remediation" / "watchdog-audit.json"
    remediation_record_path.parent.mkdir(parents=True, exist_ok=True)
    remediation_record_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "artifact_type": "audit_remediation",
                "run_id": "watchdog-audit",
                "emitted_at": "2026-03-21T12:10:00Z",
                "audit_id": "AUD-WATCHDOG-001",
                "title": "Watchdog audit remediation",
                "scope": "governance-watchdog",
                "trigger": "manual",
                "source_path": "agents/ideas/audit/incoming/AUD-WATCHDOG-001.md",
                "terminal_path": "agents/ideas/audit/failed/AUD-WATCHDOG-001.md",
                "gate_decision_path": "agents/reports/audit_gate_decision.json",
                "completion_decision_path": "agents/reports/completion_decision.json",
                "validate_record_path": "agents/.research_runtime/audit/validate/watchdog-audit.json",
                "execution_report_path": "agents/.research_runtime/audit/execution/watchdog-audit.json",
                "selected_action": "enqueue_backlog_task",
                "remediation_spec_id": "SPEC-AUD-WATCHDOG-001",
                "remediation_task_id": "2026-03-21__watchdog-audit-remediation-task",
                "remediation_task_title": "Watchdog audit remediation task",
                "backlog_depth_after_enqueue": 1,
                "reasons": ["Restore a durable audit remediation task when the backlog copy disappears."],
                "recovery_latch_updated": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    paths.research_recovery_latch_file.parent.mkdir(parents=True, exist_ok=True)
    paths.research_recovery_latch_file.write_text(
        json.dumps(
            {
                "state": "frozen",
                "batch_id": "20260321T121500Z",
                "frozen_at": "2026-03-21T12:15:00Z",
                "failure_signature": "watchdog-audit",
                "stage": "Consult",
                "reason": "Recovery latch waiting for audit remediation.",
                "frozen_backlog_cards": 2,
                "retained_backlog_cards": 0,
                "quarantine_reason": "consult_handoff",
                "remediation_decision": {
                    "decision_type": "durable_remediation_decision",
                    "decided_at": "2026-03-21T12:20:00Z",
                    "remediation_spec_id": "SPEC-AUD-WATCHDOG-001",
                    "remediation_record_path": "agents/.research_runtime/audit/remediation/watchdog-audit.json",
                    "pending_card_count": 0,
                    "backlog_card_count": 1,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    initial_report = evaluate_progress_watchdog(paths=paths)
    assert initial_report.status == "stalled"
    assert initial_report.escalation_action == "regenerate_task"
    assert initial_report.recovery_regeneration is not None
    assert initial_report.recovery_regeneration.status == "manual_only"
    assert initial_report.recovery_regeneration.family_policy_history_preserved is True

    family_policy_before = family_policy_file.read_text(encoding="utf-8")
    family_state_before = paths.goal_spec_family_state_file.read_text(encoding="utf-8")
    report = sync_progress_watchdog(paths=paths, allow_regeneration=True)

    assert report.status == "regenerated"
    assert report.reason == "durable-recovery-task-regenerated"
    assert report.visible_recovery_task_count == 1
    assert report.recovery_regeneration is not None
    assert report.recovery_regeneration.status == "regenerated"
    assert report.recovery_regeneration.regenerated_task_title == "Watchdog audit remediation task"
    assert report.recovery_regeneration.family_policy_history_preserved is True
    assert family_policy_file.read_text(encoding="utf-8") == family_policy_before
    assert paths.goal_spec_family_state_file.read_text(encoding="utf-8") == family_state_before
    assert paths.progress_watchdog_state_file.exists()
    assert paths.progress_watchdog_report_file.exists()

    backlog_cards = parse_task_store(backlog_file.read_text(encoding="utf-8"), source_file=backlog_file).cards
    assert ([card.title for card in backlog_cards], backlog_cards[0].spec_id) == (["Watchdog audit remediation task"], "SPEC-AUD-WATCHDOG-001")


def test_refresh_task_provenance_registry_bootstraps_and_tracks_visible_task_cards(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"; agents_dir = workspace / "agents"
    output_path = agents_dir / "task_provenance.json"
    tasks_path, backlog_path, archive_path = task_provenance_source_paths(agents_dir)
    agents_dir.mkdir(parents=True, exist_ok=True)

    tasks_path.write_text(
        "# Active Task\n\n"
        "## 2026-03-21 - Ship alpha\n"
        "- **Goal:** Finish alpha\n"
        "**Spec-ID:** SPEC-ALPHA\n",
        encoding="utf-8",
    )
    backlog_path.write_text(
        "# Task Backlog\n\n"
        "## 2026-03-21 - Prepare beta\n"
        "- **Goal:** Finish beta\n"
        "**Spec-ID:** SPEC-BETA\n\n"
        "## 2026-03-21 - Clean backlog\n"
        "- **Goal:** Misc cleanup\n",
        encoding="utf-8",
    )

    registry = refresh_task_provenance_registry(
        output_path,
        source_paths=(tasks_path, backlog_path, archive_path),
        relative_to=workspace,
        updated_at="2026-03-21T11:00:00Z",
    )

    assert output_path.exists()
    assert [(entry.source_file, entry.present, entry.card_count) for entry in registry.sources] == [
        ("agents/tasks.md", True, 1),
        ("agents/tasksbacklog.md", True, 2),
        ("agents/tasksarchive.md", False, 0),
    ]
    assert [(entry.source_file, entry.title, entry.spec_id) for entry in registry.task_cards] == [
        ("agents/tasks.md", "Ship alpha", "SPEC-ALPHA"),
        ("agents/tasksbacklog.md", "Prepare beta", "SPEC-BETA"),
        ("agents/tasksbacklog.md", "Clean backlog", ""),
    ]

    first_text = output_path.read_text(encoding="utf-8")
    refreshed = refresh_task_provenance_registry(
        output_path,
        source_paths=(tasks_path, backlog_path, archive_path),
        relative_to=workspace,
        updated_at="2026-03-21T11:00:00Z",
    )

    assert output_path.read_text(encoding="utf-8") == first_text
    assert load_task_provenance_registry(output_path) == refreshed


def test_refresh_task_provenance_registry_records_taskaudit_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"; agents_dir = workspace / "agents"
    output_path = agents_dir / "task_provenance.json"
    tasks_path, backlog_path, archive_path = task_provenance_source_paths(agents_dir)
    agents_dir.mkdir(parents=True, exist_ok=True)

    backlog_path.write_text(
        "# Task Backlog\n\n"
        "## 2026-03-21 - Merge alpha\n"
        "- **Goal:** Finalize alpha\n"
        "- **Spec-ID:** SPEC-ALPHA\n",
        encoding="utf-8",
    )

    registry = refresh_task_provenance_registry(
        output_path,
        source_paths=(tasks_path, backlog_path, archive_path),
        relative_to=workspace,
        updated_at="2026-03-21T12:00:00Z",
        taskaudit=TaskauditProvenance(
            record_path="agents/.research_runtime/goalspec/taskaudit/run-1.json",
            run_id="run-1",
            merged_at="2026-03-21T12:00:00Z",
            pending_path="agents/taskspending.md",
            pending_shards=("agents/taskspending/SPEC-ALPHA.md",),
            pending_card_count=1,
            merged_backlog_card_count=1,
            merged_spec_ids=("SPEC-ALPHA",),
            ordered_backlog_titles=("Merge alpha",),
        ),
    )

    assert registry.taskaudit is not None
    assert registry.taskaudit.record_path == "agents/.research_runtime/goalspec/taskaudit/run-1.json"
    assert registry.taskaudit.pending_shards == ("agents/taskspending/SPEC-ALPHA.md",)
    assert registry.taskaudit.merged_spec_ids == ("SPEC-ALPHA",)
    assert load_task_provenance_registry(output_path).taskaudit == registry.taskaudit


def test_stable_spec_metadata_from_file_reads_frontmatter_contract(tmp_path: Path) -> None:
    spec_file = tmp_path / "agents" / "specs" / "stable" / "golden" / "SPEC-ALPHA__alpha.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        "---\n"
        "spec_id: SPEC-ALPHA\n"
        "title: Alpha Goal\n"
        "decomposition_profile: moderate\n"
        "depends_on_specs: ['SPEC-BASE']\n"
        "---\n\n"
        "# Spec\n",
        encoding="utf-8",
    )

    metadata = stable_spec_metadata_from_file(spec_file, relative_to=tmp_path)

    assert metadata.spec_id == "SPEC-ALPHA"
    assert metadata.title == "Alpha Goal"
    assert metadata.decomposition_profile == "moderate"
    assert metadata.depends_on_specs == ("SPEC-BASE",)
    assert metadata.source_path == "agents/specs/stable/golden/SPEC-ALPHA__alpha.md"
