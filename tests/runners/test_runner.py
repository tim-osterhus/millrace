from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from millrace_ai.contracts import ResultClass
from millrace_ai.runner import (
    RunnerRawResult,
    StageRunRequest,
    normalize_stage_result,
    render_stage_request_context_lines,
)

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_runner_module_is_facade_over_runners_package() -> None:
    runner_facade = importlib.import_module("millrace_ai.runner")
    requests_module = importlib.import_module("millrace_ai.runners.requests")
    normalization_module = importlib.import_module("millrace_ai.runners.normalization")

    assert Path(runner_facade.__file__).as_posix().endswith("/runner.py")
    assert runner_facade.StageRunRequest is requests_module.StageRunRequest
    assert runner_facade.RunnerRawResult is requests_module.RunnerRawResult
    assert runner_facade.normalize_stage_result is normalization_module.normalize_stage_result
    assert runner_facade.render_stage_request_context_lines is (
        requests_module.render_stage_request_context_lines
    )


def _request(tmp_path: Path, *, stage: str = "builder") -> StageRunRequest:
    stage_to_plane = {
        "builder": "execution",
        "checker": "execution",
        "fixer": "execution",
        "doublechecker": "execution",
        "updater": "execution",
        "troubleshooter": "execution",
        "consultant": "execution",
        "planner": "planning",
        "manager": "planning",
        "mechanic": "planning",
        "auditor": "planning",
    }

    return StageRunRequest(
        request_id="req-001",
        run_id="run-001",
        plane=stage_to_plane[stage],
        stage=stage,
        mode_id="standard_plain",
        compiled_plan_id="plan-001",
        entrypoint_path=f"assets/entrypoints/{stage}.md",
        active_work_item_kind="task",
        active_work_item_id="task-001",
        active_work_item_path="lab/tasks/queue/task-001.md",
        run_dir=str(tmp_path),
        summary_status_path=str(tmp_path / "state" / "execution_status.md"),
        runtime_snapshot_path=str(tmp_path / "state" / "runtime_snapshot.json"),
        recovery_counters_path=str(tmp_path / "state" / "recovery_counters.json"),
        runner_name="unit-runner",
        model_name="unit-model",
        timeout_seconds=45,
    )


def _raw(
    request: StageRunRequest,
    *,
    exit_kind: str = "completed",
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
    terminal_result_path: Path | None = None,
    exit_code: int | None = 0,
) -> RunnerRawResult:
    return RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name=request.runner_name or "unit-runner",
        model_name=request.model_name,
        exit_kind=exit_kind,
        exit_code=exit_code,
        stdout_path=str(stdout_path) if stdout_path else None,
        stderr_path=str(stderr_path) if stderr_path else None,
        terminal_result_path=str(terminal_result_path) if terminal_result_path else None,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=3),
    )


def test_normalize_prefers_structured_terminal_result_file(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="builder")

    summary_artifact = tmp_path / "builder_summary.md"
    summary_artifact.write_text("summary", encoding="utf-8")

    terminal_payload = {
        "stage": "builder",
        "terminal_result": "BUILDER_COMPLETE",
        "result_class": "success",
        "summary_artifact_paths": [str(summary_artifact)],
    }
    terminal_path = tmp_path / "stage_terminal_result.json"
    terminal_path.write_text(json.dumps(terminal_payload), encoding="utf-8")

    stdout_path = tmp_path / "runner_stdout.txt"
    stdout_path.write_text("### FIX_NEEDED\n", encoding="utf-8")

    envelope = normalize_stage_result(
        request,
        _raw(
            request,
            stdout_path=stdout_path,
            terminal_result_path=terminal_path,
        ),
    )

    assert envelope.terminal_result.value == "BUILDER_COMPLETE"
    assert envelope.result_class is ResultClass.SUCCESS
    assert envelope.success is True
    assert envelope.summary_status_marker == "### BUILDER_COMPLETE"
    assert envelope.artifact_paths == (str(summary_artifact),)
    assert envelope.metadata["valid_terminal_result"] is True


def test_normalize_falls_back_to_final_stdout_terminal_token(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="checker")
    stdout_path = tmp_path / "runner_stdout.txt"
    stdout_path.write_text(
        "analysis output\n### FIX_NEEDED\n",
        encoding="utf-8",
    )

    envelope = normalize_stage_result(
        request,
        _raw(request, stdout_path=stdout_path),
    )

    assert envelope.terminal_result.value == "FIX_NEEDED"
    assert envelope.result_class is ResultClass.FOLLOWUP_NEEDED
    assert envelope.success is False
    assert envelope.metadata["failure_class"] is None
    assert envelope.metadata["valid_terminal_result"] is True


def test_normalize_classifies_illegal_terminal_result_for_stage(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="builder")
    stdout_path = tmp_path / "runner_stdout.txt"
    stdout_path.write_text("### CHECKER_PASS\n", encoding="utf-8")

    envelope = normalize_stage_result(request, _raw(request, stdout_path=stdout_path))

    assert envelope.terminal_result.value == "BLOCKED"
    assert envelope.result_class is ResultClass.RECOVERABLE_FAILURE
    assert envelope.success is False
    assert envelope.retryable is True
    assert envelope.metadata["failure_class"] == "illegal_terminal_result"
    assert envelope.metadata["valid_terminal_result"] is False


def test_normalize_classifies_conflicting_terminal_results(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="checker")
    stdout_path = tmp_path / "runner_stdout.txt"
    stdout_path.write_text("### CHECKER_PASS\n### FIX_NEEDED\n", encoding="utf-8")

    envelope = normalize_stage_result(request, _raw(request, stdout_path=stdout_path))

    assert envelope.terminal_result.value == "BLOCKED"
    assert envelope.metadata["failure_class"] == "conflicting_terminal_results"
    assert envelope.metadata["valid_terminal_result"] is False


def test_normalize_classifies_missing_terminal_result(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="builder")
    stdout_path = tmp_path / "runner_stdout.txt"
    stdout_path.write_text("no marker here\n", encoding="utf-8")

    envelope = normalize_stage_result(request, _raw(request, stdout_path=stdout_path))

    assert envelope.terminal_result.value == "BLOCKED"
    assert envelope.metadata["failure_class"] == "missing_terminal_result"
    assert envelope.metadata["valid_terminal_result"] is False


def test_normalize_classifies_illegal_result_class_in_structured_output(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="builder")

    terminal_payload = {
        "stage": "builder",
        "terminal_result": "BUILDER_COMPLETE",
        "result_class": "blocked",
    }
    terminal_path = tmp_path / "stage_terminal_result.json"
    terminal_path.write_text(json.dumps(terminal_payload), encoding="utf-8")

    envelope = normalize_stage_result(
        request,
        _raw(request, terminal_result_path=terminal_path),
    )

    assert envelope.terminal_result.value == "BLOCKED"
    assert envelope.metadata["failure_class"] == "illegal_terminal_result"
    assert envelope.metadata["valid_terminal_result"] is False


def test_normalize_rejects_summary_artifact_traversal_outside_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    request = _request(run_dir, stage="builder")

    outside_artifact = tmp_path / "outside-summary.md"
    outside_artifact.write_text("outside", encoding="utf-8")

    terminal_payload = {
        "stage": "builder",
        "terminal_result": "BUILDER_COMPLETE",
        "result_class": "success",
        "summary_artifact_paths": ["../outside-summary.md"],
    }
    terminal_path = run_dir / "stage_terminal_result.json"
    terminal_path.write_text(json.dumps(terminal_payload), encoding="utf-8")

    envelope = normalize_stage_result(
        request,
        _raw(request, terminal_result_path=terminal_path),
    )

    assert envelope.terminal_result.value == "BLOCKED"
    assert envelope.metadata["failure_class"] == "missing_required_artifact"
    assert envelope.metadata["valid_terminal_result"] is False


def test_normalize_classifies_timeout_even_with_terminal_like_stdout(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="builder")
    stdout_path = tmp_path / "runner_stdout.txt"
    stdout_path.write_text("### BUILDER_COMPLETE\n", encoding="utf-8")

    envelope = normalize_stage_result(
        request,
        _raw(request, exit_kind="timeout", stdout_path=stdout_path, exit_code=124),
    )

    assert envelope.terminal_result.value == "BLOCKED"
    assert envelope.result_class is ResultClass.RECOVERABLE_FAILURE
    assert envelope.metadata["failure_class"] == "runner_timeout"
    assert envelope.metadata["valid_terminal_result"] is False


def test_normalize_classifies_nonzero_exit_code_for_completed_runs(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="builder")
    stdout_path = tmp_path / "runner_stdout.txt"
    stdout_path.write_text("### BUILDER_COMPLETE\n", encoding="utf-8")

    envelope = normalize_stage_result(
        request,
        _raw(request, exit_kind="completed", stdout_path=stdout_path, exit_code=2),
    )

    assert envelope.terminal_result.value == "BLOCKED"
    assert envelope.result_class is ResultClass.RECOVERABLE_FAILURE
    assert envelope.metadata["failure_class"] == "runner_transport_failure"
    assert envelope.metadata["valid_terminal_result"] is False


def test_normalize_rejects_raw_result_identity_mismatch(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="builder")
    stdout_path = tmp_path / "runner_stdout.txt"
    stdout_path.write_text("### BUILDER_COMPLETE\n", encoding="utf-8")
    raw_result = _raw(request, stdout_path=stdout_path).model_copy(update={"run_id": "run-999"})

    envelope = normalize_stage_result(request, raw_result)

    assert envelope.terminal_result.value == "BLOCKED"
    assert envelope.metadata["failure_class"] == "runner_transport_failure"
    assert "run_id" in " ".join(envelope.notes)


def test_render_stage_request_context_lines_includes_live_envelope_fields(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path).model_copy(
        update={
            "required_skill_paths": (
                "millrace-agents/skills/requesting-code-review/SKILL.md",
            ),
            "attached_skill_paths": (
                "millrace-agents/skills/test-driven-development/SKILL.md",
            ),
        }
    )

    context = "\n".join(render_stage_request_context_lines(request))

    assert "Request ID: req-001" in context
    assert "Entrypoint Path: assets/entrypoints/builder.md" in context
    assert "Entrypoint Contract ID: none" in context
    assert "Active Work Item Path: lab/tasks/queue/task-001.md" in context
    assert "Required Skill Paths:" in context
    assert "- millrace-agents/skills/requesting-code-review/SKILL.md" in context
    assert "Attached Skill Paths:" in context
    assert "- millrace-agents/skills/test-driven-development/SKILL.md" in context


def test_render_stage_request_context_lines_includes_preferred_troubleshoot_report_path(
    tmp_path: Path,
) -> None:
    request = StageRunRequest(
        **(
            _request(tmp_path).model_dump(mode="python")
            | {
                "preferred_troubleshoot_report_path": str(
                    tmp_path / "troubleshoot_report.md"
                )
            }
        )
    )

    context = "\n".join(render_stage_request_context_lines(request))

    assert "Preferred Troubleshoot Report Path:" in context
    assert str(tmp_path / "troubleshoot_report.md") in context


def test_render_stage_request_context_lines_handles_optional_fields_absent(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path).model_copy(
        update={
            "entrypoint_contract_id": None,
            "active_work_item_kind": None,
            "active_work_item_id": None,
            "active_work_item_path": None,
            "required_skill_paths": (),
            "attached_skill_paths": (),
            "runner_name": None,
            "model_name": None,
        }
    )

    context = "\n".join(render_stage_request_context_lines(request))

    assert "Active Work Item: none none" in context
    assert "Active Work Item Path: none" in context
    assert "Required Skill Paths: none" in context
    assert "Attached Skill Paths: none" in context
    assert "Runner Name: none" in context
    assert "Model Name: none" in context


def test_render_stage_request_context_lines_covers_all_stage_run_request_fields(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path).model_copy(
        update={
            "entrypoint_contract_id": "builder.contract.v1",
            "required_skill_paths": ("skills/required.md",),
            "attached_skill_paths": ("skills/attached.md",),
        }
    )
    context = "\n".join(render_stage_request_context_lines(request))

    field_label_map = {
        "request_id": "Request ID:",
        "run_id": "Run ID:",
        "plane": "Plane:",
        "stage": "Stage:",
        "mode_id": "Mode ID:",
        "compiled_plan_id": "Compiled Plan ID:",
        "entrypoint_path": "Entrypoint Path:",
        "entrypoint_contract_id": "Entrypoint Contract ID:",
        "required_skill_paths": "Required Skill Paths",
        "attached_skill_paths": "Attached Skill Paths",
        "active_work_item_kind": "Active Work Item:",
        "active_work_item_id": "Active Work Item:",
        "active_work_item_path": "Active Work Item Path:",
        "run_dir": "Run Directory:",
        "summary_status_path": "Summary Status Path:",
        "runtime_snapshot_path": "Runtime Snapshot Path:",
        "recovery_counters_path": "Recovery Counters Path:",
        "preferred_troubleshoot_report_path": "Preferred Troubleshoot Report Path:",
        "runner_name": "Runner Name:",
        "model_name": "Model Name:",
        "timeout_seconds": "Timeout Seconds:",
    }

    assert set(field_label_map) == set(StageRunRequest.model_fields)
    for label in field_label_map.values():
        assert label in context


def test_normalize_classifies_provider_and_runner_errors(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="builder")

    provider_error = normalize_stage_result(request, _raw(request, exit_kind="provider_error"))
    runner_error = normalize_stage_result(request, _raw(request, exit_kind="runner_error"))

    assert provider_error.metadata["failure_class"] == "provider_failure"
    assert runner_error.metadata["failure_class"] == "runner_transport_failure"


def test_normalize_output_is_deterministic(tmp_path: Path) -> None:
    request = _request(tmp_path, stage="checker")
    stdout_path = tmp_path / "runner_stdout.txt"
    stdout_path.write_text("### CHECKER_PASS\n", encoding="utf-8")
    raw_result = _raw(request, stdout_path=stdout_path)

    first = normalize_stage_result(request, raw_result)
    second = normalize_stage_result(request, raw_result)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_normalize_surfaces_preferred_troubleshoot_report_artifact_when_present(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "troubleshoot_report.md"
    report_path.write_text("# Troubleshoot\n", encoding="utf-8")
    stdout_path = tmp_path / "runner_stdout.txt"
    stdout_path.write_text("### FIX_NEEDED\n", encoding="utf-8")
    request = StageRunRequest(
        **(
            _request(tmp_path, stage="checker").model_dump(mode="python")
            | {"preferred_troubleshoot_report_path": str(report_path)}
        )
    )

    envelope = normalize_stage_result(
        request,
        _raw(request, stdout_path=stdout_path),
    )

    assert envelope.report_artifact == str(report_path)
    assert str(report_path) in envelope.artifact_paths
