"""Derived index and relationship summaries over governed compounding stores."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..context_facts import discover_context_facts
from ..contract_compounding import (
    CompoundingKnowledgeFamily,
    CompoundingKnowledgeIndexArtifact,
    CompoundingKnowledgeIndexEntry,
    CompoundingRelationshipCluster,
    CompoundingRelationshipKind,
    CompoundingRelationshipSummaryArtifact,
)
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .harness import (
    discover_harness_benchmark_results,
    discover_harness_candidates,
    discover_harness_recommendations,
)
from .lifecycle import discover_governed_procedures

INDEX_ARTIFACT_NAME = "governed_store_index.json"
RELATIONSHIP_ARTIFACT_NAME = "relationship_summary.json"
SECONDARY_SURFACE_NOTE = (
    "Derived orientation surface only; governed compounding artifacts remain the source of truth."
)
_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9._:-]+")
_IDENTIFIER_TOKEN_RE = re.compile(r"[^A-Za-z0-9._:-]+")
_FAMILY_SORT_ORDER = {
    CompoundingKnowledgeFamily.PROCEDURE: 0,
    CompoundingKnowledgeFamily.CONTEXT_FACT: 1,
    CompoundingKnowledgeFamily.HARNESS_CANDIDATE: 2,
    CompoundingKnowledgeFamily.HARNESS_BENCHMARK: 3,
    CompoundingKnowledgeFamily.HARNESS_RECOMMENDATION: 4,
}
_CLUSTER_SORT_ORDER = {
    CompoundingRelationshipKind.SOURCE_RUN: 0,
    CompoundingRelationshipKind.TAG: 1,
    CompoundingRelationshipKind.EVIDENCE_REF: 2,
    CompoundingRelationshipKind.BENCHMARK_CANDIDATE: 3,
    CompoundingRelationshipKind.RECOMMENDATION_BUNDLE: 4,
}


@dataclass(frozen=True, slots=True)
class CompoundingOrientationSnapshot:
    """Generated orientation artifacts plus query-filtered results."""

    index_path: Path
    index_artifact: CompoundingKnowledgeIndexArtifact
    relationship_summary_path: Path
    relationship_summary_artifact: CompoundingRelationshipSummaryArtifact
    entries: tuple[CompoundingKnowledgeIndexEntry, ...]
    relationship_clusters: tuple[CompoundingRelationshipCluster, ...]


def build_compounding_orientation_snapshot(
    paths: RuntimePaths,
    *,
    query: str | None = None,
) -> CompoundingOrientationSnapshot:
    """Generate, persist, and optionally query the secondary compounding orientation artifacts."""

    index_artifact, relationship_artifact = generate_compounding_orientation_artifacts(paths)
    index_path, relationship_path = persist_compounding_orientation_artifacts(
        paths,
        index_artifact=index_artifact,
        relationship_artifact=relationship_artifact,
    )

    normalized_query = _normalize_query(query)
    entry_lookup = {entry.entry_id: entry for entry in index_artifact.entries}
    filtered_entries = _filter_entries(index_artifact.entries, query=normalized_query)
    filtered_clusters = _filter_clusters(
        relationship_artifact.clusters,
        query=normalized_query,
        entry_lookup=entry_lookup,
    )
    return CompoundingOrientationSnapshot(
        index_path=index_path,
        index_artifact=index_artifact,
        relationship_summary_path=relationship_path,
        relationship_summary_artifact=relationship_artifact,
        entries=filtered_entries,
        relationship_clusters=filtered_clusters,
    )


def generate_compounding_orientation_artifacts(
    paths: RuntimePaths,
    *,
    generated_at: datetime | None = None,
) -> tuple[CompoundingKnowledgeIndexArtifact, CompoundingRelationshipSummaryArtifact]:
    """Build the secondary orientation artifacts in memory without writing them."""

    moment = generated_at or datetime.now(timezone.utc)
    entries = _build_index_entries(paths)
    clusters = _build_relationship_clusters(entries)
    index_artifact = CompoundingKnowledgeIndexArtifact(
        generated_at=moment,
        secondary_surface_note=SECONDARY_SURFACE_NOTE,
        source_families=tuple(family.value for family in CompoundingKnowledgeFamily),
        family_counts=_count_map(entry.family.value for entry in entries),
        entries=entries,
    )
    relationship_artifact = CompoundingRelationshipSummaryArtifact(
        generated_at=moment,
        secondary_surface_note=SECONDARY_SURFACE_NOTE,
        index_artifact_ref=_relative_ref(paths.root, paths.compounding_indexes_dir / INDEX_ARTIFACT_NAME),
        cluster_counts=_count_map(cluster.kind.value for cluster in clusters),
        clusters=clusters,
    )
    return index_artifact, relationship_artifact


def persist_compounding_orientation_artifacts(
    paths: RuntimePaths,
    *,
    index_artifact: CompoundingKnowledgeIndexArtifact,
    relationship_artifact: CompoundingRelationshipSummaryArtifact,
) -> tuple[Path, Path]:
    """Persist generated compounding orientation artifacts to the runtime-owned workspace."""

    index_dir = paths.compounding_indexes_dir
    index_dir.mkdir(parents=True, exist_ok=True)
    index_path = index_dir / INDEX_ARTIFACT_NAME
    relationship_path = index_dir / RELATIONSHIP_ARTIFACT_NAME
    write_text_atomic(index_path, index_artifact.model_dump_json(indent=2) + "\n")
    write_text_atomic(relationship_path, relationship_artifact.model_dump_json(indent=2) + "\n")
    return index_path, relationship_path


def load_compounding_orientation_artifacts(
    paths: RuntimePaths,
) -> tuple[CompoundingKnowledgeIndexArtifact, CompoundingRelationshipSummaryArtifact] | None:
    """Load the stored orientation artifacts without regenerating them."""

    index_path = paths.compounding_indexes_dir / INDEX_ARTIFACT_NAME
    relationship_path = paths.compounding_indexes_dir / RELATIONSHIP_ARTIFACT_NAME
    index_exists = index_path.exists()
    relationship_exists = relationship_path.exists()
    if not index_exists and not relationship_exists:
        return None
    if not index_exists or not relationship_exists:
        missing = RELATIONSHIP_ARTIFACT_NAME if index_exists else INDEX_ARTIFACT_NAME
        raise ValueError(f"stored compounding orientation artifacts are incomplete: missing {missing}")
    index_artifact = CompoundingKnowledgeIndexArtifact.model_validate_json(index_path.read_text(encoding="utf-8"))
    relationship_artifact = CompoundingRelationshipSummaryArtifact.model_validate_json(
        relationship_path.read_text(encoding="utf-8")
    )
    return index_artifact, relationship_artifact


def _build_index_entries(paths: RuntimePaths) -> tuple[CompoundingKnowledgeIndexEntry, ...]:
    entries: list[CompoundingKnowledgeIndexEntry] = []

    for procedure in discover_governed_procedures(paths):
        related_ids = tuple(
            item
            for item in (
                procedure.artifact.supersedes_procedure_id,
                procedure.latest_record.record.replacement_procedure_id
                if procedure.latest_record is not None
                else None,
            )
            if item is not None
        )
        entries.append(
            CompoundingKnowledgeIndexEntry(
                entry_id=procedure.artifact.procedure_id,
                family=CompoundingKnowledgeFamily.PROCEDURE,
                status=procedure.retrieval_status,
                label=procedure.artifact.title,
                summary=procedure.artifact.summary,
                artifact_ref=_relative_ref(paths.root, procedure.artifact_path),
                source_run_id=procedure.artifact.source_run_id,
                source_stage=procedure.artifact.source_stage,
                tags=procedure.artifact.tags,
                evidence_refs=procedure.artifact.evidence_refs,
                related_ids=related_ids,
            )
        )

    for fact in discover_context_facts(paths, include_run_candidates=True):
        entries.append(
            CompoundingKnowledgeIndexEntry(
                entry_id=fact.artifact.fact_id,
                family=CompoundingKnowledgeFamily.CONTEXT_FACT,
                status=fact.retrieval_status,
                label=fact.artifact.title,
                summary=fact.artifact.summary,
                artifact_ref=_relative_ref(paths.root, fact.path),
                source_run_id=fact.artifact.source_run_id,
                source_stage=fact.artifact.source_stage,
                tags=fact.artifact.tags,
                evidence_refs=fact.artifact.evidence_refs,
                related_ids=tuple(
                    item for item in (fact.artifact.supersedes_fact_id,) if item is not None
                ),
            )
        )

    for candidate in discover_harness_candidates(paths):
        changed_surfaces = ", ".join(
            f"{surface.kind.value}:{surface.target}"
            for surface in candidate.candidate.changed_surfaces
        )
        entries.append(
            CompoundingKnowledgeIndexEntry(
                entry_id=candidate.candidate.candidate_id,
                family=CompoundingKnowledgeFamily.HARNESS_CANDIDATE,
                status=candidate.candidate.state.value,
                label=candidate.candidate.name,
                summary=candidate.candidate.reviewer_note or changed_surfaces or "Governed harness candidate.",
                artifact_ref=_relative_ref(paths.root, candidate.path),
            )
        )

    for benchmark in discover_harness_benchmark_results(paths):
        entries.append(
            CompoundingKnowledgeIndexEntry(
                entry_id=benchmark.result.result_id,
                family=CompoundingKnowledgeFamily.HARNESS_BENCHMARK,
                status=benchmark.result.status.value,
                label=benchmark.result.result_id,
                summary=benchmark.result.outcome_summary.message,
                artifact_ref=_relative_ref(paths.root, benchmark.path),
                tags=benchmark.result.outcome_summary.changed_config_fields,
                evidence_refs=benchmark.result.artifact_refs,
                related_ids=(benchmark.result.candidate_id,),
            )
        )

    for recommendation in discover_harness_recommendations(paths):
        related_ids = list(recommendation.recommendation.candidate_ids)
        related_ids.extend(recommendation.recommendation.benchmark_result_ids)
        if recommendation.recommendation.recommended_candidate_id is not None:
            related_ids.append(recommendation.recommendation.recommended_candidate_id)
        if recommendation.recommendation.recommended_result_id is not None:
            related_ids.append(recommendation.recommendation.recommended_result_id)
        entries.append(
            CompoundingKnowledgeIndexEntry(
                entry_id=recommendation.recommendation.recommendation_id,
                family=CompoundingKnowledgeFamily.HARNESS_RECOMMENDATION,
                status=recommendation.recommendation.disposition.value,
                label=recommendation.recommendation.recommendation_id,
                summary=recommendation.recommendation.summary,
                artifact_ref=_relative_ref(paths.root, recommendation.path),
                related_ids=tuple(related_ids),
            )
        )

    entries.sort(key=_entry_sort_key)
    return tuple(entries)


def _build_relationship_clusters(
    entries: tuple[CompoundingKnowledgeIndexEntry, ...],
) -> tuple[CompoundingRelationshipCluster, ...]:
    clusters: list[CompoundingRelationshipCluster] = []
    entry_lookup = {entry.entry_id: entry for entry in entries}

    source_run_groups: dict[str, list[CompoundingKnowledgeIndexEntry]] = defaultdict(list)
    evidence_groups: dict[str, list[CompoundingKnowledgeIndexEntry]] = defaultdict(list)
    tag_groups: dict[str, list[CompoundingKnowledgeIndexEntry]] = defaultdict(list)

    for entry in entries:
        if entry.source_run_id is not None:
            source_run_groups[entry.source_run_id].append(entry)
        for evidence_ref in entry.evidence_refs:
            evidence_groups[evidence_ref].append(entry)
        for tag in entry.tags:
            tag_groups[tag].append(entry)

    for run_id, members in sorted(source_run_groups.items()):
        if len(members) < 2:
            continue
        member_ids = _sorted_member_ids(members)
        clusters.append(
            CompoundingRelationshipCluster(
                cluster_id=_cluster_id("source_run", run_id),
                kind=CompoundingRelationshipKind.SOURCE_RUN,
                label=f"Source run: {run_id}",
                summary=_member_summary(members, prefix=f"Derived from source run {run_id}."),
                member_ids=member_ids,
                shared_terms=(run_id,),
            )
        )

    for evidence_ref, members in sorted(evidence_groups.items()):
        if len(members) < 2:
            continue
        member_ids = _sorted_member_ids(members)
        clusters.append(
            CompoundingRelationshipCluster(
                cluster_id=_cluster_id("evidence_ref", evidence_ref),
                kind=CompoundingRelationshipKind.EVIDENCE_REF,
                label=f"Evidence ref: {evidence_ref}",
                summary=_member_summary(members, prefix="Shared evidence reference."),
                member_ids=member_ids,
                shared_terms=(evidence_ref,),
            )
        )

    for tag, members in sorted(tag_groups.items()):
        if len(members) < 2:
            continue
        member_ids = _sorted_member_ids(members)
        clusters.append(
            CompoundingRelationshipCluster(
                cluster_id=_cluster_id("tag", tag),
                kind=CompoundingRelationshipKind.TAG,
                label=f"Tag: {tag}",
                summary=_member_summary(members, prefix="Shared governed tag."),
                member_ids=member_ids,
                shared_terms=(tag,),
            )
        )

    for entry in entries:
        if entry.family is CompoundingKnowledgeFamily.HARNESS_BENCHMARK and entry.related_ids:
            member_ids = tuple(
                item for item in (entry.entry_id, *entry.related_ids) if item in entry_lookup
            )
            if len(member_ids) < 2:
                continue
            clusters.append(
                CompoundingRelationshipCluster(
                    cluster_id=_cluster_id("benchmark_candidate", entry.entry_id),
                    kind=CompoundingRelationshipKind.BENCHMARK_CANDIDATE,
                    label=f"Benchmark link: {entry.entry_id}",
                    summary=f"Benchmark {entry.entry_id} compares candidate {entry.related_ids[0]}.",
                    member_ids=member_ids,
                    shared_terms=(entry.related_ids[0],),
                )
            )
        if entry.family is CompoundingKnowledgeFamily.HARNESS_RECOMMENDATION and entry.related_ids:
            member_ids = tuple(
                item
                for item in (entry.entry_id, *entry.related_ids)
                if item in entry_lookup
            )
            if len(member_ids) < 2:
                continue
            clusters.append(
                CompoundingRelationshipCluster(
                    cluster_id=_cluster_id("recommendation_bundle", entry.entry_id),
                    kind=CompoundingRelationshipKind.RECOMMENDATION_BUNDLE,
                    label=f"Recommendation bundle: {entry.entry_id}",
                    summary=f"Recommendation {entry.entry_id} links governed candidates and benchmark evidence.",
                    member_ids=member_ids,
                    shared_terms=tuple(item for item in entry.related_ids if item in entry_lookup),
                )
            )

    clusters.sort(key=_cluster_sort_key)
    return tuple(clusters)


def _filter_entries(
    entries: tuple[CompoundingKnowledgeIndexEntry, ...],
    *,
    query: str | None,
) -> tuple[CompoundingKnowledgeIndexEntry, ...]:
    if query is None:
        return entries
    tokens = _query_tokens(query)
    if not tokens:
        return entries
    return tuple(entry for entry in entries if _matches_tokens(_entry_haystack(entry), tokens))


def _filter_clusters(
    clusters: tuple[CompoundingRelationshipCluster, ...],
    *,
    query: str | None,
    entry_lookup: dict[str, CompoundingKnowledgeIndexEntry],
) -> tuple[CompoundingRelationshipCluster, ...]:
    if query is None:
        return clusters
    tokens = _query_tokens(query)
    if not tokens:
        return clusters
    return tuple(
        cluster
        for cluster in clusters
        if _matches_tokens(_cluster_haystack(cluster, entry_lookup=entry_lookup), tokens)
    )


def _normalize_query(query: str | None) -> str | None:
    if query is None:
        return None
    normalized = " ".join(query.strip().split())
    return normalized or None


def _query_tokens(query: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _QUERY_TOKEN_RE.findall(query))


def _matches_tokens(haystack: str, tokens: tuple[str, ...]) -> bool:
    lowered = haystack.lower()
    return all(token in lowered for token in tokens)


def _entry_haystack(entry: CompoundingKnowledgeIndexEntry) -> str:
    parts = [
        entry.family.value,
        entry.status,
        entry.entry_id,
        entry.label,
        entry.summary,
        entry.artifact_ref,
        *(entry.tags or ()),
        *(entry.evidence_refs or ()),
        *(entry.related_ids or ()),
    ]
    if entry.source_run_id is not None:
        parts.append(entry.source_run_id)
    if entry.source_stage is not None:
        parts.append(entry.source_stage.value)
    return " ".join(parts)


def _cluster_haystack(
    cluster: CompoundingRelationshipCluster,
    *,
    entry_lookup: dict[str, CompoundingKnowledgeIndexEntry],
) -> str:
    parts = [cluster.kind.value, cluster.cluster_id, cluster.label, cluster.summary, *cluster.shared_terms, *cluster.member_ids]
    for member_id in cluster.member_ids:
        entry = entry_lookup.get(member_id)
        if entry is None:
            continue
        parts.extend((entry.label, entry.summary, *entry.tags, *entry.evidence_refs))
    return " ".join(parts)


def _relative_ref(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _count_map(values: list[str] | tuple[str, ...] | Counter[str] | object) -> dict[str, int]:
    counts = Counter(values)
    return {key: counts[key] for key in sorted(counts)}


def _entry_sort_key(entry: CompoundingKnowledgeIndexEntry) -> tuple[int, str, str, str]:
    return (
        _FAMILY_SORT_ORDER[entry.family],
        entry.status,
        entry.label.lower(),
        entry.entry_id,
    )


def _cluster_sort_key(cluster: CompoundingRelationshipCluster) -> tuple[int, str]:
    return (_CLUSTER_SORT_ORDER[cluster.kind], cluster.cluster_id)


def _sorted_member_ids(members: list[CompoundingKnowledgeIndexEntry]) -> tuple[str, ...]:
    return tuple(entry.entry_id for entry in sorted(members, key=_entry_sort_key))


def _member_summary(
    members: list[CompoundingKnowledgeIndexEntry],
    *,
    prefix: str,
) -> str:
    family_counts = Counter(entry.family.value for entry in members)
    counts_text = ", ".join(f"{family_counts[key]} {key}" for key in sorted(family_counts))
    return f"{prefix} Members: {counts_text}."


def _cluster_id(prefix: str, value: str) -> str:
    token = _IDENTIFIER_TOKEN_RE.sub("-", value.strip()).strip("-") or "cluster"
    return f"{prefix}.{token}"
