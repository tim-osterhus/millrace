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
  compiled source lifecycle intents with explicit source, outcome, family, and
  applicability-context metadata to active queue documents.
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
- `src/millrace_ai/contracts/`: public typed contract facade plus owned contract families for enums, stage metadata, work documents, stage results, loop/mode definitions, compiler diagnostics, runtime snapshots, runtime error contexts, mailbox payloads, and recovery counters. `contracts/terminal_outcomes.py` owns the string-backed terminal outcome contract shared by stage results, runtime snapshots, runtime error contexts, and runner normalization. `contracts/stage_metadata.py` is the single registry for stage plane membership, legal terminal results, running markers, prompt markers, and result-class policy.
- `src/millrace_ai/compiler.py`: stable public facade for mode+graph-loop compile, graph preview, currentness inspection, and diagnostics.
- `src/millrace_ai/compilation/`: compiler internals for workspace compile orchestration, mode/path resolution, graph and node materialization, transition/completion/policy compilation, learning-trigger validation, entrypoint override validation, asset resolution, fingerprints, persistence, and currentness inspection.
- `src/millrace_ai/runners/`: stage runner contracts, normalization, adapter registry/dispatcher, and Codex/Pi adapters.
- `src/millrace_ai/cli/monitoring.py`: formatting for opt-in daemon monitor output.
- `src/millrace_ai/runtime/__init__.py`: stable `RuntimeEngine`, daemon supervisor, and runtime outcome import surface.
- `src/millrace_ai/runtime/engine.py`: stable stateful façade that keeps `RuntimeEngine.startup()`, `tick()`, and `close()` as the public runtime surface.
- `src/millrace_ai/runtime/outcomes.py`: runtime tick outcome contract shared by the engine and tick/request helpers without creating an engine import cycle.
- `src/millrace_ai/runtime/lifecycle.py`: startup/shutdown flow, config/compile bootstrap, watcher rebuild, and daemon-lock lifecycle.
- `src/millrace_ai/runtime/effects/`: runtime effect result contracts,
  source lifecycle intent creation from effect-rule metadata,
  destination-existence checks, and effect-result application.
- `src/millrace_ai/runtime/effects/models.py`: `RuntimeEffectResult`,
  `RuntimeEffectDecision`, `RuntimeEffectMutationPhase`,
  `SourceLifecycleIntent`, and the post-effect result application helper.
- `src/millrace_ai/runtime/effects/registry.py`: operation-indexed handler
  registry seam that keeps legacy Python handler ids as compatibility aliases
  during the operation-id migration. Maps operation IDs to handler callables
  and runner IDs.
- `src/millrace_ai/runtime/effects/legacy.py`: temporary registry for legacy
  Python runtime-effect handler registrations (`LEGACY_PYTHON_EFFECT_RUNNER_ID`).
  Provides backward-compatible handler lookup until declarative operations fully
  replace handler-backed mutation code.
- `src/millrace_ai/runtime/effects/primitives.py`: primitive executor registry
  for the interpreted runtime-effect runner path. Maps primitive IDs
  (`artifact_presence`, `artifact_model_parse`, `persist_record`,
  `enqueue_work_items`, `emit_event`) to executor callables.
- `src/millrace_ai/runtime/effects/interpreter.py`: constrained step
  interpreter that walks compiled operation steps, resolves bindings,
  dispatches to the primitive executor registry, enforces idempotent resume
  via the durable journal, and returns a `RuntimeEffectResult`. Activated
  when `effect_execution.py` detects `runner_id == "interpreted_runtime_effect"`.
- `src/millrace_ai/runtime/effects/journal.py`: durable JSONL journal helpers
  for interpreted operation mutations. Writes `started` records before,
  `completed` records after, and `failed` records for mutating primitive
  failure paths; records carry runner/source/run/request/step/primitive
  context, explicit status, and SHA-256 idempotency hashes. Also provides
  `completed_hashes_by_step()` for resume.
- `src/millrace_ai/runtime/effect_execution.py`: compiled runtime-effect
  operation-id-first dispatch, interpreted-runner dispatch by runner identity,
  operation/runner/legacy-handler identity annotation,
  failure-policy interpretation, matched-policy metadata,
  effect-rule-declared source-lifecycle application for
  `REQUEST_COMPLETE_SOURCE` and `REQUEST_BLOCK_SOURCE`, and shared
  terminal-action exhaustion fallback when runtime-failure recovery declares
  an exhausted terminal state.
- `src/millrace_ai/runtime/failure_policy.py`: runtime failure-origin
  classification plus runtime-effect failure policy matching by operation id
  with legacy handler-id fallback only for compatibility policies, including
  conservative Blueprint blocks and recoverable Mechanic Blueprint routes.
- `src/millrace_ai/runtime/effects/operation_runners/`: focused runtime-effect
  Python executors for handler-backed operations that still need file mutation
  code. Operation selection comes from compiled runtime-effect
  operation/rule/runner metadata, not from loop-mode branches.
- `src/millrace_ai/runtime/runtime_effect_status.py`: generic status metadata
  extraction for latest runtime-effect stage results used by status output.
- `src/millrace_ai/runtime/planner_effects.py`: Planner disposition handling
  for active-source continuation, emitted-child-spec completion/resolution, and
  blocked Planner outcomes.
- `src/millrace_ai/runtime/request_context.py`: compatibility facade for the
  runtime request-context surface exported from `src/millrace_ai/runtime/context/`.
- `src/millrace_ai/runtime/context/`: request-context implementation package for
  provider/render-plan resolution, context rendering, and Blueprint-specific
  context helpers including repair-context refs.
- `src/millrace_ai/runtime/lifecycle_interpreter.py`: runtime-facing bridge from
  source lifecycle intent to the workspace queue lifecycle interpreter,
  including terminal-action-driven ordinary source work-item resolution via
  compiled lifecycle plans selected by validation rather than legacy wildcard
  semantics.
- `src/millrace_ai/runtime/monitoring.py`: runtime monitor event protocol and null monitor sink.
- `src/millrace_ai/cli/monitoring.py`: basic terminal monitor renderer for the
  concise human-facing daemon stream; compact terminal-action/lifecycle/
  runtime-operation, failure-class, and incident details stay live while
  persisted runtime events and run artifacts retain the full payload.
- `src/millrace_ai/runtime/tick_cycle.py`: serial one-tick orchestration used by
  bounded daemon execution and compatibility tests.
- `src/millrace_ai/runtime/supervisor.py`: daemon-mode lane scheduler, lane-keyed worker registry, and serialized completion-application owner.
- `src/millrace_ai/runtime/scheduler_policy.py`: shared compiled scheduler-policy
  interpreter for foreground order, closure-target inversion, learning
  dispatch, and four residual-surface helpers (fallback entry behavior,
  targeted Learning routing, recovery fallback routing, and claim
  deferral/backpressure policy) used by `tick_cycle.py`, `supervisor.py`,
  `activation.py`, `lanes.py`, `repair_routes.py`, and
  `completion_behavior.py`.
- `src/millrace_ai/runtime/blocked_recovery.py`: compatibility facade for
  blocked-recovery helpers backed by `src/millrace_ai/runtime/recovery/`.
- `src/millrace_ai/runtime/recovery/`: focused recovery package for blocked
  metadata, retry policy, queue mutation, environmental classification, runtime
  error context persistence, reports, and repair-route helpers.
- `src/millrace_ai/runtime/error_recovery.py`: runtime exception recovery
  orchestration entrypoint that composes the focused `runtime/recovery/`
  helpers while preserving the stable runtime import surface, including
  shared terminal-action exhaustion decisions for post-stage and pre-dispatch
  recovery exhaustion.
- `src/millrace_ai/runtime/mailbox_intake.py`: mailbox drain, reload, and mailbox-applied intake paths.
- `src/millrace_ai/runtime/watcher_intake.py`: watcher session lifecycle and idea-file normalization.
- `src/millrace_ai/runtime/activation.py`: claim ordering and active work-item activation, backed by the shared compiled scheduler-policy interpreter.
- `src/millrace_ai/runtime/pause_state.py`: pause-source mutation helpers for operator and usage-governance pauses.
- `src/millrace_ai/runtime/usage_governance/`: opt-in usage-governance authority package, with state/ledger models, durable state persistence, runtime-token window evaluation, subscription-quota telemetry, monitor event emission, and engine-facing pause-source application split behind the stable package facade.
- `src/millrace_ai/runtime/graph_authority/`: compiled-graph activation and routing authority package — the **generic-router home** for all planes. Contains `routing.py`'s single active dispatch entrypoint (`route_stage_result_from_graph`), which performs upfront stage-result identity validation and routes every plane directly through `generic_router.py`'s active compiled-graph routing logic (`route_generic_stage_result_from_graph`). `execution.py`, `planning.py`, and `learning.py` are compatibility wrappers that forward to the generic router; active routing no longer dispatches through those wrappers or accepts route-time max-cycle recovery knobs. Shared terminal-state/action and runtime-operation resolution remains behind the stable package facade.
- `src/millrace_ai/runtime/graph_authority/counters.py`: recovery-counter entry mutation helpers that apply declared counter mutation intent from compiled graph policy metadata instead of inferring mutation from destination stage names.
- `src/millrace_ai/runtime/graph_authority/terminal_actions.py`: shared terminal-state/action and runtime-operation resolver for terminal transitions, threshold exhaustion, and runtime-failure recovery exhaustion, including explicit non-mutating terminal-action authority and lifecycle-action validation.
- `src/millrace_ai/runtime/completion_behavior.py`: closure-target activation, lineage readiness checks, and compiler-driven backlog-drain dispatch.
- `src/millrace_ai/runtime/reconciliation.py`: stale/impossible-state detection and recovery-stage activation.
- `src/millrace_ai/runtime/result_application.py`: stable façade over routed post-stage mutation helpers and ordinary source work-item lifecycle application.
- `src/millrace_ai/runtime/result_counters.py`: recovery-counter entry mutation, normalized terminal-outcome comparisons, and snapshot counter increments.
- `src/millrace_ai/runtime/work_item_transitions.py`: non-closure work-item completion, blocked transitions, terminal-action-driven source lifecycle application, explicit non-mutating terminal-action clearing, and active-snapshot clearing.
- `src/millrace_ai/runtime/handoff_incidents.py`: planning-handoff and arbiter-gap incident materialization, including source work-item lineage inheritance and terminal-action failure-class defaults for runtime-created handoff incidents.
- `src/millrace_ai/runtime/recon_transitions.py`: Recon packet persistence and runtime-operation-driven probe-to-task/spec/no-op/blocked mutation. Recon route selection resolves the registered `runtime_operation_id` from the compiled terminal action against the compiled runtime operation registry; the fixed `_TERMINAL_ACTION_RUNTIME_OPERATION_IDS` whitelist has been removed from active source.
- `src/millrace_ai/runtime/stage_result_persistence.py`: persisted stage-result JSON writes and plane status-marker updates.
- `src/millrace_ai/runtime/learning_triggers.py`: compiler-frozen learning-trigger evaluation and learning-request enqueueing.
- `src/millrace_ai/runtime/skill_evidence.py`: per-request skill revision evidence snapshots for learning-enabled runs.
- `src/millrace_ai/runtime/snapshot_state.py`: shared snapshot reset/update helpers.
- `src/millrace_ai/runtime/closure_transitions.py`: closure-target state mutation, arbiter report canonicalization, and arbiter-specific handoff/block/close paths.
- `src/millrace_ai/runtime/stage_requests.py`: request rendering, idle outcomes, queue-depth reads, and runtime clock/id helpers.
- `src/millrace_ai/runtime/run_traces.py`: persisted run-trace graph persistence, fallback derivation, and terminal-metadata provenance labels for trace inspection.
- `src/millrace_ai/runtime/inspection.py`: persisted run summary inspection, terminal-metadata provenance, and artifact selection helpers.
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

## Runtime-Effect Operation Dispatch: Interpreted vs Legacy Handler-Backed

Runtime-effect operations are dispatched through one of two paths depending on
their compiled runner identity:

### Interpreted Runner Path (`interpreted_runtime_effect`)

The interpreted runner walks a compiled step list declared in the operation's
asset definition. Each step declares a `primitive_id`; the interpreter resolves
bindings, dispatches to the primitive executor registry
(`runtime/effects/primitives.py`), and returns a `RuntimeEffectResult`.

**Current scope:** the interpreted runner is activated only by test fixture
operations. No shipped mode or graph selects an interpreted-runner operation
for a production runtime-effect rule.

### Legacy Handler-Backed Path (`legacy_python_handler`)

All shipped runtime-effect operations use the legacy handler-backed runner.
Handler callables are registered in `runtime/effects/legacy.py` and resolved
through `_handler_for_operation()` in `effect_execution.py`. These are
Python functions that perform file mutation directly, typically via the
operation runner modules in `runtime/effects/operation_runners/`.

### Primitive Vocabulary

The following primitives have interpreted executors in
`runtime/effects/primitives.py`:

| Primitive ID | Description |
| --- | --- |
| `artifact_presence` | Verify referenced artifact files exist in the run directory. |
| `artifact_model_parse` | Parse an artifact through its compiled contract or a safe JSON/text fallback. |
| `persist_record` | Persist a record to a compiled store directory. |
| `enqueue_work_items` | Enqueue child work-item documents to a queue directory. |
| `emit_event` | Emit a best-effort runtime event. |

Primitives that exist as compiled definitions but have no interpreted executor
are **compatibility-only**: they are declared in
`assets/registry/runtime_effect_primitives/` with `non_interpreted_compatibility:
true` and are used only by legacy handler-backed operations (e.g.
`blueprint_critique_packet_validation`, `legacy_python_handler`,
`store_equivalence_check`). The interpreter rejects any step whose
`primitive_id` has no registered executor.

### Runner Ownership Model

Operations are assigned to runners via the compiled
`RuntimeEffectOperationRunnerDefinition.operation_ids` list — each runner
declares which operation IDs it owns. The runtime resolves a runner for an
operation by scanning `compiled_plan.runtime_effect_runners_by_id` for the
runner whose `operation_ids` contains the operation ID. Operations do not
carry their own `runner_id` field. This model is enforced at compile time:
interpreted-runner operations may reference only primitives marked
`non_interpreted_compatibility: false` that also have registered executors,
and legacy-runner operations are checked against primitive-required
capabilities declared by their runner instead of a blanket interpreted-only
primitive ban.

### Binding Grammar

Interpreted operation steps resolve input values through a binding grammar
implemented in `interpreter.py:_resolve_binding()`:

| Prefix | Syntax | Resolution |
| --- | --- | --- |
| `$artifact.<id>` | `$artifact.planner_disposition` | Passes through the artifact ID string; the executor resolves it to a file path. |
| `$context.<key>` | `$context.source_run_id` | Looks up `key` in the interpreter's step context dict. |
| `$store.<id>` | `$store.blueprint_manifests` | Passes through the store ID string; the executor resolves it to a directory. |
| Plain string | `"literal_value"` | Treated as a JSON literal value. |

Path traversal (`..`) and absolute paths are rejected at compile time by the
step-binding model validator in `architecture/effect_operations.py`. Context
forward-references (reading a key before a prior step writes it) are also
rejected at compile time.

### Journal Semantics

Mutating interpreted primitives (`persist_record`, `enqueue_work_items`) write
durable JSONL journal records to enable idempotent resume after interruption:

- **Location:** `millrace-agents/state/runtime-effect-journal/<operation_id>.jsonl`
- **Started record:** written before each mutating primitive executes.
  Contains `operation_id`, `runner_id`, source work-item family/id, `run_id`,
  `request_id` when available, `step_id`, `primitive_id`, `status` set to
  `"started"`, `timestamp`, SHA-256 `idempotency_hash`, canonicalized
  `params`, and sorted `reads_artifact_ids`.
- **Completed record:** written after successful mutation. Contains the
  same operation/runner/source/run/request/step/primitive context, `status`
  set to `"completed"`, `timestamp`, and the SHA-256 idempotency hash computed
  from `operation_id`, `step_id`, canonicalized params, and sorted artifact
  IDs.
- **Failed record:** written for mutating primitive exceptions,
  executor-returned `pre_mutation_failure` decisions, and idempotency conflicts
  when an uncompleted started record exists. Contains the same context and hash
  fields plus `status: "failed"`, `failure_class`, and `failure_message`,
  without repeating queue or store side effects.
- **Idempotent resume:** on each invocation, the interpreter loads all
  completed hashes via `completed_hashes_by_step()`. A completed step with a
  matching hash is skipped. A completed step with a different hash triggers
  `interpreted_idempotency_conflict`, which is routed through the operation's
  `failure_mappings`.
- **Ordering guarantee:** started before mutation, completed after success,
  failed before returning a blocking runtime-effect result for mutating
  primitive failures. The journal is append-only JSONL; ordering within each
  operation file reflects the execution sequence.

### Remaining Legacy Compatibility Surfaces

- `src/millrace_ai/runtime/effects/legacy.py` — temporary handler registry;
  `LEGACY_PYTHON_EFFECT_RUNNER_ID` is the runner identity for all shipped operations.
- `src/millrace_ai/runtime/effects/operation_runners/` — handler-backed
  Python executors for Blueprint and Planner operations.
- `effect_execution.py:_handler_for_operation()` — still resolves handlers
  through the legacy registry for non-interpreted operations.
- `effect_execution.py:_legacy_handler_id_for_operation()` — maps operation
  IDs to legacy handler IDs for result annotation and failure-policy matching.
- `runtime_effect_primitives/` asset — 13 of 18 shipped primitive definitions
  are marked `non_interpreted_compatibility: true`. The five interpreted
  primitives (`artifact_presence`, `artifact_model_parse`, `persist_record`,
  `enqueue_work_items`, and `emit_event`) have the marker set to `false` and
  have registered interpreted executors.
- `default_effect_runners.json` — all six shipped operations are assigned to
  `legacy_python_handler`; no shipped operation uses the interpreted runner.

## Stage Runner Stack

Per stage execution:

1. Runtime builds `StageRunRequest` from the compiled plan and active work item.
2. Runtime evaluates compiled execution capability grants and approval gates.
3. `StageRunnerDispatcher` resolves adapter by runner name precedence.
4. Adapter executes (`codex_cli` by default, `pi_rpc` in Pi modes) and returns `RunnerRawResult`.
5. Runtime normalizes into `StageResultEnvelope`, persists the stage result,
   upserts the run-trace node, applies the authoritative router decision via
   `runtime/graph_authority/routing.py`'s `route_stage_result_from_graph`
   (which delegates to `generic_router.py`'s compiled-plan-driven
   `route_generic_stage_result_from_graph`), and records the run-trace edge.

The runtime boundary stays `StageRunRequest -> RunnerRawResult` so additional adapters can be added without changing orchestration flow.

## Kernel Boundary

The runtime kernel (`src/millrace_ai/runtime/`, `src/millrace_ai/workspace/`) owns
orchestration, lifecycle, and durable state — not workflow semantics.
Workflow routing, terminal-action lifecycle plans, queue claim policies,
and recovery/failure policies are resolved from the compiled plan, not from
kernel-level plane-enum or stage-enum branches (see ADR-0012 through
ADR-0015).

Key boundary:

- `runtime/graph_authority/generic_router.py` owns the active compiled-graph
  routing logic shared by all planes.
- `runtime/graph_authority/routing.py` is the identity-checking dispatch
  entrypoint that validates stage-result identity and routes every plane
  directly through the generic router with plane-agnostic formatter callbacks.
  It does not dispatch through per-plane wrappers or accept route-time
  max-cycle recovery knobs. Generic formatters derive fallback route reasons
  and failure classes from ``stage_result.node_id`` (compiled node identity)
  rather than ``source_stage.value`` (runtime stage-name string).
- `runtime/graph_authority/execution.py`, `planning.py`, and `learning.py` are
  compatibility wrappers that forward to the generic router with plane-specific
  terminal formatters where needed.
- `router.py` at the package root is a stable compatibility surface for
  legacy imports; active dispatch does not call its plane-specific functions.

This boundary keeps workflow-authority changes (new graphs, terminals, or
recovery policies) in graph assets and compiled plans without requiring
runtime-kernel edits.

### Runtime-Operation Registry Authority

Terminal actions declare a `runtime_operation_id` that must resolve to a
registered operation in the compiled runtime operation registry. The compiler
validates that the operation exists in `runtime_operations_by_id`, declares
`terminal_action` in its `allowed_contexts`, and satisfies its required
capabilities. At dispatch, `graph_authority/terminal_actions.py` resolves the
operation id from the terminal action for every router decision, and
`recon_transitions.py` maps that id to the Recon route through an
operation-id-keyed table.

The old `_TERMINAL_ACTION_RUNTIME_OPERATION_IDS` fixed whitelist has been
removed from active source. Operation discovery, validation, and dispatch all
read compiled registry assets. Runtime-effect dispatch (`effect_execution.py`)
uses a separate runtime-effect operation catalog; the runtime-operation
registry's `allowed_contexts` field distinguishes terminal-action operations
from runtime-effect operations.

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
8. Claim planning, execution, or learning work item according to the compiled
   scheduler policy's foreground order. `runtime/scheduler_policy.py` provides
   a shared interpreter that `activation.py`, `tick_cycle.py`, and
   `supervisor.py` all use — there is no duplicate hard-coded claim order.
   When compiled predicate-backed rules are present, the interpreter evaluates
   them first and can override the scalar `foreground_order`/
   `closure_priority` compatibility path. When a closure target is already
   open and no matching rule overrides it, the policy's `closure_priority`
   inverts execution before planning so execution claims are attempted first.
   Learning dispatch is gated by `learning_dispatch`: `"inline"` preserves
   existing post-foreground behavior, `"deferred"` suppresses separate
   learning claims, and `"interleaved"` is reserved. Fallback entry behavior
   (`recon_on_idle`, `skip`, or `pause`), targeted Learning routing,
   and claim deferral/backpressure (block, defer, allow) are all interpreted
   through the shared scheduler-policy helpers. Unrelated queued root
   specs remain behind the closure target unless the scheduler-policy
   backpressure field explicitly allows concurrent closure targets.
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
- `runtime/effects/` and `workspace/queue_lifecycle.py` keep terminal
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
- materialized node plans carry `runtime_stage`, request-context profile
  authority, and context render-plan authority for every compiled node; the
  rendered request-context artifacts also preserve the selected `provider_id`
- stage-kind assets declare `runtime_stage` and `required_skill_paths`, and
  node materialization reads required skills from those assets rather than a
  hardcoded stage map
- request-context authority resolves at runtime from graph/stage-kind-declared
  compiled node fields or request-level explicit fields; missing profile or
  render-plan authority is a stale-plan compatibility error with
  recompile/update guidance
- stale compiled plans missing canonical `runtime_stage` fail validation with
  clear recompile/update guidance; noncanonical nodes missing `runtime_stage`
  are compatibility errors before dispatch
- built-in stage work-item ownership, queue claim order, terminal lifecycle
  intent, and runtime effect operation/runner lookup are read from compiled
  workflow authority rather than prompt prose or loose runtime tables
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
operator-only redactions, profile/provider/render-plan ids, and content hashes
for later inspection.

The operator-facing `millrace runs ls/show/tail` commands inspect these
persisted artifacts without taking runtime ownership.

## Entrypoint + Skills Contract

- Entrypoints are plain markdown instruction files under `millrace-agents/entrypoints/<plane>/<stage>.md`.
- Work-item stage requests include `active_work_item_path`, `run_dir`, and relevant context paths so entrypoints do not invent runtime paths.
- Closure-target stage requests such as `arbiter` use `request_kind = closure_target` and pass canonical root-source and root-spec paths instead of fabricating an active queue document. Idea-rooted requests also carry legacy seed-idea fields for compatibility.
- Probe stage requests enter Planning through `recon`; successful Recon outputs
  are persisted as `millrace-agents/recon/packets/<PACKET_ID>.md` before
  generated task/spec artifacts are enqueued by the runtime, and the
  graph-resolved terminal action's `runtime_operation_id` selects the Recon
  route.
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
