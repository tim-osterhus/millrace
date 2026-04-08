"""Incident remediation document derivation and rendering helpers."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .incident_documents import IncidentDocument, _extract_markdown_field, _strip_ticks
from .normalization_helpers import _normalize_optional_text_or_none, _normalize_required_text
from .persistence_helpers import _sha256_text


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "incident"


def _spec_id_for_incident(document: IncidentDocument, section_text: str) -> str:
    declared = _strip_ticks(_extract_markdown_field(section_text, "Fix Spec ID"))
    if declared:
        normalized = _normalize_required_text(declared, field_name="fix_spec_id")
        return normalized if normalized.upper().startswith("SPEC-") else f"SPEC-{normalized.upper()}"
    return f"SPEC-{_slugify(document.incident_id or document.title).upper()}"


def _scope_summary_for_incident(document: IncidentDocument, section_text: str) -> str:
    declared = _extract_markdown_field(section_text, "Scope summary")
    normalized = _normalize_optional_text_or_none(declared)
    if normalized is not None:
        return normalized
    if document.summary is not None:
        return document.summary
    return f"Remediate incident {document.incident_id or document.title} with a governed fix-spec package."


def _task_step_lines(document: IncidentDocument, scope_summary: str) -> tuple[str, str, str]:
    incident_token = document.incident_id or _slugify(document.title).upper()
    return (
        f"Stabilize the failure path for `{incident_token}` by implementing the minimal unblock-first change set.",
        f"Add regression coverage and validation for `{incident_token}` so the incident cannot recur silently.",
        f"Refresh the affected runtime and task-generation surfaces described by this fix scope: {scope_summary}",
    )


def render_incident_fix_spec(
    *,
    emitted_at: datetime,
    document: IncidentDocument,
    resolved_path: str,
    lineage_path: str,
    spec_id: str,
    scope_summary: str,
) -> str:
    timestamp = emitted_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    incident_id = document.incident_id or document.source_path.stem
    title = f"{document.title} remediation"
    return "\n".join(
        [
            "---",
            f"spec_id: {spec_id}",
            f"idea_id: {incident_id}",
            f"title: {title}",
            "status: proposed",
            "golden_version: 1",
            f"base_goal_sha256: {_sha256_text(f'{incident_id}|{scope_summary}')}",
            "effort: 2",
            "decomposition_profile: simple",
            "depends_on_specs: []",
            "owner: research",
            f"created_at: {timestamp}",
            f"updated_at: {timestamp}",
            "---",
            "",
            "## Summary",
            scope_summary,
            "",
            "## Goals",
            f"- Remediate incident `{incident_id}` through the governed GoalSpec task-generation seam.",
            f"- Preserve traceability from `{resolved_path}` and `{lineage_path}` into the generated remediation work.",
            "- Keep the remediation slice reviewable and compatible with the existing Taskmaster/Taskaudit contract.",
            "",
            "## Non-Goals",
            "- Execution thaw in this run.",
            "- Replacing the existing incident archive and lineage flow.",
            "",
            "## Scope",
            "### In Scope",
            "- Emit a reviewed, stable, and decomposable fix-spec package for this incident.",
            "- Generate deterministic pending and backlog work through Taskmaster and Taskaudit.",
            "",
            "### Out of Scope",
            "- Backlog thaw or execution resume.",
            "- Broad refactors outside the bounded remediation seam.",
            "",
            "## Incident Context",
            f"- Incident path: `{resolved_path}`",
            f"- Lineage path: `{lineage_path}`",
            f"- Severity: `{document.severity.value if document.severity is not None else 'S2'}`",
            "",
            "## Implementation Plan",
            "1. Materialize a bounded fix-spec package from the resolved incident.",
            "2. Run Taskmaster to convert the stable phase plan into strict pending shards.",
            "3. Run Taskaudit to merge governed remediation work into backlog with refreshed provenance.",
            "",
            "## Requirements Traceability (Req-ID Matrix)",
            f"- `Req-ID: REQ-INC-001` | Preserve incident lineage and source-path continuity into remediation artifacts | `{lineage_path}`",
            f"- `Req-ID: REQ-INC-002` | Emit governed fix-spec work from a resolved incident artifact | `{resolved_path}`",
            "- `Req-ID: REQ-INC-003` | Keep remediation generation compatible with existing Taskmaster and Taskaudit behavior | `millrace/millrace_engine/research/taskmaster.py`",
            "",
            "## Assumptions Ledger",
            "- Incident `fix_spec` metadata may be incomplete; bounded defaults are derived when needed.",
            "- This remediation package stays single-spec and single-phase for the current incident slice.",
            "",
            "## Verification",
            "- `python3 -m py_compile millrace/millrace_engine/research/incidents.py millrace/millrace_engine/planes/research.py`",
            "- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q millrace/tests/test_research_dispatcher.py -k incident`",
            "",
            "## Dependencies",
            f"- Incident source: `{resolved_path}`",
            f"- Incident lineage: `{lineage_path}`",
            "- Task generation: `millrace/millrace_engine/research/taskmaster.py`",
            "- Provenance merge: `millrace/millrace_engine/research/taskaudit.py`",
            "",
            "## References",
            "- Research plane: `millrace/millrace_engine/planes/research.py`",
            "- Dispatcher coverage: `millrace/tests/test_research_dispatcher.py`",
            "",
        ]
    )


def render_incident_phase_spec(
    *,
    emitted_at: datetime,
    document: IncidentDocument,
    spec_id: str,
    resolved_path: str,
    lineage_path: str,
    scope_summary: str,
) -> str:
    timestamp = emitted_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    step_1, step_2, step_3 = _task_step_lines(document, scope_summary)
    return "\n".join(
        [
            "---",
            f"phase_id: PHASE-{spec_id}-01",
            "phase_key: PHASE_01",
            "phase_priority: P1",
            f"parent_spec_id: {spec_id}",
            f"title: {document.title} remediation implementation foundation",
            "status: planned",
            "owner: research",
            f"created_at: {timestamp}",
            f"updated_at: {timestamp}",
            "---",
            "",
            "## Objective",
            f"- Convert incident remediation for `{document.incident_id or document.title}` into strict, decomposable work.",
            "",
            "## Entry Criteria",
            f"- Resolved incident exists at `{resolved_path}`.",
            f"- Incident lineage exists at `{lineage_path}`.",
            "",
            "## Scope",
            "### In Scope",
            "- Implement the bounded fix path described by the incident remediation package.",
            "- Preserve regression evidence and runtime traceability for the incident.",
            "",
            "### Out of Scope",
            "- Execution thaw and resume semantics.",
            "- Additional remediation families beyond this incident.",
            "",
            "## Work Plan",
            f"1. {step_1}",
            f"2. {step_2}",
            f"3. {step_3}",
            "",
            "## Verification",
            "- `python3 -m py_compile millrace/millrace_engine/research/incidents.py millrace/millrace_engine/planes/research.py`",
            "- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q millrace/tests/test_research_dispatcher.py -k incident`",
            "",
        ]
    )


def render_incident_review_questions(
    *,
    emitted_at: datetime,
    run_id: str,
    incident_id: str,
    spec_id: str,
    title: str,
    queue_spec_path: str,
) -> str:
    return "\n".join(
        [
            "# Spec Review Questions",
            "",
            f"- **Run-ID:** {run_id}",
            f"- **Goal-ID:** {incident_id}",
            f"- **Spec-ID:** {spec_id}",
            f"- **Title:** {title}",
            f"- **Reviewed-At:** {emitted_at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')}",
            f"- **Queue-Spec:** `{queue_spec_path}`",
            "",
            "## Critic Findings",
            "- No material delta was required before decomposing this incident remediation package.",
            "",
        ]
    )


def render_incident_review_decision(
    *,
    emitted_at: datetime,
    run_id: str,
    incident_id: str,
    spec_id: str,
    title: str,
    reviewed_path: str,
    lineage_path: str,
    stable_registry_path: str,
) -> str:
    return "\n".join(
        [
            "# Spec Review Decision",
            "",
            f"- **Run-ID:** {run_id}",
            f"- **Goal-ID:** {incident_id}",
            f"- **Spec-ID:** {spec_id}",
            f"- **Title:** {title}",
            "- **Review-Status:** `no_material_delta`",
            f"- **Reviewed-At:** {emitted_at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')}",
            f"- **Reviewed-Spec:** `{reviewed_path}`",
            f"- **Stable-Registry:** `{stable_registry_path}`",
            f"- **Lineage-Record:** `{lineage_path}`",
            "",
            "## Decision",
            "- Approved for downstream remediation task generation without additional edits in this run.",
            "",
        ]
    )
