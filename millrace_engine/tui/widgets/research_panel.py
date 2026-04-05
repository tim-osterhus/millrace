"""Research visibility panel for the Millrace TUI shell."""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Static

from ..formatting import compact_run_label, format_short_timestamp, format_timestamp
from ..models import (
    DisplayMode,
    GatewayFailure,
    InterviewQuestionSummaryView,
    ResearchOverviewView,
    ResearchQueueFamilyView,
)
from .progressive_disclosure import append_panel_failure_lines, collapse_operator_text


def _none_label(value: str | None) -> str:
    normalized = " ".join((value or "").split())
    return normalized or "none"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _family_line(family: ResearchQueueFamilyView) -> str:
    owner = family.queue_owner or "none"
    first_item = family.first_item
    fragments = [
        f"{family.family}",
        f"ready {_yes_no(family.ready)}",
        f"items {family.item_count}",
        f"owner {owner}",
    ]
    if first_item is not None:
        fragments.append(f"first {first_item.title}")
        fragments.append(f"kind {first_item.item_kind}")
        if first_item.stage_blocked:
            fragments.append(f"blocked {first_item.stage_blocked}")
    return " | ".join(fragments)


def _family_operator_line(family: ResearchQueueFamilyView) -> str:
    readiness = "ready" if family.ready else "hold"
    blocked = family.first_item.stage_blocked if family.first_item is not None else None
    fragments = [f"{family.family}", readiness, f"items {family.item_count}"]
    if blocked:
        fragments.append(f"blocked {blocked}")
    return " | ".join(fragments)


def _selected_label(selected: bool) -> str:
    return "focus" if selected else "open"


def _interview_status(question: InterviewQuestionSummaryView) -> str:
    return "blocking" if question.blocking else "non-blocking"


def _interview_card(question: InterviewQuestionSummaryView, *, selected: bool) -> Vertical:
    classes = "overview-card panel-item-card"
    if selected:
        classes += " is-selected"
    return Vertical(
        Static(question.title, classes="panel-item-title"),
        Static(
            " | ".join(
                (
                    question.spec_id,
                    _interview_status(question),
                    f"updated {format_short_timestamp(question.updated_at)}",
                )
            ),
            classes="panel-item-meta",
        ),
        Static(question.question, classes="panel-item-alert"),
        classes=classes,
    )


def _interview_operator_line(question: InterviewQuestionSummaryView, *, selected: bool) -> str:
    return (
        f"{'>' if selected else '-'} {question.title}"
        f" | {question.spec_id}"
        f" | {_selected_label(selected)}"
        f" | {_interview_status(question)}"
    )


def _governance_alert_fragments(research: ResearchOverviewView) -> list[str]:
    governance = research.governance
    if governance is None:
        return []
    alerts: list[str] = []
    if governance.drift_status in {"warning", "drifted", "fail"}:
        alerts.append(f"drift {governance.drift_status}")
    if governance.canary_status in {"drifted", "failed"}:
        alerts.append(f"canary {governance.canary_status}")
    if governance.recovery_status in {"stalled", "blocked", "failed"}:
        alerts.append(f"recovery {governance.recovery_status}")
    if governance.recovery_regeneration_status in {"manual_only", "failed"}:
        alerts.append(f"regen {governance.recovery_regeneration_status}")
    return alerts


class ResearchPanel(Static):
    """Compact research-plane report with audit and governance summaries."""

    can_focus = True
    BINDINGS = (
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("home", "cursor_home", show=False),
        Binding("end", "cursor_end", show=False),
        Binding("o", "open_interview", show=False),
        Binding("enter", "open_interview", show=False),
    )

    class InterviewRequested(Message):
        """Posted when the operator wants to resolve the selected interview question."""

        bubble = True

        def __init__(self, question: InterviewQuestionSummaryView) -> None:
            super().__init__()
            self.question = question

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, classes="panel-card", markup=False)
        self.border_title = "Research"
        self._research: ResearchOverviewView | None = None
        self._failure: GatewayFailure | None = None
        self._display_mode: DisplayMode = DisplayMode.OPERATOR
        self._selected_question_id: str | None = None

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="research-operator", id="research-mode-switcher"):
            with Vertical(id="research-operator", classes="panel-mode-body"):
                yield self._section_card("research-status", "Status")
                with Horizontal(classes="panel-metrics"):
                    yield self._metric_card("research-mode", "Mode")
                    yield self._metric_card("research-families", "Families")
                    yield self._metric_card("research-deferred", "Deferred")
                yield self._section_card("research-interview", "Interview")
                yield self._section_card("research-audit", "Audit")
                yield self._section_card("research-activity", "Latest activity")
                with Vertical(
                    id="research-interview-list-card",
                    classes="overview-card panel-section-card panel-list-card",
                ):
                    yield Static("Pending interview", classes="overview-card-label")
                    yield Static("--", id="research-interview-list-headline", classes="overview-card-headline")
                    yield Static("", id="research-interview-list-detail", classes="overview-card-detail")
                    yield Vertical(id="research-interview-items", classes="panel-item-stack")
                with Vertical(id="research-families-card", classes="overview-card panel-section-card panel-list-card"):
                    yield Static("Families", classes="overview-card-label")
                    yield Static("--", id="research-families-headline", classes="overview-card-headline")
                    yield Static("", id="research-families-detail", classes="overview-card-detail")
                    yield Vertical(id="research-families-items", classes="panel-item-stack")
                yield self._section_card("research-warnings", "Attention")
            yield Static("", id="research-debug", classes="panel-debug-body")

    @staticmethod
    def _metric_card(suffix: str, title: str) -> Vertical:
        return Vertical(
            Static(title, classes="overview-card-label"),
            Static("--", id=f"{suffix}-value", classes="overview-card-value"),
            Static("", id=f"{suffix}-meta", classes="overview-card-meta"),
            classes="overview-card panel-summary-card",
            id=f"{suffix}-card",
        )

    @staticmethod
    def _section_card(suffix: str, title: str) -> Vertical:
        return Vertical(
            Static(title, classes="overview-card-label"),
            Static("--", id=f"{suffix}-headline", classes="overview-card-headline"),
            Static("", id=f"{suffix}-detail", classes="overview-card-detail"),
            classes="overview-card panel-section-card",
            id=f"{suffix}-card",
        )

    @staticmethod
    def _family_card(family: ResearchQueueFamilyView) -> Vertical:
        first_item = family.first_item
        detail_fragments = [f"items {family.item_count}", "ready" if family.ready else "hold"]
        if family.queue_owner:
            detail_fragments.append(f"owner {family.queue_owner}")
        if first_item is not None:
            detail_fragments.append(first_item.item_kind)
            if first_item.stage_blocked:
                detail_fragments.append(f"blocked {first_item.stage_blocked}")
        classes = "overview-card panel-item-card"
        classes += " state-ok" if family.ready else " state-warn"
        children: list[Widget] = [
            Static(family.family, classes="panel-item-title"),
            Static(" | ".join(detail_fragments), classes="panel-item-meta"),
        ]
        if first_item is not None:
            children.append(Static(first_item.title, classes="panel-item-alert"))
        return Vertical(*children, classes=classes)

    def on_mount(self) -> None:
        self._render_state()

    def show_snapshot(
        self,
        research: ResearchOverviewView | None,
        *,
        failure: GatewayFailure | None = None,
        display_mode: DisplayMode = DisplayMode.OPERATOR,
    ) -> None:
        self._research = research
        self._failure = failure
        self._display_mode = display_mode
        self._reconcile_selection()
        if self.is_mounted:
            self._render_state()

    def summary_text(self) -> str:
        if self._display_mode is DisplayMode.DEBUG:
            return self._render_debug_text()
        return self._render_operator_text()

    def _render_state(self) -> None:
        switcher = self.query_one("#research-mode-switcher", ContentSwitcher)
        switcher.current = "research-debug" if self._display_mode is DisplayMode.DEBUG else "research-operator"
        self.query_one("#research-debug", Static).update(self._render_debug_text())
        if self._display_mode is DisplayMode.DEBUG:
            return
        self._render_operator_cards()

    def _render_operator_cards(self) -> None:
        if self._research is None:
            self._update_section("research-status", "Waiting for the research snapshot.", self._failure_operator_detail(has_snapshot=False))
            self._update_metric("research-mode", "--", "mode unavailable")
            self._update_metric("research-families", "--", "family counts unavailable")
            self._update_metric("research-deferred", "--", "deferred counts unavailable")
            self._update_section("research-interview", "Interview state unavailable", "research snapshot not loaded")
            self._update_section("research-audit", "Audit summary unavailable", "research snapshot not loaded")
            self._update_section("research-activity", "No research activity", "waiting for research events")
            self._set_interview_items(headline="No pending interview questions", detail="research snapshot not loaded", items=())
            self._set_family_items(headline="No families visible", detail="research queue snapshot not loaded", items=())
            self._update_section("research-warnings", "Open debug for deeper detail", "audit and governance detail stay in debug mode")
            return

        research = self._research
        pending_questions = self._pending_questions(research)
        blocking_questions = sum(1 for question in pending_questions if question.blocking)
        total_families = len(research.queue_families)
        ready_families = sum(1 for family in research.queue_families if family.ready)
        total_items = sum(family.item_count for family in research.queue_families)
        blocked_items = sum(
            1
            for family in research.queue_families
            if family.first_item is not None and family.first_item.stage_blocked
        )

        self._update_section(
            "research-status",
            research.status,
            self._failure_operator_detail(has_snapshot=True)
            if self._failure is not None
            else f"mode {research.current_mode} | selected {_none_label(research.selected_family)}",
        )
        self._update_metric("research-mode", research.current_mode, f"last {research.last_mode} | configured {research.configured_mode}")
        self._update_metric("research-families", f"{ready_families}/{total_families}", f"{total_items} items visible")
        self._update_metric(
            "research-deferred",
            str(research.deferred_request_count),
            f"breadcrumbs {research.deferred_breadcrumb_count}",
        )
        if pending_questions:
            selected = self._selected_question(research) or pending_questions[0]
            interview_detail = f"{selected.spec_id} | {_interview_status(selected)}"
            if selected.updated_at is not None:
                interview_detail = f"{interview_detail} | updated {format_short_timestamp(selected.updated_at)}"
            self._update_section(
                "research-interview",
                f"{len(pending_questions)} pending | {blocking_questions} blocking",
                interview_detail,
            )
            self._set_interview_items(
                headline=f"{len(pending_questions)} pending interview question{'s' if len(pending_questions) != 1 else ''}",
                detail="Up/Down select | Enter opens answer, accept, and skip actions",
                items=tuple(
                    _interview_card(question, selected=(question.question_id == self._selected_question_id))
                    for question in pending_questions[:3]
                ),
            )
        else:
            self._update_section("research-interview", "No pending interview questions", "research is not waiting on operator interview input")
            self._set_interview_items(
                headline="No pending interview questions",
                detail="interview actions appear here when research pauses for operator input",
                items=(),
            )
        self._update_section(
            "research-audit",
            "allowed" if research.completion_allowed else "blocked",
            f"reason {research.completion_reason} | gate {_none_label(research.latest_gate_decision)}",
        )

        last_activity = self._last_meaningful_activity(research)
        if last_activity is None:
            self._update_section("research-activity", "No recent research activity", "switch to debug for audit and governance detail")
        else:
            run_fragment = f" | run {compact_run_label(last_activity.run_id)}" if last_activity.run_id else ""
            self._update_section(
                "research-activity",
                f"{format_short_timestamp(last_activity.timestamp)} | {last_activity.event_type}",
                f"{last_activity.summary}{run_fragment}",
            )

        if total_families:
            items = tuple(self._family_card(family) for family in research.queue_families[:5])
            detail = f"{total_items} queued items | selected {_none_label(research.selected_family)}"
            if total_families > 5:
                detail = f"{detail} | +{total_families - 5} more families"
            self._set_family_items(headline=f"{ready_families}/{total_families} families ready", detail=detail, items=items)
        else:
            self._set_family_items(headline="No research queue families discovered", detail="research queue is idle", items=())

        warnings = self._operator_warnings(research, blocked_items=blocked_items)
        if warnings:
            detail = " | ".join(warnings[:3])
            if pending_questions:
                detail = f"interview pending | {detail}"
            self._update_section("research-warnings", f"{len(warnings)} operator attention items", detail)
        else:
            detail = "open debug for audit summary, governance, and recent events"
            if pending_questions:
                detail = "open the selected interview question to answer, accept, or skip it"
            self._update_section("research-warnings", "No operator attention needed", detail)

    def action_cursor_up(self) -> None:
        self._move_selection(-1)

    def action_cursor_down(self) -> None:
        self._move_selection(1)

    def action_cursor_home(self) -> None:
        self._select_index(0)

    def action_cursor_end(self) -> None:
        pending = self._pending_questions(self._research)
        if pending:
            self._select_index(len(pending) - 1)

    def action_open_interview(self) -> None:
        question = self._selected_question(self._research)
        if question is None:
            return
        self.post_message(self.InterviewRequested(question))

    def _update_metric(self, suffix: str, value: str, meta: str) -> None:
        self.query_one(f"#{suffix}-value", Static).update(value)
        self.query_one(f"#{suffix}-meta", Static).update(meta)

    def _update_section(self, suffix: str, headline: str, detail: str) -> None:
        self.query_one(f"#{suffix}-headline", Static).update(headline)
        self.query_one(f"#{suffix}-detail", Static).update(detail)

    def _set_family_items(self, *, headline: str, detail: str, items: tuple[Widget, ...]) -> None:
        self.query_one("#research-families-headline", Static).update(headline)
        self.query_one("#research-families-detail", Static).update(detail)
        container = self.query_one("#research-families-items", Vertical)
        container.remove_children()
        if items:
            for item in items:
                container.mount(item)
        else:
            container.mount(
                Vertical(
                    Static("No family queue heads", classes="panel-item-title"),
                    Static(detail, classes="panel-item-meta"),
                    classes="overview-card panel-item-card panel-empty-card",
                )
            )

    def _set_interview_items(self, *, headline: str, detail: str, items: tuple[Widget, ...]) -> None:
        self.query_one("#research-interview-list-headline", Static).update(headline)
        self.query_one("#research-interview-list-detail", Static).update(detail)
        container = self.query_one("#research-interview-items", Vertical)
        container.remove_children()
        if items:
            for item in items:
                container.mount(item)
            return
        container.mount(
            Vertical(
                Static("No pending interview questions", classes="panel-item-title"),
                Static(detail, classes="panel-item-meta"),
                classes="overview-card panel-item-card panel-empty-card",
            )
        )

    @staticmethod
    def _pending_questions(research: ResearchOverviewView | None) -> tuple[InterviewQuestionSummaryView, ...]:
        if research is None:
            return ()
        return tuple(question for question in research.interview_questions if question.status == "pending")

    def _selected_question(self, research: ResearchOverviewView | None) -> InterviewQuestionSummaryView | None:
        pending = self._pending_questions(research)
        if not pending:
            return None
        if self._selected_question_id is None:
            return pending[0]
        for question in pending:
            if question.question_id == self._selected_question_id:
                return question
        return pending[0]

    def _reconcile_selection(self) -> None:
        selected = self._selected_question(self._research)
        self._selected_question_id = None if selected is None else selected.question_id

    def _select_index(self, index: int) -> None:
        pending = self._pending_questions(self._research)
        if not pending:
            self._selected_question_id = None
            if self.is_mounted:
                self._render_state()
            return
        normalized = min(max(index, 0), len(pending) - 1)
        self._selected_question_id = pending[normalized].question_id
        if self.is_mounted:
            self._render_state()

    def _move_selection(self, delta: int) -> None:
        pending = self._pending_questions(self._research)
        if not pending:
            return
        selected = self._selected_question(self._research)
        if selected is None:
            self._selected_question_id = pending[0].question_id
        else:
            current_index = next(
                (index for index, question in enumerate(pending) if question.question_id == selected.question_id),
                0,
            )
            self._selected_question_id = pending[min(max(current_index + delta, 0), len(pending) - 1)].question_id
        if self.is_mounted:
            self._render_state()

    def _failure_operator_detail(self, *, has_snapshot: bool) -> str:
        if self._failure is None:
            return ""
        qualifier = "showing last known snapshot" if has_snapshot else "no snapshot available"
        return f"{qualifier} | {collapse_operator_text(self._failure.message)} | open debug for technical detail"

    def _render_text(self) -> str:
        if self._display_mode is DisplayMode.DEBUG:
            return self._render_debug_text()
        return self._render_operator_text()

    def _render_operator_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="RESEARCH",
            failure=self._failure,
            has_snapshot=self._research is not None,
            display_mode=DisplayMode.OPERATOR,
        )
        if self._research is None:
            lines.append("Waiting for the research snapshot.")
            return "\n".join(lines)

        research = self._research
        pending_questions = self._pending_questions(research)
        total_families = len(research.queue_families)
        ready_families = sum(1 for family in research.queue_families if family.ready)
        total_items = sum(family.item_count for family in research.queue_families)
        blocked_items = sum(
            1
            for family in research.queue_families
            if family.first_item is not None and family.first_item.stage_blocked
        )

        lines.append(
            "STATE   "
            f"{research.status}"
            f" | mode {research.current_mode}"
            f" | selected {_none_label(research.selected_family)}"
        )
        lines.append(
            "METRIC  "
            f"families {ready_families}/{total_families} ready"
            f" | items {total_items}"
            f" | deferred {research.deferred_request_count}"
            f" | breadcrumbs {research.deferred_breadcrumb_count}"
        )
        if pending_questions:
            selected = self._selected_question(research) or pending_questions[0]
            lines.append(
                "INTERVIEW "
                f"{len(pending_questions)} pending"
                f" | focus {selected.title}"
                f" | {_interview_status(selected)}"
            )
        else:
            lines.append("INTERVIEW none pending")
        lines.append(
            "AUDIT   "
            f"{'allowed' if research.completion_allowed else 'blocked'}"
            f" | reason {research.completion_reason}"
            f" | gate {_none_label(research.latest_gate_decision)}"
        )

        last_activity = self._last_meaningful_activity(research)
        if last_activity is None:
            lines.append("LAST    no recent research activity")
        else:
            run_fragment = f" | run {compact_run_label(last_activity.run_id)}" if last_activity.run_id else ""
            lines.append(
                "LAST    "
                f"{format_short_timestamp(last_activity.timestamp)}"
                f" | {last_activity.event_type}"
                f" | {last_activity.summary}{run_fragment}"
            )

        lines.append("")
        if pending_questions:
            lines.append("PENDING interview questions")
            for question in pending_questions[:3]:
                lines.append(
                    _interview_operator_line(
                        question,
                        selected=(question.question_id == self._selected_question_id),
                    )
                )
            if len(pending_questions) > 3:
                lines.append(f"- +{len(pending_questions) - 3} more pending questions")
            lines.append("")
        if total_families:
            lines.append("FAMILY  queue status")
            for family in research.queue_families[:5]:
                lines.append(f"- {_family_operator_line(family)}")
            if total_families > 5:
                lines.append(f"- +{total_families - 5} more families")
        else:
            lines.append("FAMILY  none discovered")

        warnings = self._operator_warnings(research, blocked_items=blocked_items)
        if warnings:
            lines.append("")
            lines.append("WARN    operator attention")
            for warning in warnings:
                lines.append(f"- {warning}")
        lines.append("")
        if pending_questions:
            lines.append("NEXT    press Enter to answer, accept, or skip the selected interview question")
        else:
            lines.append("NEXT    switch to debug for audit and governance detail")
        return "\n".join(lines)

    def _render_debug_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="RESEARCH",
            failure=self._failure,
            has_snapshot=self._research is not None,
            display_mode=DisplayMode.DEBUG,
        )
        if self._research is None:
            lines.append("Waiting for the research snapshot.")
            return "\n".join(lines)

        research = self._research
        lines.append(
            "MODE    "
            f"current {research.current_mode}"
            f" | last {research.last_mode}"
            f" | configured {research.configured_mode}"
            f" | idle {research.configured_idle_mode}"
        )
        lines.append(
            "STATE   "
            f"{research.status}"
            f" | source {research.source_kind}"
            f" | reason {research.mode_reason}"
        )
        lines.append(
            "CYCLE   "
            f"{research.cycle_count} cycles"
            f" | transitions {research.transition_count}"
            f" | updated {format_timestamp(research.updated_at)}"
            f" | next poll {format_timestamp(research.next_poll_at)}"
        )
        lines.append(
            "QUEUE   "
            f"selected {_none_label(research.selected_family)}"
            f" | deferred requests {research.deferred_request_count}"
            f" | breadcrumbs {research.deferred_breadcrumb_count}"
        )
        lines.append("")
        lines.extend(self._interview_lines(research))
        lines.append("")
        lines.append("FAMILIES")
        if research.queue_families:
            for family in research.queue_families:
                lines.append(f"- {_family_line(family)}")
        else:
            lines.append("- no research queue families discovered")

        lines.append("")
        lines.extend(self._audit_lines(research))

        if research.governance is not None:
            lines.append("")
            lines.extend(self._governance_lines(research))

        lines.append("")
        lines.extend(self._activity_lines(research))
        return "\n".join(lines)

    def _last_meaningful_activity(self, research: ResearchOverviewView):
        for event in reversed(research.recent_activity):
            summary = " ".join(event.summary.split())
            if summary:
                return event
        return research.recent_activity[-1] if research.recent_activity else None

    def _operator_warnings(self, research: ResearchOverviewView, *, blocked_items: int) -> list[str]:
        warnings: list[str] = []
        pending_questions = self._pending_questions(research)
        if pending_questions:
            warnings.append(
                f"{len(pending_questions)} interview question{'s' if len(pending_questions) != 1 else ''} waiting for operator input"
            )
        if not research.completion_allowed:
            warnings.append(
                "completion blocked"
                f" | reason {research.completion_reason}"
                f" | gate {_none_label(research.latest_gate_decision)}"
            )
        if blocked_items:
            warnings.append(f"{blocked_items} queued family heads are stage-blocked")
        warnings.extend(_governance_alert_fragments(research))
        return warnings

    def _audit_lines(self, research: ResearchOverviewView) -> list[str]:
        lines = [
            "COMPLETE "
            f"allowed {_yes_no(research.completion_allowed)}"
            f" | reason {research.completion_reason}"
            f" | gate {_none_label(research.latest_gate_decision)}"
            f" | decision {_none_label(research.latest_completion_decision)}"
        ]
        summary = research.audit_summary
        if summary is None:
            status = "present" if research.audit_summary_present else "missing"
            lines.append(f"AUDIT   summary {status} | expected {research.audit_summary_path}")
            return lines

        lines.append(
            "AUDIT   "
            f"total {summary.total_count}"
            f" | pass {summary.pass_count}"
            f" | fail {summary.fail_count}"
            f" | updated {format_timestamp(summary.updated_at)}"
        )
        lines.append(
            "LAST    "
            f"{summary.last_status}"
            f" | decision {_none_label(summary.last_decision)}"
            f" | reasons {summary.last_reason_count}"
        )
        if summary.last_title or summary.last_at or summary.last_details != "none":
            lines.append(
                "DETAIL  "
                f"{_none_label(summary.last_title)}"
                f" | at {format_timestamp(summary.last_at)}"
                f" | {summary.last_details}"
            )
        if summary.remediation_task_id or summary.remediation_spec_id or summary.remediation_action:
            lines.append(
                "REMEDY  "
                f"{_none_label(summary.remediation_action)}"
                f" | task {_none_label(summary.remediation_task_id)}"
                f" | spec {_none_label(summary.remediation_spec_id)}"
            )
            if summary.remediation_task_title:
                lines.append(f"TASK    {summary.remediation_task_title}")
        return lines

    def _governance_lines(self, research: ResearchOverviewView) -> list[str]:
        governance = research.governance
        if governance is None:
            return []

        lines: list[str] = ["GOVERN"]
        if governance.queue_governor_status != "not_applicable":
            lines.append(
                f"- queue {governance.queue_governor_status} | {governance.queue_governor_reason}"
            )
        if governance.drift_status != "not_applicable" or governance.drift_fields:
            fragments = [
                f"drift {governance.drift_status}",
                governance.drift_reason,
            ]
            if governance.drift_fields:
                fragments.append(f"fields {', '.join(governance.drift_fields)}")
            lines.append(f"- {' | '.join(fragment for fragment in fragments if fragment)}")
        if governance.canary_status != "not_configured" or governance.canary_changed_fields:
            fragments = [
                f"canary {governance.canary_status}",
                governance.canary_reason,
            ]
            if governance.canary_changed_fields:
                fragments.append(f"changed {', '.join(governance.canary_changed_fields)}")
            lines.append(f"- {' | '.join(fragment for fragment in fragments if fragment)}")
        if governance.recovery_status != "not_active" or governance.recovery_batch_id:
            fragments = [
                f"recovery {governance.recovery_status}",
                governance.recovery_reason,
            ]
            if governance.recovery_batch_id:
                fragments.append(f"batch {governance.recovery_batch_id}")
            fragments.append(f"visible tasks {governance.recovery_visible_task_count}")
            if governance.recovery_escalation_action != "none":
                fragments.append(f"action {governance.recovery_escalation_action}")
            lines.append(f"- {' | '.join(fragment for fragment in fragments if fragment)}")
        if governance.recovery_regeneration_status or governance.regenerated_task_id:
            fragments = [
                f"regen {_none_label(governance.recovery_regeneration_status)}",
            ]
            if governance.regenerated_task_id:
                fragments.append(f"task {governance.regenerated_task_id}")
            if governance.regenerated_task_title:
                fragments.append(governance.regenerated_task_title)
            lines.append(f"- {' | '.join(fragment for fragment in fragments if fragment)}")
        return lines

    def _activity_lines(self, research: ResearchOverviewView) -> list[str]:
        if not research.recent_activity:
            return ["ACTIVITY no recent research events."]

        lines = ["ACTIVITY recent research events"]
        for event in research.recent_activity[-5:]:
            run_fragment = f" | run {event.run_id}" if event.run_id else ""
            lines.append(
                f"- {format_short_timestamp(event.timestamp)} "
                f"{event.category} {event.event_type} | {event.summary}{run_fragment}"
            )
        return lines

    def _interview_lines(self, research: ResearchOverviewView) -> list[str]:
        pending = self._pending_questions(research)
        if not pending:
            return ["INTERVIEW no pending interview questions."]

        lines = [f"INTERVIEW {len(pending)} pending question{'s' if len(pending) != 1 else ''}"]
        for question in pending:
            fragments = [
                question.question_id,
                question.spec_id,
                question.status,
                _interview_status(question),
                f"source {question.answer_source}",
            ]
            lines.append(f"- {' | '.join(fragments)}")
            lines.append(f"  title {question.title}")
            lines.append(f"  ask {question.question}")
            lines.append(f"  why {question.why_this_matters}")
            lines.append(f"  recommend {question.recommended_answer}")
            lines.append(f"  updated {format_timestamp(question.updated_at)} | path {question.source_path}")
        return lines


__all__ = ["ResearchPanel"]
