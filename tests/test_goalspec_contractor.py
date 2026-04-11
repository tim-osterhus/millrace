from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_engine.contracts import ResearchStatus
from millrace_engine.paths import RuntimePaths
from millrace_engine.research import goalspec_contractor
from millrace_engine.research.goalspec_contractor import execute_contractor
from millrace_engine.research.state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership, ResearchRuntimeMode

MINECRAFT_MOD_GOAL_TEXT = """---
idea_id: IDEA-AURA-001
title: Aura Progression Mod
---

# Aura Progression Mod

Build a Minecraft mod that adds aura-powered progression, new registrations, and in-game validation.

- Add progression content
- Register new aura items and systems
- Validate gameplay behavior with GameTests

Use Gradle for the project build.
"""

MINECRAFT_FABRIC_GOAL_TEXT = """---
idea_id: IDEA-AURA-LOADER-001
title: Aura Progression Mod
---

# Aura Progression Mod

Build a Minecraft mod that adds aura-powered progression, new registrations, and in-game validation.

- Add progression content
- Register new aura items and systems
- Validate gameplay behavior with GameTests

Use Gradle for the project build.
Loader discussion currently points at Fabric.
"""

AMBIGUOUS_GOAL_TEXT = """---
idea_id: IDEA-MIXED-001
title: Team System
---

# Team System

Build something useful for a team. The exact product shape is still unclear.
"""


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _runtime_paths(tmp_path: Path) -> RuntimePaths:
    workspace = tmp_path / "workspace"
    return RuntimePaths.from_workspace(workspace, workspace / "agents")


def _write_staged_goal(paths: RuntimePaths, body: str) -> Path:
    goal_path = paths.ideas_staging_dir / "goal.md"
    goal_path.parent.mkdir(parents=True, exist_ok=True)
    goal_path.write_text(body, encoding="utf-8")
    return goal_path


def _write_canonical_goal(paths: RuntimePaths, body: str) -> Path:
    goal_path = paths.ideas_archive_dir / "raw" / "canonical_goal.md"
    goal_path.parent.mkdir(parents=True, exist_ok=True)
    goal_path.write_text(body, encoding="utf-8")
    return goal_path


def _write_fabric_workspace_evidence(paths: RuntimePaths) -> Path:
    evidence_path = paths.root / "mods" / "aura-progression-mod" / "src" / "main" / "resources" / "fabric.mod.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text('{"schemaVersion":1,"id":"aura-progression"}\n', encoding="utf-8")
    return evidence_path


def _checkpoint(*, run_id: str, emitted_at: datetime, item_path: Path) -> ResearchCheckpoint:
    return ResearchCheckpoint(
        checkpoint_id=run_id,
        mode=ResearchRuntimeMode.GOALSPEC,
        status=ResearchStatus.GOALSPEC_RUNNING,
        node_id="objective_profile_sync",
        stage_kind_id="research.objective-profile-sync",
        started_at=emitted_at,
        updated_at=emitted_at,
        owned_queues=(
            ResearchQueueOwnership(
                family=ResearchQueueFamily.GOALSPEC,
                queue_path=item_path.parent,
                item_path=item_path,
                owner_token=run_id,
                acquired_at=emitted_at,
            ),
        ),
    )


def test_execute_contractor_emits_validated_profile_report_and_record(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    emitted_at = _dt("2026-04-10T20:00:00Z")
    goal_path = _write_staged_goal(paths, MINECRAFT_MOD_GOAL_TEXT)

    result = execute_contractor(
        paths,
        _checkpoint(run_id="goalspec-contractor-emit-001", emitted_at=emitted_at, item_path=goal_path),
        run_id="goalspec-contractor-emit-001",
        emitted_at=emitted_at,
    )

    assert result.profile.shape_class == "platform_extension"
    assert result.profile.specificity_level == "L4"
    assert result.profile.browse_used is False
    assert "host.minecraft@1" in result.profile.resolved_profile_ids
    assert "stack.jvm_gradle@1" in result.profile.resolved_profile_ids

    report_text = (paths.root / result.report_path).read_text(encoding="utf-8")
    record_payload = json.loads((paths.root / result.record_path).read_text(encoding="utf-8"))

    assert "EXAMPLES_INDEX.md" in report_text
    assert "EXAMPLES_SHAPES.md" in report_text
    assert "EXAMPLES_PLATFORM_EXTENSIONS.md" in report_text
    assert "`browse_used`: `false`" in report_text
    assert record_payload["source_checksum_sha256"]
    assert record_payload["profile_path"] == "agents/objective/contractor_profile.json"
    assert record_payload["report_path"] == "agents/reports/contractor_profile.md"


def test_execute_contractor_reuses_existing_outputs_for_unchanged_inputs(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    first_emitted_at = _dt("2026-04-10T20:10:00Z")
    second_emitted_at = _dt("2026-04-10T21:10:00Z")
    goal_path = _write_staged_goal(paths, MINECRAFT_MOD_GOAL_TEXT)
    checkpoint = _checkpoint(run_id="goalspec-contractor-reuse-001", emitted_at=first_emitted_at, item_path=goal_path)

    first = execute_contractor(
        paths,
        checkpoint,
        run_id="goalspec-contractor-reuse-001",
        emitted_at=first_emitted_at,
    )
    first_profile_text = paths.contractor_profile_file.read_text(encoding="utf-8")
    first_report_text = paths.contractor_profile_report_file.read_text(encoding="utf-8")

    second = execute_contractor(
        paths,
        _checkpoint(run_id="goalspec-contractor-reuse-001", emitted_at=second_emitted_at, item_path=goal_path),
        run_id="goalspec-contractor-reuse-001",
        emitted_at=second_emitted_at,
    )

    assert second.profile.updated_at == first.profile.updated_at
    assert paths.contractor_profile_file.read_text(encoding="utf-8") == first_profile_text
    assert paths.contractor_profile_report_file.read_text(encoding="utf-8") == first_report_text


def test_execute_contractor_invalidates_reuse_when_canonical_source_changes(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    first_emitted_at = _dt("2026-04-10T20:12:00Z")
    second_emitted_at = _dt("2026-04-10T21:12:00Z")
    canonical_goal_path = _write_canonical_goal(paths, MINECRAFT_MOD_GOAL_TEXT)
    staged_goal = f"""---
idea_id: IDEA-CANONICAL-001
title: Canonical Source Goal
canonical_source_path: {canonical_goal_path.relative_to(paths.root).as_posix()}
---

# Canonical Source Goal

Use canonical-source content for classification.
"""
    goal_path = _write_staged_goal(paths, staged_goal)

    first = execute_contractor(
        paths,
        _checkpoint(run_id="goalspec-contractor-canonical-001", emitted_at=first_emitted_at, item_path=goal_path),
        run_id="goalspec-contractor-canonical-001",
        emitted_at=first_emitted_at,
    )
    assert first.profile.shape_class == "platform_extension"

    canonical_goal_path.write_text(AMBIGUOUS_GOAL_TEXT, encoding="utf-8")

    second = execute_contractor(
        paths,
        _checkpoint(run_id="goalspec-contractor-canonical-001", emitted_at=second_emitted_at, item_path=goal_path),
        run_id="goalspec-contractor-canonical-001",
        emitted_at=second_emitted_at,
    )

    assert second.profile.shape_class == "unknown"
    assert second.profile.updated_at == second_emitted_at


def test_execute_contractor_surfaces_validation_failures_before_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _runtime_paths(tmp_path)
    emitted_at = _dt("2026-04-10T20:20:00Z")
    goal_path = _write_staged_goal(paths, MINECRAFT_MOD_GOAL_TEXT)
    original_builder = goalspec_contractor._build_profile_payload

    def _invalid_profile_payload(*args, **kwargs):
        payload = original_builder(*args, **kwargs)
        payload["classification"]["shape_class"] = "unknown"
        return payload

    monkeypatch.setattr(goalspec_contractor, "_build_profile_payload", _invalid_profile_payload)

    with pytest.raises(Exception, match="shape_class"):
        execute_contractor(
            paths,
            _checkpoint(run_id="goalspec-contractor-invalid-001", emitted_at=emitted_at, item_path=goal_path),
            run_id="goalspec-contractor-invalid-001",
            emitted_at=emitted_at,
        )

    assert not paths.contractor_profile_file.exists()
    assert not paths.contractor_profile_report_file.exists()


def test_execute_contractor_stays_conservative_for_ambiguous_goals(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    emitted_at = _dt("2026-04-10T20:30:00Z")
    goal_path = _write_staged_goal(paths, AMBIGUOUS_GOAL_TEXT)

    result = execute_contractor(
        paths,
        _checkpoint(run_id="goalspec-contractor-ambiguous-001", emitted_at=emitted_at, item_path=goal_path),
        run_id="goalspec-contractor-ambiguous-001",
        emitted_at=emitted_at,
    )

    report_text = (paths.root / result.report_path).read_text(encoding="utf-8")

    assert result.profile.shape_class == "unknown"
    assert result.profile.specificity_level == "L0"
    assert result.profile.fallback_mode == "abstain_unknown"
    assert "No trustworthy host, archetype, or stack specialization is justified yet." in result.profile.abstentions
    assert "EXAMPLES_AMBIGUOUS_AND_EDGE_CASES.md" in report_text


def test_execute_contractor_emits_typed_specialization_provenance_for_unsupported_loader(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    emitted_at = _dt("2026-04-10T20:40:00Z")
    evidence_path = _write_fabric_workspace_evidence(paths)
    goal_path = _write_staged_goal(paths, MINECRAFT_FABRIC_GOAL_TEXT)

    result = execute_contractor(
        paths,
        _checkpoint(run_id="goalspec-contractor-loader-001", emitted_at=emitted_at, item_path=goal_path),
        run_id="goalspec-contractor-loader-001",
        emitted_at=emitted_at,
    )

    report_text = (paths.root / result.report_path).read_text(encoding="utf-8")
    provenance = {(item.provenance, item.support_state, item.key, item.value) for item in result.profile.specialization_provenance}

    assert result.profile.unresolved_specializations == ("loader=fabric",)
    assert provenance == {
        ("source_requested", "unsupported", "loader", "fabric"),
        ("workspace_grounded", "unsupported", "loader", "fabric"),
    }
    grounded_record = next(item for item in result.profile.specialization_provenance if item.provenance == "workspace_grounded")
    assert grounded_record.evidence_path == evidence_path.relative_to(paths.root).as_posix()
    assert "Specialization-Provenance" in report_text
    assert "source_requested" in report_text
    assert "workspace_grounded" in report_text


def test_execute_contractor_keeps_loader_as_source_requested_only_without_repo_evidence(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    emitted_at = _dt("2026-04-10T20:45:00Z")
    goal_path = _write_staged_goal(paths, MINECRAFT_FABRIC_GOAL_TEXT)

    result = execute_contractor(
        paths,
        _checkpoint(run_id="goalspec-contractor-loader-002", emitted_at=emitted_at, item_path=goal_path),
        run_id="goalspec-contractor-loader-002",
        emitted_at=emitted_at,
    )

    provenance = {(item.provenance, item.support_state, item.key, item.value) for item in result.profile.specialization_provenance}

    assert result.profile.unresolved_specializations == ("loader=fabric",)
    assert provenance == {
        ("source_requested", "unsupported", "loader", "fabric"),
    }
