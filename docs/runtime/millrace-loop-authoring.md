# Millrace Loop Authoring

This document is for maintainers extending or changing Millrace loop, stage, or
mode assets.

Use it when you need to change:

- legacy loop JSON under `src/millrace_ai/assets/loops/`
- graph-loop JSON under `src/millrace_ai/assets/graphs/`
- stage-kind JSON under `src/millrace_ai/assets/registry/stage_kinds/`
- mode JSON under `src/millrace_ai/assets/modes/`
- stage entrypoint selection behavior
- per-stage model or runner bindings that should be frozen by compile

## Start From The Actual Contract

Do not author loops from memory or from prompt prose.

The authoritative sources are:

- `src/millrace_ai/contracts.py`
- `src/millrace_ai/architecture/stage_kinds.py`
- `src/millrace_ai/architecture/loop_graphs.py`
- `src/millrace_ai/architecture/materialization.py`
- `src/millrace_ai/compiler.py`
- `src/millrace_ai/assets/modes.py`
- `src/millrace_ai/assets/loops/execution/default.json`
- `src/millrace_ai/assets/loops/planning/default.json`
- `src/millrace_ai/assets/graphs/execution/standard.json`
- `src/millrace_ai/assets/graphs/planning/standard.json`
- `src/millrace_ai/assets/registry/stage_kinds/`
- `src/millrace_ai/assets/modes/default_codex.json`
- `src/millrace_ai/assets/modes/default_pi.json`

Loop and mode docs should describe those contracts, not override them.

## Two Parallel Authoring Surfaces

Millrace currently ships two loop-description layers that must not drift apart:

1. legacy loop assets under `src/millrace_ai/assets/loops/`
2. graph-loop and stage-kind assets under
   `src/millrace_ai/assets/graphs/` and
   `src/millrace_ai/assets/registry/stage_kinds/`

Today the runtime executes both request binding and control flow from
`compiled_graph_plan.json`, while the legacy loop path still feeds the
compatibility stage-plan snapshot in `compiled_plan.json`.

The graph-loop path exists to:

- prove the shipped topology can be represented as typed node graphs over stage kinds
- emit `compiled_graph_plan.json` during compile
- drive runtime request binding, intake, recovery, closure-target activation,
  and post-stage routing
- support preview materialization of discovered graph loops without modifying
  the frozen stage-plan authoring path

For shipped defaults, maintainers should keep both surfaces aligned until a
later unification phase removes the remaining split between graph control flow
and legacy stage-plan freezing.

## Legacy Loop JSON Rules

A legacy loop must validate as `LoopConfigDefinition`.

That means:

- `entry_stage` must appear in `stages`
- `stages` must be unique
- every stage in `stages` must belong to the loop plane
- every edge source must appear in `stages`
- every edge terminal must be legal for that `source_stage`
- every non-terminal edge must have `target_stage`
- every terminal edge must have `terminal_result`
- at least one edge path must terminate into one of the loop `terminal_results`

For `LoopEdgeDefinition`, exactly one of `target_stage` or `terminal_result`
must be set.

If `edge_kind = terminal`, the edge must terminate. If the edge is not terminal,
it must point at another stage.

## Mode JSON Rules

A mode must validate as `ModeDefinition`.

Today the important authoring rule is scope:

- `stage_entrypoint_overrides`
- `stage_skill_additions`
- `stage_model_bindings`
- `stage_runner_bindings`

may only reference stages that exist in the selected execution and planning
loops.

The compiler enforces that by building the set of selected stages first and then
rejecting mode maps that refer outside that set.

## Stage-Kind And Graph-Loop Rules

A stage-kind asset must validate as `RegisteredStageKindDefinition`.

For shipped defaults, that means at minimum:

- plane membership is declared there, not inferred from prose
- legal outcomes must cover the outcomes used by any graph edges that leave the node
- default entrypoint and required stage-core skills must remain real packaged assets

A graph-loop asset must validate as `GraphLoopDefinition`.

That means:

- every node references a declared stage kind
- every edge references a valid source node and a valid target node or terminal state
- every edge outcome is legal for the source node's stage kind
- planning intake can be modeled through multiple `entry_nodes`
- completion behavior may target only a closure-role stage kind

## Entrypoint Override Rules

Entrypoint overrides are intentionally narrow.

A valid override must be:

- relative
- under `entrypoints/`
- a markdown file path ending in `.md`
- free of parent-directory escapes

The compiler rejects absolute paths, parent traversal, empty strings, and paths
outside the entrypoint asset tree.

## Stage Bindings And Recompile Behavior

Authoring decisions that change the frozen stage-plan contract require recompile.

That includes:

- changing `runtime.default_mode`
- changing stage-level `runner`
- changing stage-level `model`
- changing stage-level `timeout_seconds`
- changing legacy loop stage topology
- changing graph-loop topology
- changing stage-kind contracts used by graph loops
- changing mode stage maps

The runtime may apply some other config changes on the next tick, but anything
that changes the frozen stage-plan should be treated as a compile concern.

## Runtime-Owned Vs Advisory Content

This distinction is the main authoring guardrail.

Runtime-owned behavior includes:

- queue state transitions
- stage routing
- retry thresholds
- recovery escalation
- terminal result semantics
- persisted runtime status

Advisory content includes:

- stage instructions in entrypoint markdown
- stage-core skill posture
- optional skill guidance
- external docs that explain how to operate or extend Millrace

Do not move runtime-owned behavior into docs, prompt prose, or skill text just
because it feels easier to describe there.

## Authoring Workflow

When you change loops or modes:

1. update the asset JSON first
2. run `millrace compile validate`
3. run `millrace compile show`
4. check that the frozen stage-plan reflects the intended entrypoints, skills,
   runner names, model names, and loop ids
5. inspect `compiled_graph_plan.json` when the change also touches stage kinds
   or graph loops
6. update docs that describe the changed contract

If the new structure changes what operators or stage agents need to know, update
the relevant runtime docs and external agent docs in the same slice.

## Tests To Touch

At minimum, expect to review and possibly update:

- `tests/assets/test_modes.py`
- `tests/assets/test_stage_kinds.py`
- `tests/assets/test_loop_graphs.py`
- `tests/integration/test_compiler.py`
- `tests/assets/test_entrypoints.py`
- runtime docs that describe mode and loop behavior

If you changed entrypoint assets or advisory skill surfaces, also inspect:

- `tests/assets/test_packaging_runtime_assets.py`
- `tests/runners/test_runner.py`
- `tests/runners/test_runners_codex_adapter.py`

## What Good Authoring Looks Like

Good loop and mode authoring is:

- concrete
- compiler-valid
- explicit about `terminal_results`
- explicit about stage topology
- explicit about whether you changed the frozen stage-plan legacy loop surface,
  the runtime-authoritative graph/stage-kind surface, or both
- explicit about what is runtime-owned and what is advisory

Bad authoring:

- invents new stage names without adding the matching stage-kind and graph
  contract support
- invents new terminal meanings in doc prose only
- uses `stage_entrypoint_overrides` as a generic escape hatch without updating
  the surrounding documentation and tests
- blurs runtime-owned routing with agent-authored reasoning

If a loop or mode change cannot be explained cleanly in terms of contracts,
stage-plan freezing, and runtime-owned boundaries, it is probably not ready to
ship.
