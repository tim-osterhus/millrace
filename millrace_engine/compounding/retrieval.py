"""Stage-aware procedure retrieval and bounded prompt injection helpers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..contract_compounding import (
    InjectedProcedure,
    ProcedureInjectionBundle,
    ProcedureRetrievalRule,
    ProcedureScope,
    ReusableProcedureArtifact,
)
from ..contract_core import StageType
from ..paths import RuntimePaths


_IMPLEMENTATION_SOURCE_STAGES: tuple[StageType, ...] = (
    StageType.BUILDER,
    StageType.HOTFIX,
    StageType.TROUBLESHOOT,
    StageType.CONSULT,
    StageType.LARGE_EXECUTE,
    StageType.REFACTOR,
)
_VALIDATION_SOURCE_STAGES: tuple[StageType, ...] = (
    StageType.BUILDER,
    StageType.HOTFIX,
    StageType.INTEGRATION,
    StageType.QA,
    StageType.DOUBLECHECK,
    StageType.TROUBLESHOOT,
    StageType.CONSULT,
    StageType.LARGE_EXECUTE,
    StageType.REFACTOR,
)
_UPDATE_SOURCE_STAGES: tuple[StageType, ...] = (
    StageType.QA,
    StageType.DOUBLECHECK,
    StageType.TROUBLESHOOT,
    StageType.CONSULT,
)

_PROCEDURE_RETRIEVAL_RULES: dict[StageType, ProcedureRetrievalRule] = {
    StageType.BUILDER: ProcedureRetrievalRule(
        stage=StageType.BUILDER,
        allowed_scopes=(ProcedureScope.WORKSPACE,),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
    StageType.INTEGRATION: ProcedureRetrievalRule(
        stage=StageType.INTEGRATION,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=_VALIDATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
    StageType.QA: ProcedureRetrievalRule(
        stage=StageType.QA,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=_VALIDATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
    StageType.HOTFIX: ProcedureRetrievalRule(
        stage=StageType.HOTFIX,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
    StageType.DOUBLECHECK: ProcedureRetrievalRule(
        stage=StageType.DOUBLECHECK,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=_VALIDATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
    StageType.TROUBLESHOOT: ProcedureRetrievalRule(
        stage=StageType.TROUBLESHOOT,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
    StageType.CONSULT: ProcedureRetrievalRule(
        stage=StageType.CONSULT,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
    StageType.UPDATE: ProcedureRetrievalRule(
        stage=StageType.UPDATE,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=_UPDATE_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=1600,
    ),
    StageType.LARGE_PLAN: ProcedureRetrievalRule(
        stage=StageType.LARGE_PLAN,
        allowed_scopes=(ProcedureScope.WORKSPACE,),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
    StageType.LARGE_EXECUTE: ProcedureRetrievalRule(
        stage=StageType.LARGE_EXECUTE,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
    StageType.REASSESS: ProcedureRetrievalRule(
        stage=StageType.REASSESS,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=_VALIDATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
    StageType.REFACTOR: ProcedureRetrievalRule(
        stage=StageType.REFACTOR,
        allowed_scopes=(ProcedureScope.RUN, ProcedureScope.WORKSPACE),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_procedures=2,
        max_prompt_characters=2200,
    ),
}


def procedure_retrieval_rule_for_stage(stage: StageType) -> ProcedureRetrievalRule | None:
    """Return the bounded retrieval rule for one execution stage."""

    return _PROCEDURE_RETRIEVAL_RULES.get(stage)


def build_injected_procedure_bundle(
    paths: RuntimePaths,
    *,
    run_id: str,
    stage: StageType,
) -> ProcedureInjectionBundle | None:
    """Load eligible procedures and trim them to the stage-specific budget."""

    rule = procedure_retrieval_rule_for_stage(stage)
    if rule is None:
        return None

    candidates = _eligible_candidates(paths, run_id=run_id, rule=rule)
    if not candidates:
        return None

    selected: list[InjectedProcedure] = []
    used_characters = 0
    truncated_count = 0

    for artifact in candidates:
        if len(selected) >= rule.max_procedures or used_characters >= rule.max_prompt_characters:
            break
        remaining = rule.max_prompt_characters - used_characters
        injected = _build_injected_procedure(artifact, remaining_characters=remaining)
        if injected is None:
            continue
        selected.append(injected)
        used_characters += injected.injected_characters
        if injected.truncated:
            truncated_count += 1

    if not selected:
        return None

    return ProcedureInjectionBundle(
        stage=stage,
        rule=rule,
        procedures=tuple(selected),
        candidate_count=len(candidates),
        selected_count=len(selected),
        budget_characters=rule.max_prompt_characters,
        used_characters=used_characters,
        truncated_count=truncated_count,
    )


def render_injected_procedure_block(bundle: ProcedureInjectionBundle | None) -> str:
    """Render the prompt block for a bounded procedure injection selection."""

    if bundle is None or not bundle.procedures:
        return ""
    lines = [
        "Injected reusable procedures:",
        "",
        (
            "Use the following governed procedures only if they fit the current task and stage. "
            "Treat them as bounded prior runtime guidance, not as mandatory instructions."
        ),
    ]
    for index, procedure in enumerate(bundle.procedures, start=1):
        lines.extend(
            [
                "",
                f"### Procedure {index}: {procedure.title}",
                f"- Procedure ID: `{procedure.procedure_id}`",
                f"- Scope: `{procedure.scope.value}`",
                f"- Source stage: `{procedure.source_stage.value}`",
                f"- Summary: {procedure.summary}",
                "",
                procedure.prompt_excerpt.rstrip(),
            ]
        )
    return "\n".join(lines).rstrip()


def _eligible_candidates(
    paths: RuntimePaths,
    *,
    run_id: str,
    rule: ProcedureRetrievalRule,
) -> list[ReusableProcedureArtifact]:
    candidates: list[ReusableProcedureArtifact] = []
    for artifact in _iter_candidate_artifacts(paths, run_id=run_id, allowed_scopes=rule.allowed_scopes):
        if artifact.source_stage not in rule.allowed_source_stages:
            continue
        candidates.append(artifact)
    candidates.sort(key=_candidate_sort_key)
    return candidates


def _iter_candidate_artifacts(
    paths: RuntimePaths,
    *,
    run_id: str,
    allowed_scopes: tuple[ProcedureScope, ...],
) -> Iterable[ReusableProcedureArtifact]:
    if ProcedureScope.WORKSPACE in allowed_scopes:
        yield from _load_artifacts_from_directory(paths.compounding_procedures_dir)
    if ProcedureScope.RUN in allowed_scopes:
        yield from _load_artifacts_from_directory(paths.compounding_procedures_dir / run_id)


def _load_artifacts_from_directory(directory: Path) -> Iterable[ReusableProcedureArtifact]:
    if not directory.exists():
        return ()
    artifacts: list[ReusableProcedureArtifact] = []
    for path in sorted(directory.glob("*.json")):
        artifacts.append(ReusableProcedureArtifact.model_validate_json(path.read_text(encoding="utf-8")))
    return tuple(artifacts)


def _candidate_sort_key(artifact: ReusableProcedureArtifact) -> tuple[int, float, str]:
    scope_priority = 0 if artifact.scope is ProcedureScope.RUN else 1
    created_at = artifact.created_at.timestamp()
    return (scope_priority, -created_at, artifact.procedure_id)


def _build_injected_procedure(
    artifact: ReusableProcedureArtifact,
    *,
    remaining_characters: int,
) -> InjectedProcedure | None:
    if remaining_characters <= 0:
        return None
    excerpt, truncated = _trim_procedure_markdown(artifact.procedure_markdown, remaining_characters)
    if excerpt is None:
        return None
    return InjectedProcedure(
        procedure_id=artifact.procedure_id,
        scope=artifact.scope,
        source_stage=artifact.source_stage,
        title=artifact.title,
        summary=artifact.summary,
        prompt_excerpt=excerpt,
        evidence_refs=artifact.evidence_refs,
        original_characters=len(artifact.procedure_markdown),
        injected_characters=len(excerpt),
        truncated=truncated,
    )


def _trim_procedure_markdown(markdown: str, budget: int) -> tuple[str | None, bool]:
    text = markdown.strip()
    if not text or budget <= 0:
        return None, False
    if len(text) <= budget:
        return text, False

    suffix = "\n\n[procedure truncated to fit stage budget]"
    if budget <= len(suffix):
        return suffix[:budget].strip() or None, True

    trimmed = text[: budget - len(suffix)].rstrip()
    if "\n" in trimmed:
        trimmed = trimmed.rsplit("\n", 1)[0].rstrip()
    if not trimmed:
        trimmed = text[: budget - len(suffix)].rstrip()
    return f"{trimmed}{suffix}", True

