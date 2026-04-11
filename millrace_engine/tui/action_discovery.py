"""Shared discovery copy for the shell footer, help modal, and palette."""

from __future__ import annotations

from dataclasses import dataclass

from .models import PANEL_BY_ID, PanelId


@dataclass(frozen=True, slots=True)
class ActionHint:
    """A readable action label plus the matching help sentence."""

    key: str
    label: str
    help_text: str


@dataclass(frozen=True, slots=True)
class ShellActionSurface:
    """The current shell discovery surface."""

    global_actions: tuple[ActionHint, ...]
    context_title: str
    context_actions: tuple[ActionHint, ...]


_GLOBAL_ACTIONS = (
    ActionHint("1-7", "Panels", "1-7 switches between the main panels."),
    ActionHint(
        "Ctrl+P",
        "Palette",
        "Ctrl+P opens the action palette for lifecycle, publish, config, and contextual shell actions.",
    ),
    ActionHint("T", "Task", "T opens Add Task."),
    ActionHint("I", "Idea", "I opens Add Idea."),
    ActionHint("?", "Help", "? opens or closes this help."),
)


def build_shell_action_surface(
    *,
    active_panel: PanelId,
    focus_zone: str,
    expanded_mode: bool,
    queue_reorder_mode: bool = False,
    logs_follow_mode: bool = True,
    logs_selected_run_id: str | None = None,
    research_has_question: bool = False,
    config_has_field: bool = False,
    publish_commit_allowed: bool = False,
    publish_push_ready: bool = False,
    publish_has_changes: bool = False,
    publish_git_worktree_valid: bool = False,
) -> ShellActionSurface:
    """Return the readable shell actions for the current context."""

    panel_label = PANEL_BY_ID[active_panel].label
    zone = "workspace" if focus_zone == "workspace" else "sidebar"

    if expanded_mode:
        if zone == "sidebar":
            return ShellActionSurface(
                global_actions=_GLOBAL_ACTIONS,
                context_title=f"{panel_label} expanded sidebar",
                context_actions=(
                    ActionHint("C", "Stream", "C moves focus from the sidebar back to the expanded stream."),
                    ActionHint("Esc", "Return", "Escape returns from expanded stream mode to the active panel."),
                    ActionHint("D", "Mode", "D toggles between operator and debug views."),
                ),
            )
        return ShellActionSurface(
            global_actions=_GLOBAL_ACTIONS,
            context_title=f"{panel_label} expanded workspace",
            context_actions=(
                ActionHint("Esc", "Return", "Escape returns from expanded stream mode to the active panel."),
                ActionHint("L", "Jump live", "L jumps the expanded stream back to the live tail."),
                ActionHint("S", "Sidebar", "S moves focus from the expanded stream back to the sidebar."),
                ActionHint("D", "Mode", "D toggles between operator and debug views."),
            ),
        )

    if zone == "sidebar":
        return ShellActionSurface(
            global_actions=_GLOBAL_ACTIONS,
            context_title=f"{panel_label} sidebar",
            context_actions=(
                ActionHint("Enter", "Open panel", "Enter reopens the highlighted sidebar panel."),
                ActionHint("C", "Workspace", "C moves focus from the sidebar into the active workspace."),
                ActionHint("D", "Mode", "D toggles between operator and debug views."),
                ActionHint("E", "Expanded", "E opens the expanded stream for the active panel."),
            ),
        )

    if active_panel is PanelId.QUEUE:
        if queue_reorder_mode:
            context_actions = (
                ActionHint("Enter", "Review reorder", "Enter reviews the staged queue reorder."),
                ActionHint("[ / ]", "Move task", "[ and ] move the selected task through the reorder draft."),
                ActionHint("Esc", "Cancel", "Escape cancels the active queue reorder draft."),
                ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
            )
        else:
            context_actions = (
                ActionHint("R", "Reorder", "R starts a queue reorder draft for the selected task."),
                ActionHint("Enter", "Review", "Enter reviews the selected queue item or staged reorder."),
                ActionHint("O", "Run detail", "O opens concise run detail for the active run context."),
                ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
            )
    elif active_panel is PanelId.RUNS:
        context_actions = (
            ActionHint("Enter", "Run detail", "Enter opens concise detail for the selected run."),
            ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
        )
    elif active_panel is PanelId.RESEARCH:
        context_actions = (
            ActionHint(
                "Enter",
                "Interview" if research_has_question else "No interview",
                (
                    "Enter opens the selected interview workflow."
                    if research_has_question
                    else "No interview action is available until a pending question is selected."
                ),
            ),
            ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
        )
    elif active_panel is PanelId.LOGS:
        context_actions = (
            ActionHint(
                "F",
                "Freeze" if logs_follow_mode else "Resume live",
                (
                    "F freezes the live log tail so you can browse older events."
                    if logs_follow_mode
                    else "F resumes the live log tail after scrollback review."
                ),
            ),
            ActionHint("Tab", "Events/artifacts", "Tab switches the logs workspace between events and artifacts."),
            ActionHint(
                "S" if logs_selected_run_id is None else "Enter",
                "Sidebar" if logs_selected_run_id is None else "Run detail",
                (
                    "S moves focus back to the sidebar."
                    if logs_selected_run_id is None
                    else "Enter opens concise run detail for the selected log event."
                ),
            ),
        )
    elif active_panel is PanelId.CONFIG:
        context_actions = (
            ActionHint(
                "Enter",
                "Edit field" if config_has_field else "No edit",
                (
                    "Enter opens guided editing for the selected config field."
                    if config_has_field
                    else "No editable config field is currently selected."
                ),
            ),
            ActionHint("R", "Reload", "R reloads config through the supported control path."),
            ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
        )
    elif active_panel is PanelId.PUBLISH:
        if not publish_commit_allowed:
            if publish_has_changes and not publish_git_worktree_valid:
                context_actions = (
                    ActionHint("R", "Refresh", "R reloads publish preflight facts after you repair staging git state."),
                    ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
                )
            elif not publish_has_changes:
                context_actions = (
                    ActionHint("G", "Sync", "G syncs manifest-selected files into staging if you expected diffs."),
                    ActionHint("R", "Refresh", "R refreshes read-only publish facts."),
                    ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
                )
            else:
                context_actions = (
                    ActionHint("R", "Refresh", "R refreshes read-only publish facts."),
                    ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
                )
        elif not publish_push_ready:
            context_actions = (
                ActionHint("N", "Commit locally", "N opens the safer local-only staging commit flow."),
                ActionHint("R", "Refresh", "R refreshes publish facts after you fix origin or branch prerequisites."),
                ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
            )
        else:
            context_actions = (
                ActionHint("N", "Commit locally", "N opens the safer local-only staging commit flow."),
                ActionHint("P", "Commit and push", "P opens the higher-friction commit-and-push flow."),
                ActionHint("R", "Refresh", "R refreshes publish preflight facts."),
                ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
            )
    else:
        context_actions = (
            ActionHint("D", "Mode", "D toggles between operator and debug views."),
            ActionHint("E", "Expanded", "E opens the expanded stream for the active panel."),
            ActionHint("S", "Sidebar", "S moves focus back to the sidebar."),
        )

    return ShellActionSurface(
        global_actions=_GLOBAL_ACTIONS,
        context_title=f"{panel_label} workspace",
        context_actions=context_actions,
    )


__all__ = ["ActionHint", "ShellActionSurface", "build_shell_action_surface"]
