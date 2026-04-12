"""Compact cockpit-style overview of the current runtime snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import ContentSwitcher, Static

from ..models import (
    CompoundingGovernanceOverviewView,
    DisplayMode,
    GatewayFailure,
    QueueOverviewView,
    QueueTaskView,
    ResearchOverviewView,
    RuntimeOverviewView,
    SentinelOverviewView,
)
from .progressive_disclosure import append_panel_failure_lines, collapse_operator_text


@dataclass(frozen=True, slots=True)
class LatestRunSummary:
    """Truthful shell-owned summary of the newest visible run artifact."""

    run_id: str
    compiled_at: str | None = None
    selection_ref: str | None = None
    stage_count: int | None = None
    latest_status: str | None = None
    latest_transition_label: str | None = None
    history_present: bool = False
    note: str | None = None
    error: str | None = None


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    rounded = max(int(seconds), 0)
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _runtime_label(value: str | None) -> str:
    normalized = " ".join(value.split()) if value is not None else ""
    return normalized or "none"


def _clip_fragment(value: str, *, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    if limit <= 3:
        return normalized[:limit]
    return f"{normalized[: limit - 3]}..."


def _compact_selection_ref(selection_ref: str | None) -> str | None:
    normalized = " ".join((selection_ref or "").split())
    if not normalized:
        return None
    ref = normalized.split(":", maxsplit=1)[1] if ":" in normalized else normalized
    ref = ref.split("@", maxsplit=1)[0]
    if ref == "mode.standard":
        ref = "mode.std"
    return _clip_fragment(ref, limit=20)


def _compact_timestamp(value: str | None) -> str | None:
    normalized = " ".join((value or "").split())
    if not normalized:
        return None
    candidate = normalized
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        return _clip_fragment(normalized, limit=12)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%H:%M:%SZ")


def _compact_datetime(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%H:%M:%SZ")


def _task_label(task: QueueTaskView | None, *, include_id: bool = False) -> str:
    if task is None:
        return "none"
    label = task.title
    if include_id:
        label = f"{label} [{task.task_id}]"
    if task.spec_id:
        label = f"{label} | {task.spec_id}"
    return label


def _mailbox_intake_fragment(queue: QueueOverviewView | None) -> str:
    if queue is None or queue.mailbox_task_intake_count <= 0:
        return "mailbox 0"
    return f"mailbox {queue.mailbox_task_intake_count}"


def _parse_latest_error(error: str) -> tuple[str, tuple[str, ...]]:
    normalized = " ".join(error.split())
    if not normalized:
        return "unknown latest run error", ()
    if ":" not in normalized:
        return _clip_fragment(normalized, limit=64), ()
    head, tail = normalized.split(":", maxsplit=1)
    details = tuple(fragment.strip() for fragment in tail.split(";") if fragment.strip())
    headline = _clip_fragment(head.strip(), limit=48)
    return headline or "latest run error", details


def _operator_error_summary(error: str) -> str:
    headline, _ = _parse_latest_error(error)
    normalized = headline.lower()
    if "invalid provenance" in normalized:
        return "invalid provenance"
    return _clip_fragment(headline, limit=28)


class OverviewPanel(Static):
    """High-signal overview of daemon, execution, research, queue, and latest-run state."""

    can_focus = True
    _LATEST_STATE_CLASSES = ("state-ok", "state-warn", "state-fail", "state-info")
    _ATTENTION_STATE_CLASSES = ("state-calm", "state-notice", "state-warning", "state-failure")

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, classes="panel-card", markup=False)
        self.border_title = "Overview"
        self._runtime: RuntimeOverviewView | None = None
        self._queue: QueueOverviewView | None = None
        self._research: ResearchOverviewView | None = None
        self._compounding: CompoundingGovernanceOverviewView | None = None
        self._latest_run: LatestRunSummary | None = None
        self._failure: GatewayFailure | None = None
        self._display_mode: DisplayMode = DisplayMode.OPERATOR

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="overview-operator", id="overview-mode-switcher"):
            with Vertical(id="overview-operator"):
                with Horizontal(classes="overview-metrics"):
                    yield self._metric_card("active", "Active task")
                    yield self._metric_card("next", "Next")
                    yield self._metric_card("backlog", "Backlog")
                with Vertical(classes="overview-stack"):
                    yield self._detail_card("runtime", "Runtime")
                    yield self._detail_card("sentinel", "Sentinel")
                    yield self._detail_card("latest", "Latest run")
                    yield self._detail_card("research", "Research")
                    yield self._detail_card("governance", "Governance")
                    yield self._detail_card("attention", "Attention")
            yield Static("", id="overview-debug", classes="overview-debug-body")

    def on_mount(self) -> None:
        self._render_state()

    def show_snapshot(
        self,
        *,
        runtime: RuntimeOverviewView | None,
        queue: QueueOverviewView | None,
        research: ResearchOverviewView | None,
        compounding: CompoundingGovernanceOverviewView | None = None,
        latest_run: LatestRunSummary | None,
        failure: GatewayFailure | None = None,
        display_mode: DisplayMode = DisplayMode.OPERATOR,
    ) -> None:
        self._runtime = runtime
        self._queue = queue
        self._research = research
        self._compounding = compounding
        self._latest_run = latest_run
        self._failure = failure
        self._display_mode = display_mode
        if self.is_mounted:
            self._render_state()

    def summary_text(self) -> str:
        if self._display_mode is DisplayMode.DEBUG:
            return self._render_debug_text()
        return self._render_operator_summary_text()

    @staticmethod
    def _metric_card(suffix: str, title: str) -> Vertical:
        return Vertical(
            Static(title, id=f"overview-{suffix}-label", classes="overview-card-label"),
            Static("--", id=f"overview-{suffix}-value", classes="overview-card-value"),
            Static("", id=f"overview-{suffix}-meta", classes="overview-card-meta"),
            classes="overview-card overview-metric-card",
            id=f"overview-{suffix}-card",
        )

    @staticmethod
    def _detail_card(suffix: str, title: str) -> Vertical:
        return Vertical(
            Static(title, id=f"overview-{suffix}-label", classes="overview-card-label"),
            Static("--", id=f"overview-{suffix}-headline", classes="overview-card-headline"),
            Static("", id=f"overview-{suffix}-detail", classes="overview-card-detail"),
            classes="overview-card overview-detail-card",
            id=f"overview-{suffix}-card",
        )

    def _render_state(self) -> None:
        switcher = self.query_one("#overview-mode-switcher", ContentSwitcher)
        switcher.current = "overview-debug" if self._display_mode is DisplayMode.DEBUG else "overview-operator"
        debug_body = self.query_one("#overview-debug", Static)
        debug_body.update(self._render_debug_text())
        if self._display_mode is DisplayMode.DEBUG:
            return
        self._render_operator_cards()

    def _render_operator_cards(self) -> None:
        if self._runtime is None:
            self._update_metric("active", "none", "waiting for runtime snapshot")
            self._update_metric("next", "--", "queue unavailable")
            self._update_metric("backlog", "--", "runtime unavailable")
            self._update_detail("runtime", "Waiting for the runtime snapshot.", self._failure_summary())
            self._update_detail("sentinel", "Waiting for runtime snapshot", "sentinel summary unavailable")
            self._update_detail("latest", "No run snapshot yet", "latest run artifacts unavailable")
            self._update_detail("research", "Waiting for research snapshot", "research status unavailable")
            self._update_detail("governance", "Waiting for governance snapshot", "compounding summary unavailable")
            self._update_attention_card()
            self._set_latest_state("state-info")
            return

        runtime = self._runtime
        queue = self._queue
        research = self._research
        selection_decision = runtime.selection_decision
        active_value = _task_label(queue.active_task if queue is not None else None) if queue is not None else _runtime_label(runtime.active_task_id)
        next_value = _task_label(queue.next_task if queue is not None else None)
        selected_size = selection_decision.selected_size if selection_decision is not None else "unknown"
        route = selection_decision.route_decision if selection_decision is not None else "unknown"
        active_meta = f"{runtime.execution_status.lower()} | {selected_size}"
        next_meta = f"queued | {route}"
        backlog_meta = f"{runtime.deferred_queue_size} deferred | {_mailbox_intake_fragment(queue)} | route {route}"

        self._update_metric("active", active_value, active_meta)
        self._update_metric("next", next_value, next_meta)
        self._update_metric("backlog", str(runtime.backlog_depth), backlog_meta)

        paused_label = "paused" if runtime.paused else "live"
        if runtime.paused and runtime.pause_reason:
            paused_label = f"paused ({runtime.pause_reason})"
        runtime_headline = f"{'running' if runtime.process_running else 'stopped'} | mode {runtime.mode}"
        runtime_detail = (
            f"{paused_label} | exec {runtime.execution_status.lower()} | uptime {_format_duration(runtime.uptime_seconds)}"
        )
        if runtime.pending_active_task_clear_reason:
            runtime_detail = f"{runtime_detail} | clear pending"
        if runtime.liveness_degraded and runtime.liveness_summary:
            runtime_detail = f"{runtime_detail} | {runtime.liveness_summary}"
        self._update_detail("runtime", runtime_headline, runtime_detail)

        sentinel_headline, sentinel_detail = self._sentinel_card_content()
        self._update_detail("sentinel", sentinel_headline, sentinel_detail)
        latest_headline, latest_detail, latest_state = self._latest_run_card_content()
        self._update_detail("latest", latest_headline, latest_detail)
        self._set_latest_state(latest_state)

        if research is None:
            research_headline = f"{runtime.research_status.lower()} | waiting"
            research_detail = "research snapshot not available"
        else:
            research_headline = f"{runtime.research_status.lower()} | mode {research.current_mode.lower()}"
            research_detail = (
                f"family {_runtime_label(research.selected_family)} | deferred {research.deferred_request_count}"
            )
        self._update_detail("research", research_headline, research_detail)
        governance_headline, governance_detail = self._governance_card_content()
        self._update_detail("governance", governance_headline, governance_detail)
        self._update_attention_card()

    def _update_metric(self, suffix: str, value: str, meta: str) -> None:
        self.query_one(f"#overview-{suffix}-value", Static).update(value)
        self.query_one(f"#overview-{suffix}-meta", Static).update(meta)

    def _update_detail(self, suffix: str, headline: str, detail: str) -> None:
        self.query_one(f"#overview-{suffix}-headline", Static).update(headline)
        self.query_one(f"#overview-{suffix}-detail", Static).update(detail)

    def _failure_summary(self) -> str:
        if self._failure is None:
            return "no cached snapshot available"
        qualifier = "stale snapshot" if self._runtime is not None else "refresh unavailable"
        return f"{qualifier} | {collapse_operator_text(self._failure.message, max_parts=2, max_length=72)}"

    def _update_attention_card(self) -> None:
        headline, detail, state_class = self._attention_card_content()
        self._update_detail("attention", headline, detail)
        card = self.query_one("#overview-attention-card", Vertical)
        for class_name in self._ATTENTION_STATE_CLASSES:
            card.remove_class(class_name)
        card.add_class(state_class)

    def _attention_card_content(self) -> tuple[str, str, str]:
        if self._failure is not None:
            qualifier = "showing last known snapshot" if self._runtime is not None else "snapshot unavailable"
            return (
                "Refresh degraded",
                f"{qualifier} | {collapse_operator_text(self._failure.message, max_parts=2, max_length=84)}",
                "state-warning",
            )
        runtime = self._runtime
        if runtime is None:
            return ("Waiting for snapshot", "runtime data has not loaded yet", "state-notice")
        summary = self._latest_run
        if summary is not None and summary.error:
            if "invalid provenance" in summary.error.lower():
                return ("Latest run needs review", "invalid provenance; open debug for technical detail", "state-failure")
            return (
                "Latest run unavailable",
                collapse_operator_text(summary.error, max_parts=2, max_length=84),
                "state-warning",
            )
        if summary is not None and summary.note:
            return (summary.note, "latest run metadata is incomplete", "state-notice")
        if runtime.paused and runtime.pause_reason:
            return (runtime.pause_reason, "daemon paused until resumed", "state-warning")
        if runtime.pending_active_task_clear_reason:
            return (
                "Active clear pending",
                f"accepted and waiting for a legal daemon boundary | {runtime.pending_active_task_clear_reason}",
                "state-warning",
            )
        if self._queue is not None and self._queue.mailbox_task_intake_count > 0:
            count = self._queue.mailbox_task_intake_count
            label = "request" if count == 1 else "requests"
            title = self._queue.mailbox_task_intake_titles[0] if self._queue.mailbox_task_intake_titles else None
            detail = f"{count} buffered add-task {label} accepted but not yet visible in backlog"
            if title:
                detail = f"{detail} | next buffered: {title}"
            return (f"Mailbox buffered {count}", detail, "state-warning")
        if self._research is not None and self._research.deferred_request_count > 0:
            count = self._research.deferred_request_count
            label = "request" if count == 1 else "requests"
            return (f"Research deferred {count}", f"{count} deferred {label} waiting for operator follow-up", "state-warning")
        if self._compounding is not None and self._compounding.pending_governance_items > 0:
            pending = self._compounding.pending_governance_items
            label = "item" if pending == 1 else "items"
            return (
                f"Governance pending {pending}",
                f"{pending} governed knowledge {label} still need review",
                "state-warning",
            )
        return ("No immediate operator action", "runtime and latest run surfaces look stable", "state-calm")

    def _governance_card_content(self) -> tuple[str, str]:
        governance = self._compounding
        if governance is None:
            return ("waiting", "compounding summary not available")
        headline = (
            f"pending {governance.pending_governance_items}"
            if governance.pending_governance_items > 0
            else "no pending review"
        )
        fragments = [
            f"proc {governance.procedure_pending_review}",
            f"facts {governance.context_fact_pending_review}",
            f"harness {governance.harness_candidate_pending_review}",
            f"recs {governance.recommendation_pending}",
        ]
        if governance.recent_usage_run_id is not None:
            fragments.append(
                f"used {governance.recent_usage_run_id} p{governance.recent_usage_procedure_count}/f{governance.recent_usage_context_fact_count}"
            )
        return (headline, " | ".join(fragments))

    def _sentinel_card_content(self) -> tuple[str, str]:
        sentinel = None if self._runtime is None else self._runtime.sentinel
        if sentinel is None:
            return ("waiting", "supervisor sentinel summary not available")
        if not sentinel.available:
            headline = "disabled by config" if not sentinel.config_enabled else "no persisted state"
            detail = sentinel.reason or "sentinel has not written persisted artifacts yet"
            return (headline, detail)

        headline = _runtime_label(sentinel.status).lower()
        if sentinel.monitoring_active:
            headline = f"{headline} | monitoring {sentinel.route_target}"
        elif sentinel.hard_cap_triggered:
            headline = f"{headline} | hard cap"
        elif sentinel.soft_cap_active:
            headline = f"{headline} | soft cap"

        fragments = [f"checks {sentinel.checks_performed}"]
        last_check = _compact_datetime(sentinel.last_check_at)
        next_check = _compact_datetime(sentinel.next_check_at)
        if last_check:
            fragments.append(f"last {last_check}")
        if next_check:
            fragments.append(f"next {next_check}")
        if sentinel.recovery_cycles_queued > 0:
            fragments.append(f"recovery {sentinel.recovery_cycles_queued}")
        if sentinel.acknowledgment_required:
            fragments.append("ack required")
        if sentinel.last_notification_status:
            fragments.append(f"notify {sentinel.last_notification_status.lower()}")
        elif sentinel.hard_cap_triggered:
            fragments.append("notify pending")
        return (headline, " | ".join(fragments))

    def _latest_run_card_content(self) -> tuple[str, str, str]:
        summary = self._latest_run
        if summary is None:
            return ("No run artifacts", "nothing compiled yet", "state-info")
        if summary.error is not None:
            return (f"FAIL {summary.run_id}", _operator_error_summary(summary.error), "state-fail")

        headline = f"{self._latest_outcome(summary)} {summary.run_id}"
        fragments: list[str] = []
        selection_ref = _compact_selection_ref(summary.selection_ref)
        if selection_ref:
            fragments.append(f"sel {selection_ref}")
        compact_time = _compact_timestamp(summary.compiled_at)
        if compact_time:
            fragments.append(compact_time)
        if summary.stage_count is not None:
            fragments.append(f"stg {summary.stage_count}")
        if summary.latest_status:
            fragments.append(f"status {_clip_fragment(summary.latest_status.lower(), limit=24)}")
        elif not summary.history_present:
            fragments.append("hist no")
        if summary.note:
            fragments.append(summary.note)

        state_class = {
            "SUCCESS": "state-ok",
            "WARN": "state-warn",
            "FAIL": "state-fail",
            "INFO": "state-info",
        }[self._latest_outcome(summary)]
        return (headline, " | ".join(fragments) if fragments else "latest run metadata available", state_class)

    @staticmethod
    def _latest_outcome(summary: LatestRunSummary) -> str:
        if summary.error:
            return "FAIL"
        status_text = " ".join((summary.latest_status or "").upper().split())
        if any(token in status_text for token in ("FAIL", "ERROR", "BLOCK", "NEEDED")):
            return "FAIL"
        if any(token in status_text for token in ("ACCEPT", "COMPLETE", "SUCCESS", "PASS")):
            return "SUCCESS"
        if summary.note:
            return "WARN"
        return "INFO"

    def _set_latest_state(self, state_class: str) -> None:
        card = self.query_one("#overview-latest-card", Vertical)
        for class_name in self._LATEST_STATE_CLASSES:
            card.remove_class(class_name)
        card.add_class(state_class)

    def _render_operator_summary_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="OVERVIEW",
            failure=self._failure,
            has_snapshot=self._runtime is not None,
            display_mode=DisplayMode.OPERATOR,
        )
        if self._runtime is None:
            lines.append("ACTIVE   none")
            lines.append("NEXT     --")
            lines.append("BACKLOG  --")
            lines.append("RUNTIME  waiting for runtime snapshot")
            lines.append("SENTINEL waiting for runtime snapshot")
            lines.append("LATEST   no run artifacts")
            lines.append("RESEARCH waiting for research snapshot")
            lines.append("GOVERN  waiting for governance snapshot")
            lines.append("ATTN     waiting for snapshot")
            if self._failure is not None:
                lines.append(f"STATE    {self._failure_summary()}")
            return "\n".join(lines)

        runtime = self._runtime
        queue = self._queue
        selection_decision = runtime.selection_decision
        lines.append(
            f"ACTIVE   {_task_label(queue.active_task if queue is not None else None) if queue is not None else _runtime_label(runtime.active_task_id)}"
        )
        lines.append(f"NEXT     {_task_label(queue.next_task if queue is not None else None)}")
        lines.append(
            "BACKLOG  "
            f"{runtime.backlog_depth} | deferred {runtime.deferred_queue_size} | route "
            f"{selection_decision.route_decision if selection_decision is not None else 'unknown'} | "
            f"{_mailbox_intake_fragment(queue)}"
        )
        lines.append(
            "RUNTIME  "
            f"{'running' if runtime.process_running else 'stopped'} | mode {runtime.mode} | "
            f"exec {runtime.execution_status} | uptime {_format_duration(runtime.uptime_seconds)}"
            f"{' | clear pending' if runtime.pending_active_task_clear_reason else ''}"
        )
        sentinel_headline, sentinel_detail = self._sentinel_card_content()
        lines.append(f"SENTINEL {sentinel_headline} | {sentinel_detail}")
        latest_headline, latest_detail, _ = self._latest_run_card_content()
        lines.append(f"LATEST   {latest_headline} | {latest_detail}")
        if self._research is None:
            lines.append(f"RESEARCH {runtime.research_status} | waiting")
        else:
            lines.append(
                "RESEARCH "
                f"{runtime.research_status} | mode {self._research.current_mode} | family "
                f"{_runtime_label(self._research.selected_family)} | deferred {self._research.deferred_request_count}"
            )
        governance_headline, governance_detail = self._governance_card_content()
        lines.append(f"GOVERN  {governance_headline} | {governance_detail}")
        attention_headline, attention_detail, _ = self._attention_card_content()
        lines.append(f"ATTN     {attention_headline} | {attention_detail}")
        return "\n".join(lines)

    def _render_debug_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="OVERVIEW",
            failure=self._failure,
            has_snapshot=self._runtime is not None,
            display_mode=DisplayMode.DEBUG,
        )
        if self._runtime is None:
            lines.append("Waiting for the runtime snapshot.")
            return "\n".join(lines)

        runtime = self._runtime
        queue = self._queue
        research = self._research
        selection_decision = runtime.selection_decision

        paused_label = "paused" if runtime.paused else "live"
        if runtime.paused and runtime.pause_reason:
            paused_label = f"paused ({runtime.pause_reason})"
        lines.append(
            "DAEMON   "
            f"{'running' if runtime.process_running else 'stopped'}"
            f" | mode {runtime.mode}"
            f" | {paused_label}"
            f" | uptime {_format_duration(runtime.uptime_seconds)}"
        )
        lines.append(
            "EXEC     "
            f"{runtime.execution_status}"
            f" | size {selection_decision.selected_size if selection_decision is not None else 'unknown'}"
            f" | route {selection_decision.route_decision if selection_decision is not None else 'unknown'}"
            f" | stages {len(runtime.selection.stage_labels)}"
        )
        lines.append(f"SENTINEL {self._debug_sentinel_summary(runtime.sentinel)}")
        if research is None:
            lines.append(f"RESEARCH {runtime.research_status} | waiting for the research snapshot")
        else:
            lines.append(
                "RESEARCH "
                f"{runtime.research_status}"
                f" | mode {research.current_mode}"
                f" | family {_runtime_label(research.selected_family)}"
                f" | deferred {research.deferred_request_count}"
            )
        governance_headline, governance_detail = self._governance_card_content()
        lines.append(f"GOVERN  {governance_headline} | {governance_detail}")
        lines.append(
            "WORK     "
            f"queued {runtime.backlog_depth}"
            f" | deferred {runtime.deferred_queue_size}"
            f" | {_mailbox_intake_fragment(queue)}"
            f" | active "
            f"{_task_label(queue.active_task, include_id=True) if queue is not None else _runtime_label(runtime.active_task_id)}"
            f" | next {_task_label(queue.next_task) if queue is not None else 'waiting'}"
        )
        if runtime.pending_active_task_clear_reason:
            lines.append(f"CLEAR    pending | {runtime.pending_active_task_clear_reason}")
        lines.extend(self._latest_run_debug_lines())
        return "\n".join(lines)

    @staticmethod
    def _debug_sentinel_summary(sentinel: SentinelOverviewView | None) -> str:
        if sentinel is None:
            return "summary unavailable"
        if not sentinel.available:
            return sentinel.reason or "no persisted state"
        fragments = [
            _runtime_label(sentinel.status),
            f"lifecycle {_runtime_label(sentinel.lifecycle_status)}",
            f"route {sentinel.route_target}",
            f"checks {sentinel.checks_performed}",
        ]
        if sentinel.monitoring_active:
            fragments.append("monitoring on")
        if sentinel.soft_cap_active:
            fragments.append("soft cap")
        if sentinel.hard_cap_triggered:
            fragments.append("hard cap")
        if sentinel.acknowledgment_required:
            fragments.append("ack required")
        if sentinel.queued_recovery_request_id:
            fragments.append(f"req {sentinel.queued_recovery_request_id}")
        if sentinel.last_notification_status:
            fragments.append(f"notify {sentinel.last_notification_status}")
        return " | ".join(fragments)

    def _latest_run_debug_lines(self) -> list[str]:
        summary = self._latest_run
        if summary is None:
            return ["LATEST   none | no run artifacts discovered"]
        if summary.error is not None:
            headline, details = _parse_latest_error(summary.error)
            lines = [f"LATEST   FAIL {summary.run_id} | {headline}"]
            for detail in details[:4]:
                lines.append(f"PROV    {detail}")
            if len(details) > 4:
                lines.append(f"PROV    +{len(details) - 4} more")
            return lines

        fragments = [f"LATEST   {self._latest_outcome(summary)} {summary.run_id}"]
        if summary.selection_ref:
            fragments.append(summary.selection_ref)
        if summary.compiled_at:
            fragments.append(summary.compiled_at)
        if summary.stage_count is not None:
            fragments.append(f"{summary.stage_count} stages")
        if summary.latest_status:
            fragments.append(f"status {summary.latest_status}")
        elif not summary.history_present:
            fragments.append("history no")
        return [" | ".join(fragments)]


__all__ = [
    "LatestRunSummary",
    "OverviewPanel",
    "_runtime_label",
]
