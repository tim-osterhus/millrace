# Millrace Ops Agent Manual

This document is for external agents acting as the dedicated operator of a
Millrace workspace.

## What Millrace Is

Millrace is a governed runtime for long-running agent work that needs durable
state, staged execution, deterministic handoffs, and recovery-aware operation.

Millrace is not the coding model itself. It wraps and governs coding sessions
run by raw agent harnesses such as Codex, Claude Code, or Aider.

In practice, Millrace adds:

- a compile step that freezes a mode and loop topology into a run plan
- file-backed runtime state under `<workspace>/millrace-agents/`
- staged execution and planning loops with typed terminal results
- persisted run artifacts and repair-oriented recovery paths
- runner dispatch that invokes a raw harness through a defined adapter contract

## What Millrace Is Not

Millrace is not:

- a replacement for raw agent harnesses across all tasks
- the best tool for one-shot greenfield coding or short bounded edits
- a general chat agent that should be dropped into arbitrary repos with no
  operator posture
- a substitute for thinking about whether the work actually needs governance

If the work fits comfortably in one session and does not need durable runtime
state, staged gates, or recovery routing, use the raw harness directly.

## Relationship To Raw Harnesses

Millrace should be used in conjunction with raw harnesses, not instead of them.

Think of the relationship this way:

- the raw harness does the local reasoning and code-editing work for one stage
- Millrace owns the queue, compiled plan, runtime state, stage progression,
  recovery routing, and persisted audit trail
- the ops agent decides when work should enter Millrace and how the runtime
  should be configured for the workspace

Millrace is useful when the harness alone is not enough because the job needs to
survive pauses, crashes, context loss, retries, or multi-stage validation.

## Required Operator Posture

Millrace is designed to be operated by a dedicated ops agent.

That ops agent should:

- treat the runtime as the source of truth for queue state and run state
- prefer supported CLI commands over direct mutation of runtime-owned files
- keep operator-authored content outcome-focused instead of embedding ad hoc
  stage-routing instructions into work items
- distinguish runtime-owned behavior from stage-owned reasoning
- route deeper technical questions into the runtime docs instead of improvising
  policy from memory

The ops agent should not behave like a generic one-shot coding agent inside the
Millrace repo. Its job is governance and operation first.

## When To Use Millrace

Use Millrace when one or more of these are true:

- the work spans multiple sessions or needs resumability
- durable queue state matters
- recovery behavior matters more than raw one-shot speed
- staged execution or planning gates should be enforced explicitly
- you need persisted run artifacts and diagnosable failure surfaces
- a dedicated ops agent is available to manage the workspace and intake flow

Typical examples:

- long-running implementation work that must survive interruption
- planning-to-execution pipelines where specs are compiled, handed off, and
  audited across multiple stages
- repair-sensitive work where a runtime failure should route into Mechanic or
  Troubleshooter rather than simply exiting

## When Not To Use Millrace

Do not use Millrace when:

- the task is small, bounded, and well served by a direct Codex or Claude Code
  session
- governance overhead is not justified
- no dedicated ops agent is available to operate the runtime intentionally
- the task is mostly exploratory and does not need durable orchestration

Millrace adds process. That process is valuable only when the work benefits from
runtime ownership.

## Operating Baseline

Millrace assumes:

- package namespace: `millrace_ai`
- installed CLI: `millrace`
- runtime workspace root: `<workspace>/millrace-agents/`
- default runtime config: `<workspace>/millrace-agents/millrace.toml`

During source development, use module form:

```bash
uv run --extra dev python -m millrace_ai <command>
```

In an installed environment, use CLI form:

```bash
millrace <command>
```

## Minimal Operator Workflow

Use the shortest truthful workflow that proves the workspace is healthy:

1. `millrace compile validate --workspace <workspace>`
2. `millrace status --workspace <workspace>`
3. `millrace queue ls --workspace <workspace>`
4. `millrace run once --workspace <workspace>` when it is safe to tick

From there, use:

- `millrace runs ls`
- `millrace runs show <RUN_ID>`
- `millrace queue add-task <path>`
- `millrace queue add-spec <path>`
- `millrace queue add-idea <path>`
- `millrace pause`
- `millrace resume`
- `millrace stop`

The complete command inventory lives in `docs/runtime/millrace-cli-reference.md`.

## Configuration And Deployment Guidance

As the ops agent:

- treat `millrace.toml` as the supported configuration surface
- configure runners, stage-level overrides, and workspace defaults through that
  file rather than inventing side channels
- assume the runtime owns content under `millrace-agents/`
- avoid direct edits to runtime-owned state or queue folders except where the
  documented CLI import surfaces intentionally accept queue input

### Codex Permission Baseline

Millrace intentionally defaults Codex execution to maximum permissions.

That is the shipped baseline because Millrace is for long-running autonomous
work. A more restrictive baseline makes stage execution less reliable and
creates avoidable operator friction without actually simplifying the runtime's
governance seams.

Permission resolution order is:

1. `runners.codex.permission_by_stage`
2. `runners.codex.permission_by_model`
3. `runners.codex.permission_default`

That means:

- use `permission_by_stage` when one stage needs a different posture than the
  rest of the runtime
- use `permission_by_model` when one model family needs a different posture
- use `permission_default` as the workspace-wide fallback

For new workspaces, bootstrap writes `permission_default = "maximum"` into the
generated `millrace.toml`.

For existing workspaces, Millrace preserves the current `millrace.toml` on
bootstrap/update. If an operator has already customized `permission_default`,
`permission_by_stage`, or `permission_by_model`, those choices are not
overwritten by deploying a newer Millrace version.

For deeper details, use:

- `docs/runtime/millrace-runtime-architecture.md`
- `docs/runtime/millrace-cli-reference.md`
- `docs/runtime/millrace-runner-architecture.md`
- `docs/runtime/millrace-runtime-error-codes.md`

## Recovery-Aware Behavior

If the runtime hands a failure into a recovery stage with a
`runtime_error_code`, treat that as a runtime-owned incident rather than a stage
failure invented by the agent.

When present, the primary evidence is:

- `runtime_error_report_path`
- `runtime_error_catalog_path`

Read the report first, then consult the error catalog if the code needs more
context. Do not improvise your own meaning for runtime error codes.

## Guardrails For External Ops Agents

- Do not invent new queue states, stage names, or terminal results.
- Do not claim that docs in `docs/skills/` are runtime-shipped assets.
- Do not position Millrace as replacing raw harnesses entirely.
- Do not widen the runtime contract in documentation just because a future
  extension seems plausible.

Operate Millrace as a governance layer over harness sessions, not as a
marketing abstraction over them.
