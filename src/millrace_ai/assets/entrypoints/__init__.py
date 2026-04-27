"""Stable public facade for entrypoint asset parsing and linting."""

from __future__ import annotations

from .linting import lint_asset_manifests
from .models import AssetLintDiagnostic, LintLevel, ParsedMarkdownAsset
from .parsing import parse_markdown_asset

AssetLintDiagnostic.__module__ = __name__
LintLevel.__module__ = __name__
ParsedMarkdownAsset.__module__ = __name__

__all__ = [
    "AssetLintDiagnostic",
    "LintLevel",
    "ParsedMarkdownAsset",
    "lint_asset_manifests",
    "parse_markdown_asset",
]
