from __future__ import annotations

import importlib
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from millrace_ai.contracts import (
    ExecutionStageName,
    MailboxCommand,
    Plane,
    PlanningStageName,
    RecoveryCounterEntry,
    RecoveryCounters,
    RuntimeMode,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.control import RuntimeControl
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.mailbox import read_pending_mailbox_commands
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime_lock import acquire_runtime_ownership_lock
from millrace_ai.state_store import (
    load_execution_status,
    load_planning_status,
    load_recovery_counters,
    load_snapshot,
    save_recovery_counters,
    save_snapshot,
    set_execution_status,
)

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="control test task",
        target_paths=["millrace/control.py"],
        acceptance=["control command mutates or defers correctly"],
        required_checks=["uv run pytest tests/runtime/test_control.py -q"],
        references=["lab/specs/drafts/millrace-runtime-module-and-cli-plan.md"],
        risk=["runtime control drift"],
        created_at=NOW,
        created_by="tests",
    )


def _activate_task(paths, task_id: str) -> None:
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc(task_id))
    claim = queue.claim_next_execution_task()
    assert claim is not None
    assert claim.work_item_id == task_id


def _spec_doc(spec_id: str) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Spec {spec_id}",
        summary="control test spec",
        source_type="manual",
        goals=["verify control add-spec"],
        constraints=["deterministic"],
        acceptance=["mailbox-safe add-spec works"],
        references=["lab/specs/pending/2026-04-15-millrace-recheck-remediation-task-breakdown.md"],
        created_at=NOW,
        created_by="tests",
    )


def _save_active_task_snapshot(
    paths,
    *,
    task_id: str,
    daemon_running: bool,
) -> None:
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": daemon_running,
                "paused": False,
                "stop_requested": False,
                "active_plane": Plane.EXECUTION,
                "active_stage": ExecutionStageName.BUILDER,
                "active_run_id": "run-active",
                "active_work_item_kind": WorkItemKind.TASK,
                "active_work_item_id": task_id,
                "active_since": NOW,
                "updated_at": NOW,
            }
        ),
    )


def _activate_spec(paths, spec_id: str) -> None:
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc(spec_id))
    claim = queue.claim_next_planning_item()
    assert claim is not None
    assert claim.work_item_id == spec_id


def _save_active_spec_snapshot(
    paths,
    *,
    spec_id: str,
    daemon_running: bool,
) -> None:
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": daemon_running,
                "paused": False,
                "stop_requested": False,
                "active_plane": Plane.PLANNING,
                "active_stage": PlanningStageName.PLANNER,
                "active_run_id": "run-active-planning",
                "active_work_item_kind": WorkItemKind.SPEC,
                "active_work_item_id": spec_id,
                "active_since": NOW,
                "updated_at": NOW,
            }
        ),
    )


def _pending_command_set(paths) -> set[MailboxCommand]:
    return {envelope.command for envelope in read_pending_mailbox_commands(paths)}


def test_control_import_surface_is_a_runtime_facade() -> None:
    control_module = importlib.import_module("millrace_ai.control")
    mailbox_module = importlib.import_module("millrace_ai.runtime.control_mailbox")
    mutations_module = importlib.import_module("millrace_ai.runtime.control_mutations")

    assert Path(control_module.__file__).as_posix().endswith("/control.py")
    assert control_module.RuntimeControl.__module__ == "millrace_ai.runtime.control"
    assert control_module.ControlActionResult.__module__ == "millrace_ai.runtime.control"
    assert hasattr(mailbox_module, "MailboxControlRouter")
    assert hasattr(mutations_module, "DirectControlMutations")


def test_pause_resume_stop_are_direct_when_daemon_is_not_running(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    control = RuntimeControl(paths)

    pause_result = control.pause_runtime()
    paused_snapshot = load_snapshot(paths)
    assert pause_result.mode == "direct"
    assert pause_result.applied is True
    assert paused_snapshot.paused is True

    resume_result = control.resume_runtime()
    resumed_snapshot = load_snapshot(paths)
    assert resume_result.mode == "direct"
    assert resume_result.applied is True
    assert resumed_snapshot.paused is False

    stop_result = control.stop_runtime()
    stopped_snapshot = load_snapshot(paths)
    assert stop_result.mode == "direct"
    assert stop_result.applied is True
    assert stopped_snapshot.stop_requested is True
    assert stopped_snapshot.process_running is False


def test_pause_resume_stop_use_mailbox_when_daemon_is_running(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": True,
                "paused": False,
                "stop_requested": False,
                "updated_at": NOW,
            }
        ),
    )
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="pause-resume-stop-mailbox",
    )
    control = RuntimeControl(paths)

    pause_result = control.pause_runtime()
    resume_result = control.resume_runtime()
    stop_result = control.stop_runtime()

    assert pause_result.mode == "mailbox"
    assert resume_result.mode == "mailbox"
    assert stop_result.mode == "mailbox"
    assert pause_result.command_id is not None
    assert resume_result.command_id is not None
    assert stop_result.command_id is not None

    assert _pending_command_set(paths) == {
        MailboxCommand.PAUSE,
        MailboxCommand.RESUME,
        MailboxCommand.STOP,
    }
    after = load_snapshot(paths)
    assert after.paused is False
    assert after.stop_requested is False


def test_retry_active_requeues_directly_when_daemon_is_not_running(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    _activate_task(paths, "task-001")
    _save_active_task_snapshot(paths, task_id="task-001", daemon_running=False)
    set_execution_status(paths, "### BLOCKED")
    save_recovery_counters(
        paths,
        RecoveryCounters(
            entries=(
                RecoveryCounterEntry(
                    failure_class="test_failure",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    troubleshoot_attempt_count=1,
                    last_updated_at=NOW,
                ),
            )
        ),
    )
    control = RuntimeControl(paths)

    result = control.retry_active(reason="operator requested retry")

    assert result.mode == "direct"
    assert result.applied is True
    assert (paths.tasks_active_dir / "task-001.md").exists() is False
    assert (paths.tasks_queue_dir / "task-001.md").is_file()

    snapshot = load_snapshot(paths)
    assert snapshot.active_plane is None
    assert snapshot.active_stage is None
    assert snapshot.active_run_id is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert snapshot.current_failure_class is None

    assert load_execution_status(paths) == "### IDLE"
    assert load_planning_status(paths) == "### IDLE"
    assert load_recovery_counters(paths).entries == ()


def test_retry_active_uses_mailbox_when_daemon_is_running(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    _activate_task(paths, "task-001")
    _save_active_task_snapshot(paths, task_id="task-001", daemon_running=True)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="retry-active-mailbox",
    )
    control = RuntimeControl(paths)

    result = control.retry_active(reason="operator requested retry")

    assert result.mode == "mailbox"
    assert result.applied is False
    assert result.command_id is not None
    assert (paths.tasks_active_dir / "task-001.md").is_file()
    assert MailboxCommand.RETRY_ACTIVE in _pending_command_set(paths)

    snapshot = load_snapshot(paths)
    assert snapshot.active_work_item_id == "task-001"


def test_retry_active_planning_requeues_active_spec_when_daemon_is_not_running(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_spec(paths, "spec-001")
    _save_active_spec_snapshot(paths, spec_id="spec-001", daemon_running=False)
    control = RuntimeControl(paths)

    result = control.retry_active_planning(reason="operator requested planning retry")

    assert result.mode == "direct"
    assert result.applied is True
    assert (paths.specs_active_dir / "spec-001.md").exists() is False
    assert (paths.specs_queue_dir / "spec-001.md").is_file()


def test_retry_active_planning_rejects_execution_active_work(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    _activate_task(paths, "task-001")
    _save_active_task_snapshot(paths, task_id="task-001", daemon_running=False)
    control = RuntimeControl(paths)

    result = control.retry_active_planning(reason="operator requested planning retry")

    assert result.mode == "direct"
    assert result.applied is False
    assert "planning" in result.detail
    assert (paths.tasks_active_dir / "task-001.md").is_file()


def test_retry_active_planning_uses_mailbox_when_daemon_is_running(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_spec(paths, "spec-001")
    _save_active_spec_snapshot(paths, spec_id="spec-001", daemon_running=True)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="planning-retry-mailbox",
    )
    control = RuntimeControl(paths)

    result = control.retry_active_planning(reason="operator requested planning retry")

    assert result.mode == "mailbox"
    assert result.applied is False
    pending = read_pending_mailbox_commands(paths)
    assert pending[0].command is MailboxCommand.RETRY_ACTIVE
    assert pending[0].payload["scope"] == "planning"


def test_clear_stale_state_directly_resets_runtime_state(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    _activate_task(paths, "task-001")
    _save_active_task_snapshot(paths, task_id="task-001", daemon_running=False)
    set_execution_status(paths, "### BLOCKED")
    save_recovery_counters(
        paths,
        RecoveryCounters(
            entries=(
                RecoveryCounterEntry(
                    failure_class="stale_active_ownership",
                    work_item_kind=WorkItemKind.TASK,
                    work_item_id="task-001",
                    troubleshoot_attempt_count=1,
                    last_updated_at=NOW,
                ),
            )
        ),
    )
    control = RuntimeControl(paths)

    result = control.clear_stale_state(reason="operator clear stale")

    assert result.mode == "direct"
    assert result.applied is True
    assert (paths.tasks_active_dir / "task-001.md").exists() is False
    assert (paths.tasks_queue_dir / "task-001.md").is_file()

    snapshot = load_snapshot(paths)
    assert snapshot.active_plane is None
    assert snapshot.active_stage is None
    assert snapshot.active_run_id is None
    assert snapshot.active_work_item_kind is None
    assert snapshot.active_work_item_id is None
    assert snapshot.current_failure_class is None
    assert snapshot.stop_requested is False

    assert load_execution_status(paths) == "### IDLE"
    assert load_planning_status(paths) == "### IDLE"
    assert load_recovery_counters(paths).entries == ()


def test_clear_stale_state_uses_mailbox_when_daemon_is_running(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    _activate_task(paths, "task-001")
    _save_active_task_snapshot(paths, task_id="task-001", daemon_running=True)
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="clear-stale-mailbox",
    )
    control = RuntimeControl(paths)

    result = control.clear_stale_state(reason="operator clear stale")

    assert result.mode == "mailbox"
    assert result.applied is False
    assert result.command_id is not None
    assert (paths.tasks_active_dir / "task-001.md").is_file()
    assert MailboxCommand.CLEAR_STALE_STATE in _pending_command_set(paths)

    snapshot = load_snapshot(paths)
    assert snapshot.active_work_item_id == "task-001"


def test_clear_stale_state_marks_applied_when_only_pause_or_stop_bits_are_reset(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "paused": True,
                "stop_requested": True,
                "updated_at": NOW,
            }
        ),
    )
    control = RuntimeControl(paths)

    result = control.clear_stale_state(reason="reset control bits")

    assert result.mode == "direct"
    assert result.applied is True
    after = load_snapshot(paths)
    assert after.paused is False
    assert after.stop_requested is False


def test_add_task_spec_and_idea_are_direct_without_daemon_owner(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    control = RuntimeControl(paths)

    add_task = control.add_task(_task_doc("task-add-direct"))
    add_spec = control.add_spec(_spec_doc("spec-add-direct"))
    add_idea = control.add_idea_markdown(source_name="idea-direct.md", markdown="# Idea Direct\n")

    assert add_task.mode == "direct"
    assert add_task.artifact_path == paths.tasks_queue_dir / "task-add-direct.md"
    assert add_spec.mode == "direct"
    assert add_spec.artifact_path == paths.specs_queue_dir / "spec-add-direct.md"
    assert add_idea.mode == "direct"
    assert add_idea.artifact_path == paths.root / "ideas" / "inbox" / "idea-direct.md"

    assert (paths.tasks_queue_dir / "task-add-direct.md").is_file()
    assert (paths.specs_queue_dir / "spec-add-direct.md").is_file()
    assert (paths.root / "ideas" / "inbox" / "idea-direct.md").is_file()


def test_add_task_spec_idea_and_reload_use_mailbox_when_daemon_owns_workspace(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    snapshot = load_snapshot(paths)
    save_snapshot(
        paths,
        snapshot.model_copy(
            update={
                "runtime_mode": RuntimeMode.DAEMON,
                "process_running": True,
                "updated_at": NOW,
            }
        ),
    )
    acquire_runtime_ownership_lock(
        paths,
        owner_pid=os.getpid(),
        owner_session_id="add-and-reload-mailbox",
    )
    control = RuntimeControl(paths)

    add_task = control.add_task(_task_doc("task-add-mailbox"))
    add_spec = control.add_spec(_spec_doc("spec-add-mailbox"))
    add_idea = control.add_idea_markdown(source_name="idea-mailbox.md", markdown="# Idea Mailbox\n")
    reload_config = control.reload_config()

    assert add_task.mode == "mailbox"
    assert add_spec.mode == "mailbox"
    assert add_idea.mode == "mailbox"
    assert reload_config.mode == "mailbox"

    assert MailboxCommand.ADD_TASK in _pending_command_set(paths)
    assert MailboxCommand.ADD_SPEC in _pending_command_set(paths)
    assert MailboxCommand.ADD_IDEA in _pending_command_set(paths)
    assert MailboxCommand.RELOAD_CONFIG in _pending_command_set(paths)

    assert not (paths.tasks_queue_dir / "task-add-mailbox.md").exists()
    assert not (paths.specs_queue_dir / "spec-add-mailbox.md").exists()
    assert not (paths.root / "ideas" / "inbox" / "idea-mailbox.md").exists()


def test_add_task_rejects_unsafe_task_id_shape(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    invalid_payload = _task_doc("task-safe").model_dump(mode="python")
    invalid_payload["task_id"] = "../escape"

    with pytest.raises(ValidationError, match="task_id"):
        RuntimeControl(paths).add_task(TaskDocument.model_validate(invalid_payload))


def test_add_idea_rejects_unsafe_source_name(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    control = RuntimeControl(paths)

    with pytest.raises(ValidationError, match="source_name"):
        control.add_idea_markdown(source_name="../escape.md", markdown="# Escape\n")


def test_add_idea_direct_rejects_duplicate_workspace_artifact(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    control = RuntimeControl(paths)

    control.add_idea_markdown(source_name="idea-duplicate.md", markdown="# Idea\n")

    with pytest.raises(WorkspaceStateError, match="idea document already exists"):
        control.add_idea_markdown(source_name="idea-duplicate.md", markdown="# Idea duplicate\n")
