"""Config visibility and controlled-edit panel for the Millrace TUI shell."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Static

from ..formatting import short_hash
from ..models import ConfigFieldView, ConfigOverviewView, DisplayMode, GatewayFailure, RuntimeOverviewView
from .progressive_disclosure import append_operator_debug_hint, append_panel_failure_lines, collapse_operator_text

_BOUNDARY_HELP = {
    "live_immediate": "takes effect on the next accepted reload without waiting for a stage or cycle",
    "stage_boundary": "waits for the current execution stage to finish before it applies",
    "cycle_boundary": "waits for the next execution or research cycle before it applies",
    "startup_only": "requires a restart boundary and stays read-only in this panel",
}
_BOUNDARY_CHIPS = {
    "live_immediate": "LIVE",
    "stage_boundary": "STAGE",
    "cycle_boundary": "CYCLE",
    "startup_only": "STARTUP",
}


def _boundary_help(boundary: str | None) -> str:
    if boundary is None:
        return "no deferred config changes are waiting in runtime state"
    return _BOUNDARY_HELP.get(boundary, boundary)


def _boundary_chip(boundary: str) -> str:
    return _BOUNDARY_CHIPS.get(boundary, boundary.upper())


def _field_state_class(field: ConfigFieldView) -> str:
    if field.boundary == "live_immediate":
        return "state-ok"
    if field.boundary == "startup_only":
        return "state-fail"
    return "state-warn"


def _pending_field_summary(runtime: RuntimeOverviewView | None, config: ConfigOverviewView) -> str:
    if runtime is None or not runtime.pending_config_fields:
        return "none queued"
    field_labels = {field.key: field.label for field in config.fields}
    labels = [field_labels.get(key, key) for key in runtime.pending_config_fields]
    preview = ", ".join(labels[:2])
    if len(labels) > 2:
        preview = f"{preview}, +{len(labels) - 2} more"
    return preview


class ConfigPanel(Static):
    """Focusable config panel with a controlled field selection model."""

    can_focus = True
    BINDINGS = (
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("home", "cursor_home", show=False),
        Binding("end", "cursor_end", show=False),
        Binding("e", "edit_selected", show=False),
        Binding("enter", "edit_selected", show=False),
        Binding("r", "reload_config", show=False),
    )

    class EditRequested(Message):
        """Posted when the operator wants to edit the currently selected field."""

        bubble = True

        def __init__(self, field_key: str) -> None:
            super().__init__()
            self.field_key = field_key

    class ReloadRequested(Message):
        """Posted when the operator wants to reload config from disk."""

        bubble = True

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, classes="panel-card", markup=False)
        self.border_title = "Config"
        self._config: ConfigOverviewView | None = None
        self._runtime: RuntimeOverviewView | None = None
        self._failure: GatewayFailure | None = None
        self._selected_field_key: str | None = None
        self._display_mode: DisplayMode = DisplayMode.OPERATOR

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="config-operator", id="config-mode-switcher"):
            with Vertical(id="config-operator", classes="panel-mode-body"):
                yield self._section_card("config-status", "Status")
                with Horizontal(classes="panel-metrics"):
                    yield self._metric_card("config-edits", "Edits")
                    yield self._metric_card("config-supported", "Supported")
                    yield self._metric_card("config-pending", "Pending")
                yield self._section_card("config-source", "Source")
                yield self._section_card("config-queue", "Change queue")
                with Vertical(id="config-fields-card", classes="overview-card panel-section-card panel-list-card"):
                    yield Static("Editable fields", classes="overview-card-label")
                    yield Static("--", id="config-fields-headline", classes="overview-card-headline")
                    yield Static("", id="config-fields-detail", classes="overview-card-detail")
                    yield Vertical(id="config-fields-items", classes="panel-item-stack")
                yield self._section_card("config-actions", "Actions")
            yield Static("", id="config-debug", classes="panel-debug-body")

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
    def _field_card(field: ConfigFieldView, *, index: int, selected: bool) -> Vertical:
        classes = f"overview-card panel-item-card {_field_state_class(field)}"
        if selected:
            classes += " is-selected"
        return Vertical(
            Static(f"{index:>2}. {field.label} = {field.value}", classes="panel-item-title"),
            Static(f"{_boundary_chip(field.boundary)} | {collapse_operator_text(field.description, max_parts=1, max_length=90)}", classes="panel-item-meta"),
            classes=classes,
        )

    @property
    def selected_field_key(self) -> str | None:
        return self._selected_field_key

    def on_mount(self) -> None:
        self._render_state()

    def show_snapshot(
        self,
        config: ConfigOverviewView | None,
        *,
        runtime: RuntimeOverviewView | None,
        failure: GatewayFailure | None = None,
        display_mode: DisplayMode = DisplayMode.OPERATOR,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._failure = failure
        self._display_mode = display_mode
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
        fields = self._editable_fields()
        if fields:
            self._select_index(len(fields) - 1)

    def action_edit_selected(self) -> None:
        if self._selected_field_key is None:
            return
        self.post_message(self.EditRequested(self._selected_field_key))

    def action_reload_config(self) -> None:
        self.post_message(self.ReloadRequested())

    def _editable_fields(self) -> tuple[ConfigFieldView, ...]:
        if self._config is None:
            return ()
        return tuple(field for field in self._config.fields if field.editable)

    def _reconcile_selection(self) -> None:
        fields = self._editable_fields()
        if not fields:
            self._selected_field_key = None
            return
        if self._selected_field_key in {field.key for field in fields}:
            return
        self._selected_field_key = fields[0].key

    def _selected_index(self) -> int | None:
        if self._selected_field_key is None:
            return None
        for index, field in enumerate(self._editable_fields()):
            if field.key == self._selected_field_key:
                return index
        return None

    def _move_selection(self, delta: int) -> None:
        fields = self._editable_fields()
        if not fields:
            return
        current_index = self._selected_index()
        if current_index is None:
            new_index = 0 if delta >= 0 else len(fields) - 1
        else:
            new_index = min(max(current_index + delta, 0), len(fields) - 1)
        self._select_index(new_index)

    def _select_index(self, index: int) -> None:
        fields = self._editable_fields()
        if not fields:
            return
        bounded_index = min(max(index, 0), len(fields) - 1)
        self._selected_field_key = fields[bounded_index].key
        if self.is_mounted:
            self._render_state()

    def _render_state(self) -> None:
        switcher = self.query_one("#config-mode-switcher", ContentSwitcher)
        switcher.current = "config-debug" if self._display_mode is DisplayMode.DEBUG else "config-operator"
        self.query_one("#config-debug", Static).update(self._render_debug_text())
        if self._display_mode is DisplayMode.DEBUG:
            return
        self._render_operator_cards()

    def _render_operator_cards(self) -> None:
        if self._config is None:
            self._update_section("config-status", "Waiting for the config snapshot.", self._failure_operator_detail(has_snapshot=False))
            self._update_metric("config-edits", "--", "edit state unavailable")
            self._update_metric("config-supported", "--", "guided field count unavailable")
            self._update_metric("config-pending", "--", "pending boundary unavailable")
            self._update_section("config-source", "No config snapshot", "config source appears after refresh")
            self._update_section("config-queue", "No pending changes", "runtime queue state will appear after refresh")
            self._set_field_items(headline="No editable fields visible", detail="config snapshot not loaded", items=())
            self._update_section("config-actions", "Waiting for config", "edit and reload controls appear when the config snapshot loads")
            return

        config = self._config
        runtime = self._runtime
        editable_fields = self._editable_fields()
        flow_label = (
            "daemon running | edit/reload requests queue through mailbox"
            if runtime is not None and runtime.process_running
            else "daemon stopped | edits write directly and reload immediately"
        )
        pending_boundary = runtime.pending_config_boundary if runtime is not None else None
        pending_summary = _pending_field_summary(runtime, config)

        self._update_section(
            "config-status",
            "Guided edits ready" if config.editing_enabled and editable_fields else "Guided edits limited",
            self._failure_operator_detail(has_snapshot=True)
            if self._failure is not None
            else flow_label,
        )
        self._update_metric("config-edits", "on" if config.editing_enabled and bool(editable_fields) else "off", flow_label)
        self._update_metric("config-supported", str(len(editable_fields)), "validated editable fields")
        self._update_metric("config-pending", pending_boundary or "none", pending_summary)
        self._update_section("config-source", f"{config.source_kind} | {config.source_ref}", f"bundle {config.bundle_version or 'unknown'} | guided fields {len(config.fields)}")
        self._update_section("config-queue", _boundary_help(pending_boundary), pending_summary)

        if editable_fields:
            items = tuple(
                self._field_card(field, index=index, selected=field.key == self._selected_field_key)
                for index, field in enumerate(editable_fields, start=1)
            )
            self._set_field_items(
                headline=f"{len(editable_fields)} guided fields",
                detail="selected field opens the controlled edit modal",
                items=items,
            )
        else:
            self._set_field_items(
                headline="No guided fields are editable",
                detail=config.editing_disabled_reason or "this config source is read-only in the guided editor",
                items=(),
            )

        if config.editing_enabled and editable_fields:
            action_detail = "Up/Down select | Enter/E edit selected | R reload config from disk"
        else:
            action_detail = (
                f"{config.editing_disabled_reason or 'guided edits are unavailable in this config source'} | R reload config from disk"
            )
        self._update_section("config-actions", "Guided config controls", action_detail)

    def _update_metric(self, suffix: str, value: str, meta: str) -> None:
        self.query_one(f"#{suffix}-value", Static).update(value)
        self.query_one(f"#{suffix}-meta", Static).update(meta)

    def _update_section(self, suffix: str, headline: str, detail: str) -> None:
        self.query_one(f"#{suffix}-headline", Static).update(headline)
        self.query_one(f"#{suffix}-detail", Static).update(detail)

    def _set_field_items(self, *, headline: str, detail: str, items: tuple[Widget, ...]) -> None:
        self.query_one("#config-fields-headline", Static).update(headline)
        self.query_one("#config-fields-detail", Static).update(detail)
        container = self.query_one("#config-fields-items", Vertical)
        container.remove_children()
        if items:
            for item in items:
                container.mount(item)
            return
        container.mount(
            Vertical(
                Static("No guided field rows", classes="panel-item-title"),
                Static(detail, classes="panel-item-meta"),
                classes="overview-card panel-item-card panel-empty-card",
            )
        )

    def _failure_operator_detail(self, *, has_snapshot: bool) -> str:
        if self._failure is None:
            return ""
        if has_snapshot:
            return collapse_operator_text(self._failure.message, max_parts=2, max_length=88)
        return "open debug once a config snapshot is available for deeper gateway detail"

    def _render_operator_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="CONFIG",
            failure=self._failure,
            has_snapshot=self._config is not None,
            display_mode=DisplayMode.OPERATOR,
        )
        if self._config is None:
            lines.append("Waiting for the config snapshot.")
            return "\n".join(lines)

        config = self._config
        runtime = self._runtime
        editable_fields = self._editable_fields()
        flow_label = (
            "daemon running | edit/reload requests queue through mailbox"
            if runtime is not None and runtime.process_running
            else "daemon stopped | edits write directly and reload immediately"
        )
        pending_boundary = runtime.pending_config_boundary if runtime is not None else None
        pending_summary = _pending_field_summary(runtime, config)
        supported_count = len(editable_fields)

        lines.append(
            "STATUS  "
            f"edits {'on' if config.editing_enabled and bool(editable_fields) else 'off'}"
            f" | supported {supported_count}"
            f" | pending {pending_boundary or 'none'}"
        )
        lines.append(f"FLOW    {flow_label}")
        lines.append(
            "SOURCE  "
            f"{config.source_kind}"
            f" | {config.source_ref}"
        )
        lines.append(
            "CHANGE  "
            f"{_boundary_help(pending_boundary)}"
        )
        lines.append(
            "QUEUE   "
            f"{pending_summary}"
        )

        lines.append("")
        lines.append("EDITABLE")
        if editable_fields:
            for index, field in enumerate(editable_fields, start=1):
                prefix = ">" if field.key == self._selected_field_key else " "
                lines.append(
                    f"{prefix} {index:>2}. {field.label} = {field.value} [{_boundary_chip(field.boundary)}]"
                )
                lines.append(f"    {collapse_operator_text(field.description, max_parts=1, max_length=72)}")
        else:
            lines.append("- no guided fields are editable in this config source")

        lines.append("")
        lines.append("BOUNDARY [LIVE] now | [STAGE] after stage | [CYCLE] next cycle | [STARTUP] restart only")

        lines.append("")
        if config.editing_enabled and editable_fields:
            lines.append("NEXT    Up/Down select field | Enter/E edit selected | R reload config from disk")
        else:
            lines.append(
                "NEXT    "
                f"{config.editing_disabled_reason or 'guided edits are unavailable in this config source'}"
                " | R reload config from disk"
            )
        append_operator_debug_hint(lines, detail_hint="open debug for hashes, startup-only fields, and raw keys")
        return "\n".join(lines)

    def _render_debug_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="CONFIG",
            failure=self._failure,
            has_snapshot=self._config is not None,
            display_mode=DisplayMode.DEBUG,
        )
        if self._config is None:
            lines.append("Waiting for the config snapshot.")
            return "\n".join(lines)

        config = self._config
        runtime = self._runtime
        flow_label = (
            "daemon running | edits and reload queue through the mailbox first"
            if runtime is not None and runtime.process_running
            else "daemon stopped | edits write directly and reload immediately"
        )
        pending_boundary = runtime.pending_config_boundary if runtime is not None else None
        pending_fields = (
            ", ".join(runtime.pending_config_fields)
            if runtime is not None and runtime.pending_config_fields
            else "none"
        )

        lines.append(
            "SOURCE  "
            f"{config.source_kind}"
            f" | {config.source_ref}"
            f" | hash {short_hash(config.config_hash)}"
        )
        lines.append(
            "FLOW    "
            f"{flow_label}"
            f" | bundle {config.bundle_version or 'unknown'}"
        )
        lines.append(
            "PENDING "
            f"{pending_boundary or 'none'}"
            f" | fields {pending_fields}"
            f" | {_boundary_help(pending_boundary)}"
        )
        if runtime is not None:
            lines.append(
                "ROLLBK  "
                f"{'armed' if runtime.rollback_armed else 'clear'}"
                f" | previous {short_hash(runtime.previous_config_hash)}"
                f" | pending {short_hash(runtime.pending_config_hash)}"
            )

        lines.append("")
        lines.append("SUPPORTED")
        lines.append("This panel intentionally limits edits to validated fields with clear runtime boundaries.")
        for index, field in enumerate(config.fields, start=1):
            prefix = ">" if field.key == self._selected_field_key else " "
            edit_marker = "editable" if field.editable else "read-only"
            lines.append(
                f"{prefix} {index:>2}. {field.label} [{field.key}] = {field.value} | {field.boundary} | {edit_marker}"
            )
            lines.append(f"    {field.description}")

        lines.append("")
        lines.append("STARTUP")
        for field in config.startup_only_fields:
            lines.append(f"- {field.label} [{field.key}] = {field.value}")
            lines.append(f"  {field.description}")

        lines.append("")
        lines.append("BOUNDARY")
        for boundary, explanation in _BOUNDARY_HELP.items():
            lines.append(f"- {boundary}: {explanation}")

        lines.append("")
        if config.editing_enabled and self._editable_fields():
            lines.append("DETAIL  Up/Down choose a supported field. Enter/E edits. R reloads config from disk.")
        else:
            lines.append(
                "DETAIL  "
                f"{config.editing_disabled_reason or 'guided edits are unavailable in this config source'}"
                " | R reloads config from disk."
            )
        return "\n".join(lines)


__all__ = ["ConfigPanel"]
