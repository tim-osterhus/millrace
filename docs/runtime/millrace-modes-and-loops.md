# Millrace Modes And Loops

This document explains the current shipped mode and loop model used by the
Millrace compiler and runtime.

The goal is not to describe a future extension system. The goal is to describe
the exact structure the runtime ships and validates today.

## The Two Current Planes

Millrace runs two distinct planes:

- execution
- planning

Each plane is backed by a loop asset that declares:

- its stage list
- its entry stage
- its edges
- its `terminal_results`

Today the shipped loop ids are:

- `execution.standard`
- `planning.standard`

The shipped canonical mode ids are:

- `default_codex`
- `default_pi`

Compatibility alias:

- `standard_plain -> default_codex`

## What A Loop Defines

Loop assets validate as `LoopConfigDefinition` in `src/millrace_ai/contracts.py`.

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

That means loops are not just ordered stage lists. They are explicit terminal-
driven transition tables.

## Shipped Execution Loop

`execution.standard` currently declares these stages:

1. `builder`
2. `checker`
3. `fixer`
4. `doublechecker`
5. `updater`
6. `troubleshooter`
7. `consultant`

Its entry stage is `builder`.

Its current `terminal_results` are:

- `UPDATE_COMPLETE`
- `NEEDS_PLANNING`
- `BLOCKED`

In the shipped graph:

- `BUILDER_COMPLETE` moves `builder -> checker`
- `FIX_NEEDED` routes `checker -> fixer` and `doublechecker -> fixer`
- successful update terminates with `UPDATE_COMPLETE`
- blocked execution routes into `troubleshooter`
- `consultant` can hand the run back into troubleshooting or terminate with
  `NEEDS_PLANNING` or `BLOCKED`

This is why the execution loop is not a straight line. It is a repair-capable
governance loop.

## Shipped Planning Loop

`planning.standard` currently declares these stages:

1. `planner`
2. `manager`
3. `mechanic`
4. `auditor`
5. `arbiter`

Its entry stage is `planner`.

Its current `terminal_results` are:

- `MANAGER_COMPLETE`
- `ARBITER_COMPLETE`
- `REMEDIATION_NEEDED`
- `BLOCKED`

In the shipped graph:

- `PLANNER_COMPLETE` moves `planner -> manager`
- blocked `planner` or `manager` work routes into `mechanic`
- `MECHANIC_COMPLETE` loops back into `planner`
- `MANAGER_COMPLETE` is the normal planning terminal
- `BLOCKED` is the terminal recovery outcome from `mechanic`
- `auditor` is present in the loop and routes `AUDITOR_COMPLETE -> planner` or
  `BLOCKED -> mechanic`
- `arbiter` is present in the loop and terminates with `ARBITER_COMPLETE`,
  `REMEDIATION_NEEDED`, or `BLOCKED`

`arbiter` is not part of the normal queued work-item handoff path. In the
shipped baseline, it is activated through the planning loop's frozen
`completion_behavior` when backlog drain leaves an eligible closure target.
Use `docs/runtime/millrace-arbiter-and-completion-behavior.md` for that
runtime-owned dispatch model.

## What A Mode Defines

Modes validate as `ModeDefinition`.

The current mode shape is intentionally small:

- `mode_id`
- `execution_loop_id`
- `planning_loop_id`
- `stage_entrypoint_overrides`
- `stage_skill_additions`
- `stage_model_bindings`
- `stage_runner_bindings`

Both shipped canonical modes point at:

- `execution_loop_id = execution.standard`
- `planning_loop_id = planning.standard`

They differ only in `stage_runner_bindings`:

- `default_codex` binds every shipped stage to `codex_cli`
- `default_pi` binds every shipped stage to `pi_rpc`

Entrypoint, skill-addition, and model maps otherwise remain empty in the
baseline, which means loop topology and stage semantics stay identical across
the two harness presets.

## Stage Maps And What They Do

These mode maps are compile-time surfaces, not free-form runtime hints.

### `stage_entrypoint_overrides`

This map replaces the default stage entrypoint path for a stage.

Rules today:

- the key must be a selected stage in the chosen loops
- the path must be relative
- the path must start with `entrypoints/`
- the path must end with `.md`

Anything else fails compile validation.

### `stage_skill_additions`

This map attaches additional advisory skill paths to a stage-plan.

It does not change runtime-owned routing. It only changes the advisory skill
surface attached to the frozen stage plan.

### `stage_model_bindings`

This map sets a mode-level model name for a stage.

If present, it wins over stage-level config for that stage during compile.

### `stage_runner_bindings`

This map sets a mode-level runner name for a stage.

If present, it wins over stage-level config for that stage during compile.

## What The Compiler Freezes From Modes And Loops

During compile, the runtime converts the selected mode plus the selected loops
into one frozen stage-plan entry per stage in those loops.

Each frozen stage-plan records:

- `stage`
- `plane`
- `entrypoint_path`
- `required_skills`
- `attached_skill_additions`
- `runner_name`
- `model_name`
- `timeout_seconds`

This matters because the runtime executes the frozen stage-plan later. It does
not keep re-deriving this structure from raw mode and loop JSON on every
handoff.

## Config Interaction And Recompile Boundaries

The config system classifies certain fields as recompile-triggering boundaries.

Relevant examples:

- `runtime.default_mode`
- `stages.<stage>.runner`
- `stages.<stage>.model`
- `stages.<stage>.timeout_seconds`

New workspaces now bootstrap with `runtime.default_mode = "default_codex"`.
Existing configs that still use `standard_plain` continue to resolve to the
same canonical Codex-backed plan.

Those are the fields that change the frozen stage-plan contract rather than only
affecting next-tick runtime behavior.

## Operator View

Operators usually care about modes and loops in two moments:

1. before running the workspace, to confirm which structure is active
2. after config or asset changes, to confirm a new frozen plan was produced

Use:

- `millrace compile validate`
- `millrace compile show`

to confirm which mode, loops, stage entrypoints, and advisory skill surfaces are
actually active.

## Maintainer View

Maintainers should think about loops and modes as separate contracts:

- loops define stage topology and transition semantics
- modes choose which loops are active and which stage maps apply to them

That separation is why a mode map cannot legally mention a stage that is not
selected by the chosen loops.

The important operator consequence is that changing from `default_codex` to
`default_pi` does not change the loop graph. It changes only the frozen runner
binding attached to each shipped stage.
present in the selected loops. The compiler enforces that scope.

For the authoring rules and validation checklist, use
`docs/runtime/millrace-loop-authoring.md`.
