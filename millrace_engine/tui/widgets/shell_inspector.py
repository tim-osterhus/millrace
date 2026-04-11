"""Persistent shell inspector for panel context and selection detail."""

from __future__ import annotations

from dataclasses import dataclass

from textual.widgets import Static


@dataclass(frozen=True, slots=True)
class ShellInspectorAction:
    """A discoverable action rendered inside the shell inspector."""

    key: str
    label: str
    detail: str


@dataclass(frozen=True, slots=True)
class ShellInspectorView:
    """Rendered shell-owned inspector state for the active panel."""

    panel_label: str
    title: str
    headline: str
    detail_lines: tuple[str, ...]
    primary_action: ShellInspectorAction | None = None
    action_lines: tuple[ShellInspectorAction, ...] = ()
    collapsed: bool = False


def render_shell_inspector(view: ShellInspectorView) -> str:
    lines = [
        f"PANEL   {view.panel_label}",
        f"FOCUS   {view.title}",
        f"STATE   {view.headline}",
    ]
    if view.detail_lines:
        lines.append("")
        lines.append("DETAIL")
        lines.extend(f"- {line}" for line in view.detail_lines)
    if view.primary_action is not None:
        lines.append("")
        lines.append("PRIMARY")
        lines.append(_render_action_line(view.primary_action))
    if view.action_lines:
        lines.append("")
        lines.append("NEXT")
        lines.extend(_render_action_line(action) for action in view.action_lines)
    return "\n".join(lines)


def _render_action_line(action: ShellInspectorAction) -> str:
    return f"- [{action.key}] {action.label}: {action.detail}"


class ShellInspector(Static):
    """Read-only inspector that stays mounted across panel changes."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, classes="shell-inspector", markup=False)
        self.border_title = "Inspector"

    def show_view(self, view: ShellInspectorView) -> None:
        if view.collapsed:
            self.add_class("is-collapsed")
        else:
            self.remove_class("is-collapsed")
        self.border_subtitle = view.panel_label
        self.update(render_shell_inspector(view))


__all__ = [
    "ShellInspector",
    "ShellInspectorAction",
    "ShellInspectorView",
    "render_shell_inspector",
]
