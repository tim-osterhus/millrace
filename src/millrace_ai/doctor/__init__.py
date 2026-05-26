"""Public facade for workspace doctor checks."""

from __future__ import annotations

import shutil as shutil
from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.paths import WorkspacePaths, workspace_paths

from .checks import DoctorContext, run_doctor_checks
from .models import DoctorIssue, DoctorReport
from .output import sorted_issues


def run_workspace_doctor(
    target: WorkspacePaths | Path | str,
    *,
    assets_root: Path | None = None,
) -> DoctorReport:
    """Run deterministic workspace/runtime checks without mutating workspace state."""

    paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
    resolved_assets_root = paths.runtime_root if assets_root is None else Path(assets_root)
    context = DoctorContext(paths=paths, assets_root=resolved_assets_root)
    run_doctor_checks(context)

    return DoctorReport(
        ok=not context.errors,
        errors=sorted_issues(context.errors),
        warnings=sorted_issues(context.warnings),
        checked_at=datetime.now(timezone.utc),
    )


__all__ = [
    "DoctorIssue",
    "DoctorReport",
    "run_workspace_doctor",
]
