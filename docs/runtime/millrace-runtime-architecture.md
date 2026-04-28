# Millrace Runtime Architecture

## Scope

Millrace is a filesystem-backed runtime implemented under `src/millrace_ai/` and imported as `millrace_ai`.
Each workspace is explicitly initialized under `<workspace>/millrace-agents/` by `millrace init` and owns its own state, queues, lock file, and logs.

Use `docs/runtime/millrace-compiler-and-frozen-plans.md` for compile semantics
and persisted compiled-plan behavior. Use `docs/runtime/millrace-modes-and-loops.md`
for the shipped mode and loop topology the compiler resolves.

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
- `millrace-agents/specs/{queue,active,done,blocked}/*.md`
- `millrace-agents/incidents/{incoming,active,resolved,blocked}/*.md`
- `millrace-agents/learning/requests/{queue,active,done,blocked}/*.md`

Canonical task/spec/incident/learning-request documents use headed markdown:

- leading H1 title
- scalar headings such as `Task-ID: ...` or `Spec-ID: ...`
- list sections such as `Acceptance:` followed by `- ...` items

JSON imports are still accepted for queue intake, but canonical on-disk queue artifacts are markdown.

### JSON runtime/state artifacts

- `millrace-agents/state/runtime_snapshot.json`
- `millrace-agents/state/recovery_counters.json`
- `millrace-agents/state/compiled_plan.json`
- `millrace-agents/state/compile_diagnostics.json`
- `millrace-agents/state/baseline_manifest.json`
- `millrace-agents/state/execution_status.md`
- `millrace-agents/state/planning_status.md`
- `millrace-agents/state/learning_status.md`
- `millrace-agents/state/usage_governance_state.json`
- `millrace-agents/state/usage_governance_ledger.jsonl`
- mailbox envelopes/archives and run-scoped runner artifacts

### Arbiter-owned completion artifacts

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
- `src/millrace_ai/workspace/work_documents.py`: headed markdown parsing/serialization for task/spec/incident/learning-request documents.
- `src/millrace_ai/workspace/queue_store.py`: queue claim/transition/requeue facade for markdown documents.
- `src/millrace_ai/workspace/state_store.py`: snapshot/status/counter persistence facade.
- `src/millrace_ai/workspace/runtime_lock.py`: daemon ownership lock acquire/release/inspection.
- `src/millrace_ai/contracts/`: public typed contract facade plus owned contract families for enums, stage metadata, work documents, stage results, loop/mode definitions, compiler diagnostics, runtime snapshots, runtime error contexts, mailbox payloads, and recovery counters. `contracts/stage_metadata.py` is the single registry for stage plane membership, legal terminal results, running markers, prompt markers, and result-class policy.
- `src/millrace_ai/compiler.py`: stable public facade for mode+graph-loop compile, graph preview, currentness inspection, and diagnostics.
- `src/millrace_ai/compilation/`: compiler internals for workspace compile orchestration, mode/path resolution, graph and node materialization, transition/completion/policy compilation, learning-trigger validation, entrypoint override validation, asset resolution, fingerprints, persistence, and currentness inspection.
- `src/millrace_ai/runners/`: stage runner contracts, normalization, adapter registry/dispatcher, and Codex/Pi adapters.
- `src/millrace_ai/cli/monitoring.py`: formatting for opt-in daemon monitor output.
- `src/millrace_ai/runtime/__init__.py`: stable `RuntimeEngine` / `RuntimeTickOutcome` import surface.
- `src/millrace_ai/runtime/engine.py`: stable stateful façade that keeps `RuntimeEngine.startup()`, `tick()`, and `close()` as the public runtime surface.
- `src/millrace_ai/runtime/outcomes.py`: runtime tick outcome contract shared by the engine and tick/request helpers without creating an engine import cycle.
- `src/millrace_ai/runtime/lifecycle.py`: startup/shutdown flow, config/compile bootstrap, watcher rebuild, and daemon-lock lifecycle.
- `src/millrace_ai/runtime/monitoring.py`: runtime monitor event protocol and null monitor sink.
- `src/millrace_ai/cli/monitoring.py`: basic terminal monitor renderer for the
  concise human-facing daemon stream; full ids and details stay in persisted
  runtime events and run artifacts.
- `src/millrace_ai/runtime/tick_cycle.py`: deterministic one-tick orchestration from mailbox intake through stage execution and router-decision finalization.
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
- `src/millrace_ai/runtime/handoff_incidents.py`: planning-handoff and arbiter-gap incident materialization.
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
- `src/millrace_ai/runtime/control_mutations.py`: direct offline workspace mutations, pause/resume source handling, requeue/reset helpers, and stale-state clearing behavior.
- `src/millrace_ai/watchers.py`: optional watcher session lifecycle and polling fallback intake.
- `src/millrace_ai/doctor.py`: workspace integrity + lock health checks.
- `src/millrace_ai/assets/entrypoints/`: packaged entrypoint markdown assets plus the parsing/linting package that validates entrypoint and advisory skill manifests.
- `src/millrace_ai/cli/errors.py`: operator error output helper used by command modules and shared workspace resolution.
- `src/millrace_ai/cli/status_view.py`: status state loading and line rendering.
- `src/millrace_ai/cli/runs_view.py`: persisted run-list loading and line rendering.
- `src/millrace_ai/cli/config_view.py`: config-show state loading and line rendering.
- `src/millrace_ai/cli/compile_view.py`: compile diagnostics and compile-show line rendering.
- `src/millrace_ai/cli/formatting.py`: pure rendering helpers for already-collected run/control values.
- `src/millrace_ai/cli/`: namespaced operator surface split into package assembly, shared resolution, command-specific views, monitor formatting, and command groups.

## Stage Runner Stack

Per stage execution:

1. Runtime builds `StageRunRequest` from the compiled plan and active work item.
2. `StageRunnerDispatcher` resolves adapter by runner name precedence.
3. Adapter executes (`codex_cli` by default, `pi_rpc` in Pi modes) and returns `RunnerRawResult`.
4. Runtime normalizes into `StageResultEnvelope` and routes next state.

The runtime boundary stays `StageRunRequest -> RunnerRawResult` so additional adapters can be added without changing orchestration flow.

## Tick Lifecycle

Startup:

1. Require an initialized workspace baseline under `millrace-agents/`; use `millrace init` to create it.
2. Load config and compile or reuse the current active mode plan.
3. Acquire daemon ownership lock (daemon mode).
4. Reconcile stale/impossible runtime state.

Per tick:

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
9. If no same-lineage work remains, consult compiled `completion_behavior` and activate `arbiter` when an open closure target is eligible.
10. Re-evaluate usage governance before dispatching an active stage.
11. Execute one stage through the configured runner adapter.
12. Route result markers, record post-stage usage, and persist snapshot/status/counters/events.

The implementation mirrors that ordering directly:

- `RuntimeEngine` holds state and exposes the stable methods
- `runtime/tick_cycle.py` owns the one-tick orchestration block
- `runtime/result_application.py` delegates routed mutation into owned collaborators for counters, work-item movement, incident creation, persistence, and closure-target handling

Idle:

- If no claimable work exists and no eligible completion audit exists, runtime emits `no_work` and keeps the daemon loop alive unless stop requested.
- If unrelated root specs are queued while a closure target is open, runtime
  emits `closure_target_backpressure`, keeps the daemon alive, and reports
  `planning_root_specs_deferred_by_closure_target` through `millrace status`.

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
  transition, recovery, learning-trigger, concurrency-policy, and
  closure-activation surfaces
- usage-governance config is next-tick runtime config, not a compile-input
  boundary
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
- `runner_invocation.<request_id>.json`
- `runner_completion.<request_id>.json`
- runner stdout/stderr artifacts where present
- per-request Codex event logs where present
- stage-authored reports such as `troubleshoot_report.md` or
  `arbiter_report.md` when emitted

The operator-facing `millrace runs ls/show/tail` commands inspect these persisted artifacts without taking runtime ownership.

## Entrypoint + Skills Contract

- Entrypoints are plain markdown instruction files under `millrace-agents/entrypoints/<plane>/<stage>.md`.
- Work-item stage requests include `active_work_item_path`, `run_dir`, and relevant context paths so entrypoints do not invent runtime paths.
- Closure-target stage requests such as `arbiter` use `request_kind = closure_target` and pass canonical root-spec and seed-idea paths instead of fabricating an active queue document.
- Learning stage requests use `request_kind = learning_request` and active request paths under `millrace-agents/learning/requests/active/`.
- Runtime ships `millrace-agents/skills/skills_index.md`, shared skill docs, and one required stage-core skill per stage.
- Entrypoint advisory sections use `Required Stage-Core Skill` and `Optional Secondary Skills` as the only runtime-shipped advisory pattern.
- Optional secondary skills must be present in the packaged or installed skills surface before entrypoints reference them. The packaged skills index points to the supported downloadable optional-skills directory at `https://github.com/tim-osterhus/millrace-skills/blob/main/index.md`.
- Compile output surfaces stage `required_skills` and `attached_skills` for operator inspection (`millrace compile show`).

For maintainer authoring rules around loops, stage maps, and advisory-vs-runtime
ownership, use `docs/runtime/millrace-loop-authoring.md`.
