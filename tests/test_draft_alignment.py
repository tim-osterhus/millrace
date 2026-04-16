from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DOCS_DIR = REPO_ROOT / "docs" / "runtime"
DOC_INDEX = RUNTIME_DOCS_DIR / "README.md"
ENTRYPOINT_MAPPING_DOC = RUNTIME_DOCS_DIR / "millrace-entrypoint-mapping.md"
PACKAGED_ENTRYPOINTS_DIR = REPO_ROOT / "millrace_ai" / "assets" / "entrypoints"

ENTRYPOINT_MAPPING_ROW = re.compile(
    r"- `(?P<draft>lab/specs/drafts/entrypoints/(?P<plane>execution|planning)/(?P<filename>[^`]+\.md))` -> "
    r"`(?P<packaged>millrace_ai/assets/entrypoints/(?P=plane)/(?P=filename))` -> "
    r"`(?P<deployed>millrace-agents/entrypoints/(?P=plane)/(?P=filename))`"
)
INDEX_DOC_ROW = re.compile(r"- `(?P<path>[^`]+\.md)`:")


def _read_required_doc(path: Path) -> str:
    assert path.is_file(), f"Missing required documentation file: {path}"
    return path.read_text(encoding="utf-8")


def _parse_entrypoint_mapping_rows(doc_text: str) -> list[re.Match[str]]:
    matches = list(ENTRYPOINT_MAPPING_ROW.finditer(doc_text))
    assert matches, "Entrypoint mapping doc must include mapping rows"
    return matches


def test_coverage_matrix_lists_every_canonical_draft_with_mappings() -> None:
    mapping_text = _read_required_doc(ENTRYPOINT_MAPPING_DOC)
    mapping_rows = _parse_entrypoint_mapping_rows(mapping_text)

    mapped_packaged_paths = {match.group("packaged") for match in mapping_rows}
    packaged_paths = {
        str(path.relative_to(REPO_ROOT))
        for path in PACKAGED_ENTRYPOINTS_DIR.rglob("*.md")
    }
    assert mapped_packaged_paths == packaged_paths

    for relative_path in sorted(mapped_packaged_paths):
        assert (REPO_ROOT / relative_path).is_file()


def test_open_decision_gates_are_explicit_and_source_linked() -> None:
    index_text = _read_required_doc(DOC_INDEX)
    referenced_docs = INDEX_DOC_ROW.findall(index_text)
    assert referenced_docs, "Runtime docs index must list maintained runtime docs"

    for relative_doc in referenced_docs:
        if relative_doc.startswith("docs/"):
            candidate = REPO_ROOT / relative_doc
        else:
            candidate = RUNTIME_DOCS_DIR / relative_doc
        assert candidate.is_file(), f"Runtime docs index references missing doc: {relative_doc}"


def test_referenced_draft_filenames_exist_on_disk() -> None:
    docs_text = "\n".join(
        (
            _read_required_doc(DOC_INDEX),
            _read_required_doc(ENTRYPOINT_MAPPING_DOC),
        )
    )
    assert "lab/specs/pending/" not in docs_text, (
        "Runtime docs should not depend on pending workspace-only spec paths."
    )
