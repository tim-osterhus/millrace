"""GoalSpec spec-synthesis stage executor."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .goalspec import (
    CompletionManifestDraftStateRecord,
    SpecSynthesisExecutionResult,
    SpecSynthesisRecord,
)
from .goalspec_helpers import (
    GoalSpecExecutionError,
    _load_json_model,
    _load_json_object,
    _relative_path,
    _spec_id_for_goal,
    _slugify,
    _utcnow,
    _write_json_model,
    resolve_goal_source,
)
from .goalspec_persistence import (
    _build_goal_spec_family_state,
    _load_objective_profile_inputs,
    _updated_goal_spec_family_state,
)
from .goalspec_stage_rendering import (
    render_phase_spec,
    render_queue_spec,
    render_synthesis_decision_record,
)
from .governance import (
    build_reused_spec_synthesis_family_state,
    evaluate_spec_synthesis_idempotency,
)
from .specs import load_goal_spec_family_state
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership


def execute_spec_synthesis(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    completion_manifest: CompletionManifestDraftStateRecord | None = None,
    emitted_at: datetime | None = None,
) -> SpecSynthesisExecutionResult:
    """Emit the draft GoalSpec package and update family-state persistence."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    objective_state, profile = _load_objective_profile_inputs(paths)
    if completion_manifest is None:
        if not paths.audit_completion_manifest_file.exists():
            raise GoalSpecExecutionError(
                "Completion manifest draft is missing; Spec Synthesis cannot proceed"
            )
        completion_manifest = CompletionManifestDraftStateRecord.model_validate(
            _load_json_object(paths.audit_completion_manifest_file)
        )

    spec_id = _spec_id_for_goal(source.idea_id)
    slug = _slugify(source.title)
    queue_spec_path = paths.ideas_specs_dir / f"{spec_id}__{slug}.md"
    golden_spec_path = paths.specs_stable_golden_dir / f"{spec_id}__{slug}.md"
    phase_spec_path = paths.specs_stable_phase_dir / f"{spec_id}__phase-01.md"
    decision_path = paths.specs_decisions_dir / f"{Path(source.source_path).stem}__spec-synthesis.md"
    record_path = paths.goalspec_spec_synthesis_records_dir / f"{run_id}.json"
    completion_manifest_path = _relative_path(paths.audit_completion_manifest_file, relative_to=paths.root)

    queue_spec_text = render_queue_spec(
        emitted_at=emitted_at,
        source=source,
        spec_id=spec_id,
        objective_state=objective_state,
        profile=profile,
        completion_manifest=completion_manifest,
        completion_manifest_path=completion_manifest_path,
    )
    phase_spec_text = render_phase_spec(
        emitted_at=emitted_at,
        spec_id=spec_id,
        title=source.title,
        completion_manifest_path=completion_manifest_path,
        objective_profile_path=objective_state.profile_path,
    )
    expected_family_state = _build_goal_spec_family_state(
        paths=paths,
        source=source,
        spec_id=spec_id,
        title=source.title,
        decomposition_profile=source.decomposition_profile,
        queue_spec_path=queue_spec_path,
        emitted_at=emitted_at,
    )
    decision_text = render_synthesis_decision_record(
        emitted_at=emitted_at,
        run_id=run_id,
        source=source,
        spec_id=spec_id,
        profile=profile,
        completion_manifest=completion_manifest,
        completion_manifest_path=completion_manifest_path,
        objective_profile_path=objective_state.profile_path,
        queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
        family_complete=expected_family_state.family_complete,
    )
    if (
        record_path.exists()
        and queue_spec_path.exists()
        and golden_spec_path.exists()
        and phase_spec_path.exists()
        and decision_path.exists()
        and paths.goal_spec_family_state_file.exists()
    ):
        existing_record = _load_json_model(record_path, SpecSynthesisRecord)
        existing_family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
        reused_emitted_at = existing_record.emitted_at
        expected_record = SpecSynthesisRecord(
            run_id=run_id,
            emitted_at=reused_emitted_at,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            source_path=source.relative_source_path,
            research_brief_path=source.relative_source_path,
            objective_profile_path=objective_state.profile_path,
            completion_manifest_path=completion_manifest_path,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            golden_spec_path=_relative_path(golden_spec_path, relative_to=paths.root),
            phase_spec_path=_relative_path(phase_spec_path, relative_to=paths.root),
            decision_path=_relative_path(decision_path, relative_to=paths.root),
            family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        )
        expected_queue_spec = render_queue_spec(
            emitted_at=reused_emitted_at,
            source=source,
            spec_id=spec_id,
            objective_state=objective_state,
            profile=profile,
            completion_manifest=completion_manifest,
            completion_manifest_path=completion_manifest_path,
        )
        expected_phase_spec = render_phase_spec(
            emitted_at=reused_emitted_at,
            spec_id=spec_id,
            title=source.title,
            completion_manifest_path=completion_manifest_path,
            objective_profile_path=objective_state.profile_path,
        )
        expected_decision = render_synthesis_decision_record(
            emitted_at=reused_emitted_at,
            run_id=run_id,
            source=source,
            spec_id=spec_id,
            profile=profile,
            completion_manifest=completion_manifest,
            completion_manifest_path=completion_manifest_path,
            objective_profile_path=objective_state.profile_path,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            family_complete=expected_family_state.family_complete,
        )
        expected_reused_family_state = build_reused_spec_synthesis_family_state(
            expected_family_state=expected_family_state,
            existing_family_state=existing_family_state,
        )
        idempotency_decision = evaluate_spec_synthesis_idempotency(
            existing_record=existing_record,
            expected_record=expected_record,
            existing_family_state=existing_family_state,
            expected_family_state=expected_reused_family_state,
            actual_queue_spec_text=queue_spec_path.read_text(encoding="utf-8"),
            actual_golden_spec_text=golden_spec_path.read_text(encoding="utf-8"),
            actual_phase_spec_text=phase_spec_path.read_text(encoding="utf-8"),
            actual_decision_text=decision_path.read_text(encoding="utf-8"),
            expected_queue_spec_text=expected_queue_spec,
            expected_phase_spec_text=expected_phase_spec,
            expected_decision_text=expected_decision,
        )
        if idempotency_decision.action == "reuse":
            return SpecSynthesisExecutionResult(
                record_path=_relative_path(record_path, relative_to=paths.root),
                queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
                golden_spec_path=_relative_path(golden_spec_path, relative_to=paths.root),
                phase_spec_path=_relative_path(phase_spec_path, relative_to=paths.root),
                decision_path=_relative_path(decision_path, relative_to=paths.root),
                family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
                queue_ownership=ResearchQueueOwnership(
                    family=ResearchQueueFamily.GOALSPEC,
                    queue_path=paths.ideas_specs_dir,
                    item_path=queue_spec_path,
                    owner_token=run_id,
                    acquired_at=reused_emitted_at,
                ),
            )

    queue_spec_path.parent.mkdir(parents=True, exist_ok=True)
    golden_spec_path.parent.mkdir(parents=True, exist_ok=True)
    phase_spec_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(queue_spec_path, queue_spec_text)
    write_text_atomic(golden_spec_path, queue_spec_text)
    write_text_atomic(phase_spec_path, phase_spec_text)

    _updated_goal_spec_family_state(
        paths=paths,
        source=source,
        spec_id=spec_id,
        title=source.title,
        decomposition_profile=source.decomposition_profile,
        queue_spec_path=queue_spec_path,
        emitted_at=emitted_at,
    )
    write_text_atomic(decision_path, decision_text)

    record = SpecSynthesisRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        goal_id=source.idea_id,
        spec_id=spec_id,
        title=source.title,
        source_path=source.relative_source_path,
        research_brief_path=source.relative_source_path,
        objective_profile_path=objective_state.profile_path,
        completion_manifest_path=completion_manifest_path,
        queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
        golden_spec_path=_relative_path(golden_spec_path, relative_to=paths.root),
        phase_spec_path=_relative_path(phase_spec_path, relative_to=paths.root),
        decision_path=_relative_path(decision_path, relative_to=paths.root),
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
    )
    _write_json_model(record_path, record)
    return SpecSynthesisExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
        golden_spec_path=_relative_path(golden_spec_path, relative_to=paths.root),
        phase_spec_path=_relative_path(phase_spec_path, relative_to=paths.root),
        decision_path=_relative_path(decision_path, relative_to=paths.root),
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        queue_ownership=ResearchQueueOwnership(
            family=ResearchQueueFamily.GOALSPEC,
            queue_path=paths.ideas_specs_dir,
            item_path=queue_spec_path,
            owner_token=run_id,
            acquired_at=emitted_at,
        ),
    )
