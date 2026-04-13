# Control-Plane Command Surface And Mailbox Semantics

## 1. Purpose And Scope

This document owns the runtime boundary that explains how Millrace accepts control-plane commands, decides whether they apply immediately or defer through the daemon mailbox, and reports that pending work back to operators and supervisors.

It covers the stable command surface exposed by `millrace_engine/cli.py` and `millrace_engine/control.py`, the daemon-state split in `millrace_engine/control_actions.py`, and the daemon-side application boundary in `millrace_engine/engine_mailbox_command_handlers.py`.

It does not own engine start/stop liveness authority, stage execution internals, or persisted state recovery as separate topics. Those remain with the lifecycle, pipeline, and state/recovery boundary docs.

## 2. Source-Of-Truth Surfaces

The command tree in `millrace_engine/cli.py` is the public entrypoint, but `EngineControl` in `millrace_engine/control.py` is the stable control API. CLI commands stay truthful only when they preserve the behavior of the `EngineControl` methods they call.

`millrace_engine/control_actions.py` is the contract owner for mutation semantics. That file decides whether a request:

- applies directly because no daemon is running
- writes a mailbox envelope because the daemon must own the live mutation
- returns a blocked or no-op result instead of mutating state unsafely

`millrace_engine/engine_mailbox_command_handlers.py` is the daemon-side application layer. It registers the mailbox-safe command family and applies those envelopes through bounded handlers such as `_handle_stop`, `_handle_pause`, `_handle_resume`, `_handle_add_task`, `_handle_queue_reorder`, `_handle_queue_cleanup_remove`, `_handle_queue_cleanup_quarantine`, `_handle_recovery_request`, and `_handle_active_task_clear`.

`millrace_engine/control_runtime_surface.py` owns the read-side truth for deferred visibility. Status and supervisor reports surface mailbox-buffered task intake, pending active-task clear intent, deferred queue size, and the attention summary operators see while waiting for the daemon to drain queued commands.

When these surfaces differ, the runtime behavior in `control_actions.py` and the daemon-side handlers win. Docs and CLI examples must match that behavior, not smooth it into a simpler but false model.

## 3. Lifecycle And State Transitions

The key lifecycle decision is daemon state.

When no daemon is running, control mutations may apply directly. `add_task` appends a backlog card immediately, `add_idea` copies the file into the raw queue immediately, queue reorder and cleanup mutate the queue directly, and recovery requests persist their audit record immediately.

When the daemon is running, Millrace preserves one live mutator for runtime-owned state. In that state, daemon-safe commands queue mailbox envelopes rather than editing queue and runtime files out-of-band. The mailbox path is explicit in `control_actions.py`: `add_task`, `supervisor_add_task`, `add_idea`, queue reorder, supervisor cleanup variants, recovery requests, sentinel incidents, compounding lifecycle changes, config reload/set, and lifecycle commands (`stop`, `pause`, `resume`) all return `mode="mailbox"` with a queued command id when the daemon is active.

Queue cleanup is intentionally split by command family. Non-supervisor `queue_cleanup_remove` and `queue_cleanup_quarantine` are direct-only when the daemon is stopped and are blocked while daemon-running with errors such as "queue cleanup remove requires the daemon to be stopped." Supervisor cleanup variants (`supervisor_queue_cleanup_remove` and `supervisor_queue_cleanup_quarantine`) are mailbox-deferred when the daemon is running and direct when it is stopped.

Not every mutation is merely delayed. The active-task remediation seam is intentionally asymmetric:

- `active_task clear` while the daemon is running is deferred until a daemon boundary. Millrace writes a mailbox command and also persists `agents/.runtime/pending_active_task_clear.json` so the request remains visible before the daemon applies it.
- repeated clear requests while one is already pending return a mailbox-mode idempotent no-op rather than queueing duplicates
- `active_task recover` while the daemon is running is blocked, not deferred, because recovery is not modeled as a mailbox-safe live mutation
- lifecycle actions when the daemon is already stopped return direct no-op results such as `"engine is not running"` rather than manufacturing a mailbox path

The daemon-side state transition occurs when the engine mailbox processor drains incoming commands and dispatches them through `EngineMailboxCommandRegistry`. Successful command handling moves mailbox work from incoming intent into applied runtime effects; failed command handling is still visible through the command archive family rather than being silently dropped.

## 4. Failure Modes And Recovery

The most important failure mode here is assuming every control mutation behaves the same way. Millrace intentionally separates three outcomes:

- direct application
- mailbox-deferred application
- blocked or idempotent non-application

If an operator expects immediate queue visibility while the daemon is running, `add_task` can look misleading until they inspect the mailbox-aware read surfaces. The task may be accepted with `mode="mailbox"` but not yet appear in backlog because the daemon has not drained the pending command. That is expected behavior, not silent loss.

Active-task remediation has stricter guardrails. A clear request can be deferred, but only one pending clear is kept visible at a time; repeated requests collapse into an idempotent result. Recover requests while the daemon is active are rejected as blocked because Millrace does not permit arbitrary active-task recovery outside the daemon-safe boundary.

Malformed or unsupported mailbox commands are another bounded failure class. `EngineMailboxCommandRegistry` raises `ControlError` for unsupported command kinds, and the daemon-side mailbox archive surfaces preserve whether a command was processed or failed. Recovery here is to inspect the control result, mailbox archives, and status/supervisor reports rather than editing runtime-owned mailbox files by hand.

This doc also does not promise that all deferred work becomes visible in the same place. Some command families primarily report through operation results, some through queue changes after daemon drain, and some through status/supervisor visibility surfaces such as pending clear and mailbox task intake. That split is part of the contract.

## 5. Operator And Control Surfaces

The operator-facing command surface starts in `millrace ...`, but the safety boundary is the same across CLI, TUI, and supervisor usage because those surfaces converge on `EngineControl`.

Important control classes in the current runtime:

- lifecycle commands: `stop`, `pause`, and `resume` queue mailbox work only when a daemon is running; otherwise they return a direct no-op state
- queue intake commands: `add-task` and `add-idea` apply directly in non-daemon mode and queue mailbox intake when the daemon is active
- queue mutation commands: reorder is direct when safe and mailbox-backed when the daemon owns the live queue; non-supervisor cleanup is direct-only when the daemon is stopped and blocked while daemon-running, while supervisor cleanup variants are mailbox-deferred when daemon-running
- supervisor variants: the same mutation classes, but with validated issuer attribution preserved in payloads and mailbox envelopes
- active-task remediation: clear may defer across the daemon boundary, recover may be blocked, and both preserve operator-readable reason fields
- recovery and incident escalation: recovery requests and sentinel incidents preserve durable audit metadata even when they must be queued

The read-side control surfaces matter just as much as the mutation surfaces. `status --detail --json` and `supervisor report --json` expose mailbox-backed visibility such as `mailbox_task_intake`, `pending_active_task_clear`, deferred queue size, and attention summaries like "mailbox-buffered add-task request accepted but not yet visible in backlog." Those reports are the truthful operator answer while queued mailbox work is still pending.

The practical rule is simple: use CLI or TUI controls, but treat mailbox files and runtime-owned command directories as engine-owned implementation state. Operators and external supervisors should not write `.runtime/commands/incoming/` themselves.

## 6. Proof Surface

`tests/test_cli.py` is the main behavioral proof surface for this boundary. It covers direct versus mailbox results, queued command semantics while the daemon is running, lifecycle command behavior, and the visibility of pending clear plus mailbox-buffered task intake in status and supervisor reports.

`tests/test_package_parity.py` proves this public deep doc and its packaged mirror stay byte-identical, and `tests/test_baseline_assets.py` proves the packaged baseline bundle continues to include the runtime deep-doc path plus the expected marker language for this boundary.

The packaged manifest in `millrace_engine/assets/manifest.json` is also part of the proof surface. If this doc path, the IA catalog, or the stable portal mirror drift from the packaged copy, parity and manifest checks should fail.

Useful smoke evidence for this boundary is narrower than a full runtime suite:

- targeted parity test for packaged docs existence and synchronization
- targeted baseline-assets test for required bundled runtime docs
- explicit parity and manifest hash checks for `docs/runtime/control-plane-command-surface-and-mailbox-semantics.md`, `docs/runtime/README.md`, and `docs/RUNTIME_DEEP_DIVE.md`

## 7. Change Guidance

Future updates to command behavior should land here when they change the control mutation contract itself: new mailbox-safe command families, new blocked-versus-deferred rules, new supervisor attribution semantics, or new read-side visibility for pending control intent.

Do not expand this doc into lifecycle ownership, queue persistence internals, or generic failure recovery. If a change primarily alters daemon liveness authority, put it in `runtime-loop-lifecycle-and-supervisor-authority.md`. If it primarily alters persisted markers, runtime snapshots, or stale-state repair, route it to the state/status/recovery boundary doc.

When adding a new command family to this boundary:

1. update the public deep doc and packaged mirror together
2. keep `docs/runtime/README.md` and `docs/RUNTIME_DEEP_DIVE.md` navigation truthful
3. add manifest and doc-proof coverage for any new packaged path
4. cite the behavior owner in `control_actions.py`, `control.py`, `control_runtime_surface.py`, or `engine_mailbox_command_handlers.py` instead of describing hypothetical control semantics
