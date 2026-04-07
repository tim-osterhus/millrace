"""GoalSpec spec-review stage executor."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .goalspec import SpecReviewExecutionResult
from .goalspec_helpers import (
    GoalSpecExecutionError,
    _load_json_model,
    _load_json_object,
    _relative_path,
    _resolve_path_token,
    _spec_id_for_goal,
    _utcnow,
    _write_json_model,
    resolve_goal_source,
)
from .goalspec_persistence import (
    _build_goal_spec_review_state,
    _stable_spec_paths_for_review,
)
from .goalspec_stage_rendering import (
    render_spec_review_decision,
    render_spec_review_questions,
)
from .specs import (
    GoalSpecLineageRecord,
    GoalSpecReviewRecord,
    load_goal_spec_family_state,
    load_stable_spec_registry,
    refresh_stable_spec_registry,
    write_goal_spec_family_state,
)
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership


def execute_spec_review(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> SpecReviewExecutionResult:
    """Promote one synthesized queue spec into reviewed state plus lineage/registry artifacts."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    spec_id = source.frontmatter.get("spec_id", "").strip() or _spec_id_for_goal(source.idea_id)
    spec_state = family_state.specs.get(spec_id)
    if spec_state is None:
        raise GoalSpecExecutionError(f"GoalSpec family state is missing {spec_id} during Spec Review")

    queue_spec_path = _resolve_path_token(spec_state.queue_path or source.relative_source_path, relative_to=paths.root)
    reviewed_path = paths.ideas_specs_reviewed_dir / Path(spec_state.queue_path or source.relative_source_path).name
    source_slug = Path(spec_state.queue_path or source.relative_source_path).stem
    questions_path = paths.specs_questions_dir / f"{source_slug}__spec-review.md"
    decision_path = paths.specs_decisions_dir / f"{source_slug}__spec-review.md"
    record_path = paths.goalspec_spec_review_records_dir / f"{run_id}.json"
    lineage_path = paths.goalspec_lineage_dir / f"{spec_id}.json"
    stable_spec_paths = _stable_spec_paths_for_review(paths, spec_id=spec_id)
    relative_stable_spec_paths = tuple(_relative_path(path, relative_to=paths.root) for path in stable_spec_paths)
    review_status = "no_material_delta"
    review_timestamp = emitted_at
    queue_spec_text = _resolve_path_token(source.source_path, relative_to=paths.root).read_text(
        encoding="utf-8",
        errors="replace",
    )

    if (
        record_path.exists()
        and questions_path.exists()
        and decision_path.exists()
        and reviewed_path.exists()
        and lineage_path.exists()
        and paths.specs_index_file.exists()
    ):
        existing_review_record = _load_json_model(record_path, GoalSpecReviewRecord)
        if existing_review_record.reviewed_at is not None:
            review_timestamp = existing_review_record.reviewed_at
        expected_questions = render_spec_review_questions(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            stable_spec_paths=relative_stable_spec_paths,
        )
        expected_decision = render_spec_review_decision(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            review_status=review_status,
            reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
            stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        )
        expected_family_state, lineage_record = _build_goal_spec_review_state(
            paths=paths,
            spec_id=spec_id,
            goal_id=source.idea_id,
            queue_spec_path=queue_spec_path,
            reviewed_path=reviewed_path,
            questions_path=questions_path,
            decision_path=decision_path,
            stable_spec_paths=stable_spec_paths,
            review_status=review_status,
            emitted_at=review_timestamp,
        )
        current_family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
        stable_registry = load_stable_spec_registry(paths.specs_index_file)
        if (
            existing_review_record
            == GoalSpecReviewRecord(
                spec_id=spec_id,
                review_status=review_status,
                questions_path=_relative_path(questions_path, relative_to=paths.root),
                decision_path=_relative_path(decision_path, relative_to=paths.root),
                reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
                reviewed_at=review_timestamp,
                findings=(),
            )
            and GoalSpecLineageRecord.model_validate(_load_json_object(lineage_path)) == lineage_record
            and current_family_state == expected_family_state
            and questions_path.read_text(encoding="utf-8") == expected_questions
            and decision_path.read_text(encoding="utf-8") == expected_decision
            and reviewed_path.read_text(encoding="utf-8") == queue_spec_text
            and {entry.spec_path for entry in stable_registry.stable_specs} >= set(relative_stable_spec_paths)
        ):
            return SpecReviewExecutionResult(
                record_path=_relative_path(record_path, relative_to=paths.root),
                questions_path=_relative_path(questions_path, relative_to=paths.root),
                decision_path=_relative_path(decision_path, relative_to=paths.root),
                reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
                lineage_path=_relative_path(lineage_path, relative_to=paths.root),
                stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
                family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
                queue_ownership=ResearchQueueOwnership(
                    family=ResearchQueueFamily.GOALSPEC,
                    queue_path=paths.ideas_specs_reviewed_dir,
                    item_path=reviewed_path,
                    owner_token=run_id,
                    acquired_at=review_timestamp,
                ),
            )

    questions_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    reviewed_path.parent.mkdir(parents=True, exist_ok=True)

    write_text_atomic(
        questions_path,
        render_spec_review_questions(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            stable_spec_paths=relative_stable_spec_paths,
        ),
    )
    write_text_atomic(
        decision_path,
        render_spec_review_decision(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            review_status=review_status,
            reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
            stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        ),
    )
    write_text_atomic(reviewed_path, queue_spec_text)
    source_path = _resolve_path_token(source.source_path, relative_to=paths.root)
    if source_path != reviewed_path and source_path.exists():
        source_path.unlink()

    next_family_state, lineage_record = _build_goal_spec_review_state(
        paths=paths,
        spec_id=spec_id,
        goal_id=source.idea_id,
        queue_spec_path=queue_spec_path,
        reviewed_path=reviewed_path,
        questions_path=questions_path,
        decision_path=decision_path,
        stable_spec_paths=stable_spec_paths,
        review_status=review_status,
        emitted_at=review_timestamp,
    )
    write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        next_family_state,
        updated_at=review_timestamp,
    )
    _write_json_model(lineage_path, lineage_record)
    _write_json_model(
        record_path,
        GoalSpecReviewRecord(
            spec_id=spec_id,
            review_status=review_status,
            questions_path=_relative_path(questions_path, relative_to=paths.root),
            decision_path=_relative_path(decision_path, relative_to=paths.root),
            reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
            reviewed_at=review_timestamp,
            findings=(),
        ),
    )
    refresh_stable_spec_registry(
        paths.specs_stable_dir,
        paths.specs_stable_dir / ".frozen",
        paths.specs_index_file,
        relative_to=paths.root,
        updated_at=review_timestamp,
    )

    return SpecReviewExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        questions_path=_relative_path(questions_path, relative_to=paths.root),
        decision_path=_relative_path(decision_path, relative_to=paths.root),
        reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        queue_ownership=ResearchQueueOwnership(
            family=ResearchQueueFamily.GOALSPEC,
            queue_path=paths.ideas_specs_reviewed_dir,
            item_path=reviewed_path,
            owner_token=run_id,
            acquired_at=review_timestamp,
        ),
    )
