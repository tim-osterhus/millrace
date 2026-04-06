"""Control-plane helpers for staging publish flows."""

from __future__ import annotations

from pathlib import Path

from .control_common import ControlError
from .paths import RuntimePaths
from .publishing import (
    PublishCommitReport,
    PublishPreflightReport,
    StagingPublishError,
    StagingSyncReport,
    commit_staging_repo,
    preflight_staging_publish,
    sync_staging_repo,
)


def default_publish_commit_message() -> str:
    """Return the default local staging commit message."""

    return "Millrace staging sync"


def publish_sync(paths: RuntimePaths, *, staging_repo_dir: Path | str | None = None) -> StagingSyncReport:
    """Sync the manifest-selected workspace surface into the staging repo."""

    try:
        return sync_staging_repo(paths, staging_repo_dir=staging_repo_dir)
    except StagingPublishError as exc:
        raise ControlError(str(exc)) from exc


def publish_preflight(
    paths: RuntimePaths,
    *,
    staging_repo_dir: Path | str | None = None,
    commit_message: str | None = None,
    push: bool = False,
) -> PublishPreflightReport:
    """Return publish readiness for the staging repo without mutating git state."""

    try:
        return preflight_staging_publish(
            paths,
            staging_repo_dir=staging_repo_dir,
            commit_message=(commit_message or default_publish_commit_message()),
            push=push,
        )
    except StagingPublishError as exc:
        raise ControlError(str(exc)) from exc


def publish_commit(
    paths: RuntimePaths,
    *,
    staging_repo_dir: Path | str | None = None,
    commit_message: str | None = None,
    push: bool = False,
) -> PublishCommitReport:
    """Commit staging-repo changes and optionally push them."""

    try:
        return commit_staging_repo(
            paths,
            staging_repo_dir=staging_repo_dir,
            commit_message=(commit_message or default_publish_commit_message()),
            push=push,
        )
    except StagingPublishError as exc:
        raise ControlError(str(exc)) from exc
