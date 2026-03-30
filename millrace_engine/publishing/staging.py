"""Manifest-driven staging sync and publish helpers."""

from __future__ import annotations

from pathlib import Path
from shutil import copy2, copytree, rmtree
from subprocess import CompletedProcess, run
from typing import TYPE_CHECKING, Literal

from pydantic import Field, field_validator

from ..contracts import ContractModel
from .manifest import LoadedStagingManifest, load_staging_manifest

if TYPE_CHECKING:
    from ..paths import RuntimePaths


class StagingPublishError(RuntimeError):
    """Raised when staging sync or publish work cannot complete."""


def _normalize_optional_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    text = value.strip()
    if not text:
        return None
    return Path(text)


def _normalize_path(value: str | Path) -> Path:
    return Path(value) if not isinstance(value, Path) else value


def _changed_paths_from_porcelain(stdout: str) -> tuple[str, ...]:
    changed: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        changed.append(line[3:] if len(line) > 3 else line)
    return tuple(changed)


def _ensure_relative_to_root(root: Path, candidate: Path, *, label: str) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise StagingPublishError(f"{label} escapes the workspace root: {candidate}") from exc


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        rmtree(path)
        return
    path.unlink()


def _git(repo_dir: Path, *args: str, check: bool = False) -> CompletedProcess[str]:
    return run(
        ["git", "-C", repo_dir.as_posix(), *args],
        capture_output=True,
        text=True,
        check=check,
    )


class StagingSelectionReport(ContractModel):
    """Deterministic staging-target selection."""

    workspace_root: Path
    staging_repo_dir: Path
    manifest_source_kind: Literal["workspace", "packaged"]
    manifest_source_ref: str
    manifest_version: int = Field(ge=1)
    required_paths: tuple[str, ...]
    optional_paths: tuple[str, ...] = ()

    @field_validator("workspace_root", "staging_repo_dir", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path) -> Path:
        return _normalize_path(value)

    @property
    def selected_paths(self) -> tuple[str, ...]:
        return self.required_paths + self.optional_paths


class StagingSyncEntry(ContractModel):
    """One sync decision for a manifest path."""

    path: str
    required: bool
    action: Literal["synced", "removed_optional", "skipped_optional"]
    source_kind: Literal["file", "directory"] | None = None


class StagingSyncReport(ContractModel):
    """Deterministic result of one staging sync."""

    selection: StagingSelectionReport
    created_staging_dir: bool
    entries: tuple[StagingSyncEntry, ...]

    @property
    def required_path_count(self) -> int:
        return len(self.selection.required_paths)

    @property
    def optional_path_count(self) -> int:
        return len(self.selection.optional_paths)


class PublishPreflightReport(ContractModel):
    """Read-only publish readiness report for one staging repo."""

    selection: StagingSelectionReport
    commit_message: str
    push_requested: bool
    git_worktree_present: bool
    git_worktree_valid: bool
    branch: str | None = None
    origin_configured: bool = False
    has_changes: bool = False
    changed_paths: tuple[str, ...] = ()
    commit_allowed: bool
    publish_allowed: bool
    status: Literal["ready", "no_changes", "skip_publish"]
    skip_reason: str | None = None

    @field_validator("commit_message", "skip_reason")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None


class PublishCommitReport(ContractModel):
    """Deterministic result of one commit/publish operation."""

    selection: StagingSelectionReport
    commit_message: str
    push_requested: bool
    push_performed: bool
    branch: str | None = None
    status: Literal["skipped", "no_changes", "committed", "pushed"]
    marker: str
    commit_sha: str | None = None
    skip_reason: str | None = None

    @field_validator("commit_message", "marker", "skip_reason")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("text fields may not be empty")
        return normalized


def resolve_staging_selection(
    paths: "RuntimePaths",
    *,
    staging_repo_dir: Path | str | None = None,
) -> StagingSelectionReport:
    """Resolve the staging repo path plus the active manifest selection."""

    loaded = load_staging_manifest(paths)
    staging_dir = (
        _normalize_optional_path(staging_repo_dir).expanduser().resolve()
        if staging_repo_dir is not None
        else paths.staging_repo_dir
    )
    return StagingSelectionReport(
        workspace_root=paths.root,
        staging_repo_dir=staging_dir,
        manifest_source_kind=loaded.source_kind,
        manifest_source_ref=loaded.source_ref,
        manifest_version=loaded.manifest.version,
        required_paths=loaded.manifest.paths,
        optional_paths=loaded.manifest.optional_paths,
    )


def _sync_one_path(
    selection: StagingSelectionReport,
    *,
    relative_path: str,
    required: bool,
) -> StagingSyncEntry:
    source = (selection.workspace_root / relative_path).resolve()
    destination = selection.staging_repo_dir / relative_path
    _ensure_relative_to_root(selection.workspace_root, source, label=relative_path)

    if not source.exists():
        if required:
            raise StagingPublishError(f"manifest source missing: {relative_path}")
        if destination.exists():
            _remove_path(destination)
            return StagingSyncEntry(path=relative_path, required=False, action="removed_optional")
        return StagingSyncEntry(path=relative_path, required=False, action="skipped_optional")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        _remove_path(destination)
        copytree(source, destination)
        return StagingSyncEntry(
            path=relative_path,
            required=required,
            action="synced",
            source_kind="directory",
        )

    copy2(source, destination)
    return StagingSyncEntry(
        path=relative_path,
        required=required,
        action="synced",
        source_kind="file",
    )


def sync_staging_repo(
    paths: "RuntimePaths",
    *,
    staging_repo_dir: Path | str | None = None,
) -> StagingSyncReport:
    """Copy the manifest-selected workspace surface into the staging repo."""

    selection = resolve_staging_selection(paths, staging_repo_dir=staging_repo_dir)
    created_staging_dir = False
    if selection.staging_repo_dir.exists() and not selection.staging_repo_dir.is_dir():
        raise StagingPublishError(
            f"staging repo path is not a directory: {selection.staging_repo_dir.as_posix()}"
        )
    if not selection.staging_repo_dir.exists():
        selection.staging_repo_dir.mkdir(parents=True)
        created_staging_dir = True

    entries: list[StagingSyncEntry] = []
    for relative_path in selection.required_paths:
        entries.append(_sync_one_path(selection, relative_path=relative_path, required=True))
    for relative_path in selection.optional_paths:
        entries.append(_sync_one_path(selection, relative_path=relative_path, required=False))

    return StagingSyncReport(
        selection=selection,
        created_staging_dir=created_staging_dir,
        entries=tuple(entries),
    )


def preflight_staging_publish(
    paths: "RuntimePaths",
    *,
    staging_repo_dir: Path | str | None = None,
    commit_message: str,
    push: bool,
) -> PublishPreflightReport:
    """Inspect staging-repo readiness without mutating git state."""

    selection = resolve_staging_selection(paths, staging_repo_dir=staging_repo_dir)
    repo_dir = selection.staging_repo_dir
    git_worktree_present = (repo_dir / ".git").exists()
    git_worktree_valid = False
    branch: str | None = None
    origin_configured = False
    has_changes = False
    changed_paths: tuple[str, ...] = ()
    status: Literal["ready", "no_changes", "skip_publish"] = "skip_publish"
    skip_reason: str | None = None

    if not git_worktree_present:
        skip_reason = "missing_git_worktree"
    else:
        valid = _git(repo_dir, "rev-parse", "--is-inside-work-tree")
        if valid.returncode != 0:
            skip_reason = "invalid_git_worktree"
        else:
            git_worktree_valid = True
            branch_result = _git(repo_dir, "symbolic-ref", "--quiet", "--short", "HEAD")
            branch = branch_result.stdout.strip() or None
            origin_configured = _git(repo_dir, "remote", "get-url", "origin").returncode == 0
            status_result = _git(repo_dir, "status", "--short", "--untracked-files=all")
            if status_result.returncode != 0:
                raise StagingPublishError(
                    f"git status failed in staging repo: {repo_dir.as_posix()}"
                )
            changed_paths = _changed_paths_from_porcelain(status_result.stdout)
            has_changes = bool(changed_paths)
            if not has_changes:
                status = "no_changes"
                skip_reason = "no_changes"
            elif push and not origin_configured:
                skip_reason = "missing_origin"
            elif push and branch is None:
                skip_reason = "detached_head"
            elif push:
                status = "ready"
            else:
                status = "ready"
                skip_reason = "push_disabled"

    commit_allowed = git_worktree_valid and has_changes
    publish_allowed = commit_allowed and push and origin_configured and branch is not None
    if status == "ready" and push and not publish_allowed:
        status = "skip_publish"
    elif status == "ready":
        status = "ready"
    elif status == "no_changes":
        status = "no_changes"
    else:
        status = "skip_publish"

    return PublishPreflightReport(
        selection=selection,
        commit_message=commit_message,
        push_requested=push,
        git_worktree_present=git_worktree_present,
        git_worktree_valid=git_worktree_valid,
        branch=branch,
        origin_configured=origin_configured,
        has_changes=has_changes,
        changed_paths=changed_paths,
        commit_allowed=commit_allowed,
        publish_allowed=publish_allowed,
        status=status,
        skip_reason=skip_reason,
    )


def commit_staging_repo(
    paths: "RuntimePaths",
    *,
    staging_repo_dir: Path | str | None = None,
    commit_message: str,
    push: bool,
) -> PublishCommitReport:
    """Commit staged sync changes and optionally push them."""

    preflight = preflight_staging_publish(
        paths,
        staging_repo_dir=staging_repo_dir,
        commit_message=commit_message,
        push=push,
    )
    selection = preflight.selection
    repo_dir = selection.staging_repo_dir

    if not preflight.git_worktree_present:
        marker = f"SKIP_PUBLISH reason=missing_git_worktree path={repo_dir.as_posix()}"
        return PublishCommitReport(
            selection=selection,
            commit_message=commit_message,
            push_requested=push,
            push_performed=False,
            status="skipped",
            marker=marker,
            skip_reason="missing_git_worktree",
        )
    if not preflight.git_worktree_valid:
        marker = f"SKIP_PUBLISH reason=invalid_git_worktree path={repo_dir.as_posix()}"
        return PublishCommitReport(
            selection=selection,
            commit_message=commit_message,
            push_requested=push,
            push_performed=False,
            status="skipped",
            marker=marker,
            skip_reason="invalid_git_worktree",
        )

    add_result = _git(repo_dir, "add", "-A")
    if add_result.returncode != 0:
        raise StagingPublishError(f"git add failed in staging repo: {repo_dir.as_posix()}")
    cached_result = _git(repo_dir, "diff", "--cached", "--quiet")
    if cached_result.returncode == 0:
        return PublishCommitReport(
            selection=selection,
            commit_message=commit_message,
            push_requested=push,
            push_performed=False,
            branch=preflight.branch,
            status="no_changes",
            marker="NO_CHANGES",
        )
    if cached_result.returncode not in {0, 1}:
        raise StagingPublishError(f"git diff --cached failed in staging repo: {repo_dir.as_posix()}")

    commit_result = _git(repo_dir, "commit", "-m", commit_message)
    if commit_result.returncode != 0:
        recached_result = _git(repo_dir, "diff", "--cached", "--quiet")
        if recached_result.returncode == 0:
            return PublishCommitReport(
                selection=selection,
                commit_message=commit_message,
                push_requested=push,
                push_performed=False,
                branch=preflight.branch,
                status="no_changes",
                marker="NO_CHANGES",
            )
        raise StagingPublishError(f"git commit failed in staging repo: {repo_dir.as_posix()}")

    head_result = _git(repo_dir, "rev-parse", "HEAD")
    if head_result.returncode != 0:
        raise StagingPublishError(f"git rev-parse HEAD failed in staging repo: {repo_dir.as_posix()}")
    commit_sha = head_result.stdout.strip() or None

    if not push:
        return PublishCommitReport(
            selection=selection,
            commit_message=commit_message,
            push_requested=False,
            push_performed=False,
            branch=preflight.branch,
            status="committed",
            marker="SKIP_PUBLISH reason=push_disabled",
            commit_sha=commit_sha,
            skip_reason="push_disabled",
        )
    if not preflight.origin_configured:
        return PublishCommitReport(
            selection=selection,
            commit_message=commit_message,
            push_requested=True,
            push_performed=False,
            branch=preflight.branch,
            status="committed",
            marker=f"SKIP_PUBLISH reason=missing_origin path={repo_dir.as_posix()}",
            commit_sha=commit_sha,
            skip_reason="missing_origin",
        )
    if preflight.branch is None:
        return PublishCommitReport(
            selection=selection,
            commit_message=commit_message,
            push_requested=True,
            push_performed=False,
            status="committed",
            marker=f"SKIP_PUBLISH reason=detached_head path={repo_dir.as_posix()}",
            commit_sha=commit_sha,
            skip_reason="detached_head",
        )

    push_result = _git(repo_dir, "push", "origin", f"HEAD:{preflight.branch}")
    if push_result.returncode != 0:
        raise StagingPublishError(
            f"git push failed for origin/{preflight.branch} in staging repo: {repo_dir.as_posix()}"
        )
    return PublishCommitReport(
        selection=selection,
        commit_message=commit_message,
        push_requested=True,
        push_performed=True,
        branch=preflight.branch,
        status="pushed",
        marker=f"PUSH_OK remote=origin branch={preflight.branch}",
        commit_sha=commit_sha,
    )
