from __future__ import annotations

import json
from pathlib import Path
import sys

from millrace_engine.contracts import ControlPlane, RunnerKind, StageContext, StageType
from millrace_engine.diagnostics import DiagnosticsClassification, create_diagnostics_bundle
from millrace_engine.paths import RuntimePaths
from millrace_engine.policies import (
    POLICY_CYCLE_NODE_ID,
    PolicyDecision,
    PolicyEvaluationRecord,
    PolicyEvidence,
    PolicyEvidenceKind,
    PolicyFactSnapshot,
    PolicyHook,
)
from millrace_engine.provenance import TransitionHistoryStore
from millrace_engine.runner import ClaudeRunner, CodexRunner, SubprocessRunner
from millrace_engine.telemetry import EXIT_NO_USAGE, extract_codex_exec_usage


def make_runtime_paths(tmp_path: Path) -> RuntimePaths:
    root = tmp_path / "millrace"
    agents = root / "agents"
    for directory in [
        agents,
        agents / "runs",
        agents / "diagnostics",
        agents / ".runtime/commands/incoming",
        agents / ".runtime/commands/processed",
        agents / ".runtime/commands/failed",
        agents / ".locks",
        agents / ".deferred",
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    return RuntimePaths.from_workspace(root, agents)


def write_fixture_runner(tmp_path: Path) -> Path:
    script = tmp_path / "fixture_runner.py"
    script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pathlib import Path",
                "import json",
                "import os",
                "import sys",
                "",
                "mode = sys.argv[1]",
                "last_path = Path(os.environ['MILLRACE_LAST_RESPONSE_PATH'])",
                "if mode == 'success':",
                "    print('work log')",
                "    print('### BUILDER_COMPLETE')",
                "    print('stderr note', file=sys.stderr)",
                "    last_path.write_text('Rendered success\\n### BUILDER_COMPLETE\\n', encoding='utf-8')",
                "    raise SystemExit(0)",
                "if mode == 'failure':",
                "    print('### BLOCKED')",
                "    print('failing fixture', file=sys.stderr)",
                "    raise SystemExit(3)",
                "if mode == 'missing-marker':",
                "    print('work log with no terminal marker')",
                "    raise SystemExit(0)",
                "if mode == 'codex-success':",
                "    events = [",
                "        {'type': 'thread.started', 'thread_id': 'thread-1'},",
                "        {'type': 'turn.started'},",
                "        {'type': 'item.completed', 'item': {'id': 'item_0', 'type': 'agent_message', 'text': 'OK'}},",
                "        {'type': 'turn.completed', 'usage': {'input_tokens': 11832, 'cached_input_tokens': 5504, 'output_tokens': 21}},",
                "    ]",
                "    for event in events:",
                "        print(json.dumps(event, separators=(',', ':')))",
                "    last_path.write_text('Consult cycle is complete.\\n### CONSULT_COMPLETE\\n', encoding='utf-8')",
                "    raise SystemExit(0)",
                "if mode == 'codex-missing-usage':",
                "    print(json.dumps({'type': 'turn.completed'}, separators=(',', ':')))",
                "    last_path.write_text('Consult cycle is complete.\\n### CONSULT_COMPLETE\\n', encoding='utf-8')",
                "    raise SystemExit(0)",
                "if mode == 'claude-success':",
                "    print('Claude output')",
                "    print('### QA_COMPLETE')",
                "    raise SystemExit(0)",
                "if mode == 'inherited-stdio-holder':",
                "    subprocess = __import__('subprocess')",
                "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'])",
                "    print('work log from parent')",
                "    print('### BUILDER_COMPLETE')",
                "    last_path.write_text('Rendered success\\n### BUILDER_COMPLETE\\n', encoding='utf-8')",
                "    raise SystemExit(0)",
                "raise SystemExit(9)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return script


def write_codex_usage_fixture(tmp_path: Path, *, include_usage: bool) -> Path:
    path = tmp_path / ("valid_single.jsonl" if include_usage else "missing_usage.jsonl")
    lines = [
        '{"type":"thread.started","thread_id":"thread-1"}',
        '{"type":"turn.started"}',
    ]
    if include_usage:
        lines.append(
            '{"type":"turn.completed","usage":{"input_tokens":11832,"cached_input_tokens":5504,"output_tokens":21}}'
        )
    else:
        lines.append('{"type":"turn.completed"}')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_subprocess_runner_captures_success_artifacts_and_notes(tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    script = write_fixture_runner(tmp_path)
    runner = SubprocessRunner(paths)
    context = StageContext.model_validate(
        {
            "stage": StageType.BUILDER,
            "runner": RunnerKind.SUBPROCESS,
            "model": "fixture-model",
            "prompt": "Implement the thing",
            "working_dir": tmp_path,
            "run_id": "runner-success",
            "command": [sys.executable, str(script), "success"],
        }
    )

    result = runner.execute(context)

    assert result.exit_code == 0
    assert result.detected_marker == "BUILDER_COMPLETE"
    assert result.stdout_path is not None and result.stdout_path.read_text(encoding="utf-8").endswith(
        "### BUILDER_COMPLETE\n"
    )
    assert result.last_response_path is not None
    notes = result.runner_notes_path.read_text(encoding="utf-8")
    assert notes == (
        "Run: runner-success\n"
        "Stage result: stage=Builder runner=subprocess model=fixture-model exit=0 "
        "marker=### BUILDER_COMPLETE stdout=agents/runs/runner-success/builder.stdout.log "
        "stderr=agents/runs/runner-success/builder.stderr.log last=agents/runs/runner-success/builder.last.md\n"
    )


def test_subprocess_runner_handles_failure_and_missing_marker_cases(tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    script = write_fixture_runner(tmp_path)
    runner = SubprocessRunner(paths)

    failure_context = StageContext.model_validate(
        {
            "stage": StageType.CONSULT,
            "runner": RunnerKind.SUBPROCESS,
            "model": "fixture-model",
            "prompt": "Diagnose the failure",
            "working_dir": tmp_path,
            "run_id": "runner-failure",
            "command": [sys.executable, str(script), "failure"],
        }
    )
    failure_result = runner.execute(failure_context)
    assert failure_result.exit_code == 3
    assert failure_result.detected_marker == "BLOCKED"

    missing_marker_context = StageContext.model_validate(
        {
            "stage": StageType.QA,
            "runner": RunnerKind.SUBPROCESS,
            "model": "fixture-model",
            "prompt": "Validate the change",
            "working_dir": tmp_path,
            "run_id": "runner-missing",
            "command": [sys.executable, str(script), "missing-marker"],
        }
    )
    missing_marker_result = runner.execute(missing_marker_context)
    assert missing_marker_result.exit_code == 0
    assert missing_marker_result.detected_marker is None


def test_subprocess_runner_does_not_block_on_inherited_stdio_holders(tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    script = write_fixture_runner(tmp_path)
    runner = SubprocessRunner(paths)
    context = StageContext.model_validate(
        {
            "stage": StageType.BUILDER,
            "runner": RunnerKind.SUBPROCESS,
            "model": "fixture-model",
            "prompt": "Implement the thing",
            "working_dir": tmp_path,
            "run_id": "runner-inherited-stdio",
            "timeout_seconds": 1,
            "command": [sys.executable, str(script), "inherited-stdio-holder"],
        }
    )

    result = runner.execute(context)

    assert result.exit_code == 0
    assert result.detected_marker == "BUILDER_COMPLETE"
    assert result.duration_seconds < 1.0
    assert result.stdout_path is not None
    assert "### BUILDER_COMPLETE" in result.stdout_path.read_text(encoding="utf-8")


def test_codex_runner_extracts_usage_and_preserves_rendered_response(tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    script = write_fixture_runner(tmp_path)
    runner = CodexRunner(paths)
    context = StageContext.model_validate(
        {
            "stage": StageType.CONSULT,
            "runner": RunnerKind.CODEX,
            "model": "gpt-5.3-codex",
            "prompt": "Investigate the blocker",
            "working_dir": tmp_path,
            "run_id": "consult_usage_logging",
            "command": [sys.executable, str(script), "codex-success"],
        }
    )

    result = runner.execute(context)

    assert result.detected_marker == "CONSULT_COMPLETE"
    assert result.usage_summary is not None and result.usage_summary.ok is True
    assert result.usage_summary.input_tokens == 11832
    assert result.usage_summary.cached_input_tokens == 5504
    assert result.usage_summary.output_tokens == 21
    assert result.last_response_path is not None
    assert "Consult cycle is complete." in result.last_response_path.read_text(encoding="utf-8")
    notes = result.runner_notes_path.read_text(encoding="utf-8")
    assert (
        "Token usage: stage=Consult runner=codex model=gpt-5.3-codex "
        "input=11832 cached=5504 output=21 stdout=agents/runs/consult_usage_logging/consult.stdout.log\n"
    ) in notes


def test_codex_runner_default_command_uses_model_and_full_auto(tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    runner = CodexRunner(paths)
    context = StageContext.model_validate(
        {
            "stage": StageType.BUILDER,
            "runner": RunnerKind.CODEX,
            "model": "gpt-5.3-codex",
            "prompt": "Implement the thing",
            "working_dir": tmp_path,
            "run_id": "codex_default_command",
        }
    )

    command = runner.build_command(context, tmp_path / "last.md")

    assert command == (
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--model",
        "gpt-5.3-codex",
        "--full-auto",
        "-o",
        str(tmp_path / "last.md"),
        "Implement the thing",
    )


def test_codex_runner_applies_search_and_reasoning_flags(tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    runner = CodexRunner(paths)
    context = StageContext.model_validate(
        {
            "stage": StageType.CONSULT,
            "runner": RunnerKind.CODEX,
            "model": "gpt-5.4",
            "prompt": "Investigate the blocker",
            "working_dir": tmp_path,
            "run_id": "codex_search_and_effort",
            "allow_search": True,
            "effort": "high",
        }
    )

    command = runner.build_command(context, tmp_path / "last.md")

    assert command == (
        "codex",
        "--search",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--model",
        "gpt-5.4",
        "--full-auto",
        "-c",
        'model_reasoning_effort="high"',
        "-o",
        str(tmp_path / "last.md"),
        "Investigate the blocker",
    )


def test_claude_runner_uses_stdout_as_rendered_last_response(tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    script = write_fixture_runner(tmp_path)
    runner = ClaudeRunner(paths)
    context = StageContext.model_validate(
        {
            "stage": StageType.QA,
            "runner": RunnerKind.CLAUDE,
            "model": "sonnet",
            "prompt": "Run QA",
            "working_dir": tmp_path,
            "run_id": "claude-success",
            "command": [sys.executable, str(script), "claude-success"],
        }
    )

    result = runner.execute(context)

    assert result.detected_marker == "QA_COMPLETE"
    assert result.last_response_path is not None
    assert result.last_response_path.read_text(encoding="utf-8").endswith("### QA_COMPLETE\n")


def test_extract_codex_exec_usage_matches_reference_fields(tmp_path: Path) -> None:
    valid_path = write_codex_usage_fixture(tmp_path, include_usage=True)
    missing_path = write_codex_usage_fixture(tmp_path, include_usage=False)

    valid = extract_codex_exec_usage(
        valid_path,
        loop="orchestrate",
        stage="Consult",
        model="gpt-5.3-codex",
        runner="codex",
    )
    assert valid.ok is True
    assert valid.input_tokens == 11832
    assert valid.cached_input_tokens == 5504
    assert valid.output_tokens == 21

    missing_usage = extract_codex_exec_usage(
        missing_path,
        loop="orchestrate",
        stage="Consult",
        model="gpt-5.3-codex",
        runner="codex",
    )
    assert missing_usage.ok is False
    assert missing_usage.reason == "missing_usage"
    assert missing_usage.helper_exit == EXIT_NO_USAGE


def test_diagnostics_bundle_creation_writes_manifest_and_summary(tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    script = write_fixture_runner(tmp_path)
    runner = SubprocessRunner(paths)
    context = StageContext.model_validate(
        {
            "stage": StageType.CONSULT,
            "runner": RunnerKind.SUBPROCESS,
            "model": "fixture-model",
            "prompt": "Diagnose the failure",
            "working_dir": tmp_path,
            "run_id": "diagnostic-run",
            "command": [sys.executable, str(script), "failure"],
        }
    )
    result = runner.execute(context)

    bundle = create_diagnostics_bundle(
        paths,
        stage=context.stage,
        marker=result.detected_marker,
        run_dir=result.run_dir,
        stdout_path=result.stdout_path,
        stderr_path=result.stderr_path,
        snapshot_paths=[result.run_dir],
        config_hashes={"active": "abc123"},
        note="Fixture failure bundle",
        bundle_name="diag-fixture",
    )

    assert (bundle / "manifest.json").exists()
    assert (bundle / "failure_summary.md").exists()
    summary = (bundle / "failure_summary.md").read_text(encoding="utf-8")
    assert "- **Stage:** consult" in summary
    assert "- **Marker:** `BLOCKED`" in summary
    assert "- **Note:** Fixture failure bundle" in summary
    assert "- `active`: `abc123`" in summary


def test_diagnostics_bundle_redacts_policy_evidence_and_applies_hooks(tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    run_dir = paths.runs_dir / "policy-diagnostics"
    run_dir.mkdir(parents=True, exist_ok=True)
    history = TransitionHistoryStore(run_dir / "transition_history.jsonl", run_id="policy-diagnostics")
    facts = PolicyFactSnapshot.model_validate(
        {
            "hook": PolicyHook.CYCLE_BOUNDARY,
            "plane": ControlPlane.EXECUTION,
            "run_id": "policy-diagnostics",
            "queue": {"backlog_depth": 0, "backlog_empty": True, "active_task_id": None},
            "runtime": {"execution_status": "IDLE"},
        }
    )
    evaluation = PolicyEvaluationRecord(
        evaluator="execution_usage_budget",
        hook=PolicyHook.CYCLE_BOUNDARY,
        decision=PolicyDecision.POLICY_BLOCKED,
        facts=facts,
        evidence=(
            PolicyEvidence(
                kind=PolicyEvidenceKind.USAGE_BUDGET,
                summary="Budget threshold requested a pause.",
                details={
                    "provider": "env",
                    "command": ["codex", "--api-key", "secret-token"],
                    "nested": {"probe_command": ["curl", "https://example.invalid"]},
                },
            ),
        ),
        notes=("pause requested",),
    )
    history.append(
        event_name="policy.hook.cycle_boundary",
        source="execution_usage_budget",
        plane=ControlPlane.EXECUTION,
        node_id=POLICY_CYCLE_NODE_ID,
        policy_evaluation=evaluation,
    )

    def custom_redactor(snapshot):
        return snapshot.model_copy(update={"notes": (*snapshot.notes, "custom redactor applied")})

    def custom_classifier(snapshot):
        return DiagnosticsClassification(
            label="custom-policy-evidence",
            summary="Custom classifier applied to latest policy evidence.",
            details={"decision": snapshot.decision},
        )

    bundle = create_diagnostics_bundle(
        paths,
        stage=StageType.CONSULT,
        marker="BLOCKED",
        run_dir=run_dir,
        stdout_path=None,
        stderr_path=None,
        snapshot_paths=[run_dir],
        config_hashes={"active": "abc123"},
        note="Policy evidence bundle",
        bundle_name="diag-policy",
        policy_evidence_redactor=custom_redactor,
        policy_evidence_classifier=custom_classifier,
    )

    payload = json.loads((bundle / "policy_evidence.json").read_text(encoding="utf-8"))
    assert payload["decision"] == "policy_blocked"
    assert payload["classification"]["label"] == "custom-policy-evidence"
    assert payload["notes"] == ["pause requested", "custom redactor applied"]
    assert payload["redaction"]["redacted"] is True
    assert "evidence[0].details.command" in payload["redaction"]["redacted_paths"]
    assert payload["evidence"][0]["details"]["command"] == "<redacted>"
    assert payload["evidence"][0]["details"]["nested"]["probe_command"] == "<redacted>"

    copied_history = [
        json.loads(line)
        for line in (bundle / run_dir.name / "transition_history.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert copied_history[0]["policy_evaluation"]["evidence"][0]["details"]["command"] == "<redacted>"
    assert copied_history[0]["attributes"]["policy_evidence_redaction"]["redacted"] is True

    summary = (bundle / "failure_summary.md").read_text(encoding="utf-8")
    assert "- **Latest policy evidence:** `policy_evidence.json`" in summary
    assert "Custom classifier applied to latest policy evidence." in summary
