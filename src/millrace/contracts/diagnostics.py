"""Diagnostic records emitted by compiler and guardrail validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias

DiagnosticContextValue: TypeAlias = str | int | bool | None | tuple[str, ...]


def immutable_context(
    context: Mapping[str, DiagnosticContextValue],
) -> Mapping[str, DiagnosticContextValue]:
    return MappingProxyType(dict(context))


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: str
    phase: str
    declaration_path: str
    message: str
    context: Mapping[str, DiagnosticContextValue]
    hint: str | None = None
    related_declaration_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "context", immutable_context(self.context))


__all__ = ("Diagnostic", "DiagnosticContextValue", "immutable_context")
