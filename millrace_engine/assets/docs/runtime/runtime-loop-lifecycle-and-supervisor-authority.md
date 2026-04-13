# Runtime Loop Lifecycle And Supervisor Authority

## 1. Purpose And Scope

This document owns the runtime boundary that decides who is allowed to start, keep, pause, resume, and stop the Millrace engine for one workspace.

It explains how the runtime loop behaves in `once` and `daemon` modes, where startup exclusivity is enforced, how the persisted runtime snapshot should be interpreted, and which modules own degraded liveness decisions when the saved snapshot and live process state disagree.

It does not own command semantics for the control plane, mailbox payload shapes, queue mutation policy, or detailed stale-state recovery for execution and research artifacts. Those boundaries belong to the control-plane, state/recovery, and failure-playbook deep docs.

## 2. Source-Of-Truth Surfaces

The authoritative behavior for this boundary lives in these modules:

- `millrace_engine/engine.py`: composition shell for the runtime engine. It wires config coordination, mailbox processing, the runtime loop, execution and research planes, and persisted runtime-state writes.
- `millrace_engine/engine_runtime_loop.py`: lifecycle sequencer for `once` and `daemon` execution. It owns watcher startup and restart, wakeups, mailbox polling during sleeps, cycle execution, and shutdown sequencing.
- `millrace_engine/engine_runtime.py`: liveness authority for saved runtime snapshots. It decides whether a persisted snapshot is live, stale, or degraded, and it formats start-collision errors for control surfaces.
- `millrace_engine/engine_mailbox_processor.py`: mailbox processing happens inside the running engine and can request pause, resume, stop, config reload, and deferred boundary actions, but it does not own overall runtime lifecycle sequencing.
- `millrace_engine/control.py` and the control reports/models it exposes: these are the operator-facing readers and mutators over the same lifecycle state.

The strongest authority order for this boundary is:

1. live PID verification from `engine_runtime.py` when a snapshot claims the engine is running
2. the current in-process `MillraceEngine` state while the engine owns the workspace
3. the persisted runtime snapshot in `agents/.runtime/state.json`
4. operator-facing summaries built from that snapshot

When these surfaces differ, live-process verification wins over the stored snapshot. A running-looking snapshot without a verifiable PID is treated as degraded rather than trusted.

## 3. Lifecycle And State Transitions

### 3.1 Startup Exclusivity

Millrace is intentionally one-runtime-per-workspace. `ensure_workspace_start_available()` in `engine_runtime.py` reads the persisted runtime snapshot before launch and refuses a new start if the existing snapshot still represents a running engine after liveness reconciliation.

That check applies before both `start --once` and `start --daemon`. The start collision message is explicit about the owning mode, the persisted `started_at` value, and the state-file path so the operator can stop the active engine instead of racing it.

This means startup exclusivity is not a best-effort convention. It is a runtime-owned contract enforced before a second engine instance is allowed to take ownership of the same workspace.

### 3.2 Shared Startup Sequence

`MillraceEngine.start()` constructs one engine instance and then hands off to `EngineRuntimeLoop.run(mode=...)`.

At runtime-loop entry, the engine:

- records `started_at`
- clears pause state and stop-request flags
- creates the async input queue
- builds the file watcher from the current config
- emits `ENGINE_STARTED`
- consumes any research recovery latch on startup
- performs startup research synchronization before the normal execution loop
- writes the live runtime snapshot with `process_running=True`, the current PID, and the chosen mode

That write is the point where the process claims workspace ownership. Until then, the engine may have been constructed, but it has not yet published itself as the active runtime for that workspace.

### 3.3 Once Mode

`once` mode is a single lifecycle pass. The runtime loop still performs startup bookkeeping, liveness-state publication, and startup research synchronization, but it does not become a long-lived watcher owner.

Important `once` behavior:

- the watcher is built but only started if the runtime is in daemon mode with watch-backed input handling
- deferred config can still apply at cycle boundaries
- if startup research synchronization already performed meaningful work, the execution cycle may be skipped entirely
- the mode exits after one execution-cycle opportunity, then writes a stopped snapshot and shuts the research plane down

The tests in `test_engine_runtime_loop_once_skips_execution_cycle_after_startup_research_sync` codify one of the key boundaries here: startup research ownership can satisfy the work of a `once` pass before execution gets a turn.

### 3.4 Daemon Mode

`daemon` mode owns the long-lived event loop. When the watcher mode is `watch`, the file watcher starts immediately and pushes runtime input events into the queue. When the watcher mode is `poll`, the runtime loop stays responsible for calling `poll_once()` during wakeups and sleep windows.

Daemon ownership includes:

- draining queued runtime input events
- ingesting poll fallback events when watch mode is unavailable
- processing mailbox commands during wakeups and while sleeping between cycles
- restarting the watcher after config changes that affect watcher behavior
- repeating execution cycles until stop conditions are met

`engine_runtime_loop.py` is therefore the true owner of daemon liveness, not the watcher adapter and not the mailbox processor. Those are dependencies under the loop’s control.

### 3.5 Pause, Resume, And Stop Authority

The engine stores `paused`, `pause_reason`, `pause_run_id`, and `stop_requested` on the in-process `MillraceEngine`. Mailbox handlers and control surfaces can mutate those fields through explicit hooks, but the runtime loop decides when those flags take effect.

This matters because a command can request lifecycle change without directly killing the loop. Examples:

- `stop_requested=True` causes the daemon loop to exit at safe loop boundaries
- pause state causes the runtime loop to hold work without pretending the engine has stopped
- deferred actions, such as active-task clear, are applied only at the correct lifecycle boundary rather than immediately inside an active stage

The persisted runtime snapshot reflects those state fields so operators can distinguish stopped, paused, and running ownership.

## 4. Failure Modes And Recovery

### 4.1 Stale Running Snapshot

The most important failure mode for this boundary is a persisted snapshot that claims the engine is running when the process is gone.

`reconcile_runtime_snapshot()` handles that by probing the saved PID:

- if the PID is live, the running snapshot stands
- if the PID is missing, the snapshot is downgraded to a degraded snapshot
- if the PID is dead, the snapshot is rewritten logically as stopped for control-surface purposes
- if the probe itself is inconclusive, the snapshot is treated as degraded instead of authoritative

This keeps Millrace from blocking startup forever on stale state while still surfacing that the saved snapshot was untrustworthy.

### 4.2 Running Snapshot With No PID

A snapshot that says `process_running=True` but has no PID is considered degraded. Operators should read that as “the runtime claimed ownership, but live verification is unavailable.” That is not the same as a clean stopped state, and it is not strong enough to justify trusting the snapshot over live process observation.

### 4.3 Ownership Drift Between Loop And Side Inputs

Watcher events, mailbox commands, research sync, and execution cycles can all request state changes, but they do not replace the runtime loop as lifecycle owner. If those subsystems disagree, the runtime loop remains the final sequencing authority because it is the component that:

- decides when events are drained
- applies config at boundaries
- chooses when a stop request is honored
- writes the running and stopped snapshots

Recovery for lifecycle drift therefore starts by reading the persisted runtime snapshot and the control-surface liveness summary, then confirming whether the process is actually alive. It does not start by inferring state from queue files or mailbox remnants alone.

### 4.4 Explicit Non-Goals

This boundary does not define how every failure in a stage or queue is repaired. It only defines how lifecycle ownership reacts to those failures:

- execution-cycle exceptions emit failure evidence, arm rollback as needed, and request runtime stop
- startup collision remains a hard no-start outcome
- completion markers can request stop, but only when the runtime loop validates the decision artifact

## 5. Operator And Control Surfaces

The operator-visible lifecycle surfaces for this boundary are:

- `millrace start --once` and `millrace start --daemon`: request ownership of the workspace
- `millrace status --detail --json` and related control reports: expose the persisted runtime snapshot plus liveness reconciliation
- mailbox-safe daemon commands: request pause, resume, stop, or bounded lifecycle-adjacent actions from outside the running process
- TUI lifecycle controls: route into the same control-plane contracts rather than bypassing them
- supervisor-facing observation through `supervisor report --json`: reads the same one-workspace runtime state instead of owning a parallel runtime

The critical operator implication is that “execution `IDLE`” is not the same thing as “the engine is stopped.” The engine may still be running in daemon mode while the execution plane is idle, paused, or waiting on research or input events.

Similarly, a degraded runtime snapshot is an operator signal, not a silent fallback. It tells the operator that the runtime state file existed but was not strong enough to stand alone as proof of a live owner.

## 6. Proof Surface

The most direct proof for this boundary comes from:

- `tests/test_engine_runtime_loop.py`
  - watcher restart and reload behavior
  - once-mode short-circuit after startup research sync
  - daemon-mode progression and deferred-boundary behavior
- `tests/test_engine_config_coordinator.py`
  - config-apply boundary timing and watcher restart expectations
- `tests/test_engine_mailbox_processor.py`
  - mailbox lifecycle commands and boundary-safe mutation behavior
- `tests/test_package_parity.py`
  - public and packaged runtime deep-doc parity for this new file
- `tests/test_baseline_assets.py`
  - required packaged runtime-doc presence and marker checks for this boundary doc
- `millrace_engine/assets/manifest.json`
  - packaged bundle truth for the shipped deep-doc path, SHA, and size

Drift should fail proof when:

- the public and packaged docs stop matching
- the packaged manifest stops describing the shipped file accurately
- the doc stops naming the actual lifecycle owners or contradicts tested once/daemon behavior
- the IA and portal surfaces stop pointing at the real deep-doc filename

## 7. Change Guidance

Update this doc when changes affect:

- startup exclusivity
- runtime snapshot ownership or liveness reconciliation
- once-versus-daemon sequencing
- watcher ownership under the runtime loop
- pause, resume, and stop semantics at the engine lifecycle level

Do not expand this doc to absorb:

- mailbox command payload and command-family semantics
- deep queue/state-marker recovery details
- stage execution and inter-plane handoff rules
- general operator unwedge catalogs beyond lifecycle ownership

If a future change primarily alters command semantics, state recovery, or operator playbooks, add or update the sibling deep doc that owns that boundary and leave this document focused on lifecycle and supervisor authority.
