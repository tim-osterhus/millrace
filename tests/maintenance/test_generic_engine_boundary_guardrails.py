from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ADR references that must appear in docs/adr/README.md and docs/doc-index.md
# docs/adr/README.md uses the filename form (e.g. 0012-core-kernel-boundary.md)
EXPECTED_ADR_FILE_NAMES = (
    "0012-core-kernel-boundary.md",
    "0013-generic-stage-and-plane-registry.md",
    "0014-runtime-operation-step-interpreter.md",
    "0015-extension-package-manifests.md",
)

# docs/doc-index.md uses the relative path form (e.g. adr/0012-core-kernel-boundary.md)
EXPECTED_ADR_DOC_INDEX_REFS = (
    "adr/0012-core-kernel-boundary.md",
    "adr/0013-generic-stage-and-plane-registry.md",
    "adr/0014-runtime-operation-step-interpreter.md",
    "adr/0015-extension-package-manifests.md",
)

# Config-data directories that must not contain Python source files
CONFIG_DATA_DIRS = (
    "src/millrace_ai/assets/modes",
    "src/millrace_ai/assets/graphs",
    "src/millrace_ai/assets/loops",
    "src/millrace_ai/assets/registry",
)

# Canonical migration ledger path
CANONICAL_LEDGER = "docs/maintenance/refactor-candidate-register.md"


def _docs_path(rel: str) -> Path:
    return REPO_ROOT / rel


def test_docs_adr_readme_references_all_four_boundary_adrs() -> None:
    """Fail if docs/adr/README.md stops referencing ADR-0012 through ADR-0015."""
    path = _docs_path("docs/adr/README.md")
    text = path.read_text(encoding="utf-8")

    missing = [ref for ref in EXPECTED_ADR_FILE_NAMES if ref not in text]
    assert not missing, (
        f"docs/adr/README.md is missing references to: {', '.join(missing)}"
    )


def test_doc_index_references_all_four_boundary_adrs() -> None:
    """Fail if docs/doc-index.md stops referencing ADR-0012 through ADR-0015."""
    path = _docs_path("docs/doc-index.md")
    text = path.read_text(encoding="utf-8")

    missing = [ref for ref in EXPECTED_ADR_DOC_INDEX_REFS if ref not in text]
    assert not missing, (
        f"docs/doc-index.md is missing references to: {', '.join(missing)}"
    )


def test_technical_overview_uses_four_layer_authority_vocabulary() -> None:
    """Fail if docs/millrace-technical-overview.md drops the four-layer vocabulary."""
    path = _docs_path("docs/millrace-technical-overview.md")
    text = path.read_text(encoding="utf-8")

    assert "four-layer authority model" in text, (
        "docs/millrace-technical-overview.md no longer mentions the 'four-layer authority model'"
    )


def test_source_package_map_uses_four_layer_vocabulary_and_marks_prospective_boundaries() -> (
    None
):
    """Fail if docs/source-package-map.md drops the four-layer vocabulary or stops marking
    prospective boundary packages as not yet created."""
    path = _docs_path("docs/source-package-map.md")
    text = path.read_text(encoding="utf-8")

    assert "four-layer" in text, (
        "docs/source-package-map.md no longer uses the four-layer vocabulary"
    )
    assert "not yet created" in text, (
        "docs/source-package-map.md no longer marks prospective boundary packages as "
        "'not yet created'"
    )


def test_config_data_directories_contain_no_python_source_files() -> None:
    """Fail if any config-data directory contains Python source files.

    Scoped to: src/millrace_ai/assets/modes/, src/millrace_ai/assets/graphs/,
    src/millrace_ai/assets/loops/, src/millrace_ai/assets/registry/.
    Does not flag unrelated docs, ADRs, or architecture directories.
    """
    violations: list[str] = []

    for rel_dir in CONFIG_DATA_DIRS:
        abs_dir = REPO_ROOT / rel_dir
        if not abs_dir.is_dir():
            continue
        for py_file in sorted(abs_dir.rglob("*.py")):
            # Exclude __init__.py files that are legitimate package markers
            # if they exist as part of the Python import system
            if py_file.name == "__init__.py" and py_file.parent == abs_dir:
                continue
            violations.append(py_file.relative_to(REPO_ROOT).as_posix())

    assert violations == [], (
        "Config-data directories contain Python source files:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_only_one_migration_ledger_exists() -> None:
    """Fail if a second migration ledger appears outside the canonical register.

    The canonical migration ledger is docs/maintenance/refactor-candidate-register.md.
    This checks that no other file in docs/ has a name containing 'ledger' (case-insensitive)
    or contains the phrase 'migration ledger' in its text content.
    """
    ledger_names: list[str] = []
    for path in sorted((REPO_ROOT / "docs").rglob("*ledger*")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == CANONICAL_LEDGER:
            continue
        ledger_names.append(rel)

    # Also check for files that claim to be a migration ledger in their content
    content_ledgers: list[str] = []
    for path in sorted((REPO_ROOT / "docs").rglob("*.md")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == CANONICAL_LEDGER:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        # If the file claims to be a migration ledger in its defined purpose
        if "migration ledger" in text.lower():
            content_ledgers.append(rel)

    violations = sorted(set(ledger_names + content_ledgers))
    assert violations == [], (
        f"Additional migration ledger files found outside {CANONICAL_LEDGER}:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
