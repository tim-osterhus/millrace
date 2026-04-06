"""Control-plane helpers for manual interview visibility and mutations."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .control_common import ControlError, expected_error_message
from .control_models import (
    InterviewListReport,
    InterviewMutationReport,
    InterviewQuestionReport,
    InterviewQuestionSummary,
)
from .paths import RuntimePaths
from .research.interview import (
    InterviewError,
    accept_interview_question,
    answer_interview_question,
    create_manual_interview_question,
    find_interview_question,
    list_interview_questions,
    load_interview_decision_for_question,
    skip_interview_question,
)


def interview_list(config_path: Path, paths: RuntimePaths) -> InterviewListReport:
    """Return all persisted manual interview questions."""

    try:
        questions = list_interview_questions(paths)
    except (InterviewError, ValidationError, ValueError) as exc:
        raise ControlError(f"interview list failed: {expected_error_message(exc)}") from exc
    return InterviewListReport(
        config_path=config_path,
        questions=tuple(
            InterviewQuestionSummary(
                question_id=question.question_id,
                status=question.status,
                spec_id=question.spec_id,
                idea_id=question.idea_id,
                title=question.title,
                question=question.question,
                why_this_matters=question.why_this_matters,
                recommended_answer=question.recommended_answer,
                answer_source=question.answer_source,
                blocking=question.blocking,
                source_path=question.source_path,
                updated_at=question.updated_at,
            )
            for question in questions
        ),
    )


def interview_show(config_path: Path, paths: RuntimePaths, question_id: str) -> InterviewQuestionReport:
    """Return one persisted interview question plus any recorded decision."""

    try:
        question, question_path = find_interview_question(paths, question_id)
        decision, decision_path = load_interview_decision_for_question(paths, question)
    except (InterviewError, ValidationError, ValueError) as exc:
        raise ControlError(f"interview show failed: {expected_error_message(exc)}") from exc
    return InterviewQuestionReport(
        config_path=config_path,
        question_path=question_path,
        question=question,
        decision_path=decision_path,
        decision=decision,
    )


def interview_create(
    config_path: Path,
    paths: RuntimePaths,
    *,
    source_path: str | Path,
    question: str,
    why_this_matters: str,
    recommended_answer: str,
    answer_source: Literal["repo", "operator", "assumption"] = "assumption",
    blocking: bool = True,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationReport:
    """Create one pending interview question for a selected staged idea or spec."""

    try:
        result = create_manual_interview_question(
            paths,
            source_path=source_path,
            question=question,
            why_this_matters=why_this_matters,
            recommended_answer=recommended_answer,
            answer_source=answer_source,
            blocking=blocking,
            evidence=evidence,
        )
    except (InterviewError, ValidationError, ValueError) as exc:
        raise ControlError(f"interview create failed: {expected_error_message(exc)}") from exc
    return InterviewMutationReport(
        config_path=config_path,
        action="create",
        question_path=paths.root / result.question_path,
        question=result.question,
    )


def interview_answer(
    config_path: Path,
    paths: RuntimePaths,
    question_id: str,
    *,
    text: str,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationReport:
    """Resolve one pending interview question with an explicit operator answer."""

    try:
        result = answer_interview_question(paths, question_id=question_id, text=text, evidence=evidence)
    except (InterviewError, ValidationError, ValueError) as exc:
        raise ControlError(f"interview answer failed: {expected_error_message(exc)}") from exc
    return InterviewMutationReport(
        config_path=config_path,
        action=result.action,
        question_path=paths.root / result.question_path,
        question=result.question,
        decision_path=paths.root / result.decision_path,
        decision=result.decision,
    )


def interview_accept(
    config_path: Path,
    paths: RuntimePaths,
    question_id: str,
    *,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationReport:
    """Resolve one pending interview question by accepting its recommended answer."""

    try:
        result = accept_interview_question(paths, question_id=question_id, evidence=evidence)
    except (InterviewError, ValidationError, ValueError) as exc:
        raise ControlError(f"interview accept failed: {expected_error_message(exc)}") from exc
    return InterviewMutationReport(
        config_path=config_path,
        action=result.action,
        question_path=paths.root / result.question_path,
        question=result.question,
        decision_path=paths.root / result.decision_path,
        decision=result.decision,
    )


def interview_skip(
    config_path: Path,
    paths: RuntimePaths,
    question_id: str,
    *,
    reason: str | None = None,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationReport:
    """Resolve one pending interview question by skipping it with an assumption record."""

    try:
        result = skip_interview_question(paths, question_id=question_id, reason=reason, evidence=evidence)
    except (InterviewError, ValidationError, ValueError) as exc:
        raise ControlError(f"interview skip failed: {expected_error_message(exc)}") from exc
    return InterviewMutationReport(
        config_path=config_path,
        action=result.action,
        question_path=paths.root / result.question_path,
        question=result.question,
        decision_path=paths.root / result.decision_path,
        decision=result.decision,
    )
