# Millrace Technical Overview

This document is the single high-density technical summary of how Millrace works
as shipped today.

If `README.md` is the landing page, this file is the implementation-oriented
system map. It is meant to answer, in one place, what Millrace is, what it
owns, how work flows through it, how the runtime compiles and executes that
workflow, which artifacts it persists, and where the authority boundaries sit
between runtime code, stage agents, and the operator.

Use this document when you want one coherent picture before dropping into the
more specialized references under `docs/runtime/`.

## What Millrace Actually Is

Millrace is a filesystem-backed runtime for long-running agent work. It is not
primarily a model wrapper, a prompt pack, or a chat workflow. It is a runtime
that surrounds raw agent harnesses with durable structure.

The core idea is simple:

- the stage agent does one bounded unit of reasoning and emits one legal result
- the runtime decides what the next stage is, when it should run, and what
  state change is authoritative
- the workspace holds durable queue state, compiled runtime structure,
  recovery context, and run artifacts so the workflow can survive across time

That split matters. Millrace is useful exactly when the agent session is no
longer enough on its own because the work has become multi-stage,
interruption-prone, recovery-sensitive, or closure-sensitive.

In other words, Millrace is for situations where "the agent said it was done"
is not a strong enough completion criterion.

Millrace is also agent-native in how it is meant to be operated. The intended
posture is an external agent acting as the Millrace operator, not a human
manually steering runtime internals without an agent in the loop. Humans can
still invoke the CLI directly, but when a harness such as Codex or Claude Code
supports repo-local skills, the right starting point is
`docs/skills/millrace-ops-agent-manual/SKILL.md` so the operator agent has the
right fit criteria, autonomy handshake, and command discipline before deciding
whether work should enter Millrace.

## High-Level System Model

Millrace has five layers:

1. operator-owned workspace input and configuration
2. compiler-resolved runtime structure
3. deterministic runtime orchestration
4. stage-runner dispatch into an external harness
5. persisted artifacts and inspection surfaces

In practice:

- the operator points Millrace at a workspace
- `millrace init` creates the managed `millrace-agents/` baseline under that
  workspace
- config, modes, loops, entrypoints, and skills are compiled into one frozen
  run plan
- each tick processes control input, intake, reconciliation, claim or
  activation, a governed stage run, and authoritative result application
- all meaningful artifacts are persisted so later ticks and later operators can
  inspect real state instead of reconstructing it

## Workspace Boundary And Ownership

The runtime is intentionally filesystem-native. Each workspace gets its own
Millrace runtime tree under `<workspace>/millrace-agents/`.

Within that tree, ownership is intentionally split.

Operator-owned surfaces include:

- the workspace root itself
- the source repository being worked on
- runtime configuration choices in `millrace-agents/millrace.toml`
- queue intake actions performed through supported CLI or import surfaces

Runtime-owned surfaces include:

- queue movement between `queue/`, `active/`, `done/`, `blocked/`, and
  `incoming/` directories
- active-stage identity in snapshot state
- recovery counters and stale-state repair context
- compiled plan persistence
- run directories and stage-result artifacts
- closure-target state for Arbiter-driven completion
- usage-governance state and token accounting when automatic pause/resume is
  enabled

This distinction is one of the most important design rules in Millrace. Stages
are not allowed to mutate authoritative queue or status state directly. The
runtime applies those changes after a legal stage result is emitted.

## Canonical On-Disk Model

Millrace persists two fundamentally different artifact families.

### 1. Canonical work documents

These are the queue-facing markdown documents that represent managed work:

- `millrace-agents/tasks/{queue,active,done,blocked}/*.md`
- `millrace-agents/specs/{queue,active,done,blocked}/*.md`
- `millrace-agents/incidents/{incoming,active,resolved,blocked}/*.md`
- `millrace-agents/learning/requests/{queue,active,done,blocked}/*.md`

They are human-readable markdown documents with headed sections, not raw JSON
blobs. JSON remains acceptable as an import format for intake, but the
canonical long-lived queue artifact is markdown.

The runtime is therefore built around operator-facing documents that also
satisfy typed contracts.

The public Python contract surface is `millrace_ai.contracts`. Internally, that
surface is a package under `src/millrace_ai/contracts/` so foundational enums,
stage metadata, work documents, stage results, loop/mode definitions, runtime
snapshots, mailbox envelopes, compiler diagnostics, and recovery counters can be
reviewed independently while preserving the root import contract.

### 2. Runtime/state artifacts

These are machine-owned, typed state and runtime outputs such as:

- `millrace-agents/state/runtime_snapshot.json`
- `millrace-agents/state/recovery_counters.json`
- `millrace-agents/state/compiled_plan.json`
- `millrace-agents/state/compile_diagnostics.json`
- `millrace-agents/state/execution_status.md`
- `millrace-agents/state/planning_status.md`
- `millrace-agents/state/learning_status.md`
- `millrace-agents/state/baseline_manifest.json`
- `millrace-agents/state/usage_governance_state.json`
- `millrace-agents/state/usage_governance_ledger.jsonl`
- mailbox command envelopes and archives
- run-scoped runner artifacts and stage results

Completion behavior adds a third specialized subtree under `millrace-agents/arbiter/`
for canonical root contracts, closure-target state, rubrics, verdicts, and
reports.

## The Compiler And Why It Exists

Millrace does not execute directly from loose config, mode, and loop inputs on
every handoff. It compiles those inputs into one frozen, inspectable plan first.

The compiler resolves:

- which mode is active
- which loop is active for each selected plane
- which entrypoint path each stage uses
- which required stage-core skills attach to each stage
- which optional attached skills were added at compile time
- which runner/model/timeout each stage will use
- whether a completion behavior exists and what it freezes
- whether learning trigger rules or plane-concurrency policy exist
- which shipped stage-kind and graph-loop assets materialize into the graph
  control-flow plan

`compiled_plan.json` materializes the stage-kind registry and graph-loop assets
into explicit node plans, raw transitions, normalized compiled intake entries,
a normalized closure-target activation entry, compiled resume and threshold
recovery policies, and explicit terminal semantics. The live runtime executes
stage-request construction, claim activation, closure-target activation,
recovery, and post-stage routing from that compiled plan.

The compiler currently ships with baseline and learning-enabled built-in modes:

- baseline modes: `default_codex`, `default_pi`
- learning-enabled modes: `learning_codex`, `learning_pi`
- execution loop: `execution.standard`
- planning loop: `planning.standard`
- learning loop: `learning.standard`

`standard_plain` remains accepted as a compatibility alias that canonicalizes to
`default_codex` before compile diagnostics, compiled-plan ids, and runtime
snapshot state are written.

Compile output is operator-visible through `millrace compile validate` and
`millrace compile show`. Failed recompiles preserve the last known good plan.

The public Python surface remains `millrace_ai.compiler`. Its implementation is
split under `src/millrace_ai/compilation/` so workspace compile orchestration,
graph preview, materialization, validation, policy compilation, asset
fingerprinting, persistence, and currentness inspection can evolve without
turning the public facade back into a multi-purpose implementation module.

For compile-time proof work, the package also exposes a graph preview surface
that can materialize a discovered graph loop without adding it to the shipped
compiled plan contract.

## Modes, Loops, And Compiled Plans

The baseline runtime modes have two planes:

- execution
- planning

Learning-enabled modes add a third plane:

- learning

Each plane is currently described in two parallel ways:

1. legacy loop assets in `src/millrace_ai/assets/loops/`
2. graph-loop assets in `src/millrace_ai/assets/graphs/` over stage
   kinds declared in `src/millrace_ai/assets/registry/stage_kinds/`

The legacy loop assets remain packaged inspection surfaces. They declare:

- the stages present in that plane
- the plane entry stage
- the terminal-result-driven edges between stages
- the plane-level `terminal_results`
- optional completion behavior for backlog-drain activation

The graph-loop assets describe the same shipped topology in a richer node model:

- explicit `nodes`
- explicit `entry_nodes`
- explicit `terminal_states`
- edges validated against stage-kind legal outcomes

The compiler now materializes one `CompiledRunPlan` in `compiled_plan.json` for
both runtime request binding and control flow.

The selected mode connects the active loops through `loop_ids_by_plane` and can
add compile-time overrides such as:

- stage entrypoint overrides
- stage skill additions
- stage model bindings
- stage runner bindings
- plane concurrency policy
- learning trigger rules

In the shipped baseline, that runner binding map is how harness choice is
expressed:

- `default_codex` binds all shipped stages to `codex_cli`
- `default_pi` binds all shipped stages to `pi_rpc`
- `learning_codex` binds execution, planning, and learning stages to
  `codex_cli`
- `learning_pi` binds execution, planning, and learning stages to `pi_rpc`

The loop topology does not fork just because the harness changes.

The learning modes preserve execution/planning mutual exclusion and freeze a
plane concurrency policy into the compiled plan for operator visibility and
future scheduler authority. The current tick executor still owns one active
stage at a time. Learning trigger rules can enqueue targeted learning requests
from runtime evidence, for example a Curator request after a successful
Doublechecker pass or an Analyst request after troubleshooting/consultation.

The compiler materializes one `CompiledRunPlan`, whose graph nodes record
the exact runtime execution contract the engine will use later:

- node id
- plane
- entrypoint path
- entrypoint contract id
- required stage-core skills
- attached skill additions
- runner name
- model name
- timeout seconds

That freeze step is what makes later execution deterministic and inspectable.
The runtime no longer has to keep inferring structure from loose config while it
is in the middle of a run.

## The Shipped Planning And Execution Planes

The current execution loop is:

- `builder`
- `checker`
- `fixer`
- `doublechecker`
- `updater`
- `troubleshooter`
- `consultant`

The current planning loop is:

- `planner`
- `manager`
- `mechanic`
- `auditor`
- `arbiter`

These are not simple linear pipelines.

Execution is a repair-capable loop. In the happy path:

- `builder` implements
- `checker` validates
- `updater` reconciles project-facing docs and repository map state

If `checker` or `doublechecker` finds fixable gaps, the runtime routes into
`fixer`. If execution blocks or recovery budgets are hit, it routes into
`troubleshooter` and then potentially into `consultant`, which can decide that
the problem must be handed back into planning.

Planning is similarly not just "write a spec and stop." In the happy path:

- `planner` synthesizes or refines a spec
- `manager` decomposes it into executable tasks

If planning hits blockage or inconsistency, `mechanic` handles repair-oriented
recovery. `auditor` is the incident intake entrypoint. `arbiter` is special: it
is part of the planning loop topology but is not a normal queued successor. It
is activated by completion behavior when backlog drain makes closure evaluation
possible.

The current learning loop is:

- `analyst`
- `professor`
- `curator`

Learning is opt-in through `learning_codex` or `learning_pi`. Its normal path is
Analyst evidence analysis, Professor synthesis, then Curator acceptance and
skill-update curation. It can terminate with `CURATOR_COMPLETE` or `BLOCKED`.
Learning requests live under `millrace-agents/learning/requests/`, and targeted
requests can start at a specific learning stage when a compiler-frozen trigger
rule says that stage is the right entry point.

The phase-1 graph loops make the shipped intake mapping explicit:

- execution graph: `task -> builder`
- planning graph: `spec -> planner`
- planning graph: `incident -> auditor`
- learning graph: `learning_request -> analyst`

## Runner Baselines

Millrace currently ships two first-class built-in runner adapters:

- `codex_cli`
- `pi_rpc`

Codex remains the canonical bootstrap posture. New workspaces default to
`runtime.default_mode = "default_codex"` and `runners.default_runner = "codex_cli"`.

Pi is opt-in through `default_pi` or direct runner selection. The Pi adapter
uses RPC mode and disables Pi-native context-file and skill discovery by
default so the baseline stays governed by Millrace entrypoints rather than
ambient Pi project state.

## Deterministic Tick Lifecycle

The runtime engine runs one deterministic tick at a time. In daemon mode it
repeats those ticks; in `run once` mode it performs startup plus a single tick.

A tick follows this broad order:

1. drain mailbox commands
2. consume watcher or polling intake events
3. refresh queue depths
4. respect stop control gates
5. evaluate usage governance before paused work can continue
6. respect pause control gates
7. run stale/impossible-state reconciliation
8. claim or continue active work
9. if nothing is claimable, evaluate completion behavior
10. return idle if no stage is active
11. evaluate usage governance again before dispatching a stage
12. execute at most one stage through the configured runner
13. normalize the result and apply the router decision
14. record post-stage usage and persist snapshot, status markers, counters, and events

In code, that is no longer implemented as one monolithic runtime script.
`RuntimeEngine` remains the stable stateful façade, while internal collaborators
own the lifecycle bootstrap (`runtime/lifecycle.py`), the one-tick
orchestration block (`runtime/tick_cycle.py`), and the routed post-stage
mutation seams (`runtime/result_application.py` plus the counter, transition,
incident, persistence, and closure-target helper modules beneath it).

Millrace is staged and deterministic by construction. It does not run planning
and execution as concurrent lanes inside one workspace owner. It serializes
stage execution under one scheduler.

## Activation, Active State, And Status Surfaces

When the runtime claims work, it writes active identity into the runtime
snapshot:

- `active_plane`
- `active_stage`
- `active_run_id`
- `active_work_item_kind`
- `active_work_item_id`
- `active_since`

Those fields are authoritative for in-flight ownership.

Millrace also maintains plane status markers:

- `millrace-agents/state/execution_status.md`
- `millrace-agents/state/planning_status.md`
- `millrace-agents/state/learning_status.md` when learning assets are active

These are active-stage-aware surfaces, not just idle-or-terminal markers. While
a stage is executing on a plane, that plane's marker reflects the current
running stage, for example `### BUILDER_RUNNING`, `### ARBITER_RUNNING`, or
`### ANALYST_RUNNING`. When
no stage is active on that plane, the marker falls back to the latest terminal
marker or `### IDLE`.

This makes the text status surface truthful for both operators and monitoring
agents.

## Stage Requests, Entrypoints, And Skills

Millrace separates runtime ownership from stage reasoning by using typed stage
requests plus advisory entrypoint and skill assets.

At execution time the runtime builds a `StageRunRequest` from the active
compiled node plan and the current active work item or closure target. That request
includes the deployed entrypoint path, required and attached skill paths, work
item identity and path when applicable, run directory, status and snapshot
paths, runtime-error context when present, and runner/model/timeout fields.

Entrypoints are plain markdown files under:

- `millrace-agents/entrypoints/execution/*.md`
- `millrace-agents/entrypoints/planning/*.md`
- `millrace-agents/entrypoints/learning/*.md`

Skills are advisory assets under `millrace-agents/skills/`. The shipped model is
skill-only, not role-plus-skill. Each stage has one required stage-core skill,
and entrypoints may direct agents to load additional optional skills only when
those skills are packaged or installed into the deployed skills surface.
`millrace-agents/skills/skills_index.md` lists packaged skills and points to the
supported downloadable optional-skills directory at
`https://github.com/tim-osterhus/millrace-skills/blob/main/index.md`.

The runtime controls which advisory assets are available and attached, but the
stage still does the substantive reasoning work inside its own contract.

## Runners And Harness Dispatch

Millrace does not execute stage logic itself. It dispatches into a runner
adapter. The runtime boundary is intentionally narrow:

- input: `StageRunRequest`
- output: `RunnerRawResult`

The built-in shipped adapters are the Codex CLI adapter and the Pi RPC adapter,
and the architecture is set up so additional adapters can be added later without
rewriting orchestration.

Each stage run produces a run directory under
`millrace-agents/runs/<run-id>/`. It can contain:

- prompt artifacts
- invocation metadata
- stdout/stderr captures
- completion metadata
- normalized stage result JSON
- stage-authored reports such as troubleshoot or arbiter reports

The runtime later inspects these persisted artifacts through `millrace runs ls`,
`millrace runs show`, and `millrace runs tail`.

## Result Normalization And Router Decisions

A stage is allowed to emit only one legal terminal result for its stage. The
runner layer normalizes raw harness output into a typed `StageResultEnvelope`.
That envelope contains:

- stage identity
- plane
- work item identity
- terminal result
- summary status marker
- result class
- timestamps and duration
- artifact paths
- metadata and notes

The runtime then routes that envelope through the router, which decides whether
to run another stage, hand work back into planning, mark the work blocked, or
return the runtime to idle.

This is one of Millrace's sharpest authority seams: the stage emits a legal
result, but the runtime owns the authoritative consequences.

For example:

- a successful `checker` result does not itself move the task to done; the
  runtime may still route to `updater`
- a `consultant` result of `NEEDS_PLANNING` does not directly rewrite queue
  state; the runtime enqueues the appropriate planning incident
- a successful `arbiter` result of `ARBITER_COMPLETE` does not directly close
  the closure target; runtime result application closes it authoritatively

## Recovery Model

Recovery is a first-class part of the runtime. Millrace maintains recovery
counters and routes failure states through recovery stages instead of treating
every blocked result as the end of the road.

Execution-side recovery uses `troubleshooter`, `consultant`, fix-cycle
counting, troubleshoot-attempt counting, and planning handoff when execution
cannot honestly recover by itself.

Planning-side recovery uses `mechanic`, mechanic-attempt counting, and incident
normalization through `auditor`.

There is a second recovery layer as well: runtime-owned post-stage exceptions.
If a stage emitted a legal terminal result but the runtime itself then fails
while applying that result, Millrace emits a runtime-owned error code and routes
that into a repair stage with an explicit runtime error context.

That distinction prevents recovery agents from diagnosing the wrong problem
class.

## Completion Behavior And Arbiter

Millrace does not equate backlog drain with completion.

Instead, the shipped planning loop freezes a `completion_behavior` that activates
`arbiter` when:

- no claimable planning work remains
- no claimable execution work remains
- there is one open closure target
- no remaining lineage work blocks closure

Closure is rooted in explicit lineage metadata carried through work documents:

- `root_spec_id`
- `root_idea_id`

When a root spec first enters the managed lineage, the runtime snapshots the
canonical root spec and seed idea into the Arbiter subtree. Arbiter later judges
against those canonical copies, not mutable operator-authored source files.

Arbiter receives a `closure_target` request, may create or reuse a rubric,
optionally widen into the shared `marathon-qa-audit` skill when narrow evidence
is not enough, and then emits one of:

- `ARBITER_COMPLETE`
- `REMEDIATION_NEEDED`
- `BLOCKED`

Runtime result application then owns the consequences:

- close the closure target
- keep it open and enqueue a planning incident
- or preserve blocked closure state without fabricating work

This is how Millrace reaches real closure rather than simply running until the
queue is empty.

## Control Plane And Daemon Ownership

Millrace has one daemon owner per workspace. That rule is enforced through the
runtime ownership lock under `state/runtime_daemon.lock.json`.

Consequences:

- a second daemon in the same workspace fails fast
- different workspaces may run separate daemons concurrently
- `millrace status watch` can monitor multiple workspaces without taking
  ownership locks

Control actions such as pause, resume, stop, retry-active, clear-stale-state,
and reload-config are exposed through supported CLI commands. If a daemon owns
the workspace, those commands are mailbox-routed. If no daemon owns the
workspace, the control layer can apply the action directly.

This avoids making operators or ops agents manually edit runtime-owned state to
recover a deployed instance.

Pauses now carry sources. Operator pause/resume controls own the `operator`
pause source. Opt-in usage governance owns the `usage_governance` pause source.
That split lets a governance pause block execution without erasing an operator
pause, and lets auto-resume clear only governance-owned pauses when the active
usage blockers have expired.

Usage governance is disabled by default. When enabled, it evaluates between
stages, records token usage from stage-result artifacts into a durable ledger,
can apply rolling five-hour, calendar-week, daemon-session, and per-run runtime
token rules, and can optionally consult Codex ChatGPT OAuth subscription quota
telemetry. Status and monitor surfaces expose the active blockers and whether
auto-resume is possible.

Usage-governance config changes are next-tick runtime changes. `config reload`
reports reload routing and compile status; the following tick evaluates the new
governance settings. Operators should use `millrace status` or the basic daemon
monitor to see whether a governance-owned pause cleared, remained, or was newly
applied.

## Watchers, Intake, And Queue Entry

Millrace can intake work through queue-import surfaces and watcher-driven idea
normalization.

The important conceptual rule is that ideas do not go straight into execution.
They enter planning. In the shipped model, task imports become execution queue
documents, spec imports become planning queue documents, ideas are normalized
into planning specs, planning emits executable tasks into execution, and
execution can hand real blockers back into planning through incidents.

That preserves the three supported handoff shapes the runtime is built around:

1. direct task handoff into execution
2. idea or spec handoff into planning, then decomposition into tasks
3. execution recovery handoff back into planning when execution hits a real
   blocker

## Operator Inspection Surfaces

Millrace is designed to be diagnosable without opening random internal files
first. The main operator surfaces are:

- `millrace status`
- `millrace status watch`
- `millrace queue ls`
- `millrace queue show <WORK_ITEM_ID>`
- `millrace runs ls`
- `millrace runs show <RUN_ID>`
- `millrace runs tail <RUN_ID>`
- `millrace compile validate`
- `millrace compile show`
- `millrace skills ...`
- `millrace doctor`

Use `status` for current runtime snapshot and closure visibility, `queue` for
managed work documents, `runs` for post-run artifacts, `compile` for frozen
structure, `skills` for installed/downloadable skill workflows, and `doctor` for
integrity problems.

## Source Layout And Compatibility Facades

The source tree under `src/millrace_ai/` is deliberately split by ownership:

- `assets/` for packaged entrypoints and skill assets
- `cli/` for operator command surfaces, command-specific view assembly, and
  formatting of already-collected values
- `config/` for runtime config loading and boundary semantics
- `runners/` for adapter dispatch and normalization
- `runtime/` for orchestration logic, pause-source handling, and usage-governance accounting
- `workspace/` for filesystem-backed path models, initialization/bootstrap,
  asset deployment, state, and queue primitives

Recent runtime modules include the daemon monitor event surface, learning
trigger evaluation, skill revision evidence snapshots, active snapshot reset
helpers, usage-governance pause/accounting helpers, and explicit workspace
baseline initialization/upgrade support.

The current cleanup path keeps compatibility facades stable while narrowing
mixed-ownership files. `workspace/paths.py` is now path-only; default bootstrap
payloads live in `workspace/bootstrap_files.py`, and runtime asset deployment
lives in `workspace/asset_deployment.py`. On the CLI side, status, runs,
config, and compile views own their state-loading concerns in dedicated modules
so `cli/shared.py` can stay focused on command wiring and `cli/formatting.py`
can stay focused on rendering already-collected values.

Usage governance is now a runtime authority package rather than one large
module. `runtime/usage_governance/__init__.py` preserves the existing public
imports, while the package-local modules separate Pydantic state/ledger models,
state-file persistence, ledger repair, runtime-token windows, subscription
quota telemetry, monitor events, and the engine-facing evaluation boundary.
Compiled graph authority follows the same pattern: the
`runtime/graph_authority/` facade preserves activation and routing imports,
while activation, validation, policy lookup, recovery counters, stage mapping,
and plane-specific routing live in separate package modules.

A set of thin root-module facades is intentionally preserved so older import
surfaces still work while the package is internally modularized. That is why
there are still top-level modules such as `millrace_ai.paths`,
`millrace_ai.state_store`, `millrace_ai.runner`, and `millrace_ai.runtime_lock`
that re-export newer package-local implementations.

## Where To Go Next

Use this document as the front door, then drop into the narrower references when
needed:

- `README.md` for the public landing-page framing
- `docs/skills/millrace-ops-agent-manual/SKILL.md` if you are an external
  agent deciding when to use Millrace and how to operate it safely
- `docs/runtime/millrace-runtime-architecture.md` for the runtime/storage model
- `docs/runtime/millrace-compiler-and-frozen-plans.md` for compile semantics
- `docs/runtime/millrace-modes-and-loops.md` for loop topology and mode maps
- `docs/runtime/millrace-arbiter-and-completion-behavior.md` for true closure
- `docs/runtime/millrace-cli-reference.md` for operator commands
- `docs/runtime/millrace-runner-architecture.md` for harness dispatch
- `docs/runtime/millrace-entrypoint-mapping.md` for deployed entrypoint and
  skill surfaces
- `docs/source-package-map.md` for the source tree and compatibility facades
