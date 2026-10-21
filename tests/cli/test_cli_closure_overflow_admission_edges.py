"""Cwd admission edges for paged closure-evidence runner requests.

A paged closure evaluation is context-unbound: its evidence lives under the
materialized workspace, so ``_bound_codex_cwd_refusal`` requires a resolvable
workspace cwd before any session starts. These cases pin the two refusal
reasons that rule emits for paged requests, and show that an inline request
with a mismatched cwd is not refused by the same rule.

The fake adapters record every ``start_session`` call. The refusal cases assert
that no call happened. Helpers that the lifecycle suite defines are copied here
rather than imported.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from millrace.adapters.cli.context import CliWorkspacePaths, OpenRuntimeContext
from millrace.adapters.cli.lifecycle import run_lifecycle_transition_once
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.runner_contract import (
    AdapterLocalConfig,
    DispatchEcho,
    Unsupported,
)
from millrace.compiler.runner_bindings import DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY
from millrace.contracts.state import RuntimeState
from millrace.kernel import empty_runtime_state
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore
from millrace.testing import materialize_fake_runner_session_cas
from support import closure_overflow as co

SENTINEL = "cwd-admission-probe"
NO_START = "cwd admission must refuse before any session starts"


# ---------------------------------------------------------------------------
# Fakes and workspace helpers (copied from the lifecycle suite).
# ---------------------------------------------------------------------------


class _ProbeAdapter:
    """A registered runner adapter that records every start attempt."""

    def __init__(
        self,
        adapter_kind: str,
        config: Any,
        *,
        start_error: BaseException | None = None,
    ) -> None:
        self.adapter_kind = adapter_kind
        self.config = config
        self._start_error = start_error
        self.requests: list[Any] = []
        self.errors: list[str] = []

    def start_session(self, request: Any) -> Any:
        self.requests.append(request)
        error = self._start_error or AssertionError(NO_START)
        self.errors.append(f"{type(error).__name__}: {error}")
        raise error

    def reconcile_session(self, request: Any) -> Any:
        return Unsupported(_dispatch_echo(request.invocation_request))


def _dispatch_echo(request: Any) -> DispatchEcho:
    return DispatchEcho.from_dispatch_envelope(
        request.dispatch_envelope,
        correlation_id=request.correlation_id,
        selected_adapter_kind=request.selected_adapter_kind,
    )


def _single(values: Any) -> Any:
    items = list(values)
    assert len(items) == 1, len(items)
    return items[0]


def _runtime(tmp_path: Path, state: RuntimeState | None = None) -> OpenRuntimeContext:
    root = tmp_path / "workspace"
    db_path = root / ".millrace" / "runtime.sqlite3"
    cas_path = root / ".millrace" / "cas"
    db_path.parent.mkdir(parents=True)
    cas_path.mkdir(parents=True)
    store = SQLiteRuntimeStore.initialize(db_path)
    cas_store = ContentAddressedByteStore(cas_path)
    runtime_state = materialize_fake_runner_session_cas(
        state=state or empty_runtime_state(),
        cas_store=cas_store,
    )
    store.persist_runtime_state(runtime_state, cas_store)
    return OpenRuntimeContext(
        paths=CliWorkspacePaths(root, db_path, cas_path),
        store=store,
        cas_store=cas_store,
    )


def _load(runtime: OpenRuntimeContext) -> RuntimeState:
    return runtime.store.load_runtime_state(runtime.cas_store)


def _start_evaluation(
    tmp_path: Path, source: dict[str, Any] | None = None, **evidence: Any
) -> OpenRuntimeContext:
    """Open the closure target and let one lifecycle tick apply evaluate."""
    fixture = co.opened_state(source, **evidence)
    runtime = _runtime(tmp_path, fixture.state)
    tick = run_lifecycle_transition_once(runtime)
    assert tick.code == "lifecycle_transition_applied", tick
    assert len(co.closure_request_items(_load(runtime))) == 1
    return runtime


def _ready_for(state: RuntimeState, work_item_id: Any) -> Any:
    return _single(
        candidate
        for candidate in list_ready_dispatch_candidates(state).candidates
        if str(candidate.work_item_id) == str(work_item_id)
    )


def _request_id(state: RuntimeState) -> Any:
    return co.closure_request_item(state).ref.work_item_id


def _reasons(result: Any) -> list[str]:
    reasons = (dict(entry).get("reason") for entry in result.diagnostics)
    return [reason for reason in reasons if isinstance(reason, str)]


def _millforge_closure_source() -> dict[str, Any]:
    """The default closure workflow with its runner bound to millforge.

    The component pin is kept, and the terminal mappings use the source-owned
    stage IDs. The compiler rejects lifecycle.* stage IDs for component-bound
    kinds, and it rejects a binding with no mappings.
    """
    source: dict[str, Any] = co.overflow_source()
    binding = source["runner_bindings"][0]
    binding["adapter_kind"] = "millforge"
    binding["terminal_result_mappings"] = (
        {
            "stage_kind_id": "origin_stage",
            "runner_result_id": "COMPLETE",
            "outcome_id": "lifecycle.origin.ready",
        },
        {
            "stage_kind_id": "alpha_stage",
            "runner_result_id": "COMPLETE",
            "outcome_id": "lifecycle.alpha.ready",
        },
        {
            "stage_kind_id": "beta_stage",
            "runner_result_id": "COMPLETE",
            "outcome_id": "lifecycle.beta.ready",
        },
        {
            "stage_kind_id": "review_stage",
            "runner_result_id": "COMPLETE",
            "outcome_id": "lifecycle.review.passed",
        },
        {
            "stage_kind_id": "review_stage",
            "runner_result_id": "BLOCKED",
            "outcome_id": "lifecycle.review.blocked",
        },
    )
    return source


@pytest.fixture
def _millforge_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compile the millforge source under the default runner policy."""
    monkeypatch.setattr(co.gl, "_CODEX_POLICY", DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY)


# ---------------------------------------------------------------------------
# Case (a): a paged request whose codex cwd cannot be resolved.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [None, SimpleNamespace(cwd="relative-cwd"), SimpleNamespace(cwd=None)],
    ids=["no_config", "relative_string_cwd", "absent_cwd"],
)
def test_paged_closure_with_unresolvable_codex_cwd_refuses_before_start(
    tmp_path: Path, config: Any
) -> None:
    runtime = _start_evaluation(tmp_path)
    adapter = _ProbeAdapter("codex", config)
    state = _load(runtime)

    result = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, _request_id(state)).activation_id,
        local_config=AdapterLocalConfig(adapters={"codex": adapter}),
    )

    assert result.code == "adapter_failure", result
    assert "closure_evidence_codex_cwd_unresolvable" in _reasons(result), (
        result.diagnostics
    )
    assert adapter.requests == []
    assert adapter.errors == []
    runtime.store.close()


# ---------------------------------------------------------------------------
# Case (b): a paged request whose runner kind cannot be verified for cwd.
# ---------------------------------------------------------------------------


def test_paged_closure_with_unverifiable_millforge_kind_refuses_before_start(
    tmp_path: Path, _millforge_policy: None
) -> None:
    runtime = _start_evaluation(tmp_path, _millforge_closure_source())
    # The cwd is the correct workspace on purpose: the kind alone is refused.
    adapter = _ProbeAdapter(
        "millforge", SimpleNamespace(cwd=runtime.paths.workspace_path)
    )
    state = _load(runtime)

    result = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, _request_id(state)).activation_id,
        local_config=AdapterLocalConfig(adapters={"millforge": adapter}),
    )

    assert result.code == "adapter_failure", result
    assert "closure_evidence_millforge_cwd_unverifiable" in _reasons(result), (
        result.diagnostics
    )
    assert adapter.requests == []
    assert adapter.errors == []
    runtime.store.close()


# ---------------------------------------------------------------------------
# Case (c): an inline request with a mismatched cwd is not refused by this rule.
# ---------------------------------------------------------------------------


def test_inline_closure_with_mismatched_codex_cwd_is_not_refused_by_cwd_rule(
    tmp_path: Path,
) -> None:
    runtime = _start_evaluation(tmp_path, None, count=1, summary_bytes=64)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    adapter = _ProbeAdapter(
        "codex",
        SimpleNamespace(cwd=elsewhere),
        start_error=RuntimeError(SENTINEL),
    )
    state = _load(runtime)

    result = run_bounded_execution_unit(
        runtime,
        activation_id=_ready_for(state, _request_id(state)).activation_id,
        local_config=AdapterLocalConfig(adapters={"codex": adapter}),
    )

    # The start attempt is reached and the fake's own sentinel is what fails it.
    assert len(adapter.requests) == 1, result
    assert adapter.errors == [f"RuntimeError: {SENTINEL}"], adapter.errors
    assert not any(
        reason.startswith("closure_evidence_") for reason in _reasons(result)
    ), result.diagnostics
    runtime.store.close()
