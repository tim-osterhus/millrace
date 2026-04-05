from __future__ import annotations

from pathlib import Path
import json

import pytest

from millrace_engine.research.interview import (
    InterviewError,
    answer_interview_question,
    accept_interview_question,
    create_manual_interview_question,
    list_interview_questions,
    skip_interview_question,
)
from tests.support import runtime_paths, runtime_workspace


def write_staged_idea(
    workspace: Path,
    *,
    idea_id: str = "IDEA-321",
    slug: str = "manual-interview",
    title: str = "Manual interview source",
) -> Path:
    path = workspace / "agents" / "ideas" / "staging" / f"{idea_id}__{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"idea_id: {idea_id}",
                f"title: {title}",
                "---",
                "",
                f"# {title}",
                "",
                "A staged idea for manual interview testing.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def write_queue_spec(
    workspace: Path,
    *,
    spec_id: str = "SPEC-654",
    idea_id: str = "IDEA-654",
    slug: str = "queue-spec",
    title: str = "Queue spec source",
) -> Path:
    path = workspace / "agents" / "ideas" / "specs" / f"{spec_id}__{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"spec_id: {spec_id}",
                f"idea_id: {idea_id}",
                f"title: {title}",
                "---",
                "",
                f"# {title}",
                "",
                "A synthesized queue spec for manual interview testing.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_create_manual_interview_question_persists_pending_question_and_blocks_duplicate_pending(tmp_path: Path) -> None:
    workspace, config_path = runtime_workspace(tmp_path)
    source_path = write_staged_idea(workspace, idea_id="IDEA-777", slug="duplicate-pending")
    paths = runtime_paths(config_path)

    result = create_manual_interview_question(
        paths,
        source_path=source_path,
        question="What is the rollback story?",
        why_this_matters="Release safety depends on it.",
        recommended_answer="Use feature flags and a documented revert path.",
        answer_source="assumption",
        evidence=["agents/ideas/staging/IDEA-777__duplicate-pending.md"],
    )

    artifact_path = workspace / result.question_path
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["question_id"] == "SPEC-777__interview-001"
    assert payload["spec_id"] == "SPEC-777"
    assert payload["idea_id"] == "IDEA-777"
    assert payload["status"] == "pending"
    assert payload["source_kind"] == "idea"
    assert payload["source_path"] == "agents/ideas/staging/IDEA-777__duplicate-pending.md"

    with pytest.raises(InterviewError, match="pending interview question already exists"):
        create_manual_interview_question(
            paths,
            source_path=source_path,
            question="What is the fallback transport?",
            why_this_matters="The runtime needs one fallback.",
            recommended_answer="Use the existing local mailbox path.",
        )


def test_answer_interview_question_updates_question_and_writes_decision(tmp_path: Path) -> None:
    workspace, config_path = runtime_workspace(tmp_path)
    source_path = write_queue_spec(workspace, spec_id="SPEC-900", idea_id="IDEA-900", slug="answer-flow")
    paths = runtime_paths(config_path)
    create_result = create_manual_interview_question(
        paths,
        source_path=source_path,
        question="How is operator override persisted?",
        why_this_matters="Resume behavior depends on it.",
        recommended_answer="Persist overrides in the workspace contract.",
        answer_source="repo",
    )

    result = answer_interview_question(
        paths,
        question_id=create_result.question.question_id,
        text="Persist the override in a versioned workspace artifact.",
        evidence=["agents/objective/contract.yaml"],
    )

    question_payload = json.loads((workspace / result.question_path).read_text(encoding="utf-8"))
    decision_payload = json.loads((workspace / result.decision_path).read_text(encoding="utf-8"))
    assert question_payload["status"] == "answered"
    assert question_payload["answer_source"] == "operator"
    assert question_payload["decision_path"] == result.decision_path
    assert decision_payload["decision_source"] == "operator"
    assert decision_payload["decision"] == "Persist the override in a versioned workspace artifact."


def test_accept_and_skip_interview_question_emit_expected_decision_sources(tmp_path: Path) -> None:
    workspace, config_path = runtime_workspace(tmp_path)
    first_source = write_queue_spec(workspace, spec_id="SPEC-901", idea_id="IDEA-901", slug="accept-flow")
    second_source = write_queue_spec(workspace, spec_id="SPEC-902", idea_id="IDEA-902", slug="skip-flow")
    paths = runtime_paths(config_path)

    accepted_question = create_manual_interview_question(
        paths,
        source_path=first_source,
        question="Should repo evidence answer this directly?",
        why_this_matters="It determines whether the later stage should pause.",
        recommended_answer="Yes, the existing control API already exposes the needed state.",
        answer_source="repo",
    ).question
    skipped_question = create_manual_interview_question(
        paths,
        source_path=second_source,
        question="Which naming convention should marketing prefer?",
        why_this_matters="It needs operator judgment.",
        recommended_answer="Use the current product name.",
    ).question

    accepted = accept_interview_question(paths, question_id=accepted_question.question_id)
    skipped = skip_interview_question(
        paths,
        question_id=skipped_question.question_id,
        reason="Deferred to operator naming review.",
    )

    accepted_decision = json.loads((workspace / accepted.decision_path).read_text(encoding="utf-8"))
    skipped_decision = json.loads((workspace / skipped.decision_path).read_text(encoding="utf-8"))
    assert accepted_decision["decision_source"] == "accepted_recommendation"
    assert accepted_decision["decision"] == "Yes, the existing control API already exposes the needed state."
    assert skipped_decision["decision_source"] == "assumption"
    assert skipped_decision["decision"] == "Deferred to operator naming review."

    questions = list_interview_questions(paths)
    assert [question.status for question in questions] == ["accepted", "skipped"]
