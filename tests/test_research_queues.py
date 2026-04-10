from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from millrace_engine.contracts import ControlPlane, ExecutionStatus
from millrace_engine.research.audit import AuditTrigger
from millrace_engine.research import BlockerQueueRecord, IncidentDocument
from millrace_engine.research.queues import discover_research_queues
from millrace_engine.research.state import (
    ResearchQueueFamily,
    ResearchQueueSelectionAuthority,
    ResearchRuntimeState,
    write_research_runtime_state,
)

from tests.support import load_workspace_fixture, runtime_paths


def _setup_research_queue_dirs(agents_dir: Path) -> None:
    for relative in [
        "ideas/raw",
        "ideas/staging",
        "ideas/specs",
        "ideas/specs_reviewed",
        "ideas/incidents/incoming",
        "ideas/incidents/working",
        "ideas/incidents/resolved",
        "ideas/incidents/archived",
        "ideas/blockers/incoming",
        "ideas/blockers/working",
        "ideas/blockers/resolved",
        "ideas/blockers/archived",
        "ideas/audit/incoming",
        "ideas/audit/working",
        "ideas/audit/passed",
        "ideas/audit/failed",
    ]:
        (agents_dir / relative).mkdir(parents=True, exist_ok=True)


def _write_audit_file(
    path: Path,
    *,
    audit_id: str,
    trigger: str = "manual",
    status: str = "incoming",
    scope: str = "manual-audit",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"audit_id: {audit_id}",
                f"scope: {scope}",
                f"trigger: {trigger}",
                f"status: {status}",
                "owner: qa",
                "created_at: 2026-03-21T12:00:00Z",
                "updated_at: 2026-03-21T12:05:00Z",
                "---",
                "",
                f"# Audit {audit_id}",
                "",
                "## Objective",
                "- Validate the queue contract.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_discover_research_queues_reports_empty_workspace_deterministically(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    _setup_research_queue_dirs(paths.agents_dir)

    discovery = discover_research_queues(paths)

    assert [scan.family for scan in discovery.families] == [
        ResearchQueueFamily.GOALSPEC,
        ResearchQueueFamily.INCIDENT,
        ResearchQueueFamily.BLOCKER,
        ResearchQueueFamily.AUDIT,
    ]
    assert discovery.ready_families == ()
    assert discovery.family_scan(ResearchQueueFamily.GOALSPEC).boundary is not None
    assert discovery.family_scan(ResearchQueueFamily.GOALSPEC).boundary.queue_owner is ControlPlane.RESEARCH
    assert [path.as_posix() for path in discovery.family_scan(ResearchQueueFamily.INCIDENT).contract_paths] == [
        "agents/ideas/incidents/incoming",
        "agents/ideas/incidents/working",
        "agents/ideas/incidents/resolved",
        "agents/ideas/incidents/archived",
    ]
    assert {
        boundary.queue_owner for boundary in discovery.family_scan(ResearchQueueFamily.BLOCKER).boundaries
    } == {ControlPlane.EXECUTION, ControlPlane.RESEARCH}
    assert [path.as_posix() for path in discovery.family_scan(ResearchQueueFamily.BLOCKER).contract_paths] == [
        "agents/tasksblocker.md",
        "agents/ideas/blockers/incoming",
        "agents/ideas/blockers/working",
        "agents/ideas/blockers/resolved",
        "agents/ideas/blockers/archived",
    ]
    assert [path.as_posix() for path in discovery.family_scan(ResearchQueueFamily.AUDIT).contract_paths] == [
        "agents/ideas/audit/incoming",
        "agents/ideas/audit/working",
        "agents/ideas/audit/passed",
        "agents/ideas/audit/failed",
    ]
    assert all(not scan.ready for scan in discovery.families)
    assert all(scan.items == () for scan in discovery.families)


def test_discover_research_queues_preserves_goal_and_spec_root_priority(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    _setup_research_queue_dirs(paths.agents_dir)

    (paths.agents_dir / "ideas" / "raw" / "2026-03-19-alpha.md").write_text("# raw alpha\n", encoding="utf-8")
    (paths.agents_dir / "ideas" / "raw" / "2026-03-20-beta.md").write_text("# raw beta\n", encoding="utf-8")
    (paths.agents_dir / "ideas" / "specs" / "SPEC-100__gamma.md").write_text("# spec gamma\n", encoding="utf-8")
    (paths.agents_dir / "ideas" / "specs" / "notes.txt").write_text("ignore me\n", encoding="utf-8")

    discovery = discover_research_queues(paths)
    goalspec = discovery.family_scan(ResearchQueueFamily.GOALSPEC)

    assert goalspec.ready
    assert [item.item_path for item in goalspec.items] == [
        paths.agents_dir / "ideas" / "raw" / "2026-03-19-alpha.md",
        paths.agents_dir / "ideas" / "raw" / "2026-03-20-beta.md",
        paths.agents_dir / "ideas" / "specs" / "SPEC-100__gamma.md",
    ]
    assert [path.as_posix() for path in goalspec.contract_paths] == [
        "agents/ideas/raw",
        "agents/ideas/staging",
        "agents/ideas/specs",
        "agents/ideas/specs_reviewed",
    ]


def test_discover_research_queues_orders_directory_items_by_oldest_file_first(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    _setup_research_queue_dirs(paths.agents_dir)

    older_path = paths.agents_dir / "ideas" / "raw" / "zeta.md"
    newer_path = paths.agents_dir / "ideas" / "raw" / "alpha.md"
    older_path.write_text("# older\n", encoding="utf-8")
    newer_path.write_text("# newer\n", encoding="utf-8")
    os.utime(older_path, ns=(1_710_000_000_000_000_000, 1_710_000_000_000_000_000))
    os.utime(newer_path, ns=(1_711_000_000_000_000_000, 1_711_000_000_000_000_000))

    discovery = discover_research_queues(paths)

    assert [item.item_path for item in discovery.family_scan(ResearchQueueFamily.GOALSPEC).items] == [
        older_path,
        newer_path,
    ]


def test_discover_research_queues_handles_mixed_families_and_snapshot_projection(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    _setup_research_queue_dirs(paths.agents_dir)

    (paths.agents_dir / "ideas" / "raw" / "2026-03-19-goal.md").write_text("# goal\n", encoding="utf-8")
    (paths.agents_dir / "ideas" / "incidents" / "incoming" / "INC-002.md").write_text("# incident 2\n", encoding="utf-8")
    (paths.agents_dir / "ideas" / "incidents" / "incoming" / "INC-010.md").write_text("# incident 10\n", encoding="utf-8")
    (paths.agents_dir / "ideas" / "audit" / "incoming" / "AUD-001.md").write_text("# audit\n", encoding="utf-8")
    paths.blocker_file.write_text(
        "\n".join(
            [
                "# Task Blockers",
                "",
                "## 2026-03-19 12:00:00 UTC — Queue handoff stalled",
                "",
                "- **Status:** `### NEEDS_RESEARCH`",
                "- **Stage blocked:** consult",
                "- **Source task card:** 2026-03-19__queue-handoff-stalled",
                "- **Deterministic next action:** Write incident intake and defer to research.",
                "- **Incident intake:** `agents/ideas/incidents/incoming/INC-002.md`",
                "",
                "## 2026-03-19 12:05:00 UTC — Missing audit evidence",
                "",
                "- **Status:** `### BLOCKED`",
                "- **Stage blocked:** qa",
                "- **Source task card:** 2026-03-19__missing-audit-evidence",
                "- **Deterministic next action:** Prepare backlog-empty audit ticket.",
                "- **Incident intake:** n/a",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    discovery = discover_research_queues(paths)
    blocker = discovery.family_scan(ResearchQueueFamily.BLOCKER)
    snapshot = discovery.to_snapshot(last_scanned_at=datetime(2026, 3, 19, 13, 0, tzinfo=timezone.utc))

    assert discovery.ready_families == (
        ResearchQueueFamily.GOALSPEC,
        ResearchQueueFamily.INCIDENT,
        ResearchQueueFamily.BLOCKER,
        ResearchQueueFamily.AUDIT,
    )
    assert [item.item_path for item in discovery.family_scan(ResearchQueueFamily.INCIDENT).items] == [
        paths.agents_dir / "ideas" / "incidents" / "incoming" / "INC-002.md",
        paths.agents_dir / "ideas" / "incidents" / "incoming" / "INC-010.md",
    ]
    assert blocker.boundary is None
    assert {boundary.queue_owner for boundary in blocker.boundaries} == {
        ControlPlane.EXECUTION,
        ControlPlane.RESEARCH,
    }
    assert [item.source_status for item in blocker.items] == [
        ExecutionStatus.NEEDS_RESEARCH,
        ExecutionStatus.BLOCKED,
    ]
    assert blocker.items[0].incident_path == Path("agents/ideas/incidents/incoming/INC-002.md")
    assert snapshot.goalspec_ready is True
    assert snapshot.incident_ready is True
    assert snapshot.blocker_ready is True
    assert snapshot.audit_ready is True
    assert snapshot.selected_family is None
    assert snapshot.selected_family_authority is None


def test_research_queue_snapshot_can_resume_owned_family_without_fresh_readiness(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    _setup_research_queue_dirs(paths.agents_dir)

    discovery = discover_research_queues(paths)
    snapshot = discovery.to_snapshot(
        ownerships=(
            {
                "family": "goalspec",
                "queue_path": "agents/ideas/specs_reviewed",
                "item_path": "agents/ideas/specs_reviewed/SPEC-100.md",
                "owner_token": "research-restart-run",
                "acquired_at": "2026-03-19T12:00:00Z",
            },
        ),
        last_scanned_at=datetime(2026, 3, 19, 12, 5, tzinfo=timezone.utc),
        selected_family=ResearchQueueFamily.GOALSPEC,
        selected_family_authority=ResearchQueueSelectionAuthority.CHECKPOINT,
    )

    assert snapshot.goalspec_ready is False
    assert snapshot.selected_family is ResearchQueueFamily.GOALSPEC
    assert snapshot.selected_family_authority is ResearchQueueSelectionAuthority.CHECKPOINT
    assert snapshot.ownerships[0].family is ResearchQueueFamily.GOALSPEC


def test_discover_research_queues_keeps_terminal_roots_non_actionable(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    _setup_research_queue_dirs(paths.agents_dir)

    (paths.agents_dir / "ideas" / "incidents" / "archived" / "INC-999.md").write_text(
        "# archived incident\n",
        encoding="utf-8",
    )
    (paths.agents_dir / "ideas" / "blockers" / "archived" / "BLK-999.md").write_text(
        "# archived blocker\n",
        encoding="utf-8",
    )
    (paths.agents_dir / "ideas" / "audit" / "passed" / "AUD-999.md").write_text(
        "# passed audit\n",
        encoding="utf-8",
    )
    (paths.agents_dir / "ideas" / "audit" / "failed" / "AUD-998.md").write_text(
        "# failed audit\n",
        encoding="utf-8",
    )

    discovery = discover_research_queues(paths)

    assert discovery.ready_families == ()
    assert discovery.family_scan(ResearchQueueFamily.INCIDENT).items == ()
    assert discovery.family_scan(ResearchQueueFamily.BLOCKER).items == ()
    assert discovery.family_scan(ResearchQueueFamily.AUDIT).items == ()


def test_discovery_does_not_mutate_existing_queue_ownership_state(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    _setup_research_queue_dirs(paths.agents_dir)

    state = ResearchRuntimeState.model_validate(
        {
            "updated_at": "2026-03-19T12:00:00Z",
            "current_mode": "STUB",
            "last_mode": "STUB",
            "mode_reason": "preexisting-state",
            "queue_snapshot": {
                "goalspec_ready": True,
                "selected_family": "goalspec",
                "ownerships": [
                    {
                        "family": "goalspec",
                        "queue_path": "agents/ideas/raw",
                        "item_path": "agents/ideas/raw/2026-03-19-goal.md",
                        "owner_token": "research-run-1",
                        "acquired_at": "2026-03-19T11:59:00Z",
                    }
                ],
            },
        }
    )
    write_research_runtime_state(paths.research_state_file, state)
    (paths.agents_dir / "ideas" / "raw" / "2026-03-19-goal.md").write_text("# goal\n", encoding="utf-8")

    state_before = paths.research_state_file.read_text(encoding="utf-8")
    blocker_before = paths.blocker_file.read_text(encoding="utf-8")

    discovery = discover_research_queues(paths)

    assert discovery.family_scan(ResearchQueueFamily.GOALSPEC).ready is True
    assert paths.research_state_file.read_text(encoding="utf-8") == state_before
    assert paths.blocker_file.read_text(encoding="utf-8") == blocker_before
    assert not paths.queue_lock_file.exists()


def test_discover_research_queues_uses_workspace_independent_file_item_keys(tmp_path: Path) -> None:
    workspace_a, config_path_a = load_workspace_fixture(tmp_path / "workspace-a", "control_mailbox")
    workspace_b, config_path_b = load_workspace_fixture(tmp_path / "workspace-b", "control_mailbox")
    paths_a = runtime_paths(config_path_a)
    paths_b = runtime_paths(config_path_b)
    _setup_research_queue_dirs(paths_a.agents_dir)
    _setup_research_queue_dirs(paths_b.agents_dir)

    for workspace in (workspace_a, workspace_b):
        (workspace / "agents" / "ideas" / "raw" / "2026-03-19-goal.md").write_text("# goal\n", encoding="utf-8")

    item_a = discover_research_queues(paths_a).family_scan(ResearchQueueFamily.GOALSPEC).items[0]
    item_b = discover_research_queues(paths_b).family_scan(ResearchQueueFamily.GOALSPEC).items[0]

    assert item_a.item_key == "agents/ideas/raw/2026-03-19-goal.md"
    assert item_b.item_key == item_a.item_key
    assert item_a.item_path != item_b.item_path


def test_discover_research_queues_includes_active_working_roots(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    _setup_research_queue_dirs(paths.agents_dir)

    (workspace / "agents" / "ideas" / "specs_reviewed" / "SPEC-200__ready.md").write_text(
        "# reviewed spec\n",
        encoding="utf-8",
    )
    (workspace / "agents" / "ideas" / "incidents" / "working" / "INC-200.md").write_text(
        "# incident working\n",
        encoding="utf-8",
    )
    (workspace / "agents" / "ideas" / "blockers" / "working" / "BLK-200.md").write_text(
        "# blocker working\n",
        encoding="utf-8",
    )
    (workspace / "agents" / "ideas" / "audit" / "working" / "AUD-200.md").write_text(
        "# audit working\n",
        encoding="utf-8",
    )

    discovery = discover_research_queues(paths)

    assert discovery.family_scan(ResearchQueueFamily.GOALSPEC).items[0].item_key == (
        "agents/ideas/specs_reviewed/SPEC-200__ready.md"
    )
    assert [item.item_key for item in discovery.family_scan(ResearchQueueFamily.INCIDENT).items] == [
        "agents/ideas/incidents/working/INC-200.md",
    ]
    assert [item.item_key for item in discovery.family_scan(ResearchQueueFamily.BLOCKER).items] == [
        "agents/ideas/blockers/working/BLK-200.md",
    ]
    assert [item.item_key for item in discovery.family_scan(ResearchQueueFamily.AUDIT).items] == [
        "agents/ideas/audit/working/AUD-200.md",
    ]


def test_discover_research_queues_attaches_typed_incident_and_blocker_contracts(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    _setup_research_queue_dirs(paths.agents_dir)

    incident_path = workspace / "agents" / "ideas" / "incidents" / "incoming" / "INC-300.md"
    incident_path.write_text(
        "\n".join(
            [
                "---",
                "incident_id: INC-300",
                "status: incoming",
                "severity: S2",
                "opened_at: 2026-03-21T12:00:00Z",
                "updated_at: 2026-03-21T12:05:00Z",
                "---",
                "",
                "# Queue contract incident",
                "",
                "## Summary",
                "- Preserve current consult lineage.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paths.blocker_file.write_text(
        "\n".join(
            [
                "# Task Blockers",
                "",
                "## 2026-03-21 12:10:00 UTC — Queue contract blocker",
                "",
                "- **Status:** `### NEEDS_RESEARCH`",
                "- **Stage blocked:** consult",
                "- **Source task card:** agents/tasks.md :: ## 2026-03-21 - Queue contract blocker",
                "- **Root-cause summary:** Consult exhausted the local path.",
                "- **Deterministic next action:** Route to incident intake.",
                "- **Incident intake:** `agents/ideas/incidents/incoming/INC-300.md`",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    discovery = discover_research_queues(paths)
    incident_item = discovery.family_scan(ResearchQueueFamily.INCIDENT).items[0]
    blocker_item = discovery.family_scan(ResearchQueueFamily.BLOCKER).items[0]

    assert incident_item.incident_document is not None
    assert incident_item.title == "Queue contract incident"
    assert incident_item.incident_document.incident_id == "INC-300"
    assert incident_item.incident_document.lifecycle_status.value == "incoming"
    assert incident_item.incident_document.summary == "Preserve current consult lineage."
    assert blocker_item.blocker_record is not None
    assert blocker_item.blocker_record.status is ExecutionStatus.NEEDS_RESEARCH
    assert blocker_item.blocker_record.root_cause_summary == "Consult exhausted the local path."
    assert blocker_item.blocker_record.incident_path == Path("agents/ideas/incidents/incoming/INC-300.md")


def test_discover_research_queues_attaches_typed_audit_contracts(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    paths = runtime_paths(config_path)
    _setup_research_queue_dirs(paths.agents_dir)

    audit_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-300.md"
    _write_audit_file(
        audit_path,
        audit_id="AUD-300",
        trigger="manual",
        status="incoming",
        scope="explicit-audit-contract",
    )

    discovery = discover_research_queues(paths)
    audit_item = discovery.family_scan(ResearchQueueFamily.AUDIT).items[0]

    assert audit_item.audit_record is not None
    assert audit_item.title == "Audit AUD-300"
    assert audit_item.audit_record.audit_id == "AUD-300"
    assert audit_item.audit_record.trigger is AuditTrigger.MANUAL
    assert audit_item.audit_record.lifecycle_status.value == "incoming"
    assert audit_item.audit_record.scope == "explicit-audit-contract"
    assert discovery.to_snapshot().audit_ready is True


def test_research_package_exports_typed_incident_and_blocker_contracts() -> None:
    assert IncidentDocument.__name__ == "IncidentDocument"
    assert BlockerQueueRecord.__name__ == "BlockerQueueRecord"
