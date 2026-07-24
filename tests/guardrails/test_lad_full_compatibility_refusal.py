from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "millrace"

GENERIC_RUNTIME_PACKAGE_NAMES = (
    "compiler",
    "contracts",
    "kernel",
    "operator",
    "substrate",
)

FORBIDDEN_FULL_LAD_AUTHORITY_LITERALS = (
    "Plane.EXECUTION",
    "Plane.PLANNING",
    "Plane.LEARNING",
    "ExecutionStageName",
    "PlanningStageName",
    "LearningStageName",
    "StageName",
    "WorkItemKind",
    "lad_codex",
    "lad_pi",
    "learning_lad_",
    "learning_enabled_millrace",
    "blueprint_lad_codex",
    "blueprint_learning_lad_codex",
    "blueprint_codex",
    "blueprint_learning_codex",
)


def _runtime_python_files() -> list[Path]:
    paths: list[Path] = []
    for package_name in GENERIC_RUNTIME_PACKAGE_NAMES:
        package_path = PACKAGE_ROOT / package_name
        assert package_path.exists()
        paths.extend(package_path.rglob("*.py"))
    return sorted(paths)


def _literal_matches(text: str) -> list[str]:
    matches: list[str] = []
    for literal in FORBIDDEN_FULL_LAD_AUTHORITY_LITERALS:
        if literal.endswith("_"):
            pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(literal)}")
        else:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])"
            )
        if pattern.search(text):
            matches.append(literal)
    return matches


def test_generic_runtime_has_no_lad_plane_branches() -> None:
    offenders: list[str] = []
    for path in _runtime_python_files():
        text = path.read_text(encoding="utf-8")
        for literal in _literal_matches(text):
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{literal}")

    assert offenders == []


def test_selected_full_lad_workflow_data_is_outside_guardrail() -> None:
    fixture_path = PACKAGE_ROOT / "workflows" / "lad_learning.py"
    text = fixture_path.read_text(encoding="utf-8")

    assert "lad.full" in text
    assert "learning_request" in text
    assert "learning.trigger.execution.needs_planning" in text


def test_detector_catches_full_lad_legacy_authority_probe() -> None:
    text = "\n".join(
        (
            "Plane.EXECUTION",
            "Plane.PLANNING",
            "Plane.LEARNING",
            "WorkItemKind.LEARNING_REQUEST",
            "learning_lad_codex",
            "blueprint_learning_lad_codex",
        )
    )

    assert _literal_matches(text) == [
        "Plane.EXECUTION",
        "Plane.PLANNING",
        "Plane.LEARNING",
        "WorkItemKind",
        "learning_lad_",
        "blueprint_learning_lad_codex",
    ]
