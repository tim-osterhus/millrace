from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report = _load_module("repo_shape_report", Path("scripts/maintenance/repo_shape_report.py"))
source_hygiene = _load_module("test_source_hygiene_guardrail", Path("tests/test_source_hygiene.py"))


def test_suspicious_source_names_use_source_hygiene_allowlist() -> None:
    paths = (
        Path("src/millrace_ai/architecture/common.py"),
        Path("src/millrace_ai/runtime/helpers.py"),
        Path("src/millrace_ai/workspace/utils.py"),
    )

    assert report.GENERIC_MODULE_ALLOWLIST == source_hygiene._GENERIC_MODULE_ALLOWLIST
    assert report.suspicious_source_names(paths) == (
        Path("src/millrace_ai/runtime/helpers.py"),
        Path("src/millrace_ai/workspace/utils.py"),
    )


def test_import_cycle_logic_resolves_concrete_millrace_imports_and_ignores_type_checking(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "millrace_ai"
    source_root.mkdir(parents=True)
    (source_root / "__init__.py").write_text("", encoding="utf-8")
    (source_root / "alpha.py").write_text(
        "\n".join(
            (
                "from __future__ import annotations",
                "from typing import TYPE_CHECKING",
                "import millrace_ai.beta",
                "if TYPE_CHECKING:",
                "    from millrace_ai import gamma",
            )
        ),
        encoding="utf-8",
    )
    (source_root / "beta.py").write_text("import millrace_ai.alpha\n", encoding="utf-8")
    (source_root / "gamma.py").write_text("from millrace_ai import alpha\n", encoding="utf-8")
    paths = (
        Path("src/millrace_ai/__init__.py"),
        Path("src/millrace_ai/alpha.py"),
        Path("src/millrace_ai/beta.py"),
        Path("src/millrace_ai/gamma.py"),
    )

    graph = report.build_import_graph(tmp_path, paths)

    assert graph["millrace_ai.alpha"] == {"millrace_ai.beta"}
    assert ("millrace_ai.alpha", "millrace_ai.beta") in report._strongly_connected_components(graph)


def test_docs_reference_check_reports_missing_source_paths_once(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    (docs_root / "shape.md").write_text(
        "See `src/millrace_ai/runtime/live.py`, `src/millrace_ai/runtime`, and `src/millrace_ai/runtime/missing.py`.",
        encoding="utf-8",
    )
    tracked_paths = (
        Path("docs/shape.md"),
        Path("src/millrace_ai/runtime/live.py"),
    )

    missing = report.find_missing_doc_source_references(tmp_path, tracked_paths)

    assert missing == (
        report.MissingDocReference(
            doc_path=Path("docs/shape.md"),
            source_path=Path("src/millrace_ai/runtime/missing.py"),
        ),
    )


def test_docs_reference_check_uses_tracked_markdown_only(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    (docs_root / "tracked.md").write_text(
        "See `src/millrace_ai/runtime/live.py`.",
        encoding="utf-8",
    )
    (docs_root / "ignored.md").write_text(
        "See `src/millrace_ai/runtime/missing.py`.",
        encoding="utf-8",
    )
    tracked_paths = (
        Path("docs/tracked.md"),
        Path("src/millrace_ai/runtime/live.py"),
    )

    missing = report.find_missing_doc_source_references(tmp_path, tracked_paths)

    assert missing == ()


def test_missing_doc_references_are_integrity_failures() -> None:
    shape = report.RepoShape(
        largest_source_modules=(),
        largest_test_modules=(),
        fan_out=(),
        fan_in=(),
        import_cycles=(),
        suspicious_names=(),
        missing_doc_references=(
            report.MissingDocReference(
                doc_path=Path("docs/shape.md"),
                source_path=Path("src/millrace_ai/runtime/missing.py"),
            ),
        ),
        tracked_artifacts=(),
        ignored_artifacts=(),
    )

    assert shape.integrity_failures == (
        "docs reference missing source path: docs/shape.md -> src/millrace_ai/runtime/missing.py",
    )


def test_ignored_artifacts_are_advisory_not_integrity_failures() -> None:
    shape = report.RepoShape(
        largest_source_modules=(),
        largest_test_modules=(),
        fan_out=(),
        fan_in=(),
        import_cycles=(),
        suspicious_names=(),
        missing_doc_references=(),
        tracked_artifacts=(),
        ignored_artifacts=(Path(".ruff_cache"),),
    )

    rendered = report.render_report(shape)

    assert shape.integrity_failures == ()
    assert "## Ignored Local Artifacts\n- .ruff_cache" in rendered


def test_ignored_artifact_paths_collapse_to_cleanup_roots() -> None:
    ignored = report.ignored_artifact_paths(
        (
            Path(".venv/lib/python/site-packages/example.py"),
            Path(".venv/bin/python"),
            Path("src/millrace_ai/__pycache__/module.cpython-311.pyc"),
        )
    )

    assert ignored == (
        Path(".venv"),
        Path("src/millrace_ai/__pycache__"),
    )


def test_tracked_artifacts_are_integrity_failures() -> None:
    shape = report.RepoShape(
        largest_source_modules=(),
        largest_test_modules=(),
        fan_out=(),
        fan_in=(),
        import_cycles=(),
        suspicious_names=(),
        missing_doc_references=(),
        tracked_artifacts=(Path("build/package.py"),),
        ignored_artifacts=(),
    )

    assert shape.integrity_failures == ("tracked build/local artifact: build/package.py",)
