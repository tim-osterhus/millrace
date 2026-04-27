"""Entrypoint and advisory asset lint policy."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from .advisory import lint_entrypoint_references
from .discovery import (
    KNOWN_ASSET_TYPES,
    KNOWN_EXECUTION_STAGES,
    KNOWN_LEARNING_STAGES,
    KNOWN_PLANES,
    KNOWN_STAGES,
    infer_entrypoint_path_target,
)
from .models import AssetLintDiagnostic, LintLevel, ParsedMarkdownAsset
from .parsing import (
    asset_type as manifest_asset_type,
)
from .parsing import (
    bool_value,
    parse_markdown_asset,
    string_list_value,
    string_value,
)
from .rendering import diag, sort_diagnostics

CORE_FORBIDDEN_CLAIMS = {
    "queue_selection",
    "routing",
    "retry_thresholds",
    "escalation_policy",
    "status_persistence",
}
ADVISORY_FORBIDDEN_CLAIMS = CORE_FORBIDDEN_CLAIMS | {"terminal_results", "required_artifacts"}

_DENYLIST_PHRASES: tuple[tuple[str, str], ...] = (
    ("select the oldest", "claims queue selection ownership"),
    ("pick the next task", "claims queue selection ownership"),
    ("write to state/execution_status.md", "claims canonical execution status persistence"),
    ("write to state/planning_status.md", "claims canonical planning status persistence"),
    ("route to", "claims stage routing ownership"),
    ("retry up to", "claims retry-threshold ownership"),
    ("escalate to", "claims escalation ownership"),
    ("you must update the runtime snapshot", "claims runtime snapshot ownership"),
)
_ESCALATE_NEGATION_PATTERN = re.compile(
    r"\b(?:do\s+not|don't|dont|must\s+not|should\s+not|cannot|can't|can\s+not|never|avoid)\b"
)
_NEGATED_SECTION_HEADER_PATTERN = re.compile(
    r"^\s*(?:not\s+allowed|forbidden|disallowed|prohibited)\s*:?\s*$"
)


def lint_asset_manifests(
    *,
    assets_root: Path | str,
    canonical_contract_ids_by_stage: Mapping[str, str] | None = None,
) -> tuple[AssetLintDiagnostic, ...]:
    """Parse and lint markdown manifests under one assets root."""

    root = Path(assets_root).expanduser().resolve()
    diagnostics: list[AssetLintDiagnostic] = []
    assets: list[ParsedMarkdownAsset] = []

    for path in sorted(root.rglob("*.md")):
        try:
            assets.append(parse_markdown_asset(path))
        except ValueError as exc:
            diagnostics.append(
                AssetLintDiagnostic(
                    path=path,
                    asset_type="unknown",
                    asset_id=path.stem,
                    stage=None,
                    lint_level=LintLevel.STRUCTURAL,
                    reason=str(exc),
                    suggested_fix="add parseable YAML frontmatter with required fields",
                )
            )

    diagnostics.extend(_lint_duplicate_asset_ids(assets))

    skill_ids = _asset_ids_by_type(assets, asset_type="skill")

    for asset in assets:
        diagnostics.extend(_lint_structural(asset))

    for asset in assets:
        diagnostics.extend(_lint_compatibility(asset, canonical_contract_ids_by_stage))

        if manifest_asset_type(asset) == "entrypoint":
            diagnostics.extend(lint_entrypoint_references(asset, skill_ids=skill_ids))

        diagnostics.extend(_lint_policy(asset))

    return sort_diagnostics(diagnostics)


def _lint_duplicate_asset_ids(assets: list[ParsedMarkdownAsset]) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []
    seen: dict[tuple[str, str], Path] = {}

    for asset in assets:
        asset_kind = manifest_asset_type(asset)
        asset_id = string_value(asset.manifest, "asset_id")
        if asset_kind is None or asset_id is None:
            continue
        key = (asset_kind, asset_id)
        original_path = seen.get(key)
        if original_path is None:
            seen[key] = asset.path
            continue

        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"duplicate asset_id `{asset_id}` for asset_type `{asset_kind}`",
                suggested_fix=f"rename one asset_id or remove duplicate (first seen at {original_path})",
            )
        )

    return diagnostics


def _asset_ids_by_type(assets: list[ParsedMarkdownAsset], *, asset_type: str) -> set[str]:
    ids: set[str] = set()
    for asset in assets:
        if manifest_asset_type(asset) != asset_type:
            continue
        asset_id = string_value(asset.manifest, "asset_id")
        if asset_id is not None:
            ids.add(asset_id)
    return ids


def _lint_structural(asset: ParsedMarkdownAsset) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []

    asset_kind = manifest_asset_type(asset)
    stage = string_value(asset.manifest, "stage")

    if asset_kind is None:
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="asset_type must be a string",
                suggested_fix="set `asset_type` to entrypoint or skill",
            )
        )
        return diagnostics

    if asset_kind not in KNOWN_ASSET_TYPES:
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"unknown asset_type: {asset_kind}",
                suggested_fix="use one of: entrypoint, skill",
            )
        )
        return diagnostics

    if asset_kind != "entrypoint":
        for field_name in ("asset_type", "asset_id", "version", "description"):
            if field_name not in asset.manifest:
                diagnostics.append(
                    diag(
                        asset,
                        LintLevel.STRUCTURAL,
                        reason=f"missing required field: {field_name}",
                        suggested_fix=f"add `{field_name}` to frontmatter",
                    )
                )

    if asset_kind == "entrypoint":
        diagnostics.extend(_lint_structural_entrypoint(asset))
    elif asset_kind == "skill":
        diagnostics.extend(_lint_structural_skill(asset))

    if stage is not None and stage not in KNOWN_STAGES:
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"unknown stage: {stage}",
                suggested_fix="set `stage` to a known execution/planning/learning stage",
            )
        )

    return diagnostics


def _lint_structural_entrypoint(asset: ParsedMarkdownAsset) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []

    path_plane, path_stage = infer_entrypoint_path_target(asset.path)
    if path_plane is None:
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint path must live under entrypoints/execution|planning|learning",
                suggested_fix="move file under entrypoints/execution, entrypoints/planning, or entrypoints/learning",
            )
        )
    elif path_stage is None:
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint filename must match a known stage name",
                suggested_fix="rename file to the canonical stage name",
            )
        )

    plane = string_value(asset.manifest, "plane")
    if plane is not None and plane not in KNOWN_PLANES:
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"unknown plane: {plane}",
                suggested_fix="set `plane` to execution, planning, or learning",
            )
        )

    advisory_only = bool_value(asset.manifest, "advisory_only")
    if advisory_only is not None and advisory_only:
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint must declare advisory_only: false",
                suggested_fix="set `advisory_only` to false",
            )
        )

    if "contract_compatibility" in asset.manifest and not string_list_value(
        asset.manifest, "contract_compatibility"
    ):
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint contract_compatibility must be a non-empty list",
                suggested_fix="declare at least one compatible contract id",
            )
        )

    if "required_result_set" in asset.manifest and not string_list_value(
        asset.manifest, "required_result_set"
    ):
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint required_result_set must be a non-empty list",
                suggested_fix="declare legal terminal results in required_result_set",
            )
        )

    if not asset.body.strip():
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint body must not be empty",
                suggested_fix="add stage instructions to entrypoint markdown body",
            )
        )

    return diagnostics


def _lint_structural_skill(asset: ParsedMarkdownAsset) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []

    for field_name in ("advisory_only", "capability_type", "forbidden_claims"):
        if field_name not in asset.manifest:
            diagnostics.append(
                diag(
                    asset,
                    LintLevel.STRUCTURAL,
                    reason=f"skill missing required field: {field_name}",
                    suggested_fix=f"add `{field_name}` to skill frontmatter",
                )
            )

    advisory_only = bool_value(asset.manifest, "advisory_only")
    if advisory_only is not None and not advisory_only:
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="skills must declare advisory_only: true",
                suggested_fix="set `advisory_only` to true",
            )
        )

    _lint_known_stage_list(asset, key="recommended_for_stages", diagnostics=diagnostics)

    if not string_list_value(asset.manifest, "forbidden_claims"):
        diagnostics.append(
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="skills must declare non-empty forbidden_claims",
                suggested_fix="declare seam-owned behavior claims under forbidden_claims",
            )
        )

    return diagnostics


def _lint_known_stage_list(
    asset: ParsedMarkdownAsset,
    *,
    key: str,
    diagnostics: list[AssetLintDiagnostic],
) -> None:
    stage_names = string_list_value(asset.manifest, key)
    if stage_names is None:
        return

    for stage_name in stage_names:
        if stage_name not in KNOWN_STAGES:
            diagnostics.append(
                diag(
                    asset,
                    LintLevel.STRUCTURAL,
                    reason=f"{key} references unknown stage: {stage_name}",
                    suggested_fix=f"remove unknown stage from `{key}`",
                )
            )


def _lint_compatibility(
    asset: ParsedMarkdownAsset,
    canonical_contract_ids_by_stage: Mapping[str, str] | None,
) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []

    if manifest_asset_type(asset) != "entrypoint":
        return diagnostics

    stage = string_value(asset.manifest, "stage")
    plane = string_value(asset.manifest, "plane")

    if stage is not None and plane is not None:
        if stage in KNOWN_EXECUTION_STAGES:
            expected_plane = "execution"
        elif stage in KNOWN_LEARNING_STAGES:
            expected_plane = "learning"
        else:
            expected_plane = "planning"
        if stage in KNOWN_STAGES and plane != expected_plane:
            diagnostics.append(
                diag(
                    asset,
                    LintLevel.COMPATIBILITY,
                    reason=(
                        f"entrypoint stage `{stage}` expects plane `{expected_plane}`, got `{plane}`"
                    ),
                    suggested_fix="align `stage` and `plane` to the canonical stage topology",
                )
            )

    path_plane, path_stage = infer_entrypoint_path_target(asset.path)
    if path_plane is not None and plane is not None and path_plane != plane:
        diagnostics.append(
            diag(
                asset,
                LintLevel.COMPATIBILITY,
                reason=(
                    f"entrypoint path plane `{path_plane}` does not match manifest plane `{plane}`"
                ),
                suggested_fix="move file or change `plane` to match path",
            )
        )

    if path_stage is not None and stage is not None and path_stage != stage:
        diagnostics.append(
            diag(
                asset,
                LintLevel.COMPATIBILITY,
                reason=(
                    f"entrypoint path stage `{path_stage}` does not match manifest stage `{stage}`"
                ),
                suggested_fix="rename file or change `stage` to match path",
            )
        )

    if canonical_contract_ids_by_stage and stage is not None:
        canonical_contract_id = canonical_contract_ids_by_stage.get(stage)
        if canonical_contract_id is not None:
            compatibility = string_list_value(asset.manifest, "contract_compatibility")
            if compatibility is not None and canonical_contract_id not in compatibility:
                diagnostics.append(
                    diag(
                        asset,
                        LintLevel.COMPATIBILITY,
                        reason=(
                            f"entrypoint contract_compatibility is missing canonical id `{canonical_contract_id}`"
                        ),
                        suggested_fix="declare canonical contract id in `contract_compatibility`",
                    )
                )

    return diagnostics


def _lint_policy(asset: ParsedMarkdownAsset) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []

    asset_kind = manifest_asset_type(asset)
    if asset_kind not in KNOWN_ASSET_TYPES:
        return diagnostics

    if asset_kind == "skill":
        forbidden_claims = set(string_list_value(asset.manifest, "forbidden_claims") or ())
        missing_claims = sorted(ADVISORY_FORBIDDEN_CLAIMS - forbidden_claims)
        if missing_claims:
            diagnostics.append(
                diag(
                    asset,
                    LintLevel.POLICY,
                    reason=(
                        "advisory asset forbidden_claims is missing seam-boundary claims: "
                        + ", ".join(missing_claims)
                    ),
                    suggested_fix="declare all required seam-boundary claims under forbidden_claims",
                )
            )

        illegal_keys = {"required_result_set", "contract_id", "contract_compatibility"}
        for key in illegal_keys:
            if key in asset.manifest:
                diagnostics.append(
                    diag(
                        asset,
                        LintLevel.POLICY,
                        reason=f"advisory asset should not declare `{key}`",
                        suggested_fix="remove hard-contract fields from advisory assets",
                    )
                )

    body_lc = asset.body.lower()
    for phrase, description in _DENYLIST_PHRASES:
        if not _body_claims_phrase(body_lc, phrase):
            continue
        diagnostics.append(
            diag(
                asset,
                LintLevel.POLICY,
                reason=f"asset body {description}",
                suggested_fix="remove runtime-owned behavior from asset prose",
            )
        )

    return diagnostics


def _body_claims_phrase(body_lc: str, phrase: str) -> bool:
    if phrase != "escalate to":
        return phrase in body_lc

    lines = body_lc.splitlines()
    for index, line in enumerate(lines):
        start = 0
        while True:
            phrase_index = line.find(phrase, start)
            if phrase_index < 0:
                break
            prefix = line[:phrase_index]
            if _ESCALATE_NEGATION_PATTERN.search(prefix):
                start = phrase_index + len(phrase)
                continue

            previous_line = _previous_non_empty_line(lines, index)
            if previous_line is not None and _NEGATED_SECTION_HEADER_PATTERN.match(previous_line):
                start = phrase_index + len(phrase)
                continue

            return True

    return False


def _previous_non_empty_line(lines: list[str], index: int) -> str | None:
    cursor = index - 1
    while cursor >= 0:
        candidate = lines[cursor].strip()
        if candidate:
            return candidate
        cursor -= 1
    return None


__all__ = ["lint_asset_manifests"]
