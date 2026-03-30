"""Persistent notice list for the shell footer area."""

from __future__ import annotations

from datetime import timezone

from textual.widgets import Static

from ..models import NoticeLevel, NoticeView


def render_notices(notices: tuple[NoticeView, ...], *, visible_limit: int = 1) -> str:
    if not notices:
        return "no notices"
    items = notices[-visible_limit:]
    lines: list[str] = []
    for notice in items:
        timestamp = notice.created_at.astimezone(timezone.utc).strftime("%H:%M:%SZ")
        level = ""
        if notice.level is NoticeLevel.WARNING:
            level = " warn"
        elif notice.level is NoticeLevel.ERROR:
            level = " error"
        lines.append(f"{timestamp}{level} {notice.title.lower()}: {notice.message}")
    return "\n".join(lines)


class NoticesView(Static):
    """Simple persistent notice surface under the main shell content."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(render_notices(()), id=id, markup=False)

    def show_notices(self, notices: tuple[NoticeView, ...]) -> None:
        self.remove_class("notice-warning")
        self.remove_class("notice-error")
        latest = self.latest_level(notices)
        if latest is NoticeLevel.WARNING:
            self.add_class("notice-warning")
        elif latest is NoticeLevel.ERROR:
            self.add_class("notice-error")
        self.update(render_notices(notices))

    def latest_level(self, notices: tuple[NoticeView, ...]) -> NoticeLevel | None:
        if not notices:
            return None
        return notices[-1].level


__all__ = ["NoticesView", "render_notices"]
