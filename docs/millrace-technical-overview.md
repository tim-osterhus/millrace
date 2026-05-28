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
supports repo-local skills, the lightweight decision layer is
`docs/skills/millrace-autonomous-delegation/SKILL.md` for trusted sessions
where the operator has authority to choose the execution path. Once Millrace is
requested or selected, `docs/skills/millrace-ops-agent-manual/SKILL.md` is the
procedural runbook for workspace validation, CLI operation, monitoring, and
safe intervention.

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

Millrace persists three broad artifact families.

### 1. Managed work items

Most queue-facing work items are canonical markdown documents:

- `millrace-agents/tasks/{queue,active,done,blocked}/*.md`
- `millrace-agents/probes/{queue,active,done,blocked}/*.md`
- `millrace-agents/specs/{queue,active,done,blocked}/*.md`
- `millrace-agents/incidents/{incoming,active,resolved,blocked}/*.md`
- `millrace-agents/learning/requests/{queue,active,done,blocked}/*.md`

Those families are human-readable documents with headed sections. JSON remains
acceptable as an import format for task, probe, and spec intake, but the
long-lived queue artifact for those families is markdown.

Blueprint Planning also ships a graph-owned `blueprint_draft` work family. It
is intentionally JSON-backed under:

- `millrace-agents/blueprints/drafts/{queue,active,approved,blocked,canceled,superseded}/*.json`

Blueprint drafts are not source tasks. They are Planning-plane draft records
that Contractor Blueprint and Evaluator Blueprint process before approved
packets can become generated execution tasks.

The runtime is therefore built around operator-facing work contracts, not one
file format. Markdown work documents and JSON-backed Blueprint drafts both
satisfy typed contracts and queue lifecycle rules.

The public Python contract surface is `millrace_ai.contracts`. Internally, that
surface is a package under `src/millrace_ai/contracts/` so foundational enums,
stage metadata, work documents, stage results, loop/mode definitions, runtime
snapshots, mailbox envelopes, compiler diagnostics, and recovery counters can be
reviewed independently while preserving the root import contract.
`contracts/stage_metadata.py` is the stage legality registry: stage plane
membership, running markers, legal terminal markers, blocked terminal results,
and result-class policy derive from there for contracts, runner prompts,
normalization, graph lookup, entrypoint linting, and built-in stage-kind asset
validation.

Entrypoint assets live under `src/millrace_ai/assets/entrypoints/`, which is
also the Python package that parses and lints those markdown manifests. Parsing,
asset path inference, advisory skill-reference checks, lint policy, and
diagnostic rendering are split into named modules behind the
`millrace_ai.assets.entrypoints` facade.

Runner adapters remain behind the public `millrace_ai.runners` and
`millrace_ai.runner` surfaces. The Codex CLI adapter keeps one public adapter
class while command construction, permission flags, artifact materialization,
timeout-marker reconciliation, and token extraction live in focused
`runners/adapters/codex_cli_*` modules.

### 2. Runtime state, run artifacts, and specialized stores

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
- execution capability approvals under `millrace-agents/approvals/`
- mailbox command envelopes and archives
- run-scoped runner artifacts, capability-gate artifacts, and stage results

Completion behavior adds a third specialized subtree under `millrace-agents/arbiter/`
for canonical root contracts, closure-target state, rubrics, verdicts, and
reports.

Blueprint Planning adds its own runtime-owned state under
`millrace-agents/blueprints/` for manifests, drafts, candidate packets,
critiques, evaluations, promotions, generated tasks, and structured repair
artifacts. Recon, Learning, approvals, archived state, and schema-epoch data
also have dedicated subtrees instead of being folded into generic queue
directories.

### 3. Deployed runtime assets

`millrace init` and `millrace upgrade --apply` deploy managed runtime assets
into the workspace: modes, graphs, loops, registry assets, entrypoints,
stage-core skills, and packaged skill indexes. Operators should refresh those
managed assets with `millrace upgrade` rather than hand-editing deployed copies
unless they intentionally localize a file.

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
- which workflow primitive assets define work-item families, document adapters,
  queue claim policy, terminal lifecycle actions, runtime effects, recovery and
  failure policies, and workspace schema epoch compatibility

`compiled_plan.json` materializes the stage-kind registry and graph-loop assets
into explicit node plans, raw transitions, normalized compiled intake entries,
a normalized closure-target activation entry, compiled resume and threshold
recovery policies, compiled workflow primitives, scheduler lane policy, and
explicit terminal semantics. The live runtime executes stage-request
construction, claim activation, closure-target activation, recovery, runtime
effect dispatch, source lifecycle mutation, and post-stage routing from that
compiled plan.

The compiler currently ships with baseline, learning-enabled, integrated, and
Blueprint built-in modes:

- baseline modes: `default_codex`, `default_pi`
- learning-enabled modes: `learning_codex`, `efficient_learning_codex`,
  `learning_pi`, `learning_codex_integrated`, `blueprint_learning_codex`
- integrated quality modes: `default_codex_integrated`,
  `learning_codex_integrated`
- Blueprint Planning modes: `blueprint_codex`, `blueprint_learning_codex`
- execution loops: `execution.standard`, `execution.with_integrator`
- planning loops: `planning.standard`, `planning.blueprint`
- learning loop: `learning.standard`

`standard_plain` remains accepted as a compatibility alias that canonicalizes to
`default_codex` before compile diagnostics, compiled-plan ids, and runtime
snapshot state are written.

Compile output is operator-visible through `millrace compile validate`,
`millrace compile show`, and `millrace compile graph`. Failed recompiles
preserve the last known good plan as an inspection and rollback artifact.
That does not mean the daemon may keep using a stale plan after inputs drift.
Runtime startup and config reload refuse stale last-known-good authority when
current compile inputs no longer match.

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
- mode-local model aliases and alias assignments
- scheduler lane and concurrency policy
- learning trigger rules

In the shipped baseline, that runner binding map is how harness choice is
expressed:

- `default_codex` binds all shipped stages to `codex_cli`
- `default_pi` binds all shipped stages to `pi_rpc`
- `learning_codex` binds execution, planning, and learning stages to
  `codex_cli`
- `efficient_learning_codex` binds the same standard loops to `codex_cli`,
  keeps Integrator inactive, and assigns stage model/depth through mode-local
  aliases
- `learning_pi` binds execution, planning, and learning stages to `pi_rpc`
- `default_codex_integrated` and `learning_codex_integrated` bind Codex stages
  while selecting `execution.with_integrator`
- `blueprint_codex` binds Codex stages while selecting `planning.blueprint`
  and standard execution
- `blueprint_learning_codex` binds Codex stages while selecting
  `planning.blueprint`, `learning.standard`, and standard execution

The loop topology does not fork just because the harness changes. It forks only
when the operator intentionally selects an integrated quality mode or the
Blueprint Planning posture.

Model selection can be controlled through the compiler-owned
`model_aliases` / `model_assignment` surface. Workspace config ships `fast`,
`standard`, and `deep` aliases, and operators can assign aliases globally, by
loop, or by stage with `millrace model-aliases ...`. Mode assets may also carry
mode-local aliases; `efficient_learning_codex` uses that mechanism so its
mixed-cost Codex profile is part of the mode rather than an accidental
workspace default. Workspace stage and loop assignments still take precedence
over mode-local assignments.

Integrated Codex modes are quality-first and more expensive. Their execution
path is:

```text
builder -> integrator -> checker -> fixer/doublechecker -> updater
```

Integrator inspects Builder evidence and the implementation diff, runs explicit
or discoverable integration gates, checks changed docs/config/assets when
relevant, and writes `integration_report.md` before Checker performs normal QA.

The learning modes preserve execution/planning mutual exclusion and freeze
scheduler lane/concurrency policy into the compiled plan. Daemon mode enforces
that policy through lane-keyed dispatch: default modes remain one active lane
per plane, while experimental multi-lane policy must declare valid conflict
coverage before it can run. Runtime-owned mutation stays single-writer and
serialized by the supervisor. Learning trigger rules can enqueue targeted
learning requests from runtime evidence. Built-in success learning starts at
Analyst; learning-enabled shipped modes trigger Librarian
after Planner completes so relevant remote optional skills can be installed
into the workspace while foreground work continues. Direct Curator trigger
rules are reserved for custom modes that name a concrete destination.
Troubleshooting and consultation recovery also route learning evidence through
Analyst.

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
- thinking level
- model-assignment alias id and source
- timeout seconds

That freeze step is what makes later execution deterministic and inspectable.
The runtime no longer has to keep inferring structure from loose config while it
is in the middle of a run.

Operators can inspect that legal topology with `millrace compile graph`. That
compiled stage graph is a control-flow graph and may contain intentional
recovery cycles; it is not a DAG runtime. Concrete run history is separate and
is recorded as a per-run trace graph.

## The Shipped Planning And Execution Planes

The standard execution loop is:

- `builder`
- `checker`
- `fixer`
- `doublechecker`
- `updater`
- `troubleshooter`
- `consultant`

The integrated execution loop inserts `integrator` after `builder` for
operator-selected high-assurance Codex modes.

The standard planning loop is:

- `recon`
- `planner`
- `manager`
- `mechanic`
- `auditor`
- `arbiter`

The Blueprint Planning loop is:

- `recon`
- `planner`
- `manager_blueprint`
- `contractor_blueprint`
- `evaluator_blueprint`
- `mechanic_blueprint`
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

- `recon` classifies lightweight probes and emits a persisted recon packet plus
  either a generated task, generated spec, no-op, or blocked result
- `planner` synthesizes or refines a spec
- `manager` decomposes it into executable tasks

If planning hits blockage or inconsistency, `mechanic` handles repair-oriented
recovery. `auditor` is the incident intake entrypoint. `arbiter` is special: it
is part of the planning loop topology but is not a normal queued successor. It
is activated by completion behavior when backlog drain makes closure evaluation
possible.

Blueprint Planning replaces the standard Planner-to-Manager decomposition path
with a stricter draft-packet loop:

- `planner` emits the planning disposition for the source spec or incident
- `manager_blueprint` writes a Blueprint manifest and ordered draft records
- `contractor_blueprint` proposes one candidate implementation packet for one
  active draft
- `evaluator_blueprint` approves that packet into one generated execution task
  or rejects it with a critique that routes the same draft back to Contractor
- `mechanic_blueprint` handles Blueprint Planning recovery and structured
  runtime-effect repair

Blueprint Planning still keeps implementation in Execution. Contractor
Blueprint proposes a plan; Builder edits source only after Evaluator Blueprint
approves and the runtime promotes a generated task. Blueprint closure is also
stricter than standard Planning: Arbiter remains suppressed while same-lineage
drafts, candidate packets, approved-but-unpromoted packets, or generated tasks
remain open.

The current learning loop is:

- `analyst`
- `professor`
- `curator`
- `librarian`

Learning is opt-in through `learning_codex`, `efficient_learning_codex`,
`learning_pi`, `learning_codex_integrated`, or `blueprint_learning_codex`.
Its normal path is Analyst evidence analysis, Professor synthesis, then Curator
acceptance and skill-update curation. Librarian is a targeted one-off Learning
stage that runs after Planner in learning-enabled modes, checks Planner output
against local and remote skill indexes, and installs up to eight relevant
uninstalled optional remote skills into the workspace. Learning can terminate
with `CURATOR_COMPLETE`, `LIBRARIAN_COMPLETE`, a stage-specific no-op outcome,
or `BLOCKED`. Learning requests live under `millrace-agents/learning/requests/`,
and targeted requests can start at a specific learning stage when a
compiler-frozen trigger rule says that stage is the right entry point. Generic
success-triggered learning starts at Analyst; direct Curator triggers require a
safe destination such as `target_skill_id` or `preferred_output_paths`.
When Curator already applies an evidence-backed patch to a
workspace-installed skill, it may also perform a format-only migration to the
current skill section contract if lint reports package-shape drift and the
existing semantics can be preserved. That migration is recorded separately from
the behavior patch and does not grant source promotion authority.

The runtime-authoritative graph loops make the shipped intake mapping explicit:

- execution graph: `task -> builder`
- planning graph: `probe -> recon`
- planning graph: `spec -> planner`
- planning graph: `incident -> auditor`
- learning graph: `learning_request -> analyst`
- selected learning trigger rules may target a specific learning stage, such as
  Planner completion requesting Librarian optional-skill preparation

## Runner Baselines

Millrace currently ships two first-class built-in runner adapters:

- `codex_cli`
- `pi_rpc`

Codex remains the canonical bootstrap posture. New workspaces default to
`runtime.default_mode = "default_codex"` and `runners.default_runner = "codex_cli"`.

When output quality matters more than stage count, operators can select
`default_codex_integrated` or `learning_codex_integrated` after refreshing
managed workspace assets with `millrace upgrade --apply`. A running daemon can
pick up a config-driven `runtime.default_mode` change through
`millrace config reload` unless it was started with an explicit `--mode`
override.

Pi is opt-in through `default_pi` or direct runner selection. The Pi adapter
uses RPC mode and disables Pi-native context-file and skill discovery by
default so the baseline stays governed by Millrace entrypoints rather than
ambient Pi project state.

## Deterministic Tick Lifecycle

The runtime engine runs one deterministic tick at a time. Daemon mode repeats
those ticks; bounded one-off operation uses `millrace run daemon --max-ticks 1`.

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
12. evaluate compiled execution capability grants and any pending approvals
13. dispatch permitted runner work through the compiled lane scheduler
14. normalize the result and apply the router decision
15. record post-stage usage and persist snapshot, status markers, counters, and events

In code, that is no longer implemented as one monolithic runtime script.
`RuntimeEngine` remains the stable stateful façade, while internal collaborators
own the lifecycle bootstrap (`runtime/lifecycle.py`), the one-tick
orchestration block (`runtime/tick_cycle.py`), and the routed post-stage
mutation seams (`runtime/result_application.py` plus the counter, transition,
incident, persistence, and closure-target helper modules beneath it).
Runtime effect operations live under `runtime/effects/`, recovery helpers live
under `runtime/recovery/`, and request-context assembly lives under
`runtime/context/`.

Millrace is staged and deterministic by construction. Default modes serialize
stage execution under one scheduler lane per plane. Experimental multi-lane
policy is compile-validated before it can dispatch, and runtime-owned mutation
remains single-writer.

## Activation, Active State, And Status Surfaces

When the runtime claims work, it writes active identity into the runtime
snapshot:

- `active_plane`
- `active_stage`
- `active_run_id`
- `active_work_item_kind`
- `active_work_item_id`
- `active_since`
- `active_runs_by_plane`
- `lanes_by_id`

The legacy foreground active fields remain a projection. Canonical in-flight
ownership lives in `active_runs_by_plane`; lane status, active run ids, active
work refs, and lane plan/fingerprint identity live in `lanes_by_id`. Each
active run records the compiled-plan id and fingerprint that launched it, so
result application can keep routing against the launch contract even when a
config reload has compiled a newer pending plan.

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
paths, runtime-error context when present, runner/model/timeout fields, and the
compiled execution capability grants for that node.
Runtime-built requests also include deterministic request-context artifacts
under the run directory. The prompt adapter includes the rendered prompt
context, while the bundle and render manifest preserve visible refs,
operator-only redactions, omitted providers, and content hashes for inspection.

Before dispatch, Millrace evaluates required execution capability grants. Denied
or unsupported required grants block before runner invocation. Approval-required
grants create or reuse runtime approval objects and block until approved.
Advisory grants can proceed when policy permits them, but they remain labeled
as advisory in prompt context, artifacts, events, and inspection output.

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
Analyst can refresh that index into `millrace-agents/skills/remote_skills_index.md`
and install relevant remote skill ids with `millrace skills install <skill_id>`
before using them as normal workspace-local optional skills. Librarian uses the
same supported commands after Planner completes in learning-enabled modes so
optional skill payloads stay workspace-local instead of being bundled into the
base runtime package.

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

Runner adapters also report contextual capability support. Codex `maximum`
permission remains intentionally broad, so boundaries that Millrace cannot
enforce are marked advisory rather than falsely reported as enforced. The Pi
adapter is similarly conservative unless a grant maps to a runtime-owned or
adapter-owned boundary.

Stages support explicit model and runner-neutral thinking selection through
runtime config. `stages.<stage>.model` sets the model, and
`stages.<stage>.thinking_level` sets the compiled thinking level for execution,
planning, and learning stages including `professor` and `librarian`. Codex
translates that value to `model_reasoning_effort`; Pi translates it to
`--thinking`. The compiled plan, stage request, runner invocation artifact,
persisted stage result, and `runs show` output all carry the selected thinking
level when it is configured. `stages.<stage>.model_reasoning_effort` remains
accepted as a Codex compatibility alias.

The alias surface is a higher-level way to control the same model and thinking
fields. `model_aliases.<alias>` defines model/depth pairs, and
`model_assignment` selects aliases globally, by loop, or by stage. The compiler
records `model_assignment_alias_id` and `model_assignment_source` on each node
so operators can see which policy produced a runner request.

Each stage run produces a run directory under
`millrace-agents/runs/<run-id>/`. It can contain:

- prompt artifacts
- invocation metadata
- stdout/stderr captures
- completion metadata
- normalized stage result JSON
- `run_trace.json`
- stage-authored reports such as troubleshoot or arbiter reports

The runtime later inspects these persisted artifacts through `millrace runs ls`,
`millrace runs show`, `millrace runs tail`, and `millrace runs trace`.

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
- runner/model identity
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
  state; the runtime enqueues the appropriate planning incident and preserves
  the source work item's root lineage on that incident
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

Recon handoff validation is deliberately stricter. If Recon returns
`RECON_TO_EXECUTION` or `RECON_TO_PLANNING` but its packet or generated
handoff artifact is invalid, Millrace records `recon_handoff_invalid`, blocks
the active probe, and does not convert that probe into Planner, Manager, or
Mechanic work.

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

Runtime-created handoff incidents participate in that same lineage contract.
When an execution task escalates to planning, the runtime copies the source
task's root lineage and source spec id onto the incident before queueing it.
That keeps same-lineage remediation claimable while unrelated root specs remain
backpressured behind the open closure target.

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
execution capability approve/deny, and reload-config are exposed through
supported CLI commands. If a daemon owns the workspace, those commands are
mailbox-routed. If no daemon owns the workspace, the control layer can apply
the action directly.

`process_running` is a runtime truth claim, not a durable wish. Runtime close
clears it, status reports it as true only while an ownership lock is active,
and `clear-stale-state` treats clearing a stale process-running bit as an
applied repair.

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
normalization. Task, probe, and spec imports accept markdown or JSON; canonical
queue artifacts remain markdown.

The important conceptual rule is that ideas do not go straight into execution.
They enter planning. In the shipped model, task imports become execution queue
documents, probe imports become Recon-classified planning intake, spec imports
become planning queue documents, ideas are normalized into planning specs,
planning emits executable tasks into execution, and
execution can hand real blockers back into planning through incidents.

That preserves the three supported handoff shapes the runtime is built around:

1. direct task handoff into execution
2. probe handoff into Recon, then generated task/spec/no-op/blocked routing
3. idea or spec handoff into planning, then decomposition into tasks
4. execution recovery handoff back into planning when execution hits a real
   blocker

## Operator Inspection Surfaces

Millrace is designed to be diagnosable without opening random internal files
first. The main operator surfaces are:

- `millrace status`
- `millrace status watch`
- `millrace run daemon --monitor basic`
- `millrace queue ls`
- `millrace queue show <WORK_ITEM_ID>`
- queue intervention commands such as `retry-blocked`, `cancel`,
  `archive-blocked`, `supersede`, `retarget-dependency`, and `repair-lineage`
- `millrace incident resolve`, `cancel`, and `archive-invalid`
- `millrace runs ls`
- `millrace runs show <RUN_ID>`
- `millrace runs tail <RUN_ID>`
- `millrace runs trace <RUN_ID>`
- `millrace compile validate`
- `millrace compile show`
- `millrace compile graph`
- `millrace config show`, `validate`, and `reload`
- `millrace modes list` and `millrace modes show`
- `millrace model-aliases ...`
- `millrace approvals ...`
- `millrace skills ...`
- `millrace upgrade`
- `millrace doctor`
- `millrace-web serve` from the separate `millrace-web` package

Use `status` for current runtime snapshot and closure visibility, `queue` for
managed work documents, `runs` for post-run artifacts, `compile` for frozen
structure, `config` and `modes` for selected runtime policy, `approvals` for
capability-gated stages, `skills` for installed/downloadable skill workflows,
`doctor` for integrity problems, and `millrace-web` for read-only local
dashboard inspection.

## Source Layout And Compatibility Facades

The source tree under `src/millrace_ai/` is deliberately split by ownership.
The most important package boundaries are:

- `architecture/` for workflow primitive and graph contract models
- `assets/` for packaged modes, graphs, registries, loops, entrypoints, and
  bundled skills
- `cli/` for operator command surfaces and command-specific rendering
- `compilation/` for compile orchestration, validation, materialization,
  policy compilation, asset fingerprints, and graph exports
- `config/` for runtime config loading and recompile/next-tick boundary
  semantics
- `contracts/` for the public typed contract facade and domain contract modules
- `doctor/` for workspace health and asset-integrity checks
- `runners/` for adapter dispatch and normalization
- `runtime/` for daemon lifecycle, tick orchestration, graph authority,
  request context, effects, recovery, approval checks, usage governance, and
  result application
- `workspace/` for path models, initialization/bootstrap, asset deployment,
  queue stores, work-document parsing, family adapters, and source-state files

The optional dashboard is intentionally outside the base runtime package under
`packages/millrace-web/`. It depends on `millrace-ai`, serves read-only state,
and does not own daemon mutation.

For the full source ownership map, use `docs/source-package-map.md`.

A set of thin root-module facades is intentionally preserved so older import
surfaces still work while the package is internally modularized. That is why
there are still top-level modules such as `millrace_ai.paths`,
`millrace_ai.state_store`, `millrace_ai.runner`, and `millrace_ai.runtime_lock`
that re-export newer package-local implementations.

Source-layout guardrails now live in `tests/test_import_cycles.py` and
`tests/test_source_hygiene.py`. They are intentionally narrow: no concrete
local import cycles, no lower-level imports from `cli`, no contract imports from
higher-level domains, path-only workspace modeling, no new generic helper
modules, and no wildcard imports.

## Where To Go Next

Use this document as the front door, then drop into the narrower references when
needed:

- `README.md` for the public landing-page framing
- `docs/doc-index.md` for the complete documentation map
- `docs/skills/millrace-autonomous-delegation/SKILL.md` if you are an external
  agent authorized to decide whether substantial work should use Millrace
- `docs/skills/millrace-ops-agent-manual/SKILL.md` if you are an external
  agent operating Millrace safely after it is requested or selected
- `docs/runtime/millrace-runtime-architecture.md` for the runtime/storage model
- `docs/runtime/millrace-compiler-and-frozen-plans.md` for compile semantics
- `docs/runtime/millrace-modes-and-loops.md` for loop topology and mode maps
- `docs/graphs/graphs-index.md` for shipped mode-to-plane graph
  configurations and per-plane graph references
- `docs/runtime/millrace-arbiter-and-completion-behavior.md` for true closure
- `docs/runtime/millrace-cli-reference.md` for operator commands
- `docs/runtime/millrace-runner-architecture.md` for harness dispatch
- `docs/runtime/millrace-entrypoint-mapping.md` for deployed entrypoint and
  skill surfaces
- `docs/source-package-map.md` for the source tree and compatibility facades
