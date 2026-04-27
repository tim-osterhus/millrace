"""Compiler diagnostics contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import model_validator

from .base import ContractModel


class CompileDiagnostics(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["compile_diagnostics"] = "compile_diagnostics"

    ok: bool
    mode_id: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    emitted_at: datetime

    @model_validator(mode="after")
    def validate_error_shape(self) -> "CompileDiagnostics":
        if not self.ok and not self.errors:
            raise ValueError("errors are required when ok is false")
        return self


__all__ = ["CompileDiagnostics"]
