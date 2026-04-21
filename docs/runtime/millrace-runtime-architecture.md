# Millrace Runtime Architecture

## Scope

Millrace is a filesystem-backed runtime implemented under `src/millrace_ai/` and imported as `millrace_ai`.
Each workspace is bootstrapped under `<workspace>/millrace-agents/` and owns its own state, queues, lock file, and logs.

Use `docs/runtime/millrace-compiler-and-frozen-plans.md` for compile semantics
and persisted frozen-plan behavior. Use `docs/runtime/millrace-modes-and-loops.md`
for the shipped mode and loop topology the compiler resolves.

## Source Tree

- importable package code lives under `src/millrace_ai/`
- runtime-facing domains are split across `assets/`, `cli/`, `config/`, `runners/`, `runtime/`, and `workspace/`
- tests mirror those ownership boundaries under `tests/assets/`, `tests/cli/`, `tests/config/`, `tests/runners/`, `tests/runtime/`, `tests/workspace/`, and `tests/integration/`
- `docs/source-package-map.md` records the old-to-new module mapping and the root facades intentionally preserved for compatibility

## Workspace Ownership Model

- Workspace root is operator-owned.
- Runtime-managed content lives under `<workspace>/millrace-agents/`.
- Exactly one daemon may own one workspace at a time via `state/runtime_daemon.lock.json`.
- A second daemon in the same workspace fails fast.
- Different workspaces can run independent daemons concurrently.
- `millrace status watch --workspace <PATH> [--workspace <PATH> ...]` is read-only monitoring and does not acquire ownership locks.
- `state/execution_status.md` and `state/planning_status.md` reflect the active running stage on their plane while work is executing, then the latest terminal marker or `### IDLE` when no stage is active.

## Canonical Artifact Model

### Markdown work documents (canonical queue artifacts)

- `millrace-agents/tasks/{queue,active,done,blocked}/*.md`
- `millrace-agents/specs/{queue,active,done,blocked}/*.md`
- `millrace-agents/incidents/{incoming,active,resolved,blocked}/*.md`

Canonical task/spec/incident documents use headed markdown:

- leading H1 title
- scalar headings such as `Task-ID: ...` or `Spec-ID: ...`
- list sections such as `Acceptance:` followed by `- ...` items

JSON imports are still accepted for queue intake, but canonical on-disk queue artifacts are markdown.

### JSON runtime/state artifacts

- `millrace-agents/state/runtime_snapshot.json`
- `millrace-agents/state/recovery_counters.json`
- `millrace-agents/state/compiled_plan.json`
- `millrace-agents/state/compile_diagnostics.json`
- mailbox envelopes/archives and run-scoped runner artifacts

### Arbiter-owned completion artifacts

- `millrace-agents/arbiter/contracts/ideas/*.md`
- `millrace-agents/arbiter/contracts/root-specs/*.md`
- `millrace-agents/arbiter/targets/*.json`
- `millrace-agents/arbiter/rubrics/*.md`
- `millrace-agents/arbiter/verdicts/*.json`
- `millrace-agents/arbiter/reports/*.md`

## Module Topology

- `src/millrace_ai/workspace/paths.py`: workspace contract + bootstrap (`millrace-agents` root + default `millrace.toml`).
- `src/millrace_ai/workspace/work_documents.py`: headed markdown parsing/serialization for task/spec/incident documents.
- `src/millrace_ai/workspace/queue_store.py`: queue claim/transition/requeue facade for markdown documents.
- `src/millrace_ai/workspace/state_store.py`: snapshot/status/counter persistence facade.
- `src/millrace_ai/workspace/runtime_lock.py`: daemon ownership lock acquire/release/inspection.
- `src/millrace_ai/compiler.py`: mode+loop compile into frozen plan + diagnostics.
- `src/millrace_ai/runners/`: stage runner contracts, normalization, adapter registry/dispatcher, and Codex adapter.
- `src/millrace_ai/runtime/__init__.py`: stable `RuntimeEngine` / `RuntimeTickOutcome` import surface.
- `src/millrace_ai/runtime/engine.py`: stable stateful façade that keeps `RuntimeEngine.startup()`, `tick()`, and `close()` as the public runtime surface.
- `src/millrace_ai/runtime/lifecycle.py`: startup/shutdown flow, config/compile bootstrap, watcher rebuild, and daemon-lock lifecycle.
- `src/millrace_ai/runtime/tick_cycle.py`: deterministic one-tick orchestration from mailbox intake through stage execution and router-decision finalization.
- `src/millrace_ai/runtime/mailbox_intake.py`: mailbox drain, reload, and mailbox-applied intake paths.
- `src/millrace_ai/runtime/watcher_intake.py`: watcher session lifecycle and idea-file normalization.
- `src/millrace_ai/runtime/activation.py`: claim ordering and active work-item activation.
- `src/millrace_ai/runtime/completion_behavior.py`: closure-target activation, lineage readiness checks, and compiler-driven backlog-drain dispatch.
- `src/millrace_ai/runtime/reconciliation.py`: stale/impossible-state detection and recovery-stage activation.
- `src/millrace_ai/runtime/result_application.py`: stable façade over routed post-stage mutation helpers.
- `src/millrace_ai/runtime/result_counters.py`: recovery-counter entry mutation and snapshot counter increments.
- `src/millrace_ai/runtime/work_item_transitions.py`: non-closure work-item completion, blocked transitions, and active-snapshot clearing.
- `src/millrace_ai/runtime/handoff_incidents.py`: planning-handoff and arbiter-gap incident materialization.
- `src/millrace_ai/runtime/stage_result_persistence.py`: persisted stage-result JSON writes and plane status-marker updates.
- `src/millrace_ai/runtime/closure_transitions.py`: closure-target state mutation, arbiter report canonicalization, and arbiter-specific handoff/block/close paths.
- `src/millrace_ai/runtime/stage_requests.py`: request rendering, idle outcomes, queue-depth reads, and runtime clock/id helpers.
- `src/millrace_ai/runtime/inspection.py`: persisted run summary inspection and artifact selection helpers.
- `src/millrace_ai/run_inspection.py`: thin compatibility layer that re-exports the runtime inspection surface.
- `src/millrace_ai/control.py`: thin public facade that preserves the stable operator control import surface.
- `src/millrace_ai/runtime/control.py`: public runtime control abstraction that coordinates routing vs direct mutation ownership.
- `src/millrace_ai/runtime/control_mailbox.py`: mailbox-safe daemon routing, command envelope creation, and control enqueue failure boundaries.
- `src/millrace_ai/runtime/control_mutations.py`: direct offline workspace mutations, requeue/reset helpers, and stale-state clearing behavior.
- `src/millrace_ai/watchers.py`: optional watcher session lifecycle and polling fallback intake.
- `src/millrace_ai/doctor.py`: workspace integrity + lock health checks.
- `src/millrace_ai/cli/`: namespaced operator surface split into package assembly, shared resolution, formatting, and command groups.

## Stage Runner Stack

Per stage execution:

1. Runtime builds `StageRunRequest` from compiled plan and active work item.
2. `StageRunnerDispatcher` resolves adapter by runner name precedence.
3. Adapter executes (`codex_cli` by default) and returns `RunnerRawResult`.
4. Runtime normalizes into `StageResultEnvelope` and routes next state.

The runtime boundary stays `StageRunRequest -> RunnerRawResult` so additional adapters (for example Pi harness) can be added without changing orchestration flow.

## Tick Lifecycle

Startup:

1. Bootstrap workspace directories/files under `millrace-agents/`, including a minimal `millrace.toml`.
2. Load config and compile active mode/loops.
3. Acquire daemon ownership lock (daemon mode).
4. Reconcile stale/impossible runtime state.

Per tick:

1. Process mailbox commands (`pause/resume/stop/retry-active/reload-config/intake`, including planning-scoped retry requests).
2. Run stale-state reconciliation and recovery routing.
3. Consume watcher/poll intake events (including idea normalization to planning specs).
4. Respect pause/stop control gates.
5. Claim planning or execution work item.
6. If no claimable work remains, consult frozen `completion_behavior` and activate `arbiter` when an open closure target is eligible.
7. Execute one stage through the configured runner adapter.
8. Route result markers and persist snapshot/status/counters/events.

The implementation mirrors that ordering directly:

- `RuntimeEngine` holds state and exposes the stable methods
- `runtime/tick_cycle.py` owns the one-tick orchestration block
- `runtime/result_application.py` delegates routed mutation into owned collaborators for counters, work-item movement, incident creation, persistence, and closure-target handling

Idle:

- If no claimable work exists and no eligible completion audit exists, runtime emits `no_work` and keeps the daemon loop alive unless stop requested.

Compile notes:

- startup compiles the active mode and loop graph into `compiled_plan.json`
- compile diagnostics persist separately in `compile_diagnostics.json`
- failed compile attempts keep the last known-good frozen plan intact when one
  exists

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
- Runtime ships `millrace-agents/skills/skills_index.md`, shared skill docs, and one required stage-core skill per stage.
- Entrypoint advisory sections use `Required Stage-Core Skill` and `Optional Secondary Skills` as the only runtime-shipped advisory pattern.
- Compile output surfaces stage `required_skills` and `attached_skills` for operator inspection (`millrace compile show`).

For maintainer authoring rules around loops, stage maps, and advisory-vs-runtime
ownership, use `docs/runtime/millrace-loop-authoring.md`.
