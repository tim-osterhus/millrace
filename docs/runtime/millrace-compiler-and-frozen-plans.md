# Millrace Compiler And Frozen Plans

This document describes the current compile contract implemented by
`src/millrace_ai/compiler.py`.

The compiler is responsible for turning the selected runtime mode plus its
execution and planning legacy loop assets into one persisted frozen run plan
under the workspace state tree. It also emits a compiled graph-plan companion
built from the stage-kind registry and graph-loop assets.

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
3. fallback default `default_codex`

Today, the shipped baseline canonical mode is `default_codex`.
`standard_plain` remains accepted only as a compatibility alias that resolves to
`default_codex`.

## Built-In Asset Loading

The current compiler loads built-in mode and legacy loop assets through
`src/millrace_ai/assets/modes.py`.

For the graph plan, it also loads:

- stage kinds through `src/millrace_ai/assets/architecture.py`
- graph loops through `src/millrace_ai/assets/loop_graphs.py`

Current shipped asset ids:

- modes: `default_codex`, `default_pi`
- compatibility alias: `standard_plain -> default_codex`
- execution loop: `execution.standard`
- planning loop: `planning.standard`
- execution graph loop: `execution.standard`
- planning graph loop: `planning.standard`

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

The compiler freezes one authoritative `FrozenRunPlan` with these core fields:

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
loop for `default_codex` freezes a closure-target policy that dispatches the
`arbiter` stage when a root lineage drains cleanly.

The compiler also writes `FrozenGraphRunPlan` to `compiled_graph_plan.json`.
That graph plan contains:

- materialized execution and planning graph-loop ids
- per-node materialized entrypoint/skill/runner/model/timeout data
- raw graph `entry_nodes`
- raw graph transitions
- normalized `compiled_entries`
- normalized `compiled_completion_entry` for closure-target activation when the
  planning graph declares completion behavior
- normalized `compiled_transitions`
- normalized `compiled_resume_policies`
- normalized `compiled_threshold_policies`
- explicit graph terminal states
- graph-shaped completion behavior for the planning plane
- `authoritative_for_runtime_execution`
- `legacy_equivalence_ready_for_cutover`
- `legacy_equivalence_issues`

The runtime now executes claim activation, closure-target activation, and
post-stage routing from that graph plan. `compiled_plan.json` still remains the
frozen stage execution contract used to build stage requests, attach
entrypoints and skills, and resolve runner/model/timeout metadata. The
`legacy_equivalence_*` fields are now compatibility diagnostics: they record
whether the shipped graph plan still matches the historical legacy activation
and routing behavior for the selected config.

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

The compiler writes three canonical JSON artifacts under
`<workspace>/millrace-agents/state/`:

- `compiled_plan.json`
- `compiled_graph_plan.json`
- `compile_diagnostics.json`

`compiled_plan.json` stores the frozen stage execution plan.

`compiled_graph_plan.json` stores the runtime-authoritative graph control-flow
plan. It captures:

- materialized node execution contracts
- normalized intake entry surfaces
- normalized closure-target activation entry when present
- normalized transition tables
- compiled resume and threshold recovery policies
- explicit terminal-state semantics
- cutover/compatibility diagnostics for the shipped defaults

The compiled threshold policies are materialized against the effective recovery
config, so config values such as `max_fix_cycles`,
`max_troubleshoot_attempts_before_consult`, and `max_mechanic_attempts` are
encoded into the graph plan rather than being re-derived later at runtime.

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
- the shipped stage-kind and graph-loop assets can be materialized into the
  sidecar graph plan
- the workspace now has current compile diagnostics

It does not prove that a later stage run will succeed. It proves that the
runtime structure is valid enough to freeze.

## What `millrace compile show` Adds

`millrace compile show` performs the same compile-and-persist operation as
`millrace compile validate`, then prints the operator inspection surface from the
active frozen plan.

Today that includes:

- graph authority fields such as `graph_authoritative_for_runtime_execution`
  and `graph_legacy_equivalence_ready_for_cutover`
- graph intake entries and graph completion activation entries
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

- two canonical built-in modes: `default_codex`, `default_pi`
- one compatibility alias: `standard_plain -> default_codex`
- one built-in execution loop: `execution.standard`
- one built-in planning loop: `planning.standard`
- one built-in execution graph loop: `execution.standard`
- one built-in planning graph loop: `planning.standard`

That is why the compiler docs should stay concrete. They should explain the
actual shipped compile contract, not a hypothetical future extension model.

## Operator Implications

For operators, the compile step is the authoritative way to answer:

- which mode is active
- which loops are active
- whether the runtime-authoritative graph plan encoded the shipped
  recovery/resume/closure seams cleanly enough to report compatibility readiness
- whether backlog drain dispatches a completion stage and which one
- which entrypoints the runtime will use
- which stage-core skills and attached skills are present
- whether a config or asset change actually produced a new frozen plan

If you need legacy-oracle comparison events while debugging a graph control-flow
change, set `MILLRACE_ENABLE_GRAPH_SHADOW_VALIDATION=1` before running the
runtime. The runtime will then emit mismatch events when graph authority and
the preserved legacy oracle disagree.

If you change mode selection, stage config, or loop-linked assets, re-run
`millrace compile validate` or `millrace compile show` before assuming the
runtime is executing against the structure you intended.
