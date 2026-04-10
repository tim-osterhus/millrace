from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_engine.paths import RuntimePaths
from millrace_engine.research.goalspec import (
    ContractorExecutionRecord,
    ContractorProfileArtifact,
    GoalSpecExecutionError,
)
from millrace_engine.research.goalspec_persistence import (
    contractor_record_path,
    load_contractor_execution_record,
    load_contractor_profile,
)
from millrace_engine.research.persistence_helpers import _write_json_model


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _runtime_paths(tmp_path: Path) -> RuntimePaths:
    workspace = tmp_path / "workspace"
    return RuntimePaths.from_workspace(workspace, workspace / "agents")


def _example_payload(paths: RuntimePaths) -> dict[str, object]:
    example_path = paths.packaged_contractor_profile_schema_file.with_name("contractor_profile.example.json")
    return json.loads(example_path.read_text(encoding="utf-8"))


def test_contractor_profile_artifact_validates_packaged_example_payload(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)

    profile = ContractorProfileArtifact.model_validate(_example_payload(paths))

    assert profile.artifact_type == "contractor_profile"
    assert profile.shape_class == "platform_extension"
    assert profile.classification.shape_class == profile.shape_class
    assert profile.evidence


def test_contractor_profile_artifact_backfills_lineage_fields(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    payload = _example_payload(paths)
    payload.pop("canonical_source_path")
    payload.pop("current_artifact_path")

    profile = ContractorProfileArtifact.model_validate(payload)

    assert profile.canonical_source_path == profile.source_path
    assert profile.current_artifact_path == profile.source_path


def test_load_contractor_profile_reads_validated_profile_from_canonical_path(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    profile = ContractorProfileArtifact.model_validate(_example_payload(paths))

    _write_json_model(paths.contractor_profile_file, profile, create_parent=True)

    loaded = load_contractor_profile(paths)

    assert loaded == profile


def test_load_contractor_profile_fails_when_profile_missing(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)

    with pytest.raises(GoalSpecExecutionError, match="Contractor profile is missing"):
        load_contractor_profile(paths)


def test_contractor_execution_record_round_trips_from_runtime_record_dir(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    record_path = contractor_record_path(paths, run_id="goalspec-contractor-001")
    record = ContractorExecutionRecord(
        run_id="goalspec-contractor-001",
        emitted_at=_dt("2026-04-10T19:10:00Z"),
        goal_id="IDEA-AURA-001",
        title="Aura Mod",
        canonical_source_path="agents/ideas/archive/raw/aura_goal.md",
        current_artifact_path="agents/ideas/staging/aura_goal.md",
        source_path="agents/ideas/archive/raw/aura_goal.md",
        research_brief_path="agents/ideas/staging/aura_goal.md",
        profile_path="agents/objective/contractor_profile.json",
        report_path="agents/reports/contractor_profile.md",
        schema_path="millrace_engine/assets/agents/objective/contractor_profile.schema.json",
        record_path="agents/.research_runtime/goalspec/contractor/goalspec-contractor-001.json",
        profile_specificity_level="L4",
        shape_class="platform_extension",
        fallback_mode="apply_resolved_profiles_only",
        browse_used=False,
    )

    _write_json_model(record_path, record, create_parent=True)

    loaded = load_contractor_execution_record(paths, run_id="goalspec-contractor-001")

    assert record_path == paths.goalspec_contractor_records_dir / "goalspec-contractor-001.json"
    assert loaded == record


def test_load_contractor_execution_record_fails_when_record_missing(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)

    with pytest.raises(GoalSpecExecutionError, match="Contractor execution record is missing"):
        load_contractor_execution_record(paths, run_id="goalspec-contractor-404")
