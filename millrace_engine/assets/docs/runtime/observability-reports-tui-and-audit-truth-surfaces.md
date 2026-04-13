# Observability, Reports, TUI, And Audit Truth Surfaces

## 1. Purpose And Scope

This document owns the runtime boundary for operator-visible truth surfaces across status reports, supervisor reports, research and audit reports, run provenance, CLI rendering, and the local TUI shell.

It explains where those surfaces get their data, which layer is authoritative when two views disagree, how deferred or degraded state is surfaced without being hidden, and how operators should interpret the compact versus detailed visibility surfaces Millrace ships today.

It does not own daemon lifecycle sequencing, command mutation rules, stage routing, or the operator recovery playbook. Those remain with the lifecycle, control-plane, pipeline, state/recovery, and failure-mode deep docs.

## 2. Source-Of-Truth Surfaces

The authoritative surfaces for this boundary are:

- `millrace_engine/control_models.py`, which defines the typed report contracts for `RuntimeState`, `RuntimeLivenessView`, `StatusReport`, `SupervisorReport`, `ResearchReport`, `CompletionStateView`, `PolicyHookSummary`, and `RunProvenanceReport`.
- `millrace_engine/control_reports.py`, which reads persisted runtime, audit, and run artifacts and builds the report-side helper views that those contracts depend on.
- `millrace_engine/control_runtime_surface.py`, which decides which reports are exposed on the read side, including the difference between lightweight `status()` visibility and detailed status or supervisor views.
- `millrace_engine/cli_rendering.py`, which deterministically renders human text from those report models while JSON mode emits the model payloads directly.
- `millrace_engine/events.py`, which defines `EventRecord`, research-event classification, and the structured event-line format reused by CLI and TUI log surfaces.
- `millrace_engine/tui/gateway.py` and `millrace_engine/tui/gateway_views.py`, which adapt `EngineControl` report outputs into shell-facing view models instead of rereading raw runtime files.
- `millrace_engine/tui/widgets/` plus `millrace_engine/tui/formatting.py` and `millrace_engine/tui/screens/shell_support.py`, which render compact operator summaries, richer debug detail, notices, and inspector copy on top of those gateway views.
- `tests/test_cli.py` and `tests/test_tui.py`, which are the strongest behavioral proof that the current JSON, text, and shell surfaces match the shipped contracts.

When these layers disagree, the authority order is:

1. persisted runtime-owned and run-owned artifacts plus live liveness reconciliation
2. typed report models and builder logic in `control_reports.py` and `control_runtime_surface.py`
3. CLI text rendering and TUI gateway/view narrowing
4. operator documentation

That ordering matters because neither the CLI text view nor the TUI is an independent source of truth. They are presentations of report data that came from runtime-owned files and live probes.

## 3. Lifecycle And State Transitions

### 3.1 Durable Inputs Before Any UI Layer

The main observability inputs are durable runtime artifacts, not widget-local state:

| Path or family | What it contributes |
| --- | --- |
| `agents/.runtime/state.json` | execution-plane runtime snapshot, pending clear, last clear, config hashes, queue counts |
| `agents/research_state.json` | research runtime snapshot, queue selection, checkpoints, retry and lock state |
| `agents/audit_history.md` and `agents/audit_summary.json` | latest audit outcomes and summarized pass/fail counts |
| decision files resolved from the objective contract | latest gate and completion decisions |
| `agents/AUTONOMY_COMPLETE` | completion-marker presence, which is reported separately from audit pass state |
| `agents/runs/<run_id>/resolved_snapshot.json` and `transition_history.jsonl` | frozen-plan and runtime-transition provenance |
| `agents/engine_events.log` | durable event ledger for CLI logs, TUI logs, and supervisor recent-events views |
| persisted Sentinel files | one-workspace Sentinel summary surfaced through the supervisor report |

Those files are not equally authoritative. For example, `agents/.runtime/state.json` still passes through liveness reconciliation before `StatusReport` or `SupervisorReport` trusts it as live, and completion reporting distinguishes audit pass state from the separate marker file under `agents/AUTONOMY_COMPLETE`.

### 3.2 Report Builders Define The Read Contract

`control_runtime_surface.py` is the narrow read-side authority over what each high-level report exposes.

`status(detail=False)` is intentionally lighter than the richer surfaces. It returns runtime state, liveness, selection preview, size evidence, and config source information, but omits heavyweight report families such as assets and research detail.

`status(detail=True)` adds:

- asset inventory
- embedded research report
- active and next task details
- mailbox task-intake visibility

`supervisor_report()` does not recompute a second truth source. It starts from `status(detail=True)` and layers on:

- workspace health summary
- machine-readable `attention_reason` and `attention_summary`
- allowed action hints
- recent structured events
- a compact Sentinel summary built from persisted Sentinel state and latest report files

`research_report()` carries the research-side audit and completion story in one place:

- configured and current research mode
- queue families and ownership state
- gate and completion decisions
- completion-marker state via `CompletionStateView`
- audit summary and latest remediation
- governance and recovery-watchdog details when present

`run_provenance()` is the execution-run observability seam, combining compile-time snapshot data, transition history, policy-hook aggregation, latest policy evidence, and current live preview when it can still be recomputed.

### 3.3 CLI JSON, CLI Text, And TUI Narrowing

The CLI has two observability modes:

- JSON mode dumps the report model
- text mode passes that model through `cli_rendering.py`

That text rendering is deterministic and intentionally explicit. `render_status()` prints liveness authority, queue counts, config hashes, pending config state, mailbox intake, and embedded research details when present. `render_supervisor_report()` adds health, attention, allowed actions, and recent events. `render_run_provenance()` turns policy-hook counts, latest evidence, and transition excerpts into readable lines without inventing new facts.

The TUI is narrower still. `RuntimeGateway.load_workspace_snapshot()` composes a shell snapshot from multiple control-plane calls:

- `status(detail=False)`
- `supervisor_report(recent_event_limit=0)`
- `config_show()`
- `queue_inspect()`
- `research_report()`
- `research_history()`
- `logs()`
- `run_provenance()` only when the operator opens one run detail

So the shell is not a pretty wrapper around `status --detail`. It is a composed local snapshot built from several truthful read surfaces.

### 3.4 Deferred, Degraded, And Last-Known Visibility

This boundary intentionally surfaces lag instead of hiding it.

- Liveness can report `degraded_snapshot`, and both status and supervisor surfaces preserve that explicitly rather than silently flattening it to healthy.
- Mailbox-buffered add-task intent appears through `mailbox_task_intake` before backlog files visibly change.
- pending clear intent and queued config apply state surface as intent or pending hashes, not as already-applied state.
- research completion can be audit-pass-allowed while still showing `marker_missing`, because audit decisions and completion-marker presence are separate facts.
- the TUI gateway rebuilds `EngineControl` fresh per call, and widget refresh failures can continue showing the last good snapshot with failure copy such as "showing last known snapshot" instead of pretending a new refresh succeeded.

The practical rule is that a pending or degraded field means exactly that: Millrace is exposing the current read contract, not promising that all runtime-owned files have already converged to a final steady state.

## 4. Failure Modes And Recovery

The main failure classes for this boundary are visibility failures, not execution failures.

One class is degraded liveness. If the persisted runtime snapshot cannot be verified as live, the report surfaces expose degraded authority and the supervisor attention reason shifts to degraded state. Operators should read `status --detail --json` or `supervisor report --json` before treating the daemon as healthy.

Another class is partial research completion visibility. A research report can show a passing completion decision while `CompletionStateView` still reports `marker_missing` or `audit_not_passed`. That is not contradictory; it means the gate, authoritative decision file, and final marker have not all aligned yet.

The TUI has its own bounded visibility failure mode: gateway refresh can fail after a prior successful snapshot. In that case the shell keeps the last good data visible and overlays failure copy instead of blanking the operator view. Recovery is to inspect the debug surface or fall back to CLI JSON commands, not to edit shell state directly.

Finally, compact operator summaries intentionally omit detail. The logs panel collapses event summaries, the status bar shortens the active task and health fragments, and the overview or research panels compress state for scanability. When exact payloads matter, use debug mode, CLI JSON, or `run-provenance`.

## 5. Operator And Control Surfaces

The primary read-side surfaces for this boundary are:

- `millrace status --detail --json`
- `millrace supervisor report --json`
- `millrace research --json`
- `millrace run-provenance <run_id> --json`
- `millrace logs --json`
- `millrace sentinel status --json`
- `python3 -m millrace_engine.tui --config millrace.toml`

The main TUI observability surfaces map back to the same control-plane data:

- the status bar and Overview panel summarize runtime, backlog, mailbox intent, liveness, and Sentinel state
- the Research panel summarizes current mode, queue families, audit state, interview blocking, and governance warnings
- the Logs panel uses the event log, with operator summaries plus debug lines that mirror the structured CLI event format
- the Runs panel and run-detail modal use `run-provenance`
- the inspector and notices rail restate the same shell snapshot in compact operator copy

Safe mutation is intentionally separate from observation. The TUI can launch supported control actions through `EngineControl`, but it does not become an alternate authority for queue files, runtime snapshots, or audit artifacts. Operators should observe through the reports and mutate through supported commands or TUI actions, not by rewriting `agents/` files directly.

A practical debugging sequence is:

1. `status --detail --json` for liveness, queue, pending config, and embedded research
2. `supervisor report --json` for health, attention reason, Sentinel summary, and recent events
3. `research --json` for audit and completion-state specifics
4. `run-provenance <run_id> --json` when the question is about one execution run's plan, transitions, or policy evidence
5. the TUI debug surfaces when you want the same facts in a local shell without losing operator context

## 6. Proof Surface

The strongest proof for this boundary is:

- `tests/test_cli.py`
  - research report gate and completion decision visibility
  - status and supervisor degraded-liveness reporting
  - mailbox-buffered intake and pending-clear visibility
  - run-provenance policy-hook and evidence rendering
- `tests/test_tui.py`
  - runtime gateway snapshot shaping
  - recent-runs and run-detail mapping
  - Sentinel summary and inspector rendering
  - mailbox and pending-intent visibility in overview, queue, and research panels
  - "last known snapshot" behavior when a refresh fails after a valid snapshot exists
- `tests/test_package_parity.py`
  - public and packaged copies of this doc, the IA, and the portal remain synchronized
- `tests/test_baseline_assets.py`
  - the bundled runtime docs include this new path and required observability markers
- `millrace_engine/assets/manifest.json`
  - the packaged path, SHA, and size stay truthful for the shipped deep-doc copy

Drift should fail proof when:

- the public and packaged observability docs diverge
- the IA still points at the stale alias filename
- the doc claims the TUI is an independent source of truth
- deferred or degraded fields are documented as already-applied final state

## 7. Change Guidance

Update this doc when changes affect:

- the fields or semantics of `StatusReport`, `SupervisorReport`, `ResearchReport`, `RunProvenanceReport`, or `CompletionStateView`
- the authority split between persisted artifacts, report builders, CLI renderers, and TUI gateway views
- operator/debug rendering boundaries in the TUI
- Sentinel summary export through the supervisor report
- the supported debugging workflow across status, supervisor, research, run-provenance, and log surfaces

Do not expand this doc to absorb:

- lifecycle start/stop ownership
- queue or mailbox mutation semantics
- stale-state recovery controls themselves
- stage execution legality or handoff routing
- the broader operator unwedge playbook

If a future change primarily alters recovery actions, route it to the failure-mode or stale-recovery docs. If it primarily alters queue mutation or mailbox safety, route it to the control-plane doc. Keep this document focused on how Millrace turns runtime-owned evidence into truthful operator visibility surfaces.
