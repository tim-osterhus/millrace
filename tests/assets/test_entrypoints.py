from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from millrace_ai.assets.entrypoints import ParsedMarkdownAsset
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    LearningStageName,
    LearningTerminalResult,
    PlanningStageName,
    PlanningTerminalResult,
)
from millrace_ai.entrypoints import LintLevel, lint_asset_manifests, parse_markdown_asset

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT_MAPPING_DOC = REPO_ROOT / "docs" / "runtime" / "millrace-entrypoint-mapping.md"
ENTRYPOINT_MAPPING_ROW = re.compile(
    r"- `(?P<runtime>src/millrace_ai/assets/entrypoints/(?:execution|planning|learning)/[^`]+\.md)` -> "
    r"`millrace-agents/entrypoints/(?:execution|planning|learning)/[^`]+\.md`"
)
LEGACY_ENTRYPOINT_TOKENS = (
    "current-task",
    "ideas/specs",
    "ideas/incidents",
    "status_contract",
)
SKILLS_SECTION_HEADER = re.compile(
    r"^##\s+(Required Stage-Core Skill|Optional Secondary Skills)\s*$",
    re.IGNORECASE,
)
SKILL_LINE = re.compile(r"^-\s+`(?P<skill>[a-z0-9-]+)`")
HYBRID_SKILL_SECTION_TITLES = [
    "Purpose",
    "Quick Start",
    "Operating Constraints",
    "Inputs This Skill Expects",
    "Output Contract",
    "Procedure",
    "Pitfalls And Gotchas",
    "Progressive Disclosure",
    "Verification Pattern",
]
SKILLS_DIR = REPO_ROOT / "src" / "millrace_ai" / "assets" / "skills"
CREATOR_PACKAGE_PATH = SKILLS_DIR / "millrace-skill-creator"
CREATOR_SKILL_PATH = CREATOR_PACKAGE_PATH / "SKILL.md"
MARATHON_QA_PACKAGE_PATH = SKILLS_DIR / "shared" / "marathon-qa-audit"
MARATHON_QA_SKILL_PATH = MARATHON_QA_PACKAGE_PATH / "SKILL.md"
STAGE_CORE_FORBIDDEN_CLAIMS = {
    "queue_selection",
    "routing",
    "retry_thresholds",
    "escalation_policy",
    "status_persistence",
    "terminal_results",
    "required_artifacts",
}


def test_entrypoints_module_is_assets_facade() -> None:
    entrypoints_facade = importlib.import_module("millrace_ai.entrypoints")
    entrypoints_module = importlib.import_module("millrace_ai.assets.entrypoints")

    assert entrypoints_facade.parse_markdown_asset is entrypoints_module.parse_markdown_asset
    assert entrypoints_facade.lint_asset_manifests is entrypoints_module.lint_asset_manifests
    assert entrypoints_facade.LintLevel.__module__ == "millrace_ai.assets.entrypoints"


def test_assets_entrypoints_public_exports_remain_importable() -> None:
    entrypoints_module = importlib.import_module("millrace_ai.assets.entrypoints")

    for name in entrypoints_module.__all__:
        assert hasattr(entrypoints_module, name), name


def _write_asset(path: Path, *, frontmatter: dict[str, object], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")

    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_entrypoint_doc(path: Path, *, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.strip() + "\n", encoding="utf-8")


def _extract_declared_skill_lines(body: str) -> list[tuple[str, str]]:
    declared: list[tuple[str, str]] = []
    active_section = False

    for raw_line in body.splitlines():
        line = raw_line.strip()
        section_match = SKILLS_SECTION_HEADER.match(line)
        if section_match:
            active_section = True
            continue

        if line.startswith("## "):
            active_section = False
            continue

        if not active_section:
            continue

        skill_match = SKILL_LINE.match(line)
        if skill_match:
            declared.append((skill_match.group("skill"), line))

    return declared


def _extract_h2_headings(body: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE)
    ]


def _assert_stage_core_manifest_contract(asset: ParsedMarkdownAsset, *, stage: str) -> None:
    manifest = asset.manifest
    forbidden_claims = manifest["forbidden_claims"]

    assert manifest["asset_type"] == "skill"
    assert manifest["asset_id"] == f"{stage}-core"
    assert manifest["advisory_only"] is True
    assert manifest["capability_type"] == "stage_core"
    assert manifest["recommended_for_stages"] == [stage]
    assert isinstance(forbidden_claims, list)
    assert set(forbidden_claims) == STAGE_CORE_FORBIDDEN_CLAIMS


def _load_shipped_skill_asset_ids() -> set[str]:
    skill_ids: set[str] = set()

    for path in sorted(SKILLS_DIR.rglob("*.md")):
        if path.name == "skills_index.md":
            continue
        try:
            asset = parse_markdown_asset(path)
        except ValueError:
            continue
        if asset.manifest.get("asset_type") != "skill":
            continue
        asset_id = asset.manifest.get("asset_id")
        if isinstance(asset_id, str) and asset_id:
            skill_ids.add(asset_id)

    return skill_ids


def test_asset_manifest_lint_rules(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    builder_entrypoint_path = assets_dir / "entrypoints" / "execution" / "builder.md"
    planning_builder_path = assets_dir / "entrypoints" / "planning" / "builder.md"
    builder_override_path = assets_dir / "entrypoints" / "execution" / "builder-override.md"

    _write_asset(
        assets_dir / "skills" / "builder-core.md",
        frontmatter={
            "asset_type": "skill",
            "asset_id": "builder-core",
            "version": 1,
            "description": "Builder stage core skill",
            "advisory_only": True,
            "capability_type": "stage_core",
            "recommended_for_stages": ["builder"],
            "forbidden_claims": [
                "queue_selection",
                "routing",
                "retry_thresholds",
                "escalation_policy",
                "status_persistence",
                "terminal_results",
                "required_artifacts",
            ],
        },
        body="Builder posture and evidence habits.",
    )

    _write_asset(
        assets_dir / "skills" / "small-diff-discipline.md",
        frontmatter={
            "asset_type": "skill",
            "asset_id": "small-diff-discipline",
            "version": 1,
            "description": "Small diff behavior",
            "advisory_only": True,
            "capability_type": "implementation",
            "recommended_for_stages": ["builder", "fixer"],
            "forbidden_claims": [
                "queue_selection",
                "routing",
                "retry_thresholds",
                "escalation_policy",
                "status_persistence",
                "terminal_results",
                "required_artifacts",
            ],
        },
        body="Use precise, incremental changes.",
    )

    _write_asset(
        assets_dir / "skills" / "small-diff-discipline-duplicate.md",
        frontmatter={
            "asset_type": "skill",
            "asset_id": "small-diff-discipline",
            "version": 1,
            "description": "Duplicate id for lint coverage",
            "advisory_only": True,
            "capability_type": "implementation",
            "recommended_for_stages": ["checker"],
            "forbidden_claims": [
                "queue_selection",
                "routing",
                "retry_thresholds",
                "escalation_policy",
                "status_persistence",
                "terminal_results",
                "required_artifacts",
            ],
        },
        body="Intentionally duplicate asset_id.",
    )

    _write_entrypoint_doc(
        builder_entrypoint_path,
        body=(
            "# Builder Entry Instructions\n\n"
            "## Required Stage-Core Skill\n"
            "- `builder-core`\n\n"
            "## Optional Secondary Skills\n"
            "- `small-diff-discipline`\n\n"
            "The stage may emit only:\n"
            "- `BUILDER_COMPLETE`: done\n"
            "- `BLOCKED`: blocked\n"
        ),
    )

    _write_asset(
        assets_dir / "skills" / "bad-skill.md",
        frontmatter={
            "asset_type": "skill",
            "asset_id": "bad-skill",
            "version": 1,
            "description": "Invalid advisory policy",
            "advisory_only": False,
            "capability_type": "verification",
            "forbidden_claims": ["queue_selection"],
        },
        body="This intentionally violates advisory-only rules.",
    )

    _write_asset(
        assets_dir / "roles" / "bad-role.md",
        frontmatter={
            "asset_type": "role_overlay",
            "asset_id": "bad-role",
            "version": 1,
            "description": "Bad role overlay",
            "advisory_only": True,
            "recommended_for_stages": ["builder"],
            "perspective_type": "backend",
            "forbidden_claims": [
                "queue_selection",
                "routing",
                "retry_thresholds",
                "escalation_policy",
                "status_persistence",
                "terminal_results",
                "required_artifacts",
            ],
        },
        body="You must write to state/execution_status.md after every step.",
    )

    _write_entrypoint_doc(
        planning_builder_path,
        body="Mismatch between path and stage/plane should fail.",
    )

    _write_entrypoint_doc(
        builder_override_path,
        body="Select the oldest queued task and route to checker directly.",
    )

    diagnostics = lint_asset_manifests(
        assets_root=assets_dir,
        canonical_contract_ids_by_stage={"builder": "builder.v1"},
    )

    assert diagnostics

    levels_by_asset = {(diag.asset_id, diag.lint_level) for diag in diagnostics}
    levels_by_path = {(diag.path, diag.lint_level) for diag in diagnostics}

    assert ("bad-skill", LintLevel.STRUCTURAL) in levels_by_asset
    assert (planning_builder_path, LintLevel.COMPATIBILITY) in levels_by_path
    assert ("builder-override", LintLevel.STRUCTURAL) in levels_by_asset
    assert ("bad-role", LintLevel.STRUCTURAL) in levels_by_asset
    assert ("builder-override", LintLevel.POLICY) in levels_by_asset
    assert any("duplicate asset_id" in diag.reason for diag in diagnostics)

    assert all(diag.path != builder_entrypoint_path for diag in diagnostics)
    assert all(diag.asset_id != "builder-core" for diag in diagnostics)
    assert any(
        diag.asset_id == "small-diff-discipline" and "duplicate asset_id" in diag.reason
        for diag in diagnostics
    )


def test_entrypoint_lint_requires_required_stage_core_skill_section(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    _write_asset(
        assets_dir / "skills" / "small-diff-discipline.md",
        frontmatter={
            "asset_type": "skill",
            "asset_id": "small-diff-discipline",
            "version": 1,
            "description": "Optional skill",
            "advisory_only": True,
            "capability_type": "implementation",
            "recommended_for_stages": ["builder"],
            "forbidden_claims": [
                "queue_selection",
                "routing",
                "retry_thresholds",
                "escalation_policy",
                "status_persistence",
                "terminal_results",
                "required_artifacts",
            ],
        },
        body="Keep changes narrow.",
    )

    entrypoint_path = assets_dir / "entrypoints" / "execution" / "builder.md"
    _write_entrypoint_doc(
        entrypoint_path,
        body=(
            "# Builder Entry Instructions\n\n"
            "## Optional Secondary Skills\n"
            "- `small-diff-discipline`\n"
        ),
    )

    diagnostics = lint_asset_manifests(assets_root=assets_dir)

    assert any(
        diag.path == entrypoint_path
        and diag.lint_level is LintLevel.STRUCTURAL
        and "required stage-core skill" in diag.reason
        for diag in diagnostics
    )


def test_entrypoint_lint_rejects_unknown_optional_secondary_skill(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    _write_asset(
        assets_dir / "skills" / "builder-core.md",
        frontmatter={
            "asset_type": "skill",
            "asset_id": "builder-core",
            "version": 1,
            "description": "Builder stage core skill",
            "advisory_only": True,
            "capability_type": "stage_core",
            "recommended_for_stages": ["builder"],
            "forbidden_claims": [
                "queue_selection",
                "routing",
                "retry_thresholds",
                "escalation_policy",
                "status_persistence",
                "terminal_results",
                "required_artifacts",
            ],
        },
        body="Builder posture and evidence habits.",
    )

    entrypoint_path = assets_dir / "entrypoints" / "execution" / "builder.md"
    _write_entrypoint_doc(
        entrypoint_path,
        body=(
            "# Builder Entry Instructions\n\n"
            "## Required Stage-Core Skill\n"
            "- `builder-core`\n\n"
            "## Optional Secondary Skills\n"
            "- `missing-optional-skill`\n"
        ),
    )

    diagnostics = lint_asset_manifests(assets_root=assets_dir)

    assert any(
        diag.path == entrypoint_path
        and diag.lint_level is LintLevel.STRUCTURAL
        and "unknown skill `missing-optional-skill`" in diag.reason
        for diag in diagnostics
    )


def test_policy_lint_ignores_negated_escalate_phrase(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    entrypoint_path = assets_dir / "entrypoints" / "planning" / "mechanic.md"
    _write_entrypoint_doc(
        entrypoint_path,
        body=(
            "## Hard Boundaries\n\n"
            "Not allowed:\n"
            "- do not escalate to another plane without preserving evidence\n"
        ),
    )

    diagnostics = lint_asset_manifests(assets_root=assets_dir)

    assert all(
        not (diag.path == entrypoint_path and diag.lint_level is LintLevel.POLICY)
        for diag in diagnostics
    )


def test_policy_lint_flags_unnegated_escalate_phrase(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    entrypoint_path = assets_dir / "entrypoints" / "planning" / "mechanic.md"
    _write_entrypoint_doc(
        entrypoint_path,
        body="- escalate to another plane immediately",
    )

    diagnostics = lint_asset_manifests(assets_root=assets_dir)

    assert any(
        diag.path == entrypoint_path
        and diag.lint_level is LintLevel.POLICY
        and "escalation ownership" in diag.reason
        for diag in diagnostics
    )


def _load_runtime_entrypoint_paths_from_docs() -> list[Path]:
    doc_text = ENTRYPOINT_MAPPING_DOC.read_text(encoding="utf-8")
    runtime_paths = [match.group("runtime") for match in ENTRYPOINT_MAPPING_ROW.finditer(doc_text)]
    assert runtime_paths, "Entrypoint mapping doc must include runtime asset paths"
    return [REPO_ROOT / relative_path for relative_path in runtime_paths]


def _expected_stage_result_sets() -> dict[str, set[str]]:
    return {
        ExecutionStageName.BUILDER.value: {
            ExecutionTerminalResult.BUILDER_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        },
        ExecutionStageName.CHECKER.value: {
            ExecutionTerminalResult.CHECKER_PASS.value,
            ExecutionTerminalResult.FIX_NEEDED.value,
            ExecutionTerminalResult.BLOCKED.value,
        },
        ExecutionStageName.FIXER.value: {
            ExecutionTerminalResult.FIXER_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        },
        ExecutionStageName.DOUBLECHECKER.value: {
            ExecutionTerminalResult.DOUBLECHECK_PASS.value,
            ExecutionTerminalResult.FIX_NEEDED.value,
            ExecutionTerminalResult.BLOCKED.value,
        },
        ExecutionStageName.UPDATER.value: {
            ExecutionTerminalResult.UPDATE_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        },
        ExecutionStageName.TROUBLESHOOTER.value: {
            ExecutionTerminalResult.TROUBLESHOOT_COMPLETE.value,
            ExecutionTerminalResult.BLOCKED.value,
        },
        ExecutionStageName.CONSULTANT.value: {
            ExecutionTerminalResult.CONSULT_COMPLETE.value,
            ExecutionTerminalResult.NEEDS_PLANNING.value,
            ExecutionTerminalResult.BLOCKED.value,
        },
        PlanningStageName.PLANNER.value: {
            PlanningTerminalResult.PLANNER_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        },
        PlanningStageName.MANAGER.value: {
            PlanningTerminalResult.MANAGER_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        },
        PlanningStageName.MECHANIC.value: {
            PlanningTerminalResult.MECHANIC_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        },
        PlanningStageName.AUDITOR.value: {
            PlanningTerminalResult.AUDITOR_COMPLETE.value,
            PlanningTerminalResult.BLOCKED.value,
        },
        PlanningStageName.ARBITER.value: {
            PlanningTerminalResult.ARBITER_COMPLETE.value,
            PlanningTerminalResult.REMEDIATION_NEEDED.value,
            PlanningTerminalResult.BLOCKED.value,
        },
        LearningStageName.ANALYST.value: {
            LearningTerminalResult.ANALYST_COMPLETE.value,
            LearningTerminalResult.BLOCKED.value,
        },
        LearningStageName.PROFESSOR.value: {
            LearningTerminalResult.PROFESSOR_COMPLETE.value,
            LearningTerminalResult.BLOCKED.value,
        },
        LearningStageName.CURATOR.value: {
            LearningTerminalResult.CURATOR_COMPLETE.value,
            LearningTerminalResult.BLOCKED.value,
        },
    }


def _expected_stage_core_skill_ids() -> dict[str, str]:
    return {
        ExecutionStageName.BUILDER.value: "builder-core",
        ExecutionStageName.CHECKER.value: "checker-core",
        ExecutionStageName.FIXER.value: "fixer-core",
        ExecutionStageName.DOUBLECHECKER.value: "doublechecker-core",
        ExecutionStageName.UPDATER.value: "updater-core",
        ExecutionStageName.TROUBLESHOOTER.value: "troubleshooter-core",
        ExecutionStageName.CONSULTANT.value: "consultant-core",
        PlanningStageName.PLANNER.value: "planner-core",
        PlanningStageName.MANAGER.value: "manager-core",
        PlanningStageName.MECHANIC.value: "mechanic-core",
        PlanningStageName.AUDITOR.value: "auditor-core",
        PlanningStageName.ARBITER.value: "arbiter-core",
        LearningStageName.ANALYST.value: "analyst-core",
        LearningStageName.PROFESSOR.value: "professor-core",
        LearningStageName.CURATOR.value: "curator-core",
    }


def _expected_stage_core_skill_paths() -> dict[str, Path]:
    return {
        ExecutionStageName.BUILDER.value: SKILLS_DIR / "stage" / "execution" / "builder-core" / "SKILL.md",
        ExecutionStageName.CHECKER.value: SKILLS_DIR / "stage" / "execution" / "checker-core" / "SKILL.md",
        ExecutionStageName.FIXER.value: SKILLS_DIR / "stage" / "execution" / "fixer-core" / "SKILL.md",
        ExecutionStageName.DOUBLECHECKER.value: SKILLS_DIR
        / "stage"
        / "execution"
        / "doublechecker-core"
        / "SKILL.md",
        ExecutionStageName.UPDATER.value: SKILLS_DIR / "stage" / "execution" / "updater-core" / "SKILL.md",
        ExecutionStageName.TROUBLESHOOTER.value: SKILLS_DIR
        / "stage"
        / "execution"
        / "troubleshooter-core"
        / "SKILL.md",
        ExecutionStageName.CONSULTANT.value: SKILLS_DIR / "stage" / "execution" / "consultant-core" / "SKILL.md",
        PlanningStageName.PLANNER.value: SKILLS_DIR / "stage" / "planning" / "planner-core" / "SKILL.md",
        PlanningStageName.MANAGER.value: SKILLS_DIR / "stage" / "planning" / "manager-core" / "SKILL.md",
        PlanningStageName.MECHANIC.value: SKILLS_DIR / "stage" / "planning" / "mechanic-core" / "SKILL.md",
        PlanningStageName.AUDITOR.value: SKILLS_DIR / "stage" / "planning" / "auditor-core" / "SKILL.md",
        PlanningStageName.ARBITER.value: SKILLS_DIR / "stage" / "planning" / "arbiter-core" / "SKILL.md",
        LearningStageName.ANALYST.value: SKILLS_DIR / "stage" / "learning" / "analyst-core" / "SKILL.md",
        LearningStageName.PROFESSOR.value: SKILLS_DIR / "stage" / "learning" / "professor-core" / "SKILL.md",
        LearningStageName.CURATOR.value: SKILLS_DIR / "stage" / "learning" / "curator-core" / "SKILL.md",
    }


def _expected_stage_core_body_keywords() -> dict[str, tuple[str, ...]]:
    return {
        ExecutionStageName.BUILDER.value: (
            "contract",
            "feature",
            "foundational",
            "verification",
        ),
        ExecutionStageName.CHECKER.value: (
            "contract",
            "expected outcome",
            "evidence",
            "fix-needed",
        ),
        ExecutionStageName.FIXER.value: (
            "contract",
            "repair",
            "regression",
        ),
        ExecutionStageName.DOUBLECHECKER.value: (
            "contract",
            "expectations",
            "displaced",
        ),
        ExecutionStageName.UPDATER.value: (
            "stale",
            "evidence",
            "outline.md",
        ),
        ExecutionStageName.TROUBLESHOOTER.value: (
            "symptom",
            "blocker",
            "local",
        ),
        ExecutionStageName.CONSULTANT.value: (
            "continuation",
            "evidence",
            "incident",
        ),
        PlanningStageName.PLANNER.value: (
            "assumption",
            "scope",
            "pass-through",
            "fan-out",
        ),
        PlanningStageName.MANAGER.value: (
            "slice",
            "dependency",
            "parallel fan-out",
            "boundary",
        ),
        PlanningStageName.MECHANIC.value: (
            "planning",
            "repair",
            "evidence",
        ),
        PlanningStageName.AUDITOR.value: (
            "incident",
            "evidence",
            "assumption",
        ),
        PlanningStageName.ARBITER.value: (
            "rubric",
            "parity",
            "remediation",
        ),
        LearningStageName.ANALYST.value: (
            "learning request",
            "research packet",
            "evidence",
        ),
        LearningStageName.PROFESSOR.value: (
            "skill candidates",
            "research packets",
            "skill-creator",
        ),
        LearningStageName.CURATOR.value: (
            "skill improvements",
            "evidence",
            "scope",
        ),
    }


def test_packaged_to_runtime_entrypoint_mapping_complete() -> None:
    runtime_paths = _load_runtime_entrypoint_paths_from_docs()
    mapped_runtime = set(runtime_paths)

    runtime_root = REPO_ROOT / "src" / "millrace_ai" / "assets" / "entrypoints"
    expected_runtime = {
        path
        for path in runtime_root.rglob("*.md")
    }
    assert mapped_runtime == expected_runtime

    for runtime_path in runtime_paths:
        assert runtime_path.exists()


def test_parse_markdown_asset_accepts_metadata_free_entrypoint_only(tmp_path: Path) -> None:
    entrypoint_path = tmp_path / "entrypoints" / "execution" / "builder.md"
    entrypoint_path.parent.mkdir(parents=True, exist_ok=True)
    entrypoint_path.write_text("# Builder\n\nInstruction body.\n", encoding="utf-8")
    parsed_entrypoint = parse_markdown_asset(entrypoint_path)
    assert parsed_entrypoint.body.startswith("# Builder")
    assert parsed_entrypoint.manifest["asset_type"] == "entrypoint"
    assert parsed_entrypoint.manifest["stage"] == "builder"
    assert parsed_entrypoint.manifest["plane"] == "execution"

    skill_path = tmp_path / "skills" / "small-diff-discipline.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("# skill\n\nMissing manifest.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing YAML frontmatter start marker"):
        parse_markdown_asset(skill_path)


def test_parse_markdown_asset_accepts_stage_suffixed_entrypoint_filename(tmp_path: Path) -> None:
    entrypoint_path = tmp_path / "entrypoints" / "execution" / "custom-builder.md"
    entrypoint_path.parent.mkdir(parents=True, exist_ok=True)
    entrypoint_path.write_text("# Builder\n\nInstruction body.\n", encoding="utf-8")

    parsed_entrypoint = parse_markdown_asset(entrypoint_path)

    assert parsed_entrypoint.manifest["asset_type"] == "entrypoint"
    assert parsed_entrypoint.manifest["stage"] == "builder"
    assert parsed_entrypoint.manifest["plane"] == "execution"


def test_lint_accepts_stage_suffixed_entrypoint_filename(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    entrypoint_path = assets_dir / "entrypoints" / "planning" / "custom-planner.md"
    _write_entrypoint_doc(
        entrypoint_path,
        body=(
            "## Required Stage-Core Skill\n"
            "- `planner-core`\n"
        ),
    )

    diagnostics = lint_asset_manifests(assets_root=assets_dir)

    assert all(
        not (
            diag.path == entrypoint_path
            and diag.lint_level is LintLevel.STRUCTURAL
            and "filename must match a known stage name" in diag.reason
        )
        for diag in diagnostics
    )


def _extract_legal_terminal_results(body: str) -> set[str]:
    tokens = set(re.findall(r"`###\s+([A-Z][A-Z_]+)`", body))
    tokens.update(re.findall(r"^###\s+([A-Z][A-Z_]+)\s*$", body, flags=re.MULTILINE))
    return tokens


def _assert_required_result_set(
    *,
    stage: str,
    body: str,
    expected_stage_results: dict[str, set[str]],
) -> None:
    expected_results = expected_stage_results[stage]
    extracted_results = _extract_legal_terminal_results(body)

    missing_tokens = expected_results - extracted_results
    unexpected_tokens = extracted_results - expected_results

    assert not missing_tokens
    assert not unexpected_tokens


def test_runtime_entrypoint_result_set_check_rejects_illegal_extra_tokens() -> None:
    expected_stage_results = _expected_stage_result_sets()
    body = (
        "The stage may emit only:\n"
        "- `### BUILDER_COMPLETE`\n"
        "- `### BLOCKED`\n"
        "- `### UNEXPECTED_TERMINAL`\n"
    )

    with pytest.raises(AssertionError):
        _assert_required_result_set(
            stage=ExecutionStageName.BUILDER.value,
            body=body,
            expected_stage_results=expected_stage_results,
        )


def test_runtime_entrypoint_required_result_sets() -> None:
    runtime_paths = _load_runtime_entrypoint_paths_from_docs()
    expected_stage_results = _expected_stage_result_sets()
    discovered_stages: set[str] = set()

    for runtime_path in runtime_paths:
        raw = runtime_path.read_text(encoding="utf-8")
        assert not raw.startswith("---")

        asset = parse_markdown_asset(runtime_path)
        stage = str(asset.manifest["stage"])
        assert stage in expected_stage_results

        plane = runtime_path.parent.name
        if stage in {member.value for member in ExecutionStageName}:
            assert plane == "execution"
        elif stage in {member.value for member in LearningStageName}:
            assert plane == "learning"
        else:
            assert plane == "planning"

        _assert_required_result_set(
            stage=stage,
            body=asset.body,
            expected_stage_results=expected_stage_results,
        )

        discovered_stages.add(stage)

    assert discovered_stages == set(expected_stage_results)


def test_runtime_skills_index_stub_has_minimal_shape() -> None:
    skills_index_path = SKILLS_DIR / "skills_index.md"
    asset = parse_markdown_asset(skills_index_path)
    text = asset.body

    assert asset.manifest["asset_type"] == "skill"
    assert text.startswith("# Skills Index")
    assert "| Skill | Description | Tags | Path | Status |" in text
    for skill_id in _expected_stage_core_skill_ids().values():
        assert skill_id in text
    for skill_path in _expected_stage_core_skill_paths().values():
        assert str(skill_path.relative_to(SKILLS_DIR.parent)) in text
    assert "skills/millrace-skill-creator/SKILL.md" in text
    assert CREATOR_PACKAGE_PATH.is_dir()
    assert CREATOR_SKILL_PATH.is_file()
    creator_asset = parse_markdown_asset(CREATOR_SKILL_PATH)
    assert creator_asset.manifest["asset_type"] == "skill"
    assert creator_asset.manifest["asset_id"] == "millrace-skill-creator"
    assert "skills-readme" in text
    assert "skills/README.md" in text
    assert "marathon-qa-audit" in text
    assert "skills/shared/marathon-qa-audit/SKILL.md" in text
    assert MARATHON_QA_PACKAGE_PATH.is_dir()
    assert MARATHON_QA_SKILL_PATH.is_file()
    assert "Stage-Core Skills" in text
    assert "Shared Runtime Skills" in text
    assert "Supported Downloadable Skills" in text
    assert "https://github.com/tim-osterhus/millrace-skills/blob/main/index.md" in text
    assert "deferred" not in text.lower()


def test_stage_core_skill_docs_use_hybrid_section_contract_and_shipped_semantics() -> None:
    stage_to_path = _expected_stage_core_skill_paths()
    stage_to_body: dict[str, str] = {}

    for stage, path in stage_to_path.items():
        asset = parse_markdown_asset(path)
        _assert_stage_core_manifest_contract(asset, stage=stage)
        stage_to_body[stage] = asset.body

    expected_body_keywords = _expected_stage_core_body_keywords()

    for stage, body in stage_to_body.items():
        headings = _extract_h2_headings(body)
        assert set(headings) == set(HYBRID_SKILL_SECTION_TITLES)
        assert len(headings) == len(HYBRID_SKILL_SECTION_TITLES)
        assert "## Purpose" in body
        assert "## Verification Pattern" in body
        body_lower = body.lower()
        for keyword in expected_body_keywords[stage]:
            assert keyword in body_lower


def test_runtime_skills_readme_describes_creator_package_and_selection_contract() -> None:
    skills_readme_path = SKILLS_DIR / "README.md"
    asset = parse_markdown_asset(skills_readme_path)
    body = asset.body

    assert "entrypoints" in body.lower()
    assert "skills_index.md" in body
    assert "millrace-skill-creator" in body
    assert "skills/stage/<plane>/<stage>-core/SKILL.md" in body
    assert "skills/shared/<skill-id>/SKILL.md" in body
    assert "marathon-qa-audit" in body
    assert "https://github.com/tim-osterhus/millrace-skills/blob/main/index.md" in body
    assert "deferred" not in body.lower()
    assert CREATOR_PACKAGE_PATH.is_dir()
    assert CREATOR_SKILL_PATH.is_file()
    creator_asset = parse_markdown_asset(CREATOR_SKILL_PATH)
    assert creator_asset.manifest["asset_id"] == "millrace-skill-creator"
    assert creator_asset.manifest["asset_type"] == "skill"


def test_runtime_shared_marathon_qa_skill_is_shipped_with_honest_audit_guidance() -> None:
    asset = parse_markdown_asset(MARATHON_QA_SKILL_PATH)
    body = asset.body

    assert asset.manifest["asset_type"] == "skill"
    assert asset.manifest["asset_id"] == "marathon-qa-audit"
    assert asset.manifest["advisory_only"] is True
    assert asset.manifest["capability_type"] == "verification"
    assert asset.manifest["recommended_for_stages"] == ["checker", "arbiter"]
    assert set(asset.manifest["forbidden_claims"]) == STAGE_CORE_FORBIDDEN_CLAIMS

    headings = _extract_h2_headings(body)
    assert "Purpose" in headings
    assert "Quick Start" in headings
    assert "Audit Modes" in headings
    assert "Evidence-Depth Ladder" in headings
    assert "Decision Rules" in headings
    assert "Verification Pattern" in headings
    assert "full-band" in body.lower()
    assert "reduced evidence quality" in body.lower()
    assert "affirmative failure evidence" in body.lower()
    assert "checker" in body.lower()
    assert "arbiter" in body.lower()


def test_runtime_entrypoints_align_to_runtime_workspace_contract() -> None:
    runtime_paths = _load_runtime_entrypoint_paths_from_docs()
    stage_to_body: dict[str, str] = {}
    entrypoint_bodies: list[tuple[str, str, str]] = []
    expected_stage_core_ids = _expected_stage_core_skill_ids()

    for runtime_path in runtime_paths:
        body = parse_markdown_asset(runtime_path).body
        entrypoint_id = runtime_path.stem
        stage = entrypoint_id
        entrypoint_bodies.append((entrypoint_id, stage, body))
        stage_to_body[entrypoint_id] = body
        for token in LEGACY_ENTRYPOINT_TOKENS:
            assert token not in body
        assert "runs/<RUN_ID>" not in body
        assert "reports/" not in body
        assert "`historylog.md`" not in body

    assert "active_work_item_path" in stage_to_body["builder"]
    assert "active_work_item_path" in stage_to_body["checker"]
    assert "active_work_item_path" in stage_to_body["doublechecker"]
    assert "active_work_item_path" in stage_to_body["planner"]
    assert "active_work_item_path" in stage_to_body["auditor"]
    assert "active_work_item_path" in stage_to_body["consultant"]
    assert "closure_target_path" in stage_to_body["arbiter"]
    assert "active_work_item_path" not in stage_to_body["arbiter"]
    assert "marathon-qa-audit" in stage_to_body["arbiter"]
    assert "if no rubric exists yet" in stage_to_body["arbiter"].lower()
    assert "full-band audit" in stage_to_body["arbiter"].lower()

    assert "summary_status_path" in stage_to_body["checker"]
    assert "marathon-qa-audit" in stage_to_body["checker"]
    assert "broader final-state or end-to-end audit" in stage_to_body["checker"].lower()
    assert "summary_status_path" in stage_to_body["doublechecker"]
    assert "summary_status_path" in stage_to_body["updater"]
    assert "run_dir/builder_summary.md" in stage_to_body["builder"]
    assert "millrace-agents/runs/latest/builder_summary.md" in stage_to_body["builder"]
    assert "millrace-agents/historylog.md" in stage_to_body["builder"]
    assert "millrace-agents/specs/queue/<SPEC_ID>.md" in stage_to_body["planner"]
    assert "millrace-agents/incidents/incoming/<INCIDENT_ID>.md" in stage_to_body["consultant"]
    assert "millrace-agents/incidents/active/<INCIDENT_ID>.md" in stage_to_body["auditor"]
    assert "millrace-agents/arbiter/contracts/root-specs/<ROOT_SPEC_ID>.md" in stage_to_body["arbiter"]
    assert "millrace-agents/arbiter/verdicts/<ROOT_SPEC_ID>.json" in stage_to_body["arbiter"]

    shipped_skill_ids = _load_shipped_skill_asset_ids()
    assert "skills-readme" in shipped_skill_ids
    for skill_id in _expected_stage_core_skill_ids().values():
        assert skill_id in shipped_skill_ids

    for entrypoint_id, stage, body in entrypoint_bodies:
        assert stage in expected_stage_core_ids, f"entrypoint `{entrypoint_id}` does not map to a known stage"
        assert "millrace-agents/skills/skills_index.md" in body
        assert "up to three additional relevant installed skills" in body
        assert "required_skill_paths" in body
        assert "## Required Stage-Core Skill" in body
        assert "## Optional Secondary Skills" in body
        assert "Optional Role Overlays" not in body
        assert f"`{expected_stage_core_ids[stage]}`" in body
        assert "deferred" not in body.lower()

        declared_skill_lines = _extract_declared_skill_lines(body)
        assert declared_skill_lines

        for skill_id, skill_line in declared_skill_lines:
            assert skill_id in shipped_skill_ids, (
                f"stage `{stage}` references skill `{skill_id}` without a shipped asset"
            )


def test_learning_entrypoints_define_durable_handoff_artifacts() -> None:
    learning_dir = REPO_ROOT / "src" / "millrace_ai" / "assets" / "entrypoints" / "learning"
    analyst = (learning_dir / "analyst.md").read_text(encoding="utf-8")
    professor = (learning_dir / "professor.md").read_text(encoding="utf-8")
    curator = (learning_dir / "curator.md").read_text(encoding="utf-8")

    for body in (analyst, professor, curator):
        assert "active_work_item_path" in body
        assert "run_dir" in body
        assert "summary_status_path" in body
        assert "stop immediately" in body.lower()
        assert "target_stage" in body
        assert "requested_action" in body
        assert "artifact_paths" in body
        assert "preferred_output_paths" in body

    assert "millrace skills refresh-remote-index" in analyst
    assert "millrace skills install <skill_id>" in analyst
    assert "remote_skills_index.md" in analyst

    assert "run_dir/analyst_research_packet.md" in analyst
    assert "source_refs" in analyst
    assert "Do not author or modify skills" in analyst

    assert "run_dir/professor_skill_candidate/" in professor
    assert "run_dir/professor_skill_patch.md" in professor
    assert "millrace-skill-creator" in professor
    assert "Professor approval is not publication" in professor

    assert "run_dir/curator_decision.md" in curator
    assert "workspace-installed skills" in curator
    assert "source promotion" in curator
    assert "promotion remains an operator command" in curator.lower()


def test_learning_core_skills_back_artifact_handoff_contracts() -> None:
    learning_skills_dir = SKILLS_DIR / "stage" / "learning"
    analyst = (learning_skills_dir / "analyst-core" / "SKILL.md").read_text(encoding="utf-8")
    professor = (learning_skills_dir / "professor-core" / "SKILL.md").read_text(encoding="utf-8")
    curator = (learning_skills_dir / "curator-core" / "SKILL.md").read_text(encoding="utf-8")

    assert "analyst_research_packet.md" in analyst
    assert "requested_action" in analyst
    assert "target_stage" in analyst

    assert "professor_skill_candidate" in professor
    assert "professor_skill_patch.md" in professor
    assert "lint_skill.py" in professor
    assert "evaluate_skill.py" in professor

    assert "curator_decision.md" in curator
    assert "workspace-installed skills" in curator
    assert "source promotion" in curator


def test_runtime_recovery_entrypoints_reference_runtime_error_context_docs() -> None:
    manager_body = (
        REPO_ROOT / "src" / "millrace_ai" / "assets" / "entrypoints" / "planning" / "manager.md"
    ).read_text(encoding="utf-8")
    mechanic_body = (
        REPO_ROOT / "src" / "millrace_ai" / "assets" / "entrypoints" / "planning" / "mechanic.md"
    ).read_text(encoding="utf-8")
    troubleshooter_body = (
        REPO_ROOT
        / "src"
        / "millrace_ai"
        / "assets"
        / "entrypoints"
        / "execution"
        / "troubleshooter.md"
    ).read_text(encoding="utf-8")
    runtime_error_doc = REPO_ROOT / "docs" / "runtime" / "millrace-runtime-error-codes.md"

    assert runtime_error_doc.is_file()

    assert "mark the source spec as processed" not in manager_body
    assert "processed-spec disposition update when applicable" not in manager_body
    assert "source-spec disposition" in manager_body

    for body in (mechanic_body, troubleshooter_body):
        assert "runtime_error_code" in body
        assert "runtime_error_report_path" in body
        assert "runtime_error_catalog_path" in body


def test_planner_and_manager_assets_require_root_lineage_preservation() -> None:
    planner_body = (
        REPO_ROOT / "src" / "millrace_ai" / "assets" / "entrypoints" / "planning" / "planner.md"
    ).read_text(encoding="utf-8")
    manager_body = (
        REPO_ROOT / "src" / "millrace_ai" / "assets" / "entrypoints" / "planning" / "manager.md"
    ).read_text(encoding="utf-8")
    planner_skill = (
        REPO_ROOT
        / "src"
        / "millrace_ai"
        / "assets"
        / "skills"
        / "stage"
        / "planning"
        / "planner-core"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    manager_skill = (
        REPO_ROOT
        / "src"
        / "millrace_ai"
        / "assets"
        / "skills"
        / "stage"
        / "planning"
        / "manager-core"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Root-Idea-ID" in planner_body
    assert "Root-Spec-ID" in planner_body
    assert "preserve or repair the active root lineage ids" in planner_body.lower()

    assert "Root-Idea-ID" in manager_body
    assert "Root-Spec-ID" in manager_body
    assert "copy the active spec's root lineage ids onto every emitted task" in manager_body.lower()

    assert "preserve root lineage ids" in planner_skill.lower()
    assert "preserve root lineage ids on every emitted task" in manager_skill.lower()


def test_troubleshooter_assets_treat_runtime_prompt_contract_mismatches_as_locally_repairable() -> None:
    troubleshooter_body = (
        REPO_ROOT
        / "src"
        / "millrace_ai"
        / "assets"
        / "entrypoints"
        / "execution"
        / "troubleshooter.md"
    ).read_text(encoding="utf-8")
    troubleshooter_skill = (
        REPO_ROOT
        / "src"
        / "millrace_ai"
        / "assets"
        / "skills"
        / "stage"
        / "execution"
        / "troubleshooter-core"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "runtime prompt or contract mismatch" in troubleshooter_body
    assert "patch the local source of the defect and retry" in troubleshooter_body
    assert "prompt-contract mismatches" in troubleshooter_skill
    assert "locally repairable" in troubleshooter_skill


def test_runtime_docs_describe_skill_only_advisory_model() -> None:
    docs = "\n".join(
        [
            (REPO_ROOT / "docs" / "runtime" / "millrace-entrypoint-mapping.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "runtime" / "millrace-runtime-architecture.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "runtime" / "millrace-cli-reference.md").read_text(encoding="utf-8"),
        ]
    )

    assert "role_overlays" not in docs
    assert "role overlay" not in docs.lower()
    assert "Required Stage-Core Skill" in docs
    assert "Optional Secondary Skills" in docs
    assert "attached_skills" in docs
