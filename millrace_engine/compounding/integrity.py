"""Integrity linting over governed compounding stores and derived orientation artifacts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator

from ..context_facts import discover_context_facts
from ..contract_core import ContractModel, _normalize_datetime, _normalize_sequence
from ..paths import RuntimePaths
from .harness import (
    discover_harness_benchmark_results,
    discover_harness_candidates,
    discover_harness_recommendations,
    discover_harness_search_requests,
)
from .lifecycle import discover_governed_procedures, discover_lifecycle_records
from .orientation import (
    generate_compounding_orientation_artifacts,
    load_compounding_orientation_artifacts,
)


class CompoundingIntegrityStatus(str, Enum):
    """Overall integrity severity for the governed compounding stores."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class CompoundingIntegrityFamily(str, Enum):
    """Artifact family addressed by one integrity issue."""

    PROCEDURE = "procedure"
    CONTEXT_FACT = "context_fact"
    LIFECYCLE_RECORD = "lifecycle_record"
    HARNESS_BENCHMARK = "harness_benchmark"
    HARNESS_RECOMMENDATION = "harness_recommendation"
    HARNESS_SEARCH_REQUEST = "harness_search_request"
    ORIENTATION_INDEX = "orientation_index"
    ORIENTATION_RELATIONSHIP = "orientation_relationship"


class CompoundingIntegrityIssue(ContractModel):
    """One explicit lint finding over governed compounding stores."""

    issue_id: str
    severity: CompoundingIntegrityStatus
    family: CompoundingIntegrityFamily
    message: str
    artifact_ref: str | None = None
    related_refs: tuple[str, ...] = ()

    @field_validator("issue_id", "message", "artifact_ref")
    @classmethod
    def normalize_text(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{getattr(info, 'field_name', 'value')} may not be empty")
        return normalized

    @field_validator("related_refs", mode="before")
    @classmethod
    def normalize_related_refs(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_sequence([str(item) for item in value])


class CompoundingIntegrityReport(ContractModel):
    """Typed lint report over primary governed artifacts and stored orientation artifacts."""

    generated_at: datetime
    status: CompoundingIntegrityStatus
    summary: str
    checked_counts: dict[str, int] = Field(default_factory=dict)
    issue_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    orientation_index_present: bool = False
    relationship_summary_present: bool = False
    issues: tuple[CompoundingIntegrityIssue, ...] = ()

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_generated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("summary may not be empty")
        return normalized

    @field_validator("checked_counts", mode="before")
    @classmethod
    def normalize_checked_counts(cls, value: dict[str, int] | None) -> dict[str, int]:
        if not value:
            return {}
        normalized: dict[str, int] = {}
        for key in sorted(value):
            count = int(value[key])
            if count < 0:
                raise ValueError("checked_counts values must be non-negative")
            normalized[str(key)] = count
        return normalized

    @field_validator("issues", mode="before")
    @classmethod
    def normalize_issues(
        cls,
        value: tuple[CompoundingIntegrityIssue, ...]
        | list[CompoundingIntegrityIssue]
        | tuple[dict[str, Any], ...]
        | list[dict[str, Any]]
        | None,
    ) -> tuple[CompoundingIntegrityIssue, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, CompoundingIntegrityIssue) else CompoundingIntegrityIssue.model_validate(item)
            for item in value
        )


def build_compounding_integrity_report(paths: RuntimePaths) -> CompoundingIntegrityReport:
    """Lint the governed compounding stores and the stored orientation artifacts."""

    procedures = discover_governed_procedures(paths)
    lifecycle_records = discover_lifecycle_records(paths)
    facts = discover_context_facts(paths, include_run_candidates=True)
    candidates = discover_harness_candidates(paths)
    benchmarks = discover_harness_benchmark_results(paths)
    recommendations = discover_harness_recommendations(paths)
    search_requests = discover_harness_search_requests(paths)

    procedure_ids = {item.artifact.procedure_id for item in procedures}
    workspace_procedure_ids = {
        item.artifact.procedure_id
        for item in procedures
        if item.artifact.scope.value == "workspace"
    }
    fact_ids = {item.artifact.fact_id for item in facts}
    candidate_ids = {item.candidate.candidate_id for item in candidates}
    benchmark_ids = {item.result.result_id for item in benchmarks}
    search_ids = {item.request.search_id for item in search_requests}
    issues: list[CompoundingIntegrityIssue] = []

    for procedure in procedures:
        artifact = procedure.artifact
        if artifact.scope.value == "workspace" and procedure.retrieval_status == "stale":
            issues.append(
                _issue(
                    issue_id=f"procedure.stale.{artifact.procedure_id}",
                    severity=CompoundingIntegrityStatus.WARN,
                    family=CompoundingIntegrityFamily.PROCEDURE,
                    message=f"workspace procedure {artifact.procedure_id} is stale and withheld from retrieval",
                    artifact_ref=_relative_ref(paths.root, procedure.artifact_path),
                )
            )
        if artifact.supersedes_procedure_id is not None and artifact.supersedes_procedure_id not in procedure_ids:
            issues.append(
                _issue(
                    issue_id=f"procedure.supersedes_missing.{artifact.procedure_id}",
                    severity=CompoundingIntegrityStatus.FAIL,
                    family=CompoundingIntegrityFamily.PROCEDURE,
                    message=(
                        f"procedure {artifact.procedure_id} references missing superseded procedure "
                        f"{artifact.supersedes_procedure_id}"
                    ),
                    artifact_ref=_relative_ref(paths.root, procedure.artifact_path),
                    related_refs=(artifact.supersedes_procedure_id,),
                )
            )

    for record in lifecycle_records:
        if record.record.procedure_id not in workspace_procedure_ids:
            issues.append(
                _issue(
                    issue_id=f"lifecycle.orphaned.{record.record.record_id}",
                    severity=CompoundingIntegrityStatus.FAIL,
                    family=CompoundingIntegrityFamily.LIFECYCLE_RECORD,
                    message=(
                        f"lifecycle record {record.record.record_id} points at missing workspace procedure "
                        f"{record.record.procedure_id}"
                    ),
                    artifact_ref=_relative_ref(paths.root, record.path),
                    related_refs=(record.record.procedure_id,),
                )
            )
        if (
            record.record.replacement_procedure_id is not None
            and record.record.replacement_procedure_id not in workspace_procedure_ids
        ):
            issues.append(
                _issue(
                    issue_id=f"lifecycle.replacement_missing.{record.record.record_id}",
                    severity=CompoundingIntegrityStatus.FAIL,
                    family=CompoundingIntegrityFamily.LIFECYCLE_RECORD,
                    message=(
                        f"lifecycle record {record.record.record_id} references missing replacement procedure "
                        f"{record.record.replacement_procedure_id}"
                    ),
                    artifact_ref=_relative_ref(paths.root, record.path),
                    related_refs=(record.record.replacement_procedure_id,),
                )
            )

    for fact in facts:
        artifact = fact.artifact
        if artifact.scope.value == "workspace" and fact.retrieval_status == "stale":
            issues.append(
                _issue(
                    issue_id=f"context_fact.stale.{artifact.fact_id}",
                    severity=CompoundingIntegrityStatus.WARN,
                    family=CompoundingIntegrityFamily.CONTEXT_FACT,
                    message=f"context fact {artifact.fact_id} is stale and withheld from retrieval",
                    artifact_ref=_relative_ref(paths.root, fact.path),
                )
            )
        if artifact.supersedes_fact_id is not None and artifact.supersedes_fact_id not in fact_ids:
            issues.append(
                _issue(
                    issue_id=f"context_fact.supersedes_missing.{artifact.fact_id}",
                    severity=CompoundingIntegrityStatus.FAIL,
                    family=CompoundingIntegrityFamily.CONTEXT_FACT,
                    message=(
                        f"context fact {artifact.fact_id} references missing superseded fact "
                        f"{artifact.supersedes_fact_id}"
                    ),
                    artifact_ref=_relative_ref(paths.root, fact.path),
                    related_refs=(artifact.supersedes_fact_id,),
                )
            )

    for benchmark in benchmarks:
        if benchmark.result.candidate_id not in candidate_ids:
            issues.append(
                _issue(
                    issue_id=f"harness_benchmark.missing_candidate.{benchmark.result.result_id}",
                    severity=CompoundingIntegrityStatus.FAIL,
                    family=CompoundingIntegrityFamily.HARNESS_BENCHMARK,
                    message=(
                        f"benchmark {benchmark.result.result_id} references missing candidate "
                        f"{benchmark.result.candidate_id}"
                    ),
                    artifact_ref=_relative_ref(paths.root, benchmark.path),
                    related_refs=(benchmark.result.candidate_id,),
                )
            )
        for artifact_ref in benchmark.result.artifact_refs:
            if not _artifact_ref_path(paths.root, artifact_ref).exists():
                issues.append(
                    _issue(
                        issue_id=f"harness_benchmark.missing_artifact.{benchmark.result.result_id}.{_token(artifact_ref)}",
                        severity=CompoundingIntegrityStatus.FAIL,
                        family=CompoundingIntegrityFamily.HARNESS_BENCHMARK,
                        message=f"benchmark {benchmark.result.result_id} references missing artifact {artifact_ref}",
                        artifact_ref=_relative_ref(paths.root, benchmark.path),
                        related_refs=(artifact_ref,),
                    )
                )

    for recommendation in recommendations:
        related_candidate_ids = tuple(
            item
            for item in (
                recommendation.recommendation.recommended_candidate_id,
                *recommendation.recommendation.candidate_ids,
            )
            if item is not None
        )
        for candidate_id in related_candidate_ids:
            if candidate_id not in candidate_ids:
                issues.append(
                    _issue(
                        issue_id=f"harness_recommendation.missing_candidate.{recommendation.recommendation.recommendation_id}.{candidate_id}",
                        severity=CompoundingIntegrityStatus.FAIL,
                        family=CompoundingIntegrityFamily.HARNESS_RECOMMENDATION,
                        message=(
                            f"recommendation {recommendation.recommendation.recommendation_id} references missing candidate "
                            f"{candidate_id}"
                        ),
                        artifact_ref=_relative_ref(paths.root, recommendation.path),
                        related_refs=(candidate_id,),
                    )
                )
        related_benchmark_ids = tuple(
            item
            for item in (
                recommendation.recommendation.recommended_result_id,
                *recommendation.recommendation.benchmark_result_ids,
            )
            if item is not None
        )
        for benchmark_id in related_benchmark_ids:
            if benchmark_id not in benchmark_ids:
                issues.append(
                    _issue(
                        issue_id=f"harness_recommendation.missing_benchmark.{recommendation.recommendation.recommendation_id}.{benchmark_id}",
                        severity=CompoundingIntegrityStatus.FAIL,
                        family=CompoundingIntegrityFamily.HARNESS_RECOMMENDATION,
                        message=(
                            f"recommendation {recommendation.recommendation.recommendation_id} references missing benchmark "
                            f"{benchmark_id}"
                        ),
                        artifact_ref=_relative_ref(paths.root, recommendation.path),
                        related_refs=(benchmark_id,),
                    )
                )
        if recommendation.recommendation.search_id not in search_ids:
            issues.append(
                _issue(
                    issue_id=f"harness_recommendation.missing_search.{recommendation.recommendation.recommendation_id}",
                    severity=CompoundingIntegrityStatus.FAIL,
                    family=CompoundingIntegrityFamily.HARNESS_RECOMMENDATION,
                    message=(
                        f"recommendation {recommendation.recommendation.recommendation_id} references missing search "
                        f"{recommendation.recommendation.search_id}"
                    ),
                    artifact_ref=_relative_ref(paths.root, recommendation.path),
                    related_refs=(recommendation.recommendation.search_id,),
                )
            )

    stored_orientation = None
    orientation_index_present = (paths.compounding_indexes_dir / "governed_store_index.json").exists()
    relationship_summary_present = (paths.compounding_indexes_dir / "relationship_summary.json").exists()
    if orientation_index_present or relationship_summary_present:
        try:
            stored_orientation = load_compounding_orientation_artifacts(paths)
        except (ValidationError, ValueError) as exc:
            issues.append(
                _issue(
                    issue_id="orientation_store.load_failed",
                    severity=CompoundingIntegrityStatus.FAIL,
                    family=CompoundingIntegrityFamily.ORIENTATION_INDEX,
                    message=f"stored orientation artifacts could not be loaded: {exc}",
                    artifact_ref=_relative_ref(paths.root, paths.compounding_indexes_dir),
                )
            )
    if stored_orientation is not None:
        stored_index, stored_relationship = stored_orientation
        expected_index, expected_relationship = generate_compounding_orientation_artifacts(
            paths,
            generated_at=stored_index.generated_at,
        )
        for entry in stored_index.entries:
            if not _artifact_ref_path(paths.root, entry.artifact_ref).exists():
                issues.append(
                    _issue(
                        issue_id=f"orientation_index.missing_artifact.{entry.entry_id}",
                        severity=CompoundingIntegrityStatus.FAIL,
                        family=CompoundingIntegrityFamily.ORIENTATION_INDEX,
                        message=f"orientation index entry {entry.entry_id} points at missing artifact {entry.artifact_ref}",
                        artifact_ref=entry.artifact_ref,
                        related_refs=(entry.entry_id,),
                    )
                )
        stored_entry_ids = {entry.entry_id for entry in stored_index.entries}
        for cluster in stored_relationship.clusters:
            missing_members = tuple(member_id for member_id in cluster.member_ids if member_id not in stored_entry_ids)
            if missing_members:
                issues.append(
                    _issue(
                        issue_id=f"orientation_relationship.missing_members.{cluster.cluster_id}",
                        severity=CompoundingIntegrityStatus.FAIL,
                        family=CompoundingIntegrityFamily.ORIENTATION_RELATIONSHIP,
                        message=f"relationship cluster {cluster.cluster_id} references missing index entries",
                        artifact_ref=stored_relationship.index_artifact_ref,
                        related_refs=missing_members,
                    )
                )
        if stored_index.family_counts != expected_index.family_counts or _entry_signatures(stored_index) != _entry_signatures(
            expected_index
        ):
            issues.append(
                _issue(
                    issue_id="orientation_index.out_of_sync",
                    severity=CompoundingIntegrityStatus.FAIL,
                    family=CompoundingIntegrityFamily.ORIENTATION_INDEX,
                    message="stored orientation index is out of sync with the current governed stores",
                    artifact_ref=_relative_ref(paths.root, paths.compounding_indexes_dir / "governed_store_index.json"),
                )
            )
        if (
            stored_relationship.index_artifact_ref != expected_relationship.index_artifact_ref
            or stored_relationship.cluster_counts != expected_relationship.cluster_counts
            or _cluster_signatures(stored_relationship) != _cluster_signatures(expected_relationship)
        ):
            issues.append(
                _issue(
                    issue_id="orientation_relationship.out_of_sync",
                    severity=CompoundingIntegrityStatus.FAIL,
                    family=CompoundingIntegrityFamily.ORIENTATION_RELATIONSHIP,
                    message="stored relationship summary is out of sync with the current governed stores",
                    artifact_ref=_relative_ref(paths.root, paths.compounding_indexes_dir / "relationship_summary.json"),
                )
            )

    issues.sort(key=_issue_sort_key)
    failure_count = sum(1 for issue in issues if issue.severity is CompoundingIntegrityStatus.FAIL)
    warning_count = sum(1 for issue in issues if issue.severity is CompoundingIntegrityStatus.WARN)
    status = _report_status(failure_count=failure_count, warning_count=warning_count)
    return CompoundingIntegrityReport(
        generated_at=datetime.now(timezone.utc),
        status=status,
        summary=_summary(status=status, failure_count=failure_count, warning_count=warning_count, issue_count=len(issues)),
        checked_counts={
            "context_fact": len(facts),
            "harness_benchmark": len(benchmarks),
            "harness_candidate": len(candidates),
            "harness_recommendation": len(recommendations),
            "harness_search_request": len(search_requests),
            "lifecycle_record": len(lifecycle_records),
            "orientation_cluster": len(stored_orientation[1].clusters) if stored_orientation is not None else 0,
            "orientation_entry": len(stored_orientation[0].entries) if stored_orientation is not None else 0,
            "procedure": len(procedures),
        },
        issue_count=len(issues),
        warning_count=warning_count,
        failure_count=failure_count,
        orientation_index_present=orientation_index_present,
        relationship_summary_present=relationship_summary_present,
        issues=tuple(issues),
    )


def _issue(
    *,
    issue_id: str,
    severity: CompoundingIntegrityStatus,
    family: CompoundingIntegrityFamily,
    message: str,
    artifact_ref: str | None = None,
    related_refs: tuple[str, ...] = (),
) -> CompoundingIntegrityIssue:
    return CompoundingIntegrityIssue(
        issue_id=issue_id,
        severity=severity,
        family=family,
        message=message,
        artifact_ref=artifact_ref,
        related_refs=related_refs,
    )


def _artifact_ref_path(root: Path, artifact_ref: str) -> Path:
    path = Path(artifact_ref)
    return path if path.is_absolute() else (root / path).resolve()


def _relative_ref(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _token(value: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in value).strip("-") or "artifact"


def _entry_signatures(index_artifact: object) -> tuple[tuple[object, ...], ...]:
    entries = getattr(index_artifact, "entries")
    signatures = []
    for entry in entries:
        signatures.append(
            (
                entry.entry_id,
                entry.family.value,
                entry.status,
                entry.label,
                entry.summary,
                entry.artifact_ref,
                entry.source_run_id,
                entry.source_stage.value if entry.source_stage is not None else None,
                tuple(entry.tags),
                tuple(entry.evidence_refs),
                tuple(entry.related_ids),
            )
        )
    return tuple(signatures)


def _cluster_signatures(relationship_artifact: object) -> tuple[tuple[object, ...], ...]:
    clusters = getattr(relationship_artifact, "clusters")
    signatures = []
    for cluster in clusters:
        signatures.append(
            (
                cluster.cluster_id,
                cluster.kind.value,
                cluster.label,
                cluster.summary,
                tuple(cluster.member_ids),
                tuple(cluster.shared_terms),
            )
        )
    return tuple(signatures)


def _report_status(*, failure_count: int, warning_count: int) -> CompoundingIntegrityStatus:
    if failure_count > 0:
        return CompoundingIntegrityStatus.FAIL
    if warning_count > 0:
        return CompoundingIntegrityStatus.WARN
    return CompoundingIntegrityStatus.PASS


def _summary(
    *,
    status: CompoundingIntegrityStatus,
    failure_count: int,
    warning_count: int,
    issue_count: int,
) -> str:
    if status is CompoundingIntegrityStatus.PASS:
        return "governed compounding stores passed integrity lint"
    return (
        "governed compounding stores reported integrity findings "
        f"(failures={failure_count}, warnings={warning_count}, issues={issue_count})"
    )


def _issue_sort_key(issue: CompoundingIntegrityIssue) -> tuple[int, str]:
    severity_order = {
        CompoundingIntegrityStatus.FAIL: 0,
        CompoundingIntegrityStatus.WARN: 1,
        CompoundingIntegrityStatus.PASS: 2,
    }
    return (severity_order[issue.severity], issue.issue_id)
