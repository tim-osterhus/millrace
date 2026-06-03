# Millrace Runner Architecture

## Scope

This document describes the runner execution architecture implemented under `src/millrace_ai/runners/`.
The runtime contract stays:

- input: `StageRunRequest`
- output: `RunnerRawResult`

Use `docs/runtime/millrace-compiler-and-frozen-plans.md` and
`docs/runtime/millrace-modes-and-loops.md` for the compile-time surfaces that
freeze `runner_name`, `model_name`, and other compiled node fields before runner
dispatch happens.
Those compile-time surfaces now also define the identity that runtime requests
and run inspection carry forward: `mode_id`, `compiled_plan_id`, `node_id`, and
`stage_kind_id`.
They also carry `model_assignment_alias_id` and `model_assignment_source` when
the compiler selected model/depth through a runtime config alias.

## Components

- `src/millrace_ai/runners/requests.py`
  - `StageRunRequest`, `RunnerRawResult`, and prompt-context rendering
- `src/millrace_ai/runners/normalization/`
  - `StageResultEnvelope` normalization facade plus focused helpers for terminal
    parsing, artifact safety, failure mapping, terminal-result mapping, and
    request provenance preservation
- `src/millrace_ai/runners/base.py`
  - adapter protocol (`name`, contextual capability support, `run(request)`)
- `src/millrace_ai/runners/registry.py`
  - mapping from runner name to adapter
- `src/millrace_ai/runners/dispatcher.py`
  - runtime-facing callable resolver
- `src/millrace_ai/runners/contracts.py`
  - invocation/completion artifact schemas
- `src/millrace_ai/runners/process.py`
  - subprocess helper with timeout/error mapping
- `src/millrace_ai/runners/adapters/_prompting.py`
  - shared Millrace-owned stage prompt construction
- `src/millrace_ai/runners/adapters/codex_cli.py`
  - public built-in Codex CLI adapter class and run orchestration
- `src/millrace_ai/runners/adapters/codex_cli_command.py`
  - Codex CLI command construction, permission flag resolution, model/profile flags, and model reasoning-effort config
- `src/millrace_ai/runners/adapters/codex_cli_artifacts.py`
  - Codex runner artifact paths, event-log persistence, last-message stdout materialization, and timeout-marker reconciliation
- `src/millrace_ai/runners/adapters/codex_cli_tokens.py`
  - token usage extraction from Codex JSONL event payloads
- `src/millrace_ai/runners/adapters/pi_rpc.py`
  - built-in Pi RPC adapter
- `src/millrace_ai/runners/adapters/pi_rpc_client.py`
  - focused JSONL RPC transport used by the Pi adapter
- `src/millrace_ai/runner.py`
  - thin compatibility facade that preserves the legacy root import path

## Resolution Order

Runner name for a stage execution resolves in this order:

1. `StageRunRequest.runner_name`
2. `RuntimeConfig.runners.default_runner`
3. literal fallback `"codex_cli"`

Unknown names fail fast via `UnknownRunnerError`.

In practice, that means there are two distinct moments:

1. compile decides what runner name is attached to a compiled node binding
2. dispatch decides which adapter to execute from the resolved request

The same split applies to runtime identity:

1. compile freezes node/stage-kind identity into the compiled graph
2. runtime stage-request construction copies that identity into every
   `StageRunRequest`

Compile first freezes the runner-neutral `thinking_level` node binding from
graph-loop node defaults, `stages.<stage>.thinking_level`, the legacy
`stages.<stage>.model_reasoning_effort` alias, or mode-level
`stage_thinking_bindings`. It then applies `model_assignment` aliases as the
final compiler overlay. A missing value or explicit mode `null` means no
compiled stage override before aliasing. The Codex adapter passes the selected
value as
`-c model_reasoning_effort="<value>"` after generic
`runners.codex.extra_config`; the Pi adapter passes it as `--thinking <value>`.
When no compiled value exists, adapters may still use their runner-global
defaults, such as `runners.codex.model_reasoning_effort` or
`runners.pi.thinking`.

Compile also freezes execution capability grants onto each node. Runtime
dispatch evaluates those grants before runner invocation, then passes the
satisfied grant set and runner support decisions through `StageRunRequest`.

The shipped canonical modes make that explicit:

- `default_codex` binds every shipped stage to `codex_cli`
- `default_pi` binds every shipped stage to `pi_rpc`
- `learning_codex` binds execution, planning, and learning stages to
  `codex_cli`
- `efficient_learning_mixed` binds the same standard learning topology to a
  mixed Codex/Pi runner profile and applies a mode-local model/depth alias
  profile. DeepSeek stages use `pi_rpc`; Codex stages use `codex_cli`.
- `learning_pi` binds execution, planning, and learning stages to `pi_rpc`
- `default_codex_integrated` and `learning_codex_integrated` bind Codex stages
  while selecting the quality-first `execution.with_integrator` loop
- `blueprint_codex` binds Codex stages while selecting the Blueprint Planning
  loop and standard execution
- `blueprint_learning_codex` binds Codex stages while selecting the Blueprint
  Planning loop, standard execution, and the Learning plane
- `standard_plain` remains accepted only as a compatibility alias for
  `default_codex`

## Compiled Identity In Requests And Inspection

Runner dispatch is no longer just "run stage X." The runtime carries compiled
identity through the request and result path so operators can inspect exactly
which frozen node contract produced a run.

`StageRunRequest` now carries compiled identity such as:

- `compiled_plan_id`
- `mode_id`
- `node_id`
- `stage_kind_id`
- `model_assignment_alias_id`
- `model_assignment_source`

`stage` and `stage_kind_id` are intentionally different identities:

- `stage_kind_id` is the graph-authoring and diagnostics identity.
- `stage` is the runtime dispatch/normalization identity from compiled
  `runtime_stage`.

Compatibility behavior for older compiled plans keeps canonical nodes stable by
backfilling missing `runtime_stage` from `stage_kind_id`; noncanonical stage
kinds without `runtime_stage` fail with an explicit compatibility error before
dispatch.

It also carries the active node's `execution_capability_grants` and any
`capability_support_decisions` produced by the runtime gate. The prompt context
renders these as structured, prompt-safe summaries so stages can see whether a
grant is enforced, advisory, denied, unsupported, or approval-gated.

Every runtime-built request also gets a deterministic request-context render
plan. The renderer writes:

- `context/context.json`: machine-readable context bundle
- `context/prompt_context.md`: prompt-safe rendered context included by the
  shared prompt builder
- `context/render_manifest.json`: visible refs, redacted operator-only refs,
  omitted providers, and content hash evidence

`StageRunRequest` carries the context profile id, render plan id, bundle path,
rendered prompt-context path, and visible context artifact refs. Normalization
persists those fields into stage-result metadata so `millrace runs show` can
explain which context the stage actually saw.

Request-context authority follows graph/stage-kind-declared compiled node data
first, then request-level explicit fields. Missing or unknown profile/render-plan
ids are treated as operator-facing stale-plan compatibility errors with
recompile/update guidance; runtime no longer infers authority from
`<stage_kind_id>.default`.

Default running markers, legal terminal markers, and fallback
`allowed_result_classes_by_outcome` values are derived from
`src/millrace_ai/contracts/stage_metadata.py`. Compiled node plans can still
provide explicit values, but runner prompts and normalization no longer carry a
separate hard-coded copy of stage legality.

Normalization preserves that identity into the persisted stage-result metadata,
and `millrace runs show` surfaces it at both the run level and the per-stage
level.

That gives operators a direct line from:

- the persisted compiled plan
- to the runtime request that executed
- to the normalized stage result inspected after the run

## Artifacts

Each stage run writes adapter artifacts into `run_dir`:

- `runner_prompt.<request_id>.md`
- `runner_invocation.<request_id>.json`
- `runner_stdout.<request_id>.txt`
- `runner_stderr.<request_id>.txt`
- `runner_completion.<request_id>.json`

This keeps execution diagnosable and preserves contracts for Phase 2 external shim migration.

Run inspection also reads compiled identity back out of stage-result artifacts.
Operator-facing `runs show` output now carries:

- run-level `compiled_plan_id`
- run-level `mode_id`
- per-stage `compiled_plan_id`
- per-stage `mode_id`
- per-stage `node_id`
- per-stage `stage_kind_id`
- per-stage `request_kind`
- per-stage model-assignment alias id and source when present
- per-stage request-context profile, render plan, context bundle, and visible
  context refs
- per-stage `failure_origin` when a runtime/runner boundary can classify one
- per-stage execution capability grant summaries
- per-stage runner support summaries

That makes run inspection line up with the compiled plan and the runtime status
surface instead of only showing stage names.

## Codex Adapter Behavior

Codex adapter:

- builds a deterministic stage prompt from `StageRunRequest`
- evaluates execution capability support from the current Codex permission
  posture
- builds command/permission flags through `codex_cli_command.py`
- shells out to configured Codex command/args
- captures stdout/stderr
- persists invocation/completion/event-log artifacts through runner-owned artifact helpers
- extracts token usage from Codex event payloads through `codex_cli_tokens.py`
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
permission_default = "maximum"
# permission_by_stage = { planner = "elevated", builder = "maximum" }
# permission_by_model = { "gpt-5.4" = "maximum" }
skip_git_repo_check = true
extra_config = []
```

Millrace intentionally ships with maximum Codex permissions as the default
operator posture. The framework is meant for long-running autonomous execution,
and forcing the baseline into a more restrictive permission mode makes the
runtime less reliable without adding meaningful governance. Operators can still
reduce permissions deliberately through `permission_default`,
`permission_by_stage`, or `permission_by_model`.

Because `maximum` grants broad local authority, the Codex adapter reports many
filesystem, network, package, shell, and git boundaries as advisory rather than
pretending Millrace can enforce them at the OS boundary.

Permission precedence:

1. stage override (`permission_by_stage`)
2. model override (`permission_by_model`)
3. `permission_default`

Bootstrap/update behavior:

- new workspaces get an explicit `permission_default = "maximum"` in the
  generated `millrace.toml`
- existing workspace configs are preserved; bootstrap does not overwrite a
  customized `millrace.toml`

Codex permission mappings:

- `basic`: `--full-auto`
- `elevated`: `-c approval_policy="never" --sandbox danger-full-access`
- `maximum`: `--dangerously-bypass-approvals-and-sandbox`

## Pi Adapter Behavior

Pi adapter:

- shells out to `pi --mode rpc --no-session`
- evaluates execution capability support conservatively before dispatch
- sends the same Millrace-owned stage prompt contract used by the Codex path
- persists streamed Pi events to `runner_events.<request_id>.jsonl` only for
  failed runs by default, or for every run when `event_log_policy = "full"`
- strips verbose `message_update` snapshots from persisted event logs because
  they duplicate the final assistant text that is already written to
  `runner_stdout.<request_id>.txt`
- materializes final assistant text into `runner_stdout.<request_id>.txt`
- queries `get_last_assistant_text` and `get_session_stats` after `agent_end`
- uses Millrace timeout governance, including RPC `abort` plus bounded hard-kill
  fallback

Default Pi config fields:

```toml
[runners.pi]
command = "pi"
args = []
disable_context_files = true
disable_skills = true
event_log_policy = "failure_full"
# optional default used only when a stage request has no compiled thinking_level
# thinking = "medium"
```

Pi can auto-discover `AGENTS.md` / `CLAUDE.md` context files and Pi-native
skills on its own. Millrace disables both by default in the built-in Pi posture
so `default_pi` stays deterministic against the same stage-entrypoint contract
as `default_codex`.

The default `failure_full` policy keeps successful runs closer to the Codex
artifact footprint while still preserving the raw RPC trace for timeouts,
provider failures, and other PI-side debugging cases.

`runners.default_runner` remains a generic runtime fallback. It is not the
primary selector for the shipped harness presets.

## Phase 2 Compatibility

Phase 2 external shim adapter should preserve:

- the dispatcher registry seam
- invocation/completion artifact schema compatibility
- `StageRunRequest -> RunnerRawResult` runtime normalization boundary

This ensures swapping Codex in-process adapter for external Codex/Pi shim does not require runtime orchestration rewrites.
