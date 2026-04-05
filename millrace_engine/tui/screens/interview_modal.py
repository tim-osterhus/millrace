"""Focused modal workflow for resolving one pending interview question."""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

from ..models import InterviewQuestionSummaryView


@dataclass(frozen=True, slots=True)
class InterviewResolutionRequest:
    """Normalized interview-resolution payload returned to the shell."""

    action: str
    question_id: str
    answer_text: str | None = None
    skip_reason: str | None = None


class InterviewModal(ModalScreen[InterviewResolutionRequest | None]):
    """Collect one operator decision for a pending interview question."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+enter", "submit_answer", "Answer"),
    ]

    def __init__(self, *, question: InterviewQuestionSummaryView) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog modal-form"):
            yield Static("Resolve Interview Question", classes="modal-title")
            yield Static(self._copy_text(), classes="modal-copy")
            yield Static("Answer", classes="modal-label")
            yield TextArea(
                "",
                id="interview-answer-text",
                classes="modal-textarea",
                placeholder="Write the operator answer that should be recorded as the decision.",
                show_line_numbers=False,
            )
            yield Static("Skip Reason (optional)", classes="modal-label")
            yield Input(
                placeholder="Record why this question is being skipped or deferred.",
                id="interview-skip-reason",
            )
            yield Static("", id="interview-error", classes="modal-error")
            with Horizontal(classes="modal-actions"):
                yield Button("Cancel", id="interview-cancel")
                yield Button("Skip", id="interview-skip")
                yield Button("Accept Recommendation", id="interview-accept")
                yield Button("Record Answer", id="interview-answer", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#interview-answer-text", TextArea).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit_answer(self) -> None:
        answer = self.query_one("#interview-answer-text", TextArea).text.strip()
        if not answer:
            self._set_error("Answer text is required to record an operator answer.")
            self.query_one("#interview-answer-text", TextArea).focus()
            return
        self.dismiss(
            InterviewResolutionRequest(
                action="answer",
                question_id=self._question.question_id,
                answer_text=answer,
            )
        )

    def action_accept_recommendation(self) -> None:
        self.dismiss(
            InterviewResolutionRequest(
                action="accept",
                question_id=self._question.question_id,
            )
        )

    def action_skip_question(self) -> None:
        reason = self.query_one("#interview-skip-reason", Input).value.strip() or None
        self.dismiss(
            InterviewResolutionRequest(
                action="skip",
                question_id=self._question.question_id,
                skip_reason=reason,
            )
        )

    def _copy_text(self) -> str:
        lines = [
            f"Question ID: {self._question.question_id}",
            f"Spec ID: {self._question.spec_id}",
        ]
        if self._question.idea_id:
            lines.append(f"Idea ID: {self._question.idea_id}")
        lines.extend(
            [
                f"Title: {self._question.title}",
                f"Blocking: {'yes' if self._question.blocking else 'no'}",
                f"Source: {self._question.source_path}",
                f"Question: {self._question.question}",
                f"Why this matters: {self._question.why_this_matters}",
                f"Recommended answer: {self._question.recommended_answer}",
                f"Current answer source: {self._question.answer_source}",
            ]
        )
        return "\n".join(lines)

    @on(Button.Pressed, "#interview-cancel")
    def _handle_cancel_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    @on(Button.Pressed, "#interview-answer")
    def _handle_answer_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_submit_answer()

    @on(Button.Pressed, "#interview-accept")
    def _handle_accept_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_accept_recommendation()

    @on(Button.Pressed, "#interview-skip")
    def _handle_skip_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_skip_question()

    @on(Input.Changed, "#interview-skip-reason")
    @on(TextArea.Changed, "#interview-answer-text")
    def _handle_input_changed(self, _: Input.Changed | TextArea.Changed) -> None:
        self._clear_error()

    def _set_error(self, message: str) -> None:
        self.query_one("#interview-error", Static).update(message)

    def _clear_error(self) -> None:
        self._set_error("")


__all__ = ["InterviewModal", "InterviewResolutionRequest"]
