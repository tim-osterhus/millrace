from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.contracts import CrossPlaneParentRun, ExecutionResearchHandoff, ResearchStatus
from millrace_engine.events import EventType
from millrace_engine.research.incident_document_rendering import _slugify, render_incident_fix_spec
from millrace_engine.research.incident_documents import load_incident_document, parse_incident_document
from millrace_engine.research.incident_intake_helpers import materialize_incident_source
from millrace_engine.research.incident_state_helpers import incident_archive_evidence_paths
from millrace_engine.research.incidents import IncidentFixSpecRecord, IncidentRemediationRecord, resolve_incident_source
from millrace_engine.research.path_helpers import _normalize_path_token, _relative_path, _resolve_path_token
from millrace_engine.research.state import ResearchCheckpoint
from millrace_engine.sentinel_incidents import persist_sentinel_incident
from millrace_engine.sentinel_models import SentinelIncidentPayload
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


def test_research_path_helpers_preserve_token_and_relative_path_semantics(tmp_path: Path) -> None:
    workspace, paths = _configured_paths(tmp_path)
    absolute = workspace / "agents" / "ideas" / "incidents" / "incoming" / "INC-PATH-001.md"

    assert _normalize_path_token(Path("agents/ideas/incidents/incoming/INC-PATH-001.md")) == (
        "agents/ideas/incidents/incoming/INC-PATH-001.md"
    )
    assert _normalize_path_token("  agents/ideas/incidents/incoming/INC-PATH-001.md  ") == (
        "agents/ideas/incidents/incoming/INC-PATH-001.md"
    )
    assert _normalize_path_token("   ") == ""
    assert _resolve_path_token(absolute, relative_to=paths.root) == absolute
    assert _resolve_path_token("agents/ideas/incidents/incoming/INC-PATH-001.md", relative_to=paths.root) == absolute
    assert _relative_path(absolute, relative_to=paths.root) == "agents/ideas/incidents/incoming/INC-PATH-001.md"
    assert _relative_path(Path("/tmp/external.md"), relative_to=paths.root) == "/tmp/external.md"


def test_incident_document_module_parses_frontmatter_and_summary_contracts(tmp_path: Path) -> None:
    incident_path = tmp_path / "agents" / "ideas" / "incidents" / "incoming" / "INC-DOC-001.md"
    incident_path.parent.mkdir(parents=True, exist_ok=True)
    incident_path.write_text(
        "\n".join(
            [
                "---",
                "incident_id: INC-DOC-001",
                "status: incoming",
                "severity: s3",
                "fingerprint: fp-doc-001",
                "failure_signature: consult:doc-001",
                "source_task: agents/tasks.md :: ## Incident doc parsing",
                "opened_at: 2026-03-21T12:00:00Z",
                "updated_at: 2026-03-21T12:05:00Z",
                "---",
                "",
                "# Incident doc parsing",
                "",
                "- **Incident-ID:** `INC-DOC-001`",
                "- **Severity Class:** `S3`",
                "",
                "## Summary",
                "- Preserve summary normalization across the extraction seam.",
                "- Keep queue discovery behavior stable.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    document = load_incident_document(incident_path)
    reparsed = parse_incident_document(incident_path.read_text(encoding="utf-8"), source_path=incident_path)

    assert document == reparsed
    assert document.incident_id == "INC-DOC-001"
    assert document.lifecycle_status.value == "incoming"
    assert document.severity.value == "S3"
    assert document.summary == "Preserve summary normalization across the extraction seam. Keep queue discovery behavior stable."


def test_incident_document_rendering_preserves_fix_spec_contract_text(tmp_path: Path) -> None:
    incident_path = tmp_path / "agents" / "ideas" / "incidents" / "resolved" / "INC-RENDER-001.md"
    document = parse_incident_document(
        "\n".join(
            [
                "---",
                "incident_id: INC-RENDER-001",
                "status: resolved",
                "severity: S2",
                "---",
                "",
                "# Rendering seam incident",
                "",
                "## Summary",
                "- Preserve generated remediation copy.",
                "",
            ]
        )
        + "\n",
        source_path=incident_path,
    )

    rendered = render_incident_fix_spec(
        emitted_at=_dt("2026-03-21T12:05:00Z"),
        document=document,
        resolved_path="agents/ideas/incidents/resolved/INC-RENDER-001.md",
        lineage_path="agents/.research_runtime/incidents/lineage/inc-render-001.json",
        spec_id="SPEC-INC-RENDER-001",
        scope_summary="Preserve generated remediation copy.",
    )

    assert "spec_id: SPEC-INC-RENDER-001" in rendered
    assert "title: Rendering seam incident remediation" in rendered
    assert "## Requirements Traceability (Req-ID Matrix)" in rendered
    assert "`agents/ideas/incidents/resolved/INC-RENDER-001.md`" in rendered


def test_incident_document_rendering_slugify_keeps_ascii_only_legacy_contract() -> None:
    assert _slugify("Café Incident") == "caf-incident"
    assert _slugify("Δelta failure") == "elta-failure"
    assert _slugify("中文 事件") == "incident"


def test_persist_sentinel_incident_generates_parseable_incident_and_bundle(tmp_path: Path) -> None:
    workspace, paths = _configured_paths(tmp_path)
    bundle = persist_sentinel_incident(
        paths,
        payload=SentinelIncidentPayload(
            failure_signature="sentinel:no-progress",
            summary="Sentinel detected no meaningful progress.",
            severity="S2",
            routing_target="troubleshoot",
            evidence_pointers=(
                "agents/reports/sentinel/latest.json",
                "agents/.runtime/recovery/latest.json",
            ),
            observed_status_markers=(
                {"plane": "execution", "marker": "IDLE", "source_path": "agents/status.md"},
                {"plane": "research", "marker": "IDLE", "source_path": "agents/research_status.md"},
            ),
            elapsed_since_last_progress_seconds=900,
            source="sentinel",
            suggested_recovery="Queue troubleshoot with the linked recovery request.",
            recovery_request_id="recovery-20260411T010000000000Z-troubleshoot",
            sentinel_check_id="sentinel-20260411T010000Z",
            sentinel_report_path="agents/reports/sentinel/latest.json",
            sentinel_state_path="agents/.runtime/sentinel/state.json",
            report_status="degraded",
            report_reason="no-meaningful-progress-for-900-seconds",
        ),
        issuer="sentinel.test",
        emitted_at=_dt("2026-04-11T01:00:00Z"),
        incident_id="INC-SENTINEL-TROUBLESHOOT-20260411T010000Z-TEST0001",
    )

    incident_path = workspace / bundle.incident_path
    assert incident_path.exists()
    document = load_incident_document(incident_path)
    assert document.incident_id == "INC-SENTINEL-TROUBLESHOOT-20260411T010000Z-TEST0001"
    assert document.failure_signature == "sentinel:no-progress"
    assert document.severity is not None and document.severity.value == "S2"
    assert document.source_task == "sentinel :: sentinel-20260411T010000Z"

    incident_text = incident_path.read_text(encoding="utf-8")
    assert "source: sentinel" in incident_text
    assert "routing_target: troubleshoot" in incident_text
    assert "`recovery-20260411T010000000000Z-troubleshoot`" in incident_text
    assert (workspace / bundle.bundle_path).exists()
