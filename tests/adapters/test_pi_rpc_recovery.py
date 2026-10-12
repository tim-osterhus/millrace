"""Recovery ordering derived from the hash-pinned Pi 0.87.0 source.

These are scripted wire regressions, not a replay of C02's unknown event.
"""

import copy
import sys
import threading
import time
from pathlib import Path

import pytest

from millrace.adapters.pi_rpc_client import (
    PiLifecycle,
    PiRpcClient,
    PiRpcError,
)
from tests.support.pi_rpc import LIMITS, MODEL, lifecycle


def compaction_start(reason="threshold"):
    return dict(type="compaction_start", reason=reason)


def compaction_end(reason="threshold", *, retry=False, aborted=False, failed=False):
    event = dict(type="compaction_end", reason=reason, aborted=aborted, willRetry=retry)
    if failed:
        event["errorMessage"] = "scripted summary failure"
    elif not aborted:
        event["result"] = {"summary": "scripted summary"}
    return event


def retry_start(attempt=1, maximum=3):
    return dict(
        type="auto_retry_start",
        attempt=attempt,
        maxAttempts=maximum,
        delayMs=1,
        errorMessage="scripted provider failure",
    )


def failed_run(*, will_retry=True, stop="error"):
    events = lifecycle("prompt")[:-1]
    for event in events:
        if event["type"] == "message_end" and event["message"]["role"] == "assistant":
            event["message"]["stopReason"] = stop
    events[-1]["willRetry"] = will_retry
    return events


def continued_run(*, stop="stop", retry_end=False, attempt=1):
    events = lifecycle("prompt")
    events = events[:2] + events[4:]
    events[-2]["messages"] = events[-2]["messages"][1:]
    for event in events:
        if event["type"] == "message_end":
            event["message"]["stopReason"] = stop
    if retry_end:
        # AgentSession emits this after successful message_end, before turn_end.
        events.insert(4, dict(type="auto_retry_end", success=True, attempt=attempt))
    return events


def continued_tool_run(*, stop="error", retry_end=False):
    events = lifecycle("prompt", tool=("read", {"path": "opaque"}), result="text")
    events = events[:2] + events[4:-1]
    events[-1]["messages"] = events[-1]["messages"][1:]
    events[-1]["willRetry"] = stop == "error"
    events[-3]["message"]["stopReason"] = stop
    if retry_end:
        events.insert(4, dict(type="auto_retry_end", success=True, attempt=1))
    return events


def parse(events):
    parser = PiLifecycle(MODEL, "prompt")
    for event in events:
        parser.event(event)
    return parser


@pytest.mark.parametrize("failed", [False, True])
def test_threshold_compaction_between_tool_turns_keeps_same_agent_run(failed):
    events = lifecycle("prompt", tool=("read", {"path": "opaque"}), result="text")
    events[11:11] = [compaction_start(), compaction_end(failed=failed)]
    parser = parse(events)
    assert parser.runs == 1 and parser.settled == 1
    assert parser.final_text()
    assert "intrinsic recovery" in parser.uncertain
    assert parser.fallback_usage() is None


def test_overflow_compaction_authorizes_exactly_one_continuation():
    parser = parse(
        failed_run(will_retry=False, stop="length")
        + [compaction_start("overflow"), compaction_end("overflow", retry=True)]
        + continued_run()
    )
    assert parser.runs == 2 and parser.settled == 1
    assert parser.final_text() and parser.uncertain


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_or_cancelled_retry_closes_recovery_and_never_reuses_text(cancelled):
    events = failed_run() + [retry_start()]
    if not cancelled:
        more = continued_run(stop="error")[:-1]
        events += more
    events += [
        dict(type="auto_retry_end", success=False, attempt=1),
        dict(type="agent_settled"),
    ]
    parser = parse(events)
    assert parser.settled == 1 and parser.recovery is None and not parser.will_retry
    with pytest.raises(PiRpcError, match="no successful"):
        parser.final_text()


def test_retry_success_ends_at_message_boundary_before_turn_end():
    parser = parse(failed_run() + [retry_start()] + continued_run(retry_end=True))
    assert parser.final_text() and parser.runs == 2 and parser.settled == 1
    assert parser.recovery is None and parser.fallback_usage() is None


def test_multiple_retry_attempts_are_one_cycle():
    events = failed_run() + [retry_start()]
    more = continued_run(stop="error")[:-1]
    more[-1]["willRetry"] = True
    events += more + [retry_start(2)] + continued_run(retry_end=True, attempt=2)
    parser = parse(events)
    assert parser.runs == 3 and parser.final_text()


def test_retry_attempts_reset_after_successful_tool_turn_and_later_error():
    parser = parse(
        failed_run()
        + [retry_start()]
        + continued_tool_run(retry_end=True)
        + [retry_start()]
        + continued_run(retry_end=True)
    )
    assert parser.runs == 3 and parser.settled == 1 and parser.final_text()
    assert parser.recovery is None and parser.retry_attempt == 0
    assert "intrinsic recovery" in parser.uncertain


def test_overflow_recovery_resets_after_successful_tool_turn_and_later_overflow():
    parser = parse(
        failed_run(will_retry=False, stop="length")
        + [compaction_start("overflow"), compaction_end("overflow", retry=True)]
        + continued_tool_run(stop="length")
        + [compaction_start("overflow"), compaction_end("overflow", retry=True)]
        + continued_run()
    )
    assert parser.runs == 3 and parser.settled == 1 and parser.final_text()
    assert not parser.overflow_retry and parser.recovery is None
    assert "intrinsic recovery" in parser.uncertain


def test_false_retry_end_cannot_qualify_success_without_native_success_end():
    events = failed_run() + [retry_start()] + continued_run()[:-1]
    events += [
        dict(type="auto_retry_end", success=False, attempt=1),
        dict(type="agent_settled"),
    ]
    with pytest.raises(PiRpcError, match="retry outcome"):
        parse(events)


def test_overflow_recovery_cannot_repeat_its_compact_and_retry():
    events = failed_run(will_retry=False, stop="length")
    events += [compaction_start("overflow"), compaction_end("overflow", retry=True)]
    events += continued_run(stop="length")[:-1]
    with pytest.raises(PiRpcError, match="compaction boundary"):
        parse(events + [compaction_start("overflow")])


def test_summarization_retry_is_nested_and_balanced_inside_compaction():
    events = lifecycle("prompt", tool=("read", {"path": "opaque"}), result="text")
    summary = retry_start()
    summary["type"] = "summarization_retry_scheduled"
    events[11:11] = [
        compaction_start(),
        summary,
        dict(
            type="summarization_retry_attempt_start",
            source="compaction",
            reason="threshold",
        ),
        dict(type="summarization_retry_finished"),
        compaction_end(),
    ]
    assert parse(events).final_text()


@pytest.mark.parametrize("aborted_backoff", [False, True])
def test_summary_retry_can_repeat_or_finish_during_aborted_backoff(aborted_backoff):
    events = lifecycle("prompt", tool=("read", {"path": "opaque"}), result="text")
    summary = retry_start()
    summary["type"] = "summarization_retry_scheduled"
    nested = [compaction_start(), summary]
    if not aborted_backoff:
        attempt_start = dict(
            type="summarization_retry_attempt_start",
            source="compaction",
            reason="threshold",
        )
        nested += [attempt_start, {**summary, "attempt": 2}, attempt_start]
    nested += [
        dict(type="summarization_retry_finished"),
        compaction_end(aborted=aborted_backoff),
    ]
    events[11:11] = nested
    parser = parse(events)
    assert parser.final_text() and parser.recovery is None
    assert parser.summary_attempt == 0 and not parser.summary_waiting
    assert parser.fallback_usage() is None


@pytest.mark.parametrize("aborted", [False, True])
def test_terminal_compaction_never_grants_a_spurious_new_run(aborted):
    events = lifecycle("prompt")[:-1]
    parser = parse(events + [compaction_start(), compaction_end(aborted=aborted)])
    with pytest.raises(PiRpcError, match="agent start boundary"):
        parser.event(dict(type="agent_start"))


@pytest.mark.parametrize(
    "suffix",
    [
        [compaction_end()],
        [retry_start()],
        [dict(type="auto_retry_end", success=True, attempt=1)],
        [compaction_start(), compaction_start()],
        [compaction_start(), dict(type="agent_settled")],
        [compaction_start(), dict(type="agent_start")],
        [compaction_start(), compaction_end("overflow")],
        [compaction_start(), compaction_end(retry=True, aborted=True)],
        [compaction_start(), compaction_end(retry=True)],
        [dict(type="summarization_retry_finished")],
        [
            dict(
                type="summarization_retry_attempt_start",
                source="compaction",
                reason="threshold",
            )
        ],
    ],
)
def test_recovery_ends_nesting_and_unsolicited_continuation_are_refused(suffix):
    with pytest.raises(PiRpcError):
        parse(lifecycle("prompt")[:-1] + suffix)


@pytest.mark.parametrize("kind", ["retry", "overflow", "manual"])
def test_only_threshold_compaction_may_start_between_turns_in_open_run(kind):
    prefix = lifecycle("prompt", tool=("read", {"path": "opaque"}), result="text")[:11]
    event = retry_start() if kind == "retry" else compaction_start(kind)
    with pytest.raises(PiRpcError, match="recovery boundary"):
        parse(prefix + [event])


@pytest.mark.parametrize(
    "field,bad",
    [
        ("attempt", 0),
        ("attempt", True),
        ("attempt", 4),
        ("maxAttempts", 0),
        ("delayMs", -1),
        ("delayMs", 2**53),
        ("errorMessage", {}),
    ],
)
def test_retry_fields_are_strict_and_bounded(field, bad):
    event = retry_start()
    event[field] = bad
    with pytest.raises(PiRpcError):
        parse(failed_run() + [event])


@pytest.mark.parametrize("success", [True, False])
def test_retry_end_requires_matching_attempt(success):
    with pytest.raises(PiRpcError):
        parse(
            failed_run()
            + [retry_start(), dict(type="auto_retry_end", success=success, attempt=2)]
        )


def test_compaction_recovery_failure_without_second_start_is_narrowly_supported():
    events = failed_run(will_retry=False, stop="length")
    events += [compaction_start("overflow"), compaction_end("overflow", retry=True)]
    events += continued_run(stop="length")[:-1]
    events += [compaction_end("overflow", failed=True), dict(type="agent_settled")]
    parser = parse(events)
    with pytest.raises(PiRpcError, match="no successful"):
        parser.final_text()
    with pytest.raises(PiRpcError, match="event after settlement"):
        parser.event(compaction_end("overflow", failed=True))


def test_compaction_cannot_interrupt_open_message_or_tool():
    for prefix in [
        lifecycle("prompt")[:3],
        lifecycle("prompt", tool=("read", {"path": "opaque"}), result="text")[:7],
    ]:
        with pytest.raises(PiRpcError):
            parse(copy.deepcopy(prefix) + [compaction_start()])


def process_client(tmp_path, mode, timeout=3):
    return PiRpcClient(
        (
            sys.executable,
            str(Path(__file__).parents[1] / "fixtures/pi_rpc_process.py"),
            mode,
        ),
        tmp_path,
        {"HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
        LIMITS,
        MODEL,
        "prompt",
        timeout,
    )


@pytest.mark.parametrize("mode", ["retry", "compaction"])
def test_scripted_subprocess_recovery_checks_final_text_stats_and_owned_closure(
    tmp_path, mode
):
    client = process_client(tmp_path, mode)
    try:
        client.ready()
        with pytest.raises(PiRpcError, match="incomplete recovery evidence"):
            client.run()
        assert client.usage.total_tokens == 260
        assert client.lifecycle.settled == 1 and not client.close()
        assert "intrinsic recovery" in client.lifecycle.uncertain
        assert client.lifecycle.fallback_usage() is None
    finally:
        assert client.dispose()
    assert not any(thread.is_alive() for thread in client.threads)


@pytest.mark.parametrize("mode", ["retry_hang", "compaction_hang"])
def test_recovery_never_moves_absolute_invocation_deadline(tmp_path, mode):
    client = process_client(tmp_path, mode, timeout=0.3)
    deadline = client.deadline
    started = time.monotonic()
    try:
        client.ready()
        with pytest.raises(PiRpcError, match="deadline") as error:
            client.run()
        assert error.value.kind == "timeout"
        assert client.deadline == deadline and client.lifecycle.recovery is not None
    finally:
        assert client.dispose()
    assert time.monotonic() - started < 3
    assert client.process.poll() is not None


@pytest.mark.parametrize("mode", ["retry_hang", "compaction_hang"])
def test_abort_during_recovery_closes_cycle_and_cannot_accept_prior_text(
    tmp_path, mode
):
    client = process_client(tmp_path, mode)
    recovery_started = threading.Event()
    original = client.lifecycle.event

    def observe(event):
        original(event)
        if event["type"] in {"auto_retry_start", "compaction_start"}:
            recovery_started.set()

    client.lifecycle.event = observe
    errors = []

    def invoke():
        try:
            client.run()
        except PiRpcError as error:
            errors.append(error)

    worker = threading.Thread(target=invoke)
    try:
        client.ready()
        worker.start()
        assert recovery_started.wait(1)
        client.cancel_requested.set()
        assert client.abort_ack.wait(1)
        worker.join(2)
        assert not worker.is_alive()
        assert len(errors) == 1 and str(errors[0]) == "no successful final assistant"
        assert client.lifecycle.recovery is None and client.lifecycle.settled == 1
        assert client.lifecycle.uncertain and client.deadline > time.monotonic()
    finally:
        assert client.dispose()
        worker.join(2)
