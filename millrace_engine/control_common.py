"""Shared control-plane errors and normalization helpers."""

from __future__ import annotations

import tomllib
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from .config import LoadedConfig, load_engine_config
from .queue import QueueError


class ControlError(RuntimeError):
    """Base control-plane failure."""


def single_line_message(value: object) -> str:
    text = str(value).strip()
    if not text:
        return ""
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def validation_error_message(exc: ValidationError) -> str:
    details: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(token) for token in error.get("loc", ()))
        message = str(error.get("msg", "invalid value")).strip()
        details.append(f"{location}: {message}" if location else message)
    if details:
        return "; ".join(details)
    fallback = single_line_message(exc)
    return fallback or "validation failed"


def expected_error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return validation_error_message(exc)
    return single_line_message(exc)


def load_control_config(config_path: Path) -> LoadedConfig:
    try:
        return load_engine_config(config_path)
    except FileNotFoundError as exc:
        raise ControlError(str(exc)) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ControlError(f"config TOML is invalid: {single_line_message(exc)}") from exc
    except ValidationError as exc:
        raise ControlError(f"config validation failed: {validation_error_message(exc)}") from exc


def queue_control_error(exc: Exception, *, prefix: str) -> ControlError:
    detail = expected_error_message(exc)
    if isinstance(exc, QueueError) and detail.lower().startswith("queue "):
        return ControlError(detail)
    return ControlError(f"{prefix}: {detail}")


def event_log_control_error(exc: Exception) -> ControlError:
    return ControlError(f"event log is invalid: {expected_error_message(exc)}")


def normalize_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
