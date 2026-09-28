import sys
import time
from pathlib import Path

import pytest

from millrace.adapters.pi_rpc_client import (
    PiLifecycle,
    PiRpcClient,
    PiRpcError,
    parse_candidate,
    token_usage,
)
from tests.support.pi_rpc import CANDIDATE, LIMITS, MODEL, lifecycle


def test_report_is_optional_string_and_exact_three_key_result():
    assert (
        parse_candidate(
            '{"marker":"OPAQUE","artifact_payload_candidate":null,"observation_payload_candidate":null}',
            1024,
        )["marker"]
        == "OPAQUE"
    )
    with pytest.raises(ValueError):
        parse_candidate(
            '{"marker":"X","artifact_payload_candidate":null,"observation_payload_candidate":{"runner_report":42}}',
            1024,
        )


@pytest.mark.parametrize(
    "raw", ["{}", '{"marker":"X","marker":"Y"}', "NaN", "```{}", "{} prose"]
)
def test_hostile_candidate_refuses(raw):
    with pytest.raises(ValueError):
        parse_candidate(raw, 1024)


def test_usage_cache_is_input_and_reasoning_is_not_added_twice():
    usage = token_usage(
        dict(input=640, output=44, cacheRead=20, cacheWrite=0, total=704)
    )
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
        660,
        44,
        704,
    )


@pytest.mark.parametrize("bad", [True, 1.0, "1", -1, 2**53, None])
def test_usage_never_coerces(bad):
    assert (
        token_usage(dict(input=bad, output=1, cacheRead=0, cacheWrite=0, total=2))
        is None
    )


def test_coherent_text_tools_are_positive_but_bash_and_image_remain_sticky():
    for tool, result, safe in [
        (("read", {"path": "opaque"}), "bytes", True),
        (
            ("write", {"path": "opaque", "content": "bytes"}),
            "Successfully wrote to opaque",
            True,
        ),
        (
            ("edit", {"path": "opaque", "edits": [{"oldText": "a", "newText": "b"}]}),
            "Successfully replaced 1 block(s) in opaque.",
            True,
        ),
        (("bash", {"command": "printf x"}), "x", False),
        (("read", {"path": "opaque"}), "Read image file [image/png]", False),
    ]:
        parser = PiLifecycle(MODEL, "prompt")
        for e in lifecycle("prompt", tool=tool, result=result):
            parser.event(e)
        assert bool(parser.uncertain) is not safe
        assert parser.final_text()


@pytest.mark.parametrize(
    "mutation",
    ["role", "user", "snapshot", "tool_id", "error_bit", "duplicate", "lost_end"],
)
def test_corrupt_lifecycle_is_never_positive(mutation):
    events = lifecycle("prompt", tool=("read", {"path": "opaque"}), result="text")
    if mutation == "role":
        events[2]["message"]["role"] = "invented"
    elif mutation == "user":
        events[3]["message"]["content"][0]["text"] = "unowned"
    elif mutation == "snapshot":
        events[-2]["messages"] = []
    elif mutation == "tool_id":
        events[6]["toolCallId"] = "unknown"
    elif mutation == "error_bit":
        events[7]["isError"] = "false"
    elif mutation == "duplicate":
        events.append(events[-1])
    else:
        events.pop(7)
    parser = PiLifecycle(MODEL, "prompt")
    with pytest.raises(PiRpcError):
        for e in events:
            parser.event(e)


@pytest.mark.parametrize("mode", ["normal", "stderr"])
def test_real_process_interactive_readiness_completion_and_actual_closure(
    tmp_path, mode
):
    client = PiRpcClient(
        (
            sys.executable,
            str(Path(__file__).parents[1] / "fixtures/pi_rpc_process.py"),
            mode,
        ),
        tmp_path,
        {
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        LIMITS,
        MODEL,
        "prompt",
        3,
    )
    try:
        client.ready()
        assert parse_candidate(client.run(), 8192) == CANDIDATE
        assert (
            client.closed
            and client.process.returncode == 0
            and all(client.eof.values())
        )
        assert not any(t.is_alive() for t in client.threads)
        assert client.usage.total_tokens == 130
    finally:
        client.dispose()


@pytest.mark.parametrize(
    "mode",
    ["flood", "bad_utf8", "partial", "duplicate_settled", "duplicate_response", "hang"],
)
def test_real_process_corruption_and_deadlines_are_bounded(tmp_path, mode):
    client = PiRpcClient(
        (
            sys.executable,
            str(Path(__file__).parents[1] / "fixtures/pi_rpc_process.py"),
            mode,
        ),
        tmp_path,
        {
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        LIMITS,
        MODEL,
        "prompt",
        0.4,
    )
    start = time.monotonic()
    try:
        with pytest.raises((PiRpcError, BrokenPipeError)):
            client.ready()
            client.run()
    finally:
        assert client.dispose()
    assert time.monotonic() - start < 3
    assert client.process.poll() is not None


@pytest.mark.parametrize(
    "mode,expected_code,expected_bytes,expected_events",
    [
        ("flood", "stream_bound", 65536, 2),
        ("event_count", "event_count", 0, 10001),
    ],
)
def test_stream_limits_report_bounded_transport_facts(
    tmp_path, mode, expected_code, expected_bytes, expected_events
):
    limits = {
        **LIMITS,
        "max_event_count": 10000,
        "max_stdout_bytes": 2097152 if mode == "event_count" else 65536,
    }
    client = PiRpcClient(
        (
            sys.executable,
            str(Path(__file__).parents[1] / "fixtures/pi_rpc_process.py"),
            mode,
        ),
        tmp_path,
        {"HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
        limits,
        MODEL,
        "prompt",
        5,
    )
    try:
        client.ready()
        with pytest.raises(PiRpcError):
            client.run()
    finally:
        client.dispose()
    assert client.stream_error_codes["stdout"] == expected_code
    assert client.stream_bytes["stdout"] > expected_bytes
    assert client.stream_events["stdout"] == expected_events


def test_finite_stream_over_one_hundred_thousand_events_reaches_lifecycle_gate(
    tmp_path,
):
    limits = {
        **LIMITS,
        "max_event_count": 500_000,
        "max_stdout_bytes": 268_435_456,
    }
    client = PiRpcClient(
        (
            sys.executable,
            str(Path(__file__).parents[1] / "fixtures/pi_rpc_process.py"),
            "event_count_large",
        ),
        tmp_path,
        {"HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
        limits,
        MODEL,
        "prompt",
        30,
    )
    try:
        client.ready()
        assert parse_candidate(client.run(), 8192) == CANDIDATE
    finally:
        client.dispose()
    assert client.stream_events["stdout"] > 100_000
    assert client.stream_error_codes["stdout"] == "none"
    assert client.lifecycle.uncertain == {"unqualified queue event"}


def test_legal_intrinsic_retry_has_two_runs_but_one_settlement_and_sticky_risk():
    first = lifecycle("prompt")[:-1]
    first[-1]["willRetry"] = True
    # A provider error can include earlier text, which is never terminal recovery.
    for e in first:
        if e["type"] == "message_end" and e["message"]["role"] == "assistant":
            e["message"]["stopReason"] = "error"
    second = lifecycle("prompt")
    # Second agent run carries only its newly generated assistant messages.
    second = second[:2] + second[4:]
    second[-2]["messages"] = second[-2]["messages"][1:]
    events = (
        first
        + [
            dict(
                type="auto_retry_start",
                attempt=1,
                maxAttempts=3,
                delayMs=1,
                errorMessage="retry",
            )
        ]
        + second[:-1]
        + [dict(type="auto_retry_end", success=True, attempt=1), second[-1]]
    )
    parser = PiLifecycle(MODEL, "prompt")
    for event in events:
        parser.event(event)
    assert parser.runs == 2 and parser.settled == 1
    assert parser.final_text() and parser.uncertain and parser.fallback_usage() is None


def test_last_error_never_recovers_prior_candidate_text():
    events = lifecycle("prompt")
    for e in events:
        if e["type"] == "message_end" and e["message"]["role"] == "assistant":
            e["message"]["stopReason"] = "error"
    parser = PiLifecycle(MODEL, "prompt")
    for event in events:
        parser.event(event)
    with pytest.raises(PiRpcError, match="no successful"):
        parser.final_text()
    assert parser.fallback_usage().total_tokens == 130


def test_zero_missing_inconsistent_and_overbound_candidates():
    assert (
        token_usage(dict(input=0, output=0, cacheRead=0, cacheWrite=0, total=0)) is None
    )
    assert token_usage(dict(input=1, output=2, total=3)) is None
    assert (
        token_usage(dict(input=1, output=2, cacheRead=0, cacheWrite=0, total=4)) is None
    )
    import json

    with pytest.raises(PiRpcError, match="result bound"):
        parse_candidate(
            json.dumps(
                {
                    **CANDIDATE,
                    "observation_payload_candidate": {"runner_report": "x" * 9000},
                }
            ),
            8192,
        )


def test_absolute_spent_deadline_never_calls_popen(tmp_path, monkeypatch):
    import time

    from millrace.adapters import pi_rpc_client as module
    from millrace.adapters.pi_rpc_client import PiRpcNotStarted

    called = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: called.append(a))
    with pytest.raises(PiRpcNotStarted):
        PiRpcClient(
            ("never-executed",),
            tmp_path,
            {},
            LIMITS,
            MODEL,
            "prompt",
            100,
            deadline=time.monotonic() - 1,
        )
    assert not called


def test_group_profile_defers_bash_feedback_to_witness_and_accepts_stock_flat_edit():
    parser = PiLifecycle(MODEL, "prompt", group_accounting=True)
    events = lifecycle(
        "prompt", tool=("bash", {"command": "exit 1"}), result="failure feedback"
    )
    # The lifecycle is coherent; independent root evidence remains mandatory.
    for event in events:
        parser.event(event)
    assert not parser.uncertain
    parser = PiLifecycle(MODEL, "prompt", group_accounting=True)
    for event in lifecycle(
        "prompt",
        tool=("edit", {"path": "x", "oldText": "a", "newText": "b"}),
        result="Successfully replaced 1 block(s) in x.",
    ):
        parser.event(event)
    assert not parser.uncertain


def test_supervised_close_requires_report_even_without_group_witness():
    import queue
    import threading
    import time
    from types import SimpleNamespace

    client = object.__new__(PiRpcClient)
    client.closed = False
    client.process = SimpleNamespace(
        stdin=None, stdout=None, stderr=None, returncode=0, poll=lambda: 0
    )
    client.eof = {"stdout": True, "stderr": True, "bridge": True}
    client.errors = []
    client.pending = {}
    client.groups = None
    client.supervisor = SimpleNamespace(finish=lambda: False)
    client.bridge_stream = None
    client.threads = []
    client.stop = threading.Event()
    client.events = queue.Queue()
    client.deadline = time.monotonic() + 5
    assert not client.close()
    assert client.closed
    assert not client.close(), "repeated close must also refuse a missing report"


@pytest.mark.parametrize("verified", [True, False])
def test_dispose_reconciles_control_epipe_only_through_terminal_report(verified):
    import threading
    from types import SimpleNamespace

    client = object.__new__(PiRpcClient)
    client.closed = False

    class ExitingProcess:
        returncode = None
        stdin = stdout = stderr = None

        def poll(self):
            return self.returncode

        def wait(self, _timeout):
            self.returncode = 0 if verified else 1
            return self.returncode

    client.process = ExitingProcess()
    client.supervisor = SimpleNamespace(
        startup_failed=False,
        signal_pi=lambda _operation: (_ for _ in ()).throw(BrokenPipeError()),
        finish=lambda: verified,
    )
    client.groups = None
    client.threads = []
    client.stop = threading.Event()
    client.bridge_stream = None
    assert client.dispose() is verified
    assert client.process.returncode == (0 if verified else 1)


def test_group_prespawn_failure_closes_both_private_descriptors(tmp_path, monkeypatch):
    import os

    from millrace.adapters import pi_rpc_client as module
    from millrace.adapters.pi_rpc_groups import GroupProfile

    descriptors = []
    original_pipe = os.pipe

    def pipe():
        pair = original_pipe()
        descriptors.extend(pair)
        return pair

    monkeypatch.setattr(module.os, "pipe", pipe)
    profile = GroupProfile(tmp_path / "bridge.mjs", "b" * 64, tmp_path, "c" * 64)
    limits = dict(LIMITS)
    del limits["max_stdout_bytes"]
    with pytest.raises(KeyError):
        PiRpcClient(
            ("node", "pi"),
            tmp_path,
            {},
            limits,
            MODEL,
            "prompt",
            1,
            group_profile=profile,
        )
    assert len(descriptors) == 2
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
