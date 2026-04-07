"""Storage and discovery helpers for durable context facts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import re

from .contract_context_facts import ContextFactArtifact, ContextFactLifecycleState, ContextFactScope
from .markdown import write_text_atomic
from .paths import RuntimePaths


ContextFactRetrievalStatus = Literal["eligible", "stale", "deprecated", "run_candidate"]
_FILENAME_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class StoredContextFact:
    """One persisted context fact plus its discovery metadata."""

    path: Path
    artifact: ContextFactArtifact
    retrieval_status: ContextFactRetrievalStatus
    eligible_for_retrieval: bool


def persist_context_fact(paths: RuntimePaths, artifact: ContextFactArtifact) -> Path:
    """Persist one context-fact artifact into its fact-specific storage family."""

    path = _artifact_path(paths, artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, artifact.model_dump_json(indent=2) + "\n")
    return path


def discover_context_facts(
    paths: RuntimePaths,
    *,
    include_run_candidates: bool = True,
) -> tuple[StoredContextFact, ...]:
    """Return discovered durable facts from the dedicated fact store."""

    facts: list[StoredContextFact] = []
    facts.extend(_stored_context_fact(path, artifact) for path, artifact in _load_workspace_artifacts(paths))
    if include_run_candidates:
        facts.extend(_stored_context_fact(path, artifact) for path, artifact in _load_run_scoped_artifacts(paths))
    facts.sort(key=_fact_sort_key)
    return tuple(facts)


def context_fact_for_id(
    paths: RuntimePaths,
    fact_id: str,
    *,
    include_run_candidates: bool = True,
) -> StoredContextFact:
    """Resolve one stored context fact by id."""

    normalized_fact_id = fact_id.strip()
    if not normalized_fact_id:
        raise ValueError("fact_id may not be empty")
    matches = [
        fact
        for fact in discover_context_facts(paths, include_run_candidates=include_run_candidates)
        if fact.artifact.fact_id == normalized_fact_id
    ]
    if not matches:
        raise ValueError(f"context fact not found: {normalized_fact_id}")
    if len(matches) > 1:
        raise ValueError(f"fact_id is ambiguous across multiple artifacts: {normalized_fact_id}")
    return matches[0]


def load_retrievable_context_facts(paths: RuntimePaths) -> tuple[ContextFactArtifact, ...]:
    """Return workspace facts eligible for broader-scope retrieval."""

    return tuple(
        fact.artifact
        for fact in discover_context_facts(paths, include_run_candidates=False)
        if fact.eligible_for_retrieval
    )


def _stored_context_fact(path: Path, artifact: ContextFactArtifact) -> StoredContextFact:
    retrieval_status = _retrieval_status_for(artifact)
    return StoredContextFact(
        path=path,
        artifact=artifact,
        retrieval_status=retrieval_status,
        eligible_for_retrieval=retrieval_status == "eligible",
    )


def _retrieval_status_for(artifact: ContextFactArtifact) -> ContextFactRetrievalStatus:
    if artifact.scope is ContextFactScope.RUN:
        return "run_candidate"
    if artifact.lifecycle_state is ContextFactLifecycleState.PROMOTED:
        return "eligible"
    if artifact.lifecycle_state is ContextFactLifecycleState.DEPRECATED:
        return "deprecated"
    return "stale"


def _load_workspace_artifacts(paths: RuntimePaths) -> tuple[tuple[Path, ContextFactArtifact], ...]:
    directory = paths.compounding_context_facts_dir
    if not directory.exists():
        return ()
    artifacts: list[tuple[Path, ContextFactArtifact]] = []
    for path in sorted(directory.glob("*.json")):
        artifact = ContextFactArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        artifacts.append((path, artifact))
    return tuple(artifacts)


def _load_run_scoped_artifacts(paths: RuntimePaths) -> tuple[tuple[Path, ContextFactArtifact], ...]:
    directory = paths.compounding_context_facts_dir
    if not directory.exists():
        return ()
    artifacts: list[tuple[Path, ContextFactArtifact]] = []
    for run_dir in sorted(path for path in directory.iterdir() if path.is_dir()):
        for path in sorted(run_dir.glob("*.json")):
            artifact = ContextFactArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            artifacts.append((path, artifact))
    return tuple(artifacts)


def _artifact_path(paths: RuntimePaths, artifact: ContextFactArtifact) -> Path:
    filename = f"{_filename_token(artifact.fact_id)}.json"
    if artifact.scope is ContextFactScope.RUN:
        return paths.compounding_context_facts_dir / artifact.source_run_id / filename
    return paths.compounding_context_facts_dir / filename


def _filename_token(value: str) -> str:
    return _FILENAME_TOKEN_RE.sub("-", value.strip()).strip("-") or "context-fact"


def _fact_sort_key(item: StoredContextFact) -> tuple[int, int, str, str]:
    scope_priority = 0 if item.artifact.scope is ContextFactScope.WORKSPACE else 1
    status_priority_map = {"eligible": 0, "stale": 1, "deprecated": 2, "run_candidate": 3}
    status_priority = status_priority_map[item.retrieval_status]
    return (scope_priority, status_priority, item.artifact.title.lower(), item.artifact.fact_id)
