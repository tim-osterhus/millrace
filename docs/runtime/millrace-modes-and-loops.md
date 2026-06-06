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
- `efficient_learning_mixed`
- `learning_pi`
- `default_codex_integrated`
- `learning_codex_integrated`
- `blueprint_codex`
- `blueprint_learning_codex`

Compatibility alias:

- `standard_plain -> default_codex`

## Discoverable Non-Shipped Fixtures

Millrace also ships a minimal architecture proof fixture that is discoverable
through asset discovery but is **not** a shipped default mode. It is not
present in `SHIPPED_MODE_IDS` and does not change the default product surface.

### `minimal_three_plane`

`minimal_three_plane` is a three-plane architecture proof fixture. It proves
that simple graph-defined workflows can compile and execute against the current
canonical plane infrastructure using only generic lifecycle terminal actions.

Key properties:

- It selects all three canonical planes (`execution`, `planning`, `learning`)
  and uses one custom minimal graph loop per plane:
  `execution.minimal_three_plane`, `planning.minimal_three_plane`,
  `learning.minimal_three_plane`.
- Each plane uses a single custom stage kind bound to a canonical
  `runtime_stage`:
  - `basic_worker` (execution) → `runtime_stage: builder`
  - `basic_planner` (planning) → `runtime_stage: planner`
  - `basic_learner` (learning) → `runtime_stage: analyst`
- The fixture uses only generic lifecycle terminal actions
  (`complete_work_item`, `block_work_item`, `no_op_complete_work_item`).
- It contains no domain-specific workflow identifiers: no Recon, Blueprint,
  closure target, Arbiter, Manager, Mechanic, planner disposition, candidate
  evaluation, or learning promotion.
- It is discoverable as a mode asset and compiles successfully, but it is not
  a shipped product mode and is not listed in `SHIPPED_MODE_IDS`.

This fixture intentionally limits itself to the current canonical plane IDs and
canonical `runtime_stage` values. **Arbitrary plane IDs and arbitrary runtime
stages are deferred** to the generic stage and plane registry work tracked in
ADR-0013.

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

### Custom Graph Nodes And Custom Stage Kinds

Millrace supports custom graph nodes and custom stage kinds over canonical
runtime stages. Shipped stage-kind assets declare `runtime_stage` and
`required_skill_paths`; node materialization reads required skills from those
assets rather than a hardcoded stage map. The compiler validates that each
materialized node carries a known `stage_kind_id` and that stage kind is
registered in the shipped or workspace-local asset root. Custom stage kinds
can declare their own `runtime_stage` mapping, entrypoint contract,
request-context profile, and render-plan authority.

Custom graph nodes use the same typed node-and-edge contract as shipped nodes.
A graph node selects a registered stage kind and inherits its runtime stage,
required skills, runner binding, and request-context authority. Mode maps
such as `stage_entrypoint_overrides`, `stage_skill_additions`, and
`stage_runner_bindings` apply uniformly whether the node references a shipped
or custom stage kind.

Arbitrary runtime stages (stage kinds without a declared `runtime_stage`) are
**not supported yet**. Every node must resolve to a known runtime stage through
its stage kind or a mode/workspace config overlay. Stale compiled plans missing
canonical `runtime_stage` on materialized nodes fail with clear recompile or
update guidance.

Workspace-local mode, graph, stage-kind, and entrypoint assets live under the
workspace runtime asset root and are not part of the core Millrace package.
Compile with the active workspace asset root to use custom assets.

The `minimal_three_plane` fixture (see "Discoverable Non-Shipped Fixtures"
above) is an example of custom stage kinds and graph loops that are
package-shipped for testing but remain outside the default product surface.

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

The shipped Execution and Planning graphs also declare their default
runtime-failure repair node in the compiled graph authority:

- `execution.standard` and `execution.with_integrator` route unclassified
  runtime-owned Execution blockers to `troubleshooter` when attempts remain.
- `planning.standard` routes unclassified runtime-owned Planning blockers to
  `mechanic` when attempts remain.
- `planning.blueprint` routes unclassified runtime-owned Planning blockers to
  `mechanic_blueprint` when attempts remain.
- `learning.standard` intentionally declares no default runtime-failure repair
  node.

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
- `model_aliases`
- `model_assignment`
- `concurrency_policy`
- `scheduler_policy_id`
- `learning_trigger_rules`

Modes may optionally set `scheduler_policy_id` to select a compiled scheduler
policy explicitly; if omitted, the compiler chooses the shipped default that
matches the selected plane set.

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
- `efficient_learning_mixed` binds the same standard learning topology to a
  mixed Codex/Pi runner profile, leaves Integrator out of the active execution
  loop, and carries mode-local model/depth alias assignments for each shipped
  standard stage
- `learning_pi` binds execution, planning, and learning stages to `pi_rpc`
- `default_codex_integrated` binds execution with Integrator plus planning to
  `codex_cli`
- `learning_codex_integrated` binds execution with Integrator, planning, and
  learning to `codex_cli`
- `blueprint_codex` binds execution and Blueprint Planning stages to
  `codex_cli`
- `blueprint_learning_codex` binds execution, Blueprint Planning, and learning
  stages to `codex_cli`

Entrypoint, skill-addition, and direct model maps otherwise remain empty in the
baseline. Harness-only presets keep topology identical; integrated presets
intentionally select a more expensive execution topology. Learning modes add a
compiled scheduler policy, concurrency policy, and learning trigger rules;
those are explicit mode data, not prompt-only instructions.
`efficient_learning_mixed` is the one shipped mode that uses `model_aliases`,
`model_assignment`, and `stage_runner_bindings` inside the mode asset so its
mixed Codex/Pi stage-cost profile can travel with the mode instead of
depending on workspace-local alias defaults.

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

## Model Assignment Aliases

Runtime config can assign model/depth policy by alias instead of editing every
stage or mode map. Mode assets may also define mode-local aliases and
assignments when a shipped mode owns a specific stage-cost profile. The shipped
workspace config aliases are:

- `fast`: `model = "gpt-5.4-mini"`, `thinking_level = "high"`
- `standard`: `model = "gpt-5.5"`, `thinking_level = "medium"`
- `deep`: `model = "gpt-5.5"`, `thinking_level = "xhigh"`

Alias assignment is compiler-owned and runs after graph-loop node defaults,
stage config, and mode `stage_model_bindings` / `stage_thinking_bindings` have
already been applied. Workspace stage assignment wins over workspace loop
assignment, which wins over mode stage assignment, mode loop assignment,
mode default assignment, and the workspace global default alias. The built-in
`standard` alias is the final alias fallback before preserving the pre-alias
assignment.

When the selected assignment comes from a mode map, the compiler resolves the
alias against mode-local aliases first, then workspace aliases. When the
selected assignment comes from workspace config, the compiler resolves workspace
aliases first. This lets `efficient_learning_mixed` define aliases such as
`codex_max` and `deepseek_fast` with different meanings from workspace
defaults while still allowing explicit operator config to override the preset.

`efficient_learning_mixed` ships these mode-local aliases:

| Alias | Runner Family | Model | Thinking Level |
| --- | --- | --- | --- |
| `codex_max` | Codex | `gpt-5.5` | `xhigh` |
| `codex_med` | Codex | `gpt-5.5` | `medium` |
| `codex_fast` | Codex | `gpt-5.4-mini` | `xhigh` |
| `deepseek_max` | Pi | `deepseek-v4-pro` | `max` |
| `deepseek_med` | Pi | `deepseek-v4-pro` | `high` |
| `deepseek_fast` | Pi | `deepseek-v4-flash` | `max` |

Its active standard-topology stage assignments are:

| Alias | Stages |
| --- | --- |
| `codex_max` | `arbiter`, `troubleshooter`, `mechanic`, `planner`, `recon`, `consultant`, `auditor` |
| `codex_med` | `librarian`, `curator`, `professor` |
| `codex_fast` | `updater` |
| `deepseek_max` | `manager`, `checker`, `doublechecker` |
| `deepseek_med` | `builder`, `analyst` |
| `deepseek_fast` | `fixer` |

Integrator remains inactive because the mode selects `execution.standard`.
The compiler rejects stage maps for nodes outside the selected loop, so
Integrator does not receive an active assignment in this mode.

Example:

```toml
[model_assignment.by_loop]
"planning.blueprint" = "deep"

[model_assignment.by_stage]
"contractor_blueprint" = "deep"
```

Set `model_assignment.enabled = false` to preserve legacy stage/mode model
resolution exactly. Invalid selected aliases warn during compile and fall back
deterministically; provider support for a syntactically valid model id is still
checked only when the runner/provider handles the request.

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
- `model_assignment_alias_id`
- `model_assignment_source`
- `timeout_seconds`

This matters because the runtime executes the compiled node bindings later. It
does not keep re-deriving this structure from raw mode and loop JSON on every
handoff.

`compiled_plan.json` includes node plans, raw transitions, normalized compiled
intake entries, normalized closure-target activation entry when completion
behavior is present, normalized compiled transition indexes, compiled resume and
threshold recovery policies, terminal states, loop ids by plane, optional
learning trigger rules, and the compiled scheduler policy selected for the
mode.

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
- `model_aliases.<alias>.model`
- `model_aliases.<alias>.thinking_level`
- `model_assignment.enabled`
- `model_assignment.default_alias`
- `model_assignment.by_loop.<loop_id>`
- `model_assignment.by_stage.<stage>`

New workspaces now bootstrap with `runtime.default_mode = "default_codex"`.
Existing configs that still use `standard_plain` continue to resolve to the
same canonical Codex-backed plan.

Those are the fields that change the compiled runtime plan.

Fields such as `usage_governance.*` are next-tick runtime settings and do not
change selected modes, loops, or compiled node bindings.

Use `learning_codex`, `efficient_learning_mixed`, `learning_pi`,
`learning_codex_integrated`, or `blueprint_learning_codex` only when the
workspace should opt into runtime learning requests, the
Analyst/Professor/Curator flow, and Planner-triggered Librarian optional-skill
preparation.

## Operator View

Operators usually care about modes and loops in two moments:

1. before running the workspace, to confirm which structure is active
2. after config or asset changes, to confirm a new compiled plan was produced

Use:

- `millrace compile validate`
- `millrace compile show`
- `millrace model-aliases list`
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

That separation is why topology-affecting mode maps cannot legally mention a
stage that is not selected by the chosen loops. Model-assignment presets are
non-topological: an inactive stage assignment is ignored unless a selected
graph later includes that stage.

The important operator consequence is that changing from `default_codex` to
`default_pi` does not change the loop graph. It changes only the compiled
runner binding attached to each shipped stage. Changing to a `learning_*` mode
does change the selected plane set by adding `learning.standard`.

For the authoring rules and validation checklist, use
`docs/runtime/millrace-loop-authoring.md`.
