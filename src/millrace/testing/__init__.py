"""Testing helpers for repository tests."""

from millrace.testing.fakes import (
    decide_with_fake_runner_completion,
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_completion_input_id,
    fake_runner_dispatch_envelope_for_run,
    fake_runner_observation_payload,
    fake_runner_session_state,
    materialize_fake_runner_session_cas,
)

__all__ = (
    "deterministic_context",
    "decide_with_fake_runner_completion",
    "fake_completed_runner_observation_state",
    "fake_runner_dispatch_envelope_for_run",
    "fake_runner_completion_input_id",
    "fake_runner_observation_payload",
    "fake_runner_session_state",
    "materialize_fake_runner_session_cas",
)
