# ADR-0005: Make the compiled graph plan the runtime authority

**Status**: Accepted  
**Date**: 2026-04-27  
**Deciders**: Millrace maintainers

## Context

Millrace started with mode and loop assets that were useful for describing
stage order, but runtime behavior still depended on multiple places agreeing
with each other: loop JSON, router logic, entrypoint conventions, skill
metadata, recovery counters, and prompt prose. That was workable while the
runtime was small, but it made long-running operation harder to inspect and
harder to trust.

The risk was not just duplication. The bigger risk was authority drift. If the
compiler described one structure while runtime activation, routing, recovery, or
stage-request construction inferred another, operators could not know which
contract actually governed a run. That is especially dangerous for resumable
agent work because stale or mismatched authority may not fail immediately; it
may route work incorrectly several ticks later.

As Millrace added graph loops, stage kinds, completion behavior, learning
triggers, runner bindings, and compile-input fingerprints, the runtime needed a
single durable artifact that answered: what exactly is this workspace allowed to
execute right now?

## Decision

Millrace will treat `<workspace>/millrace-agents/state/compiled_plan.json` as
the runtime-authoritative execution contract.

The compiler materializes selected mode, graph-loop, stage-kind, entrypoint,
skill, runner, model, timeout, recovery, completion, learning-trigger, and
concurrency-policy inputs into one compiled graph plan. Runtime startup,
stage-request construction, activation, recovery, completion behavior,
post-stage routing, status identity, and run inspection consume that compiled
plan instead of re-deriving authority from loose assets during a run.

Compile input fingerprints are part of the authority model. If current mode,
config, or packaged/deployed assets no longer match the persisted plan, the plan
is stale. Runtime startup and config reload must not continue on a stale
last-known-good plan when recompilation fails.

Legacy loop assets may remain as compatibility or inspection surfaces, but graph
loops and stage-kind materialization are the runtime control-flow authority.

## Alternatives considered

- **Keep runtime routing table driven and use compiler output for inspection
  only**: Rejected because it preserves two sources of truth and leaves
  operators guessing whether compile diagnostics match live behavior.
- **Let stage entrypoints or skills describe recovery and routing behavior**:
  Rejected because advisory prompt text is not a durable, typed, inspectable
  runtime contract.
- **Compile only stage order and keep recovery/completion behavior hard-coded**:
  Rejected because the most failure-sensitive parts of Millrace are the recovery
  and closure boundaries; leaving them outside the compiled authority would keep
  the most important decisions least inspectable.
- **Recompile from loose assets on every handoff**: Rejected because it would
  make a long-running run sensitive to mid-run asset drift and would weaken
  reproducibility of persisted run artifacts.

## Consequences

**Positive:**
- Operators can inspect one persisted artifact to understand the active runtime
  structure.
- Stage requests, run artifacts, status output, and compile output can all carry
  the same `compiled_plan_id`, node id, stage-kind id, mode id, and loop ids.
- Runtime behavior becomes less dependent on prompt prose or duplicated routing
  tables.
- Stale-plan refusal makes config and asset drift visible instead of allowing
  mismatched authority to keep running.

**Negative / accepted costs:**
- The compiler has higher responsibility and must be tested as a runtime
  boundary, not just as an asset linter.
- Adding a new plane, stage kind, graph loop, or mode requires updating the
  compiler/materialization path and the runtime consumers together.
- Operator docs must stay precise about which behavior is compiler-owned and
  which behavior is advisory.

**Neutral but notable:**
- The runtime still keeps a `RuntimeEngine` facade and owned collaborator
  modules. The decision is about authority, not about collapsing all runtime
  implementation into the compiler.
- Compatibility loop assets can remain useful as long as they are not mistaken
  for live routing authority.

## Follow-up

- Keep `millrace compile show` aligned with the fields runtime consumers
  actually use.
- Keep tests that prove compiled graph identity reaches stage requests,
  stage-result artifacts, status output, and run inspection.
- When adding new runtime-owned behavior, decide explicitly whether it belongs
  in the compiled plan or in next-tick runtime config.
- Continue removing runtime paths that infer control-flow authority from loose
  assets when compiled graph data is available.
