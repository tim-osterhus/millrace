# Millrace Compiler And Compiled Plans

This document describes the current compile contract implemented by
`src/millrace_ai/compiler.py`.

The compiler is responsible for turning the selected runtime mode plus its
execution and planning graph loops into one persisted compiled run plan under
the workspace state tree.

## Why The Compile Step Exists

Millrace does not execute directly from loose mode, loop, and config inputs on
every stage handoff.

Instead, it compiles those inputs into a single plan so the runtime can operate
against one concrete structure for the current workspace state:

- selected mode id
- selected execution loop id
- selected planning loop id
- one materialized node binding per stage-kind node in those loops
- one compiled completion-behavior policy when the planning loop declares one
- one deterministic `compiled_plan_id`

That compile step is what lets later runtime state, run artifacts, and operator
inspection refer to the same plan identity.

## Mode Resolution Order

The compiler resolves the active mode in this order:

1. explicit `requested_mode_id` from the CLI command
2. `runtime.default_mode` from `millrace.toml`
3. fallback default `default_codex`

Today, the shipped baseline canonical mode is `default_codex`.
`standard_plain` remains accepted only as a compatibility alias that resolves to
`default_codex`.

## Built-In Asset Loading

The current compiler loads built-in mode assets through
`src/millrace_ai/assets/modes.py`, stage kinds through
`src/millrace_ai/assets/architecture.py`, and graph loops through
`src/millrace_ai/assets/loop_graphs.py`.

Current shipped asset ids:

- modes: `default_codex`, `default_pi`, `skills_pipeline_codex`
- compatibility alias: `standard_plain -> default_codex`
- execution loop: `execution.standard`
- planning loop: `planning.standard`

The mode and graph-loop load step validates all of the following before the
runtime gets a compiled plan:

- the requested mode id exists
- the mode JSON validates as `ModeDefinition`
- the referenced execution and planning graph-loop ids exist
- the graph-loop JSON validates as `GraphLoopDefinition`
- the execution graph-loop declares `plane = execution`
- the planning graph-loop declares `plane = planning`

This is a built-in asset contract today, not a generalized plugin system.

## What Gets Compiled

The compiler materializes one authoritative `CompiledRunPlan` with these core
fields:

- `compiled_plan_id`
- `mode_id`
- `execution_loop_id`
- `planning_loop_id`
- `execution_graph`
- `planning_graph`
- `compiled_at`
- `source_refs`

Within each graph plane, node plans carry the runtime fields the engine needs
later:

- `node_id`
- `plane`
- `entrypoint_path`
- `entrypoint_contract_id`
- `required_skill_paths`
- `attached_skill_additions`
- `runner_name`
- `model_name`
- `timeout_seconds`

The compiled node materialization step is where the compiler resolves the final
per-node execution contract from graph-loop, mode, and config inputs.

The planning graph `completion_behavior` is where the compiler freezes backlog-drain semantics that
materially affect runtime control flow. In the shipped baseline, the planning
loop for `default_codex` freezes a closure-target policy that dispatches the
`arbiter` stage when a root lineage drains cleanly.

## Node Materialization Rules

For each node in the selected execution and planning graph loops, the compiler:

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
- otherwise the compiler uses the stage kind or graph node default path

Runner and model resolution work like this:

- mode-level stage bindings win when present
- otherwise stage config from `millrace.toml` is used
- otherwise the field remains unset in the node plan and later runtime
  resolution falls back to the runner subsystem defaults

Timeout resolution works like this:

- stage config timeout wins when present
- otherwise the compile default is `3600` seconds

## `compiled_plan_id`

The compiler builds `compiled_plan_id` deterministically from:

- `mode_id`
- `execution_loop_id`
- `planning_loop_id`
- the serialized execution graph
- the serialized planning graph

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

`compiled_plan.json` stores the runtime-authoritative compiled run plan. It captures:

- materialized node execution contracts
- entrypoint contract ids for those node execution contracts
- normalized intake entry surfaces
- normalized closure-target activation entry when present
- normalized transition tables
- compiled resume and threshold recovery policies
- explicit terminal-state semantics
The compiled threshold policies are materialized against the effective recovery
config, so config values such as `max_fix_cycles`,
`max_troubleshoot_attempts_before_consult`, and `max_mechanic_attempts` are
encoded into the compiled plan rather than being re-derived later at runtime.
The runtime also builds stage requests from these graph node plans, so
entrypoint paths, contract ids, required skills, attached skills, runner/model
bindings, and timeout values now come directly from the compiled plan.

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
- the referenced built-in graph-loop assets can be loaded and validated
- all mode stage maps stay inside the selected graph loops
- all node bindings can be materialized successfully
- the workspace now has current compile diagnostics

It does not prove that a later stage run will succeed. It proves that the
runtime structure is valid enough to freeze.

## What `millrace compile show` Adds

`millrace compile show` performs the same compile-and-persist operation as
`millrace compile validate`, then prints the operator inspection surface from the
active frozen plan.

Today that includes:

- intake entries and completion activation entries
- node request-binding surfaces including `entrypoint_path`,
  `entrypoint_contract_id`, `required_skills`, `attached_skills`,
  `runner_name`, `model_name`, and `timeout_seconds`
- `compiled_plan_id`
- `execution_loop_id`
- `planning_loop_id`
- compiled `completion_behavior` fields such as `request_kind` and completion terminals
- each node as `<plane>.<node_id>`
- `entrypoint_path`
- `required_skills`
- `attached_skills`

Use `compile show` when you need to inspect the actual compiled runtime plan
rather than only asking whether the compile was valid.

## Current Shipped Baseline

The current shipped baseline is intentionally small:

- two canonical built-in modes: `default_codex`, `default_pi`
- one specialized pipeline mode: `skills_pipeline_codex`
- one compatibility alias: `standard_plain -> default_codex`
- one built-in execution loop: `execution.standard`
- one built-in planning loop: `planning.standard`
That is why the compiler docs should stay concrete. They should explain the
actual shipped compile contract, not a hypothetical future extension model.

## Operator Implications

For operators, the compile step is the authoritative way to answer:

- which mode is active
- which loops are active
- which node-bound entrypoints, skills, runner/model bindings, and timeouts the
  runtime will actually use
- whether backlog drain dispatches a completion stage and which one
- whether a config or asset change actually produced a new compiled plan

If you change mode selection, stage config, or loop-linked assets, re-run
`millrace compile validate` or `millrace compile show` before assuming the
runtime is executing against the structure you intended.
