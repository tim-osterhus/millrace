"""Blueprint-owned Doctor diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from millrace_ai.doctor.models import DoctorIssue

from .state import collect_blueprint_manifest_diagnostics

if TYPE_CHECKING:
    from millrace_ai.doctor.checks import DoctorContext


def run_doctor_diagnostics(context: DoctorContext) -> None:
    for diagnostic in collect_blueprint_manifest_diagnostics(context.paths):
        context.errors.append(
            DoctorIssue(
                code=diagnostic.code,
                message=diagnostic.message,
                path=diagnostic.path,
            )
        )


__all__ = ["run_doctor_diagnostics"]
