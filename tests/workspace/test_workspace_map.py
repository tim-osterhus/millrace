from __future__ import annotations

import json
from pathlib import Path

from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.workspace_map import refresh_workspace_map, show_workspace_map, validate_workspace_map


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def test_refresh_writes_deterministic_workspace_map_outputs(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    package_dir = paths.root / "src" / "sample_pkg"
    package_dir.mkdir(parents=True)
    package_dir.joinpath("__init__.py").write_text("from .core import PublicThing\n", encoding="utf-8")
    package_dir.joinpath("core.py").write_text(
        "import json\n\nclass PublicThing:\n    def method(self):\n        return json.dumps({})\n\n"
        "def _private_helper():\n    return None\n",
        encoding="utf-8",
    )
    tests_dir = paths.root / "tests"
    tests_dir.mkdir()
    tests_dir.joinpath("test_core.py").write_text(
        "from sample_pkg.core import PublicThing\n\n\ndef test_public_thing():\n    assert PublicThing()\n",
        encoding="utf-8",
    )
    paths.root.joinpath("README.md").write_text("See `src/sample_pkg/core.py`.\n", encoding="utf-8")

    first = refresh_workspace_map(paths)
    first_payloads = {
        path.name: path.read_text(encoding="utf-8")
        for path in (
            paths.workspace_map_manifest_file,
            paths.workspace_map_generated_file_tree_file,
            paths.workspace_map_generated_repo_map_file,
            paths.workspace_map_generated_symbols_file,
            paths.workspace_map_generated_imports_file,
            paths.workspace_map_generated_reverse_imports_file,
            paths.workspace_map_generated_public_api_file,
            paths.workspace_map_generated_tests_map_file,
            paths.workspace_map_generated_docs_references_file,
            paths.workspace_map_generated_freshness_file,
        )
    }
    second = refresh_workspace_map(paths)

    assert second.fingerprint == first.fingerprint
    assert {
        path.name: path.read_text(encoding="utf-8")
        for path in (
            paths.workspace_map_manifest_file,
            paths.workspace_map_generated_file_tree_file,
            paths.workspace_map_generated_repo_map_file,
            paths.workspace_map_generated_symbols_file,
            paths.workspace_map_generated_imports_file,
            paths.workspace_map_generated_reverse_imports_file,
            paths.workspace_map_generated_public_api_file,
            paths.workspace_map_generated_tests_map_file,
            paths.workspace_map_generated_docs_references_file,
            paths.workspace_map_generated_freshness_file,
        )
    } == first_payloads

    manifest = json.loads(paths.workspace_map_manifest_file.read_text(encoding="utf-8"))
    assert manifest["mode"] == "full-rebuild"
    assert "millrace-agents/workspace-map/generated/symbols.jsonl" in manifest["outputs"]
    assert "src/sample_pkg/core.py" in paths.workspace_map_generated_file_tree_file.read_text(encoding="utf-8")
    assert "millrace-agents/workspace-map/generated" not in paths.workspace_map_generated_file_tree_file.read_text(
        encoding="utf-8"
    )
    assert '"name":"PublicThing"' in paths.workspace_map_generated_symbols_file.read_text(encoding="utf-8")
    assert '"name":"_private_helper"' not in paths.workspace_map_generated_public_api_file.read_text(encoding="utf-8")
    assert '"path":"tests/test_core.py"' in paths.workspace_map_generated_tests_map_file.read_text(encoding="utf-8")
    assert '"target":"src/sample_pkg/core.py"' in paths.workspace_map_generated_docs_references_file.read_text(
        encoding="utf-8"
    )


def test_refresh_scans_supported_text_extensions_and_excludes_runtime_surfaces(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    supported_paths = (
        "pkg/__init__.py",
        "pkg/types.pyi",
        "README.md",
        "docs/usage.rst",
        "pyproject.toml",
        "config/settings.json",
        "config/settings.yaml",
        "config/settings.yml",
        "notes.txt",
        "scripts/run.sh",
        "web/app.js",
        "web/app.ts",
        "web/view.tsx",
        "web/view.jsx",
        "web/styles.css",
        "web/index.html",
    )
    for relative_path in supported_paths:
        file_path = paths.root / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("content\n", encoding="utf-8")
    for runtime_relative_path in (
        "entrypoints/execution/builder.md",
        "skills/stage/execution/builder-core/SKILL.md",
        "runs/run-1/runner_prompt.builder.md",
        "state/runtime_snapshot.json",
        "workspace-map/generated/symbols.jsonl",
    ):
        file_path = paths.runtime_root / runtime_relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("runtime\n", encoding="utf-8")

    refresh_workspace_map(paths)

    file_tree = paths.workspace_map_generated_file_tree_file.read_text(encoding="utf-8")
    for relative_path in supported_paths:
        assert f"- {relative_path}" in file_tree
    assert "millrace-agents/" not in file_tree


def test_refresh_excludes_root_workspaces_from_generated_outputs(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    skipped_relative_path = "workspaces/nested/should_skip.py"
    paths.root.joinpath(skipped_relative_path).parent.mkdir(parents=True)
    paths.root.joinpath(skipped_relative_path).write_text("def skipped():\n    return True\n", encoding="utf-8")
    kept_relative_path = "src/workspaces/kept.py"
    paths.root.joinpath(kept_relative_path).parent.mkdir(parents=True)
    paths.root.joinpath(kept_relative_path).write_text("def kept():\n    return True\n", encoding="utf-8")

    refresh_workspace_map(paths)

    generated_path_outputs = (
        paths.workspace_map_generated_file_tree_file,
        paths.workspace_map_generated_repo_map_file,
        paths.workspace_map_generated_symbols_file,
        paths.workspace_map_generated_imports_file,
        paths.workspace_map_generated_reverse_imports_file,
        paths.workspace_map_generated_public_api_file,
        paths.workspace_map_generated_tests_map_file,
        paths.workspace_map_generated_docs_references_file,
        paths.workspace_map_generated_freshness_file,
        paths.workspace_map_manifest_file,
    )
    for output_path in generated_path_outputs:
        output = output_path.read_text(encoding="utf-8")
        assert skipped_relative_path not in output

    assert kept_relative_path in paths.workspace_map_generated_file_tree_file.read_text(encoding="utf-8")
    assert validate_workspace_map(paths) == ()


def test_refresh_writes_schema_versioned_json_and_jsonl_records(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    paths.root.joinpath("module.py").write_text("import json\n\ndef public():\n    return json.dumps({})\n", encoding="utf-8")
    refresh_workspace_map(paths)

    for json_path in (
        paths.workspace_map_manifest_file,
        paths.workspace_map_generated_imports_file,
        paths.workspace_map_generated_reverse_imports_file,
        paths.workspace_map_generated_freshness_file,
    ):
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == "1.0"

    imports_payload = json.loads(paths.workspace_map_generated_imports_file.read_text(encoding="utf-8"))
    assert imports_payload["records"] == [{"imports": ["json"], "path": "module.py"}]

    jsonl_paths = (
        paths.workspace_map_generated_symbols_file,
        paths.workspace_map_generated_public_api_file,
        paths.workspace_map_generated_tests_map_file,
        paths.workspace_map_generated_docs_references_file,
    )
    for jsonl_path in jsonl_paths:
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if line:
                assert json.loads(line)["schema_version"] == "1.0"


def test_validate_detects_stale_malformed_and_non_workspace_paths(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    paths.root.joinpath("module.py").write_text("def public():\n    return 1\n", encoding="utf-8")
    refresh_workspace_map(paths)

    assert validate_workspace_map(paths) == ()

    paths.workspace_map_generated_imports_file.write_text("{not json\n", encoding="utf-8")
    paths.workspace_map_generated_symbols_file.write_text('{"path":"../escape.py","name":"bad"}\n', encoding="utf-8")

    issues = validate_workspace_map(paths)

    codes = {(issue.code, issue.path) for issue in issues}
    assert ("malformed", "millrace-agents/workspace-map/generated/imports.json") in codes
    assert ("non_workspace_confined", "millrace-agents/workspace-map/generated/symbols.jsonl") in codes
    assert ("stale", "millrace-agents/workspace-map/generated/imports.json") in codes


def test_validate_detects_schema_invalid_records_without_rewriting(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    paths.root.joinpath("module.py").write_text("def public():\n    return 1\n", encoding="utf-8")
    refresh_workspace_map(paths)
    original_symbols = paths.workspace_map_generated_symbols_file.read_text(encoding="utf-8")
    invalid_manifest = json.loads(paths.workspace_map_manifest_file.read_text(encoding="utf-8"))
    invalid_manifest["schema_version"] = "0.9"
    paths.workspace_map_manifest_file.write_text(json.dumps(invalid_manifest), encoding="utf-8")
    paths.workspace_map_generated_symbols_file.write_text(
        '{"schema_version":"0.9","path":"module.py","name":"public"}\n'
        '{"schema_version":"1.0","path":"/escape.py","name":"bad"}\n',
        encoding="utf-8",
    )

    issues = validate_workspace_map(paths)

    codes = {(issue.code, issue.path) for issue in issues}
    assert ("schema_invalid", "millrace-agents/workspace-map/manifest.json") in codes
    assert ("schema_invalid", "millrace-agents/workspace-map/generated/symbols.jsonl") in codes
    assert ("non_workspace_confined", "millrace-agents/workspace-map/generated/symbols.jsonl") in codes
    assert paths.workspace_map_generated_symbols_file.read_text(encoding="utf-8") != original_symbols


def test_show_reads_generated_summary(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    paths.root.joinpath("module.py").write_text("def public():\n    return 1\n", encoding="utf-8")
    refresh_workspace_map(paths)

    output = show_workspace_map(paths)

    assert "workspace-map:" in output
    assert "status: fresh" in output
    assert "symbols:" in output
