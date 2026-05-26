# Millrace Runtime Architecture

## Scope

Millrace is a filesystem-backed runtime implemented under `src/millrace_ai/` and imported as `millrace_ai`.
Each workspace is explicitly initialized under `<workspace>/millrace-agents/` by `millrace init` and owns its own state, queues, lock file, and logs.

Use `docs/runtime/millrace-compiler-and-frozen-plans.md` for compile semantics
and persisted compiled-plan behavior. Use `docs/runtime/millrace-modes-and-loops.md`
for the shipped mode and loop topology the compiler resolves. Use
`docs/runtime/millrace-runtime-authority-map.md` for trace-by-trace ownership
of intake, queue selection, runner requests, artifacts, result normalization,
and durable runtime mutation.

## Source Tree

- importable package code lives under `src/millrace_ai/`
- runtime-facing domains are split across `assets/`, `cli/`, `config/`, `runners/`, `runtime/`, and `workspace/`
- tests mirror those ownership boundaries under `tests/assets/`, `tests/cli/`, `tests/config/`, `tests/runners/`, `tests/runtime/`, `tests/workspace/`, and `tests/integration/`
- cross-cutting source guardrails live in `tests/test_import_cycles.py` and
  `tests/test_source_hygiene.py` so import cycles and ownership-boundary drift
  fail in the normal pytest suite
- `docs/source-package-map.md` records the old-to-new module mapping and the root facades intentionally preserved for compatibility

## Workspace Ownership Model

- Workspace root is operator-owned.
- Runtime-managed content lives under `<workspace>/millrace-agents/`.
- Exactly one daemon may own one workspace at a time via `state/runtime_daemon.lock.json`.
- A second daemon in the same workspace fails fast.
- Different workspaces can run independent daemons concurrently.
- `millrace status watch --workspace <PATH> [--workspace <PATH> ...]` is read-only monitoring and does not acquire ownership locks.
- `state/execution_status.md`, `state/planning_status.md`, and `state/learning_status.md` reflect the active running stage on their plane while work is executing, then the latest terminal marker or `### IDLE` when no stage is active.

## Canonical Artifact Model

### Markdown work documents (canonical queue artifacts)

- `millrace-agents/tasks/{queue,active,done,blocked}/*.md`
- `millrace-agents/probes/{queue,active,done,blocked}/*.md`
- `millrace-agents/specs/{queue,active,done,blocked}/*.md`
- `millrace-agents/incidents/{incoming,active,resolved,blocked}/*.md`
- `millrace-agents/learning/requests/{queue,active,done,blocked}/*.md`

Canonical task/probe/spec/incident/learning-request documents use headed markdown:

- leading H1 title
- scalar headings such as `Task-ID: ...` or `Spec-ID: ...`
- list sections such as `Acceptance:` followed by `- ...` items

Incident documents also accept `Status-Hint` values of `incoming`, `active`,
`blocked`, or `resolved`. Task and probe documents use their own queue-state
hints: `queued`, `active`, `blocked`, and `done`.

JSON imports are still accepted for queue intake, but canonical on-disk queue artifacts are markdown.

Operator intervention archives live below existing lifecycle directories, so
claimable queue globbing remains unchanged. Examples include
`tasks/queue/cancelled/`, `tasks/blocked/superseded/`,
`incidents/active/cancelled/`, `incidents/resolved/operator/`, and
`incidents/incoming/invalid-archived/`. Each archive directory can carry an
`interventions.jsonl` audit ledger, and applied interventions also append
runtime events.

### JSON runtime/state artifacts

- `millrace-agents/state/runtime_snapshot.json`
- `millrace-agents/state/recovery_counters.json`
- `millrace-agents/state/compiled_plan.json`
- `millrace-agents/state/compile_diagnostics.json`
- `millrace-agents/state/baseline_manifest.json`
- `millrace-agents/state/workspace_schema_epoch.json`
- `millrace-agents/state/execution_status.md`
- `millrace-agents/state/planning_status.md`
- `millrace-agents/state/learning_status.md`
- `millrace-agents/state/usage_governance_state.json`
- `millrace-agents/state/usage_governance_ledger.jsonl`
- `millrace-agents/diagnostics/blocked/*.json`
- `millrace-agents/diagnostics/auto-recovery/*.json`
- `millrace-agents/approvals/pending/*.json`
- `millrace-agents/approvals/resolved/*.json`
- mailbox envelopes/archives and run-scoped runner artifacts
- `millrace-agents/blueprints/manifests/*.json`, keyed by manifest id for new
  writes while still accepting legacy root-keyed filenames by embedded
  `manifest_id`
- `millrace-agents/blueprints/drafts/{queue,active,approved,blocked,canceled,superseded}/*.json`
- `millrace-agents/blueprints/packets/{candidates,approved,rejected,superseded}/*.json`
- `millrace-agents/blueprints/{critiques,evaluations,promotions}/**/*.json`

Storage identity belongs to the artifact or work-item id, not to lineage.
`root_spec_id` and generic `root_source` metadata are closure and inventory
metadata; legacy `root_idea_id` remains the idea-rooted compatibility field.
Multiple Blueprint manifests may share the same root lineage during Arbiter
remediation as long as their `manifest_id` values differ. A same-root manifest
is valid lineage; a duplicate manifest is the same `manifest_id` with divergent
normalized content.

Queue intake artifacts must be reproducible from the active workspace: typed
work documents may embed supporting context or reference repo-relative files,
but they should not depend on arbitrary local absolute paths outside the
workspace.

### Arbiter-owned completion artifacts

- `millrace-agents/arbiter/contracts/root-sources/<kind>/*.md`
- `millrace-agents/arbiter/contracts/ideas/*.md`
- `millrace-agents/arbiter/contracts/root-specs/*.md`
- `millrace-agents/arbiter/targets/*.json`
- `millrace-agents/arbiter/rubrics/*.md`
- `millrace-agents/arbiter/verdicts/*.json`
- `millrace-agents/arbiter/reports/*.md`

## Module Topology

- `src/millrace_ai/workspace/paths.py`: workspace path contract for the `millrace-agents` tree.
- `src/millrace_ai/workspace/bootstrap_files.py`: default bootstrap payload construction for state, status, config, and recovery-counter files.
- `src/millrace_ai/workspace/asset_deployment.py`: packaged runtime asset source resolution and deployment into initialized workspaces.
- `src/millrace_ai/workspace/initialization.py`: explicit `millrace init` workspace baseline orchestration and the `bootstrap_workspace` compatibility alias.
- `src/millrace_ai/workspace/baseline.py`: managed baseline manifests and upgrade classification.
- `src/millrace_ai/workspace/work_documents.py`: headed markdown parsing/serialization for task/probe/spec/incident/learning-request documents.
- `src/millrace_ai/workspace/queue_store.py`: queue claim/transition/requeue facade for markdown documents.
- `src/millrace_ai/workspace/work_item_adapters.py`: family-aware document
  adapters for built-in queue work documents.
- `src/millrace_ai/workspace/queue_lifecycle.py`: interpreter that applies
  compiled source lifecycle intents to active queue documents.
- `src/millrace_ai/workspace/blueprint_state.py`: durable Blueprint manifest,
  draft, packet, critique, evaluation, and promotion state helpers. Manifest
  reads resolve both new manifest-id-keyed paths and legacy root-keyed paths by
  embedded `manifest_id`; root lineage listing can return multiple manifests.
- `src/millrace_ai/workspace/operator_interventions.py`: audited queue and
  incident cancellation, supersession, dependency retargeting, operator
  resolution, and invalid-incident archive helpers.
- `src/millrace_ai/workspace/task_lifecycle_integrity.py`: duplicate task lifecycle detection and safe stale-blocked predecessor retirement when a same-root continuation reaches `done`.
- `src/millrace_ai/workspace/state_store.py`: snapshot/status/counter persistence facade.
- `src/millrace_ai/workspace/runtime_lock.py`: daemon ownership lock acquire/release/inspection.
- `src/millrace_ai/workspace/schema_epoch.py`: workspace schema epoch marker,
  daemon-owned reset refusal, archive manifest, and clean-state reset helpers.
- `src/millrace_ai/contracts/`: public typed contract facade plus owned contract families for enums, stage metadata, work documents, stage results, loop/mode definitions, compiler diagnostics, runtime snapshots, runtime error contexts, mailbox payloads, and recovery counters. `contracts/stage_metadata.py` is the single registry for stage plane membership, legal terminal results, running markers, prompt markers, and result-class policy.
- `src/millrace_ai/compiler.py`: stable public facade for mode+graph-loop compile, graph preview, currentness inspection, and diagnostics.
- `src/millrace_ai/compilation/`: compiler internals for workspace compile orchestration, mode/path resolution, graph and node materialization, transition/completion/policy compilation, learning-trigger validation, entrypoint override validation, asset resolution, fingerprints, persistence, and currentness inspection.
- `src/millrace_ai/runners/`: stage runner contracts, normalization, adapter registry/dispatcher, and Codex/Pi adapters.
- `src/millrace_ai/cli/monitoring.py`: formatting for opt-in daemon monitor output.
- `src/millrace_ai/runtime/__init__.py`: stable `RuntimeEngine`, daemon supervisor, and runtime outcome import surface.
- `src/millrace_ai/runtime/engine.py`: stable stateful façade that keeps `RuntimeEngine.startup()`, `tick()`, and `close()` as the public runtime surface.
- `src/millrace_ai/runtime/outcomes.py`: runtime tick outcome contract shared by the engine and tick/request helpers without creating an engine import cycle.
- `src/millrace_ai/runtime/lifecycle.py`: startup/shutdown flow, config/compile bootstrap, watcher rebuild, and daemon-lock lifecycle.
- `src/millrace_ai/runtime/effects.py`: runtime effect result contracts,
  source lifecycle intent creation, destination-existence checks, and
  effect-result application.
- `src/millrace_ai/runtime/effect_execution.py`: compiled runtime-effect
  dispatch, failure-policy interpretation, matched-policy metadata, and
  source-lifecycle application after runtime-owned mutation.
- `src/millrace_ai/runtime/failure_policy.py`: runtime failure-origin
  classification plus runtime-effect failure policy matching, including
  conservative Blueprint blocks and recoverable Mechanic Blueprint routes.
- `src/millrace_ai/runtime/blueprint_effects.py`: Blueprint-specific runtime
  effects for manifest/draft promotion, packet persistence, evaluator
  approval/rejection, idempotent Manager replay, Contractor candidate replay,
  Evaluator approval replay, Mechanic repaired-task application, and precise
  Blueprint failure classes.
- `src/millrace_ai/runtime/blueprint_recovery_diagnostics.py`: shared
  Blueprint runtime-effect repair diagnostics used by status and doctor to
  expose the structured repair contract, replay conflict classes,
  inert-artifact guard, and runtime ownership boundary.
- `src/millrace_ai/runtime/planner_effects.py`: Planner disposition handling
  for active-source continuation, emitted-child-spec completion/resolution, and
  blocked Planner outcomes.
- `src/millrace_ai/runtime/request_context.py`: deterministic context bundles,
  including Blueprint manifest resolution by `draft.manifest_id` and Mechanic
  Blueprint repair output refs for recoverable runtime-effect failures.
- `src/millrace_ai/runtime/lifecycle_interpreter.py`: runtime-facing bridge from
  source lifecycle intent to the workspace queue lifecycle interpreter.
- `src/millrace_ai/runtime/monitoring.py`: runtime monitor event protocol and null monitor sink.
- `src/millrace_ai/cli/monitoring.py`: basic terminal monitor renderer for the
  concise human-facing daemon stream; full ids and details stay in persisted
  runtime events and run artifacts.
- `src/millrace_ai/runtime/tick_cycle.py`: serial one-tick orchestration used by
  bounded daemon execution and compatibility tests.
- `src/millrace_ai/runtime/supervisor.py`: daemon-mode lane scheduler, lane-keyed worker registry, and serialized completion-application owner.
- `src/millrace_ai/runtime/blocked_recovery.py`: blocked-work metadata,
  blocked dependency retryability decisions, family-aware manual blocked
  retry validation, and daemon idle-cycle transient dependency auto-recovery.
- `src/millrace_ai/runtime/mailbox_intake.py`: mailbox drain, reload, and mailbox-applied intake paths.
- `src/millrace_ai/runtime/watcher_intake.py`: watcher session lifecycle and idea-file normalization.
- `src/millrace_ai/runtime/activation.py`: claim ordering and active work-item activation.
- `src/millrace_ai/runtime/pause_state.py`: pause-source mutation helpers for operator and usage-governance pauses.
- `src/millrace_ai/runtime/usage_governance/`: opt-in usage-governance authority package, with state/ledger models, durable state persistence, runtime-token window evaluation, subscription-quota telemetry, monitor event emission, and engine-facing pause-source application split behind the stable package facade.
- `src/millrace_ai/runtime/graph_authority/`: compiled-graph activation and routing authority package, with activation decisions, validation, policy lookup, counter resolution, stage mapping, and execution/planning/learning routing split behind the stable package facade.
- `src/millrace_ai/runtime/completion_behavior.py`: closure-target activation, lineage readiness checks, and compiler-driven backlog-drain dispatch.
- `src/millrace_ai/runtime/reconciliation.py`: stale/impossible-state detection and recovery-stage activation.
- `src/millrace_ai/runtime/result_application.py`: stable façade over routed post-stage mutation helpers.
- `src/millrace_ai/runtime/result_counters.py`: recovery-counter entry mutation and snapshot counter increments.
- `src/millrace_ai/runtime/work_item_transitions.py`: non-closure work-item completion, blocked transitions, and active-snapshot clearing.
- `src/millrace_ai/runtime/handoff_incidents.py`: planning-handoff and arbiter-gap incident materialization, including source work-item lineage inheritance for runtime-created handoff incidents.
- `src/millrace_ai/runtime/recon_transitions.py`: Recon packet persistence and probe-to-task/spec/no-op/blocked mutation.
- `src/millrace_ai/runtime/stage_result_persistence.py`: persisted stage-result JSON writes and plane status-marker updates.
- `src/millrace_ai/runtime/learning_triggers.py`: compiler-frozen learning-trigger evaluation and learning-request enqueueing.
- `src/millrace_ai/runtime/skill_evidence.py`: per-request skill revision evidence snapshots for learning-enabled runs.
- `src/millrace_ai/runtime/snapshot_state.py`: shared snapshot reset/update helpers.
- `src/millrace_ai/runtime/closure_transitions.py`: closure-target state mutation, arbiter report canonicalization, and arbiter-specific handoff/block/close paths.
- `src/millrace_ai/runtime/stage_requests.py`: request rendering, idle outcomes, queue-depth reads, and runtime clock/id helpers.
- `src/millrace_ai/runtime/inspection.py`: persisted run summary inspection and artifact selection helpers.
- `src/millrace_ai/run_inspection.py`: thin compatibility layer that re-exports the runtime inspection surface.
- `src/millrace_ai/control.py`: thin public facade that preserves the stable operator control import surface.
- `src/millrace_ai/runtime/control.py`: public runtime control abstraction that coordinates routing vs direct mutation ownership.
- `src/millrace_ai/runtime/control_mailbox.py`: mailbox-safe daemon routing, command envelope creation, and control enqueue failure boundaries.
- `src/millrace_ai/runtime/control_mutations.py`: direct offline workspace mutations, pause/resume source handling, requeue/reset helpers, stale-state clearing behavior, and operator intervention snapshot refreshes.
- `src/millrace_ai/watchers.py`: optional watcher session lifecycle and polling fallback intake.
- `src/millrace_ai/doctor/`: workspace integrity + lock health checks.
- `src/millrace_ai/assets/entrypoints/`: packaged entrypoint markdown assets plus the parsing/linting package that validates entrypoint and advisory skill manifests.
- `src/millrace_ai/cli/errors.py`: operator error output helper used by command modules and shared workspace resolution.
- `src/millrace_ai/cli/status_view.py`: compatibility facade for status
  output.
- `src/millrace_ai/cli/status/`: status data collection, view-model assembly,
  text rendering, and JSON payload rendering.
- `src/millrace_ai/cli/runs_view.py`: persisted run-list loading and line rendering.
- `src/millrace_ai/cli/config_view.py`: config-show state loading and line rendering.
- `src/millrace_ai/cli/compile_view.py`: compile diagnostics and compile-show line rendering.
- `src/millrace_ai/cli/formatting.py`: pure rendering helpers for already-collected run/control values.
- `src/millrace_ai/cli/`: namespaced operator surface split into package assembly, shared resolution, command-specific views, monitor formatting, and command groups.

## Stage Runner Stack

Per stage execution:

1. Runtime builds `StageRunRequest` from the compiled plan and active work item.
2. Runtime evaluates compiled execution capability grants and approval gates.
3. `StageRunnerDispatcher` resolves adapter by runner name precedence.
4. Adapter executes (`codex_cli` by default, `pi_rpc` in Pi modes) and returns `RunnerRawResult`.
5. Runtime normalizes into `StageResultEnvelope`, persists the stage result,
   upserts the run-trace node, applies the authoritative router decision, and
   records the run-trace edge.

The runtime boundary stays `StageRunRequest -> RunnerRawResult` so additional adapters can be added without changing orchestration flow.

## Tick Lifecycle

Startup:

1. Require an initialized workspace baseline under `millrace-agents/`; use `millrace init` to create it.
2. Load config and compile or reuse the current active mode plan.
3. Check the workspace schema epoch marker against the compiled plan's schema
   epoch before loading mutable runtime state.
4. Acquire daemon ownership lock.
5. Reconcile stale/impossible runtime state.

Per daemon scheduler cycle:

1. Process mailbox commands (`pause/resume/stop/retry-active/reload-config/intake`, including planning-scoped retry requests).
2. Consume watcher/poll intake events (including idea normalization to planning specs).
3. Refresh queue depths.
4. Respect stop control gates.
5. Evaluate opt-in usage governance and respect pause gates.
6. Run stale-state reconciliation and recovery routing.
7. Refresh queue depths again.
8. Claim planning, execution, or learning work item. When a closure target is
   already open, the runtime claims only same-lineage execution/planning work
   and leaves unrelated queued root specs behind the closure target.
9. If no same-lineage work remains, check closure lineage integrity before
   Arbiter activation. Same-root work with a mismatched effective root spec
   blocks as `closure_lineage_drift` instead of letting Arbiter re-enter a
   planning-only remediation loop.
10. Consult compiled `completion_behavior` and activate `arbiter` when an open closure target is eligible.
11. Re-evaluate usage governance before dispatching active stages.
12. Evaluate execution capability grants and pending approvals before runner
    invocation.
13. Dispatch eligible lanes according to the compiled scheduler policy.
    Default modes remain one active lane per plane and shipped modes keep
    Planning and Execution mutually exclusive. Experimental multi-lane policy
    requires compiler-validated lane conflict coverage before the daemon can
    dispatch more than one lane in the same plane.
14. Worker tasks execute blocking runner adapters from immutable
    `StageRunRequest` inputs and return typed outcomes only.
15. Before reporting plain no-work idle, inspect stranded queued execution
    tasks whose dependencies are blocked. If a same-lineage blocked predecessor
    is classified as a transient environment/provider failure and cooldown plus
    retry-budget gates pass, requeue it through the audited blocked work-item
    retry transition while preserving the task compatibility event.
16. The supervisor applies completed outcomes serially: normalize, persist,
    update `run_trace.json`, route, update queue/snapshot/status/counters, emit
    monitor/runtime events, and evaluate post-stage usage governance.

The implementation mirrors that ordering directly:

- `RuntimeEngine` holds state and exposes the stable methods
- `runtime/tick_cycle.py` owns the serial one-tick orchestration block used by
  bounded daemon operation
- `runtime/supervisor.py` owns daemon dispatch, lane-keyed worker tracking,
  compiled lane conflict checks, and serialized result application
- `runtime/lanes.py` owns durable lane projection and launch-plan fingerprints
  on active runs
- `runtime/result_application.py` delegates routed mutation into owned
  collaborators for counters, work-item movement, incident creation,
  persistence, closure-target handling, and next-stage running status-marker
  updates after compiled `RUN_STAGE` decisions
- `runtime/effects.py` and `workspace/queue_lifecycle.py` keep terminal
  lifecycle effects behind runtime-owned intent objects and queue interpreters

Idle:

- If no claimable work exists and no eligible completion audit exists, runtime emits `no_work` and keeps the daemon loop alive unless stop requested.
- If queued execution work is stranded behind a same-lineage blocked
  dependency, runtime distinguishes transient runner/provider blockers from
  semantic blockers. Transient classes (`network_unavailable`,
  `provider_unavailable`, `provider_rate_limited`, `runner_timeout`) may be
  requeued automatically with cooldown and retry-budget diagnostics; semantic
  blocked states, missing binaries, auth failures, malformed output, and
  unknown transport failures remain blocked for operator review.
- Manual `queue retry-blocked` uses the same blocked-work retry validator for
  task, probe, spec, incident, learning-request, and parseable graph-owned
  families. It requires an offline workspace, validates the blocked artifact
  before movement, enforces destination-collision and root-spec guards, writes
  a family audit log, refreshes snapshot queue depths, and emits a runtime
  event. Operators should cancel bad blocked work and intake a corrected item
  when replaying the original artifact would repeat a semantic failure.
- If unrelated root specs are queued while a closure target is open, runtime
  emits `closure_target_backpressure`, keeps the daemon alive, and reports
  `planning_root_specs_deferred_by_closure_target` through `millrace status`.
- If Consultant or another routed stage escalates a same-lineage work item back
  into planning while a closure target is open, the runtime-created handoff
  incident inherits `Root-Idea-ID`, `Root-Spec-ID`, and `Source-Spec-ID` from
  the source work document before it is enqueued. That keeps the incident
  visible to the strict closure-scoped planning selector.
- If a stage-authored same-ID continuation bypassed queue API uniqueness and
  later reaches `tasks/done/`, the task transition layer retires a same-root
  stale predecessor from `tasks/blocked/` into
  `tasks/blocked/superseded/`. The archived copy remains inspectable, but it no
  longer appears in closure readiness scans.
- If an operator identifies queued/blocked work or an incident as bad intake,
  the supported path is an audited intervention command: cancel it, supersede
  it with an existing replacement, retarget queued dependents, resolve/cancel
  the incident, or archive an invalid incoming incident artifact. These
  commands archive rather than delete documents. Direct commands refuse live
  active mutation; daemon-owned workspaces receive mailbox commands that apply
  only after active stage workers drain and before the next work claim.
- If queued/active/blocked work shares the open target's root idea but carries
  another effective `Root-Spec-ID`, runtime emits
  `closure_lineage_drift_detected`, writes a diagnostic under
  `millrace-agents/arbiter/diagnostics/lineage-drift/`, marks planning
  `### BLOCKED`, and leaves the strict queue selector unchanged.
- If Arbiter asks for remediation more than once without any intervening
  execution-stage completion, runtime blocks with
  `closure_repeated_remediation_without_execution` instead of opening another
  planning incident.

Usage governance notes:

- governance is default-off and applies at runtime boundaries, not compile time
- runtime token rules count persisted stage-result token usage once by
  stage-result artifact path
- `usage_governance` is a separate pause source from `operator`, so manual
  resume cannot clear an active governance blocker
- optional Codex subscription quota checks read local Codex session telemetry
  and report degraded status when telemetry is unavailable

Compile notes:

- startup compiles the active mode into `compiled_plan.json`
- `millrace_ai.compiler` is a public facade; the implementation lives under
  `src/millrace_ai/compilation/`
- that compiled plan carries materialized node plans plus compiled entry,
  transition, recovery, learning-trigger, execution-capability,
  concurrency-policy, workflow-primitive, and closure-activation surfaces
- built-in stage work-item ownership, queue claim order, terminal lifecycle
  intent, and runtime effect handler lookup are read from compiled workflow
  authority rather than prompt prose or loose runtime tables
- usage-governance config is next-tick runtime config, not a compile-input
  boundary
- execution-capability config is a compile-input boundary because it changes
  sealed node grants
- compile diagnostics persist separately in `compile_diagnostics.json`
- failed compile attempts keep the last known-good compiled plan intact when one
  exists
- the live runtime executes stage-request construction, activation, and
  post-stage routing from `compiled_plan.json`

## Run Artifact Model

Each run persists under `millrace-agents/runs/<run-id>/`.

Run directories hold:

- `stage_results/*.json`
- `runner_prompt.<request_id>.md`
- `context/context.json`
- `context/prompt_context.md`
- `context/render_manifest.json`
- `runner_invocation.<request_id>.json`
- `runner_completion.<request_id>.json`
- `capability_gate.<request_id>.json` when runtime grant evaluation runs
- runner stdout/stderr artifacts where present
- per-request Codex event logs where present
- stage-authored reports such as `integration_report.md`,
  `troubleshoot_report.md`, or `arbiter_report.md` when emitted

The request-context artifacts are deterministic runtime-owned inputs. The
prompt adapter reads `context/prompt_context.md`, while `context/context.json`
and `context/render_manifest.json` preserve visible refs, omitted providers,
operator-only redactions, and content hashes for later inspection.

The operator-facing `millrace runs ls/show/tail` commands inspect these
persisted artifacts without taking runtime ownership.

## Entrypoint + Skills Contract

- Entrypoints are plain markdown instruction files under `millrace-agents/entrypoints/<plane>/<stage>.md`.
- Work-item stage requests include `active_work_item_path`, `run_dir`, and relevant context paths so entrypoints do not invent runtime paths.
- Closure-target stage requests such as `arbiter` use `request_kind = closure_target` and pass canonical root-source and root-spec paths instead of fabricating an active queue document. Idea-rooted requests also carry legacy seed-idea fields for compatibility.
- Probe stage requests enter Planning through `recon`; successful Recon outputs
  are persisted as `millrace-agents/recon/packets/<PACKET_ID>.md` before
  generated task/spec artifacts are enqueued by the runtime.
- Recon handoff artifacts are strict runtime-owned promotion contracts. A
  malformed `recon_packet.md`, missing generated task/spec, or packet/artifact
  ID mismatch records `recon_handoff_invalid`. If the active Planning graph
  declares a default runtime repair node and repair attempts remain, the runtime
  routes the active probe to that repair node with the runtime error report in
  request context. Otherwise it blocks the probe with the same evidence.
- Stage request construction also checks stage/work-item ownership before a
  runner is invoked, so stale state cannot send a probe to Manager or a spec to
  Recon.
- Learning stage requests use `request_kind = learning_request` and active request paths under `millrace-agents/learning/requests/active/`.
- Learning requests can finish with stage-specific no-op terminal outcomes when
  evidence was reviewed and no skill update is warranted. No-op requests are
  moved to `millrace-agents/learning/requests/done/`, not `blocked/`.
- Runtime-generated generic success learning starts at Analyst. Mode-authored
  direct Curator triggers must include `target_skill_id` or
  `preferred_output_paths`; otherwise compile validation rejects the mode rather
  than letting Curator guess a destination.
- In learning-enabled shipped modes, `PLANNER_COMPLETE` enqueues a targeted
  Librarian learning request. Librarian prepares remote optional skills from
  the supported index as workspace-local installs and does not block foreground
  Planning or Execution.
- Runtime ships `millrace-agents/skills/skills_index.md`, shared skill docs, and one required stage-core skill per stage.
- Entrypoint advisory sections use `Required Stage-Core Skill` and `Optional Secondary Skills` as the only runtime-shipped advisory pattern.
- Optional secondary skills must be present in the packaged or installed skills surface before entrypoints reference them. The packaged skills index points to the supported downloadable optional-skills directory at `https://github.com/tim-osterhus/millrace-skills/blob/main/index.md`.
- Analyst and Librarian may refresh that remote index with `millrace skills
  refresh-remote-index` and install relevant listed remote skills with
  `millrace skills install <skill_id>` before loading them as workspace-local
  optional skills.
- Compile output surfaces stage `required_skills` and `attached_skills` for operator inspection (`millrace compile show`).

For maintainer authoring rules around loops, stage maps, and advisory-vs-runtime
ownership, use `docs/runtime/millrace-loop-authoring.md`.
