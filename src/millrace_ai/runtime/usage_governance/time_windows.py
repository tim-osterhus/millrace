"""Usage-governance time-window helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROLLING_5H = timedelta(hours=5)


def calendar_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def calendar_week_start(now: datetime, *, timezone_info: ZoneInfo) -> datetime:
    local_now = ensure_utc(now).astimezone(timezone_info)
    local_start = local_now - timedelta(
        days=local_now.weekday(),
        hours=local_now.hour,
        minutes=local_now.minute,
        seconds=local_now.second,
        microseconds=local_now.microsecond,
    )
    return local_start.astimezone(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def datetime_from_unix_seconds(value: object) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(float(value), timezone.utc)


__all__ = [
    "ROLLING_5H",
    "calendar_timezone",
    "calendar_week_start",
    "datetime_from_unix_seconds",
    "ensure_utc",
]
