# Millrace Modes And Loops

This document explains the current shipped mode and loop model used by the
Millrace compiler and runtime.

The goal is to describe the exact structure the runtime ships and validates
today. Runtime execution now compiles and runs from the graph-loop-backed
compiled plan.

## Shipped Planes

Baseline Millrace modes select two planes:

- execution
- planning

Learning-enabled modes add a third plane:

- learning

Each plane still ships with a legacy loop asset that declares:

- its stage list
- its entry stage
- its edges
- its `terminal_results`

Those legacy loop assets remain part of the shipped asset contract, but the
compiler/runtime now materialize `compiled_plan.json` from graph loops and stage
kinds. `router.py` remains in the package as a shared decision-shape module, not
the runtime's live routing authority.

Today the shipped loop ids are:

- `execution.standard`
- `execution.with_integrator`
- `planning.standard`
- `planning.blueprint`
- `learning.standard`

The shipped canonical mode ids are:

- `default_codex`
- `default_pi`
- `learning_codex`
- `learning_pi`
- `default_codex_integrated`
- `learning_codex_integrated`
- `blueprint_codex`
- `blueprint_learning_codex`

Compatibility alias:

- `standard_plain -> default_codex`

## What A Legacy Loop Defines

Loop assets validate as `LoopConfigDefinition` through the
`millrace_ai.contracts` facade; the implementation lives under
`src/millrace_ai/contracts/loop_config.py`.

Each loop defines:

- `loop_id`
- `plane`
- `stages`
- `entry_stage`
- `edges`
- `terminal_results`
- optional `completion_behavior`

An edge validates as `LoopEdgeDefinition` and contains:

- `source_stage`
- `on_terminal_result`
- exactly one of `target_stage` or `terminal_result`
- `edge_kind`
- optional `max_attempts`

That means legacy loops are not just ordered stage lists. They are explicit
terminal-driven transition tables.

## Stage-Kind And Graph-Loop Runtime Surface

Phase 1 also ships a parallel architecture surface:

- stage kinds under `src/millrace_ai/assets/registry/stage_kinds/`
- graph loops under `src/millrace_ai/assets/graphs/`

These assets validate as:

- `RegisteredStageKindDefinition`
- `GraphLoopDefinition`

Built-in stage-kind assets are also checked against
`src/millrace_ai/contracts/stage_metadata.py`, which owns the canonical plane,
running-marker, legal-terminal-result, and result-class policy for shipped
stage identities.

The graph-loop surface does two things today:

- it proves the shipped execution, planning, and learning topology can be
  represented as node-and-edge graphs over declared stage kinds
- it lets the compiler emit `compiled_plan.json` as the runtime's
  authoritative control-flow artifact for intake, recovery, closure-target
  activation, learning-trigger activation, request binding, and routing

That graph surface is real, typed, and runtime-authoritative for both request
binding and control flow.

## Shipped Plane Graphs

Detailed topology for each shipped plane graph now lives under `docs/graphs/`.
Use those documents when you need node-by-node edges, terminal states, recovery
policies, selected modes, or runner-neutral graph behavior:

- `docs/graphs/execution-standard.md`: `execution.standard`
- `docs/graphs/execution-with-integrator.md`: `execution.with_integrator`
- `docs/graphs/planning-standard.md`: `planning.standard`
- `docs/graphs/planning-blueprint.md`: `planning.blueprint`
- `docs/graphs/learning-standard.md`: `learning.standard`
- `docs/graphs/graphs-index.md`: full shipped mode-to-plane graph
  configurations, including Codex versus Pi runner binding differences

This document keeps the higher-level compiler and mode model: which loop ids
ship, how modes select plane graphs, which maps a mode can override, and what
the compiler freezes into the runtime plan.

## What A Mode Defines

Modes validate as `ModeDefinition`.

The current mode shape is intentionally small:

- `mode_id`
- `loop_ids_by_plane`
- `stage_entrypoint_overrides`
- `stage_skill_additions`
- `stage_model_bindings`
- `stage_runner_bindings`
- `stage_thinking_bindings`
- `concurrency_policy`
- `learning_trigger_rules`

Baseline modes point at:

- `loop_ids_by_plane.execution = execution.standard`
- `loop_ids_by_plane.planning = planning.standard`

Integrated Codex modes point execution at:

- `loop_ids_by_plane.execution = execution.with_integrator`

Blueprint Codex modes point Planning at:

- `loop_ids_by_plane.planning = planning.blueprint`

The learning-enabled modes, including `blueprint_learning_codex`, also point
at:

- `loop_ids_by_plane.learning = learning.standard`

The mode families differ primarily in `stage_runner_bindings`:

- `default_codex` binds every shipped stage to `codex_cli`
- `default_pi` binds every shipped stage to `pi_rpc`
- `learning_codex` binds execution, planning, and learning stages to
  `codex_cli`
- `learning_pi` binds execution, planning, and learning stages to `pi_rpc`
- `default_codex_integrated` binds execution with Integrator plus planning to
  `codex_cli`
- `learning_codex_integrated` binds execution with Integrator, planning, and
  learning to `codex_cli`
- `blueprint_codex` binds execution and Blueprint Planning stages to
  `codex_cli`
- `blueprint_learning_codex` binds execution, Blueprint Planning, and learning
  stages to `codex_cli`

Entrypoint, skill-addition, and model maps otherwise remain empty in the
baseline. Harness-only presets keep topology identical; integrated presets
intentionally select a more expensive execution topology. Learning modes add a
compiled concurrency policy and learning trigger rules; those are explicit mode
data, not prompt-only instructions.

Specialized repository-local workflows should provide their own workspace-local
mode, loop, graph, and entrypoint assets under their owning project area, then
compile with the workspace runtime asset root. Those workflow assets are not
part of the core Millrace package.

## Stage Maps And What They Do

These mode maps are compile-time surfaces, not free-form runtime hints.

### `stage_entrypoint_overrides`

This map replaces the default stage entrypoint path for a stage.

Rules today:

- the key must be a selected stage in the chosen plane loops
- the path must be relative
- the path must start with `entrypoints/`
- the path must end with `.md`

Anything else fails compile validation.

### `stage_skill_additions`

This map attaches additional advisory skill paths to a node binding.

It does not change runtime-owned routing. It only changes the advisory skill
surface attached to the compiled node binding.

### `stage_model_bindings`

This map sets a mode-level model name for a stage.

If present, it wins over stage-level config for that stage during compile.

### `stage_runner_bindings`

This map sets a mode-level runner name for a stage.

If present, it wins over stage-level config for that stage during compile.

### `stage_thinking_bindings`

This map sets a mode-level runner-neutral thinking level for a stage.

If the stage key is present, it wins over graph-loop node defaults and
stage-level config during compile. JSON `null` is meaningful here: it freezes
the compiled stage default, so the request carries no explicit thinking
override and the selected adapter can use its own default behavior.

## Stage Config Overlays

Runtime config may define `stages.<stage>` entries for execution, planning, and
learning stages. Learning stages such as `analyst`, `professor`, `curator`, and
`librarian` use the same supported config surface as execution and planning
stages.

Supported stage config fields are:

- `runner`
- `model`
- `thinking_level`
- `model_reasoning_effort`
- `timeout_seconds`

`thinking_level` is runner-neutral. It is copied into the compiled node binding
and stage request, then translated by the selected adapter. Codex passes it as
`model_reasoning_effort="<value>"`; Pi passes it as `--thinking <value>`.
It is also visible in `compile show`, runner invocation artifacts, persisted
stage results, and `runs show`.

`model_reasoning_effort` remains accepted as a Codex compatibility alias. If
both fields are set for a stage, they must match.

## What The Compiler Freezes From Modes And Loops

During compile, the runtime converts the selected mode plus the selected graph
loops into one compiled runtime plan.

Each materialized node binding records:

- `node_id`
- `plane`
- `entrypoint_path`
- `required_skills`
- `attached_skill_additions`
- `runner_name`
- `model_name`
- `thinking_level`
- `model_reasoning_effort`
- `timeout_seconds`

This matters because the runtime executes the compiled node bindings later. It
does not keep re-deriving this structure from raw mode and loop JSON on every
handoff.

`compiled_plan.json` includes node plans, raw transitions, normalized compiled
intake entries, normalized closure-target activation entry when completion
behavior is present, normalized compiled transition indexes, compiled resume and
threshold recovery policies, terminal states, loop ids by plane, optional
learning trigger rules, and optional scheduler lane/concurrency policy.

For operator inspection, `millrace compile graph --workspace <workspace>` exports
the selected compiled topology as stable graph contracts. That output describes
legal control flow and can contain recovery cycles. Per-run history belongs to
`millrace runs trace <run_id>`, which reads `run_trace.json` or derives a
fallback trace from stage results for older runs.

## Config Interaction And Recompile Boundaries

The config system classifies certain fields as recompile-triggering boundaries.

Relevant examples:

- `runtime.default_mode`
- `stages.<stage>.runner`
- `stages.<stage>.model`
- `stages.<stage>.thinking_level`
- `stages.<stage>.model_reasoning_effort`
- `stages.<stage>.timeout_seconds`

New workspaces now bootstrap with `runtime.default_mode = "default_codex"`.
Existing configs that still use `standard_plain` continue to resolve to the
same canonical Codex-backed plan.

Those are the fields that change the compiled runtime plan.

Fields such as `usage_governance.*` are next-tick runtime settings and do not
change selected modes, loops, or compiled node bindings.

Use `learning_codex`, `learning_pi`, `learning_codex_integrated`, or
`blueprint_learning_codex` only when the workspace should opt into runtime
learning requests, the Analyst/Professor/Curator flow, and Planner-triggered
Librarian optional-skill preparation.

## Operator View

Operators usually care about modes and loops in two moments:

1. before running the workspace, to confirm which structure is active
2. after config or asset changes, to confirm a new compiled plan was produced

Use:

- `millrace compile validate`
- `millrace compile show`
- `millrace modes list`
- `millrace modes show <MODE_ID>`

to confirm which mode, loops, stage entrypoints, and advisory skill surfaces are
actually active.

## Maintainer View

Maintainers should think about loops and modes as separate contracts:

- graph loops and stage kinds define the current runtime-authoritative
  control-flow topology, request binding, and transition semantics
- modes choose which plane loops are active and which stage maps apply to them
- legacy loops remain shipped reference assets and should stay semantically
  aligned with the graph loops

That separation is why a mode map cannot legally mention a stage that is not
selected by the chosen loops.

The important operator consequence is that changing from `default_codex` to
`default_pi` does not change the loop graph. It changes only the compiled
runner binding attached to each shipped stage. Changing to a `learning_*` mode
does change the selected plane set by adding `learning.standard`.

For the authoring rules and validation checklist, use
`docs/runtime/millrace-loop-authoring.md`.
