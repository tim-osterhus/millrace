# Runtime State, Status Markers, And Stale Recovery Semantics

## 1. Purpose And Scope

This document owns the runtime boundary for persisted execution-state files, status-marker contracts, stale runtime-snapshot interpretation, and the supported controls Millrace exposes for recovering from stale execution state.

It explains which files carry live execution and research truth, which files are only supporting evidence, how stale or degraded runtime snapshots are normalized before operators act on them, and which recovery paths are officially supported when state no longer reflects reality.

It does not own engine start/stop lifecycle sequencing, mailbox command-family semantics, queue algorithm design, or the research-plane recovery latch. Those belong to the lifecycle, control-plane, pipeline, and failure-playbook deep docs.

## 2. Source-Of-Truth Surfaces

The authority for this boundary is split across a small set of runtime modules and reports:

- `millrace_engine/status.py` owns execution and research status-marker parsing, plane ownership, one-marker file shape, legal transition graphs, and stage-terminal validation.
- `millrace_engine/paths.py` defines the canonical workspace file inventory for runtime-owned state, including the status files, queue stores, `.runtime/` snapshot files, and recovery-request paths.
- `millrace_engine/control_models.py` defines the persisted and operator-visible models for `RuntimeState`, `RuntimeLivenessView`, `DeferredActiveTaskClear`, `ActiveTaskRemediationResult`, and `RecoveryRequestRecord`.
- `millrace_engine/control_reports.py` reads those persisted files and builds `StatusReport` and `SupervisorReport`, including liveness normalization, pending-clear visibility, and recovery-related report fields.
- `millrace_engine/engine_runtime.py` is the authority for deciding whether a persisted runtime snapshot is live, stale, or degraded when it claims the engine is still running.
- `millrace_engine/control_actions.py` owns the supported stale-state recovery controls: active-task clear/recover and durable manual recovery requests.
- `tests/test_status.py`, `tests/test_cli.py`, and `tests/test_engine_runtime_loop.py` are the strongest behavioral proof surfaces for the claims in this document.

When these surfaces disagree, the strongest order is:

1. live liveness reconciliation from `engine_runtime.py` for `agents/.runtime/state.json`
2. status-marker validation from `status.py`
3. persisted runtime-owned files under `agents/` and `agents/.runtime/`
4. operator-facing reports built by `control_reports.py`

That ordering matters because Millrace does not blindly trust file contents that merely look plausible. A status marker in the wrong plane is invalid even if the text parses, and a runtime snapshot that claims `process_running=true` can still be stale if the PID probe says the process is gone.

## 3. Lifecycle And State Transitions

### 3.1 Persisted Runtime State Inventory

The main runtime-owned files for this boundary are:

| Path | Purpose | Primary owner |
| --- | --- | --- |
| `agents/status.md` | execution-plane marker file | `status.py` |
| `agents/research_status.md` | research-plane marker file | `status.py` |
| `agents/tasks.md` | visible active-task store | `queue.py` and control/remediation helpers |
| `agents/tasksbacklog.md` | visible queued execution backlog | `queue.py` |
| `agents/tasksbackburner.md` | quarantined, deferred, and remediation evidence store | `queue.py` and remediation helpers |
| `agents/research_state.json` | typed research runtime snapshot | research state helpers and reports |
| `agents/.runtime/state.json` | persisted execution/runtime snapshot | engine runtime-state writes and control reports |
| `agents/.runtime/pending_active_task_clear.json` | deferred clear intent waiting for a daemon boundary | `control_actions.py` and mailbox handlers |
| `agents/.runtime/last_active_task_clear.json` | last supported active-task remediation outcome | `control_actions.py` |
| `agents/.runtime/recovery/requests/<request_id>.json` | durable manual recovery request artifact | `control_actions.py` |
| `agents/.runtime/recovery/latest.json` | latest queued manual recovery request | `control_actions.py` |

Not every one of these files has the same authority level. The status files are marker contracts. The queue files are visible work-state stores. `state.json` is the runtime liveness snapshot. The pending and last active-task-clear files are bounded support records for one specific recovery seam. Recovery-request artifacts are audit evidence for explicitly authorized manual recovery, not general runtime state.

### 3.2 Status-Marker Contract

`agents/status.md` and `agents/research_status.md` are not free-form notes. Each must contain exactly one authoritative marker line such as `### IDLE` or `### GOAL_INTAKE_RUNNING`.

`status.py` enforces four things:

- file shape: one marker line, not appended history
- plane ownership: execution markers cannot appear in `research_status.md`, and research markers cannot appear in `status.md`
- legal transition graphs for both planes
- stage-terminal restrictions such as troubleshoot not being allowed to emit `NEEDS_RESEARCH`

This makes the marker files narrow contract surfaces instead of loosely interpreted status text. A wrong marker is not “stale but readable”; it is invalid state.

Execution `IDLE` is also intentionally narrower than many operators first assume. It means no execution stage is currently active. It does not mean the engine process is stopped, and it does not prove that research work, pending mailbox work, or queued execution work are absent.

### 3.3 Runtime Snapshot And Liveness Reconciliation

`agents/.runtime/state.json` persists the operator-facing `RuntimeState` model. That payload includes fields such as:

- `process_running`
- `process_id`
- `paused`
- `execution_status`
- `research_status`
- `active_task_id`
- `backlog_depth`
- `deferred_queue_size`
- `config_hash`
- `started_at`
- `updated_at`
- `mode`

That snapshot is not trusted on its own when it claims the engine is running. `engine_runtime.py` reconciles it through `reconcile_runtime_snapshot()`.

The key cases are:

- no snapshot: authority is `snapshot_absent`
- snapshot says stopped: authority is `snapshot_stopped`
- snapshot says running and PID probe succeeds: authority is `live_probe`
- snapshot says running but PID is missing: degraded snapshot
- snapshot says running but PID probe proves the process is gone: stale snapshot normalized to stopped
- snapshot says running but PID verification is inconclusive: degraded snapshot normalized away from live authority

So the saved snapshot is a published claim, not unquestioned truth. Millrace keeps it visible, but only after liveness reconciliation decides whether it is still trustworthy.

### 3.4 Stale Marker Normalization And Boundary State

The execution-plane `NEEDS_RESEARCH` marker still exists in the status vocabulary, but the shipped runtime no longer treats it as a durable start-blocking steady state for normal execution resume. `tests/test_cli.py` proves that both `start --once` and `start --daemon` normalize a stale `### NEEDS_RESEARCH` execution marker and continue promoting backlog work, ending back at `### IDLE` after the run.

That is an important contract boundary:

- `NEEDS_RESEARCH` can still be emitted as part of execution-stage handoff semantics
- a stale leftover `NEEDS_RESEARCH` marker is not the supported way to keep a workspace suspended forever
- on start, the engine normalizes that stale execution marker back into runnable execution flow

This is separate from research-plane state. `agents/research_state.json` and `agents/research_status.md` remain the durable research authority; execution-marker normalization does not erase the research-plane snapshot model.

## 4. Failure Modes And Recovery

### 4.1 Stale Runtime Snapshot

The clearest stale-state failure is a persisted `state.json` claiming `process_running=true` for a process that no longer exists. Millrace treats that as stale state, not as a reason to block startup forever.

Recovery path:

1. read the reconciled liveness view through `status --detail --json` or `supervisor report --json`
2. confirm whether the snapshot was downgraded to stopped or marked degraded
3. restart through the supported CLI/TUI start surfaces if the workspace is no longer owned

The supported recovery is not “delete `state.json` and hope.” Manual file deletion bypasses the liveness logic and should be treated as forensic last-resort territory, not an operator workflow.

### 4.2 Deferred Active-Task Clear

Active-task clear is the main supported stale-execution-state repair seam.

If the daemon is not running, `active-task clear --reason ...` applies directly and moves the active task through the supported remediation path.

If the daemon is running, clear does not mutate `agents/tasks.md` out-of-band. Instead Millrace:

- queues the clear intent through the mailbox
- writes `agents/.runtime/pending_active_task_clear.json`
- records the latest result in `agents/.runtime/last_active_task_clear.json`
- applies the clear only after a daemon boundary, as proved by `tests/test_engine_runtime_loop.py`

That deferred state is part of the official contract and is surfaced in status and supervisor reports so operators can tell the difference between “clear not requested” and “clear requested but not yet drained.”

### 4.3 Active-Task Recover And Manual Recovery Requests

`active-task recover` is stricter than clear. When the daemon is running, recover is blocked rather than deferred. That is intentional: Millrace does not treat active-task recovery as a mailbox-safe live mutation.

For higher-privilege out-of-order recovery, the supported path is `recovery request <target> --issuer <name> --reason <text> --force-queue`. The current targets are `troubleshoot` and `mechanic`.

Those requests produce durable audit artifacts:

- one request file under `agents/.runtime/recovery/requests/`
- one latest pointer at `agents/.runtime/recovery/latest.json`
- direct or mailbox-mode evidence depending on whether a daemon is running

This path is for explicit manual recovery authorization. It is not a shortcut around normal control commands, and it is much more truthful than editing marker files or runtime JSON by hand.

### 4.4 Explicit Non-Goals

This boundary intentionally does not promise that every broken workspace can be repaired by rewriting files. The supported controls are:

- status observation through CLI, TUI, and supervisor reports
- active-task clear
- active-task recover when allowed
- supervisor-safe variants of those controls
- explicit manual recovery requests with durable audit artifacts

Manual file surgery should be treated as unsupported operational behavior except for local forensic inspection or narrowly guided development debugging. If the supported controls are insufficient, that gap belongs in the failure-playbook or future control-surface work, not in a silent operator convention to edit runtime-owned files directly.

## 5. Operator And Control Surfaces

The operator-facing truth surfaces for this boundary are:

- `millrace status --detail --json`
- `millrace supervisor report --json`
- `millrace active-task clear ...`
- `millrace active-task recover ...`
- `millrace supervisor active-task clear ...`
- `millrace supervisor active-task recover ...`
- `millrace recovery request troubleshoot|mechanic --issuer <name> --reason ... --force-queue --json`
- the TUI surfaces that render the same runtime status and route into the same control API

`StatusReport` and `SupervisorReport` expose the important stale-state visibility fields directly:

- reconciled liveness summary
- `pending_active_task_clear`
- `last_active_task_clear`
- backlog depth and deferred queue size
- mailbox-buffered intake visibility when queued add-task work has not drained yet
- supervisor attention summaries such as degraded state or idle with pending work

The practical rule is to observe state through those surfaces first, then mutate through supported commands. Do not treat `agents/status.md`, `agents/research_status.md`, or `.runtime/` JSON files as operator-editable controls.

## 6. Proof Surface

The strongest proof for this boundary is:

- `tests/test_status.py`
  - one-marker file shape
  - plane ownership enforcement
  - legal transition validation
- `tests/test_cli.py`
  - stale `NEEDS_RESEARCH` normalization during start
  - direct active-task clear behavior
  - daemon-running clear deferral
  - pending-clear visibility in `status --detail --json` and `supervisor report --json`
  - direct and mailbox manual recovery-request persistence
- `tests/test_engine_runtime_loop.py`
  - deferred active-task clear applies only after a daemon boundary
- `tests/test_package_parity.py`
  - public and packaged copies of this doc, the IA, and the portal stay synchronized
- `tests/test_baseline_assets.py`
  - the packaged bundle includes this runtime deep doc and the required marker language
- `millrace_engine/assets/manifest.json`
  - the packaged path, hash, and size for the shipped doc files stay truthful

Drift should fail proof when:

- a status-marker claim stops matching the legal transitions in `status.py`
- stale-snapshot wording stops matching `reconcile_runtime_snapshot()`
- the doc implies file surgery is a supported operator recovery path
- IA or portal links point at the wrong filename

## 7. Change Guidance

Update this doc when changes affect:

- status-marker vocabulary ownership or legal transitions
- the set of runtime-owned state files for execution status and stale-state recovery
- liveness reconciliation for `agents/.runtime/state.json`
- the pending or last active-task-clear report contract
- the durable manual recovery-request artifact contract

Do not expand this doc to absorb:

- daemon start/stop sequencing and watcher ownership
- mailbox command-family semantics in general
- stage pipeline routing
- research recovery-latch internals

If a future change primarily alters daemon lifecycle authority, route it to `runtime-loop-lifecycle-and-supervisor-authority.md`. If it primarily alters deferred command behavior, route it to `control-plane-command-surface-and-mailbox-semantics.md`. Keep this document focused on persisted runtime state, status markers, stale-state interpretation, and supported stale-state recovery controls.
