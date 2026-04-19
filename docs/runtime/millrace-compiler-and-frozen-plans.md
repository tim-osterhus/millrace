# Millrace Compiler And Frozen Plans

This document describes the current compile contract implemented by
`src/millrace_ai/compiler.py`.

The compiler is responsible for turning the selected runtime mode plus its
execution and planning loop assets into one persisted frozen run plan under the
workspace state tree.

## Why The Compile Step Exists

Millrace does not execute directly from loose mode, loop, and config inputs on
every stage handoff.

Instead, it compiles those inputs into a frozen plan so the runtime can operate
against one concrete structure for the current workspace state:

- selected mode id
- selected execution loop id
- selected planning loop id
- one frozen stage-plan entry per stage in those loops
- one frozen `completion_behavior` policy when the selected loops declare one
- one deterministic `compiled_plan_id`

That compile step is what lets later runtime state, run artifacts, and operator
inspection refer to the same plan identity.

## Mode Resolution Order

The compiler resolves the active mode in this order:

1. explicit `requested_mode_id` from the CLI command
2. `runtime.default_mode` from `millrace.toml`
3. fallback default `standard_plain`

Today, the shipped baseline mode is `standard_plain`.

## Built-In Asset Loading

The current compiler loads built-in mode and loop assets through
`src/millrace_ai/assets/modes.py`.

Current shipped asset ids:

- mode: `standard_plain`
- execution loop: `execution.standard`
- planning loop: `planning.standard`

The mode bundle load step validates all of the following before the runtime gets
a frozen plan:

- the requested mode id exists
- the mode JSON validates as `ModeDefinition`
- the referenced execution and planning loop ids exist
- the loop JSON validates as `LoopConfigDefinition`
- the execution loop declares `plane = execution`
- the planning loop declares `plane = planning`
- all shipped modes point at the same execution/planning graph

This is a built-in asset contract today, not a generalized plugin system.

## What Gets Frozen

The compiler freezes one `FrozenRunPlan` with these core fields:

- `compiled_plan_id`
- `mode_id`
- `execution_loop_id`
- `planning_loop_id`
- `stage_plans`
- `completion_behavior`
- `compiled_at`
- `source_refs`

Each stage-plan is a `FrozenStagePlan` with the runtime fields the engine needs
later:

- `stage`
- `plane`
- `entrypoint_path`
- `entrypoint_contract_id`
- `required_skills`
- `attached_skill_additions`
- `runner_name`
- `model_name`
- `timeout_seconds`

The stage-plan freeze is where the compiler resolves the final per-stage
execution contract from loop, mode, and config inputs.

`completion_behavior` is where the compiler freezes backlog-drain semantics that
materially affect runtime control flow. In the shipped baseline, the planning
loop for `standard_plain` freezes a closure-target policy that dispatches the
`arbiter` stage when a root lineage drains cleanly.

## Stage-Plan Freezing Rules

For each stage in the selected execution and planning loops, the compiler:

1. validates that all mode stage maps refer only to stages present in the
   selected loops
2. resolves `entrypoint_path`
3. resolves `runner_name`
4. resolves `model_name`
5. resolves `timeout_seconds`
6. attaches required stage-core skills and any mode-level skill additions

Entrypoint resolution works like this:

- if the mode supplies a stage entrypoint override, it must be a relative
  `entrypoints/.../*.md` path
- otherwise the compiler uses the default packaged path:
  `entrypoints/<plane>/<stage>.md`

Runner and model resolution work like this:

- mode-level stage bindings win when present
- otherwise stage config from `millrace.toml` is used
- otherwise the field remains unset in the stage-plan and later runtime
  resolution falls back to the runner subsystem defaults

Timeout resolution works like this:

- stage config timeout wins when present
- otherwise the compile default is `3600` seconds

## `compiled_plan_id`

The compiler builds `compiled_plan_id` deterministically from:

- `mode_id`
- `execution_loop_id`
- `planning_loop_id`
- the serialized `completion_behavior`
- the serialized `stage_plans`

The current shape is:

```text
plan-<mode_id>-<12-char-sha256-prefix>
```

That means two compilations with the same effective structure produce the same
plan id even if they happen at different times.

## Persisted Compile Artifacts

The compiler writes two canonical JSON artifacts under
`<workspace>/millrace-agents/state/`:

- `compiled_plan.json`
- `compile_diagnostics.json`

`compiled_plan.json` stores the active frozen plan.

`compile_diagnostics.json` stores the latest compile result with:

- `ok`
- `mode_id`
- `errors`
- `warnings`
- `emitted_at`

The runtime snapshot later points back at the compiled plan through
`compiled_plan_id` and `compiled_plan_path`.

## Failure Policy And Last-Known-Good Behavior

The compile failure policy is narrow and important:

- the compiler always writes fresh diagnostics
- a failed compile does not overwrite the existing `compiled_plan.json`
- if a valid previous plan exists, the compiler returns it as the
  last-known-good active plan
- if no valid previous plan exists, `active_plan` is `None`

This is why a failed recompile can still leave the workspace operating against a
previous valid plan while surfacing the new compile error clearly in diagnostics.

In other words:

- diagnostics are always current
- the frozen plan changes only on successful compile

## What `millrace compile validate` Proves

`millrace compile validate` loads the effective runtime config, resolves the
active mode, compiles the frozen plan, persists the canonical compile artifacts,
and prints compile diagnostics.

It proves:

- the selected mode can be resolved
- the referenced built-in loop assets can be loaded and validated
- all mode stage maps stay inside the selected loops
- all stage-plans can be frozen successfully
- the workspace now has current compile diagnostics

It does not prove that a later stage run will succeed. It proves that the
runtime structure is valid enough to freeze.

## What `millrace compile show` Adds

`millrace compile show` performs the same compile-and-persist operation as
`millrace compile validate`, then prints the operator inspection surface from the
active frozen plan.

Today that includes:

- `compiled_plan_id`
- `execution_loop_id`
- `planning_loop_id`
- frozen `completion_behavior` fields such as `stage`, `request_kind`, and completion terminals
- each stage as `<plane>.<stage>`
- `entrypoint_path`
- `required_skills`
- `attached_skills`

Use `compile show` when you need to inspect the actual frozen stage-plan rather
than only asking whether the compile was valid.

## Current Shipped Baseline

The current shipped baseline is intentionally small:

- one built-in mode: `standard_plain`
- one built-in execution loop: `execution.standard`
- one built-in planning loop: `planning.standard`

That is why the compiler docs should stay concrete. They should explain the
actual shipped compile contract, not a hypothetical future extension model.

## Operator Implications

For operators, the compile step is the authoritative way to answer:

- which mode is active
- which loops are active
- whether backlog drain dispatches a completion stage and which one
- which entrypoints the runtime will use
- which stage-core skills and attached skills are present
- whether a config or asset change actually produced a new frozen plan

If you change mode selection, stage config, or loop-linked assets, re-run
`millrace compile validate` or `millrace compile show` before assuming the
runtime is executing against the structure you intended.
