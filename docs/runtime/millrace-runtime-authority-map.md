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
- `runtime/learning_triggers.py`, `runtime/handoff_incidents.py`, and
  `runtime/closure_transitions.py` own runtime-generated follow-up work.
- `runtime/activation.py` decides which work can be claimed for a plane.
- `workspace/queue_store.py` and `workspace/queue_selection.py` move queue
  artifacts into active state. Planning queue ordering is driven by the
  compiled per-plane queue claim policy when one is present.

### Request, runner, and normalization authority

- `runtime/stage_requests.py` builds `StageRunRequest` from the active run,
  active work item or closure target, and compiled node plan.
- `runtime/request_context.py` writes deterministic request-context artifacts
  and prompt context.
- `runtime/capability_gates.py` and `runtime/approvals.py` evaluate compiled
  execution-capability grants before runner invocation.
- `runners/dispatcher.py` resolves the selected runner adapter.
- runner adapters write runner-owned invocation/completion/stdout/stderr/event
  artifacts under the run directory and return `RunnerRawResult`.
- `runners/normalization.py` converts raw runner output into
  `StageResultEnvelope`.

### Mutation and inspection authority

- `runtime/result_application.py` is the post-stage routing facade.
- `runtime/effect_execution.py`, `runtime/effects/`,
  `runtime/lifecycle_interpreter.py`, and `workspace/queue_lifecycle.py` apply
  compiled operation-id-first runtime-effect and source-lifecycle intents.
- `runtime/work_item_transitions.py`, `runtime/recon_transitions.py`,
  `runtime/effects/operations.py`, `runtime/closure_transitions.py`,
  `runtime/result_counters.py`, `runtime/stage_result_persistence.py`,
  `runtime/run_traces.py`, `runtime/snapshot_state.py`, and
  `workspace/*_state.py` own the durable mutations for their domains.
- `runtime/supervisor.py` serializes result application in daemon mode even
  when workers run concurrently.
- `runtime/inspection.py`, CLI status/runs/queue views, `doctor.py`, monitor
  events, `runtime_snapshot.json`, status markdown files, `run_trace.json`,
  runtime events, and run artifacts expose read-only inspection surfaces.

## Authority Traces

### Standard Execution Task

**Intake source:** `TaskDocument` markdown in
`millrace-agents/tasks/queue/`. It may come from `millrace queue add-task`, a
Planner/Manager handoff, a Blueprint approval, Recon handoff, or another
runtime-owned generated-task path.

**Queue selection owner:** `runtime/activation.py` asks
`QueueStore.claim_next_execution_task()`, which delegates to
`workspace/queue_selection.py`. When a closure target is open, activation
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

**Result normalization owner:** `runners/normalization.py` extracts the legal
terminal marker and metadata into `StageResultEnvelope`; runtime stage-result
persistence writes the normalized JSON and status marker.

**Runtime mutation owner:** `runtime/result_application.py` applies the
compiled router decision through execution-specific helpers. Runtime-owned
mutation may move the task to the next active stage, `done`, or `blocked`,
route to Fixer/Troubleshooter/Consultant/Updater, enqueue a Planning incident
for `NEEDS_PLANNING`, update counters, update the run trace, clear or advance
active runs, emit events, and apply post-stage usage governance.

**Inspection/monitor visibility:** `millrace status`, `millrace queue ls/show`,
`millrace runs ls/show/trace`, `run_trace.json`, stage-result JSON, runtime
events, and the basic monitor show the active task, compiled identity, route,
terminal result, artifacts, counters, and blocker details.

**Root-source contract:** not every task is a root source. For closure-scoped
tasks, task metadata must preserve the root spec/root source lineage inherited
from the Planning source so Arbiter readiness scans can recover after restart.

### Planning Spec Or Probe Intake

**Intake source:** `SpecDocument` markdown in
`millrace-agents/specs/queue/` or `ProbeDocument` markdown in
`millrace-agents/probes/queue/`. Sources include `millrace queue add-spec`,
`millrace queue add-probe`, watcher-normalized ideas, mailbox-applied intake,
runtime-generated child specs, and Recon-generated specs.

**Queue selection owner:** `runtime/activation.py` asks
`QueueStore.claim_next_planning_item()` with the compiled Planning queue claim
policy. `workspace/queue_selection.py` claims the next eligible planning
family. In shipped policy, incidents and Blueprint drafts can take precedence
over probes/specs; while a closure target is open, claims are restricted to
same-lineage Planning work.

**Compiled plan authority:** `compiled_plan.json` selects
`planning.standard` or `planning.blueprint`, maps probe work to Recon, maps
spec work to Planner, validates stage/work-item ownership, carries the
Planning transition table, runtime failure recovery node, queue claim policy,
runtime-effect rules with operation/runner authority plus optional
legacy-handler aliases, and completion behavior.

**Runner request builder:** `runtime/stage_requests.py` builds
`request_kind = active_work_item` requests with the active probe/spec path.
`runtime/request_context.py` renders probe/spec context, visible refs, root
lineage, and preferred output refs for strict handoff artifacts.

**Stage artifact owner:** Recon, Planner, Manager, Auditor, and repair stages
write run-scoped artifacts such as `recon_packet.md`,
`planner_disposition.json`, reports, or generated task/spec drafts. These are
handoff artifacts for runtime consumption, not direct queue mutations.

**Result normalization owner:** `runners/normalization.py` normalizes the
stage terminal result. `runtime/stage_result_persistence.py` persists the
stage result and Planning status marker.

**Runtime mutation owner:** `runtime/recon_transitions.py` validates Recon
handoff artifacts and persists canonical Recon packets before enqueuing a
generated task/spec, completing a no-op probe, or blocking the probe.
`runtime/planner_effects.py`, `runtime/effect_execution.py`,
`runtime/lifecycle_interpreter.py`, `runtime/work_item_transitions.py`, and
Blueprint-specific effect handlers apply compiled Planning mutations.
`runtime/completion_behavior.py` opens a closure target when a root spec is
claimed and snapshots canonical contracts.

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
`claim_next_work_item_for_plane(Plane.LEARNING)`. `workspace/queue_selection.py`
claims the oldest queued learning request when no learning request is already
active.

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

**Result normalization owner:** `runners/normalization.py` normalizes Learning
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
work-item requests for Blueprint stages. `runtime/request_context.py` resolves
the manifest by `draft.manifest_id`, attaches relevant draft/packet/evaluation
state, and gives preferred refs for Blueprint repair artifacts when needed.

**Stage artifact owner:** Manager Blueprint emits manifest/draft artifacts;
Contractor Blueprint emits candidate packets; Evaluator Blueprint emits
evaluation, critique, approved packet, and generated task artifacts; Mechanic
Blueprint emits structured repair decisions and repaired task artifacts. These
are stage artifacts only.

**Result normalization owner:** `runners/normalization.py` normalizes
Blueprint terminal results and metadata. Stage-result persistence records the
normalized result before runtime effect application.

**Runtime mutation owner:** `runtime/effects/operations.py` applies compiled
Blueprint runtime operations: persist manifests/drafts, queue drafts, persist
candidate packets, route rejected drafts back to Contractor, approve drafts,
write promotion records, enqueue generated execution tasks, apply safe
Mechanic repair actions, and block precise replay/partial-mutation failures.
`runtime/effect_execution.py` selects those operations by compiled operation id
and runner id; legacy handler ids are compatibility aliases, not dispatch
authority.
`runtime/blueprint_effects.py` is a legacy import facade, and
`workspace/blueprint_state.py` owns durable Blueprint file layout helpers.

**Inspection/monitor visibility:** status exposes Blueprint counters and latest
repair context; `runs show/trace` exposes runtime-effect operation, runner,
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

**Result normalization owner:** `runners/normalization.py` normalizes the
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

**Result normalization owner:** `runners/normalization.py` normalizes the
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
`completion_behavior` owns the backlog-drain trigger, accepted root-source
kinds, `runtime_inventory` resolution policy, Arbiter activation entry, and
blocked-work policy.

**Runner request builder:** `runtime/stage_requests.py` builds Arbiter
`request_kind = closure_target` requests, not fake active queue-item requests.
The request carries the closure target path, root spec id, root source
kind/id/path, canonical root spec path, preferred rubric/verdict/report paths,
and legacy idea fields only when the root is idea-shaped.

**Stage artifact owner:** Arbiter writes rubric, verdict, and report artifacts
through request-provided preferred paths or run-scoped report paths. It does
not close the target, enqueue incidents, or move root work directly.

**Result normalization owner:** `runners/normalization.py` normalizes Arbiter
results onto `work_item_kind = spec` and `work_item_id = <root_spec_id>` while
preserving closure root-source metadata.

**Runtime mutation owner:** `runtime/completion_behavior.py` resolves and
snapshots contracts when opening the target. `workspace/arbiter_state.py`
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
`runtime/approvals.py`, `runtime/blocked_recovery.py`, and
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

- "Queue selection owner" is split deliberately: compiled workflow primitives
  own the policy, while runtime/workspace helpers own the atomic file movement.
- "Compiled plan authority" does not mean stages can infer authority from
  prompts. The runtime reads the persisted plan and validates stage/work-item
  ownership before runner invocation.
- "Root source" identifies recoverable closure evidence; it is not a storage
  key for Blueprint manifests or a license to search arbitrary local files.
