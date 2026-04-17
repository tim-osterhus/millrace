from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from millrace_ai.entrypoints import parse_markdown_asset

REPO_ROOT = Path(__file__).resolve().parents[2]
CREATOR_ROOT = REPO_ROOT / "src" / "millrace_ai" / "assets" / "skills" / "millrace-skill-creator"
REQUIRED_FILES = (
    "SKILL.md",
    "references/hybrid-format.md",
    "references/donor-synthesis.md",
    "scripts/_shared.py",
    "scripts/scaffold_skill.py",
    "scripts/lint_skill.py",
    "scripts/evaluate_skill.py",
    "evals/creator_smoke_cases.json",
    "evals/pilot_shape_cases.json",
)
REQUIRED_SECTION_TITLES = (
    "Purpose",
    "Quick Start",
    "Operating Constraints",
    "Inputs This Skill Expects",
    "Output Contract",
    "Procedure",
    "Pitfalls And Gotchas",
    "Progressive Disclosure",
    "Verification Pattern",
)


def _copy_creator_package(tmp_path: Path) -> Path:
    copied_root = tmp_path / "copied-creator"
    shutil.copytree(CREATOR_ROOT, copied_root)
    return copied_root


def _run_script(package_root: Path, script_name: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    script_path = package_root / "scripts" / script_name
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(script_path), *args],
        cwd=cwd or package_root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result


def _section_titles(body: str) -> list[str]:
    return [line.removeprefix("## ").strip() for line in body.splitlines() if line.startswith("## ")]


def _assert_no_support_dirs(root: Path) -> None:
    assert not (root / "references").exists()
    assert not (root / "scripts").exists()
    assert not (root / "evals").exists()


def test_creator_package_has_expected_files_and_contract() -> None:
    for relative_path in REQUIRED_FILES:
        assert (CREATOR_ROOT / relative_path).is_file(), relative_path

    asset = parse_markdown_asset(CREATOR_ROOT / "SKILL.md")
    assert asset.manifest["asset_type"] == "skill"
    assert asset.manifest["asset_id"] == "millrace-skill-creator"
    assert asset.manifest["advisory_only"] is True
    assert _section_titles(asset.body) == list(REQUIRED_SECTION_TITLES)


def test_scaffold_defaults_to_minimal_portable_and_support_dirs_are_optional(tmp_path: Path) -> None:
    package_root = _copy_creator_package(tmp_path)

    portable_root = tmp_path / "portable-skill"
    _run_script(package_root, "scaffold_skill.py", str(portable_root))

    portable_skill = portable_root / "SKILL.md"
    portable_text = portable_skill.read_text(encoding="utf-8")
    assert not portable_text.startswith("---")
    assert _section_titles(portable_text) == list(REQUIRED_SECTION_TITLES)
    _assert_no_support_dirs(portable_root)

    supported_root = tmp_path / "supported-skill"
    _run_script(
        package_root,
        "scaffold_skill.py",
        str(supported_root),
        "--include",
        "references",
        "--include",
        "scripts",
        "--include",
        "evals",
    )

    assert (supported_root / "references" / "hybrid-format.md").is_file()
    assert (supported_root / "references" / "donor-synthesis.md").is_file()
    assert (supported_root / "scripts" / "scaffold_skill.py").is_file()
    assert (supported_root / "scripts" / "_shared.py").is_file()
    assert (supported_root / "evals" / "skill_smoke_cases.json").is_file()


def test_opinionated_scaffold_requires_package_specific_inputs_and_omits_optional_metadata(tmp_path: Path) -> None:
    package_root = _copy_creator_package(tmp_path)

    opinionated_root = tmp_path / "opinionated-skill"
    _run_script(
        package_root,
        "scaffold_skill.py",
        str(opinionated_root),
        "--profile",
        "millrace-opinionated",
        "--asset-id",
        "sample-skill",
        "--description",
        "Sample skill description",
        "--capability-type",
        "documentation",
        "--forbidden-claim",
        "queue_selection",
        "--forbidden-claim",
        "routing",
    )

    opinionated_asset = parse_markdown_asset(opinionated_root / "SKILL.md")
    assert opinionated_asset.manifest["asset_type"] == "skill"
    assert opinionated_asset.manifest["asset_id"] == "sample-skill"
    assert opinionated_asset.manifest["description"] == "Sample skill description"
    assert opinionated_asset.manifest["capability_type"] == "documentation"
    assert opinionated_asset.manifest["forbidden_claims"] == ["queue_selection", "routing"]
    assert "recommended_for_stages" not in opinionated_asset.manifest
    assert _section_titles(opinionated_asset.body) == list(REQUIRED_SECTION_TITLES)


def test_opinionated_scaffold_quotes_scalar_like_string_values(tmp_path: Path) -> None:
    package_root = _copy_creator_package(tmp_path)

    scalar_root = tmp_path / "scalar-like-skill"
    _run_script(
        package_root,
        "scaffold_skill.py",
        str(scalar_root),
        "--profile",
        "millrace-opinionated",
        "--asset-id",
        "scalar-like-skill",
        "--description",
        "true",
        "--capability-type",
        "documentation",
        "--forbidden-claim",
        "queue_selection",
    )

    asset = parse_markdown_asset(scalar_root / "SKILL.md")
    assert asset.manifest["description"] == "true"
    assert isinstance(asset.manifest["description"], str)

    lint_result = _run_script(package_root, "lint_skill.py", str(scalar_root))
    assert "ok: true" in lint_result.stdout.lower()


def test_lint_validates_portable_and_opinionated_packages_without_support_files(tmp_path: Path) -> None:
    package_root = _copy_creator_package(tmp_path)

    portable_root = tmp_path / "portable-skill"
    _run_script(package_root, "scaffold_skill.py", str(portable_root))

    first = _run_script(package_root, "lint_skill.py", str(portable_root))
    second = _run_script(package_root, "lint_skill.py", str(portable_root))
    assert first.stdout == second.stdout
    assert "ok: true" in first.stdout.lower()

    opinionated_root = tmp_path / "opinionated-skill"
    _run_script(
        package_root,
        "scaffold_skill.py",
        str(opinionated_root),
        "--profile",
        "millrace-opinionated",
        "--asset-id",
        "sample-skill",
        "--description",
        "Sample skill description",
        "--capability-type",
        "documentation",
        "--forbidden-claim",
        "queue_selection",
    )

    opinionated_lint = _run_script(package_root, "lint_skill.py", str(opinionated_root))
    assert "ok: true" in opinionated_lint.stdout.lower()

    scripts_root = tmp_path / "scripts-skill"
    _run_script(package_root, "scaffold_skill.py", str(scripts_root), "--include", "scripts")
    scripts_lint = _run_script(package_root, "lint_skill.py", str(scripts_root))
    assert "ok: true" in scripts_lint.stdout.lower()


def test_evaluate_runs_package_local_and_supplied_fixture_checks_with_case_filtering(tmp_path: Path) -> None:
    package_root = _copy_creator_package(tmp_path)
    package_instance = tmp_path / "fixture-skill"
    _run_script(package_root, "scaffold_skill.py", str(package_instance), "--include", "evals")

    package_local = _run_script(package_root, "evaluate_skill.py", str(package_instance))
    assert "package-smoke" in package_local.stdout
    assert "PASS" in package_local.stdout

    supplied_fixture = tmp_path / "external_cases.json"
    supplied_fixture.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "external-smoke",
                        "description": "Supplied fixture check.",
                        "required_paths": ["SKILL.md"],
                        "required_skill_sections": list(REQUIRED_SECTION_TITLES),
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    filtered = _run_script(
        package_root,
        "evaluate_skill.py",
        str(package_instance),
        "--fixtures",
        str(supplied_fixture),
        "--case-id",
        "external-smoke",
    )
    assert "external-smoke" in filtered.stdout
    assert "package-smoke" not in filtered.stdout
