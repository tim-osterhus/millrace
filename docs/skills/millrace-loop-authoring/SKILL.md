# Millrace Loop Authoring Skill

Use this document when you are an external agent proposing or implementing
changes to Millrace loops, modes, stages, or entrypoint selection.

## Your Job

Your job is not to invent a better story for the runtime.

Your job is to make changes that remain compiler-valid, contract-valid, and
truthful to the runtime-owned boundaries already enforced by Millrace.

## Read These First

Before changing anything, load these source-of-truth files:

- `src/millrace_ai/contracts.py`
- `src/millrace_ai/compiler.py`
- `src/millrace_ai/assets/modes.py`
- `src/millrace_ai/assets/loops/execution/default.json`
- `src/millrace_ai/assets/loops/planning/default.json`
- `src/millrace_ai/assets/loops/learning/default.json`
- `src/millrace_ai/assets/modes/default_codex.json`
- `src/millrace_ai/assets/modes/default_pi.json`
- `src/millrace_ai/assets/modes/learning_codex.json`
- `src/millrace_ai/assets/modes/learning_pi.json`

If you are writing docs as part of the change, also read:

- `docs/runtime/millrace-compiler-and-frozen-plans.md`
- `docs/runtime/millrace-modes-and-loops.md`
- `docs/runtime/millrace-loop-authoring.md`

## Core Mental Model

Think in this order:

1. contracts
2. loop topology
3. mode selection
4. compiler freezing
5. runtime execution

Not the other way around.

Loops define stage topology and `terminal_results`.
Modes choose which plane loops are active through `loop_ids_by_plane` and what
stage maps, concurrency policy, and learning trigger rules apply.
The compiler freezes that into a compiled plan the runtime can execute.

## Non-Negotiable Guardrails

- Do not invent stage names that are not backed by the typed contracts.
- Do not invent terminal meanings in prose alone.
- Do not treat docs or skills as a place to define runtime-owned routing.
- Do not use `stage_entrypoint_overrides` casually; it is a constrained compile
  surface, not a free-form prompt switchboard.
- Do not describe advisory skills as if they own queue movement, retries, or
  status persistence.

Millrace has runtime-owned boundaries for a reason. Respect them.

## Compiler-Valid Authoring Checklist

When changing a loop, confirm all of the following:

- every stage belongs to the declared plane
- `entry_stage` is in `stages`
- `stages` are unique
- every edge source is in `stages`
- every `on_terminal_result` is legal for its source stage
- every edge sets exactly one of `target_stage` or `terminal_result`
- the loop includes at least one terminal path

When changing a mode, confirm all of the following:

- every loop id in `loop_ids_by_plane` exists
- execution and planning loop ids are present
- learning loop id is present only when the mode intentionally enables learning
- `stage_entrypoint_overrides` only references selected stages
- `stage_skill_additions` only references selected stages
- `stage_model_bindings` only references selected stages
- `stage_runner_bindings` only references selected stages
- `learning_trigger_rules` are present only when a learning loop is selected

## Runtime-Owned Vs Advisory

This distinction should guide every change.

Runtime-owned:

- queue transitions
- stage routing
- retry thresholds
- recovery escalation
- runtime status persistence

Advisory:

- entrypoint guidance
- stage-core skill posture
- optional skill additions
- external operator and authoring docs

If you catch yourself solving a runtime-owned problem by editing only docs,
skills, or prompt prose, you are probably editing the wrong layer.

## Safe Authoring Workflow

1. Change the asset or contract in the smallest truthful way.
2. Re-run compile inspection with `millrace compile validate` and
   `millrace compile show`.
3. Update tests that lock the changed contract.
4. Update runtime docs if the external contract changed.
5. Update external skill docs only if agents need new guidance.

## When To Stop

Stop and ask for clarification if:

- the desired change requires a new stage name or terminal result that does not
  exist in `src/millrace_ai/contracts.py`
- the new loop cannot be explained without changing runtime-owned routing rules
- the change depends on a plugin system or extension mechanism the runtime does
  not currently ship

Millrace loop authoring should stay concrete, compiler-valid, and anchored in
the runtime as it exists today.
