"""GoalSpec artifact/state persistence helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..paths import RuntimePaths
from .goalspec import (
    AcceptanceProfileRecord,
    CompletionManifestDraftArtifact,
    CompletionManifestDraftStateRecord,
    GoalSource,
    ObjectiveProfileSyncStateRecord,
)
from .goalspec_helpers import (
    GoalSpecExecutionError,
    _load_json_object,
    _relative_path,
    _resolve_path_token,
    _slugify,
)
from .governance import (
    evaluate_initial_family_plan_guard,
    resolve_family_governor_state,
)
from .specs import (
    GoalSpecDecompositionProfile,
    GoalSpecFamilySpecState,
    GoalSpecFamilyState,
    GoalSpecLineageRecord,
    build_initial_family_plan_snapshot,
    load_goal_spec_family_state,
    write_goal_spec_family_state,
)


def _load_objective_profile_inputs(
    paths: RuntimePaths,
) -> tuple[ObjectiveProfileSyncStateRecord, AcceptanceProfileRecord]:
    if not paths.objective_profile_sync_state_file.exists():
        raise GoalSpecExecutionError(
            "Objective Profile Sync state is missing; GoalSpec spec synthesis cannot proceed"
        )
    state = ObjectiveProfileSyncStateRecord.model_validate(
        _load_json_object(paths.objective_profile_sync_state_file)
    )
    profile_path = _resolve_path_token(state.profile_path, relative_to=paths.root)
    if not profile_path.exists():
        raise GoalSpecExecutionError(
            f"Objective profile JSON is missing: {profile_path.as_posix()}"
        )
    profile = AcceptanceProfileRecord.model_validate(_load_json_object(profile_path))
    return state, profile


def _build_completion_manifest_draft_state(
    *,
    emitted_at: datetime,
    run_id: str,
    source: GoalSource,
    objective_state: ObjectiveProfileSyncStateRecord,
    profile: AcceptanceProfileRecord,
    spec_id: str,
    paths: RuntimePaths,
) -> CompletionManifestDraftStateRecord:
    slug = _slugify(source.title)
    queue_spec_path = paths.ideas_specs_dir / f"{spec_id}__{slug}.md"
    golden_spec_path = paths.specs_stable_golden_dir / f"{spec_id}__{slug}.md"
    phase_spec_path = paths.specs_stable_phase_dir / f"{spec_id}__phase-01.md"
    decision_path = paths.specs_decisions_dir / f"{Path(source.source_path).stem}__spec-synthesis.md"
    open_questions = profile.hard_blockers or (
        "Spec Review and task generation remain downstream after this draft synthesis pass.",
    )
    return CompletionManifestDraftStateRecord(
        draft_id=f"{_slugify(source.idea_id)}-completion-manifest",
        goal_id=source.idea_id,
        title=source.title,
        run_id=run_id,
        updated_at=emitted_at,
        source_path=source.relative_source_path,
        research_brief_path=source.relative_source_path,
        objective_profile_state_path=_relative_path(paths.objective_profile_sync_state_file, relative_to=paths.root),
        objective_profile_path=objective_state.profile_path,
        completion_manifest_plan_path=_relative_path(paths.completion_manifest_plan_file, relative_to=paths.root),
        goal_intake_record_path=objective_state.goal_intake_record_path,
        acceptance_focus=profile.milestones,
        open_questions=open_questions,
        required_outputs=(
            CompletionManifestDraftArtifact(
                artifact_kind="queue_spec",
                path=_relative_path(queue_spec_path, relative_to=paths.root),
                purpose="Primary draft spec candidate for downstream review.",
            ),
            CompletionManifestDraftArtifact(
                artifact_kind="stable_golden_spec",
                path=_relative_path(golden_spec_path, relative_to=paths.root),
                purpose="Stable copy of the emitted draft spec.",
            ),
            CompletionManifestDraftArtifact(
                artifact_kind="stable_phase_spec",
                path=_relative_path(phase_spec_path, relative_to=paths.root),
                purpose="Bounded phase plan aligned to the emitted draft spec.",
            ),
            CompletionManifestDraftArtifact(
                artifact_kind="synthesis_record",
                path=_relative_path(decision_path, relative_to=paths.root),
                purpose="Critic/designer/clarifier synthesis summary for traceability.",
            ),
        ),
    )


def _build_goal_spec_family_state(
    *,
    paths: RuntimePaths,
    source: GoalSource,
    spec_id: str,
    title: str,
    decomposition_profile: GoalSpecDecompositionProfile,
    queue_spec_path: Path,
    emitted_at: datetime,
    planned_specs: tuple[GoalSpecFamilySpecState, ...] = (),
    planned_spec_ids: tuple[str, ...] = (),
    family_complete: bool = True,
) -> GoalSpecFamilyState:
    current_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    policy_payload = (
        _load_json_object(paths.objective_family_policy_file)
        if paths.objective_family_policy_file.exists()
        else {}
    )
    next_state = current_state
    if next_state.goal_id and next_state.goal_id != source.idea_id and not next_state.family_complete:
        raise GoalSpecExecutionError(
            "GoalSpec family state is still active for another goal; refusing to overwrite incomplete family state"
        )
    if not next_state.goal_id or next_state.goal_id != source.idea_id:
        next_state = GoalSpecFamilyState(
            goal_id=source.idea_id,
            source_idea_path=source.relative_source_path,
            family_phase="initial_family",
            family_complete=True,
            active_spec_id="",
            spec_order=(),
            specs={},
            family_governor=resolve_family_governor_state(
                paths=paths,
                current_state=current_state,
                policy_payload=policy_payload,
            ),
        )
    resolved_governor = resolve_family_governor_state(
        paths=paths,
        current_state=next_state,
        policy_payload=policy_payload,
    )
    next_state = next_state.model_copy(update={"family_governor": resolved_governor})

    spec_state = GoalSpecFamilySpecState(
        status="emitted",
        title=title,
        decomposition_profile=decomposition_profile,
        queue_path=_relative_path(queue_spec_path, relative_to=paths.root),
    )
    specs = dict(next_state.specs)
    specs[spec_id] = spec_state
    spec_order = next_state.spec_order or (spec_id,)
    if spec_id not in spec_order:
        spec_order = spec_order + (spec_id,)
    for planned_spec_id, planned_spec in zip(planned_spec_ids, planned_specs):
        specs[planned_spec_id] = planned_spec
        if planned_spec_id not in spec_order:
            spec_order = spec_order + (planned_spec_id,)
    guard_decision = evaluate_initial_family_plan_guard(
        current_state=next_state,
        candidate_spec_id=spec_id,
        proposed_spec_order=spec_order,
        proposed_specs=specs,
    )
    if guard_decision.action == "block":
        raise GoalSpecExecutionError(
            f"GoalSpec family governance blocked {spec_id}: {guard_decision.reason}"
        )

    next_state = next_state.model_copy(
        update={
            "goal_id": source.idea_id,
            "source_idea_path": source.relative_source_path,
            "family_phase": "initial_family",
            "family_complete": family_complete,
            "active_spec_id": spec_id,
            "spec_order": spec_order,
            "specs": specs,
            "family_governor": resolved_governor,
            "updated_at": emitted_at,
        }
    )
    if next_state.initial_family_plan is None and guard_decision.action == "freeze":
        next_state = next_state.model_copy(
            update={
                "initial_family_plan": build_initial_family_plan_snapshot(
                    next_state,
                    repo_root=paths.root,
                    trigger_spec_id=spec_id,
                    goal_file=_resolve_path_token(source.source_path, relative_to=paths.root),
                    policy_path=paths.objective_family_policy_file,
                    policy_payload=policy_payload,
                    frozen_at=emitted_at,
                )
            }
        )
    return next_state


def _updated_goal_spec_family_state(
    *,
    paths: RuntimePaths,
    source: GoalSource,
    spec_id: str,
    title: str,
    decomposition_profile: GoalSpecDecompositionProfile,
    queue_spec_path: Path,
    emitted_at: datetime,
    planned_specs: tuple[GoalSpecFamilySpecState, ...] = (),
    planned_spec_ids: tuple[str, ...] = (),
    family_complete: bool = True,
) -> GoalSpecFamilyState:
    next_state = _build_goal_spec_family_state(
        paths=paths,
        source=source,
        spec_id=spec_id,
        title=title,
        decomposition_profile=decomposition_profile,
        queue_spec_path=queue_spec_path,
        emitted_at=emitted_at,
        planned_specs=planned_specs,
        planned_spec_ids=planned_spec_ids,
        family_complete=family_complete,
    )
    return write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        next_state,
        updated_at=emitted_at,
    )


def _stable_spec_paths_for_review(paths: RuntimePaths, *, spec_id: str) -> tuple[Path, ...]:
    candidates = sorted(
        (
            path
            for path in paths.specs_stable_dir.rglob("*.md")
            if path.name.startswith(f"{spec_id}__") and ".frozen" not in path.parts
        ),
        key=lambda path: path.as_posix(),
    )
    if not candidates:
        raise GoalSpecExecutionError(f"Stable spec copies are missing for {spec_id}")
    return tuple(candidates)


def _build_goal_spec_review_state(
    *,
    paths: RuntimePaths,
    spec_id: str,
    goal_id: str,
    queue_spec_path: Path,
    reviewed_path: Path,
    questions_path: Path,
    decision_path: Path,
    stable_spec_paths: tuple[Path, ...],
    review_status: str,
    emitted_at: datetime,
) -> tuple[GoalSpecFamilyState, GoalSpecLineageRecord]:
    current_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    spec_state = current_state.specs.get(spec_id)
    if spec_state is None:
        raise GoalSpecExecutionError(f"GoalSpec family state is missing {spec_id} during Spec Review")

    updated_spec_state = spec_state.model_copy(
        update={
            "status": "reviewed",
            "review_status": review_status,
            "queue_path": spec_state.queue_path or _relative_path(queue_spec_path, relative_to=paths.root),
            "reviewed_path": _relative_path(reviewed_path, relative_to=paths.root),
            "stable_spec_paths": tuple(_relative_path(path, relative_to=paths.root) for path in stable_spec_paths),
            "review_questions_path": _relative_path(questions_path, relative_to=paths.root),
            "review_decision_path": _relative_path(decision_path, relative_to=paths.root),
        }
    )
    next_specs = dict(current_state.specs)
    next_specs[spec_id] = updated_spec_state
    next_state = current_state.model_copy(
        update={
            "active_spec_id": spec_id,
            "specs": next_specs,
            "updated_at": emitted_at,
        }
    )
    return next_state, updated_spec_state.lineage(
        spec_id=spec_id,
        goal_id=goal_id,
        source_idea_path=next_state.source_idea_path,
    )
