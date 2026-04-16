# Millrace Runtime Architecture

## Scope

Millrace is a filesystem-backed runtime implemented in `millrace_ai`.
Each workspace is bootstrapped under `<workspace>/millrace-agents/` and owns its own state, queues, lock file, and logs.

## Workspace Ownership Model

- Workspace root is operator-owned.
- Runtime-managed content lives under `<workspace>/millrace-agents/`.
- Exactly one daemon may own one workspace at a time via `state/runtime_daemon.lock.json`.
- A second daemon in the same workspace fails fast.
- Different workspaces can run independent daemons concurrently.
- `millrace status watch --workspace <PATH> [--workspace <PATH> ...]` is read-only monitoring and does not acquire ownership locks.

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

## Module Topology

- `millrace_ai/paths.py`: workspace contract + bootstrap (`millrace-agents` root + default `millrace.toml`).
- `millrace_ai/work_documents.py`: headed markdown parsing/serialization for task/spec/incident documents.
- `millrace_ai/queue_store.py`: queue claim/transition/requeue for markdown documents.
- `millrace_ai/state_store.py`: snapshot/status/counter persistence and reconciliation signals.
- `millrace_ai/runtime_lock.py`: daemon ownership lock acquire/release/inspection.
- `millrace_ai/compiler.py`: mode+loop compile into frozen plan + diagnostics.
- `millrace_ai/runners/*`: stage runner adapter registry/dispatcher and Codex adapter.
- `millrace_ai/runtime/__init__.py`: stable `RuntimeEngine` / `RuntimeTickOutcome` import surface.
- `millrace_ai/runtime/engine.py`: orchestration facade for startup, tick ordering, lock lifecycle, and runtime-owned control resets.
- `millrace_ai/runtime/mailbox_intake.py`: mailbox drain, reload, and mailbox-applied intake paths.
- `millrace_ai/runtime/watcher_intake.py`: watcher session lifecycle and idea-file normalization.
- `millrace_ai/runtime/activation.py`: claim ordering and active work-item activation.
- `millrace_ai/runtime/reconciliation.py`: stale/impossible-state detection and recovery-stage activation.
- `millrace_ai/runtime/result_application.py`: router decisions, counter updates, stage-result persistence, and handoff/blocking side effects.
- `millrace_ai/runtime/stage_requests.py`: request rendering, idle outcomes, queue-depth reads, and runtime clock/id helpers.
- `millrace_ai/runtime/inspection.py`: persisted run summary inspection and artifact selection helpers.
- `millrace_ai/run_inspection.py`: thin compatibility layer that re-exports the runtime inspection surface.
- `millrace_ai/control.py`: operator mutation routing (direct vs mailbox-safe when daemon-owned).
- `millrace_ai/watchers.py`: optional watcher session lifecycle and polling fallback intake.
- `millrace_ai/doctor.py`: workspace integrity + lock health checks.
- `millrace_ai/cli/`: namespaced operator surface split into package assembly, shared resolution, formatting, and command groups.

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
6. Execute one stage through the configured runner adapter.
7. Route result markers and persist snapshot/status/counters/events.

Idle:

- If no claimable work exists, runtime emits `no_work` tick reason and keeps the daemon loop alive unless stop requested.

## Run Artifact Model

Each run persists under `millrace-agents/runs/<run-id>/`.

Run directories hold:

- run-scoped compile artifacts
- `stage_results/*.json`
- runner stdout/stderr artifacts where present
- troubleshoot reports when `Troubleshooter` emits one

The operator-facing `millrace runs ls/show/tail` commands inspect these persisted artifacts without taking runtime ownership.

## Entrypoint + Skills Contract

- Entrypoints are plain markdown instruction files under `millrace-agents/entrypoints/<plane>/<stage>.md`.
- Stage requests include `active_work_item_path`, `run_dir`, and relevant context paths so entrypoints do not invent runtime paths.
- Runtime ships `millrace-agents/skills/skills_index.md`, shared skill docs, and one required stage-core skill per stage.
- Entrypoint advisory sections use `Required Stage-Core Skill` and `Optional Secondary Skills` as the only runtime-shipped advisory pattern.
- Compile output surfaces stage `required_skills` and `attached_skill_additions` for operator inspection (`millrace compile show`).
