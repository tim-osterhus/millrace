from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field

from millrace_engine.contracts import ContractModel
from millrace_engine.research.audit import _extract_section_lines
from millrace_engine.research.parser_helpers import (
    _markdown_section,
    _parse_frontmatter_block,
    _parse_simple_frontmatter,
    _split_frontmatter_block,
)
from millrace_engine.research.persistence_helpers import _load_json_model, _load_json_object, _write_json_model


class _AliasedPayload(ContractModel):
    model_config = ConfigDict(populate_by_name=True)

    record_id: str = Field(alias="recordId")
    note: str | None = None


def test_split_frontmatter_block_returns_body_when_frontmatter_absent() -> None:
    text = "# Spec\n\nNo frontmatter here.\n"

    frontmatter, body = _split_frontmatter_block(text)

    assert frontmatter == {}
    assert body == text


def test_parse_frontmatter_block_handles_present_frontmatter_and_trailing_body() -> None:
    text = (
        "---\n"
        "audit_id: AUD-001\n"
        "status: incoming\n"
        "---\n"
        "# Audit\n"
    )

    frontmatter, body = _parse_frontmatter_block(text)

    assert frontmatter == {"audit_id": "AUD-001", "status": "incoming"}
    assert body == "# Audit\n"


def test_parse_simple_frontmatter_reads_goalspec_style_block() -> None:
    text = (
        "---\n"
        "spec_id: SPEC-ALPHA\n"
        "depends_on_specs: ['SPEC-BASE']\n"
        "---\n\n"
        "# Spec\n"
    )

    assert _parse_simple_frontmatter(text) == {
        "spec_id": "SPEC-ALPHA",
        "depends_on_specs": "['SPEC-BASE']",
    }


def test_markdown_section_keeps_nested_headings_inside_current_section() -> None:
    text = (
        "## Work Plan\n"
        "1. Prepare\n"
        "### Detail\n"
        "More context\n"
        "## Verification\n"
        "1. Done\n"
    )

    assert _markdown_section(text, "Work Plan") == "1. Prepare\n### Detail\nMore context"


def test_markdown_section_ignores_heading_markers_inside_code_fences() -> None:
    text = (
        "## Verification\n"
        "```md\n"
        "## Not a real heading\n"
        "```\n"
        "`pytest -q tests/test_research_dispatcher.py`\n"
        "## References\n"
        "`agents/specs/stable/golden/SPEC-ALPHA.md`\n"
    )

    assert _markdown_section(text, "Verification") == (
        "```md\n## Not a real heading\n```\n`pytest -q tests/test_research_dispatcher.py`"
    )


def test_extract_section_lines_ignores_code_fence_headings() -> None:
    text = (
        "---\n"
        "audit_id: AUD-002\n"
        "---\n"
        "# Audit\n\n"
        "## Commands\n"
        "```md\n"
        "- pytest -q tests/ignored.py\n"
        "## Summary\n"
        "```\n"
        "- pytest -q tests/test_research_dispatcher.py\n"
        "\n"
        "## Summary\n"
        "- Open issues detected: 0\n"
    )

    assert _extract_section_lines(text, section_names=frozenset({"commands"})) == (
        "pytest -q tests/test_research_dispatcher.py",
    )


def test_write_json_model_preserves_sorted_keys_newline_and_aliases(tmp_path: Path) -> None:
    path = tmp_path / "agents" / "records" / "sample.json"
    model = _AliasedPayload(record_id="REC-1", note="stable")

    _write_json_model(path, model, create_parent=True, by_alias=True)

    assert path.read_text(encoding="utf-8") == '{\n  "note": "stable",\n  "recordId": "REC-1"\n}\n'
    assert _load_json_object(path) == {"note": "stable", "recordId": "REC-1"}
    assert _load_json_model(path, _AliasedPayload) == model
