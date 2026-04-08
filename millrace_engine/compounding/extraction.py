"""Run-scoped reusable-procedure extraction from execution transitions."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..contract_compounding import ProcedureScope, ReusableProcedureArtifact
from ..contract_core import StageType
from ..contract_documents import StageResult
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from ..provenance import RuntimeTransitionRecord

_SUPPORTED_EDGE_CONTEXT: dict[tuple[StageType, str, str], tuple[str, tuple[str, ...]]] = {
    (
        StageType.BUILDER,
        "BUILDER_COMPLETE",
        "execution.builder.success.integration",
    ): ("Builder execution candidate", ("builder", "execution-success")),
    (
        StageType.BUILDER,
        "BUILDER_COMPLETE",
        "execution.builder.success.qa",
    ): ("Builder execution candidate", ("builder", "execution-success")),
    (
        StageType.QA,
        "QA_COMPLETE",
        "execution.qa.success.update",
    ): ("QA validation candidate", ("qa", "execution-success")),
    (
        StageType.DOUBLECHECK,
        "QA_COMPLETE",
        "execution.doublecheck.success.update",
    ): ("Quickfix recovery candidate", ("quickfix", "recovery-success")),
    (
        StageType.TROUBLESHOOT,
        "TROUBLESHOOT_COMPLETE",
        "execution.troubleshoot.success.resume",
    ): ("Troubleshoot recovery candidate", ("troubleshoot", "recovery-success")),
    (
        StageType.CONSULT,
        "CONSULT_COMPLETE",
        "execution.consult.success.resume",
    ): ("Consult recovery candidate", ("consult", "recovery-success")),
}


def clear_run_scoped_procedure_candidates(paths: RuntimePaths, run_id: str) -> None:
    """Remove stale run-scoped procedure candidates before a run starts."""

    run_dir = paths.compounding_procedures_dir / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)


def persist_candidate_from_transition(
    paths: RuntimePaths,
    record: RuntimeTransitionRecord,
    result: StageResult,
) -> Path | None:
    """Persist one run-scoped candidate procedure when the transition is eligible."""

    edge_id = record.selected_edge_id or ""
    context = _SUPPORTED_EDGE_CONTEXT.get((result.stage, result.status, edge_id))
    if context is None:
        return None

    title, tags = context
    procedure_text = _candidate_source_text(result)
    if procedure_text is None:
        return None

    evidence_refs = _candidate_evidence_refs(paths, record, result)
    artifact = ReusableProcedureArtifact(
        procedure_id=f"proc.run.{record.run_id}.{record.event_id}.{record.node_id}",
        scope=ProcedureScope.RUN,
        source_run_id=record.run_id,
        source_stage=result.stage,
        title=title,
        summary=_candidate_summary(record),
        procedure_markdown=_render_candidate_markdown(record, result, procedure_text),
        tags=tags,
        evidence_refs=evidence_refs,
        created_at=record.timestamp,
    )
    candidate_dir = paths.compounding_procedures_dir / record.run_id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = candidate_dir / f"{record.event_id}--{record.node_id}.json"
    write_text_atomic(candidate_path, artifact.model_dump_json(indent=2) + "\n")
    return candidate_path


def _candidate_source_text(result: StageResult) -> str | None:
    runner_result = result.runner_result
    if runner_result is not None and runner_result.last_response_path is not None:
        response_path = runner_result.last_response_path
        if response_path.exists():
            text = response_path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    for candidate in (result.stdout, result.stderr):
        text = candidate.strip()
        if text:
            return text
    return None


def _candidate_summary(record: RuntimeTransitionRecord) -> str:
    reason = record.selected_edge_reason or f"{record.node_id} completed with {record.status_after}"
    return f"{reason} Run-scoped candidate extracted from finalized transition evidence."


def _render_candidate_markdown(
    record: RuntimeTransitionRecord,
    result: StageResult,
    procedure_text: str,
) -> str:
    lines = [
        f"# {result.stage.value.title()} Candidate",
        "",
        "## Source Outcome",
        f"- Run: `{record.run_id}`",
        f"- Transition Event: `{record.event_id}`",
        f"- Stage: `{result.stage.value}`",
        f"- Status: `{result.status}`",
    ]
    if record.selected_edge_id is not None:
        lines.append(f"- Selected Edge: `{record.selected_edge_id}`")
    diagnostics_ref = record.attributes.get("recovery_diagnostics_dir")
    if diagnostics_ref:
        lines.append(f"- Recovery Diagnostics: `{diagnostics_ref}`")
    lines.extend(
        [
            "",
            "## Candidate Procedure Source",
            procedure_text.rstrip(),
        ]
    )
    return "\n".join(lines).rstrip()


def _candidate_evidence_refs(
    paths: RuntimePaths,
    record: RuntimeTransitionRecord,
    result: StageResult,
) -> tuple[str, ...]:
    refs: list[str] = []
    seen: set[str] = set()

    def add_ref(path_like: str | Path | None) -> None:
        if path_like is None:
            return
        path = path_like if isinstance(path_like, Path) else Path(str(path_like))
        try:
            normalized = path.relative_to(paths.root).as_posix() if path.is_absolute() else path.as_posix()
        except ValueError:
            normalized = path.as_posix()
        normalized = normalized.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        refs.append(normalized)

    add_ref(paths.runs_dir / record.run_id / "transition_history.jsonl")
    run_dir = result.metadata.get("run_dir")
    if isinstance(run_dir, Path):
        add_ref(run_dir)
    else:
        add_ref(run_dir if isinstance(run_dir, str) else None)
    for artifact in result.artifacts:
        add_ref(artifact)
    diagnostics_ref = record.attributes.get("recovery_diagnostics_dir")
    if isinstance(diagnostics_ref, str):
        add_ref(diagnostics_ref)
    return tuple(refs)
