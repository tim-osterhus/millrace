from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace_ai.architecture import (
    ArtifactContractDefinition,
    ArtifactFilenameAdapterDefinition,
    ArtifactFormat,
)
from millrace_ai.contracts import TaskDocument
from millrace_ai.runtime.artifact_contracts import (
    RuntimeArtifactError,
    parse_resolved_run_artifact_as,
    resolve_run_artifact,
)

NOW = datetime(2026, 5, 20, tzinfo=timezone.utc)


def _task_contract() -> ArtifactContractDefinition:
    return ArtifactContractDefinition(
        artifact_id="generated_task",
        canonical_filename="generated_task.json",
        accepted_filenames=("generated_task.md",),
        preferred_format=ArtifactFormat.JSON,
        schema_id="task_document_v1",
        filename_adapters=(
            ArtifactFilenameAdapterDefinition(
                filename="generated_task.json",
                format=ArtifactFormat.JSON,
                parser_id="builtin.json",
                renderer_id="builtin.json",
            ),
            ArtifactFilenameAdapterDefinition(
                filename="generated_task.md",
                format=ArtifactFormat.MARKDOWN,
                parser_id="builtin.markdown",
                renderer_id="builtin.markdown",
            ),
        ),
        destination_family_id="task",
    )


def _compiled_plan(contract: ArtifactContractDefinition) -> SimpleNamespace:
    return SimpleNamespace(artifact_contracts_by_id={contract.artifact_id: contract})


def _task_doc() -> TaskDocument:
    return TaskDocument(
        task_id="task-001",
        title="Generated task",
        summary="Exercise runtime artifact contract parsing.",
        target_paths=("src/millrace_ai/runtime/",),
        acceptance=("The runtime selects the canonical artifact.",),
        required_checks=("pytest tests/runtime/test_artifact_contracts.py -q",),
        references=("lab/tasks/queue/2026-05-19-v020-remediation-03-runtime-artifact-resolution.md",),
        risk=("Canonical artifact drift.",),
        created_at=NOW,
        created_by="tests",
    )


def test_resolve_run_artifact_selects_canonical_contract_path(tmp_path: Path) -> None:
    contract = _task_contract()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "generated_task.json").write_text(
        _task_doc().model_dump_json(indent=2),
        encoding="utf-8",
    )
    (run_dir / "generated_task.md").write_text("# Legacy fallback\n", encoding="utf-8")

    resolved = resolve_run_artifact(_compiled_plan(contract), "generated_task", run_dir)
    parsed = parse_resolved_run_artifact_as(resolved, TaskDocument)

    assert resolved.path == run_dir / "generated_task.json"
    assert resolved.contract is contract
    assert resolved.adapter.parser_id == "builtin.json"
    assert parsed.task_id == "task-001"


def test_resolve_run_artifact_reports_missing_contract_fields(tmp_path: Path) -> None:
    contract = _task_contract()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(RuntimeArtifactError) as exc_info:
        resolve_run_artifact(_compiled_plan(contract), "generated_task", run_dir)

    message = str(exc_info.value)
    assert "artifact_id=generated_task" in message
    assert "selected_filename=<none>" in message
    assert "expected_format=json" in message
    assert "failure_class=artifact_missing" in message


def test_parse_resolved_artifact_reports_canonical_json_failure(tmp_path: Path) -> None:
    contract = _task_contract()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "generated_task.json").write_text(
        '{"task_id": "task-001", "title": 12}',
        encoding="utf-8",
    )
    (run_dir / "generated_task.md").write_text("# Legacy fallback\n", encoding="utf-8")

    resolved = resolve_run_artifact(_compiled_plan(contract), "generated_task", run_dir)

    with pytest.raises(RuntimeArtifactError) as exc_info:
        parse_resolved_run_artifact_as(resolved, TaskDocument)

    message = str(exc_info.value)
    assert "artifact_id=generated_task" in message
    assert "selected_filename=generated_task.json" in message
    assert "expected_format=json" in message
    assert "failure_class=json_model_parse" in message
