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
    "testing",
)

FORBIDDEN_LAD_C_AUTHORITY_LITERALS = (
    "Plane.LEARNING",
    "LearningStageName",
    "learning_request",
    "learning_enabled_millrace",
    "learning_lad_",
    "learning-standard",
    "learning/standard.json",
    "millrace_ai.runtime.learning_triggers",
    "millrace_ai.extensions.builtin.learning_trigger_handler",
    "QueueStore",
    "analyst",
    "professor",
    "curator",
    "librarian",
)


def _runtime_python_files() -> list[Path]:
    paths: list[Path] = []
    for package_name in GENERIC_RUNTIME_PACKAGE_NAMES:
        package_path = PACKAGE_ROOT / package_name
        assert package_path.exists(), f"missing package: {package_path}"
        paths.extend(package_path.rglob("*.py"))
    return sorted(paths)


def _literal_matches(text: str) -> list[str]:
    matches: list[str] = []
    for literal in FORBIDDEN_LAD_C_AUTHORITY_LITERALS:
        if literal.endswith("_"):
            pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(literal)}")
        else:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])"
            )
        if pattern.search(text):
            matches.append(literal)
    return matches


def test_generic_runtime_omits_lad_c_learning_authority_surfaces() -> None:
    offenders: list[str] = []
    for path in _runtime_python_files():
        text = path.read_text(encoding="utf-8")
        for literal in _literal_matches(text):
            offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{literal}")

    assert offenders == []


def test_lad_learning_workflow_donor_is_absent() -> None:
    fixture_path = PACKAGE_ROOT / "workflows" / "lad_learning.py"
    assert not fixture_path.exists()


def test_detector_catches_lad_c_learning_authority_probe() -> None:
    text = "\n".join(
        (
            "Plane.LEARNING",
            "LearningStageName",
            "learning_request",
            "learning_lad_codex",
            "millrace_ai.runtime.learning_triggers",
            "librarian",
        )
    )

    assert _literal_matches(text) == [
        "Plane.LEARNING",
        "LearningStageName",
        "learning_request",
        "learning_lad_",
        "millrace_ai.runtime.learning_triggers",
        "librarian",
    ]
