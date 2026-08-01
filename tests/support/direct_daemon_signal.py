"""Subprocess fixture for direct daemon signal lifecycle tests."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from millrace.adapters.cli import daemon, session_cancellation
from millrace.adapters.cli.context import CliWorkspacePaths
from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationRequest,
    AdapterLocalConfig,
    DispatchEcho,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    StartedSession,
    Unsupported,
    runner_cancellation_diagnostic_digest,
)
from support.runner_sessions import _success_outcome


def _operation(
    operation: str,
    result: str,
) -> RunnerCancellationOperationResult:
    now = time.time_ns()
    diagnostic = {"operation": operation}
    return RunnerCancellationOperationResult(
        operation,
        result,
        now,
        now,
        diagnostic,
        runner_cancellation_diagnostic_digest(diagnostic),
    )


class _DirectSignalHandle:
    def __init__(self, request: AdapterInvocationRequest, mode: str) -> None:
        self._request = request
        self._mode = mode
        self._ready_after: str | None = None

    def poll_completion(self):
        if self._ready_after is None:
            return None
        if self._mode == "completion_race":
            return _success_outcome(self._request)
        return AdapterErrorResult.from_unredacted(
            adapter_id=self._request.adapter_id,
            error_kind="cancelled",
            dispatch_echo=DispatchEcho.from_dispatch_envelope(
                self._request.dispatch_envelope,
                correlation_id=self._request.correlation_id,
                selected_adapter_kind=self._request.selected_adapter_kind,
            ),
            redaction_policy=self._request.redaction_policy,
        )

    def request_cancel(self) -> RunnerCancellationOperationResult:
        if self._mode in {"cooperative", "completion_race"}:
            self._ready_after = "cooperative_cancel"
        return _operation("cooperative_cancel", "succeeded")

    def terminate(self) -> RunnerCancellationOperationResult:
        if self._mode == "terminate":
            self._ready_after = "terminate"
        return _operation("terminate", "succeeded")

    def kill(self) -> RunnerCancellationOperationResult:
        return _operation("kill", "succeeded")

    def cleanup(self) -> RunnerCleanupResult:
        now = time.time_ns()
        disposition = "orphan_risk" if self._mode == "orphan" else "complete"
        diagnostic = {"cleanup": disposition}
        return RunnerCleanupResult(
            disposition,
            now,
            now,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


class _DirectSignalAdapter:
    adapter_kind = "codex"

    def __init__(self, marker_path: Path, mode: str) -> None:
        self._marker_path = marker_path
        self._mode = mode

    def start_session(self, request: AdapterInvocationRequest) -> StartedSession:
        self._marker_path.write_text(request.session_id, encoding="utf-8")
        return StartedSession(
            DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            ),
            _DirectSignalHandle(request, self._mode),
            f"direct-signal:{request.session_id}",
            {},
        )

    def reconcile_session(self, request):
        invocation = request.invocation_request
        return Unsupported(
            DispatchEcho.from_dispatch_envelope(
                invocation.dispatch_envelope,
                correlation_id=invocation.correlation_id,
                selected_adapter_kind=invocation.selected_adapter_kind,
            )
        )


class _MarkerStream:
    def __init__(self, marker_path: Path) -> None:
        self._marker_path = marker_path

    def write(self, value: str) -> int:
        self._marker_path.write_text(value, encoding="utf-8")
        return len(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--cas", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--marker", required=True, type=Path)
    parser.add_argument(
        "--mode",
        choices=("idle", "cooperative", "completion_race", "terminate", "orphan"),
        required=True,
    )
    args = parser.parse_args()

    session_cancellation.cooperative_cancel_grace_seconds = 0.05
    session_cancellation.terminate_grace_seconds = 0.05
    local_config = AdapterLocalConfig()
    if args.mode != "idle":
        local_config = AdapterLocalConfig(
            adapters={
                "codex": _DirectSignalAdapter(args.marker, args.mode),
            }
        )
    summary = daemon.run_daemon_loop(
        daemon.DaemonRunOptions(
            paths=CliWorkspacePaths(args.workspace, args.db, args.cas),
            idle_sleep_seconds=10.0,
            max_ticks=None,
            activation_id=None,
            adapter_kind=None,
            local_config=local_config,
            monitor="basic" if args.mode == "idle" else "none",
            actor_id="local_operator",
        ),
        progress_stream=_MarkerStream(args.marker) if args.mode == "idle" else None,
    )
    args.summary.write_text(
        json.dumps(summary.data(), sort_keys=True),
        encoding="utf-8",
    )
    return 0 if daemon._summary_is_success(summary) else 3


if __name__ == "__main__":
    raise SystemExit(main())
