"""Compiler persistence and time helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from millrace_ai.architecture import CompiledRunPlan


def utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def load_existing_plan(path: Path) -> CompiledRunPlan | None:
    if not path.is_file():
        return None

    try:
        payload = path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        return CompiledRunPlan.model_validate_json(payload)
    except ValidationError:
        return None


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


__all__ = ["atomic_write_json", "load_existing_plan", "utc_now"]
