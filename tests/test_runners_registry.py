from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import ExecutionStageName, Plane, WorkItemKind
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runners.base import StageRunnerAdapter
from millrace_ai.runners.dispatcher import StageRunnerDispatcher
from millrace_ai.runners.errors import UnknownRunnerError
from millrace_ai.runners.registry import RunnerRegistry


def _request(tmp_path: Path, *, runner_name: str | None) -> StageRunRequest:
    return StageRunRequest(
        request_id="req-001",
        run_id="run-001",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
        mode_id="standard_plain",
        compiled_plan_id="plan-001",
        entrypoint_path=str(tmp_path / "entrypoint.md"),
        entrypoint_contract_id="builder.contract.v1",
        active_work_item_kind=WorkItemKind.TASK,
        active_work_item_id="task-001",
        active_work_item_path=str(tmp_path / "task-001.json"),
        run_dir=str(tmp_path / "runs" / "run-001"),
        summary_status_path=str(tmp_path / "execution_status.md"),
        runtime_snapshot_path=str(tmp_path / "runtime_snapshot.json"),
        recovery_counters_path=str(tmp_path / "recovery_counters.json"),
        runner_name=runner_name,
        model_name="gpt-5",
        timeout_seconds=300,
    )


class _Adapter(StageRunnerAdapter):
    def __init__(self, name: str, *, token: str) -> None:
        self._name = name
        self._token = token

    @property
    def name(self) -> str:
        return self._name

    def run(self, request: StageRunRequest) -> RunnerRawResult:
        now = datetime.now(timezone.utc)
        run_dir = Path(request.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir / f"{self._name}.stdout.txt"
        stdout_path.write_text(f"### {self._token}\n", encoding="utf-8")
        return RunnerRawResult(
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            runner_name=self._name,
            model_name=request.model_name,
            exit_kind="completed",
            exit_code=0,
            stdout_path=str(stdout_path),
            stderr_path=None,
            terminal_result_path=None,
            started_at=now,
            ended_at=now,
        )


def test_dispatcher_prefers_request_runner_name(tmp_path: Path) -> None:
    request = _request(tmp_path, runner_name="other")

    registry = RunnerRegistry()
    registry.register(_Adapter("codex_cli", token="BLOCKED"))
    registry.register(_Adapter("other", token="BUILDER_COMPLETE"))
    dispatcher = StageRunnerDispatcher(registry=registry, config=RuntimeConfig())

    result = dispatcher(request)
    assert result.runner_name == "other"


def test_dispatcher_uses_default_runner_when_request_runner_missing(tmp_path: Path) -> None:
    request = _request(tmp_path, runner_name=None)

    config_payload = RuntimeConfig().model_dump(mode="python")
    config_payload["runners"] = {"default_runner": "codex_cli"}
    config = RuntimeConfig.model_validate(config_payload)

    registry = RunnerRegistry()
    registry.register(_Adapter("codex_cli", token="BUILDER_COMPLETE"))
    dispatcher = StageRunnerDispatcher(registry=registry, config=config)

    result = dispatcher(request)
    assert result.runner_name == "codex_cli"


def test_dispatcher_raises_for_unknown_runner(tmp_path: Path) -> None:
    request = _request(tmp_path, runner_name="missing")
    dispatcher = StageRunnerDispatcher(registry=RunnerRegistry(), config=RuntimeConfig())

    with pytest.raises(UnknownRunnerError, match="missing"):
        dispatcher(request)
