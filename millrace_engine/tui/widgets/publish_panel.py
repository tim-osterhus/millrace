"""Publish preflight and deliberate staging actions for the Millrace TUI shell."""

from __future__ import annotations

from pathlib import PurePosixPath

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import ContentSwitcher, Static, Tree

from ..models import DisplayMode, GatewayFailure, PublishOverviewView
from .progressive_disclosure import append_operator_debug_hint, append_panel_failure_lines

_SKIP_REASON_COPY = {
    "missing_git_worktree": "staging repo is missing a git worktree",
    "invalid_git_worktree": "staging repo is not a valid git worktree",
    "no_changes": "no git working tree changes are waiting",
    "missing_origin": "origin is not configured for the staging repo",
    "detached_head": "staging repo HEAD is detached",
    "push_disabled": "preflight stayed local so push was intentionally skipped",
}
_DISPLAY_PATH_LIMIT = 6
_OPERATOR_PATH_LIMIT = 3


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _skip_reason_copy(reason: str | None) -> str:
    if reason is None:
        return "none"
    return _SKIP_REASON_COPY.get(reason, reason)


def _operator_blocked_reason(publish: PublishOverviewView) -> str | None:
    if publish.status != "skip_publish":
        return None
    if publish.skip_reason is None:
        return "publish preflight reported a blocked state"
    return _skip_reason_copy(publish.skip_reason)


def _operator_status_copy(publish: PublishOverviewView) -> str:
    blocked_reason = _operator_blocked_reason(publish)
    if blocked_reason is not None:
        return f"blocked | {blocked_reason}"
    if publish.commit_allowed:
        return "ready | commit path available"
    if publish.has_changes:
        return "attention | refresh preflight before commit"
    return "idle | no publishable changes yet"


def _path_state_class(publish: PublishOverviewView) -> str:
    if _operator_blocked_reason(publish) is not None:
        return "state-fail"
    if publish.commit_allowed:
        return "state-ok"
    return "state-warn"


class PublishPanel(Static):
    """Focusable publish panel with explicit sync, preflight, and commit intents."""

    can_focus = True
    BINDINGS = (
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("home", "cursor_home", show=False),
        Binding("end", "cursor_end", show=False),
        Binding("g", "sync_staging", show=False),
        Binding("r", "refresh_preflight", show=False),
        Binding("n", "commit_local", show=False),
        Binding("p", "commit_and_push", show=False),
    )

    class SyncRequested(Message):
        """Posted when the operator wants a staging sync."""

        bubble = True

    class PreflightRequested(Message):
        """Posted when the operator wants a fresh publish preflight."""

        bubble = True

    class CommitRequested(Message):
        """Posted when the operator wants a local or push publish commit flow."""

        bubble = True

        def __init__(self, *, push: bool) -> None:
            super().__init__()
            self.push = push

    class SelectionChanged(Message):
        """Posted when the highlighted publish path changes."""

        bubble = True

        def __init__(self, path: str | None) -> None:
            super().__init__()
            self.path = path

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, classes="panel-card", markup=False)
        self.border_title = "Publish"
        self._publish: PublishOverviewView | None = None
        self._failure: GatewayFailure | None = None
        self._display_mode: DisplayMode = DisplayMode.OPERATOR
        self._selected_path: str | None = None

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="publish-operator", id="publish-mode-switcher"):
            with Vertical(id="publish-operator", classes="panel-mode-body"):
                yield self._section_card("publish-status", "Status")
                with Horizontal(classes="panel-metrics"):
                    yield self._metric_card("publish-ready", "Ready")
                    yield self._metric_card("publish-push", "Push")
                    yield self._metric_card("publish-changed", "Changed")
                yield self._section_card("publish-repo", "Staging repo")
                yield self._section_card("publish-health", "Health")
                with Vertical(id="publish-paths-card", classes="overview-card panel-section-card panel-list-card"):
                    yield Static("Changed paths", classes="overview-card-label")
                    yield Static("--", id="publish-paths-headline", classes="overview-card-headline")
                    yield Static("", id="publish-paths-detail", classes="overview-card-detail")
                    yield Tree("Changed paths", id="publish-paths-tree", classes="panel-tree")
                    yield Static("", id="publish-paths-focus", classes="overview-card-detail")
                yield self._section_card("publish-actions", "Actions")
            yield Static("", id="publish-debug", classes="panel-debug-body")

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
    def _node_detail(path: str, *, publish: PublishOverviewView) -> str:
        selected = "tracked by manifest" if path in publish.selected_paths else "changed outside manifest selection"
        return f"{path} | {selected} | branch {publish.branch or 'detached'}"

    def on_mount(self) -> None:
        tree = self.query_one("#publish-paths-tree", Tree)
        tree.show_root = False
        tree.root.expand()
        self._render_state()

    @property
    def selected_path(self) -> str | None:
        return self._selected_path

    def show_snapshot(
        self,
        publish: PublishOverviewView | None,
        *,
        failure: GatewayFailure | None = None,
        display_mode: DisplayMode = DisplayMode.OPERATOR,
    ) -> None:
        self._publish = publish
        self._failure = failure
        self._display_mode = display_mode
        if self.is_mounted:
            self._render_state()

    def summary_text(self) -> str:
        if self._display_mode is DisplayMode.DEBUG:
            return self._render_debug_text()
        return self._render_operator_text()

    @on(Tree.NodeHighlighted, "#publish-paths-tree")
    def _handle_path_tree_highlighted(self, event: Tree.NodeHighlighted) -> None:
        path = self._path_from_tree_data(event.node.data)
        if path != self._selected_path:
            self._selected_path = path
            self.post_message(self.SelectionChanged(path))
        self._update_focus_label(event.node.data)

    @on(Tree.NodeSelected, "#publish-paths-tree")
    def _handle_path_tree_selected(self, event: Tree.NodeSelected) -> None:
        path = self._path_from_tree_data(event.node.data)
        if path != self._selected_path:
            self._selected_path = path
            self.post_message(self.SelectionChanged(path))
        self._update_focus_label(event.node.data)

    def action_cursor_up(self) -> None:
        self.query_one("#publish-paths-tree", Tree).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#publish-paths-tree", Tree).action_cursor_down()

    def action_cursor_home(self) -> None:
        self.query_one("#publish-paths-tree", Tree).action_scroll_home()

    def action_cursor_end(self) -> None:
        self.query_one("#publish-paths-tree", Tree).action_scroll_end()

    def action_sync_staging(self) -> None:
        self.post_message(self.SyncRequested())

    def action_refresh_preflight(self) -> None:
        self.post_message(self.PreflightRequested())

    def action_commit_local(self) -> None:
        self.post_message(self.CommitRequested(push=False))

    def action_commit_and_push(self) -> None:
        self.post_message(self.CommitRequested(push=True))

    def _render_state(self) -> None:
        switcher = self.query_one("#publish-mode-switcher", ContentSwitcher)
        switcher.current = "publish-debug" if self._display_mode is DisplayMode.DEBUG else "publish-operator"
        self.query_one("#publish-debug", Static).update(self._render_debug_text())
        if self._display_mode is DisplayMode.DEBUG:
            return
        self._render_operator_cards()

    def _render_operator_cards(self) -> None:
        if self._publish is None:
            self._update_section("publish-status", "Waiting for the publish preflight.", self._failure_operator_detail(has_snapshot=False))
            self._update_metric("publish-ready", "--", "commit readiness unavailable")
            self._update_metric("publish-push", "--", "push readiness unavailable")
            self._update_metric("publish-changed", "--", "changed path counts unavailable")
            self._update_section("publish-repo", "No staging snapshot", "staging repo context appears after preflight")
            self._update_section("publish-health", "No health snapshot", "git worktree checks appear after preflight")
            self._set_path_items(headline="No changed paths visible", detail="publish preflight not loaded")
            self._render_path_tree(())
            self._update_section("publish-actions", "Waiting for publish status", "sync, preflight, and commit actions appear after refresh")
            return

        publish = self._publish
        push_ready = publish.commit_allowed and publish.origin_configured and publish.branch is not None
        blocked_reason = _operator_blocked_reason(publish)

        self._update_section(
            "publish-status",
            _operator_status_copy(publish),
            self._failure_operator_detail(has_snapshot=True)
            if self._failure is not None
            else ("publish is blocked until staging health is fixed" if blocked_reason is not None else "staging readiness is current"),
        )
        self._update_metric("publish-ready", "yes" if publish.commit_allowed else "no", f"branch {publish.branch or 'detached'}")
        self._update_metric("publish-push", "yes" if push_ready else "no", "origin configured" if publish.origin_configured else "origin missing")
        self._update_metric("publish-changed", str(len(publish.changed_paths)), f"selected {len(publish.selected_paths)} manifest paths")
        self._update_section(
            "publish-repo",
            publish.staging_repo_dir,
            f"manifest {publish.manifest_source_kind} | branch {publish.branch or 'detached'}",
        )
        self._update_section(
            "publish-health",
            f"worktree {_yes_no(publish.git_worktree_present)} | valid {_yes_no(publish.git_worktree_valid)} | origin {_yes_no(publish.origin_configured)}",
            blocked_reason or "preflight is truthful and read-only until sync or commit is requested",
        )

        if publish.changed_paths:
            detail = (
                f"{len(publish.changed_paths)} changed paths | +{len(publish.changed_paths) - _OPERATOR_PATH_LIMIT} more"
                if len(publish.changed_paths) > _OPERATOR_PATH_LIMIT
                else f"{len(publish.changed_paths)} changed paths"
            )
            self._set_path_items(headline="Staging worktree changes", detail=detail)
            self._render_path_tree(publish.changed_paths)
        else:
            self._set_path_items(headline="No staging worktree changes detected", detail="sync staging or refresh preflight to re-check readiness")
            self._render_path_tree(())

        if publish.commit_allowed:
            action_detail = "N commit locally | P commit and push | G sync staging | R refresh preflight"
        else:
            action_detail = "G sync staging, then R refresh preflight to re-check readiness"
        self._update_section("publish-actions", "Publish controls", action_detail)

    def _update_metric(self, suffix: str, value: str, meta: str) -> None:
        self.query_one(f"#{suffix}-value", Static).update(value)
        self.query_one(f"#{suffix}-meta", Static).update(meta)

    def _update_section(self, suffix: str, headline: str, detail: str) -> None:
        self.query_one(f"#{suffix}-headline", Static).update(headline)
        self.query_one(f"#{suffix}-detail", Static).update(detail)

    def _set_path_items(self, *, headline: str, detail: str) -> None:
        self.query_one("#publish-paths-headline", Static).update(headline)
        self.query_one("#publish-paths-detail", Static).update(detail)
        self.query_one("#publish-paths-focus", Static).update(detail)

    def _render_path_tree(self, paths: tuple[str, ...]) -> None:
        tree = self.query_one("#publish-paths-tree", Tree)
        tree.reset("Changed paths")
        tree.show_root = False
        root = tree.root
        root.expand()
        if self._publish is None or not paths:
            leaf = root.add_leaf(
                "No staging worktree changes detected",
                data=("empty", None, "sync staging or refresh preflight to re-check readiness"),
            )
            tree.select_node(leaf)
            self._selected_path = None
            self.query_one("#publish-paths-focus", Static).update("Sync staging or refresh preflight to inspect changed paths.")
            return

        nodes_by_prefix = {"": root}
        leaf_by_path: dict[str, object] = {}
        for path in paths:
            parent = root
            prefix_parts: list[str] = []
            parts = PurePosixPath(path).parts
            for index, part in enumerate(parts):
                prefix_parts.append(part)
                prefix = "/".join(prefix_parts)
                existing = nodes_by_prefix.get(prefix)
                if existing is not None:
                    parent = existing
                    continue
                is_leaf = index == len(parts) - 1
                node = (
                    parent.add_leaf(part, data=("path", path, self._node_detail(path, publish=self._publish)))
                    if is_leaf
                    else parent.add(part, data=("group", None, f"{prefix}/"), expand=True)
                )
                nodes_by_prefix[prefix] = node
                parent = node
                if is_leaf:
                    leaf_by_path[path] = node

        selected_path = self._selected_path if self._selected_path in leaf_by_path else paths[0]
        self._selected_path = selected_path
        selected_node = leaf_by_path[selected_path]
        tree.select_node(selected_node)
        self._update_focus_label(("path", selected_path, self._node_detail(selected_path, publish=self._publish)))

    @staticmethod
    def _path_from_tree_data(data: object) -> str | None:
        if not isinstance(data, tuple) or len(data) != 3:
            return None
        _, path, _ = data
        return path if isinstance(path, str) else None

    def _update_focus_label(self, data: object) -> None:
        label = "Use Up/Down to inspect changed staging paths."
        if isinstance(data, tuple) and len(data) == 3:
            _, path, detail = data
            label = detail if path is not None else detail
        self.query_one("#publish-paths-focus", Static).update(label)

    def _failure_operator_detail(self, *, has_snapshot: bool) -> str:
        if self._failure is None:
            return ""
        if has_snapshot:
            return _skip_reason_copy(self._failure.message)
        return "open debug once publish preflight is available for deeper gateway detail"

    def _render_operator_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="PUBLISH",
            failure=self._failure,
            has_snapshot=self._publish is not None,
            display_mode=DisplayMode.OPERATOR,
        )
        if self._publish is None:
            lines.append("Waiting for the publish preflight.")
            return "\n".join(lines)

        publish = self._publish
        push_ready = publish.commit_allowed and publish.origin_configured and publish.branch is not None
        blocked_reason = _operator_blocked_reason(publish)
        lines.append(
            "STATUS  "
            f"{_operator_status_copy(publish)}"
            f" | changed {len(publish.changed_paths)}"
        )
        lines.append(
            "READY   "
            f"commit {_yes_no(publish.commit_allowed)}"
            f" | push-ready {_yes_no(push_ready)}"
            f" | branch {publish.branch or 'detached'}"
        )
        lines.append(
            "REPO    "
            f"{publish.staging_repo_dir}"
            f" | manifest {publish.manifest_source_kind}"
            f" | selected {len(publish.selected_paths)}"
        )
        lines.append(
            "HEALTH  "
            f"worktree {_yes_no(publish.git_worktree_present)}"
            f" | valid {_yes_no(publish.git_worktree_valid)}"
            f" | origin {_yes_no(publish.origin_configured)}"
        )
        lines.append(
            "NEXT    "
            + (
                "N commit locally (default safe path) | P commit and push (higher friction)"
                if publish.commit_allowed
                else "G sync staging, then R refresh preflight to re-check readiness"
            )
        )

        if blocked_reason is not None:
            lines.append("ALERT   publish is blocked until staging health is fixed")

        lines.append("")
        lines.append("CHANGED")
        if publish.changed_paths:
            for path in publish.changed_paths[:_OPERATOR_PATH_LIMIT]:
                lines.append(f"- {path}")
            if len(publish.changed_paths) > _OPERATOR_PATH_LIMIT:
                lines.append(f"- ... {len(publish.changed_paths) - _OPERATOR_PATH_LIMIT} more changed paths")
        else:
            lines.append("- no git working tree changes detected")

        lines.append("")
        lines.append("DETAIL  G sync manifest paths into staging | R refreshes read-only preflight")
        append_operator_debug_hint(lines, detail_hint="open debug for raw status, manifest refs, and full path lists")
        return "\n".join(lines)

    def _render_debug_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="PUBLISH",
            failure=self._failure,
            has_snapshot=self._publish is not None,
            display_mode=DisplayMode.DEBUG,
        )
        if self._publish is None:
            lines.append("Waiting for the publish preflight.")
            return "\n".join(lines)

        publish = self._publish
        push_ready = publish.commit_allowed and publish.origin_configured and publish.branch is not None
        lines.append(
            "STATUS  "
            f"{publish.status}"
            f" | commit {_yes_no(publish.commit_allowed)}"
            f" | push-ready {_yes_no(push_ready)}"
            f" | changed {len(publish.changed_paths)}"
        )
        lines.append(
            "REPO    "
            f"{publish.staging_repo_dir}"
            f" | branch {publish.branch or 'detached'}"
            f" | origin {_yes_no(publish.origin_configured)}"
        )
        lines.append(
            "MANIFST "
            f"{publish.manifest_source_kind}"
            f" | {publish.manifest_source_ref}"
            f" | version {publish.manifest_version}"
            f" | selected {len(publish.selected_paths)}"
        )
        lines.append(
            "GIT     "
            f"worktree {_yes_no(publish.git_worktree_present)}"
            f" | valid {_yes_no(publish.git_worktree_valid)}"
            f" | has changes {_yes_no(publish.has_changes)}"
        )
        lines.append(
            "COMMIT  "
            f"default local commit"
            f" | message {publish.commit_message}"
            f" | push requested {_yes_no(publish.push_requested)}"
        )
        lines.append(
            "SKIP    "
            f"{_skip_reason_copy(publish.skip_reason)}"
            f" | publish_allowed {_yes_no(publish.publish_allowed)}"
        )

        lines.append("")
        lines.append("SELECTED")
        for path in publish.selected_paths[:_DISPLAY_PATH_LIMIT]:
            lines.append(f"- {path}")
        if len(publish.selected_paths) > _DISPLAY_PATH_LIMIT:
            lines.append(f"- ... {len(publish.selected_paths) - _DISPLAY_PATH_LIMIT} more manifest paths")

        lines.append("")
        lines.append("CHANGED")
        if publish.changed_paths:
            for path in publish.changed_paths[:_DISPLAY_PATH_LIMIT]:
                lines.append(f"- {path}")
            if len(publish.changed_paths) > _DISPLAY_PATH_LIMIT:
                lines.append(f"- ... {len(publish.changed_paths) - _DISPLAY_PATH_LIMIT} more changed paths")
        else:
            lines.append("- no git working tree changes detected")

        lines.append("")
        lines.append("DETAIL  G sync manifest paths into staging. R refreshes read-only preflight.")
        lines.append("DETAIL  N creates the default no-push commit. P is the higher-friction commit-and-push path.")
        return "\n".join(lines)


__all__ = ["PublishPanel"]
