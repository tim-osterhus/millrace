"""Stage-aware procedure and fact retrieval with bounded prompt injection."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from ..context_facts import discover_context_facts
from ..contract_compounding import (
    ConsideredProcedure,
    InjectedProcedure,
    ProcedureInjectionBundle,
    ProcedureRetrievalRule,
    ProcedureScope,
    ReusableProcedureArtifact,
)
from ..contract_context_facts import (
    ConsideredContextFact,
    ContextFactArtifact,
    ContextFactInjectionBundle,
    ContextFactRetrievalRule,
    ContextFactScope,
    ContextFactSelectionReason,
    InjectedContextFact,
)
from ..contract_core import StageType
from ..paths import RuntimePaths
from .lifecycle import load_retrievable_workspace_procedures

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
_TOKEN_RE = re.compile(r"[A-Za-z0-9]{4,}")

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

_CONTEXT_FACT_RETRIEVAL_RULES: dict[StageType, ContextFactRetrievalRule] = {
    StageType.BUILDER: ContextFactRetrievalRule(
        stage=StageType.BUILDER,
        allowed_scopes=(ContextFactScope.WORKSPACE,),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
    StageType.INTEGRATION: ContextFactRetrievalRule(
        stage=StageType.INTEGRATION,
        allowed_scopes=(ContextFactScope.RUN, ContextFactScope.WORKSPACE),
        allowed_source_stages=_VALIDATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
    StageType.QA: ContextFactRetrievalRule(
        stage=StageType.QA,
        allowed_scopes=(ContextFactScope.RUN, ContextFactScope.WORKSPACE),
        allowed_source_stages=_VALIDATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
    StageType.HOTFIX: ContextFactRetrievalRule(
        stage=StageType.HOTFIX,
        allowed_scopes=(ContextFactScope.RUN, ContextFactScope.WORKSPACE),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
    StageType.DOUBLECHECK: ContextFactRetrievalRule(
        stage=StageType.DOUBLECHECK,
        allowed_scopes=(ContextFactScope.RUN, ContextFactScope.WORKSPACE),
        allowed_source_stages=_VALIDATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
    StageType.TROUBLESHOOT: ContextFactRetrievalRule(
        stage=StageType.TROUBLESHOOT,
        allowed_scopes=(ContextFactScope.RUN, ContextFactScope.WORKSPACE),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
    StageType.CONSULT: ContextFactRetrievalRule(
        stage=StageType.CONSULT,
        allowed_scopes=(ContextFactScope.RUN, ContextFactScope.WORKSPACE),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
    StageType.UPDATE: ContextFactRetrievalRule(
        stage=StageType.UPDATE,
        allowed_scopes=(ContextFactScope.RUN, ContextFactScope.WORKSPACE),
        allowed_source_stages=_UPDATE_SOURCE_STAGES,
        max_facts=1,
        max_prompt_characters=700,
    ),
    StageType.LARGE_PLAN: ContextFactRetrievalRule(
        stage=StageType.LARGE_PLAN,
        allowed_scopes=(ContextFactScope.WORKSPACE,),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
    StageType.LARGE_EXECUTE: ContextFactRetrievalRule(
        stage=StageType.LARGE_EXECUTE,
        allowed_scopes=(ContextFactScope.RUN, ContextFactScope.WORKSPACE),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
    StageType.REASSESS: ContextFactRetrievalRule(
        stage=StageType.REASSESS,
        allowed_scopes=(ContextFactScope.RUN, ContextFactScope.WORKSPACE),
        allowed_source_stages=_VALIDATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
    StageType.REFACTOR: ContextFactRetrievalRule(
        stage=StageType.REFACTOR,
        allowed_scopes=(ContextFactScope.RUN, ContextFactScope.WORKSPACE),
        allowed_source_stages=_IMPLEMENTATION_SOURCE_STAGES,
        max_facts=2,
        max_prompt_characters=1100,
    ),
}


def procedure_retrieval_rule_for_stage(stage: StageType) -> ProcedureRetrievalRule | None:
    """Return the bounded retrieval rule for one execution stage."""

    return _PROCEDURE_RETRIEVAL_RULES.get(stage)


def context_fact_retrieval_rule_for_stage(stage: StageType) -> ContextFactRetrievalRule | None:
    """Return the bounded retrieval rule for durable context facts."""

    return _CONTEXT_FACT_RETRIEVAL_RULES.get(stage)


def build_injected_procedure_bundle(
    paths: RuntimePaths,
    *,
    run_id: str,
    stage: StageType,
    max_total_characters: int | None = None,
) -> ProcedureInjectionBundle | None:
    """Load eligible procedures and trim them to stage and optional combined budgets."""

    rule = procedure_retrieval_rule_for_stage(stage)
    if rule is None:
        return None

    budget = rule.max_prompt_characters
    if max_total_characters is not None:
        budget = min(budget, max_total_characters)
    if budget <= 0:
        return None

    candidates = _eligible_candidates(paths, run_id=run_id, rule=rule)
    if not candidates:
        return None

    selected: list[InjectedProcedure] = []
    used_characters = 0
    truncated_count = 0

    for artifact in candidates:
        if len(selected) >= rule.max_procedures or used_characters >= budget:
            break
        remaining = budget - used_characters
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
        considered_procedures=tuple(_considered_procedure(artifact) for artifact in candidates),
        procedures=tuple(selected),
        candidate_count=len(candidates),
        selected_count=len(selected),
        budget_characters=budget,
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


def build_injected_context_fact_bundle(
    paths: RuntimePaths,
    *,
    run_id: str,
    stage: StageType,
    task_text: str = "",
    max_total_characters: int | None = None,
) -> ContextFactInjectionBundle | None:
    """Load eligible facts and trim them to stage and combined-budget limits."""

    rule = context_fact_retrieval_rule_for_stage(stage)
    if rule is None:
        return None

    budget = rule.max_prompt_characters
    if max_total_characters is not None:
        budget = min(budget, max_total_characters)
    if budget <= 0:
        return None

    candidates = _eligible_context_fact_candidates(paths, run_id=run_id, rule=rule, task_text=task_text)
    if not candidates:
        return None

    selected: list[InjectedContextFact] = []
    used_characters = 0
    truncated_count = 0

    for artifact, selection_reason in candidates:
        if len(selected) >= rule.max_facts or used_characters >= budget:
            break
        remaining = budget - used_characters
        injected = _build_injected_context_fact(
            artifact,
            selection_reason=selection_reason,
            remaining_characters=remaining,
        )
        if injected is None:
            continue
        selected.append(injected)
        used_characters += injected.injected_characters
        if injected.truncated:
            truncated_count += 1

    if not selected:
        return None

    return ContextFactInjectionBundle(
        stage=stage,
        rule=rule,
        considered_facts=tuple(
            _considered_context_fact(artifact, selection_reason=selection_reason)
            for artifact, selection_reason in candidates
        ),
        facts=tuple(selected),
        candidate_count=len(candidates),
        selected_count=len(selected),
        budget_characters=budget,
        used_characters=used_characters,
        truncated_count=truncated_count,
    )


def render_injected_context_fact_block(bundle: ContextFactInjectionBundle | None) -> str:
    """Render the prompt block for a bounded context-fact injection selection."""

    if bundle is None or not bundle.facts:
        return ""
    lines = [
        "Injected durable context facts:",
        "",
        (
            "Treat these as bounded factual context recalled from prior governed runtime evidence. "
            "Keep them separate from procedures and use them only when they materially help the current stage."
        ),
    ]
    for index, fact in enumerate(bundle.facts, start=1):
        tags = ", ".join(f"`{tag}`" for tag in fact.tags)
        lines.extend(
            [
                "",
                f"### Fact {index}: {fact.title}",
                f"- Fact ID: `{fact.fact_id}`",
                f"- Scope: `{fact.scope.value}`",
                f"- Source stage: `{fact.source_stage.value}`",
                f"- Selection reason: `{fact.selection_reason.value}`",
                f"- Summary: {fact.summary}",
            ]
        )
        if tags:
            lines.append(f"- Tags: {tags}")
        lines.extend(["", fact.statement_excerpt.rstrip()])
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
        yield from load_retrievable_workspace_procedures(paths)
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


def _considered_procedure(artifact: ReusableProcedureArtifact) -> ConsideredProcedure:
    return ConsideredProcedure(
        procedure_id=artifact.procedure_id,
        scope=artifact.scope,
        source_stage=artifact.source_stage,
        title=artifact.title,
        summary=artifact.summary,
        evidence_refs=artifact.evidence_refs,
    )


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


def _eligible_context_fact_candidates(
    paths: RuntimePaths,
    *,
    run_id: str,
    rule: ContextFactRetrievalRule,
    task_text: str,
) -> list[tuple[ContextFactArtifact, ContextFactSelectionReason]]:
    candidates: list[tuple[ContextFactArtifact, ContextFactSelectionReason]] = []
    task_tokens = _match_tokens(f"{rule.stage.value} {task_text}")
    for stored_fact in discover_context_facts(paths):
        artifact = stored_fact.artifact
        if artifact.scope not in rule.allowed_scopes:
            continue
        if artifact.source_stage not in rule.allowed_source_stages:
            continue
        if artifact.scope is ContextFactScope.WORKSPACE and not stored_fact.eligible_for_retrieval:
            continue
        if artifact.scope is ContextFactScope.RUN and artifact.source_run_id != run_id:
            continue
        selection_reason = _selection_reason_for_fact(artifact, task_tokens=task_tokens)
        candidates.append((artifact, selection_reason))
    candidates.sort(key=_context_fact_sort_key)
    return candidates


def _selection_reason_for_fact(
    artifact: ContextFactArtifact,
    *,
    task_tokens: frozenset[str],
) -> ContextFactSelectionReason:
    if artifact.scope is ContextFactScope.RUN:
        return ContextFactSelectionReason.RUN_SCOPE
    artifact_tokens = _match_tokens(
        " ".join((artifact.title, artifact.summary, artifact.statement, " ".join(artifact.tags)))
    )
    if task_tokens and artifact_tokens.intersection(task_tokens):
        return ContextFactSelectionReason.PATTERN_MATCH
    return ContextFactSelectionReason.BROADER_SCOPE


def _context_fact_sort_key(
    item: tuple[ContextFactArtifact, ContextFactSelectionReason],
) -> tuple[int, int, float, str]:
    artifact, selection_reason = item
    scope_priority = 0 if artifact.scope is ContextFactScope.RUN else 1
    selection_priority = {
        ContextFactSelectionReason.RUN_SCOPE: 0,
        ContextFactSelectionReason.PATTERN_MATCH: 1,
        ContextFactSelectionReason.BROADER_SCOPE: 2,
    }[selection_reason]
    created_at = artifact.created_at.timestamp()
    return (scope_priority, selection_priority, -created_at, artifact.fact_id)


def _considered_context_fact(
    artifact: ContextFactArtifact,
    *,
    selection_reason: ContextFactSelectionReason,
) -> ConsideredContextFact:
    return ConsideredContextFact(
        fact_id=artifact.fact_id,
        scope=artifact.scope,
        source_stage=artifact.source_stage,
        title=artifact.title,
        summary=artifact.summary,
        tags=artifact.tags,
        evidence_refs=artifact.evidence_refs,
        selection_reason=selection_reason,
    )


def _build_injected_context_fact(
    artifact: ContextFactArtifact,
    *,
    selection_reason: ContextFactSelectionReason,
    remaining_characters: int,
) -> InjectedContextFact | None:
    if remaining_characters <= 0:
        return None
    excerpt, truncated = _trim_context_fact_statement(artifact.statement, remaining_characters)
    if excerpt is None:
        return None
    return InjectedContextFact(
        fact_id=artifact.fact_id,
        scope=artifact.scope,
        source_stage=artifact.source_stage,
        title=artifact.title,
        summary=artifact.summary,
        statement_excerpt=excerpt,
        tags=artifact.tags,
        evidence_refs=artifact.evidence_refs,
        selection_reason=selection_reason,
        original_characters=len(artifact.statement),
        injected_characters=len(excerpt),
        truncated=truncated,
    )


def _trim_context_fact_statement(statement: str, budget: int) -> tuple[str | None, bool]:
    text = statement.strip()
    if not text or budget <= 0:
        return None, False
    if len(text) <= budget:
        return text, False

    suffix = "\n\n[fact truncated to fit governed context budget]"
    if budget <= len(suffix):
        return None, True

    trimmed = text[: budget - len(suffix)].rstrip()
    if "\n" in trimmed:
        trimmed = trimmed.rsplit("\n", 1)[0].rstrip()
    if not trimmed:
        trimmed = text[: budget - len(suffix)].rstrip()
    return f"{trimmed}{suffix}", True


def _match_tokens(text: str) -> frozenset[str]:
    return frozenset(token.lower() for token in _TOKEN_RE.findall(text))
