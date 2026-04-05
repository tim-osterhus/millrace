from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import millrace_engine.contracts as contracts
import millrace_engine.contract_documents as contract_documents
import millrace_engine.loop_architecture as loop_architecture
import millrace_engine.loop_architecture_catalog as loop_architecture_catalog
import millrace_engine.loop_architecture_common as loop_architecture_common
import millrace_engine.loop_architecture_loop_contracts as loop_architecture_loop_contracts
import millrace_engine.loop_architecture_profile_contracts as loop_architecture_profile_contracts
import millrace_engine.loop_architecture_stage_contracts as loop_architecture_stage_contracts

from millrace_engine.contracts import (
    ContractSurface,
    ExecutionStatus,
    ResearchStatus,
    RunnerKind,
    RunnerResult,
    StageResult,
    StageType,
    TaskCard,
)


TASK_CARD_MARKDOWN = """## 2026-03-17 - Canonicalize contract surface
- **Goal:** Keep the public model explicit.
**Spec-ID:** SPEC-CONTRACT-001
**Gates:** INTEGRATION
**Integration:** skip
**Dependencies:**
  - upstream-task
**Blocks:**
  - downstream-task
**Provides:**
  - public-contract
"""


def test_task_card_canonical_contract_includes_v1_fields_and_richer_markdown_fields() -> None:
    source_file = Path("agents/tasksbacklog.md")
    card = TaskCard.from_markdown(TASK_CARD_MARKDOWN, source_file=source_file)

    assert card.title == "Canonicalize contract surface"
    assert card.spec_id == "SPEC-CONTRACT-001"
    assert card.gates == ("INTEGRATION",)
    assert card.integration_preference == "skip"
    assert card.depends_on == ("upstream-task",)
    assert card.blocks == ("downstream-task",)
    assert card.provides == ("public-contract",)
    assert card.metadata == {}
    assert card.source_file == source_file
    assert card.raw_markdown == TASK_CARD_MARKDOWN.rstrip("\n")
    assert card.body.startswith("- **Goal:**")


def test_stage_result_populates_public_fields_from_runner_result() -> None:
    run_dir = Path("/tmp/millrace-run")
    stdout_path = run_dir / "builder.stdout.log"
    stderr_path = run_dir / "builder.stderr.log"
    last_response_path = run_dir / "builder.last.md"
    runner_notes_path = run_dir / "runner_notes.md"
    started_at = datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc)
    completed_at = datetime(2026, 3, 17, 12, 0, 5, tzinfo=timezone.utc)

    runner_result = RunnerResult.model_validate(
        {
            "stage": StageType.BUILDER,
            "runner": RunnerKind.SUBPROCESS,
            "model": "fixture-model",
            "command": ("python", "-m", "builder"),
            "exit_code": 0,
            "duration_seconds": 5.0,
            "stdout": "builder ok\n",
            "stderr": "",
            "detected_marker": "BUILDER_COMPLETE",
            "raw_marker_line": "### BUILDER_COMPLETE",
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
            "last_response_path": last_response_path,
            "runner_notes_path": runner_notes_path,
            "run_dir": run_dir,
            "started_at": started_at,
            "completed_at": completed_at,
        }
    )

    result = StageResult.model_validate(
        {
            "stage": StageType.BUILDER,
            "status": "BUILDER_COMPLETE",
            "runner_result": runner_result,
        }
    )

    assert result.exit_code == 0
    assert result.stdout == "builder ok\n"
    assert result.stderr == ""
    assert result.duration_seconds == 5.0
    assert result.runner_used == "subprocess"
    assert result.model_used == "fixture-model"
    assert result.artifacts == (
        stdout_path,
        stderr_path,
        last_response_path,
        runner_notes_path,
    )
    assert result.metadata["command"] == ["python", "-m", "builder"]
    assert result.metadata["detected_marker"] == "BUILDER_COMPLETE"
    assert result.metadata["run_dir"] == run_dir


def test_contract_surfaces_mark_forward_compatible_vocabulary_explicitly() -> None:
    assert StageType.BUILDER.surface is ContractSurface.PUBLIC_V1
    assert StageType.GOAL_INTAKE.surface is ContractSurface.FORWARD_COMPATIBLE

    assert ExecutionStatus.UPDATE_RUNNING.surface is ContractSurface.PUBLIC_V1
    assert ExecutionStatus.LARGE_PLAN_COMPLETE.surface is ContractSurface.FORWARD_COMPATIBLE

    assert ResearchStatus.GOALSPEC_RUNNING.surface is ContractSurface.PUBLIC_V1
    assert ResearchStatus.COMPLETION_MANIFEST_RUNNING.surface is ContractSurface.FORWARD_COMPATIBLE


def test_contracts_re_export_loop_architecture_surface() -> None:
    assert contracts.ControlPlane is loop_architecture.ControlPlane
    assert contracts.RegistryTier is loop_architecture.RegistryTier
    assert contracts.RegisteredStageKindDefinition is loop_architecture.RegisteredStageKindDefinition
    assert contracts.LoopConfigDefinition is loop_architecture.LoopConfigDefinition
    assert contracts.StructuredStageResult is loop_architecture.StructuredStageResult


def test_contracts_re_export_document_contract_family() -> None:
    assert contracts.TaskCard is contract_documents.TaskCard
    assert contracts.AuditContract is contract_documents.AuditContract
    assert contracts.CompletionManifest is contract_documents.CompletionManifest
    assert contracts.ObjectiveContract is contract_documents.ObjectiveContract
    assert contracts.AuditGateDecision is contract_documents.AuditGateDecision
    assert contracts.CompletionDecision is contract_documents.CompletionDecision
    assert contracts.BlockerEntry is contract_documents.BlockerEntry
    assert contracts.StageResult is contract_documents.StageResult


def test_loop_architecture_re_exports_split_contract_families() -> None:
    assert loop_architecture.ControlPlane is loop_architecture_common.ControlPlane
    assert (
        loop_architecture.RegisteredStageKindDefinition
        is loop_architecture_stage_contracts.RegisteredStageKindDefinition
    )
    assert loop_architecture.LoopConfigDefinition is loop_architecture_loop_contracts.LoopConfigDefinition
    assert loop_architecture.ModeDefinition is loop_architecture_profile_contracts.ModeDefinition
    assert loop_architecture.LoopArchitectureCatalog is loop_architecture_catalog.LoopArchitectureCatalog
