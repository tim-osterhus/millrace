# Millrace Runner Architecture

## Scope

This document describes the runner execution architecture implemented under `src/millrace_ai/runners/`.
The runtime contract stays:

- input: `StageRunRequest`
- output: `RunnerRawResult`

Use `docs/runtime/millrace-compiler-and-frozen-plans.md` and
`docs/runtime/millrace-modes-and-loops.md` for the compile-time surfaces that
freeze `runner_name`, `model_name`, and other stage-plan fields before runner
dispatch happens.

## Components

- `src/millrace_ai/runners/requests.py`
  - `StageRunRequest`, `RunnerRawResult`, and prompt-context rendering
- `src/millrace_ai/runners/normalization.py`
  - terminal extraction, failure mapping, and `StageResultEnvelope` normalization
- `src/millrace_ai/runners/base.py`
  - adapter protocol (`name`, `run(request)`)
- `src/millrace_ai/runners/registry.py`
  - mapping from runner name to adapter
- `src/millrace_ai/runners/dispatcher.py`
  - runtime-facing callable resolver
- `src/millrace_ai/runners/contracts.py`
  - invocation/completion artifact schemas
- `src/millrace_ai/runners/process.py`
  - subprocess helper with timeout/error mapping
- `src/millrace_ai/runners/adapters/codex_cli.py`
  - built-in Codex CLI adapter
- `src/millrace_ai/runner.py`
  - thin compatibility facade that preserves the legacy root import path

## Resolution Order

Runner name for a stage execution resolves in this order:

1. `StageRunRequest.runner_name`
2. `RuntimeConfig.runners.default_runner`
3. literal fallback `"codex_cli"`

Unknown names fail fast via `UnknownRunnerError`.

In practice, that means there are two distinct moments:

1. compile decides what runner name is attached to a frozen stage-plan
2. dispatch decides which adapter to execute from the resolved request

## Artifacts

Each stage run writes adapter artifacts into `run_dir`:

- `runner_prompt.<request_id>.md`
- `runner_invocation.<request_id>.json`
- `runner_stdout.<request_id>.txt`
- `runner_stderr.<request_id>.txt`
- `runner_completion.<request_id>.json`

This keeps execution diagnosable and preserves contracts for Phase 2 external shim migration.

## Codex Adapter Behavior

Codex adapter:

- builds a deterministic stage prompt from `StageRunRequest`
- shells out to configured Codex command/args
- captures stdout/stderr
- maps subprocess outcomes to `RunnerRawResult.exit_kind`:
  - `completed`
  - `timeout`
  - `runner_error`

Default config fields:

```toml
[runners]
default_runner = "codex_cli"

[runners.codex]
command = "codex"
args = ["exec"]
profile = "default"
permission_default = "basic"
# permission_by_stage = { planner = "basic", builder = "elevated" }
# permission_by_model = { "gpt-5.4" = "maximum" }
skip_git_repo_check = true
extra_config = []
```

Permission precedence:

1. stage override (`permission_by_stage`)
2. model override (`permission_by_model`)
3. `permission_default`

Codex permission mappings:

- `basic`: `--full-auto`
- `elevated`: `-c approval_policy="never" --sandbox danger-full-access`
- `maximum`: `--dangerously-bypass-approvals-and-sandbox`

## Phase 2 Compatibility

Phase 2 external shim adapter should preserve:

- the dispatcher registry seam
- invocation/completion artifact schema compatibility
- `StageRunRequest -> RunnerRawResult` runtime normalization boundary

This ensures swapping Codex in-process adapter for external Codex/Pi shim does not require runtime orchestration rewrites.
