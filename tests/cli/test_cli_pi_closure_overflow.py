"""Real PiRpcAdapter closure-overflow proofs through the bounded execution unit.

The fake external process is ``tests/fixtures/pi_rpc_closure_reader.py``. It runs
no child commands and reads only the closure evidence pages it is pointed at.
All state lives under pytest's ``tmp_path``; no R or PP path is referenced.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.cli.test_cli_bounded_execution_unit import _load, _runtime
from tests.compiler.test_pi_runner_bindings import EFFECTS
from tests.support.pi_rpc import LIMITS, MODEL

from millrace.adapters import pi_rpc
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.pi_rpc import PiRpcAdapter
from millrace.adapters.pi_rpc_config import PiAttempt, PiRpcConfig
from millrace.adapters.runner_contract import AdapterLocalConfig
from millrace.compiler.runner_bindings import DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY
from millrace.contracts.closure_request_overflow import OVERFLOW_KEY, SNAPSHOT_KEY
from millrace.contracts.runner_payload_capacity import (
    installed_payload_capacity_pin,
    payload_capacity_pin_record,
)
from support import closure_overflow as co

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pi_rpc_closure_reader.py"
PROMPT_LIMIT = 262144
INLINE_BODY_BYTES = 131072
TIMEOUT_SECONDS = 10


@pytest.fixture(autouse=True)
def _pi_compile_policy(monkeypatch):
    """Compile Pi sources under the default runner policy.

    ``generic_lifecycle.compile_lifecycle`` forces the Codex-only policy, which
    rewrites a pi_rpc binding to codex and then fails the payload capacity pin.
    The Pi compiler authority tests compile the same shape under the default
    policy, so this seam matches that path without editing shared support.
    """
    monkeypatch.setattr(co.gl, "_CODEX_POLICY", DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY)


def _pi_closure_source(source):
    """The Pi closure transform used by the compiler authority test."""
    for runner in source["runner_bindings"]:
        runner["adapter_kind"] = "pi_rpc"
        runner["component_pin"] = None
        runner["terminal_result_mappings"] = ()
        runner["required_capability_ids"] = EFFECTS
        runner["payload_capacity_pin"] = payload_capacity_pin_record(
            installed_payload_capacity_pin()
        )
    known = {entry["id"] for entry in source["capabilities"]}
    source["capabilities"].extend(
        dict(
            id=name,
            kind="runner.invoke",
            support_status="supported",
            grant_status="granted",
            approval_policy_id=None,
        )
        for name in EFFECTS
        if name not in known
    )
    return source


def _config(cwd: Path) -> PiRpcConfig:
    return PiRpcConfig(
        {
            **LIMITS,
            "adapter_id": "pi-local",
            "max_result_bytes": 8192,
            "max_event_bytes": 4194304,
            "max_stdout_bytes": 16777216,
            "max_input_bundle_bytes": PROMPT_LIMIT,
            "timeout_seconds": TIMEOUT_SECONDS,
            "cwd": str(cwd),
            "executable": sys.executable,
            "argv": [str(FIXTURE)],
        },
        {"schema_version": "pi-rpc-profile.v1", "model": MODEL, "managed_args": []},
        b"",
    )


def _stub_pi(monkeypatch, tmp_path: Path, *, on_materialize=None):
    """Stub only the environment edges; the adapter and fixture run for real."""
    calls = {"materialize": 0, "prespawn": 0, "probe_versions": 0}

    def materialize(self):
        calls["materialize"] += 1
        if on_materialize is not None:
            on_materialize(self)
        path = tmp_path / f"attempt-{calls['materialize']}"
        path.mkdir()
        st = path.stat()
        return PiAttempt(
            path,
            (st.st_dev, st.st_ino),
            {
                "HOME": str(path),
                "TMPDIR": str(path),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

    def prespawn(self, attempt):
        calls["prespawn"] += 1

    def probe_versions(self, attempt, deadline):
        calls["probe_versions"] += 1

    monkeypatch.setenv("PI_RPC_API_KEY", "fixture-secret")
    monkeypatch.setattr(PiRpcConfig, "verify", lambda self: {})
    monkeypatch.setattr(PiRpcConfig, "materialize", materialize)
    monkeypatch.setattr(PiRpcConfig, "prespawn", prespawn)
    monkeypatch.setattr(PiRpcConfig, "probe_versions", probe_versions)
    return calls


def _materialized_pages(workspace: Path) -> dict[str, bytes]:
    """Every regular file in the single closure-evidence session directory."""
    root = workspace / co.MATERIALIZATION_ROOT
    directories = sorted(path for path in root.iterdir() if path.is_dir())
    assert len(directories) == 1, directories
    return {
        entry.name: entry.read_bytes() for entry in sorted(directories[0].iterdir())
    }


def _assert_pages_rebuild(captured: dict[str, bytes], *, evidence_count: int) -> None:
    """Rebuild every segment from the recorded bytes and check the last page."""
    index_names = sorted(name for name in captured if name.startswith("index-"))
    assert index_names, sorted(captured)
    document = json.loads(b"".join(captured[name] for name in index_names))
    assert document["record_kind"] == "closure_evidence_page_index"
    segments = document["segments"]
    evidence = [s for s in segments if s["segment"] == "evidence_artifact"]
    assert [s["position"] for s in evidence] == list(range(evidence_count))
    declared = set(index_names)
    for segment in segments:
        rebuilt = b""
        for descriptor in segment["pages"]:
            name = Path(descriptor["path"]).name
            data = captured[name]
            assert len(data) == descriptor["bytes"], name
            assert hashlib.sha256(data).hexdigest() == descriptor["sha256"], name
            rebuilt += data
            declared.add(name)
        assert len(rebuilt) == segment["document_bytes"]
        assert hashlib.sha256(rebuilt).hexdigest() == segment["document_sha256"]
    assert set(captured) == declared
    last_page = segments[-1]["pages"][-1]
    last_bytes = captured[Path(last_page["path"]).name]
    assert hashlib.sha256(last_bytes).hexdigest() == last_page["sha256"]


def _assert_target_closed(runtime) -> None:
    after = _load(runtime)
    targets = list(after.closure_targets.values())
    assert [target.status for target in targets] == ["closed"]
    assert len(after.closure_terminal_records) == 1


def _inline_body_state(source):
    """Evidence whose inline closure request canonically measures 131072 bytes."""
    size, previous = INLINE_BODY_BYTES, None
    for _ in range(16):
        fixture = co.evaluated_state(
            source, count=1, summary_bytes=size, summary_character="界"
        )
        measured = co.work_payload_canonical_bytes(
            co.closure_request_item(fixture.state)
        )
        if measured == INLINE_BODY_BYTES:
            return fixture
        if previous is None or previous[0] == size:
            step = INLINE_BODY_BYTES - measured
        else:
            slope = (measured - previous[1]) / (size - previous[0])
            step = round((INLINE_BODY_BYTES - measured) / slope)
        previous = (size, measured)
        size += step
    raise AssertionError(f"could not size inline body to {INLINE_BODY_BYTES}")


def test_oversized_pi_closure_request_with_pass_closes_target(tmp_path, monkeypatch):
    state = co.evaluated_state(_pi_closure_source(co.overflow_source()))
    item = co.closure_request_item(state.state)
    assert co.work_payload_canonical_bytes(item) > co.REQUEST_LIMIT
    assert SNAPSHOT_KEY in item.payload
    runtime = _runtime(tmp_path, state.state)
    captured: dict[str, bytes] = {}

    def capture(config):
        if not captured:
            captured.update(_materialized_pages(Path(config.cwd)))

    _stub_pi(monkeypatch, tmp_path, on_materialize=capture)
    adapter = PiRpcAdapter(_config(runtime.paths.workspace_path))
    local = AdapterLocalConfig(adapters={"pi_rpc": adapter})
    outcome = run_bounded_execution_unit(runtime, local_config=local)
    assert outcome.code == "observation_accepted", outcome
    assert len(captured) > 2, sorted(captured)
    _assert_pages_rebuild(captured, evidence_count=3)
    _assert_target_closed(runtime)
    runtime.store.close()


def test_inline_unicode_closure_body_prompt_stays_under_pi_input_bound(
    tmp_path, monkeypatch
):
    fixture = _inline_body_state(
        _pi_closure_source(
            co.overflow_source(request_limit=PROMPT_LIMIT, overflow=False)
        )
    )
    item = co.closure_request_item(fixture.state)
    assert co.work_payload_canonical_bytes(item) == INLINE_BODY_BYTES
    assert SNAPSHOT_KEY in item.payload
    assert OVERFLOW_KEY not in item.payload
    runtime = _runtime(tmp_path, fixture.state)
    prompts: list[str] = []
    original = pi_rpc.prompt_for_request

    def recording(request):
        prompt = original(request)
        prompts.append(prompt)
        return prompt

    monkeypatch.setattr(pi_rpc, "prompt_for_request", recording)
    _stub_pi(monkeypatch, tmp_path)
    adapter = PiRpcAdapter(_config(runtime.paths.workspace_path))
    local = AdapterLocalConfig(adapters={"pi_rpc": adapter})
    outcome = run_bounded_execution_unit(runtime, local_config=local)
    assert outcome.code == "observation_accepted", outcome
    assert len(prompts) == 1
    prompt_bytes = len(prompts[0].encode("utf-8"))
    assert INLINE_BODY_BYTES < prompt_bytes <= PROMPT_LIMIT, prompt_bytes
    assert "界" in prompts[0]
    _assert_target_closed(runtime)
    runtime.store.close()


def test_mismatched_pi_cwd_refuses_before_spawn(tmp_path, monkeypatch):
    state = co.evaluated_state(_pi_closure_source(co.overflow_source()))
    runtime = _runtime(tmp_path, state.state)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    calls = _stub_pi(monkeypatch, tmp_path)
    spawns: list[object] = []
    real_popen = subprocess.Popen

    def counting_popen(*args, **kwargs):
        spawns.append(args)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", counting_popen)
    adapter = PiRpcAdapter(_config(elsewhere))
    local = AdapterLocalConfig(adapters={"pi_rpc": adapter})
    outcome = run_bounded_execution_unit(runtime, local_config=local)
    assert outcome.code == "adapter_failure", outcome
    assert any(
        dict(diagnostic).get("reason") == "closure_evidence_pi_rpc_cwd_mismatch"
        for diagnostic in outcome.diagnostics
    ), outcome.diagnostics
    assert calls == {"materialize": 0, "prespawn": 0, "probe_versions": 0}
    assert spawns == []
    runtime.store.close()
