"""Entrypoint advisory skill reference linting."""

from __future__ import annotations

import re

from .models import AssetLintDiagnostic, LintLevel, ParsedMarkdownAsset
from .rendering import diag

ENTRYPOINT_SECTION_HEADER_PATTERN = re.compile(
    r"^##\s+(?P<section>required stage-core skill|optional secondary skills)\s*$",
    re.IGNORECASE,
)
ENTRYPOINT_SKILL_LINE_PATTERN = re.compile(r"^-\s+`(?P<skill>[a-z0-9][a-z0-9-]*)`")


def extract_entrypoint_skill_sections(body: str) -> dict[str, list[tuple[str, str]]]:
    sections: dict[str, list[tuple[str, str]]] = {
        "required_stage_core_skill": [],
        "optional_secondary_skills": [],
    }
    active_section: str | None = None

    for raw_line in body.splitlines():
        line = raw_line.strip()
        section_match = ENTRYPOINT_SECTION_HEADER_PATTERN.match(line)
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

        skill_match = ENTRYPOINT_SKILL_LINE_PATTERN.match(line)
        if skill_match:
            sections[active_section].append((skill_match.group("skill"), line))

    return sections


def lint_entrypoint_references(
    asset: ParsedMarkdownAsset,
    *,
    skill_ids: set[str],
) -> list[AssetLintDiagnostic]:
    diagnostics: list[AssetLintDiagnostic] = []
    sections = extract_entrypoint_skill_sections(asset.body)
    required_stage_core_ids = sections["required_stage_core_skill"]
    optional_secondary_ids = sections["optional_secondary_skills"]

    if len(required_stage_core_ids) != 1:
        diagnostics.append(
            diag(
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
            diag(
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
            diag(
                asset,
                LintLevel.STRUCTURAL,
                reason=f"entrypoint references unknown skill `{skill_id}` in `Optional Secondary Skills`",
                suggested_fix=f"add skill `{skill_id}` or remove it from `Optional Secondary Skills`",
            )
        )

    return diagnostics


__all__ = ["extract_entrypoint_skill_sections", "lint_entrypoint_references"]
