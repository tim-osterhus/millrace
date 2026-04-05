from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.contracts import CrossPlaneParentRun, ExecutionResearchHandoff, ResearchStatus
from millrace_engine.events import EventType
from millrace_engine.research.incident_intake_helpers import materialize_incident_source
from millrace_engine.research.incident_state_helpers import incident_archive_evidence_paths
from millrace_engine.research.incidents import IncidentFixSpecRecord, IncidentRemediationRecord, resolve_incident_source
from millrace_engine.research.state import ResearchCheckpoint
from tests.support import load_workspace_fixture


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _configured_paths(tmp_path: Path):
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    loaded = load_engine_config(config_path)
    return workspace, build_runtime_paths(loaded.config)


def test_materialize_incident_source_creates_authoritative_document_from_handoff_checkpoint(
    tmp_path: Path,
) -> None:
    workspace, paths = _configured_paths(tmp_path)
    incident_rel_path = Path("agents/ideas/incidents/incoming/INC-MATERIALIZE-001.md")
    diagnostics_dir = workspace / "agents" / "diagnostics" / "diag-materialize-incident"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    handoff = ExecutionResearchHandoff(
        handoff_id="execution-run-materialize:needs_research:batch-materialize",
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id="execution-run-materialize",
            snapshot_id="snapshot-materialize",
            frozen_plan_id="frozen-plan:materialize",
            frozen_plan_hash="materialize",
            transition_history_path=Path("agents/runs/execution-run-materialize/transition_history.jsonl"),
        ),
        task_id="agents/tasks.md :: ## 2026-03-21 - Materialize incident source",
        task_title="Materialize incident source",
        stage="Consult",
        reason="Consult exhausted the local path",
        incident_path=incident_rel_path,
        diagnostics_dir=diagnostics_dir,
        recovery_batch_id="batch-materialize",
        failure_signature="consult:materialize",
    )
    checkpoint = ResearchCheckpoint.model_validate(
        {
            "checkpoint_id": "research-incident-materialize",
            "mode": "INCIDENT",
            "status": ResearchStatus.INCIDENT_RUNNING,
            "node_id": "incident_intake",
            "stage_kind_id": "research.incident-intake",
            "started_at": "2026-03-21T12:00:00Z",
            "updated_at": "2026-03-21T12:00:00Z",
            "active_request": {
                "event_type": EventType.NEEDS_RESEARCH.value,
                "received_at": "2026-03-21T12:00:00Z",
                "queue_family": "incident",
                "payload": {"path": incident_rel_path.as_posix()},
                "handoff": handoff.model_dump(mode="json", exclude_none=True),
            },
            "parent_handoff": handoff.model_dump(mode="json", exclude_none=True),
        }
    )

    materialized_path = materialize_incident_source(paths, checkpoint, emitted_at=_dt("2026-03-21T12:05:00Z"))

    assert materialized_path == workspace / incident_rel_path
    assert materialized_path is not None
    assert materialized_path.exists()
    materialized_text = materialized_path.read_text(encoding="utf-8")
    assert "incident_id: INC-MATERIALIZE-001" in materialized_text
    assert "# Materialize incident source" in materialized_text
    assert "Consult returned `NEEDS_RESEARCH` during `Consult`." in materialized_text
    assert "Consult exhausted the local path" in materialized_text
    assert diagnostics_dir.as_posix() in materialized_text

    resolved_path, document = resolve_incident_source(paths, checkpoint)
    assert resolved_path == materialized_path
    assert document.incident_id == "INC-MATERIALIZE-001"
    assert document.source_task == "agents/tasks.md :: ## 2026-03-21 - Materialize incident source"
    assert document.failure_signature == "consult:materialize"


def test_incident_archive_evidence_paths_preserve_runtime_order_and_skip_duplicate_entries(tmp_path: Path) -> None:
    workspace, paths = _configured_paths(tmp_path)
    run_id = "incident-helper-run"
    intake_path = paths.research_runtime_dir / "incidents" / "intake" / f"{run_id}.json"
    resolve_path = paths.research_runtime_dir / "incidents" / "resolve" / f"{run_id}.json"
    remediation_path = paths.research_runtime_dir / "incidents" / "remediation" / f"{run_id}.json"
    lineage_path = paths.research_runtime_dir / "incidents" / "lineage" / "inc-helper-001.json"
    taskmaster_path = paths.research_runtime_dir / "goalspec" / "taskmaster" / f"{run_id}.json"
    taskaudit_path = paths.research_runtime_dir / "goalspec" / "taskaudit" / f"{run_id}.json"
    task_provenance_path = workspace / "agents" / "task_provenance.json"

    for path in (
        intake_path,
        resolve_path,
        lineage_path,
        taskmaster_path,
        taskaudit_path,
        task_provenance_path,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    remediation_record = IncidentRemediationRecord(
        run_id=run_id,
        emitted_at=_dt("2026-03-21T12:05:00Z"),
        incident_id="INC-HELPER-001",
        incident_title="Helper archive evidence",
        resolved_path="agents/ideas/incidents/resolved/INC-HELPER-001.md",
        lineage_path="agents/.research_runtime/incidents/lineage/inc-helper-001.json",
        family_state_path="agents/objective/family_state.json",
        goalspec_lineage_path="agents/.research_runtime/goalspec/lineage/SPEC-INC-HELPER-001.json",
        fix_spec=IncidentFixSpecRecord(
            spec_id="SPEC-INC-HELPER-001",
            title="Helper archive evidence remediation",
            scope_summary="Preserve archive evidence ordering.",
            queue_spec_path="agents/ideas/specs/SPEC-INC-HELPER-001.md",
            reviewed_path="agents/ideas/specs_reviewed/SPEC-INC-HELPER-001.md",
            golden_spec_path="agents/specs/stable/golden/SPEC-INC-HELPER-001.md",
            phase_spec_path="agents/specs/stable/phase/SPEC-INC-HELPER-001__phase-01.md",
            review_questions_path="agents/specs/questions/SPEC-INC-HELPER-001__spec-review.md",
            review_decision_path="agents/specs/decisions/SPEC-INC-HELPER-001__spec-review.md",
            stable_registry_path="agents/specs/index.json",
        ),
        taskmaster_record_path="agents/.research_runtime/goalspec/taskmaster/incident-helper-run.json",
        taskaudit_record_path="agents/.research_runtime/goalspec/taskaudit/incident-helper-run.json",
        task_provenance_path="agents/task_provenance.json",
    )
    remediation_path.parent.mkdir(parents=True, exist_ok=True)
    remediation_path.write_text(remediation_record.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")

    evidence_paths = incident_archive_evidence_paths(paths, run_id=run_id, lineage_path=lineage_path)

    assert evidence_paths == (
        "agents/.research_runtime/incidents/intake/incident-helper-run.json",
        "agents/.research_runtime/incidents/resolve/incident-helper-run.json",
        "agents/.research_runtime/incidents/remediation/incident-helper-run.json",
        "agents/.research_runtime/goalspec/taskmaster/incident-helper-run.json",
        "agents/.research_runtime/goalspec/taskaudit/incident-helper-run.json",
        "agents/task_provenance.json",
        "agents/.research_runtime/incidents/lineage/inc-helper-001.json",
    )
