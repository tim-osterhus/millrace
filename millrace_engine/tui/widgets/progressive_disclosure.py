"""Shared progressive-disclosure helpers for panel degraded-state rendering."""

from __future__ import annotations

from ..models import DisplayMode, GatewayFailure

_OPERATOR_DEBUG_HINT = "open debug for technical detail"


def collapse_operator_text(text: str, *, max_parts: int = 2, max_length: int = 88) -> str:
    """Collapse noisy operator-facing detail into one short actionable line."""

    normalized = " ".join(text.replace("\n", " ").split())
    if not normalized:
        return "detail unavailable"
    parts = [part.strip() for part in normalized.replace("; ", " | ").split("|") if part.strip()]
    if not parts:
        parts = [normalized]
    collapsed = " | ".join(parts[:max_parts])
    if len(parts) > max_parts:
        collapsed = f"{collapsed} | +{len(parts) - max_parts} more"
    if len(collapsed) > max_length:
        collapsed = f"{collapsed[: max_length - 3].rstrip()}..."
    return collapsed


def append_operator_debug_hint(
    lines: list[str],
    *,
    detail_hint: str = _OPERATOR_DEBUG_HINT,
) -> None:
    """Point operator mode toward debug mode for technical detail."""

    lines.append(f"DETAIL  {detail_hint}")


def append_panel_failure_lines(
    lines: list[str],
    *,
    panel_label: str,
    failure: GatewayFailure | None,
    has_snapshot: bool,
    display_mode: DisplayMode,
) -> None:
    """Append one consistent degraded-state preamble for operator or debug modes."""

    if failure is None:
        return
    qualifier = "stale" if has_snapshot else "unavailable"
    lines.append(f"{panel_label} {qualifier}: {failure.message}")
    if display_mode is DisplayMode.OPERATOR:
        if has_snapshot:
            lines.append("STATE   showing last known snapshot")
        append_operator_debug_hint(lines)
    else:
        snapshot_state = "last known snapshot available" if has_snapshot else "no snapshot available"
        lines.append(f"STATE   {snapshot_state}")
        lines.append(
            "DETAIL  "
            f"op {failure.operation}"
            f" | cat {failure.category.value}"
            f" | type {failure.exception_type}"
            f" | retry {'yes' if failure.retryable else 'no'}"
        )
    lines.append("")


__all__ = ["append_operator_debug_hint", "append_panel_failure_lines", "collapse_operator_text"]
