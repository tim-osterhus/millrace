"""Manual GoalSpec interview artifact contracts and file-backed helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import field_validator

from ..contracts import ContractModel, _normalize_datetime
from ..paths import RuntimePaths
from .goalspec_helpers import (
    _first_heading,
    _load_json_model,
    _relative_path,
    _slugify,
    _spec_id_for_goal,
    _split_frontmatter,
    _utcnow,
    _write_json_model,
)
from .normalization_helpers import (
    _normalize_optional_text,
    _normalize_required_text,
    _normalize_text_sequence,
)

INTERVIEW_ARTIFACT_SCHEMA_VERSION = "1.0"
InterviewQuestionStatus = Literal["pending", "answered", "accepted", "skipped"]
InterviewAnswerSource = Literal["repo", "operator", "assumption"]
InterviewDecisionSource = Literal["repo", "operator", "accepted_recommendation", "assumption"]
InterviewSourceKind = Literal["idea", "spec"]


class InterviewError(ValueError):
    """Raised when manual interview operations cannot complete safely."""


class InterviewSource(ContractModel):
    """Resolved staged idea or synthesized spec selected for manual interview."""

    source_kind: InterviewSourceKind
    source_path: str
    relative_source_path: str
    title: str
    spec_id: str
    idea_id: str = ""

    @field_validator("source_path", "relative_source_path", "title", "spec_id", "idea_id", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if isinstance(value, Path):
            value = value.as_posix()
        if field_name == "idea_id":
            return _normalize_optional_text(value if isinstance(value, str) or value is None else str(value))
        return _normalize_required_text(str(value or ""), field_name=field_name)


class InterviewQuestionRecord(ContractModel):
    """Durable one-question-at-a-time interview prompt for one spec under review."""

    schema_version: Literal["1.0"] = INTERVIEW_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["spec_interview_question"] = "spec_interview_question"
    question_id: str
    spec_id: str
    idea_id: str = ""
    status: InterviewQuestionStatus = "pending"
    source_kind: InterviewSourceKind
    source_path: str
    title: str
    question: str
    why_this_matters: str
    recommended_answer: str
    answer_source: InterviewAnswerSource = "assumption"
    blocking: bool = True
    evidence: tuple[str, ...] = ()
    decision_path: str = ""
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None

    @field_validator("created_at", "updated_at", "resolved_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value: datetime | str | None) -> datetime | None:
        if value in (None, ""):
            return None
        return _normalize_datetime(value)

    @field_validator(
        "question_id",
        "spec_id",
        "source_path",
        "title",
        "question",
        "why_this_matters",
        "recommended_answer",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if isinstance(value, Path):
            value = value.as_posix()
        return _normalize_required_text(str(value or ""), field_name=field_name)

    @field_validator("idea_id", "decision_path", mode="before")
    @classmethod
    def normalize_optional_fields(cls, value: str | Path | None) -> str:
        if isinstance(value, Path):
            value = value.as_posix()
        return _normalize_optional_text(value if isinstance(value, str) or value is None else str(value))

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        return _normalize_text_sequence(value)


class InterviewDecisionRecord(ContractModel):
    """Durable resolution artifact emitted when a manual interview question is resolved."""

    schema_version: Literal["1.0"] = INTERVIEW_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["spec_interview_decision"] = "spec_interview_decision"
    decision_id: str
    question_id: str
    spec_id: str
    idea_id: str = ""
    decision: str
    decision_source: InterviewDecisionSource
    evidence: tuple[str, ...] = ()
    resolved_at: datetime

    @field_validator("resolved_at", mode="before")
    @classmethod
    def normalize_resolved_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("decision_id", "question_id", "spec_id", "decision", mode="before")
    @classmethod
    def validate_required_text(cls, value: str | Path | None, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if isinstance(value, Path):
            value = value.as_posix()
        return _normalize_required_text(str(value or ""), field_name=field_name)

    @field_validator("idea_id", mode="before")
    @classmethod
    def normalize_idea_id(cls, value: str | Path | None) -> str:
        if isinstance(value, Path):
            value = value.as_posix()
        return _normalize_optional_text(value if isinstance(value, str) or value is None else str(value))

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        return _normalize_text_sequence(value)


class InterviewCreateResult(ContractModel):
    """Resolved outputs from materializing one manual interview question."""

    source: InterviewSource
    question: InterviewQuestionRecord
    question_path: str


class InterviewMutationResult(ContractModel):
    """Resolved outputs from answering, accepting, or skipping one question."""

    action: Literal["answer", "accept", "skip"]
    question: InterviewQuestionRecord
    question_path: str
    decision: InterviewDecisionRecord
    decision_path: str


def _question_path_for(paths: RuntimePaths, question_id: str) -> Path:
    return paths.specs_questions_dir / f"{question_id}.json"


def _decision_path_for(paths: RuntimePaths, decision_id: str) -> Path:
    return paths.specs_decisions_dir / f"{decision_id}.json"


def _resolve_source_path(paths: RuntimePaths, source_path: str | Path) -> Path:
    candidate = Path(source_path).expanduser()
    if not candidate.is_absolute():
        candidate = (paths.root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.exists() or not candidate.is_file():
        raise InterviewError(f"interview source does not exist: {candidate.as_posix()}")
    return candidate


def resolve_interview_source(paths: RuntimePaths, source_path: str | Path) -> InterviewSource:
    """Resolve one staged idea or synthesized spec selected for manual interview."""

    resolved_path = _resolve_source_path(paths, source_path)
    text = resolved_path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = _split_frontmatter(text)
    stem_token = resolved_path.stem.split("__", 1)[0].strip()
    idea_id = (
        frontmatter.get("idea_id")
        or frontmatter.get("goal_id")
        or (stem_token if stem_token.upper().startswith("IDEA-") else "")
    )
    spec_id = (
        frontmatter.get("spec_id")
        or (stem_token if stem_token.upper().startswith("SPEC-") else "")
        or (_spec_id_for_goal(idea_id) if idea_id else "")
    )
    if not spec_id:
        raise InterviewError(
            f"interview source must provide spec_id or idea_id frontmatter: {resolved_path.as_posix()}"
        )
    title = frontmatter.get("title") or _first_heading(body) or resolved_path.stem
    source_kind: InterviewSourceKind = "spec" if (frontmatter.get("spec_id") or stem_token.upper().startswith("SPEC-")) else "idea"
    return InterviewSource(
        source_kind=source_kind,
        source_path=resolved_path.as_posix(),
        relative_source_path=_relative_path(resolved_path, relative_to=paths.root),
        title=title,
        spec_id=spec_id,
        idea_id=idea_id,
    )


def load_interview_question(path: Path) -> InterviewQuestionRecord:
    """Load one JSON interview question artifact."""

    return _load_json_model(path, InterviewQuestionRecord)  # type: ignore[return-value]


def load_interview_decision(path: Path) -> InterviewDecisionRecord:
    """Load one JSON interview decision artifact."""

    return _load_json_model(path, InterviewDecisionRecord)  # type: ignore[return-value]


def list_interview_questions(paths: RuntimePaths) -> tuple[InterviewQuestionRecord, ...]:
    """Return all persisted manual interview questions sorted by creation time."""

    if not paths.specs_questions_dir.exists():
        return ()
    questions = [load_interview_question(path) for path in sorted(paths.specs_questions_dir.glob("*.json"))]
    return tuple(sorted(questions, key=lambda record: (record.created_at, record.question_id)))


def _find_pending_for_spec(
    questions: tuple[InterviewQuestionRecord, ...],
    *,
    spec_id: str,
) -> InterviewQuestionRecord | None:
    for question in questions:
        if question.spec_id == spec_id and question.status == "pending":
            return question
    return None


def _next_question_id(questions: tuple[InterviewQuestionRecord, ...], *, spec_id: str) -> str:
    existing = sum(1 for question in questions if question.spec_id == spec_id)
    return f"{spec_id}__interview-{existing + 1:03d}"


def create_manual_interview_question(
    paths: RuntimePaths,
    *,
    source_path: str | Path,
    question: str,
    why_this_matters: str,
    recommended_answer: str,
    answer_source: InterviewAnswerSource = "assumption",
    blocking: bool = True,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewCreateResult:
    """Materialize one pending interview question for a selected staged idea or spec."""

    source = resolve_interview_source(paths, source_path)
    existing = list_interview_questions(paths)
    pending = _find_pending_for_spec(existing, spec_id=source.spec_id)
    if pending is not None:
        raise InterviewError(f"pending interview question already exists for {source.spec_id}: {pending.question_id}")
    now = _utcnow()
    question_id = _next_question_id(existing, spec_id=source.spec_id)
    record = InterviewQuestionRecord(
        question_id=question_id,
        spec_id=source.spec_id,
        idea_id=source.idea_id,
        source_kind=source.source_kind,
        source_path=source.relative_source_path,
        title=source.title,
        question=question,
        why_this_matters=why_this_matters,
        recommended_answer=recommended_answer,
        answer_source=answer_source,
        blocking=blocking,
        evidence=_normalize_text_sequence(evidence),
        created_at=now,
        updated_at=now,
    )
    question_path = _question_path_for(paths, question_id)
    _write_json_model(question_path, record)
    return InterviewCreateResult(
        source=source,
        question=record,
        question_path=_relative_path(question_path, relative_to=paths.root),
    )


def find_interview_question(paths: RuntimePaths, question_id: str) -> tuple[InterviewQuestionRecord, Path]:
    """Load one persisted interview question by question id."""

    normalized = _normalize_required_text(question_id, field_name="question_id")
    path = _question_path_for(paths, normalized)
    if not path.exists():
        raise InterviewError(f"interview question not found: {normalized}")
    return load_interview_question(path), path


def load_interview_decision_for_question(
    paths: RuntimePaths,
    question: InterviewQuestionRecord,
) -> tuple[InterviewDecisionRecord | None, Path | None]:
    """Return the current decision artifact for one question, if present."""

    if not question.decision_path:
        return None, None
    path = _resolve_source_path(paths, question.decision_path)
    return load_interview_decision(path), path


def _update_question(
    paths: RuntimePaths,
    question_path: Path,
    question: InterviewQuestionRecord,
    *,
    status: InterviewQuestionStatus,
    answer_source: InterviewAnswerSource,
    decision_path: Path,
    resolved_at: datetime,
) -> InterviewQuestionRecord:
    updated = question.model_copy(
        update={
            "status": status,
            "answer_source": answer_source,
            "decision_path": _relative_path(decision_path, relative_to=paths.root),
            "updated_at": resolved_at,
            "resolved_at": resolved_at,
        }
    )
    _write_json_model(question_path, updated)
    return updated


def _resolve_pending_question(paths: RuntimePaths, question_id: str) -> tuple[InterviewQuestionRecord, Path]:
    question, question_path = find_interview_question(paths, question_id)
    if question.status != "pending":
        raise InterviewError(f"interview question is already resolved: {question.question_id}")
    return question, question_path


def answer_interview_question(
    paths: RuntimePaths,
    *,
    question_id: str,
    text: str,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationResult:
    """Resolve one pending interview question with an explicit operator answer."""

    question, question_path = _resolve_pending_question(paths, question_id)
    resolved_at = _utcnow()
    decision = InterviewDecisionRecord(
        decision_id=f"{question.question_id}__decision",
        question_id=question.question_id,
        spec_id=question.spec_id,
        idea_id=question.idea_id,
        decision=text,
        decision_source="operator",
        evidence=_normalize_text_sequence(evidence),
        resolved_at=resolved_at,
    )
    decision_path = _decision_path_for(paths, decision.decision_id)
    _write_json_model(decision_path, decision)
    updated_question = _update_question(
        paths,
        question_path,
        question,
        status="answered",
        answer_source="operator",
        decision_path=decision_path,
        resolved_at=resolved_at,
    )
    return InterviewMutationResult(
        action="answer",
        question=updated_question,
        question_path=_relative_path(question_path, relative_to=paths.root),
        decision=decision,
        decision_path=_relative_path(decision_path, relative_to=paths.root),
    )


def accept_interview_question(
    paths: RuntimePaths,
    *,
    question_id: str,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationResult:
    """Resolve one pending interview question by accepting its recommended answer."""

    question, question_path = _resolve_pending_question(paths, question_id)
    resolved_at = _utcnow()
    decision = InterviewDecisionRecord(
        decision_id=f"{question.question_id}__decision",
        question_id=question.question_id,
        spec_id=question.spec_id,
        idea_id=question.idea_id,
        decision=question.recommended_answer,
        decision_source="accepted_recommendation",
        evidence=_normalize_text_sequence(evidence) or question.evidence,
        resolved_at=resolved_at,
    )
    decision_path = _decision_path_for(paths, decision.decision_id)
    _write_json_model(decision_path, decision)
    updated_question = _update_question(
        paths,
        question_path,
        question,
        status="accepted",
        answer_source=question.answer_source,
        decision_path=decision_path,
        resolved_at=resolved_at,
    )
    return InterviewMutationResult(
        action="accept",
        question=updated_question,
        question_path=_relative_path(question_path, relative_to=paths.root),
        decision=decision,
        decision_path=_relative_path(decision_path, relative_to=paths.root),
    )


def skip_interview_question(
    paths: RuntimePaths,
    *,
    question_id: str,
    reason: str | None = None,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationResult:
    """Resolve one pending interview question by recording a skipped/assumed outcome."""

    question, question_path = _resolve_pending_question(paths, question_id)
    resolved_at = _utcnow()
    decision = InterviewDecisionRecord(
        decision_id=f"{question.question_id}__decision",
        question_id=question.question_id,
        spec_id=question.spec_id,
        idea_id=question.idea_id,
        decision=_normalize_optional_text(reason) or "Question skipped by operator.",
        decision_source="assumption",
        evidence=_normalize_text_sequence(evidence),
        resolved_at=resolved_at,
    )
    decision_path = _decision_path_for(paths, decision.decision_id)
    _write_json_model(decision_path, decision)
    updated_question = _update_question(
        paths,
        question_path,
        question,
        status="skipped",
        answer_source="assumption",
        decision_path=decision_path,
        resolved_at=resolved_at,
    )
    return InterviewMutationResult(
        action="skip",
        question=updated_question,
        question_path=_relative_path(question_path, relative_to=paths.root),
        decision=decision,
        decision_path=_relative_path(decision_path, relative_to=paths.root),
    )


__all__ = [
    "INTERVIEW_ARTIFACT_SCHEMA_VERSION",
    "InterviewAnswerSource",
    "InterviewCreateResult",
    "InterviewDecisionRecord",
    "InterviewDecisionSource",
    "InterviewError",
    "InterviewMutationResult",
    "InterviewQuestionRecord",
    "InterviewQuestionStatus",
    "InterviewSource",
    "InterviewSourceKind",
    "accept_interview_question",
    "answer_interview_question",
    "create_manual_interview_question",
    "find_interview_question",
    "list_interview_questions",
    "load_interview_decision",
    "load_interview_decision_for_question",
    "load_interview_question",
    "resolve_interview_source",
    "skip_interview_question",
]
