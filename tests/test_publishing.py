from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.publishing import (
    StagingPublishError,
    commit_staging_repo,
    load_staging_manifest,
    preflight_staging_publish,
    sync_staging_repo,
)
from millrace_engine.publishing.manifest import StagingManifestError, parse_staging_manifest
from tests.support import load_workspace_fixture


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", repo_dir.as_posix(), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_staging_manifest(workspace: Path, payload: str) -> None:
    manifest_path = workspace / "agents" / "staging_manifest.yml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(payload, encoding="utf-8")


def test_load_staging_manifest_uses_packaged_fallback_when_workspace_copy_is_absent(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = build_runtime_paths(load_engine_config(config_path).config)

    manifest = load_staging_manifest(paths)

    assert manifest.source_kind == "packaged"
    assert manifest.source_ref == "package:agents/staging_manifest.yml"
    assert manifest.manifest.version == 1
    assert manifest.manifest.paths == (
        "agents",
        "README.md",
        "ADVISOR.md",
        "OPERATOR_GUIDE.md",
        "docs/RUNTIME_DEEP_DIVE.md",
    )


@pytest.mark.parametrize(
    ("manifest_path", "message"),
    [
        ("/tmp/outside.txt", "repo-relative"),
        ("../outside.txt", "invalid path segment"),
    ],
)
def test_parse_staging_manifest_rejects_invalid_paths(
    manifest_path: str,
    message: str,
) -> None:
    payload = "\n".join(
        [
            "version: 1",
            "paths:",
            f"  - {manifest_path}",
            "",
        ]
    )

    with pytest.raises(StagingManifestError, match=message):
        parse_staging_manifest(payload)


def test_sync_staging_repo_copies_required_paths_and_removes_missing_optional_paths(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = build_runtime_paths(load_engine_config(config_path).config)
    (workspace / "README.md").write_text("workspace readme\n", encoding="utf-8")
    _write_staging_manifest(
        workspace,
        "\n".join(
            [
                "version: 1",
                "paths:",
                "  - README.md",
                "  - agents/status.md",
                "optional_paths:",
                "  - legacy.txt",
                "",
            ]
        ),
    )

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    (staging_dir / "legacy.txt").write_text("remove me\n", encoding="utf-8")

    report = sync_staging_repo(paths, staging_repo_dir=staging_dir)

    assert report.selection.manifest_source_kind == "workspace"
    assert report.selection.staging_repo_dir == staging_dir.resolve()
    assert (staging_dir / "README.md").read_text(encoding="utf-8") == "workspace readme\n"
    assert (staging_dir / "agents" / "status.md").exists()
    assert not (staging_dir / "legacy.txt").exists()
    assert [(entry.path, entry.action) for entry in report.entries] == [
        ("README.md", "synced"),
        ("agents/status.md", "synced"),
        ("legacy.txt", "removed_optional"),
    ]


def test_sync_staging_repo_rejects_sources_that_resolve_outside_workspace_root(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = build_runtime_paths(load_engine_config(config_path).config)
    outside_file = tmp_path / "escaped.txt"
    outside_file.write_text("outside workspace\n", encoding="utf-8")
    (workspace / "linked.txt").symlink_to(outside_file)
    _write_staging_manifest(
        workspace,
        "\n".join(
            [
                "version: 1",
                "paths:",
                "  - linked.txt",
                "",
            ]
        ),
    )

    with pytest.raises(StagingPublishError, match="escapes the workspace root"):
        sync_staging_repo(paths, staging_repo_dir=tmp_path / "staging")


def test_preflight_and_commit_reports_cover_ready_commit_and_no_change_states(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = build_runtime_paths(load_engine_config(config_path).config)
    (workspace / "README.md").write_text("workspace readme\n", encoding="utf-8")
    _write_staging_manifest(
        workspace,
        "\n".join(
            [
                "version: 1",
                "paths:",
                "  - README.md",
                "",
            ]
        ),
    )

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    assert _git(staging_dir, "init").returncode == 0
    assert _git(staging_dir, "config", "user.email", "tests@example.com").returncode == 0
    assert _git(staging_dir, "config", "user.name", "Millrace Tests").returncode == 0

    sync_staging_repo(paths, staging_repo_dir=staging_dir)

    preflight = preflight_staging_publish(
        paths,
        staging_repo_dir=staging_dir,
        commit_message="Test publish commit",
        push=False,
    )
    assert preflight.status == "ready"
    assert preflight.commit_allowed is True
    assert preflight.publish_allowed is False
    assert preflight.skip_reason == "push_disabled"
    assert preflight.changed_paths == ("README.md",)

    commit_report = commit_staging_repo(
        paths,
        staging_repo_dir=staging_dir,
        commit_message="Test publish commit",
        push=False,
    )
    assert commit_report.status == "committed"
    assert commit_report.marker == "SKIP_PUBLISH reason=push_disabled"
    assert commit_report.commit_sha is not None

    second_commit_report = commit_staging_repo(
        paths,
        staging_repo_dir=staging_dir,
        commit_message="Test publish commit",
        push=False,
    )
    assert second_commit_report.status == "no_changes"
    assert second_commit_report.marker == "NO_CHANGES"
