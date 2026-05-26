"""Output-stability helpers for doctor reports."""

from __future__ import annotations

from .models import DoctorIssue


def sorted_issues(issues: list[DoctorIssue]) -> tuple[DoctorIssue, ...]:
    """Return issues in the deterministic order used by doctor reports."""

    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                "" if issue.path is None else issue.path.as_posix(),
                issue.code,
                issue.message,
            ),
        )
    )


__all__ = ["sorted_issues"]
