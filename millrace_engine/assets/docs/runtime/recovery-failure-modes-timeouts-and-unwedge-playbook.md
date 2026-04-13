# Recovery Failure Modes, Timeouts, And Unwedge Playbook

## 1. Purpose And Scope

This document owns the runtime boundary for failure-mode signatures, timeout evidence, bounded recovery behavior, and the supported operator unwedge playbook for one Millrace workspace.

It explains how the daemon responds to `NET_WAIT`, usage-budget pauses, stale or degraded runtime state, deferred clear requests, manual recovery requests, research-thaw seams, and Sentinel monitoring or cap states. It also explains what evidence those cases leave behind and which command-driven interventions are actually supported.

It does not own the normal lifecycle contract, the full report-shaping contract, or the runner adapter configuration surface. Those remain with the lifecycle, observability, and runner boundary docs.

## 2. Source-Of-Truth Surfaces

The authoritative surfaces for this boundary are:

- `millrace_engine/engine_runtime_loop.py`, which owns once/daemon sequencing, mailbox draining, inter-task waiting, pending-config application, and when deferred active-task clear requests actually apply.
- `millrace_engine/engine_outage_recovery.py`, which owns daemon-only `NET_WAIT` recovery after an execution cycle ends in `NET_WAIT`.
- `millrace_engine/policies/outage.py` and the outage config in `millrace_engine/config.py`, which define the route vocabulary, probe timing, and route-selection rules for outage recovery.
- `millrace_engine/control_actions.py`, which owns supported operator recovery actions such as active-task clear/recover, manual `recovery request`, and Sentinel incident generation.
- `millrace_engine/sentinel_runtime.py`, which owns one-workspace Sentinel monitoring, stale-progress detection, soft-cap suppression, hard-cap escalation, and acknowledgment state.
- `millrace_engine/control_runtime_surface.py` and `millrace_engine/control_reports.py`, which surface degraded liveness, pending clear, mailbox task intake, and supervisor attention states to operators.
- `millrace_engine/runner.py`, but only for the timeout signature that operators observe: timed-out runners emit `RUNNER_TIMEOUT after <seconds>s` and return exit code `124`.
- `tests/test_engine_runtime_loop.py`, `tests/test_cli.py`, `tests/test_status.py`, and `tests/test_sentinel_runtime.py`, which are the strongest proof surfaces for the behaviors documented here.

When these layers disagree, the runtime code and tests win over docs. In particular, supported recovery means “the code exposes and tests this path,” not “an operator can probably repair it by editing files.”

## 3. Lifecycle And State Transitions

### 3.1 Failure Taxonomy And Observable Signatures

The main shipped failure and recovery signatures are:

| Failure or recovery state | Where it shows up | Supported meaning |
| --- | --- | --- |
| `degraded_snapshot` | `status`, `supervisor report` | persisted runtime snapshot could not be trusted as live |
| `usage_budget_threshold` pause | runtime state, events, run provenance | daemon paused itself because usage policy blocked more execution |
| `NET_WAIT` | execution status, transition history, outage policy evidence | transport or preflight wait condition; daemon may probe and resume or route |
| pending active-task clear | runtime state and supervisor/status detail | clear was requested, but daemon-time application is deferred until a boundary |
| mailbox recovery request | operation result plus mailbox archive and recovery artifact | recovery intent accepted, but daemon still owns live application timing |
| research recovery latch | latch file, watchdog report/state, backlog-repopulated event | research-side remediation froze or regenerated work and later thawed it |
| Sentinel `degraded` | sentinel report/status | no meaningful progress or limited observation without cap suppression |
| Sentinel `monitoring` | sentinel report/status | a linked recovery cycle exists and repeat auto-routing is suppressed while it is unresolved |
| Sentinel `suppressed` | sentinel report/status | soft cap reached; acknowledgment is required before auto-queue rearms |
| Sentinel `escalated` | sentinel report/status | hard cap or intentional escalation path reached |

These are intentionally different classes. A degraded runtime snapshot is not the same as a queued recovery request, and Sentinel suppression is not the same as an execution-plane `BLOCKED` marker.

### 3.2 Deferred Versus Immediate Recovery

Millrace keeps one live owner for runtime state when the daemon is running.

That changes recovery timing:

- `active-task clear` is mailbox-deferred while the daemon is running and applies only after a daemon boundary.
- `active-task recover` is blocked while the daemon is running; it is not mailbox-safe.
- `recovery request <target> --issuer <name> --reason ... --force-queue` always writes durable audit artifacts and becomes mailbox-backed when the daemon is running.
- Sentinel incident generation is direct when the daemon is stopped and mailbox-backed when the daemon is running.

`engine_runtime_loop.py` also keeps daemon sleeps interruptible. `sleep_with_mailbox_activity()` drains mailbox and watcher activity during wait periods so stop requests and queued commands do not starve behind a long delay.

### 3.3 `NET_WAIT` Outage Recovery

`EngineOutageRecovery.handle_net_wait_recovery()` is only used when all of the following are true:

- mode is `daemon`
- the execution cycle finished in `NET_WAIT`
- the run has a transition-history file
- outage policy is enabled

From there, Millrace reconstructs the triggering `NET_WAIT` policy record, freezes an outage policy snapshot, and probes until one of three bounded outcomes occurs:

- `RESUME`: transport looks healthy again, execution status returns to `IDLE`, and the daemon continues
- `ROUTE_TO_BLOCKER`: the task is moved into blocker state, execution pauses, and pause reason becomes `net_wait_route_to_blocker`
- `ROUTE_TO_INCIDENT`: the task is quarantined into the incident/research path and execution returns to `IDLE`

The effective route vocabulary is exact: `pause_resume`, `incident`, or `blocker`, with `route_to_incident` and `route_to_blocker` booleans overriding the base policy when configured.

### 3.4 Usage-Budget Pauses

Usage-budget policy is a different boundary from outage recovery. When it fires, the daemon:

- sets `paused = true`
- records `pause_reason = "usage_budget_threshold"`
- records `pause_run_id`
- emits `engine.paused`
- preserves the policy evidence in run provenance as `policy_blocked`

This is an intentional self-pause, not a crash and not a stale-state artifact.

### 3.5 Sentinel Monitoring, Caps, And Acknowledgment

Sentinel is a one-workspace local monitor built on persisted supervisor observations.

Important current states are:

- `healthy`: idle can still be healthy when no stall is observed
- `degraded`: progress appears stale or supervisor observation is limited
- `monitoring`: one linked recovery request and incident are active, so repeat routing stays suppressed until that cycle resolves or escalates
- `suppressed`: soft cap reached; acknowledgment is required before auto-queue can rearm
- `escalated`: hard cap or configured escalation path reached; notification and optional halt guardrails are recorded as evidence

Acknowledgment is deliberately narrow. `sentinel acknowledge` is only valid when a pending cap or escalation acknowledgment exists; it is not a generic “clear any degraded state” switch.

## 4. Failure Modes And Recovery

### 4.1 Timeout Signature And First Response

Runner timeout is the canonical timeout signature in the shipped runtime:

- stderr gets `RUNNER_TIMEOUT after <seconds>s`
- runner exit code becomes `124`
- stdout/stderr and runner notes still land in the run directory

This doc does not re-own runner configuration, but it does own the unwedge implication: timeout evidence lives in run artifacts first. The supported first response is to inspect the run directory, `run-provenance`, and status or supervisor surfaces before choosing a recovery action.

### 4.2 Stale Or Degraded Runtime State

When the persisted runtime snapshot cannot be verified as live, Millrace surfaces `degraded_snapshot` instead of blindly trusting `process_running=true`.

Supported response:

1. inspect `status --detail --json` or `supervisor report --json`
2. confirm whether the runtime is actually stopped or merely degraded
3. use supported lifecycle commands such as `stop`, `start --once`, or `start --daemon` rather than editing `.runtime/state.json`

Unsupported response:

- rewriting `agents/.runtime/state.json`
- rewriting status markers directly to “unstick” the daemon

### 4.3 Deferred Active-Task Clear

If the daemon is running and an operator requests clear, the current state can intentionally remain unchanged until a boundary. That is not a stuck command; it is the contract.

Supported response:

- observe `pending_active_task_clear` in status or supervisor output
- wait for a daemon boundary or stop the daemon first
- use `active-task recover` only when the daemon is stopped

Unsupported response:

- deleting `pending_active_task_clear.json`
- manually moving cards between `tasks.md` and backlog files during daemon ownership

### 4.4 `NET_WAIT` And Outage Routing

`NET_WAIT` can resolve without operator action when the configured outage route is `pause_resume` and the probe later passes. It can also intentionally end in blocker or incident routing.

Supported response:

- inspect run provenance and diagnostics for outage policy evidence
- use blocker or incident routing results as the next truth surface, not the original stage marker
- restart or resume only through supported commands if the daemon is paused

Do not treat every `NET_WAIT` as a reason to delete queue files or force-edit blocker stores.

### 4.5 Research Freeze And Backlog Thaw

The research recovery latch is another bounded unwedge seam. When backlog work is regenerated, later backlog-visible work can thaw frozen cards and clear the latch, with watchdog state returning to `not_active`.

Supported response:

- let the runtime thaw through the documented add-task or backlog-visible path
- inspect watchdog state and `handoff.backlog_repopulated` events

Unsupported response:

- manual latch-file deletion as a normal workflow

### 4.6 Sentinel Soft Cap And Hard Cap

When Sentinel detects repeated unresolved recovery cycles:

- soft cap moves the workspace into `suppressed`
- hard cap moves it into `escalated`
- acknowledgment clears the cap state only when the cap or escalation acknowledgment requirement is actually present

The soft-cap report reason is `soft-cap-reached-acknowledgment-required-before-rearming-auto-queue`. Hard-cap state records notification attempts and any configured halt guardrail result as evidence instead of silently resetting the cycle.

Supported response:

- `sentinel check`, `sentinel status`, or `sentinel watch`
- `sentinel acknowledge --issuer <name> --reason "..."`
- `sentinel incident ...`
- `recovery request troubleshoot|mechanic --issuer <name> --reason "..." --force-queue`

This repo does not ship a hosted multi-workspace Sentinel service. The recovery loop is intentionally local and file-backed.

### 4.7 Explicit Anti-Patterns

The following are unsupported normal-operation unwedge tactics:

- editing `agents/status.md` or `agents/research_status.md` by hand
- editing `agents/tasks.md`, `tasksbacklog.md`, `tasksblocker.md`, or `tasksbackburner.md` while the daemon owns the queue
- writing mailbox envelopes into `agents/.runtime/commands/incoming/`
- deleting `pending_active_task_clear.json`, recovery artifacts, or Sentinel state to force a different outcome
- treating the TUI as permission to bypass the control layer

Those actions may destroy evidence or create a state the runtime does not know how to reconcile.

## 5. Operator And Control Surfaces

The supported operator unwedge surfaces are:

- `millrace status --detail --json`
- `millrace supervisor report --json`
- `millrace stop`, `millrace pause`, `millrace resume`, `millrace start --once`, `millrace start --daemon`
- `millrace active-task clear --reason "..."`
- `millrace active-task recover --reason "..."`
- supervisor-attributed variants of those controls
- `millrace recovery request troubleshoot --issuer <name> --reason "..." --force-queue --json`
- `millrace recovery request mechanic --issuer <name> --reason "..." --force-queue --json`
- `millrace sentinel check|status|watch --json`
- `millrace sentinel acknowledge --issuer <name> --reason "..." --json`
- `millrace sentinel incident --failure-signature <token> --summary "..." --json`
- `python3 -m millrace_engine.tui --config millrace.toml` for local observation and supported control actions

A practical unwedge sequence is:

1. observe current truth through status or supervisor reports
2. decide whether the state is deferred, paused, degraded, blocked, or escalated
3. prefer direct supported controls over manual file edits
4. escalate to a recovery request or Sentinel acknowledgment only when the bounded local controls say that is the current path

## 6. Proof Surface

The strongest proof for this boundary is:

- `tests/test_engine_runtime_loop.py`
  - watcher restart and config-reload restart behavior
  - once-mode skip after startup research sync
  - deferred active-task clear applies only after a cycle boundary
- `tests/test_cli.py`
  - manual recovery request direct and mailbox behavior
  - degraded liveness in status and supervisor reports
  - mailbox task-intake and pending-clear visibility
  - usage-budget pause surfaces in state, events, and run provenance
  - daemon `NET_WAIT` resume, blocker route, and incident route behavior
- `tests/test_status.py`
  - authoritative status-marker legality and wrong-plane rejection
- `tests/test_sentinel_runtime.py`
  - idle healthy versus degraded stale state
  - monitoring, suppression, escalation, acknowledgment, and notification behavior
- `tests/test_package_parity.py`
  - public and packaged copies of this doc, the IA, and the portal remain synchronized
- `tests/test_baseline_assets.py`
  - the bundled runtime docs include this new path and required recovery markers
- `millrace_engine/assets/manifest.json`
  - the packaged path, SHA, and size stay truthful for the shipped deep-doc copy

Drift should fail proof when:

- the doc implies blocked or deferred controls apply immediately
- the doc claims manual file surgery is a supported recovery path
- the IA still points at the stale Run 09 alias filename
- the public and packaged copies diverge

## 7. Change Guidance

Update this doc when changes affect:

- outage-route selection, probe timing, or `NET_WAIT` recovery behavior
- supported active-task remediation behavior
- manual recovery-request targets or artifact semantics
- Sentinel monitoring, soft-cap, hard-cap, or acknowledgment behavior
- the operator-visible timeout signature or evidence path
- the recommended command-driven unwedge workflow

Do not expand this doc to absorb:

- full runner configuration semantics
- lifecycle authority in general
- report-shaping details that belong to the observability doc
- generic queue or mailbox mutation semantics outside recovery use

If a future change primarily alters how state is observed, route it to `observability-reports-tui-and-audit-truth-surfaces.md`. If it primarily alters status markers and stale runtime-state interpretation, route it to `runtime-state-status-markers-and-stale-recovery-semantics.md`. Keep this document focused on the supported recovery and unwedge playbook the current runtime actually exposes.
