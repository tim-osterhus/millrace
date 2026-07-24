# Maintainer Live Workflow Testing

Millrace v0.22 supports the `codex` and `millforge` runner kinds. Their current
test evidence has two different scopes:

| Runner branch | Current evidence |
| --- | --- |
| Codex | Base `kernel_ping` harness coverage and bounded opt-in/configuration preflight in `tests/e2e/test_actual_model_workflow_smoke.py` |
| Millforge | Current official Plus `0.22.0` live proof for `simple_loop` and `vendor_selection` |

The Codex branch is not a completed current official-Plus Codex live proof.
Preflight or offline success must not be reported as completed live workflow
evidence.

## Offline Checks

Run the maintained modules without the live marker first:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/e2e/test_actual_model_workflow_smoke.py \
  tests/e2e/test_simple_loop_millforge_live_proof.py \
  tests/e2e/test_vendor_selection_millforge_live_proof.py \
  -m "not live_model"
```

These checks validate package selection, runner preflight, selected dispatch
identity, redaction, finite limits, routes, and failure classification without
calling a model.

## Workspace Boundary

Live runtime and artifact state must stay outside the source repository. The
harness requires explicit roots and never infers a writable location from the
source checkout:

```bash
export MILLRACE_RUNTIME_REPO="$(git rev-parse --show-toplevel)"
export MILLRACE_E2E_WORKSPACES_ROOT="$(cd "$MILLRACE_RUNTIME_REPO/../../.." && pwd)/workspaces"
mkdir -p "$MILLRACE_E2E_WORKSPACES_ROOT"
```

Each live row uses a fresh direct child of that root. The adapter's working
root must be inside the row's artifact root. Workspace output is retained test
evidence, not source.

## Codex Preflight

The generic Codex harness covers the base `kernel_ping` workflow and bounded
preflight behavior. Configure the wrapper described in
[Codex runner setup](../codex-runner.md), then set:

```bash
export MILLRACE_E2E_ACTUAL_MODEL=1
export MILLRACE_E2E_RUNNER=codex
export MILLRACE_E2E_ADAPTER_CONFIG=/absolute/path/to/codex-adapter.json
export MILLRACE_E2E_SECRET_CANARY='<unique test canary>'
export MILLRACE_E2E_ARTIFACT_ROOT="$MILLRACE_E2E_WORKSPACES_ROOT/e2e-codex-preflight-$(date -u +%Y%m%dT%H%M%SZ)"

export MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW=8
export MILLRACE_E2E_MAX_ADAPTER_TIMEOUT_SECONDS=120
export MILLRACE_E2E_MAX_INPUT_BUNDLE_BYTES=65536
export MILLRACE_E2E_MAX_STDOUT_BYTES=65536
export MILLRACE_E2E_MAX_STDERR_DIAGNOSTIC_BYTES=4096
export MILLRACE_E2E_MAX_WORKFLOW_SECONDS=600
export MILLRACE_E2E_MAX_TOTAL_SECONDS=1800
export MILLRACE_E2E_MAX_RETRIES=0

PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q \
  tests/e2e/test_actual_model_workflow_smoke.py -m live_model
```

The Codex adapter config must include `MILLRACE_E2E_ACTUAL_MODEL` in its live
opt-in flags, include the exact canary in its redaction policy, and use an
absolute `cwd` inside `MILLRACE_E2E_ARTIFACT_ROOT`. The live-marked test stops
after accepted bounded preflight. It does not invoke a completed workflow and
does not establish official Plus live proof.

## Official Plus Millforge Proof

The maintained official Plus live rows are:

- `tests/e2e/test_simple_loop_millforge_live_proof.py`, which must reach
  `closed_successfully`;
- `tests/e2e/test_vendor_selection_millforge_live_proof.py`, which must reach
  the selected durable operator wait without approving a purchase.

These tests call a real model, use the credential named by the adapter config,
and can consume substantial time and tokens. They remain disabled without the
explicit runner, config, package, workspace, and finite limits below.

Install `millforge==0.1.0`, select the Plus `0.22.0` package root, and use a
separate adapter file for each row based on
[Millforge runner setup](../millforge-runner.md). In each file,
`workspace_root` must equal that row's fresh artifact root. The referenced
credential environment variable must already be populated.

Set the common live bounds:

```bash
export MILLRACE_E2E_ACTUAL_MODEL=1
export MILLRACE_E2E_RUNNER=millforge
export MILLRACE_E2E_PACKAGE_ROOT=/absolute/path/to/millrace_workflow_package
export MILLRACE_E2E_MAX_ADAPTER_TIMEOUT_SECONDS=3600
export MILLRACE_E2E_MAX_INPUT_BUNDLE_BYTES=65536
export MILLRACE_E2E_MAX_STDOUT_BYTES=131072
export MILLRACE_E2E_MAX_STDERR_DIAGNOSTIC_BYTES=16384
export MILLRACE_E2E_MAX_WORKFLOW_SECONDS=7200
export MILLRACE_E2E_MAX_TOTAL_SECONDS=14400
export MILLRACE_E2E_MAX_RETRIES=0
```

Run `simple_loop` with its exact artifact-root prefix:

```bash
export MILLRACE_E2E_ARTIFACT_ROOT="$MILLRACE_E2E_WORKSPACES_ROOT/e2e-mf-simple-loop-$(date -u +%Y%m%dT%H%M%SZ)"
export MILLRACE_E2E_ADAPTER_CONFIG=/absolute/path/to/simple-loop-millforge-adapter.json
export MILLRACE_E2E_WORKFLOW_FILTER=simple_loop
export MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW=8

PYTHONDONTWRITEBYTECODE=1 uv run --frozen --with millforge==0.1.0 \
  pytest -q tests/e2e/test_simple_loop_millforge_live_proof.py -m live_model
```

Run `vendor_selection` with its exact artifact-root prefix and exact 16-tick
requirement:

```bash
export MILLRACE_E2E_ARTIFACT_ROOT="$MILLRACE_E2E_WORKSPACES_ROOT/e2e-mf-vendor-selection-$(date -u +%Y%m%dT%H%M%SZ)"
export MILLRACE_E2E_ADAPTER_CONFIG=/absolute/path/to/vendor-selection-millforge-adapter.json
export MILLRACE_E2E_WORKFLOW_FILTER=vendor_selection
export MILLRACE_E2E_MAX_TICKS_PER_WORKFLOW=16

PYTHONDONTWRITEBYTECODE=1 uv run --frozen --with millforge==0.1.0 \
  pytest -q tests/e2e/test_vendor_selection_millforge_live_proof.py -m live_model
```

Both modules require literal selected Millforge authority and reject Codex or
fake evidence for these rows. The profile, selected workflow timeout, local
adapter timeout, and E2E ceilings remain separate; execution uses the lower
applicable limit.

## Reading Results

A live result distinguishes provider transport failure, adapter protocol
failure, runtime refusal, a legal durable operator wait, and completed workflow
state. Millforge returns candidate execution evidence. Millrace validates that
evidence and remains responsible for accepted routing, waits, and completion.

Keep each generated workspace and inspect its status, runs, traces, waits, and
artifacts before deleting it. See [Errors and refusals](../errors.md) when a row
does not reach its declared outcome.
