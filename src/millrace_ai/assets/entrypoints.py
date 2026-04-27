"""Entrypoint and advisory asset manifest parsing plus lint helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from millrace_ai.contracts import Plane
from millrace_ai.contracts.stage_metadata import known_stage_values, known_stage_values_for_plane


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


KNOWN_EXECUTION_STAGES = known_stage_values_for_plane(Plane.EXECUTION)
KNOWN_PLANNING_STAGES = known_stage_values_for_plane(Plane.PLANNING)
KNOWN_LEARNING_STAGES = known_stage_values_for_plane(Plane.LEARNING)
KNOWN_STAGES = known_stage_values()
KNOWN_PLANES = {plane.value for plane in Plane}
KNOWN_ASSET_TYPES = {"entrypoint", "skill"}

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
_ENTRYPOINT_SECTION_HEADER_PATTERN = re.compile(
    r"^##\s+(?P<section>required stage-core skill|optional secondary skills)\s*$",
    re.IGNORECASE,
)
_ENTRYPOINT_SKILL_LINE_PATTERN = re.compile(r"^-\s+`(?P<skill>[a-z0-9][a-z0-9-]*)`")


def parse_markdown_asset(path: Path) -> ParsedMarkdownAsset:
    """Parse markdown frontmatter into a compact manifest map."""

    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    manifest: Mapping[str, object]
    if lines and lines[0].strip() == "---":
        frontmatter_text, body = _split_frontmatter(raw)
        manifest = _parse_frontmatter_map(frontmatter_text, path=path)
    else:
        path_plane, path_stage = _infer_entrypoint_path_target(path)
        if path_plane is None:
            raise ValueError("asset file is missing YAML frontmatter start marker")

        manifest = {"asset_type": "entrypoint", "plane": path_plane}
        if path_stage is not None:
            manifest["stage"] = path_stage
        body = raw.strip()

    return ParsedMarkdownAsset(path=path, manifest=manifest, body=body)


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

        if _asset_type(asset) == "entrypoint":
            diagnostics.extend(_lint_entrypoint_references(asset, skill_ids=skill_ids))

        diagnostics.extend(_lint_policy(asset))

    return tuple(sorted(diagnostics, key=lambda item: (str(item.path), item.lint_level.value, item.reason)))


def _lint_duplicate_asset_ids(assets: list[ParsedMarkdownAsset]) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []
    seen: dict[tuple[str, str], Path] = {}

    for asset in assets:
        asset_type = _asset_type(asset)
        asset_id = _string_value(asset.manifest, "asset_id")
        if asset_type is None or asset_id is None:
            continue
        key = (asset_type, asset_id)
        original_path = seen.get(key)
        if original_path is None:
            seen[key] = asset.path
            continue

        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"duplicate asset_id `{asset_id}` for asset_type `{asset_type}`",
                suggested_fix=f"rename one asset_id or remove duplicate (first seen at {original_path})",
            )
        )

    return diagnostics


def _asset_ids_by_type(assets: list[ParsedMarkdownAsset], *, asset_type: str) -> set[str]:
    ids: set[str] = set()
    for asset in assets:
        if _asset_type(asset) != asset_type:
            continue
        asset_id = _string_value(asset.manifest, "asset_id")
        if asset_id is not None:
            ids.add(asset_id)
    return ids


def _extract_entrypoint_skill_sections(body: str) -> dict[str, list[tuple[str, str]]]:
    sections: dict[str, list[tuple[str, str]]] = {
        "required_stage_core_skill": [],
        "optional_secondary_skills": [],
    }
    active_section: str | None = None

    for raw_line in body.splitlines():
        line = raw_line.strip()
        section_match = _ENTRYPOINT_SECTION_HEADER_PATTERN.match(line)
        if section_match:
            active_section = (
                section_match.group("section").lower().replace(" ", "_").replace("-", "_")
            )
            continue

        if line.startswith("## "):
            active_section = None
            continue

        if active_section is None:
            continue

        skill_match = _ENTRYPOINT_SKILL_LINE_PATTERN.match(line)
        if skill_match:
            sections[active_section].append((skill_match.group("skill"), line))

    return sections


def _lint_structural(asset: ParsedMarkdownAsset) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []

    asset_type = _asset_type(asset)
    stage = _string_value(asset.manifest, "stage")

    if asset_type is None:
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="asset_type must be a string",
                suggested_fix="set `asset_type` to entrypoint or skill",
            )
        )
        return diagnostics

    if asset_type not in KNOWN_ASSET_TYPES:
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"unknown asset_type: {asset_type}",
                suggested_fix="use one of: entrypoint, skill",
            )
        )
        return diagnostics

    if asset_type != "entrypoint":
        for field_name in ("asset_type", "asset_id", "version", "description"):
            if field_name not in asset.manifest:
                diagnostics.append(
                    _diag(
                        asset,
                        LintLevel.STRUCTURAL,
                        reason=f"missing required field: {field_name}",
                        suggested_fix=f"add `{field_name}` to frontmatter",
                    )
                )

    if asset_type == "entrypoint":
        diagnostics.extend(_lint_structural_entrypoint(asset))
    elif asset_type == "skill":
        diagnostics.extend(_lint_structural_skill(asset))

    if stage is not None and stage not in KNOWN_STAGES:
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"unknown stage: {stage}",
                suggested_fix="set `stage` to a known execution/planning/learning stage",
            )
        )

    return diagnostics


def _lint_structural_entrypoint(asset: ParsedMarkdownAsset) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []

    path_plane, path_stage = _infer_entrypoint_path_target(asset.path)
    if path_plane is None:
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint path must live under entrypoints/execution|planning|learning",
                suggested_fix="move file under entrypoints/execution, entrypoints/planning, or entrypoints/learning",
            )
        )
    elif path_stage is None:
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint filename must match a known stage name",
                suggested_fix="rename file to the canonical stage name",
            )
        )

    plane = _string_value(asset.manifest, "plane")
    if plane is not None and plane not in KNOWN_PLANES:
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"unknown plane: {plane}",
                suggested_fix="set `plane` to execution, planning, or learning",
            )
        )

    advisory_only = _bool_value(asset.manifest, "advisory_only")
    if advisory_only is not None and advisory_only:
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint must declare advisory_only: false",
                suggested_fix="set `advisory_only` to false",
            )
        )

    if "contract_compatibility" in asset.manifest and not _string_list_value(
        asset.manifest, "contract_compatibility"
    ):
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint contract_compatibility must be a non-empty list",
                suggested_fix="declare at least one compatible contract id",
            )
        )

    if "required_result_set" in asset.manifest and not _string_list_value(
        asset.manifest, "required_result_set"
    ):
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint required_result_set must be a non-empty list",
                suggested_fix="declare legal terminal results in required_result_set",
            )
        )

    if not asset.body.strip():
        diagnostics.append(
            _diag(
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
                _diag(
                    asset,
                    LintLevel.STRUCTURAL,
                    reason=f"skill missing required field: {field_name}",
                    suggested_fix=f"add `{field_name}` to skill frontmatter",
                )
            )

    advisory_only = _bool_value(asset.manifest, "advisory_only")
    if advisory_only is not None and not advisory_only:
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="skills must declare advisory_only: true",
                suggested_fix="set `advisory_only` to true",
            )
        )

    _lint_known_stage_list(asset, key="recommended_for_stages", diagnostics=diagnostics)

    if not _string_list_value(asset.manifest, "forbidden_claims"):
        diagnostics.append(
            _diag(
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
    stage_names = _string_list_value(asset.manifest, key)
    if stage_names is None:
        return

    for stage_name in stage_names:
        if stage_name not in KNOWN_STAGES:
            diagnostics.append(
                _diag(
                    asset,
                    LintLevel.STRUCTURAL,
                    reason=f"{key} references unknown stage: {stage_name}",
                    suggested_fix=f"remove unknown stage from `{key}`",
                )
            )


def _lint_entrypoint_references(
    asset: ParsedMarkdownAsset,
    *,
    skill_ids: set[str],
) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []
    sections = _extract_entrypoint_skill_sections(asset.body)
    required_stage_core_ids = sections["required_stage_core_skill"]
    optional_secondary_ids = sections["optional_secondary_skills"]

    if len(required_stage_core_ids) != 1:
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason="entrypoint must declare exactly one required stage-core skill",
                suggested_fix="add one bullet under `## Required Stage-Core Skill`",
            )
        )

    for skill_id, _line in required_stage_core_ids:
        if skill_id in skill_ids:
            continue
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"entrypoint references unknown skill `{skill_id}` in `Required Stage-Core Skill`",
                suggested_fix=f"add skill `{skill_id}` or remove it from `Required Stage-Core Skill`",
            )
        )

    for skill_id, _line in optional_secondary_ids:
        if skill_id in skill_ids:
            continue
        diagnostics.append(
            _diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"entrypoint references unknown skill `{skill_id}` in `Optional Secondary Skills`",
                suggested_fix=f"add skill `{skill_id}` or remove it from `Optional Secondary Skills`",
            )
        )

    return diagnostics


def _lint_compatibility(
    asset: ParsedMarkdownAsset,
    canonical_contract_ids_by_stage: Mapping[str, str] | None,
) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []

    if _asset_type(asset) != "entrypoint":
        return diagnostics

    stage = _string_value(asset.manifest, "stage")
    plane = _string_value(asset.manifest, "plane")

    if stage is not None and plane is not None:
        if stage in KNOWN_EXECUTION_STAGES:
            expected_plane = "execution"
        elif stage in KNOWN_LEARNING_STAGES:
            expected_plane = "learning"
        else:
            expected_plane = "planning"
        if stage in KNOWN_STAGES and plane != expected_plane:
            diagnostics.append(
                _diag(
                    asset,
                    LintLevel.COMPATIBILITY,
                    reason=(
                        f"entrypoint stage `{stage}` expects plane `{expected_plane}`, got `{plane}`"
                    ),
                    suggested_fix="align `stage` and `plane` to the canonical stage topology",
                )
            )

    path_plane, path_stage = _infer_entrypoint_path_target(asset.path)
    if path_plane is not None and plane is not None and path_plane != plane:
        diagnostics.append(
            _diag(
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
            _diag(
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
            compatibility = _string_list_value(asset.manifest, "contract_compatibility")
            if compatibility is not None and canonical_contract_id not in compatibility:
                diagnostics.append(
                    _diag(
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

    asset_type = _asset_type(asset)
    if asset_type not in KNOWN_ASSET_TYPES:
        return diagnostics

    if asset_type == "skill":
        forbidden_claims = set(_string_list_value(asset.manifest, "forbidden_claims") or ())
        missing_claims = sorted(ADVISORY_FORBIDDEN_CLAIMS - forbidden_claims)
        if missing_claims:
            diagnostics.append(
                _diag(
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
                    _diag(
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
            _diag(
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


def _split_frontmatter(raw: str) -> tuple[str, str]:
    lines = raw.splitlines()
    if not lines:
        raise ValueError("asset file is empty")
    if lines[0].strip() != "---":
        raise ValueError("asset file is missing YAML frontmatter start marker")

    end_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        raise ValueError("asset file is missing YAML frontmatter end marker")

    frontmatter = "\n".join(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :]).strip()
    return frontmatter, body


def _parse_frontmatter_map(frontmatter: str, *, path: Path) -> dict[str, object]:
    manifest: dict[str, object] = {}
    active_list_key: str | None = None

    for line_number, raw_line in enumerate(frontmatter.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if raw_line.startswith("  - ") or raw_line.startswith("- "):
            if active_list_key is None:
                raise ValueError(
                    f"frontmatter parse error in {path.name}:{line_number} (list item without key)"
                )
            item_raw = stripped[2:].strip()
            current = manifest.get(active_list_key)
            if not isinstance(current, list):
                raise ValueError(
                    f"frontmatter parse error in {path.name}:{line_number} (list key malformed)"
                )
            current.append(_parse_scalar(item_raw))
            continue

        if ":" not in raw_line:
            raise ValueError(
                f"frontmatter parse error in {path.name}:{line_number} (missing `:` separator)"
            )

        key_raw, value_raw = raw_line.split(":", 1)
        key = key_raw.strip()
        value = value_raw.strip()

        if not key:
            raise ValueError(f"frontmatter parse error in {path.name}:{line_number} (empty key)")

        if value == "":
            manifest[key] = []
            active_list_key = key
        else:
            manifest[key] = _parse_scalar(value)
            active_list_key = None

    return manifest


def _parse_scalar(value: str) -> object:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]

    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]

    if re.fullmatch(r"-?\d+", value):
        return int(value)

    return value


def _asset_type(asset: ParsedMarkdownAsset) -> str | None:
    return _string_value(asset.manifest, "asset_type")


def _asset_id(asset: ParsedMarkdownAsset) -> str:
    value = _string_value(asset.manifest, "asset_id")
    return value if value is not None else asset.path.stem


def _string_value(manifest: Mapping[str, object], key: str) -> str | None:
    value = manifest.get(key)
    return value if isinstance(value, str) else None


def _bool_value(manifest: Mapping[str, object], key: str) -> bool | None:
    value = manifest.get(key)
    return value if isinstance(value, bool) else None


def _string_list_value(manifest: Mapping[str, object], key: str) -> list[str] | None:
    raw_value = manifest.get(key)
    if raw_value is None:
        return None

    if not isinstance(raw_value, list):
        return None

    values: list[str] = []
    for item in raw_value:
        if not isinstance(item, str):
            return None
        values.append(item)
    return values


def _infer_entrypoint_path_target(path: Path) -> tuple[str | None, str | None]:
    parts = path.parts
    if "entrypoints" not in parts:
        return None, None

    entrypoints_index = parts.index("entrypoints")
    if entrypoints_index + 1 >= len(parts):
        return None, None

    plane = parts[entrypoints_index + 1]
    if plane not in KNOWN_PLANES:
        return None, None

    stem = path.stem
    if stem in KNOWN_STAGES:
        return plane, stem

    stage = next(
        (
            candidate
            for candidate in sorted(KNOWN_STAGES, key=len, reverse=True)
            if stem.endswith(f"-{candidate}") or stem.endswith(f"_{candidate}")
        ),
        None,
    )
    return plane, stage


def _diag(
    asset: ParsedMarkdownAsset,
    lint_level: LintLevel,
    *,
    reason: str,
    suggested_fix: str,
) -> AssetLintDiagnostic:
    return AssetLintDiagnostic(
        path=asset.path,
        asset_type=_asset_type(asset) or "unknown",
        asset_id=_asset_id(asset),
        stage=_string_value(asset.manifest, "stage"),
        lint_level=lint_level,
        reason=reason,
        suggested_fix=suggested_fix,
    )


__all__ = [
    "AssetLintDiagnostic",
    "LintLevel",
    "ParsedMarkdownAsset",
    "lint_asset_manifests",
    "parse_markdown_asset",
]
