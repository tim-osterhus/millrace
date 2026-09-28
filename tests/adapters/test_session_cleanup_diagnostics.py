from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from cli.test_cli_bounded_execution_unit import _load, _reopen_runtime
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    RunnerCleanupResult,
    runner_cancellation_diagnostic_digest,
)
from millrace.contracts.runner import runner_session_completion_diagnostic_from_payload
from support.runner_sessions import (
    _config,
    _dispatch_echo,
    _error_outcome,
    _ImmediateHandle,
    _ready_runtime,
    _RecordingAdapter,
    _success_start,
)


def test_normal_orphan_cleanup_facts_survive_reopen_without_candidate_text(
    tmp_path,
) -> None:
    runtime = _ready_runtime(tmp_path)
    safe_facts = {
        "client_closed": False,
        "root_exited": True,
        "stdout_bytes": 16777217,
        "stdout_error_code": "stream_bound",
        "raw_candidate": "secret-value",
    }

    def start(request: AdapterInvocationRequest) -> object:
        handle = _ImmediateHandle(
            _error_outcome(request, dispatch_echo=_dispatch_echo(request)),
            cleanup_disposition="orphan_risk",
        )
        handle._cleanup = RunnerCleanupResult(
            "orphan_risk",
            0,
            0,
            safe_facts,
            runner_cancellation_diagnostic_digest(safe_facts),
        )
        return replace(_success_start(request), handle=handle)

    adapter = _RecordingAdapter(start)
    adapter.config = SimpleNamespace(
        cwd=runtime.paths.workspace_path, wrapper_protocol_version=4
    )
    result = run_bounded_execution_unit(
        runtime,
        local_config=_config(adapter),
    )
    assert result.code == "runner_session_orphan_risk"
    reopened = _reopen_runtime(runtime)
    completion = next(iter(_load(reopened).runner_session_completions.values()))
    assert completion.terminal_state == "lost"
    stored = reopened.cas_store.get_bytes(completion.diagnostic_digest)
    diagnostic = runner_session_completion_diagnostic_from_payload(json.loads(stored))
    assert diagnostic.diagnostic["cleanup"] == {
        "client_closed": False,
        "root_exited": True,
        "stdout_bytes": 16777217,
        "stdout_error_code": "stream_bound",
        "diagnostic_digest": runner_cancellation_diagnostic_digest(safe_facts),
    }
    assert b"secret-value" not in stored
    reopened.close()


def test_normal_cleanup_exception_persists_only_safe_fallback_flag(tmp_path) -> None:
    runtime = _ready_runtime(tmp_path)

    def start(request: AdapterInvocationRequest) -> object:
        handle = _ImmediateHandle(
            _error_outcome(request, dispatch_echo=_dispatch_echo(request))
        )

        def broken_cleanup() -> RunnerCleanupResult:
            raise RuntimeError("secret-value")

        handle.cleanup = broken_cleanup
        return replace(_success_start(request), handle=handle)

    adapter = _RecordingAdapter(start)
    adapter.config = SimpleNamespace(
        cwd=runtime.paths.workspace_path, wrapper_protocol_version=4
    )
    result = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    assert result.code == "runner_session_orphan_risk"
    completion = next(iter(_load(runtime).runner_session_completions.values()))
    stored = runtime.cas_store.get_bytes(completion.diagnostic_digest)
    diagnostic = runner_session_completion_diagnostic_from_payload(json.loads(stored))
    assert diagnostic.diagnostic["cleanup"]["cleanup_exception"] is True
    assert b"secret-value" not in stored
    runtime.close()
