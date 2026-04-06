"""Shell-structure view models re-exported by tui.models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PanelId(StrEnum):
    OVERVIEW = "overview"
    QUEUE = "queue"
    RUNS = "runs"
    RESEARCH = "research"
    LOGS = "logs"
    CONFIG = "config"
    PUBLISH = "publish"


class DisplayMode(StrEnum):
    OPERATOR = "operator"
    DEBUG = "debug"


class ShellBodyMode(StrEnum):
    COMPACT = "compact"
    EXPANDED = "expanded"


def toggle_display_mode(mode: DisplayMode) -> DisplayMode:
    if mode is DisplayMode.OPERATOR:
        return DisplayMode.DEBUG
    return DisplayMode.OPERATOR


def toggle_shell_body_mode(mode: ShellBodyMode) -> ShellBodyMode:
    if mode is ShellBodyMode.COMPACT:
        return ShellBodyMode.EXPANDED
    return ShellBodyMode.COMPACT


@dataclass(frozen=True, slots=True)
class PanelDefinition:
    id: PanelId
    label: str
    summary: str
    placeholder_body: str
    palette_help: str


PANELS: tuple[PanelDefinition, ...] = (
    PanelDefinition(
        id=PanelId.OVERVIEW,
        label="Overview",
        summary="Workspace shell, status landmarks, and later runtime summary widgets.",
        placeholder_body=(
            "The overview will host workspace health and the top-level runtime "
            "summary in later runs."
        ),
        palette_help="Open the shell summary panel.",
    ),
    PanelDefinition(
        id=PanelId.QUEUE,
        label="Queue",
        summary="Execution backlog, active work, and queue actions arrive in later runs.",
        placeholder_body=(
            "Queue visibility and queue actions are staged for later runs once "
            "gateway models exist."
        ),
        palette_help="Open active and backlog queue state.",
    ),
    PanelDefinition(
        id=PanelId.RUNS,
        label="Runs",
        summary="Recent execution runs, frozen-plan summaries, and concise provenance drilldown.",
        placeholder_body="Run provenance is rendered through the dedicated runs panel widget.",
        palette_help="Open recent runs and provenance summary.",
    ),
    PanelDefinition(
        id=PanelId.RESEARCH,
        label="Research",
        summary="Research queue, reports, and history views land after the gateway layer exists.",
        placeholder_body=(
            "Research visibility is intentionally placeholder-only in the shell foundation."
        ),
        palette_help="Open research queue and governance summary.",
    ),
    PanelDefinition(
        id=PanelId.LOGS,
        label="Logs",
        summary="Structured event streaming and log tailing arrive in later runs.",
        placeholder_body=(
            "Logs will be powered by the runtime gateway and workers after the "
            "shell foundation is stable."
        ),
        palette_help="Open filtered runtime event stream.",
    ),
    PanelDefinition(
        id=PanelId.CONFIG,
        label="Config",
        summary=(
            "Active config summary, boundary-aware explanations, and controlled "
            "reload or edit flows."
        ),
        placeholder_body=(
            "Config visibility and controlled edits are rendered through the "
            "dedicated config panel widget."
        ),
        palette_help="Open config state and guided edits.",
    ),
    PanelDefinition(
        id=PanelId.PUBLISH,
        label="Publish",
        summary=(
            "Staging preflight, changed-path visibility, and deliberate publish "
            "preparation actions."
        ),
        placeholder_body=(
            "Publish visibility and actions are rendered through the dedicated "
            "publish panel widget."
        ),
        palette_help="Open staging preflight and publish actions.",
    ),
)

PANEL_BY_ID: dict[PanelId, PanelDefinition] = {panel.id: panel for panel in PANELS}
DEFAULT_PANEL = PanelId.OVERVIEW
EXPANDED_STREAM_WIDGET_ID = "shell-expanded-stream"


def panel_widget_id(panel_id: PanelId) -> str:
    return f"panel-{panel_id.value}"


def shell_content_target(panel_id: PanelId, body_mode: ShellBodyMode) -> str:
    if body_mode is ShellBodyMode.EXPANDED:
        return EXPANDED_STREAM_WIDGET_ID
    return panel_widget_id(panel_id)


def nav_button_id(panel_id: PanelId) -> str:
    return f"nav-{panel_id.value}"


__all__ = [
    "DEFAULT_PANEL",
    "DisplayMode",
    "EXPANDED_STREAM_WIDGET_ID",
    "PANEL_BY_ID",
    "PANELS",
    "PanelDefinition",
    "PanelId",
    "ShellBodyMode",
    "nav_button_id",
    "panel_widget_id",
    "shell_content_target",
    "toggle_display_mode",
    "toggle_shell_body_mode",
]
