"""Shared truthfulness helpers for the Publish TUI surface."""

from __future__ import annotations

from .models import PublishOverviewView

_SKIP_REASON_COPY = {
    "missing_git_worktree": "staging repo is missing a git worktree",
    "invalid_git_worktree": "staging repo is not a valid git worktree",
    "no_changes": "no changed staging paths are waiting for a commit",
    "missing_origin": "origin is not configured for the staging repo",
    "detached_head": "staging repo HEAD is detached",
    "push_disabled": "push was not requested by the current preflight",
}


def publish_skip_reason_copy(reason: str | None) -> str:
    """Return operator-facing copy for a publish skip reason."""

    if reason is None:
        return "none"
    return _SKIP_REASON_COPY.get(reason, reason)


def publish_push_ready(publish: PublishOverviewView) -> bool:
    """Return whether current facts support a push path."""

    return publish.commit_allowed and publish.origin_configured and publish.branch is not None


def publish_commit_block_reason(publish: PublishOverviewView) -> str | None:
    """Return the current commit-blocked reason, if any."""

    if not publish.git_worktree_present:
        return publish_skip_reason_copy("missing_git_worktree")
    if not publish.git_worktree_valid:
        return publish_skip_reason_copy("invalid_git_worktree")
    if not publish.has_changes:
        return publish_skip_reason_copy("no_changes")
    return None


def publish_push_block_reason(publish: PublishOverviewView) -> str | None:
    """Return the current push-blocked reason when commit is still possible."""

    if publish_commit_block_reason(publish) is not None:
        return None
    if not publish.origin_configured:
        return publish_skip_reason_copy("missing_origin")
    if publish.branch is None:
        return publish_skip_reason_copy("detached_head")
    return None


def publish_status_copy(publish: PublishOverviewView) -> str:
    """Return the high-level operator status headline."""

    commit_blocked = publish_commit_block_reason(publish)
    if commit_blocked is not None:
        if commit_blocked == publish_skip_reason_copy("no_changes"):
            return "idle | no staging changes to commit"
        return f"blocked | {commit_blocked}"

    push_blocked = publish_push_block_reason(publish)
    if push_blocked is not None:
        return f"ready | local commit available; push blocked by {push_blocked}"

    return "ready | local commit available; push looks ready from current facts"


def publish_safe_next_step_headline(publish: PublishOverviewView) -> str:
    """Return the highest-value next step from current publish facts."""

    commit_blocked = publish_commit_block_reason(publish)
    if commit_blocked == publish_skip_reason_copy("missing_git_worktree"):
        return "Repair or create the staging git repo outside TUI, then refresh preflight."
    if commit_blocked == publish_skip_reason_copy("invalid_git_worktree"):
        return "Repair the staging git worktree outside TUI, then refresh preflight."
    if commit_blocked == publish_skip_reason_copy("no_changes"):
        return "Nothing needs a staging commit right now."

    push_blocked = publish_push_block_reason(publish)
    if push_blocked is not None:
        return "N local commit is safe right now; fix push prerequisites before P."

    return "N local commit is the safe default; P is only for intentional remote publish."


def publish_safe_next_step_detail(publish: PublishOverviewView) -> str:
    """Return supporting detail for the next-step guidance."""

    commit_blocked = publish_commit_block_reason(publish)
    if commit_blocked == publish_skip_reason_copy("missing_git_worktree"):
        return "G only syncs manifest-selected files into staging; it does not create git history."
    if commit_blocked == publish_skip_reason_copy("invalid_git_worktree"):
        return "Use R after fixing the staging git worktree so the panel reloads truthful branch and origin facts."
    if commit_blocked == publish_skip_reason_copy("no_changes"):
        return "If you expected staged diffs, use G to sync manifest-selected files into staging, then R to refresh facts."

    push_blocked = publish_push_block_reason(publish)
    if push_blocked == publish_skip_reason_copy("missing_origin"):
        return "Configure origin for the staging repo outside TUI, then R refresh before using P."
    if push_blocked == publish_skip_reason_copy("detached_head"):
        return "Check out a branch in the staging repo outside TUI, then R refresh before using P."

    return "R refreshes read-only preflight facts first, and G re-syncs manifest-selected files when staging looks stale."
