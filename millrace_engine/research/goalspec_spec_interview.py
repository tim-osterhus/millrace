"""GoalSpec spec-interview stage executor."""

from __future__ import annotations

from datetime import datetime

from ..contracts import SpecInterviewPolicy
from ..paths import RuntimePaths
from .goalspec import (
    SpecInterviewExecutionResult,
    SpecInterviewRecord,
)
from .goalspec_helpers import (
    GoalSpecExecutionError,
    _relative_path,
    _resolve_path_token,
    _spec_id_for_goal,
    _utcnow,
    _write_json_model,
    resolve_goal_source,
)
from .interview import (
    accept_interview_question,
    create_manual_interview_question,
    list_interview_questions,
)
from .specs import load_goal_spec_family_state
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership


_INTERVIEW_AMBIGUITY_MARKERS = ("tbd", "todo", "???", "needs decision")


def _first_spec_interview_ambiguity(text: str) -> str | None:
    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        lowered = line.casefold()
        if any(marker in lowered for marker in _INTERVIEW_AMBIGUITY_MARKERS):
            return line
    return None


def _latest_question_for_spec(paths: RuntimePaths, *, spec_id: str):
    questions = [question for question in list_interview_questions(paths) if question.spec_id == spec_id]
    pending = next((question for question in questions if question.status == "pending"), None)
    resolved = next(
        (
            question
            for question in reversed(questions)
            if question.status != "pending" and question.decision_path
        ),
        None,
    )
    return pending, resolved


def execute_spec_interview(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    policy: SpecInterviewPolicy,
    emitted_at: datetime | None = None,
) -> SpecInterviewExecutionResult:
    """Resolve one optional GoalSpec interview turn for the active synthesized spec."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    spec_id = source.frontmatter.get("spec_id", "").strip() or _spec_id_for_goal(source.idea_id)
    spec_state = family_state.specs.get(spec_id)
    if spec_state is None:
        raise GoalSpecExecutionError(f"GoalSpec family state is missing {spec_id} during Spec Interview")

    queue_spec_path = _resolve_path_token(spec_state.queue_path or source.relative_source_path, relative_to=paths.root)
    queue_spec_text = queue_spec_path.read_text(encoding="utf-8", errors="replace")
    record_path = paths.goalspec_spec_interview_records_dir / f"{run_id}.json"
    queue_ownership = ResearchQueueOwnership(
        family=ResearchQueueFamily.GOALSPEC,
        queue_path=paths.ideas_specs_dir,
        item_path=queue_spec_path,
        owner_token=run_id,
        acquired_at=emitted_at,
    )

    ambiguity = _first_spec_interview_ambiguity(queue_spec_text)
    should_run = policy is SpecInterviewPolicy.ALWAYS or (
        policy is SpecInterviewPolicy.WHEN_AMBIGUOUS and ambiguity is not None
    )
    if policy in {SpecInterviewPolicy.OFF, SpecInterviewPolicy.MANUAL_ONLY} or not should_run:
        return SpecInterviewExecutionResult(queue_ownership=queue_ownership)

    pending_question, resolved_question = _latest_question_for_spec(paths, spec_id=spec_id)
    if pending_question is not None:
        record = SpecInterviewRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            spec_id=spec_id,
            title=source.title,
            source_path=_relative_path(queue_spec_path, relative_to=paths.root),
            question_path=_relative_path(
                paths.specs_questions_dir / f"{pending_question.question_id}.json",
                relative_to=paths.root,
            ),
            policy=policy,
            resolution="waiting_for_operator",
            blocking=True,
        )
        _write_json_model(record_path, record)
        return SpecInterviewExecutionResult(
            record_path=_relative_path(record_path, relative_to=paths.root),
            question_path=record.question_path,
            blocked=True,
            queue_ownership=queue_ownership,
        )

    if resolved_question is not None:
        resolution = "repo_answered" if resolved_question.answer_source == "repo" else "operator_resolved"
        record = SpecInterviewRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            spec_id=spec_id,
            title=source.title,
            source_path=_relative_path(queue_spec_path, relative_to=paths.root),
            question_path=_relative_path(
                paths.specs_questions_dir / f"{resolved_question.question_id}.json",
                relative_to=paths.root,
            ),
            decision_path=resolved_question.decision_path,
            policy=policy,
            resolution=resolution,
            blocking=False,
        )
        _write_json_model(record_path, record)
        return SpecInterviewExecutionResult(
            record_path=_relative_path(record_path, relative_to=paths.root),
            question_path=record.question_path,
            decision_path=record.decision_path,
            queue_ownership=queue_ownership,
        )

    if ambiguity is not None:
        created = create_manual_interview_question(
            paths,
            source_path=queue_spec_path,
            question=f"How should this ambiguity be resolved for {spec_id}?",
            why_this_matters=ambiguity,
            recommended_answer="Document the intended choice explicitly before Spec Review resumes.",
            answer_source="assumption",
            blocking=True,
            evidence=(_relative_path(queue_spec_path, relative_to=paths.root), ambiguity),
        )
        record = SpecInterviewRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            spec_id=spec_id,
            title=source.title,
            source_path=_relative_path(queue_spec_path, relative_to=paths.root),
            question_path=created.question_path,
            policy=policy,
            resolution="waiting_for_operator",
            blocking=True,
        )
        _write_json_model(record_path, record)
        return SpecInterviewExecutionResult(
            record_path=_relative_path(record_path, relative_to=paths.root),
            question_path=created.question_path,
            blocked=True,
            queue_ownership=queue_ownership,
        )

    accepted = accept_interview_question(
        paths,
        question_id=create_manual_interview_question(
            paths,
            source_path=queue_spec_path,
            question=f"Does {spec_id} preserve the required upstream GoalSpec traceability?",
            why_this_matters="Spec Review should inherit explicit links to the completion manifest and objective profile inputs.",
            recommended_answer="Yes. The synthesized spec already references the staged GoalSpec inputs needed for downstream review.",
            answer_source="repo",
            blocking=False,
            evidence=(
                _relative_path(queue_spec_path, relative_to=paths.root),
                "agents/audit/completion_manifest.json",
                "agents/reports/acceptance_profiles",
            ),
        ).question.question_id,
        evidence=(
            _relative_path(queue_spec_path, relative_to=paths.root),
            "agents/audit/completion_manifest.json",
            "agents/reports/acceptance_profiles",
        ),
    )
    record = SpecInterviewRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        spec_id=spec_id,
        title=source.title,
        source_path=_relative_path(queue_spec_path, relative_to=paths.root),
        question_path=accepted.question_path,
        decision_path=accepted.decision_path,
        policy=policy,
        resolution="repo_answered",
        blocking=False,
    )
    _write_json_model(record_path, record)
    return SpecInterviewExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        question_path=accepted.question_path,
        decision_path=accepted.decision_path,
        queue_ownership=queue_ownership,
    )
