# Adapters

Adapters connect Millrace to processes and services outside the runtime.

They translate a selected dispatch into an external call and return evidence
or a typed error. They do not select workflows, discover package assets, route
work, or mutate durable state.

## Runner Boundary

The generic runner boundary is built around:

- `AdapterInvocationRequest`, the selected dispatch and local invocation
  context;
- `RunnerAdapter`, the invocation interface;
- `AdapterSuccessResult` and `AdapterErrorResult`, the two process outcomes;
- `DispatchEcho`, the identity evidence required on a successful result;
- `runner_evidence_from_adapter_outcome()`, the checked conversion from a
  successful adapter result into runtime evidence.

Only an authenticated success result can become runner evidence. A process
error remains an adapter error and cannot be presented to the kernel as a
stage observation.

## Codex

`codex.py` implements the selected `codex` runner kind. It materializes the
already-selected dispatch, prompt, and skill assets into a JSON invocation
bundle and calls an explicit local wrapper command.

The wrapper must return one strict JSON result with the original dispatch
identity. The adapter checks byte limits, timeout, redaction, result shape, and
echo identity before returning evidence.

Local configuration owns the wrapper command, working directory, environment
allowlist, byte ceilings, and maximum timeout. It cannot enlarge the timeout or
authority selected by the workflow.

See `docs/codex-runner.md` for operator setup.

## Subprocess Transport

`subprocess_transport.py` provides bounded local process execution for reviewed
adapters. It accepts an explicit argument vector, stdin bytes, working
directory, environment, timeout, and output ceilings.

It is an internal transport helper, not a runner kind or runtime evidence
source by itself.
