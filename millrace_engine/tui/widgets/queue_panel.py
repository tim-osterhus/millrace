"""Navigable queue panel for active, next, and backlog work."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import ContentSwitcher, DataTable, Static

from ..models import DisplayMode, GatewayFailure, QueueOverviewView, QueueTaskView
from .progressive_disclosure import append_panel_failure_lines, collapse_operator_text


def _task_label(task: QueueTaskView | None, *, include_title_only: bool = False) -> str:
    if task is None:
        return "none"
    if include_title_only:
        return task.title
    label = f"{task.title} [{task.task_id}]"
    if task.spec_id:
        label = f"{label} | {task.spec_id}"
    return label


def _task_operator_label(task: QueueTaskView | None) -> str:
    if task is None:
        return "none"
    return task.title


def _queue_card_label(label: str, value: str) -> str:
    return f"{label:<7} {value}"


def _mailbox_buffer_label(queue: QueueOverviewView) -> str:
    count = queue.mailbox_task_intake_count
    if count <= 0:
        return "mailbox clear"
    return f"{count} mailbox buffered"


class QueuePanel(Static):
    """Display queue state and allow keyboard navigation of the visible backlog."""

    can_focus = True
    BINDINGS = (
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("home", "cursor_home", show=False),
        Binding("end", "cursor_end", show=False),
        Binding("o", "open_run_detail", show=False),
        Binding("r", "begin_reorder", show=False),
        Binding("[", "move_selected_earlier", show=False),
        Binding("]", "move_selected_later", show=False),
        Binding("escape", "cancel_reorder", show=False),
        Binding("enter", "submit_selection", show=False),
    )

    class SelectionChanged(Message):
        """Posted when the highlighted backlog item changes."""

        bubble = True

        def __init__(self, task_id: str | None) -> None:
            super().__init__()
            self.task_id = task_id

    class SelectionSubmitted(Message):
        """Posted when the operator presses enter on the highlighted backlog item."""

        bubble = True

        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    class ReorderRequested(Message):
        """Posted when the operator wants the shell to confirm a staged reorder."""

        bubble = True

        def __init__(self, task_ids: tuple[str, ...]) -> None:
            super().__init__()
            self.task_ids = task_ids

    class RunRequested(Message):
        """Posted when the operator wants provenance detail for the current active run."""

        bubble = True

        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, classes="panel-card", markup=False)
        self.border_title = "Queue"
        self._queue: QueueOverviewView | None = None
        self._failure: GatewayFailure | None = None
        self._selected_task_id: str | None = None
        self._reorder_task_ids: tuple[str, ...] | None = None
        self._run_id: str | None = None
        self._display_mode: DisplayMode = DisplayMode.OPERATOR

    @property
    def selected_task_id(self) -> str | None:
        return self._selected_task_id

    @property
    def reorder_mode(self) -> bool:
        return self._reorder_task_ids is not None

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="queue-operator", id="queue-mode-switcher"):
            with Vertical(id="queue-operator", classes="panel-mode-body"):
                yield self._section_card("queue-status", "Status")
                with Horizontal(classes="panel-metrics"):
                    yield self._metric_card("queue-active", "Active")
                    yield self._metric_card("queue-next", "Next")
                    yield self._metric_card("queue-backlog", "Backlog")
                yield self._section_card("queue-run", "Run detail")
                with Vertical(id="queue-list-card", classes="overview-card panel-section-card panel-list-card"):
                    yield Static("Backlog", classes="overview-card-label")
                    yield Static("--", id="queue-list-headline", classes="overview-card-headline")
                    yield Static("", id="queue-list-detail", classes="overview-card-detail")
                    yield DataTable(id="queue-table", classes="panel-data-table")
                    yield Static("", id="queue-list-focus", classes="overview-card-detail")
                yield self._section_card("queue-actions", "Actions")
            yield Static("", id="queue-debug", classes="panel-debug-body")

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

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_column("Ord", key="order", width=5)
        table.add_column("Task", key="task")
        table.add_column("Spec", key="spec", width=18)
        table.add_column("Draft", key="draft", width=12)
        self._render_state()

    @on(DataTable.RowHighlighted, "#queue-table")
    def _handle_queue_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        task_id = event.row_key.value
        if task_id.startswith("__queue-empty__") or task_id == self._selected_task_id:
            return
        self._selected_task_id = task_id
        self.post_message(self.SelectionChanged(task_id))
        self._update_focus_label()

    @on(DataTable.RowSelected, "#queue-table")
    def _handle_queue_row_selected(self, event: DataTable.RowSelected) -> None:
        task_id = event.row_key.value
        if task_id.startswith("__queue-empty__"):
            return
        if task_id != self._selected_task_id:
            self._selected_task_id = task_id
            self.post_message(self.SelectionChanged(task_id))
            self._update_focus_label()
        self.action_submit_selection()

    def show_snapshot(
        self,
        queue: QueueOverviewView | None,
        *,
        run_id: str | None = None,
        failure: GatewayFailure | None = None,
        display_mode: DisplayMode = DisplayMode.OPERATOR,
    ) -> None:
        self._queue = queue
        self._failure = failure
        self._run_id = " ".join((run_id or "").split()) or None
        self._display_mode = display_mode
        self._reconcile_reorder_state()
        self._reconcile_selection()
        if self.is_mounted:
            self._render_state()

    def summary_text(self) -> str:
        if self._display_mode is DisplayMode.DEBUG:
            return self._render_debug_text()
        return self._render_operator_text()

    def action_cursor_up(self) -> None:
        self._move_selection(-1)

    def action_cursor_down(self) -> None:
        self._move_selection(1)

    def action_cursor_home(self) -> None:
        self._select_index(0)

    def action_cursor_end(self) -> None:
        backlog = self._visible_backlog()
        if backlog:
            self._select_index(len(backlog) - 1)

    def action_begin_reorder(self) -> None:
        backlog = self._backlog()
        if not backlog or self._reorder_task_ids is not None:
            return
        self._reorder_task_ids = tuple(task.task_id for task in backlog)
        if self.is_mounted:
            self._render_state()

    def action_move_selected_earlier(self) -> None:
        self._move_selected_task(-1)

    def action_move_selected_later(self) -> None:
        self._move_selected_task(1)

    def action_cancel_reorder(self) -> None:
        if self._reorder_task_ids is None:
            return
        self._reorder_task_ids = None
        if self.is_mounted:
            self._render_state()

    def action_open_run_detail(self) -> None:
        if self._run_id is None:
            return
        self.post_message(self.RunRequested(self._run_id))

    def action_submit_selection(self) -> None:
        if self._selected_task_id is None:
            return
        if self._reorder_task_ids is None:
            self.action_begin_reorder()
            return
        if self._reorder_task_ids == tuple(task.task_id for task in self._backlog()):
            return
        self.post_message(self.ReorderRequested(self._reorder_task_ids))

    def _backlog(self) -> tuple[QueueTaskView, ...]:
        if self._queue is None:
            return ()
        return self._queue.backlog

    def _visible_backlog(self) -> tuple[QueueTaskView, ...]:
        backlog = self._backlog()
        if self._reorder_task_ids is None:
            return backlog
        by_id = {task.task_id: task for task in backlog}
        return tuple(by_id[task_id] for task_id in self._reorder_task_ids if task_id in by_id)

    def _reconcile_reorder_state(self) -> None:
        if self._reorder_task_ids is None:
            return
        live_ids = tuple(task.task_id for task in self._backlog())
        if tuple(sorted(self._reorder_task_ids)) != tuple(sorted(live_ids)):
            self._reorder_task_ids = None

    def clear_reorder_draft(self) -> None:
        if self._reorder_task_ids is None:
            return
        self._reorder_task_ids = None
        if self.is_mounted:
            self._render_state()

    def _reconcile_selection(self) -> None:
        backlog = self._visible_backlog()
        previous = self._selected_task_id
        if not backlog:
            self._selected_task_id = None
        elif previous is None:
            self._selected_task_id = backlog[0].task_id
        else:
            for task in backlog:
                if task.task_id == previous:
                    self._selected_task_id = previous
                    break
            else:
                self._selected_task_id = backlog[0].task_id
        if previous != self._selected_task_id and self.is_mounted:
            self.post_message(self.SelectionChanged(self._selected_task_id))

    def _selected_index(self) -> int | None:
        if self._selected_task_id is None:
            return None
        for index, task in enumerate(self._visible_backlog()):
            if task.task_id == self._selected_task_id:
                return index
        return None

    def _move_selection(self, delta: int) -> None:
        backlog = self._visible_backlog()
        if not backlog:
            return
        current_index = self._selected_index()
        if current_index is None:
            new_index = 0 if delta >= 0 else len(backlog) - 1
        else:
            new_index = min(max(current_index + delta, 0), len(backlog) - 1)
        self._select_index(new_index)

    def _select_index(self, index: int) -> None:
        backlog = self._visible_backlog()
        if not backlog:
            return
        bounded_index = min(max(index, 0), len(backlog) - 1)
        task_id = backlog[bounded_index].task_id
        if task_id == self._selected_task_id:
            return
        self._selected_task_id = task_id
        if self.is_mounted:
            self._render_state()
            self.post_message(self.SelectionChanged(task_id))

    def _move_selected_task(self, delta: int) -> None:
        if self._reorder_task_ids is None or self._selected_task_id is None:
            return
        task_ids = list(self._reorder_task_ids)
        try:
            current_index = task_ids.index(self._selected_task_id)
        except ValueError:
            self._reorder_task_ids = None
            if self.is_mounted:
                self._render_state()
            return
        new_index = min(max(current_index + delta, 0), len(task_ids) - 1)
        if new_index == current_index:
            return
        task_ids[current_index], task_ids[new_index] = task_ids[new_index], task_ids[current_index]
        self._reorder_task_ids = tuple(task_ids)
        if self.is_mounted:
            self._render_state()

    def _render_state(self) -> None:
        switcher = self.query_one("#queue-mode-switcher", ContentSwitcher)
        switcher.current = "queue-debug" if self._display_mode is DisplayMode.DEBUG else "queue-operator"
        self.query_one("#queue-debug", Static).update(self._render_debug_text())
        if self._display_mode is DisplayMode.DEBUG:
            return
        self._render_operator_surface()

    def _render_operator_surface(self) -> None:
        if self._queue is None:
            self._update_status_card(
                "Waiting for the queue snapshot.",
                self._failure_operator_detail(has_snapshot=False),
            )
            self._update_metric("queue-active", "none", "runtime queue unavailable")
            self._update_metric("queue-next", "--", "waiting for next task")
            self._update_metric("queue-backlog", "--", "no backlog snapshot")
            self._update_section("queue-run", "No active run detail", "queue snapshot not loaded")
            self._set_backlog_content(
                headline="No visible backlog",
                detail="queue snapshot not available",
                focus="",
            )
            self._update_section("queue-actions", "Waiting for queue state", "open debug for task ids and draft detail")
            return

        queue = self._queue
        backlog = self._visible_backlog()
        active_count = 1 if queue.active_task is not None else 0
        self._update_status_card(
            "Snapshot ready" if self._failure is None else "Refresh degraded",
            self._failure_operator_detail(has_snapshot=True) if self._failure is not None else "selection and reorder controls remain live",
        )
        self._update_metric("queue-active", _task_operator_label(queue.active_task), f"{active_count} active task")
        next_meta = "queued next task" if queue.next_task is not None else "no next task queued"
        self._update_metric("queue-next", _task_operator_label(queue.next_task), next_meta)
        backlog_meta = (
            f"{len(backlog)} visible | {_mailbox_buffer_label(queue)}"
            if queue.backlog_depth == len(backlog)
            else f"{len(backlog)} of {queue.backlog_depth} visible | {_mailbox_buffer_label(queue)}"
        )
        self._update_metric("queue-backlog", str(queue.backlog_depth), backlog_meta)

        if self._run_id is None:
            self._update_section("queue-run", "No active run detail", "no current execution run is visible")
        else:
            self._update_section("queue-run", self._run_id, "press o for concise provenance detail")

        if not backlog:
            if queue.mailbox_task_intake_count > 0:
                detail = (
                    f"{queue.mailbox_task_intake_count} add-task request buffered in the mailbox "
                    "and not yet visible in backlog"
                )
            else:
                detail = (
                    "one task is active and nothing is queued behind it"
                    if queue.active_task is not None
                    else "no queued tasks are waiting"
                )
            self._set_backlog_content(headline="Backlog empty", detail=detail, focus="")
        else:
            live_order = {task.task_id: index for index, task in enumerate(self._backlog(), start=1)}
            selected = next((task for task in backlog if task.task_id == self._selected_task_id), None)
            focus = f"Focus: {selected.title}" if selected is not None else ""
            detail = (
                f"partial snapshot showing {len(backlog)} of {queue.backlog_depth}"
                if queue.backlog_depth != len(backlog)
                else "visible queue order"
            )
            self._render_backlog_table(backlog, live_order=live_order)
            self._set_backlog_content(
                headline=f"{len(backlog)} queued tasks",
                detail=detail,
                focus=focus,
            )

        if self._reorder_task_ids is None:
            self._update_section("queue-actions", "Up/Down select, Enter or r starts reorder", "live queue stays unchanged until confirmation")
            return
        live_order = {task.task_id: index for index, task in enumerate(self._backlog(), start=1)}
        changed_count = sum(
            1
            for index, task_id in enumerate(self._reorder_task_ids, start=1)
            if live_order.get(task_id) != index
        )
        draft_label = "no position changes yet" if changed_count == 0 else f"{changed_count} position changes pending"
        self._update_section("queue-actions", f"Draft reorder: {draft_label}", "[ earlier, ] later, Enter review, Esc cancel")

    def _update_metric(self, suffix: str, value: str, meta: str) -> None:
        self.query_one(f"#{suffix}-value", Static).update(value)
        self.query_one(f"#{suffix}-meta", Static).update(meta)

    def _update_section(self, suffix: str, headline: str, detail: str) -> None:
        self.query_one(f"#{suffix}-headline", Static).update(headline)
        self.query_one(f"#{suffix}-detail", Static).update(detail)

    def _update_status_card(self, headline: str, detail: str) -> None:
        self._update_section("queue-status", headline, detail)

    def _set_backlog_content(self, *, headline: str, detail: str, focus: str) -> None:
        self.query_one("#queue-list-headline", Static).update(headline)
        self.query_one("#queue-list-detail", Static).update(detail)
        self.query_one("#queue-list-focus", Static).update(focus)
        if self._queue is None or not self._visible_backlog():
            self._render_backlog_table((), live_order={})

    def _render_backlog_table(
        self,
        backlog: tuple[QueueTaskView, ...],
        *,
        live_order: dict[str, int],
    ) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.clear(columns=False)
        if not backlog:
            table.add_row("--", "No queued tasks", "", "", key="__queue-empty__")
            table.move_cursor(row=0, column=0, animate=False, scroll=False)
            return
        for index, task in enumerate(backlog, start=1):
            spec_label = task.spec_id or "none"
            live_index = live_order.get(task.task_id)
            draft_label = "live"
            if self._reorder_task_ids is not None:
                if live_index is not None and live_index != index:
                    draft_label = f"from {live_index}"
                else:
                    draft_label = "kept"
            table.add_row(
                str(index),
                task.title,
                spec_label,
                draft_label,
                key=task.task_id,
            )
        self._sync_backlog_table_cursor(scroll=False)

    def _sync_backlog_table_cursor(self, *, scroll: bool) -> None:
        table = self.query_one("#queue-table", DataTable)
        if self._selected_task_id is None:
            if table.row_count:
                table.move_cursor(row=0, column=0, animate=False, scroll=scroll)
            return
        try:
            row_index = table.get_row_index(self._selected_task_id)
        except Exception:
            if table.row_count:
                table.move_cursor(row=0, column=0, animate=False, scroll=scroll)
            return
        table.move_cursor(row=row_index, column=0, animate=False, scroll=scroll)

    def _update_focus_label(self) -> None:
        backlog = self._visible_backlog()
        selected = next((task for task in backlog if task.task_id == self._selected_task_id), None)
        focus = f"Focus: {selected.title}" if selected is not None else ""
        self.query_one("#queue-list-focus", Static).update(focus)

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
            panel_label="QUEUE",
            failure=self._failure,
            has_snapshot=self._queue is not None,
            display_mode=DisplayMode.OPERATOR,
        )
        if self._queue is None:
            lines.append("Waiting for the queue snapshot.")
            return "\n".join(lines)

        queue = self._queue
        backlog = self._visible_backlog()
        active_count = 1 if queue.active_task is not None else 0
        lines.append(
            "SUMMARY "
            f"active {active_count}"
            f" | next {'set' if queue.next_task is not None else 'none'}"
            f" | backlog {queue.backlog_depth}"
            f" | mailbox {queue.mailbox_task_intake_count}"
        )
        lines.append(_queue_card_label("ACTIVE", _task_operator_label(queue.active_task)))
        lines.append(_queue_card_label("NEXT", _task_operator_label(queue.next_task)))
        if queue.mailbox_task_intake_count > 0:
            title = queue.mailbox_task_intake_titles[0] if queue.mailbox_task_intake_titles else "buffered add-task request"
            lines.append(f"MAILBOX {queue.mailbox_task_intake_count} buffered | {title}")
        if self._run_id is not None:
            lines.append(f"RUN     {self._run_id} | o detail")
        if queue.backlog_depth != len(backlog):
            lines.append(f"LIST    partial snapshot showing {len(backlog)} of {queue.backlog_depth}")
        lines.append("")
        if not backlog:
            if queue.mailbox_task_intake_count > 0:
                lines.append(
                    f"BACKLOG empty | {queue.mailbox_task_intake_count} add-task request buffered in mailbox"
                )
            elif queue.active_task is not None:
                lines.append("BACKLOG empty | one task is active and nothing is queued behind it")
            else:
                lines.append("BACKLOG empty | no queued tasks are waiting")
            return "\n".join(lines)

        lines.append(f"BACKLOG {len(backlog)} visible")
        live_order = {task.task_id: index for index, task in enumerate(self._backlog(), start=1)}
        for index, task in enumerate(backlog, start=1):
            marker = ">" if task.task_id == self._selected_task_id else " "
            fragments = [f"{marker} {index:>2}.", task.title]
            if self._reorder_task_ids is not None:
                live_index = live_order.get(task.task_id)
                if live_index is not None and live_index != index:
                    fragments.append(f"(from {live_index})")
            lines.append(" ".join(fragment for fragment in fragments if fragment))
        selected = next((task for task in backlog if task.task_id == self._selected_task_id), None)
        if selected is not None:
            lines.append(f"FOCUS   {selected.title}")
        if self._reorder_task_ids is None:
            lines.append("NEXT    Up/Down select | Enter/r reorder")
            lines.append("SAFE    backlog stays unchanged until confirmation")
        else:
            changed_count = sum(
                1
                for index, task_id in enumerate(self._reorder_task_ids, start=1)
                if live_order.get(task_id) != index
            )
            lines.append(
                "DRAFT   "
                f"{'no position changes yet' if changed_count == 0 else f'{changed_count} position changes pending'}"
            )
            lines.append("NEXT    [ earlier | ] later | Enter review | Esc cancel")
            lines.append("SAFE    live queue remains unchanged until confirmation")
        return "\n".join(lines)

    def _render_debug_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="QUEUE",
            failure=self._failure,
            has_snapshot=self._queue is not None,
            display_mode=DisplayMode.DEBUG,
        )
        if self._queue is None:
            lines.append("Waiting for the queue snapshot.")
            return "\n".join(lines)

        queue = self._queue
        backlog = self._visible_backlog()
        active_count = 1 if queue.active_task is not None else 0
        lines.append(
            f"COUNTS  active {active_count} | backlog {queue.backlog_depth} | listed {len(backlog)} | mailbox {queue.mailbox_task_intake_count}"
        )
        lines.append(f"ACTIVE  {_task_label(queue.active_task)}")
        lines.append(f"NEXT    {_task_label(queue.next_task)}")
        if queue.mailbox_task_intake_count > 0:
            detail = queue.mailbox_task_intake_titles[0] if queue.mailbox_task_intake_titles else "buffered add-task request"
            lines.append(f"MAILBOX {queue.mailbox_task_intake_count} buffered | {detail}")
        if self._run_id is None:
            lines.append("RUN     none")
        else:
            lines.append(f"RUN     active {self._run_id} | press o for provenance detail")
        if queue.backlog_depth != len(backlog):
            lines.append(f"LIST    partial snapshot showing {len(backlog)} of {queue.backlog_depth}")
        lines.append("")
        if not backlog:
            if queue.mailbox_task_intake_count > 0:
                lines.append("BACKLOG empty. Accepted add-task work is still buffered in the mailbox.")
            elif queue.active_task is not None:
                lines.append("BACKLOG empty. One task is active and nothing is queued behind it.")
            else:
                lines.append("BACKLOG empty. No queued tasks are waiting.")
            return "\n".join(lines)

        if self._reorder_task_ids is None:
            lines.append("BACKLOG  Up/Down move selection. Enter or r starts a reorder draft.")
            lines.append("ACTIONS  Reorder stays draft-only until you review and confirm it.")
        else:
            lines.append("REORDER  Draft active. [ and ] move the selected task. Enter reviews. Esc cancels.")
            lines.append("ACTIONS  Live backlog is unchanged until the shell confirms and applies this draft.")
        for index, task in enumerate(backlog, start=1):
            prefix = ">" if task.task_id == self._selected_task_id else " "
            spec_fragment = f" | {task.spec_id}" if task.spec_id else ""
            lines.append(f"{prefix} {index:>2}. {task.title} [{task.task_id}{spec_fragment}]")
        selected = next((task for task in backlog if task.task_id == self._selected_task_id), None)
        if selected is not None:
            lines.append("")
            lines.append(f"SELECT  {_task_label(selected)}")
        return "\n".join(lines)


__all__ = ["QueuePanel"]
