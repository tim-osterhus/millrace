# Millrace Runtime Authority Map

## Scope

This map records where runtime mutation authority lives across the major
Millrace work flows. It complements
`docs/runtime/millrace-runtime-architecture.md`,
`docs/runtime/millrace-compiler-and-frozen-plans.md`, and
`docs/runtime/millrace-arbiter-and-completion-behavior.md`.

The central rule is:

- stages and runner adapters produce artifacts and terminal results
- the runtime normalizes those results and mutates durable queue, lifecycle,
  snapshot, status, counter, closure-target, Blueprint, mailbox, and event
  state

Stage prompts may receive preferred output paths, active work-item paths, and
root-source contract paths. Those paths are artifact contracts, not permission
to move queue documents or rewrite authoritative runtime state directly.

Owner names below refer to implementation homes under `src/millrace_ai/`.
Public compatibility facades may re-export the same behavior.

## Kernel Boundary

The runtime kernel (`src/millrace_ai/runtime/`, `src/millrace_ai/workspace/`)
owns orchestration, lifecycle, and durable state. Workflow-semantic decisions
(routing, terminal actions, claim policies, recovery policies) are resolved
from the compiled plan, not from kernel-level plane-enum or stage-enum
branches (see ADR-0012 through ADR-0016). ADR-0016 records the active
extension boundary bridges and the remaining kernel-to-domain compatibility
facades while that migration continues.

The compiled-plan router path uses:

- `runtime/graph_authority/generic_router.py` — active compiled-graph routing
  logic (`route_generic_stage_result_from_graph`) shared by all planes.
- `runtime/graph_authority/routing.py` — single active dispatch entrypoint
  (`route_stage_result_from_graph`) that validates stage-result identity and
  routes every plane directly through the generic router with plane-agnostic
  formatter callbacks. It does not dispatch through per-plane wrappers or
  accept route-time max-cycle recovery knobs. Generic formatters derive
  fallback route reasons and failure classes from ``stage_result.node_id``
  (compiled node identity) rather than ``source_stage.value`` (runtime
  stage-name string).
- `runtime/graph_authority/execution.py`, `planning.py`, and `learning.py` —
  compatibility wrappers that forward to the generic router for import
  compatibility.
- `millrace_ai.router` — stable compatibility facade; active dispatch does
  not use its legacy plane-specific functions.

## Shared Authority Layers

### Compile-time authority

- `compiler.py` and `compilation/` build and persist
  `millrace-agents/state/compiled_plan.json`.
- `compilation/workspace_plan.py`, graph materialization, workflow primitive
  validation, queue-claim policy validation, runtime-effect rule validation,
  effect-operation catalog validation, learning-trigger validation, and
  completion-behavior compilation decide which plane graphs, work-item
  families, queue policies, transitions, failure policies, runtime effects,
  learning triggers, and closure activation rules may run.
- `runtime/compiled_plans.py` preserves the active launch-plan authority for
  already-dispatched work when config reload compiles a newer pending plan.

### Intake and queue selection authority

- Operator intake commands, mailbox-applied intake, watcher intake, and
  runtime-generated work write typed queue artifacts under
  `millrace-agents/`.
- `runtime/mailbox_intake.py` owns daemon-safe mailbox application.
- `runtime/watcher_intake.py` owns watcher/poll intake and idea normalization.
- `runtime/learning_triggers.py`, `runtime/handoff_incidents.py`,
  `runtime/closure_boundary.py`, and `runtime/closure_transitions.py` own
  runtime-generated follow-up work.
  Learning trigger/promotion calls from tick-cycle and supervisor paths, and
  closure transition calls from result-application paths, resolve through the
  built-in extension boundary registry before delegating to those compatibility
  implementations.
- `runtime/activation.py` decides which work can be claimed for a plane, using
  `runtime/scheduler_policy.py` for the compiled foreground order,
  closure-target inversion, and learning eligibility shared with tick and
  supervisor.
- `workspace/queue_store.py` and `workspace/queue_selection.py` move queue
  artifacts into active state. Planning queue ordering is driven by the
  compiled per-plane queue claim policy when one is present.

### Request, runner, and normalization authority

- `runtime/stage_requests.py` builds `StageRunRequest` from the active run,
  active work item or closure target, and compiled node plan. Active
  work-item artifact-path resolution is delegated through work-family queue
  adapters when family contracts declare them.
- `runtime/context/` writes deterministic request-context artifacts and prompt
  context. `runtime/request_context.py` remains the compatibility facade.
- `runtime/capability_gates.py` and `runtime/approvals.py` evaluate compiled
  execution-capability grants before runner invocation.
- `runners/dispatcher.py` resolves the selected runner adapter.
- runner adapters write runner-owned invocation/completion/stdout/stderr/event
  artifacts under the run directory and return `RunnerRawResult`.
- `runners/normalization/` converts raw runner output into
  `StageResultEnvelope`.

### Mutation and inspection authority

- `runtime/result_application.py` is the post-stage routing facade.
- `runtime/effect_execution.py`, `runtime/effects/`,
  `runtime/lifecycle_interpreter.py`, and `workspace/queue_lifecycle.py` apply
  compiled operation-id-first runtime-effect and source-lifecycle intents.
  Runtime-effect rules declare source completion/blocking consequence metadata
  for `REQUEST_COMPLETE_SOURCE` and `REQUEST_BLOCK_SOURCE`, while repair-route
  exceptions remain failure-policy-owned.
- `runtime/work_item_transitions.py`, `runtime/recon_transitions.py`,
  `runtime/effects/operation_runners/`, `runtime/closure_boundary.py`,
  `runtime/closure_transitions.py`, `runtime/result_counters.py`,
  `runtime/stage_result_persistence.py`, `runtime/run_traces.py`,
  `runtime/snapshot_state.py`, and
  `workspace/*_state.py` own the durable mutations for their domains.
  Recon and closure post-stage mutations are reached through built-in
  extension boundary adapters from `runtime/result_application.py`; Blueprint
  operation runners and context providers remain documented compatibility
  facades until their implementation moves behind extension-owned interfaces.
- `runtime/supervisor.py` serializes result application in daemon mode even
  when workers run concurrently.
- `runtime/inspection.py`, CLI status/runs/queue views, `doctor.py`, monitor
  events, `runtime_snapshot.json`, status markdown files,
  `runtime/status_projections.py` (shared family-keyed queue-depth,
  scope-keyed status, and lane-keyed active-run projection helpers with
  plane-keyed compatibility derivations), `run_trace.json`,
  runtime events, and run artifacts expose read-only inspection surfaces.

### Runtime-operation registry authority

- Runtime-operation definitions are discovered from packaged registry assets
  under `src/millrace_ai/assets/registry/runtime_operations/` and compiled
  into `CompiledRunPlan.runtime_operations_by_id`.
- Each operation declares an `operation_id`, `allowed_contexts`
  (`terminal_action` or `runtime_effect`), `required_capabilities`,
  `mutation_phase`, and `idempotency` policy.
- Shipped operations cover the four Recon terminal routes
  (`recon.enqueue_task`, `recon.enqueue_spec`, `recon.noop`,
  `recon.block_work_item`) and two generic lifecycle operations
  (`lifecycle.complete_work_item`, `lifecycle.block_work_item`).
- `compilation/validation/lifecycle.py` validates that every compiled terminal
  action's `runtime_operation_id` exists in `runtime_operations_by_id`,
  declares `terminal_action` in its allowed contexts, and satisfies its
  required capabilities. Unknown operations, wrong-context operations, and
  missing capabilities all fail compile.
- At dispatch, `graph_authority/terminal_actions.py` resolves the operation id
  from the compiled terminal action and attaches it to every
  `RouterDecision`. Recon routing in `recon_transitions.py` maps that id to
  the correct Recon behavior through the `_RECON_ROUTE_BY_RUNTIME_OPERATION`
  table. The fixed `_TERMINAL_ACTION_RUNTIME_OPERATION_IDS` whitelist has been
  removed from active source.
- Runtime-effect dispatch (`effect_execution.py`) uses a separate
  runtime-effect operation catalog; the `allowed_contexts` field keeps the two
  contexts distinct.

### Runtime-effect operation interpretation authority

Runtime-effect operations execute through one of two dispatch paths
based on compiled runner identity:

**Interpreted runner path** (`runner_id = "interpreted_runtime_effect"`):
- Operations are defined in `assets/registry/runtime_effect_operations/`
  and declare a step list. Each step references a `primitive_id` and optional
  `input_bindings`, `params`, `reads_artifact_ids`, and `mutation_phase`.
- The runtime resolves the runner through
  `compiled_plan.runtime_effect_runners_by_id` by finding the runner whose
  `operation_ids` includes the operation. Operations do not carry their own
  `runner_id`; runner ownership is declared on the runner definition via
  `RuntimeEffectOperationRunnerDefinition.operation_ids`.
- `effect_execution.py:_operation_selection_for_rule()` detects
  `runner_id == INTERPRETED_RUNNER_ID` and constructs an inline handler that
  delegates to `interpret_operation()` in `runtime/effects/interpreter.py`.
  It does not call `_handler_for_operation()` for interpreted runners.
- The interpreter walks compiled steps, resolves `$artifact.<id>`,
  `$context.<key>`, and `$store.<id>` bindings, dispatches each step's
  `primitive_id` to the `PrimitiveExecutorRegistry` in
  `runtime/effects/primitives.py`, and accumulates results into a
  `RuntimeEffectResult`.
- Five primitives have interpreted executors: `artifact_presence`,
  `artifact_model_parse`, `persist_record`, `enqueue_work_items`,
  `emit_event`. The other 13 primitives in the shipped asset
  (`default_runtime_effect_primitives.json`) are marked
  `non_interpreted_compatibility: true` and have no executor.
- Mutating primitives (`persist_record`, `enqueue_work_items`) write durable
  JSONL journal records to `millrace-agents/state/runtime-effect-journal/
  <operation_id>.jsonl` (`runtime/effects/journal.py`). Started, completed,
  and failed records carry operation, runner, source work-item, run/request,
  step, primitive, status, timestamp, and idempotency-hash context. The
  interpreter loads completed idempotency hashes on each invocation and skips
  steps whose hash matches a completed record. Mutating primitive exceptions,
  executor-returned `pre_mutation_failure` decisions, and started-record
  idempotency conflicts append failed records before blocking; non-equivalent
  duplicates fail with `interpreted_idempotency_conflict`.
- Compile validation (`compilation/validation/runtime_effects.py`)
  derives interpreted primitive authority from compiled definitions whose
  `non_interpreted_compatibility` marker is `false`, rejects interpreted-runner
  operations whose primitives have no interpreted executor, rejects
  interpreted executor ids without matching compiled primitive definitions,
  and checks primitive-required runner capabilities for both interpreted and
  legacy runners.
- **Current scope:** all shipped operations use the legacy handler-backed
  runner (`legacy_python_handler`). The interpreted runner is activated only
  by test fixture operations. Documentation claims must not imply shipped
  operation migration beyond fixture coverage.

**Legacy handler-backed path** (`runner_id = "legacy_python_handler"`):
- All six shipped runtime-effect operations (Planner disposition, five
  Blueprint operations) use this runner.
- Handler callables are registered in `runtime/effects/legacy.py` and
  executed directly. The handler registry in `runtime/effects/registry.py`
  provides operation-id-to-handler lookup.
- `effect_execution.py:_handler_for_operation()` resolves handlers for
  legacy-runner operations through the registry; `_legacy_handler_id_for_
  operation()` maps operation IDs to legacy handler IDs for result
  annotation and failure-policy matching.
- `runtime/effects/operation_runners/` contains the Python mutation code
  for these handler-backed operations (Blueprint candidate evaluation,
  Mechanic repair, etc.).

### Scheduler-policy authority

- Scheduler-policy definitions are discovered from packaged registry assets
  under `src/millrace_ai/assets/registry/scheduler_policies/` and compiled
  into `CompiledRunPlan.scheduler_policy`.
- Modes select a policy explicitly via `scheduler_policy_id`, or the compiler
  auto-selects `default.three_plane` (for learning-enabled modes) or
  `default.two_plane` (for two-plane modes). The auto-selected policy must
  match the mode's plane set exactly.
- Each policy declares `plane_order`, lane membership with
  `allowed_family_ids` per lane, `claim_policy_id` references, family order,
  `foreground_order`, `closure_priority`, `predicates`, `rules`,
  `learning_dispatch` (`"inline"`, `"deferred"`, or reserved
  `"interleaved"`), lane conflict policies, and multi-lane guardrails
  (`experimental_multi_lane`).
- `runtime/scheduler_policy.py` provides a shared compiled-policy interpreter
  used by `activation.py`, `tick_cycle.py`, and `supervisor.py`. It evaluates
  matching predicate-backed rules before falling back to the scalar
  `foreground_order`/`closure_priority` compatibility path, inverts execution
  before planning when the closure path still uses closure priority, and gates
  separate learning dispatch behind `learning_dispatch != "deferred"`.
- Four residual-surface interpreter helpers give scheduler policy
  authority over runtime behavior that was previously split across compiled
  graph, mode, work-item-family, queue-claim, completion, and recovery-policy
  surfaces:
  - `fallback_entry_selection(policy)` returns `"recon_on_idle"`, `"skip"`,
    or `"pause"` from the compiled `fallback_entry_policy` field. Used by
    `activation.py` in `claim_next_work_item()` before resolving concrete
    entries through compiled graph/work-item-family data.
  - `learning_target_stage_routing(policy)` returns the
    `learning_target_stage_kind_id` from compiled scheduler-policy. Used by
    `activation.py` in `_activation_for_claim()` before delegating to
    `learning_stage_activation_for_graph()`. When `target_stage` is `None`,
    the existing safety check skips directly to compiled graph activation.
  - `recovery_fallback_selection(policy)` returns the
    `recovery_fallback_node_id` from compiled scheduler-policy. Used by
    `repair_routes.py` after runtime-effect-specific failure policy routes
    are exhausted and before falling through to
    `graph.runtime_failure_recovery`. Matched runtime-effect-specific
    recovery policies remain higher-priority narrow overrides.
  - `backpressure_outcome(policy, *, has_open_closure_target)` returns
    `"block"`, `"defer"`, or `"allow"` from the compiled
    `backpressure_policy` field. Used by `activation.py` in
    `activate_claim_for_plane()` through `closure_boundary.py`; QueueStore
    remains the filesystem mutation adapter.
- Compile validation (`compilation/validation/scheduler_policies.py`) rejects
  policies with unknown planes, invalid claim policy references, invalid
  family references, rules that reference unknown predicates or target planes,
  rules whose `order_override` escapes `plane_order`, or multi-active lane
  settings outside the supported state model.
- `lanes.py` and `lane_conflicts.py` read lane membership and lane conflict
  policies from the compiled scheduler policy instead of maintaining
  hard-coded plane-lane tables.

## Authority Traces

### Standard Execution Task

**Intake source:** `TaskDocument` markdown in
`millrace-agents/tasks/queue/`. It may come from `millrace queue add-task`, a
Planner/Manager handoff, a Blueprint approval, Recon handoff, or another
runtime-owned generated-task path.

**Queue selection owner:** `runtime/activation.py` asks
`QueueStore.claim_next_execution_task()`, which delegates to
`workspace/queue_selection.py`. The foreground claim order (planning before
execution normally, execution before planning for an open closure target) and
learning eligibility come from the compiled scheduler-policy interpreter
(`runtime/scheduler_policy.py`). When a closure target is open, activation
restricts execution claims to the open root lineage before unrelated work.

**Compiled plan authority:** `compiled_plan.json` selects the Execution graph
(`execution.standard` or `execution.with_integrator`), node bindings, legal
terminal results, runtime failure recovery node, capability grants, scheduler
lane policy, and transition table.

**Runner request builder:** `runtime/stage_requests.py` builds
`request_kind = active_work_item` requests for Builder and later Execution
stages, attaches the active task path, compiled node identity, context bundle,
skill evidence, runner/model/thinking bindings, and capability-gate data.

**Stage artifact owner:** the stage and selected runner adapter write only
run-scoped artifacts under `millrace-agents/runs/<run_id>/`, such as
`runner_prompt.*.md`, runner invocation/completion files, stdout/stderr, and
stage-authored reports. The stage must not move task queue files directly.

**Result normalization owner:** `runners/normalization/` extracts the legal
terminal marker and metadata into `StageResultEnvelope`; runtime stage-result
persistence writes the normalized JSON and status marker.

**Compiled-plan router path:** `runtime/graph_authority/routing.py`'s
`route_stage_result_from_graph` validates the normalized result identity
(run id, work item id, stage, node id, stage kind id, work item family id),
then delegates to `generic_router.py`'s
`route_generic_stage_result_from_graph`, which resolves the compiled graph
plan (threshold policies, transitions, terminal actions) and returns a
`RouterDecision` with terminal-action/lifecycle/runtime-operation metadata.
Fallback route reasons and failure classes derive from compiled ``node_id``
rather than runtime stage-name strings.

**Runtime mutation owner:** `runtime/result_application.py` applies the
compiled router decision through execution-specific helpers. Runtime-owned
mutation may move the task to the next active stage, `done`, or `blocked`,
route to Fixer/Troubleshooter/Consultant/Updater, enqueue a Planning incident
for `NEEDS_PLANNING`, update counters, update the run trace, clear or advance
active runs, emit events, and apply post-stage usage governance.

**Inspection/monitor visibility:** `millrace status`, `millrace queue ls/show`,
`millrace runs ls/show/trace`, `run_trace.json`, stage-result JSON, runtime
events, and the basic monitor show the active task, compiled identity, route,
terminal metadata provenance, terminal result, artifacts, counters, blocker
details, and runtime-operation/incident markers.

**Root-source contract:** not every task is a root source. For closure-scoped
tasks, task metadata must preserve the root spec/root source lineage inherited
from the Planning source so Arbiter readiness scans can recover after restart.

### Planning Spec Or Probe Intake

**Intake source:** `SpecDocument` markdown in
`millrace-agents/specs/queue/` or `ProbeDocument` markdown in
`millrace-agents/probes/queue/`. Sources include `millrace queue add-spec`,
`millrace queue add-probe`, watcher-normalized ideas, mailbox-applied intake,
runtime-generated child specs, and Recon-generated specs.

**Queue selection owner:** `runtime/activation.py` calls
`workspace/queue_selection.claim_next_for_plane()` with the compiled Planning
queue claim policy and compiled work-family data. `workspace/queue_selection.py`
claims the next eligible planning family via the declared family adapter or
generic `QueueFamilyInterpreter` path. The foreground claim order and
closure-target inversion come from the compiled scheduler-policy interpreter.
In shipped policy, incidents and Blueprint drafts can take precedence over
probes/specs; while a closure target is open, claims are restricted to
same-lineage Planning work.

**Compiled plan authority:** `compiled_plan.json` selects
`planning.standard` or `planning.blueprint`, maps probe work to Recon, maps
spec work to Planner, validates stage/work-item ownership, carries the
Planning transition table, runtime failure recovery node, queue claim policy,
runtime-effect rules with operation/runner authority plus optional
legacy-handler aliases, and completion behavior.

**Runner request builder:** `runtime/stage_requests.py` builds
`request_kind = active_work_item` requests with the active probe/spec path.
`runtime/context/` renders probe/spec context, visible refs, root lineage, and
preferred output refs for strict handoff artifacts.

**Stage artifact owner:** Recon, Planner, Manager, Auditor, and repair stages
write run-scoped artifacts such as `recon_packet.md`,
`planner_disposition.json`, reports, or generated task/spec drafts. These are
handoff artifacts for runtime consumption, not direct queue mutations.

**Result normalization owner:** `runners/normalization/` normalizes the
stage terminal result. `runtime/stage_result_persistence.py` persists the
stage result and Planning status marker.

**Runtime mutation owner:** `runtime/recon_transitions.py` validates Recon
handoff artifacts and persists canonical Recon packets before enqueuing a
generated task/spec, completing a no-op probe, or blocking the probe.
`runtime/planner_effects.py`, `runtime/effect_execution.py`,
`runtime/lifecycle_interpreter.py`, `runtime/work_item_transitions.py`, and
Blueprint operation runners under `runtime/effects/operation_runners/` apply
compiled Planning mutations. For runtime-effect results, `effect_execution.py`
applies effect-rule-declared source completion/blocking lifecycle intent
before queue mutation, while repair-route exceptions stay failure-policy-
owned.
`runtime/closure_boundary.py` opens a closure target when a root spec is
claimed, snapshots canonical contracts through its internal
`completion_behavior.py` implementation, and derives closure-blocking lineage
through family adapter-backed lineage scans plus inventory-backed blocker refs.

**Inspection/monitor visibility:** status shows Planning active stage, queue
depths, root-source failures, closure-target state, and deferred roots.
`runs show/trace`, Recon packets, runtime events, diagnostics, queue commands,
doctor, and the basic monitor expose the Planning route and handoff state.

**Root-source contract:** root specs must carry recoverable root lineage.
Closure target creation resolves the root source, writes canonical contracts
under `millrace-agents/arbiter/contracts/root-sources/<kind>/<id>.md` and
`root-specs/<root_spec_id>.md`, then records those paths in the target. Probe
roots are recoverable from durable probe lifecycle files or workspace-relative
references; idea roots prefer `millrace-agents/intake/ideas/<id>.md` before
legacy inbox files.

### Learning Concurrent With Foreground Work

**Intake source:** `LearningRequestDocument` markdown in
`millrace-agents/learning/requests/queue/`. Requests can be created by
operator skill commands or by `runtime/learning_triggers.py` after a matching
stage result, including the shipped Planner-to-Librarian trigger.

**Queue selection owner:** in learning-enabled modes, `runtime/supervisor.py`
and `runtime/tick_cycle.py` dispatch the Learning lane through
`claim_next_work_item_for_plane(Plane.LEARNING)` after the shared compiled
scheduler-policy interpreter (`scheduler_policy.py`) allows a separate
learning claim. When `learning_dispatch` is `"deferred"`, learning is never
claimed through foreground channels.
`workspace/queue_selection.py` claims the oldest queued learning request when
no learning request is already active.

**Compiled plan authority:** `compiled_plan.json` must include
`learning.standard`, the Learning graph, scheduler/concurrency policy, lane
conflict policy, compiled learning trigger rules, target stage, target skill
or preferred output paths, legal terminals, and runner bindings. Compile
validation rejects direct Curator triggers without a concrete destination.

**Runner request builder:** `runtime/stage_requests.py` builds
`request_kind = learning_request` requests with the active learning-request
path and Learning node identity. Request context includes the originating run,
trigger metadata, artifact paths, and optional skill destination metadata.

**Stage artifact owner:** Analyst, Professor, Curator, and Librarian write
run-scoped research, skill-update, install, or no-op artifacts. Librarian may
use the supported remote optional-skill index and install workspace-local
optional skills through the learning workflow, but the runtime still owns the
learning request lifecycle.

**Result normalization owner:** `runners/normalization/` normalizes Learning
terminal results and preserves request-kind, source refs, artifact paths, and
compiled identity in the stage-result envelope.

**Runtime mutation owner:** `runtime/result_application.py` moves learning
requests to `done` or `blocked` and records no-op completions as done.
`runtime/learning_promotions.py` records Curator promotion candidates and
defers applying them until foreground Planning/Execution lanes drain when
needed. The supervisor serializes these mutations with all other result
application.

**Inspection/monitor visibility:** status shows `learning_status_marker`,
Learning queue depth, active Learning lane and active-run count. `runs
ls/show/trace`, runtime events, promotion records, queue commands, and the
basic monitor show trigger sources, terminal results, artifacts, and deferred
promotion state.

**Root-source contract:** Learning requests are not Arbiter closure roots.
Recoverability comes from the durable learning-request document plus
`source_refs`, `originating_run_ids`, and artifact paths pointing back to the
stage result and run artifacts that triggered the request.

### Blueprint Draft Flow

**Intake source:** a Planner-completed spec or Auditor-completed incident in a
Blueprint mode reaches `manager_blueprint`, which emits a manifest and ordered
draft artifacts. The runtime persists queued Blueprint drafts under
`millrace-agents/blueprints/drafts/queue/`.

**Queue selection owner:** `runtime/activation.py` uses the compiled Planning
queue claim policy. `workspace/queue_selection.py` delegates Blueprint draft
claims to `workspace/blueprint_state.py`, which moves one eligible draft to
`drafts/active/`. While a closure target is open, only same-lineage drafts
remain claimable.

**Compiled plan authority:** `compiled_plan.json` selects
`planning.blueprint`, maps Manager/Contractor/Evaluator/Mechanic Blueprint
nodes, validates Blueprint work-item families and artifact contracts, selects
runtime-effect operations plus legacy handlers for Blueprint terminal results,
and freezes repair policies.

**Runner request builder:** `runtime/stage_requests.py` builds active
work-item requests for Blueprint stages. `runtime/context/` resolves the
manifest by `draft.manifest_id`, attaches relevant draft/packet/evaluation
state, and gives preferred refs for Blueprint repair artifacts when needed.

**Stage artifact owner:** Manager Blueprint emits manifest/draft artifacts;
Contractor Blueprint emits candidate packets; Evaluator Blueprint emits
evaluation, critique, approved packet, and generated task artifacts; Mechanic
Blueprint emits structured repair decisions and repaired task artifacts. These
are stage artifacts only.

**Result normalization owner:** `runners/normalization/` normalizes
Blueprint terminal results and metadata. Stage-result persistence records the
normalized result before runtime effect application.

**Runtime mutation owner:** `runtime/effects/operation_runners/` applies
compiled Blueprint runtime operations: persist manifests/drafts, queue drafts,
persist candidate packets, route rejected drafts back to Contractor, approve
drafts, write promotion records, enqueue generated execution tasks, apply safe
Mechanic repair actions, and block precise replay/partial-mutation failures.
`runtime/effect_execution.py` selects those operations by compiled operation id
and runner id; legacy handler ids are compatibility aliases, not dispatch
authority.
`runtime/effects/operation_runners/` contains the Python executors for
operations that still need file mutation code, and `workspace/blueprint_state.py`
owns durable Blueprint file layout helpers.

**Inspection/monitor visibility:** status exposes Blueprint counters and latest
runtime-effect metadata; `runs show/trace` exposes runtime-effect operation, runner,
legacy handler, failure class, source lifecycle, created paths, and compiled
identity; doctor checks closure/Blueprint health; raw Blueprint state remains inspectable under
`millrace-agents/blueprints/`.

**Root-source contract:** Blueprint lineage uses the source spec or incident
root source. `root_spec_id` and `root_source` are inventory and closure
metadata, not Blueprint storage ids. Manifests are keyed by `manifest_id`, and
same-root remediation manifests are valid when their manifest ids differ.

### Runtime-Generated Incident Or Handoff Work

**Intake source:** runtime result application creates incident work when a
foreground route requires Planning intervention, such as Consultant
`NEEDS_PLANNING`, an Arbiter `REMEDIATION_NEEDED` result, or an Arbiter gap
handoff. The generated incident is written to
`millrace-agents/incidents/incoming/`.

**Queue selection owner:** `runtime/activation.py` and the compiled Planning
claim policy select incoming incidents as Planning work. The QueueStore moves
the incident to `incidents/active/` and activation maps it to the Planning
entry node, normally Auditor.

**Compiled plan authority:** `compiled_plan.json` supplies the Planning graph,
incident work-item family, claim order, Auditor/Planner/Manager transitions,
closure behavior, and same-lineage claim restriction when an open target
exists.

**Runner request builder:** `runtime/stage_requests.py` builds an active
work-item request with `active_work_item_path` pointing at the active incident.
Request context includes inherited source lineage, run/report evidence, and
any closure target metadata needed by the Planning stage.

**Stage artifact owner:** the Planning stage writes analysis, plan, or handoff
artifacts in its run directory. It may propose work but does not directly move
the incident or enqueue canonical queue work outside declared artifacts.

**Result normalization owner:** `runners/normalization/` normalizes the
incident-stage terminal result. Stage-result persistence and run-trace writers
record the concrete route and artifacts.

**Runtime mutation owner:** `runtime/handoff_incidents.py` materializes
handoff incidents with inherited lineage. `runtime/closure_transitions.py`
handles Arbiter remediation incidents. Planning result application then moves
the incident through active/resolved/blocked lifecycle and enqueues any
validated generated follow-up work through runtime-owned effects.

**Inspection/monitor visibility:** incidents appear in `queue ls/show`, status
Planning queues, runtime events, monitor events, `runs show/trace`, and
closure-target latest verdict/report paths when Arbiter caused the incident.

**Root-source contract:** runtime-created handoff incidents inherit
`Root-Idea-ID`, `Root-Spec-ID`, `Source-Spec-ID`, and generic root-source
metadata from the source work item or closure target. That inheritance keeps
the incident visible to strict same-lineage selection after restart.

### Self-Contained Intake Artifact Flow

**Intake source:** an operator or mailbox command imports a typed task, probe,
spec, or idea with enough embedded context or workspace/repo-relative
references to be reproducible from the active workspace. JSON imports are
accepted where documented, but canonical queue artifacts are markdown.

**Queue selection owner:** direct offline commands use control mutations and
QueueStore helpers immediately. In daemon-owned workspaces,
`runtime/control_mailbox.py` writes mailbox envelopes and
`runtime/mailbox_intake.py` applies them at the start of a safe tick before
claiming work.

**Compiled plan authority:** compiled workflow primitives decide whether the
resulting family is claimable, which plane owns it, which stage may receive it,
and which lifecycle/effect rules apply after stage completion.

**Runner request builder:** once claimed, `runtime/stage_requests.py` builds a
normal active-work-item request for the owning plane. It does not depend on the
operator's original absolute import path.

**Stage artifact owner:** stages write run-scoped artifacts and consume only
request-provided paths plus workspace-relative or public URL references present
in the typed intake document.

**Result normalization owner:** `runners/normalization/` normalizes the
terminal result. The source artifact's typed document adapter remains the
parse/serialization authority for queue state.

**Runtime mutation owner:** `workspace/work_documents.py`,
`workspace/work_item_adapters.py`, `workspace/queue_lifecycle.py`,
`workspace/queue_store.py`, `runtime/effect_execution.py`, and
`runtime/result_application.py` own canonical queue movement and lifecycle
updates. Mailbox application archives processed/failed envelopes and emits
events.

**Inspection/monitor visibility:** queue commands, status, mailbox archives,
runtime events, doctor, and run inspection show whether the artifact was
accepted, claimed, active, done/resolved, blocked, cancelled, or invalid.

**Root-source contract:** self-contained intake must not rely on arbitrary
local absolute paths outside the workspace. If the artifact becomes a root
spec, closure creation must be able to resolve its `root_source` from durable
intake storage, lifecycle folders, or workspace-relative references and copy it
into Arbiter contracts.

### Non-Idea Closure Target Roots

**Intake source:** a root spec whose original source is not an idea. Supported
root-source kinds are compiled through completion behavior and currently cover
`probe`, `manual`, `spec`, and `incident` in addition to `idea`.

**Queue selection owner:** `runtime/activation.py` opens or backfills a
closure target when a root spec is claimed or when backlog-drain recovery finds
an eligible latest root spec. Queue selection then suppresses unrelated root
specs while the open target exists and claims only same-lineage work.

**Compiled plan authority:** the selected Planning graph's compiled
`completion_behavior` owns the compiled backlog-drain trigger, accepted
root-source kinds, `runtime_inventory` resolution policy, Arbiter activation
entry, and blocked-work policy; active runtime callers reach that behavior
through `runtime/closure_boundary.py`.

**Runner request builder:** `runtime/stage_requests.py` builds Arbiter
`request_kind = closure_target` requests, not fake active queue-item requests.
The request carries the closure target path, root spec id, root source
kind/id/path, canonical root spec path, preferred rubric/verdict/report paths,
and legacy idea fields only when the root is idea-shaped.

**Stage artifact owner:** Arbiter writes rubric, verdict, and report artifacts
through request-provided preferred paths or run-scoped report paths. It does
not close the target, enqueue incidents, or move root work directly.

**Result normalization owner:** `runners/normalization/` normalizes Arbiter
results onto `work_item_kind = spec` and `work_item_id = <root_spec_id>` while
preserving closure root-source metadata.

**Runtime mutation owner:** `runtime/closure_boundary.py` resolves and
snapshots contracts when opening the target through its internal
`completion_behavior.py` implementation. `workspace/arbiter_state.py`
writes root-source/root-spec contracts and target state.
`runtime/closure_transitions.py` closes targets, keeps them open for
remediation, persists latest verdict/report paths, enqueues remediation
incidents, blocks closure, and records lineage-drift diagnostics.

**Inspection/monitor visibility:** `millrace status`, `doctor`, `runs
show/trace`, Arbiter target JSON, canonical contracts, runtime events, and the
basic monitor expose root source kind/id/path, deferred roots, closure
blockers, lineage drift, latest verdict/report, and target open/closed state.

**Root-source contract:** non-idea roots use
`millrace-agents/arbiter/contracts/root-sources/<kind>/<source_id>.md` as the
authoritative canonical source. Manual/spec roots may use the root spec
markdown itself when appropriate; probe/spec/incident roots are resolved from
durable intake copies, lifecycle directories, or workspace-relative references.
The legacy `contracts/ideas/` mirror is not authoritative for non-idea roots.

### Operator Or Mailbox Intervention

**Intake source:** operator commands such as pause/resume/stop/reload,
retry-active, queue cancel/supersede/retarget/retry-blocked, incident
resolve/cancel/archive-invalid, and approval approve/deny. If a daemon owns
the workspace, commands become mailbox envelopes under the runtime mailbox;
otherwise direct offline control mutations apply immediately.

**Queue selection owner:** interventions are applied before normal work claim.
`workspace/mailbox.py` claims mailbox commands deterministically, and
`runtime/mailbox_intake.py` drains them at the beginning of a tick. Operator
interventions that would affect active runtime-owned state are deferred until
active stage workers drain.

**Compiled plan authority:** most control commands are runtime-control
surfaces rather than graph transitions. The compiled plan still constrains
family-aware retry/cancel support, work-item ownership, pending launch-plan
replacement on reload, approval-gated capability grants, and safe routing after
the intervention.

**Runner request builder:** no runner request is built for the intervention
itself. If the intervention requeues, retries, reloads, approves, or unpauses
work, later requests are built normally by `runtime/stage_requests.py` from
the then-current active launch plan.

**Stage artifact owner:** no stage owns intervention artifacts. The durable
evidence is the mailbox envelope/archive, intervention audit ledger,
approval record, runtime event, diagnostic report, or queue archive generated
by runtime/workspace helpers.

**Result normalization owner:** no `RunnerRawResult` normalization occurs for
the intervention. The typed control/mailbox contracts validate command
payloads, and direct mutation helpers return control results for CLI output.

**Runtime mutation owner:** `runtime/control.py` coordinates routing versus
direct mutation. `runtime/control_mailbox.py` writes daemon-safe envelopes.
`runtime/control_mutations.py`, `runtime/mailbox_intake.py`,
`workspace/operator_interventions.py`, `runtime/pause_state.py`,
`runtime/approvals.py`, `runtime/recovery/queue_mutation.py`, and
`workspace/lineage_integrity.py` own the concrete safe mutations.

**Inspection/monitor visibility:** CLI command output, mailbox processed/failed
archives, `interventions.jsonl`, approval records, runtime events, status,
queue counts, doctor, diagnostics, and monitor events show whether the command
applied, deferred, failed, or left work blocked for review.

**Root-source contract:** interventions must preserve recoverability. They
archive or audit instead of deleting, refuse unsafe active mutation, preserve
open closure targets during stale-state clearing, and repair lineage only
through explicit audited paths that keep root-source/root-spec contracts
consistent.

## Ambiguities To Keep Visible

- "Queue selection owner" is split deliberately: compiled scheduler policy
  owns the foreground order and closure priority, while runtime/workspace
  helpers own the atomic file movement.
- "Compiled plan authority" does not mean stages can infer authority from
  prompts. The runtime reads the persisted plan and validates stage/work-item
  ownership before runner invocation.
- "Runtime-operation id" resolves through the compiled runtime operation
  registry; dispatch reads `RouterDecision.runtime_operation_id` from the
  compiled terminal action and maps it through an operation-id-keyed route
  table. The old fixed whitelist is no longer active authority.
- "Root source" identifies recoverable closure evidence; it is not a storage
  key for Blueprint manifests or a license to search arbitrary local files.
- Runtime-effect operation dispatch splits into interpreted and legacy
  handler-backed paths based on compiled runner identity. The interpreted
  path walks compiled steps through a primitive executor registry; the
  legacy path calls registered Python handler functions. All shipped
  operations use the legacy path; the interpreted path is fixture-only.
- Primitive definitions in `runtime_effect_primitives/` assets carry a
  `non_interpreted_compatibility` marker. Primitives marked `true` have no
  interpreted executor and are rejected at compile time when referenced by
  interpreted-runner operations. Primitives marked `false` must have a
  registered executor and a matching compiled definition.
- Operation runner ownership flows from `RuntimeEffectOperationRunnerDefinition.
  operation_ids`, not from an operation-local `runner_id` field. The runtime
  resolves runners by scanning the compiled runner map for the runner whose
  `operation_ids` includes the target operation.
