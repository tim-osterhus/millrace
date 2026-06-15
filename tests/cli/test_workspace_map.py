from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from millrace_ai import cli
from millrace_ai.paths import bootstrap_workspace, workspace_paths


def test_workspace_map_cli_refresh_validate_and_show(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    paths.root.joinpath("module.py").write_text("def public():\n    return 1\n", encoding="utf-8")
    paths.root.joinpath("workspaces/nested/should_skip.py").parent.mkdir(parents=True)
    paths.root.joinpath("workspaces/nested/should_skip.py").write_text("def skipped():\n    return 1\n", encoding="utf-8")
    runner = CliRunner()

    refresh = runner.invoke(cli.app, ["workspace-map", "refresh", "--workspace", str(paths.root)])
    assert refresh.exit_code == 0
    assert "workspace_map_refreshed: true" in refresh.output
    assert paths.workspace_map_generated_symbols_file.is_file()
    assert "workspaces/nested/should_skip.py" not in paths.workspace_map_generated_file_tree_file.read_text(
        encoding="utf-8"
    )

    validate = runner.invoke(cli.app, ["workspace-map", "validate", "--workspace", str(paths.root)])
    assert validate.exit_code == 0
    assert "ok: true" in validate.output

    show = runner.invoke(cli.app, ["workspace-map", "show", "--workspace", str(paths.root)])
    assert show.exit_code == 0
    assert "workspace-map:" in show.output
    assert "symbols:" in show.output


def test_workspace_map_cli_validate_fails_without_rewriting(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    paths.root.joinpath("module.py").write_text("def public():\n    return 1\n", encoding="utf-8")
    runner = CliRunner()
    runner.invoke(cli.app, ["workspace-map", "refresh", "--workspace", str(paths.root)])
    paths.workspace_map_generated_symbols_file.write_text("", encoding="utf-8")

    validate = runner.invoke(cli.app, ["workspace-map", "validate", "--workspace", str(paths.root)])

    assert validate.exit_code == 1
    assert "ok: false" in validate.output
    assert "stale: millrace-agents/workspace-map/generated/symbols.jsonl" in validate.output


def test_workspace_map_cli_refresh_skips_external_directory_symlink(tmp_path: Path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external_dir.joinpath("linked_module.py").write_text("def outside():\n    return 2\n", encoding="utf-8")
    paths.root.joinpath("module.py").write_text("def public():\n    return 1\n", encoding="utf-8")
    paths.root.joinpath("external-dir-link").symlink_to(external_dir, target_is_directory=True)
    runner = CliRunner()

    refresh = runner.invoke(cli.app, ["workspace-map", "refresh", "--workspace", str(paths.root)])

    assert refresh.exit_code == 0
    assert "warnings: 1" in refresh.output
    freshness = json.loads(paths.workspace_map_generated_freshness_file.read_text(encoding="utf-8"))
    assert freshness["warnings"] == ["skipped directory symlink: external-dir-link"]
    assert "linked_module.py" not in paths.workspace_map_generated_file_tree_file.read_text(encoding="utf-8")

    validate = runner.invoke(cli.app, ["workspace-map", "validate", "--workspace", str(paths.root)])

    assert validate.exit_code == 0
    assert "ok: true" in validate.output
