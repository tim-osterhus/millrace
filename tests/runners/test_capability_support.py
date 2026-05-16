from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.config import CodexPermissionLevel, RuntimeConfig
from millrace_ai.contracts import (
    CapabilityDecisionState,
    CapabilityEnforcementMode,
    CapabilityScope,
    ExecutionCapabilityGrant,
    Plane,
    WorkItemKind,
)
from millrace_ai.runner import RunnerRawResult, StageRunRequest, render_stage_request_context_lines
from millrace_ai.runners.adapters.codex_cli import CodexCliRunnerAdapter
from millrace_ai.runners.contracts import (
    completion_artifact_from_raw_result,
    invocation_artifact_from_request,
)


def _grant(
    capability_id: str,
    *,
    enforcement: CapabilityEnforcementMode = CapabilityEnforcementMode.ADVISORY_ONLY,
) -> ExecutionCapabilityGrant:
    return ExecutionCapabilityGrant(
        grant_id=f"grant-{capability_id.replace('.', '-')}",
        request_id=f"request-{capability_id.replace('.', '-')}",
        capability_id=capability_id,
        access="execute",
        scope=CapabilityScope(kind="runner" if capability_id == "runner.invoke" else "workspace", value="workspace"),
        decision_state=CapabilityDecisionState.GRANTED,
        enforcement_mode=enforcement,
        evidence_requirements=("runner_invocation", "runner_completion"),
        decision_reason="test grant",
        resolved_by="test",
    )


def _request(tmp_path: Path) -> StageRunRequest:
    return StageRunRequest(
        request_id="req-001",
        run_id="run-001",
        plane=Plane.EXECUTION,
        stage="builder",
        mode_id="default_codex",
        compiled_plan_id="plan-001",
        entrypoint_path=str(tmp_path / "entrypoint.md"),
        active_work_item_kind=WorkItemKind.TASK,
        active_work_item_id="task-001",
        active_work_item_path=str(tmp_path / "task.md"),
        run_dir=str(tmp_path),
        summary_status_path=str(tmp_path / "execution_status.md"),
        runtime_snapshot_path=str(tmp_path / "runtime_snapshot.json"),
        recovery_counters_path=str(tmp_path / "recovery_counters.json"),
        runner_name="codex_cli",
        model_name="gpt-5",
        timeout_seconds=60,
        execution_capability_grants=(
            _grant("runner.invoke", enforcement=CapabilityEnforcementMode.RUNTIME_ENFORCED),
            _grant("workspace.write"),
        ),
    )


def test_request_context_renders_execution_capability_grants(tmp_path: Path) -> None:
    request = _request(tmp_path)

    lines = render_stage_request_context_lines(request)

    assert "Execution Capability Grants:" in lines
    assert "- grant-runner-invoke runner.invoke decision=granted enforcement=runtime_enforced" in lines
    assert "- grant-workspace-write workspace.write decision=granted enforcement=advisory_only" in lines


def test_runner_artifacts_include_capability_grant_state(tmp_path: Path) -> None:
    request = _request(tmp_path)
    now = datetime.now(timezone.utc)
    raw = RunnerRawResult(
        request_id=request.request_id,
        run_id=request.run_id,
        stage=request.stage,
        runner_name="codex_cli",
        exit_kind="completed",
        exit_code=0,
        started_at=now,
        ended_at=now,
        capability_support_decisions=request.capability_support_decisions,
        capability_evidence_refs=("runner_completion:req-001",),
    )

    invocation = invocation_artifact_from_request(
        request=request,
        runner_name="codex_cli",
        command=("codex", "exec"),
        prompt_path=str(tmp_path / "prompt.md"),
        emitted_at=now,
    )
    completion = completion_artifact_from_raw_result(
        request=request,
        runner_name="codex_cli",
        raw_result=raw,
        command=("codex", "exec"),
        emitted_at=now,
    )

    assert invocation.execution_capability_grants[0]["grant_id"] == "grant-runner-invoke"
    assert completion.execution_capability_grants[1]["capability_id"] == "workspace.write"
    assert completion.capability_evidence_refs == ("runner_completion:req-001",)


def test_codex_maximum_permissions_report_advisory_workspace_write(tmp_path: Path) -> None:
    adapter = CodexCliRunnerAdapter(config=RuntimeConfig(), workspace_root=tmp_path)
    decision = adapter.evaluate_capability_grant(
        _grant("workspace.write"),
        invocation_context={"stage": "builder", "model": "gpt-5"},
    )

    assert decision.support_state == "supported"
    assert decision.enforcement_mode is CapabilityEnforcementMode.ADVISORY_ONLY
    assert "maximum" in decision.reason


def test_codex_basic_permissions_report_runtime_invocation_supported(tmp_path: Path) -> None:
    adapter = CodexCliRunnerAdapter(
        config=RuntimeConfig(runners={"codex": {"permission_default": CodexPermissionLevel.BASIC}}),
        workspace_root=tmp_path,
    )
    decision = adapter.evaluate_capability_grant(
        _grant("runner.invoke", enforcement=CapabilityEnforcementMode.RUNTIME_ENFORCED),
        invocation_context={"stage": "builder", "model": "gpt-5"},
    )

    assert decision.support_state == "supported"
    assert decision.enforcement_mode is CapabilityEnforcementMode.RUNTIME_ENFORCED
