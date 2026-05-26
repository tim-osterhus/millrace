"""Diagnostic result models for workspace doctor checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DoctorIssue:
    """One doctor finding with deterministic code and optional path context."""

    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Aggregated doctor findings for one workspace check pass."""

    ok: bool
    errors: tuple[DoctorIssue, ...]
    warnings: tuple[DoctorIssue, ...]
    checked_at: datetime


__all__ = ["DoctorIssue", "DoctorReport"]
