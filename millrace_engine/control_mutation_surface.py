"""Mutation and operator-facing control surface helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .adapters.control_mailbox import ControlCommand
from .control_actions import (
    add_idea as add_idea_operation,
)
from .control_actions import (
    add_task as add_task_operation,
    active_task_remediate as active_task_remediate_operation,
    active_task_rejected as active_task_rejected_operation,
    recovery_request as recovery_request_operation,
    write_last_active_task_clear as write_last_active_task_clear_operation,
)
from .control_actions import (
    lifecycle_action,
    supervisor_lifecycle_action,
)
from .control_actions import (
    queue_cleanup_quarantine as queue_cleanup_quarantine_operation,
)
from .control_actions import (
    queue_cleanup_remove as queue_cleanup_remove_operation,
)
from .control_actions import (
    queue_reorder as queue_reorder_operation,
)
from .control_actions import (
    supervisor_add_task as supervisor_add_task_operation,
)
from .control_actions import (
    supervisor_queue_cleanup_quarantine as supervisor_queue_cleanup_quarantine_operation,
)
from .control_actions import (
    supervisor_queue_cleanup_remove as supervisor_queue_cleanup_remove_operation,
)
from .control_actions import (
    supervisor_queue_reorder as supervisor_queue_reorder_operation,
)
from .control_interview import (
    interview_accept as interview_accept_operation,
)
from .control_interview import (
    interview_answer as interview_answer_operation,
)
from .control_interview import (
    interview_create as interview_create_operation,
)
from .control_interview import (
    interview_list as interview_list_operation,
)
from .control_interview import (
    interview_show as interview_show_operation,
)
from .control_interview import (
    interview_skip as interview_skip_operation,
)
from .control_models import (
    ActiveTaskRemediationResult,
    InterviewListReport,
    InterviewMutationReport,
    InterviewQuestionReport,
    OperationResult,
    RecoveryRequestResult,
)
from .control_publish import (
    publish_commit as publish_commit_operation,
)
from .control_publish import (
    publish_preflight as publish_preflight_operation,
)
from .control_publish import (
    publish_sync as publish_sync_operation,
)
from .publishing import PublishCommitReport, PublishPreflightReport, StagingSyncReport


def queue_reorder(control, task_ids: list[str] | tuple[str, ...]) -> OperationResult:
    return queue_reorder_operation(
        control.paths,
        task_ids=task_ids,
        daemon_running=control.is_daemon_running(),
    )


def active_task_remediate(control, intent: str, *, reason: str) -> ActiveTaskRemediationResult:
    result = active_task_remediate_operation(
        control.paths,
        intent=intent,
        reason=reason,
        daemon_running=control.is_daemon_running(),
    )
    if result.request.intent == "clear" and result.outcome_state == "rejected":
        write_last_active_task_clear_operation(control.paths, result)
    return result


def supervisor_active_task_remediate(
    control,
    intent: str,
    *,
    reason: str,
    issuer: str,
) -> ActiveTaskRemediationResult:
    normalized_issuer = control._normalize_supervisor_issuer(issuer)
    result = active_task_remediate_operation(
        control.paths,
        intent=intent,
        reason=reason,
        issuer=normalized_issuer,
        daemon_running=control.is_daemon_running(),
    )
    if result.request.intent == "clear" and result.outcome_state == "rejected":
        write_last_active_task_clear_operation(control.paths, result)
    return result


def active_task_rejected(intent: str, *, reason: str, issuer: str | None = None) -> ActiveTaskRemediationResult:
    return active_task_rejected_operation(intent=intent, reason=reason, issuer=issuer)


def queue_cleanup_remove(control, task_id: str, *, reason: str) -> OperationResult:
    return queue_cleanup_remove_operation(
        control.paths,
        task_id=task_id,
        reason=reason,
        daemon_running=control.is_daemon_running(),
    )


def queue_cleanup_quarantine(control, task_id: str, *, reason: str) -> OperationResult:
    return queue_cleanup_quarantine_operation(
        control.paths,
        task_id=task_id,
        reason=reason,
        daemon_running=control.is_daemon_running(),
    )


def supervisor_queue_reorder(control, task_ids: list[str] | tuple[str, ...], *, issuer: str) -> OperationResult:
    return supervisor_queue_reorder_operation(
        control.paths,
        task_ids=task_ids,
        issuer=issuer,
        daemon_running=control.is_daemon_running(),
    )


def supervisor_queue_cleanup_remove(control, task_id: str, *, reason: str, issuer: str) -> OperationResult:
    return supervisor_queue_cleanup_remove_operation(
        control.paths,
        task_id=task_id,
        reason=reason,
        issuer=issuer,
        daemon_running=control.is_daemon_running(),
    )


def supervisor_queue_cleanup_quarantine(control, task_id: str, *, reason: str, issuer: str) -> OperationResult:
    return supervisor_queue_cleanup_quarantine_operation(
        control.paths,
        task_id=task_id,
        reason=reason,
        issuer=issuer,
        daemon_running=control.is_daemon_running(),
    )


def recovery_request(
    control,
    target: str,
    *,
    reason: str,
    issuer: str,
    force_queue: bool,
) -> RecoveryRequestResult:
    return recovery_request_operation(
        control.paths,
        target=target,
        reason=reason,
        issuer=issuer,
        force_queue=force_queue,
        daemon_running=control.is_daemon_running(),
    )


def interview_list(control) -> InterviewListReport:
    return interview_list_operation(control.config_path, control.paths)


def interview_show(control, question_id: str) -> InterviewQuestionReport:
    return interview_show_operation(control.config_path, control.paths, question_id)


def interview_create(
    control,
    *,
    source_path: str | Path,
    question: str,
    why_this_matters: str,
    recommended_answer: str,
    answer_source: Literal["repo", "operator", "assumption"] = "assumption",
    blocking: bool = True,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationReport:
    return interview_create_operation(
        control.config_path,
        control.paths,
        source_path=source_path,
        question=question,
        why_this_matters=why_this_matters,
        recommended_answer=recommended_answer,
        answer_source=answer_source,
        blocking=blocking,
        evidence=evidence,
    )


def interview_answer(
    control,
    question_id: str,
    *,
    text: str,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationReport:
    return interview_answer_operation(
        control.config_path,
        control.paths,
        question_id,
        text=text,
        evidence=evidence,
    )


def interview_accept(
    control,
    question_id: str,
    *,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationReport:
    return interview_accept_operation(
        control.config_path,
        control.paths,
        question_id,
        evidence=evidence,
    )


def interview_skip(
    control,
    question_id: str,
    *,
    reason: str | None = None,
    evidence: tuple[str, ...] | list[str] | None = None,
) -> InterviewMutationReport:
    return interview_skip_operation(
        control.config_path,
        control.paths,
        question_id,
        reason=reason,
        evidence=evidence,
    )


def add_task(control, title: str, *, body: str | None = None, spec_id: str | None = None) -> OperationResult:
    return add_task_operation(
        control.paths,
        title=title,
        body=body,
        spec_id=spec_id,
        daemon_running=control.is_daemon_running(),
    )


def supervisor_add_task(
    control,
    title: str,
    *,
    issuer: str,
    body: str | None = None,
    spec_id: str | None = None,
) -> OperationResult:
    return supervisor_add_task_operation(
        control.paths,
        title=title,
        issuer=issuer,
        body=body,
        spec_id=spec_id,
        daemon_running=control.is_daemon_running(),
    )


def add_idea(control, file: Path | str) -> OperationResult:
    return add_idea_operation(
        control.paths,
        file=file,
        daemon_running=control.is_daemon_running(),
    )


def publish_sync(control, *, staging_repo_dir: Path | str | None = None) -> StagingSyncReport:
    return publish_sync_operation(control.paths, staging_repo_dir=staging_repo_dir)


def publish_preflight(
    control,
    *,
    staging_repo_dir: Path | str | None = None,
    commit_message: str | None = None,
    push: bool = False,
) -> PublishPreflightReport:
    return publish_preflight_operation(
        control.paths,
        staging_repo_dir=staging_repo_dir,
        commit_message=commit_message,
        push=push,
    )


def publish_commit(
    control,
    *,
    staging_repo_dir: Path | str | None = None,
    commit_message: str | None = None,
    push: bool = False,
) -> PublishCommitReport:
    return publish_commit_operation(
        control.paths,
        staging_repo_dir=staging_repo_dir,
        commit_message=commit_message,
        push=push,
    )


def stop(control) -> OperationResult:
    return lifecycle_action(
        control.paths,
        command=ControlCommand.STOP,
        daemon_running=control.is_daemon_running(),
    )


def supervisor_stop(control, *, issuer: str) -> OperationResult:
    return supervisor_lifecycle_action(
        control.paths,
        command=ControlCommand.STOP,
        issuer=issuer,
        daemon_running=control.is_daemon_running(),
    )


def pause(control) -> OperationResult:
    return lifecycle_action(
        control.paths,
        command=ControlCommand.PAUSE,
        daemon_running=control.is_daemon_running(),
    )


def supervisor_pause(control, *, issuer: str) -> OperationResult:
    return supervisor_lifecycle_action(
        control.paths,
        command=ControlCommand.PAUSE,
        issuer=issuer,
        daemon_running=control.is_daemon_running(),
    )


def resume(control) -> OperationResult:
    return lifecycle_action(
        control.paths,
        command=ControlCommand.RESUME,
        daemon_running=control.is_daemon_running(),
    )


def supervisor_resume(control, *, issuer: str) -> OperationResult:
    return supervisor_lifecycle_action(
        control.paths,
        command=ControlCommand.RESUME,
        issuer=issuer,
        daemon_running=control.is_daemon_running(),
    )
