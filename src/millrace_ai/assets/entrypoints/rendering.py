"""Diagnostic construction and ordering helpers."""

from __future__ import annotations

from .models import AssetLintDiagnostic, LintLevel, ParsedMarkdownAsset
from .parsing import asset_id, asset_type, string_value


def diag(
    asset: ParsedMarkdownAsset,
    lint_level: LintLevel,
    *,
    reason: str,
    suggested_fix: str,
) -> AssetLintDiagnostic:
    return AssetLintDiagnostic(
        path=asset.path,
        asset_type=asset_type(asset) or "unknown",
        asset_id=asset_id(asset),
        stage=string_value(asset.manifest, "stage"),
        lint_level=lint_level,
        reason=reason,
        suggested_fix=suggested_fix,
    )


def sort_diagnostics(
    diagnostics: list[AssetLintDiagnostic],
) -> tuple[AssetLintDiagnostic, ...]:
    return tuple(
        sorted(
            diagnostics,
            key=lambda item: (str(item.path), item.lint_level.value, item.reason),
        )
    )


__all__ = ["diag", "sort_diagnostics"]
