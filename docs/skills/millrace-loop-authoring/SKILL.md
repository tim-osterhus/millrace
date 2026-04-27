---
asset_type: skill
asset_id: millrace-loop-authoring
version: 1
description: External authoring skill for changing Millrace loop, stage-kind, graph, mode, entrypoint, and compiled-plan surfaces safely.
advisory_only: true
capability_type: documentation
forbidden_claims:
  - runtime_authority
  - queue_mutation
  - routing_by_prompt
  - terminal_results_by_prose
  - uncompiled_loop_changes
---

# Millrace Loop Authoring

Use this skill when you are proposing or implementing changes to Millrace
loops, graph loops, stage kinds, modes, stage entrypoint selection, or
compiled-plan behavior.

This is an advisory authoring guide. It does not define runtime behavior.

## Your Job

Keep changes compiler-valid, contract-valid, and truthful to the runtime-owned
boundaries enforced by Millrace.

Do not invent a better story in prose. Make the runtime contract real, compile
it, inspect it, and test it.

## Read These First

Before changing anything, load the relevant source-of-truth files:

- `src/millrace_ai/contracts.py`
- `src/millrace_ai/architecture/stage_kinds.py`
- `src/millrace_ai/architecture/loop_graphs.py`
- `src/millrace_ai/architecture/materialization.py`
- `src/millrace_ai/compiler.py`
- `src/millrace_ai/assets/modes.py`
- `src/millrace_ai/assets/loops/execution/default.json`
- `src/millrace_ai/assets/loops/planning/default.json`
- `src/millrace_ai/assets/loops/learning/default.json`
- `src/millrace_ai/assets/graphs/execution/standard.json`
- `src/millrace_ai/assets/graphs/planning/standard.json`
- `src/millrace_ai/assets/graphs/learning/standard.json`
- `src/millrace_ai/assets/registry/stage_kinds/`
- `src/millrace_ai/assets/modes/default_codex.json`
- `src/millrace_ai/assets/modes/default_pi.json`
- `src/millrace_ai/assets/modes/learning_codex.json`
- `src/millrace_ai/assets/modes/learning_pi.json`

If you are writing docs as part of the change, also read:

- `docs/runtime/millrace-compiler-and-frozen-plans.md`
- `docs/runtime/millrace-modes-and-loops.md`
- `docs/runtime/millrace-loop-authoring.md`
- `docs/adr/0005-compiled-graph-plan-as-runtime-authority.md`

## Core Mental Model

Think in this order:

1. contracts and stage kinds
2. graph-loop topology
3. legacy loop compatibility surface
4. mode selection and bindings
5. compiler materialization
6. runtime execution from `compiled_plan.json`

The current runtime control-flow authority is the compiled graph plan persisted
at `millrace-agents/state/compiled_plan.json`.

Stage-kind assets define legal stage identity, plane membership, legal
outcomes, default entrypoints, required stage-core skills, and request-binding
fields. Graph-loop assets define node topology, entry nodes, outcome edges,
terminal states, completion behavior, and learning activation. Modes choose
active plane loops through `loop_ids_by_plane` and freeze selected entrypoints,
skill additions, model bindings, runner bindings, concurrency policy, and
learning trigger rules.

Legacy loop JSON under `src/millrace_ai/assets/loops/` remains a shipped
inspection and compatibility surface. When you change a shipped default loop
shape, keep the legacy loop surface and graph-loop surface aligned unless the
task explicitly says otherwise and the docs explain the reason.

## When To Author A Custom Loop

Use a custom loop when the desired behavior requires durable runtime-owned
stage topology or activation rules, such as:

- a new sequence of execution, planning, or learning stages
- different terminal routing or recovery paths
- a new stage kind with distinct legal outcomes or request fields
- a mode that selects different loops, entrypoints, runners, models, or
  learning trigger policy

Do not use a custom loop for:

- ordinary task instructions that belong in a work item
- optional skill advice that does not need to own routing
- one-off operator preferences
- prompt prose that claims queue movement, retries, recovery, or terminal
  semantics without compiled support

Workspace-specific custom assets should live with the owning workspace or
project until they are intentionally promoted into packaged Millrace defaults.
Packaged core assets under `src/millrace_ai/assets/` should stay general and
tested.

## Required Asset Set

For a new runtime shape, check which assets must change:

- Stage kind: add or update `src/millrace_ai/assets/registry/stage_kinds/` when
  adding stages, legal outcomes, default entrypoints, required stage-core
  skills, or request fields.
- Graph loop: add or update `src/millrace_ai/assets/graphs/` when changing
  runtime control flow, entry nodes, terminal states, completion behavior, or
  learning activation.
- Legacy loop: add or update `src/millrace_ai/assets/loops/` when touching a
  shipped/default loop surface that should remain inspectable and compatible.
- Mode: add or update `src/millrace_ai/assets/modes/` when selecting loops or
  changing per-stage entrypoint, skill, runner, model, timeout, concurrency, or
  learning-trigger policy.
- Entrypoints and skills: add or update packaged entrypoints and required
  stage-core skills when a new stage kind needs runnable instructions.
- Runtime docs and tests: update them when the external contract or packaged
  behavior changes.

## Non-Negotiable Guardrails

- Do not invent stage names that are not backed by typed contracts and
  stage-kind assets.
- Do not invent terminal meanings in prose alone.
- Do not treat docs or skills as a place to define runtime-owned routing.
- Do not use `stage_entrypoint_overrides` as a generic prompt switchboard.
- Do not describe advisory skills as if they own queue movement, retries,
  status persistence, or compiled-plan state.
- Do not rely on uncompiled asset edits. Recompile and inspect the plan.

## Compiler-Valid Authoring Checklist

When changing a legacy loop, confirm:

- every stage belongs to the declared plane
- `entry_stage` is in `stages`
- `stages` are unique
- every edge source is in `stages`
- every `on_terminal_result` is legal for its source stage
- every edge sets exactly one of `target_stage` or `terminal_result`
- the loop includes at least one terminal path

When changing a stage kind or graph loop, confirm:

- every graph node references a declared stage kind
- every edge source and target node exists
- every edge outcome is legal for the source node stage kind
- every terminal state is explicit
- entry nodes are correct for the plane and intake type
- planning completion behavior targets only a closure-role stage kind
- learning intake is represented through `learning_request`
- request bindings only reference fields allowed by the stage kind
- default entrypoints and required stage-core skills are real packaged assets

When changing a mode, confirm:

- every loop id in `loop_ids_by_plane` exists
- execution and planning loop ids are present
- learning loop id is present only when the mode intentionally enables learning
- `stage_entrypoint_overrides` only references selected stages
- `stage_skill_additions` only references selected stages
- `stage_model_bindings` only references selected stages
- `stage_runner_bindings` only references selected stages
- concurrency policy only references selected planes and stages
- `learning_trigger_rules` are present only when a learning loop is selected
- each learning trigger target is a valid selected learning stage

## Runtime-Owned Vs Advisory

Runtime-owned behavior includes:

- queue transitions
- stage routing
- retry thresholds
- recovery escalation
- terminal result semantics
- persisted runtime status
- compiled-plan identity and currentness

Advisory content includes:

- entrypoint guidance
- stage-core skill posture
- optional skill additions
- external operator and authoring docs

If you are solving a runtime-owned problem by editing only docs, skills, or
prompt prose, you are editing the wrong layer.

## Safe Authoring Workflow

1. Decide whether the change is workspace-local or packaged core behavior.
2. Add or update contracts and stage-kind assets before using new stages,
   outcomes, request fields, entrypoints, or required skills.
3. Add or update the graph-loop asset that should own runtime control flow.
4. Keep legacy loop JSON aligned when changing shipped/default loop topology.
5. Add or update the mode that selects the loops and freezes bindings.
6. Add or update entrypoint and required stage-core skill assets for new stages.
7. Run `millrace compile validate` against the target workspace and mode.
8. Run `millrace compile show` and inspect the selected loops, stage plans,
   entrypoints, skills, runners, models, timeouts, and learning rules.
9. Inspect `millrace-agents/state/compiled_plan.json` whenever stage kinds,
   graph loops, materialization, or runtime routing changed.
10. Update tests and runtime docs that lock or describe the changed contract.

## Tests To Touch

At minimum, review and update as needed:

- `tests/assets/test_modes.py`
- `tests/assets/test_stage_kinds.py`
- `tests/assets/test_loop_graphs.py`
- `tests/integration/test_compiler.py`
- `tests/integration/test_single_compiled_plan.py`
- `tests/assets/test_entrypoints.py`
- `tests/assets/test_packaging_runtime_assets.py`

If runtime consumers changed, also inspect tests around runtime activation,
completion behavior, learning triggers, stage requests, reconciliation, and
runner request construction.

## When To Stop

Stop and ask for clarification if:

- the desired change requires a new stage, outcome, or terminal state but the
  runtime contract is not being changed
- the new loop cannot be explained as contracts, graph topology, mode
  selection, and compiled-plan materialization
- the change depends on a plugin or extension mechanism the runtime does not
  currently ship
- the user wants packaged core behavior but the change is clearly
  workspace-specific

Millrace loop authoring should stay concrete, compiler-valid, and anchored in
the runtime as it exists today.
