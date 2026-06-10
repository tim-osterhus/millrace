"""Required-extension declaration contracts.

Contracts that let modes and compiled plans declare extension package
requirements.  The compiler validates that every declared required
extension exists among the discovered manifests and rejects missing
or unavailable extensions with clear diagnostics.

ADRs: ADR-0012, ADR-0015.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import field_validator, model_validator

from .base import ContractModel

_CANONICAL_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")

_SEMVER_RE = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _validate_semver_or_none(value: str | None, *, field_label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not re.fullmatch(_SEMVER_RE, normalized):
        raise ValueError(
            f"{field_label} must be valid semver (e.g. 1.0.0), got {normalized!r}"
        )
    return normalized


class RequiredExtensionDeclaration(ContractModel):
    """A single required-extension entry in config/plan metadata.

    Declares that a mode or plan requires a specific extension package
    (identified by its canonical package_id).  An optional minimum version
    constraint lets operators pin a floor without coupling to exact releases.

    The compiler validates that the declared package_id exists among the
    discovered extension manifests and satisfies any version constraint.
    Selection uses compiler-validated identifiers — arbitrary Python
    imports from config data are never performed at compile time.
    """

    extension_package_id: str
    min_version: str | None = None

    @field_validator("extension_package_id")
    @classmethod
    def validate_package_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _CANONICAL_ID_RE.fullmatch(normalized):
            raise ValueError(
                f"extension_package_id must match {_CANONICAL_ID_RE.pattern!r}"
            )
        return normalized

    @field_validator("min_version")
    @classmethod
    def validate_min_version(cls, value: str | None) -> str | None:
        return _validate_semver_or_none(value, field_label="min_version")


class RequiredExtensionsSpec(ContractModel):
    """Collection of required-extension declarations for a mode or plan.

    This is the top-level contract that modes and compiled plans use to
    declare their extension dependencies.  It is designed to live inside
    ModeDefinition or as a standalone metadata entry.
    """

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["required_extensions_spec"] = "required_extensions_spec"

    required_extensions: tuple[RequiredExtensionDeclaration, ...] = ()

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> "RequiredExtensionsSpec":
        seen: set[str] = set()
        duplicates: list[str] = []
        for req in self.required_extensions:
            if req.extension_package_id in seen:
                duplicates.append(req.extension_package_id)
            seen.add(req.extension_package_id)
        if duplicates:
            raise ValueError(
                "duplicate required extension package ids: "
                + ", ".join(sorted(duplicates))
            )
        return self


__all__ = [
    "RequiredExtensionDeclaration",
    "RequiredExtensionsSpec",
]
