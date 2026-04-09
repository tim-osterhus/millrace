from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from millrace_engine.baseline_assets import packaged_baseline_asset
from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.contracts import ResearchRecoveryDecision
from millrace_engine.markdown import parse_task_cards
from millrace_engine.queue import TaskQueue, load_research_recovery_latch
from millrace_engine.research.incidents import load_incident_recurrence_ledger


TASK_ALPHA = """## 2026-03-16 - Implement status store

- **Goal:** Replace placeholder status handling.
- **Context:** Run 03 needs a real overwrite-only contract.
- **Spec-ID:** SPEC-STATUS
- **Dependencies:** none
- **Deliverables:**
  - Add validated status helpers.
- **Acceptance:** Status transitions are enforced.
- **Notes:** Preserve exact markdown body.
"""

TASK_BETA = """## 2026-03-17 - Build queue operations

- **Goal:** Implement file-backed queue mutation.
- **Context:** Active and backlog stores need atomic updates.
- **Spec-ID:** SPEC-QUEUE
- **Dependencies:**
  - 2026-03-16 - Implement status store
- **Deliverables:**
  - Add promote and archive operations.
- **Acceptance:** Queue mutations are atomic and deterministic.
- **Notes:** Include a backlog freezer for research handoff.
"""

TASK_GAMMA = """## 2026-03-18 - Research-generated remediation task

- **Goal:** Simulate regenerated backlog work.
- **Context:** Thaw should only run once fresh backlog cards exist.
- **Spec-ID:** SPEC-THAW
- **Dependencies:** none
- **Deliverables:**
  - Provide a regenerated backlog card.
- **Acceptance:** Existing frozen cards thaw behind this card.
- **Notes:** This card stands in for Taskaudit output.
"""

TASK_DELTA = """## 2026-03-19 - Publish release notes

- **Goal:** Document the release scope.
- **Context:** This card is unrelated to runtime recovery work.
- **Spec-ID:** SPEC-DOCS
- **Dependencies:** none
- **Deliverables:**
  - Publish concise release notes.
- **Acceptance:** The docs lane can continue independently.
- **Notes:** This card should survive dependency-aware quarantine.
"""

TASK_EPSILON = """## 2026-03-20 - Capture stakeholder notes

- **Goal:** Preserve a manually queued note-taking task.
- **Context:** This card omits dependency metadata on purpose.
- **Deliverables:**
  - Capture the next meeting notes.
- **Acceptance:** Conservative quarantine should freeze this card when overlap is unknown.
- **Notes:** Metadata is intentionally incomplete.
"""

TASK_ZETA = """## 2026-03-21 - Investigate unknown failure

- **Goal:** Triage an execution failure with incomplete provenance.
- **Context:** The runtime has not yet attached spec metadata.
- **Deliverables:**
  - Gather the failure context.
- **Acceptance:** Unsafe dependency quarantine falls back to a full freeze.
- **Notes:** This active card intentionally has no dependency metadata.
"""


def make_queue(tmp_path: Path) -> tuple[TaskQueue, Path]:
    workspace = tmp_path / "millrace"
    workspace.mkdir()
    agents = workspace / "agents"
    agents.mkdir()

    for relative, contents in {
        "status.md": "### IDLE\n",
        "research_status.md": "### IDLE\n",
        "tasks.md": "# Active Task\n",
        "tasksbacklog.md": "# Task Backlog\n",
        "tasksarchive.md": "# Task Archive\n",
        "tasksbackburner.md": "# Task Backburner\n",
        "tasksblocker.md": "# Task Blockers\n",
        "taskspending.md": "# Tasks Pending\n",
        "historylog.md": "# History Log\n",
        "engine_events.log": "",
        "research_events.md": "# Research Events\n",
    }.items():
        path = agents / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    for relative in [
        ".runtime/commands/incoming",
        ".runtime/commands/processed",
        ".runtime/commands/failed",
        ".locks",
        ".deferred",
        "historylog",
        "runs",
        "diagnostics",
        "ideas/raw",
    ]:
        (agents / relative).mkdir(parents=True, exist_ok=True)

    config_path = workspace / "millrace.toml"
    config_path.write_text(packaged_baseline_asset("millrace.toml").read_text(encoding="utf-8"), encoding="utf-8")
    loaded = load_engine_config(config_path)
    queue = TaskQueue(build_runtime_paths(loaded.config))
    return queue, workspace


def test_parse_task_cards_preserves_full_markdown_body() -> None:
    cards = parse_task_cards(f"# Task Backlog\n\n{TASK_ALPHA}")

    assert len(cards) == 1
    assert cards[0].title == "Implement status store"
    assert "- **Goal:** Replace placeholder status handling." in cards[0].body
    assert cards[0].render_markdown() == TASK_ALPHA.rstrip("\n")


def test_promote_and_archive_cycle_moves_cards_between_visible_stores(tmp_path: Path) -> None:
    queue, workspace = make_queue(tmp_path)
    backlog_path = workspace / "agents/tasksbacklog.md"
    backlog_path.write_text(f"# Task Backlog\n\n{TASK_ALPHA}\n\n{TASK_BETA}", encoding="utf-8")

    promoted = queue.promote_next()

    assert promoted.title == "Implement status store"
    assert "Implement status store" in (workspace / "agents/tasks.md").read_text(encoding="utf-8")
    backlog_cards = parse_task_cards(backlog_path.read_text(encoding="utf-8"))
    assert [card.title for card in backlog_cards] == ["Build queue operations"]

    queue.archive(promoted)

    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8")) == []
    archive_cards = parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8"))
    assert [card.title for card in archive_cards] == ["Implement status store"]


def test_quarantine_writes_blocker_entry_and_freeze_state(tmp_path: Path) -> None:
    queue, workspace = make_queue(tmp_path)
    (workspace / "agents/tasks.md").write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")
    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{TASK_BETA}", encoding="utf-8")

    active_card = queue.active_task()
    assert active_card is not None

    latch = queue.quarantine(
        active_card,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-QUEUE-001.md"),
    )

    assert latch.frozen_backlog_cards == 1
    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8")) == []
    assert parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8")) == []

    backburner_text = (workspace / "agents/tasksbackburner.md").read_text(encoding="utf-8")
    assert "research_recovery:freeze:start" in backburner_text
    assert "Implement status store" in backburner_text
    assert "Build queue operations" in backburner_text

    blocker_text = (workspace / "agents/tasksblocker.md").read_text(encoding="utf-8")
    assert "### NEEDS_RESEARCH" in blocker_text
    assert "Consult exhausted the local path" in blocker_text

    persisted_latch = load_research_recovery_latch(workspace / "agents/.runtime/research_recovery_latch.json")
    assert persisted_latch is not None
    assert persisted_latch.batch_id == latch.batch_id
    assert persisted_latch.fingerprint is not None
    assert persisted_latch.incident_path == latch.incident_path
    assert latch.incident_path is not None
    assert latch.incident_path.name in blocker_text

    ledger = load_incident_recurrence_ledger(workspace / "agents/.research_runtime/incidents/recurrence_ledger.json")
    assert len(ledger.records) == 1
    assert ledger.records[0].occurrence_count == 1
    assert ledger.records[0].active_incident_path == latch.incident_path.as_posix()
    assert ledger.records[0].fingerprint == persisted_latch.fingerprint


def test_quarantine_reuses_equivalent_incident_path_and_updates_recurrence_ledger(tmp_path: Path) -> None:
    queue, workspace = make_queue(tmp_path)
    tasks_path = workspace / "agents/tasks.md"
    tasks_path.write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")

    first_card = queue.active_task()
    assert first_card is not None
    first_latch = queue.quarantine(
        first_card,
        "Consult exhausted the local path",
        None,
        failure_signature="compile-failed",
    )
    assert first_latch.fingerprint is not None
    assert first_latch.incident_path is not None

    incident_path = workspace / first_latch.incident_path
    incident_path.parent.mkdir(parents=True, exist_ok=True)
    incident_path.write_text(
        "\n".join(
            [
                "---",
                f"incident_id: {incident_path.stem}",
                f"fingerprint: {first_latch.fingerprint}",
                "failure_signature: compile-failed",
                "status: incoming",
                "opened_at: 2026-03-21T12:00:00Z",
                "updated_at: 2026-03-21T12:05:00Z",
                "---",
                "",
                "# Equivalent incident",
                "",
                "## Summary",
                "- Preserve one active incident path for equivalent recurrence.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    tasks_path.write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")
    second_card = queue.active_task()
    assert second_card is not None
    second_latch = queue.quarantine(
        second_card,
        "Consult exhausted the local path",
        None,
        failure_signature="compile-failed",
    )

    assert second_latch.incident_path == first_latch.incident_path
    assert second_latch.fingerprint == first_latch.fingerprint

    ledger = load_incident_recurrence_ledger(workspace / "agents/.research_runtime/incidents/recurrence_ledger.json")
    assert len(ledger.records) == 1
    assert ledger.records[0].occurrence_count == 2
    assert ledger.records[0].active_incident_path == first_latch.incident_path.as_posix()
    assert tuple(observation.source for observation in ledger.records[0].observations) == (
        "execution_quarantine",
        "execution_quarantine",
    )


def test_quarantine_canonicalizes_consult_supplied_duplicate_incident_path(tmp_path: Path) -> None:
    queue, workspace = make_queue(tmp_path)
    tasks_path = workspace / "agents/tasks.md"
    tasks_path.write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")

    first_card = queue.active_task()
    assert first_card is not None
    first_latch = queue.quarantine(
        first_card,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-CONSULT-ORIGINAL.md"),
        failure_signature="compile-failed",
    )
    assert first_latch.fingerprint is not None
    assert first_latch.incident_path is not None

    incident_path = workspace / first_latch.incident_path
    incident_path.parent.mkdir(parents=True, exist_ok=True)
    incident_path.write_text(
        "\n".join(
            [
                "---",
                f"incident_id: {incident_path.stem}",
                f"fingerprint: {first_latch.fingerprint}",
                "failure_signature: compile-failed",
                "status: incoming",
                "opened_at: 2026-03-21T12:00:00Z",
                "updated_at: 2026-03-21T12:05:00Z",
                "---",
                "",
                "# Equivalent incident",
                "",
                "## Summary",
                "- Preserve the existing incident path when consult re-emits an equivalent incident.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    tasks_path.write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")
    second_card = queue.active_task()
    assert second_card is not None
    second_latch = queue.quarantine(
        second_card,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-CONSULT-DUPLICATE.md"),
        failure_signature="compile-failed",
    )

    assert second_latch.incident_path == first_latch.incident_path

    blocker_text = (workspace / "agents/tasksblocker.md").read_text(encoding="utf-8")
    assert "INC-CONSULT-DUPLICATE.md" not in blocker_text
    assert first_latch.incident_path.name in blocker_text

    ledger = load_incident_recurrence_ledger(workspace / "agents/.research_runtime/incidents/recurrence_ledger.json")
    assert len(ledger.records) == 1
    assert ledger.records[0].occurrence_count == 2
    assert ledger.records[0].active_incident_path == first_latch.incident_path.as_posix()


def test_thaw_restores_frozen_cards_after_regenerated_backlog_work_appears(tmp_path: Path) -> None:
    queue, workspace = make_queue(tmp_path)
    (workspace / "agents/tasks.md").write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")
    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{TASK_BETA}", encoding="utf-8")

    active_card = queue.active_task()
    assert active_card is not None
    latch = queue.quarantine(
        active_card,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-QUEUE-002.md"),
    )
    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    latch_with_decision = latch.model_copy(
        update={
            "remediation_decision": ResearchRecoveryDecision.model_validate(
                {
                    "decision_type": "regenerated_backlog_work",
                    "decided_at": "2026-03-21T12:00:00Z",
                    "remediation_spec_id": "SPEC-THAW",
                    "remediation_record_path": "agents/.research_runtime/incidents/remediation/queue-thaw.json",
                    "taskaudit_record_path": "agents/.research_runtime/goalspec/taskaudit/queue-thaw.json",
                    "task_provenance_path": "agents/task_provenance.json",
                    "lineage_path": "agents/.research_runtime/incidents/lineage/inc-queue-002.json",
                    "pending_card_count": 1,
                    "backlog_card_count": 1,
                }
            )
        }
    )
    latch_path.write_text(
        latch_with_decision.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )

    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{TASK_GAMMA}", encoding="utf-8")

    thawed = queue.thaw(latch_with_decision)

    assert thawed == 2
    backlog_cards = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert [card.title for card in backlog_cards] == [
        "Research-generated remediation task",
        "Implement status store",
        "Build queue operations",
    ]
    assert "research_recovery:freeze:start" not in (
        workspace / "agents/tasksbackburner.md"
    ).read_text(encoding="utf-8")
    assert not (workspace / "agents/.runtime/research_recovery_latch.json").exists()


def test_quarantine_dependency_mode_retains_unrelated_backlog_and_conservatively_freezes_missing_metadata(
    tmp_path: Path,
) -> None:
    queue, workspace = make_queue(tmp_path)
    (workspace / "agents/tasks.md").write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")
    (workspace / "agents/tasksbacklog.md").write_text(
        f"# Task Backlog\n\n{TASK_BETA}\n\n{TASK_DELTA}\n\n{TASK_EPSILON}",
        encoding="utf-8",
    )

    active_card = queue.active_task()
    assert active_card is not None

    latch = queue.quarantine(
        active_card,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-QUEUE-DEPENDENCY-001.md"),
        quarantine_mode_requested="dependency",
    )

    backlog_cards = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert [card.title for card in backlog_cards] == ["Publish release notes"]

    backburner_text = (workspace / "agents/tasksbackburner.md").read_text(encoding="utf-8")
    assert "Implement status store" in backburner_text
    assert "Build queue operations" in backburner_text
    assert "Capture stakeholder notes" in backburner_text
    assert "Publish release notes" not in backburner_text

    assert latch.quarantine_mode_requested == "dependency"
    assert latch.quarantine_mode_applied == "dependency"
    assert latch.quarantine_reason == "dependency_overlap_match"
    assert latch.frozen_backlog_cards == 2
    assert latch.retained_backlog_cards == 1
    assert latch.missing_metadata_quarantined == 1

    blocker_text = (workspace / "agents/tasksblocker.md").read_text(encoding="utf-8")
    assert "while unrelated backlog work continues promotion" in blocker_text
    assert not (workspace / "agents/.runtime/research_recovery_latch.json").exists()


def test_quarantine_dependency_mode_falls_back_to_full_freeze_when_active_metadata_is_missing(
    tmp_path: Path,
) -> None:
    queue, workspace = make_queue(tmp_path)
    (workspace / "agents/tasks.md").write_text(f"# Active Task\n\n{TASK_ZETA}", encoding="utf-8")
    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{TASK_DELTA}", encoding="utf-8")

    active_card = queue.active_task()
    assert active_card is not None

    latch = queue.quarantine(
        active_card,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-QUEUE-DEPENDENCY-002.md"),
        quarantine_mode_requested="dependency",
    )

    assert parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8")) == []
    backburner_text = (workspace / "agents/tasksbackburner.md").read_text(encoding="utf-8")
    assert "Investigate unknown failure" in backburner_text
    assert "Publish release notes" in backburner_text

    assert latch.quarantine_mode_requested == "dependency"
    assert latch.quarantine_mode_applied == "full"
    assert latch.quarantine_reason == "fallback_full_active_metadata_missing"
    assert latch.frozen_backlog_cards == 1
    assert latch.retained_backlog_cards == 0
    assert latch.missing_metadata_quarantined == 0
    assert (workspace / "agents/.runtime/research_recovery_latch.json").exists()


def test_thaw_restores_frozen_cards_once_any_visible_backlog_work_reappears_even_without_decision(
    tmp_path: Path,
) -> None:
    queue, workspace = make_queue(tmp_path)
    (workspace / "agents/tasks.md").write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")
    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{TASK_BETA}", encoding="utf-8")

    active_card = queue.active_task()
    assert active_card is not None
    latch = queue.quarantine(
        active_card,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-QUEUE-003.md"),
    )

    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{TASK_GAMMA}", encoding="utf-8")

    thawed = queue.thaw(latch)

    assert thawed == 2
    backlog_cards = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert [card.title for card in backlog_cards] == [
        "Research-generated remediation task",
        "Implement status store",
        "Build queue operations",
    ]
    assert "research_recovery:freeze:start" not in (
        workspace / "agents/tasksbackburner.md"
    ).read_text(encoding="utf-8")
    assert not (workspace / "agents/.runtime/research_recovery_latch.json").exists()


def test_thaw_waits_until_visible_backlog_cards_reappear(tmp_path: Path) -> None:
    queue, workspace = make_queue(tmp_path)
    (workspace / "agents/tasks.md").write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")
    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{TASK_BETA}", encoding="utf-8")

    active_card = queue.active_task()
    assert active_card is not None
    latch = queue.quarantine(
        active_card,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-QUEUE-004.md"),
    )
    thawed = queue.thaw(latch)

    assert thawed == 0
    assert parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8")) == []
    assert "research_recovery:freeze:start" in (
        workspace / "agents/tasksbackburner.md"
    ).read_text(encoding="utf-8")
    assert (workspace / "agents/.runtime/research_recovery_latch.json").exists()


def test_thaw_ignores_remediation_spec_mismatch_once_any_visible_backlog_work_exists(tmp_path: Path) -> None:
    queue, workspace = make_queue(tmp_path)
    (workspace / "agents/tasks.md").write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")
    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{TASK_BETA}", encoding="utf-8")

    active_card = queue.active_task()
    assert active_card is not None
    latch = queue.quarantine(
        active_card,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-QUEUE-004.md"),
    )
    latch_with_decision = latch.model_copy(
        update={
            "remediation_decision": ResearchRecoveryDecision.model_validate(
                {
                    "decision_type": "regenerated_backlog_work",
                    "decided_at": "2026-03-21T12:00:00Z",
                    "remediation_spec_id": "SPEC-THAW",
                    "remediation_record_path": "agents/.research_runtime/incidents/remediation/queue-thaw.json",
                    "taskaudit_record_path": "agents/.research_runtime/goalspec/taskaudit/queue-thaw.json",
                    "task_provenance_path": "agents/task_provenance.json",
                    "lineage_path": "agents/.research_runtime/incidents/lineage/inc-queue-004.json",
                    "pending_card_count": 1,
                    "backlog_card_count": 1,
                }
            )
        }
    )

    unrelated_task = """## 2026-03-21 - Unrelated recovery task

- **Goal:** This should still allow Bash-style thaw once backlog work exists.
- **Spec-ID:** SPEC-OTHER
"""
    (workspace / "agents/tasksbacklog.md").write_text(f"# Task Backlog\n\n{unrelated_task}", encoding="utf-8")

    thawed = queue.thaw(latch_with_decision)

    assert thawed == 2
    backlog_cards = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert [card.spec_id for card in backlog_cards] == ["SPEC-OTHER", "SPEC-STATUS", "SPEC-QUEUE"]
    assert "research_recovery:freeze:start" not in (
        workspace / "agents/tasksbackburner.md"
    ).read_text(encoding="utf-8")
    assert not (workspace / "agents/.runtime/research_recovery_latch.json").exists()


def test_cleanup_remove_rewrites_backlog_and_records_cleanup_trail(tmp_path: Path) -> None:
    queue, workspace = make_queue(tmp_path)
    backlog_path = workspace / "agents/tasksbacklog.md"
    backlog_path.write_text(f"# Task Backlog\n\n{TASK_ALPHA}\n\n{TASK_BETA}", encoding="utf-8")
    backlog_cards = parse_task_cards(backlog_path.read_text(encoding="utf-8"))

    record = queue.remove_task(backlog_cards[1].task_id, reason="invalid duplicate backlog task")

    assert record.action == "remove"
    assert record.source_store == "backlog"
    assert record.destination_store == "backburner"
    assert record.task.title == "Build queue operations"
    assert record.reason == "invalid duplicate backlog task"

    remaining = parse_task_cards(backlog_path.read_text(encoding="utf-8"))
    assert [card.title for card in remaining] == ["Implement status store"]

    backburner_text = (workspace / "agents/tasksbackburner.md").read_text(encoding="utf-8")
    assert "queue_cleanup:remove:start" in backburner_text
    assert "source_store: backlog" in backburner_text
    assert "reason: invalid duplicate backlog task" in backburner_text
    assert "Build queue operations" in backburner_text


def test_cleanup_quarantine_clears_active_task_and_records_cleanup_trail(tmp_path: Path) -> None:
    queue, workspace = make_queue(tmp_path)
    (workspace / "agents/tasks.md").write_text(f"# Active Task\n\n{TASK_ALPHA}", encoding="utf-8")
    active_card = queue.active_task()
    assert active_card is not None

    record = queue.quarantine_task(active_card.task_id, reason="obsolete active task after operator review")

    assert record.action == "quarantine"
    assert record.source_store == "active"
    assert record.destination_store == "backburner"
    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8")) == []

    backburner_text = (workspace / "agents/tasksbackburner.md").read_text(encoding="utf-8")
    assert "queue_cleanup:quarantine:start" in backburner_text
    assert "source_store: active" in backburner_text
    assert "reason: obsolete active task after operator review" in backburner_text
    assert "Implement status store" in backburner_text


def test_merge_pending_family_updates_backlog_and_clears_pending_surfaces(tmp_path: Path) -> None:
    queue, workspace = make_queue(tmp_path)
    backlog_path = workspace / "agents/tasksbacklog.md"
    pending_path = workspace / "agents/taskspending.md"
    shard_path = workspace / "agents/taskspending/SPEC-THAW.md"

    backlog_path.write_text(f"# Task Backlog\n\n{TASK_ALPHA}", encoding="utf-8")
    pending_path.write_text(f"# Tasks Pending\n\n{TASK_GAMMA}", encoding="utf-8")
    shard_path.parent.mkdir(parents=True, exist_ok=True)
    shard_path.write_text(TASK_GAMMA, encoding="utf-8")

    merged_cards = parse_task_cards(backlog_path.read_text(encoding="utf-8")) + parse_task_cards(
        pending_path.read_text(encoding="utf-8")
    )
    queue.merge_pending_family(
        expected_backlog_sha256=sha256(backlog_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
        expected_pending_sha256=sha256(pending_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
        ordered_backlog_cards=merged_cards,
        pending_preamble="# Tasks Pending",
        clear_shard_paths=(shard_path,),
    )

    backlog_cards = parse_task_cards(backlog_path.read_text(encoding="utf-8"))
    assert [card.title for card in backlog_cards] == [
        "Implement status store",
        "Research-generated remediation task",
    ]
    assert parse_task_cards(pending_path.read_text(encoding="utf-8")) == []
    assert not shard_path.exists()
