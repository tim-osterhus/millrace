# ADR-0013: Generic Stage And Plane Registry

Status: Accepted

Date: 2026-06-04

## Context

`src/millrace_ai/contracts/stage_metadata.py` currently serves as the shipped
stage legality registry. It defines which planes exist (`plane_id`), which stages belong to
which planes (`stage_kind_id` per `plane_id`), which terminal markers each
stage can emit (`terminal_outcome_id`), which terminal results map to which
result classes (`result_class_id`), and what the running/blocked markers
(`runtime_role_id`) are for each stage and graph node (`node_id`). Runner prompts, result normalization, entrypoint linting,
graph stage-kind validation, and built-in stage-kind asset validation all
derive authority from that module.

This works for the shipped stages because the shipped set is fixed and
well-known. However, the module's role has been drifting toward universal
runtime authority: some readers treat `stage_metadata.py` as the source of
truth for all stage definitions, all plane membership, and all terminal
result policy.

Custom graphs and stage-kind assets already declare their own `runtime_stage`,
terminal states, and legal outcomes through JSON assets and the compiled plan.
The compiled plan, not the metadata module, is the runtime-authoritative
execution contract (see ADR-0005 and ADR-0010). This ADR clarifies
`stage_metadata.py`'s actual role: it is the shipped registry instance for
built-in stages, not universal runtime authority for future or custom stage
configurations.

## Decision

`stage_metadata.py` is the shipped registry instance. It is the canonical
metadata source for:

- The shipped built-in stages and their plane membership (execution, planning,
  learning)
- The shipped terminal markers and result-class mappings that runner
  normalization and entrypoint linting use by default
- The shipped running/blocked markers that status output and snapshot
  serialization use by default

`stage_metadata.py` is **not** universal runtime authority. It must not be
treated as:

- The exclusive source of plane membership for custom or future graph nodes
- The owner of terminal-outcome policy for non-built-in terminal states
- The definition of what constitutes a legal stage kind (stage-kind assets
  define that through `runtime_stage`, `legal_terminal_states`, and
  `required_skill_paths`)
- The arbiter of which stages may appear in a compiled graph (graph-loop
  assets and stage-kind assets declare that through compiled transitions)

Custom graphs, stage-kind assets, and compiled plans derive their authority
from:

1. The graph-loop JSON assets under `src/millrace_ai/assets/graphs/`
2. The stage-kind registry JSON under
   `src/millrace_ai/assets/registry/stage_kinds/`
3. The compiled plan produced by the compiler from those assets, which is the
   runtime-authoritative execution contract

When a custom graph node declares a `runtime_stage` that corresponds to a
shipped stage, the shipped stage metadata from `stage_metadata.py` provides the
default terminal markers, result classes, and entrypoint contract. When a
custom graph node declares a `runtime_stage` that does not correspond to a
shipped stage, the stage-kind asset provides the legal terminal states and
required skills; terminal markers and result classes are resolved from the
graph's terminal-state/action metadata and the compiled plan.

The identity fields that the shipped registry establishes for built-in
stages are `plane_id`, `lane_id`, `node_id`, `stage_kind_id`,
`runtime_role_id`, `terminal_outcome_id`, and `result_class_id`. These
fields define the registry's identity vocabulary: `plane_id` identifies the
operational plane, `lane_id` identifies lane membership within a plane,
`node_id` identifies the graph node, `stage_kind_id` identifies the stage
definition, `runtime_role_id` identifies running and blocked marker roles,
`terminal_outcome_id` identifies legal terminal outcomes, and
`result_class_id` maps outcomes to result classes.

## Consequences

This ADR preserves the existing `stage_metadata.py` role for shipped stages
while explicitly constraining its authority domain. Custom graph authors and
future stage implementors know they must declare stage-kind assets and graph
topology, not patch `stage_metadata.py`.

The runtime already ships enough authority surfaces for custom graphs
(stage-kind assets, graph-loop assets, terminal actions, lifecycle plans,
runtime-effect rules) without requiring registry changes. No new package is
needed to support custom stage kinds.

A prospective `registry/` package that would unify stage-kind, graph-loop, and
workflow-primitive asset loading under one seam is not yet created. This ADR
does not create it; it only clarifies the boundary so that when such a package
is proposed, `stage_metadata.py` is already scoped to shipped-stage defaults
rather than universal authority.
