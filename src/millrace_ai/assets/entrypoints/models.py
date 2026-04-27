"""Models for entrypoint asset parsing and linting."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


class LintLevel(str, Enum):
    STRUCTURAL = "structural"
    COMPATIBILITY = "compatibility"
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class AssetLintDiagnostic:
    """One lint finding for an asset manifest/body pair."""

    path: Path
    asset_type: str
    asset_id: str
    stage: str | None
    lint_level: LintLevel
    reason: str
    suggested_fix: str


@dataclass(frozen=True, slots=True)
class ParsedMarkdownAsset:
    """Parsed markdown asset with YAML-like frontmatter manifest."""

    path: Path
    manifest: Mapping[str, object]
    body: str


__all__ = ["AssetLintDiagnostic", "LintLevel", "ParsedMarkdownAsset"]
