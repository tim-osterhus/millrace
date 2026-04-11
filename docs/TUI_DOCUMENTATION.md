# Millrace TUI Documentation

This document explains how to use and operate the Millrace TUI in practice.

It starts with the shortest useful path, then expands into the full operating model.

## Basic Summary

If you only need the basics, this is the normal flow:

1. Open a terminal in the Millrace workspace root that contains `millrace.toml`.
2. Launch the TUI:

```bash
./.venv/bin/python -m millrace_engine.tui
```

3. Let the startup health gate run.
4. If the runtime is not already running, the TUI will ask whether to launch daemon mode now.
5. Use:
   - `t` to add a task
   - `i` to queue an idea file
   - `Ctrl+P` to open the command palette
   - `1` through `7` to switch panels
   - `?` to open or close keyboard help
6. If you want one single foreground cycle, choose `Start Once`.
7. If you want continuous operation, choose `Start Daemon`.
8. Watch the system in:
   - `Overview` for top-level runtime state
   - `Queue` for active and pending work
   - `Research` for research-side status and activity
   - `Logs` for the live event stream
   - `Runs` for recent execution runs and run detail

The TUI is not the engine itself. It is a control shell over the same Millrace control plane used by the CLI and the advisor workflow.

## What The TUI Is

The TUI is a local operator shell for a Millrace workspace.

It gives you:

- a startup workspace health gate
- a persistent shell with high-signal panels
- guided modal flows for common actions
- lifecycle controls for the Millrace runtime
- live event and status visibility without needing to inspect files manually

It does not replace the underlying Millrace engine. Instead, it sits on top of the same file-backed runtime contract that the CLI uses.

That means:

- the TUI can observe the runtime even when the daemon is already running
- the TUI can launch or control the daemon through supported control paths
- the daemon can keep running after the TUI closes
- CLI commands, the advisor workflow, and the TUI all converge on the same workspace state

## What The TUI Is Not

The TUI is not:

- a second runtime engine
- a separate research process
- a direct editor over arbitrary workspace files
- a bypass around daemon safety rules

When the daemon is running, mutating operations still go through mailbox-safe control paths. The TUI does not directly seize ownership of live runtime state.

## External Supervisor Boundary

If OpenClaw or another external supervisor harness is coordinating Millrace work, keep that harness on the CLI-first supervisor contract instead of treating the TUI as the automation surface:

```bash
millrace --config millrace.toml supervisor report --json
millrace --config millrace.toml supervisor pause --issuer <name> --json
millrace --config millrace.toml supervisor add-task "Example task" --issuer <name> --json
millrace --config millrace.toml supervisor cleanup remove <task-id> --issuer <name> --reason "Invalid queued work" --json
```

The TUI can observe and act through the same control plane for a local operator, but it is not the remote harness interface and it should not replace the attributable supervisor contract.

## Launching The TUI

From the workspace root:

```bash
./.venv/bin/python -m millrace_engine.tui
```

You can also point it at another workspace:

```bash
./.venv/bin/python -m millrace_engine.tui --config /absolute/path/to/workspace/millrace.toml
```

Behavior to know:

- `--config` defaults to `./millrace.toml`
- if the config file does not exist, the TUI exits immediately with a config-not-found error
- the window subtitle reflects the active workspace path

## Startup Sequence

When you launch the TUI, it does not immediately jump into the shell.

The startup sequence is:

1. Resolve the config path.
2. Open the health gate.
3. Run the deterministic workspace health check.
4. If health passes, enter the main shell.
5. Load the first workspace snapshot.
6. If the runtime is not already running, offer to launch daemon mode.

This startup flow is deliberate. Merely opening the TUI should not silently mutate runtime state.

## Health Gate

Before the shell becomes interactive, the TUI shows a workspace health gate.

Purpose:

- confirm the workspace is usable
- prevent a broken workspace from dropping you into a misleading live shell
- give quick recovery context without leaving the TUI

Available actions on the health gate:

- `Retry`
- `Open Config`
- `Open Logs`
- `Quit`

Keyboard shortcuts on the health gate:

- `r` retries the health check
- `f` opens config recovery preview
- `l` opens runtime log recovery preview
- `q` quits the app

If health passes, the TUI enters the shell automatically.

If health fails, the shell does not open. Instead, the gate shows the failing checks and keeps you in a recovery-oriented view until you retry or quit.

## Startup Daemon Prompt

After the health gate passes and the first workspace refresh succeeds, the TUI checks whether the runtime daemon is already running.

If the daemon is not running, the TUI opens a startup confirmation modal:

- `Start Daemon`
- `Stay Idle`

This prompt appears only once per TUI session.

Choose `Start Daemon` if you want the workspace to keep operating in the background while the TUI stays attached as a live control panel.

Choose `Stay Idle` if you only want to inspect the workspace, queue work manually, or later run `Start Once` instead of daemon mode.

If the daemon is already running, this modal does not appear.

## The Main Shell

Once the health gate passes, the TUI enters the main shell.

The shell layout is:

- a left sidebar with panel navigation, daemon badge, and mode toggle
- a status bar above the active panel body
- the active panel content area, which uses composed cards, section stacks, and structured list rows in operator mode
- a right-hand inspector that mirrors the current panel focus and context
- a notices strip for action outcomes and failures
- a real footer that exposes the currently relevant discovery surface

The TUI does not render a second fake terminal title row inside the app. The host terminal provides the outer window chrome; the shipped shell begins at the sidebar, status bar, panel body, and notices rail.

The shell continuously refreshes workspace snapshots and tails the event stream in the background. In practice, this gives you a live control surface without requiring manual file inspection.

## Operator And Debug Modes

The shell has two display modes:

- `operator` mode (default): concise, summary-first panel content for daily operation, rendered as widget-composed cards and list sections
- `debug` mode: denser detail blocks, fuller metadata, and expanded failure context

Switch modes using either:

- the sidebar mode toggle (`Mode: OPERATOR -> DEBUG` or `Mode: DEBUG -> OPERATOR`)
- the command palette action `Toggle Display Mode`

Display mode is session-scoped to the current TUI run.

## Expanded Stream Mode

The shell also has one expanded-stream presentation mode.

Expanded mode is not a separate panel. It temporarily replaces the active panel body with a full-height live stream while keeping the sidebar, top status strip, inspector, notices rail, and footer visible.

Use expanded mode when you want a continuously readable foreground feed instead of the normal compact panel body.

Enter and leave it with:

- `e` to toggle expanded mode on or off
- `Escape` to leave expanded mode directly
- `l` to jump back to the live tail after you have scrolled away

Expanded mode changes renderer with the current display mode:

- `operator expanded`: a narrated activity feed synthesized from runtime events for day-to-day operation
- `debug expanded`: the raw structured event stream, close to `python3 -m millrace_engine logs --follow`

Scroll behavior matters:

- if you scroll upward, the view leaves live tail and enters scrollback
- new events keep arriving even while you stay in scrollback
- use `l` or scroll back to the end when you want to follow the newest lines again

## Lifecycle Signals

Lifecycle state is intentionally visible in more than one place:

- sidebar daemon badge
- top status strip (`life ...` and action-busy indicator when lifecycle commands are running)
- notices strip for action success/failure summaries

You should be able to distinguish idle, launching, running, paused, stopping, and failure states without opening debug mode.

## Global Controls

These shortcuts are always important:

- `1` through `7` switch panels
- `s` focuses the sidebar
- `c` focuses the active panel content
- `t` opens the Add Task modal
- `i` opens the Add Idea modal
- `Ctrl+P` opens the command palette
- `?` opens keyboard help

The command palette is the quickest way to discover lifecycle, display-mode, publish, config, and panel actions without memorizing every shortcut.

## Panels

The TUI has seven main panels.

In operator mode, these panels are intended to read like a compact control surface: metric cards, detail cards, status sections, and structured list items instead of one long text report.

### 1. Overview

The `Overview` panel is the top-level cockpit.

It summarizes:

- whether the daemon is running
- execution mode and pause state
- research status
- backlog depth and active task
- the latest visible run artifact
- pending compounding governance review work
- the most recent governed procedure/context-fact usage visible from local runs
- basic route and stage information from the current runtime selection

In operator mode, those signals land in metric cards plus runtime/latest-run/research/governance/attention detail cards.

Use `Overview` when you want the fastest single-screen answer to: "What is Millrace doing right now?"

### 2. Queue

The `Queue` panel shows:

- the active task
- the next task
- the visible backlog
- the current active run context, when available

In operator mode, queue state is split into status and metric cards plus a backlog list of selectable task cards.

Queue controls:

- `Up` and `Down` move selection
- `Home` and `End` jump within the visible backlog
- `Enter` starts or applies a reorder draft
- `r` starts a reorder draft
- `[` moves the selected task earlier in the draft
- `]` moves the selected task later in the draft
- `q` quarantines the selected queued task after confirmation
- `x` removes the selected queued task after confirmation
- `Escape` cancels the draft
- `o` opens run detail for the active run context

Important behavior:

- queue reordering is draft-only at first
- the live backlog does not change until you confirm the reorder
- when the daemon is running, the reorder request is queued through the mailbox first
- queue cleanup actions also stay confirmation-first, and when the daemon is running they queue through the mailbox instead of mutating live state directly

### 3. Runs

The `Runs` panel shows recent execution runs discovered in the workspace.

It summarizes:

- recent run ids
- compile time
- stage counts
- concise frozen-plan and selection information

In operator mode, recent runs render as a flagged run list with compact status cards rather than a prose report.

Runs controls:

- `Up` and `Down` move selection
- `Home` and `End` jump within the list
- `Enter` opens run detail

Use `Runs` when you want to inspect execution history without going straight to raw run artifact files.

### 4. Research

The `Research` panel is the TUI's research-plane visibility surface.

It shows:

- current, last, configured, and idle research modes
- research status and mode reason
- cycle and transition counts
- selected queue family
- deferred request counts
- visible research queue families
- audit and completion-gate summaries
- governance state when available
- recent research activity

In operator mode, those signals are grouped into status/audit/activity cards plus a family queue card stack.

Research controls:

- `Up` and `Down` move between pending interview questions when research is waiting on operator input
- `Home` and `End` jump within the pending interview list
- `Enter` opens the selected interview workflow
- `o` also opens the selected interview workflow

Use `Research` when you want to know whether research is active, deferred, blocked, or completing governed work as part of the same overall runtime.

### 5. Logs

The `Logs` panel shows the structured runtime event stream.

It supports:

- live follow mode
- frozen inspection mode
- source filtering
- event-type filtering
- handoff into run detail when an event includes a run id

Logs controls:

- `Up` and `Down` move selection
- `Home` and `End` jump within the filtered list
- `Enter` opens run detail if the selected event has a run id
- `f` toggles follow and frozen mode
- `Ctrl+Left` and `Ctrl+Right` change the source filter
- `Ctrl+Up` and `Ctrl+Down` change the event-type filter

Use `Logs` when you want the closest equivalent to tailing `agents/engine_events.log`, but with local filtering and run-aware drilldown.

Use expanded mode when you want a larger foreground stream:

- in `operator` mode, expanded view turns the same event flow into narrated human-readable runtime lines
- in `debug` mode, expanded view shows the raw event lines directly
- the compact `Logs` panel remains the better fit when you want filters and selection-driven drilldown instead of an immersive feed

### 6. Config

The `Config` panel shows the active configuration in a controlled, boundary-aware way.

It includes:

- config source and hash
- whether edits are currently mailbox-routed or direct
- pending boundary state
- rollback state
- supported editable fields
- startup-only read-only fields
- explanations of boundary semantics

In operator mode, config state is grouped into status/source/change-queue cards plus a selectable list of editable field cards.

Config controls:

- `Up` and `Down` move between supported editable fields
- `Home` and `End` jump within editable fields
- `e` opens the guided edit modal
- `Enter` also opens the guided edit modal
- `r` reloads config from disk through the supported control path

Important behavior:

- the panel does not expose arbitrary free-form config mutation
- only validated, supported fields can be edited
- startup-only fields remain read-only here
- if the daemon is running, edits queue through the mailbox first
- if the daemon is stopped, edits write directly and reload immediately

### 7. Publish

The `Publish` panel is a deliberate staging and publish surface.

It shows:

- publish status
- whether commit is allowed
- whether push appears ready from current facts
- resolved staging repo path and branch
- origin status
- manifest source and selected paths
- changed paths
- skip reasons when publish is not currently allowed

In operator mode, publish state is grouped into status, repo-truth, git-facts, changed-path inspection, and safe-next-action cards.

Publish controls:

- `r` refreshes preflight
- `g` syncs the manifest-selected files into staging
- `n` opens the local-only commit flow
- `p` opens the commit-and-push flow

Important behavior:

- publish preflight is read-only
- Publish acts on the resolved staging repo shown in the panel, not directly on the main workspace checkout
- the local no-push commit path is the default safer path
- blocked states explain whether commit is blocked or only push is blocked
- the push path is intentionally higher friction and asks for explicit confirmation

## Status Bar And Notices

Above the active panel, the TUI keeps a status bar visible at all times.

The status bar summarizes:

- workspace path
- active panel
- health status
- daemon state
- execution state
- research state
- backlog depth
- active task
- refresh freshness
- any in-progress lifecycle action

The right-hand inspector keeps the active panel, current focus target, and selection-specific context visible without leaving the shell.

Below the active panel, the notices area records recent action results and failures.

The footer remains mounted as the live binding-discovery strip for whichever shell or panel actions are currently relevant.

Use the status bar for continuous situational awareness, the inspector for selection-specific context, the notices area for immediate action feedback, and the footer when you want to discover the current keyboard contract quickly.

## Command Palette

Open the command palette with `Ctrl+P`.

It exposes system actions such as:

- `Start Once`
- `Start Daemon`
- `Pause Runtime`
- `Resume Runtime`
- `Stop Runtime`
- `Edit Config Field`
- `Reload Config`
- `Publish Preflight`
- `Publish Sync`
- `Publish Commit (No Push)`
- `Publish Commit And Push`
- panel navigation commands
- focus commands
- keyboard help

If you do not remember a shortcut, use the command palette first.

## Common Operator Actions

### Add A Task

Press `t`.

The Add Task modal collects:

- task title, required
- spec id, optional
- task body, optional

Use this for execution backlog work you want the runtime to perform.

### Add An Idea

Press `i`.

The Add Idea modal collects one file path. The file must already exist.

Path rules:

- absolute paths are accepted
- relative paths resolve from the active workspace root
- the path must resolve to a real file

Use this when you want to queue a source markdown file into research-side intake.

### Start Once

Use the command palette and choose `Start Once`.

Behavior:

- launches a foreground subprocess
- runs one once-mode execution cycle
- returns control to the TUI when complete
- reports success or failure through the notices area

Use `Start Once` when you want a single bounded cycle instead of a long-running daemon.

### Start Daemon

Use the startup modal or the command palette and choose `Start Daemon`.

Behavior:

- launches the daemon as a detached subprocess
- keeps the TUI attached as the monitoring and control surface
- allows the daemon to continue running even if the TUI later exits

Use this for normal background operation.

### Pause, Resume, Stop

Use the command palette:

- `Pause Runtime`
- `Resume Runtime`
- `Stop Runtime`

These are daemon control operations, not direct process hacks. They use the supported control plane.

### Reorder The Queue

Go to `Queue`.

Workflow:

1. Select a task with `Up` or `Down`.
2. Press `Enter` or `r` to begin a reorder draft.
3. Use `[` and `]` to move the selected task.
4. Press `Enter` to review and confirm.
5. Confirm the reorder in the modal.

Until you confirm, the live backlog has not changed.

### Edit Supported Config Fields

Go to `Config`.

Workflow:

1. Select a supported editable field.
2. Press `e` or `Enter`.
3. Enter the new validated value.
4. Submit the change.

The modal explains:

- the field key
- the runtime boundary for the field
- the current value
- whether the daemon state changes how the edit is applied

### Inspect Run Detail

Run detail can be opened from:

- `Runs`
- `Logs`, when the selected event has a run id
- `Queue`, when there is an active run context

The run detail modal summarizes:

- run id
- compile time
- frozen plan id and hash
- selection and routing information
- governed procedure creation plus procedure/context-fact usage counts when the run recorded them
- stage labels
- transition history summary
- policy summary
- integration summary
- snapshot and trace file locations when present

Use it when you want a truthful concise explanation of one run without leaving the TUI.

### Publish From The TUI

Go to `Publish`.

Typical workflow:

1. Press `r` to refresh publish preflight.
2. Review the resolved staging repo path and confirm that Publish is acting on staging rather than the main checkout directly.
3. Review branch, origin, manifest source, and changed paths.
4. If preflight says there is nothing to commit or staging looks stale, press `g` to sync manifest-selected files into staging, then `r` to refresh facts.
5. Press `n` for the safer local commit path when commit is available.
6. Press `p` only when you intentionally want commit-and-push behavior and the panel says push facts are ready.

The push flow is intentionally higher friction than the local-only commit path.

## Operating Modes

There are three main ways to use the TUI.

### TUI As The Main Control Surface

This is the simplest interactive mode.

Typical flow:

1. Launch the TUI.
2. Start the daemon from the startup prompt.
3. Add tasks or ideas from inside the TUI.
4. Watch progress in Overview, Queue, Research, Logs, and Runs.
5. Pause, resume, stop, inspect config, or publish as needed.

### TUI Plus CLI

This is useful when you want JSON output or scripted control.

Typical flow:

1. Keep the TUI open.
2. Use CLI commands in another terminal for one-shot actions.
3. Watch the resulting state changes and events in the TUI.

This works cleanly because both the CLI and the TUI use the same control layer and workspace state.

### TUI Plus Advisor

The advisor workflow and the TUI are separate clients over the same system.

A practical pattern is:

- let the advisor submit tasks, ideas, or control actions
- keep the TUI open as the live cockpit

This means the advisor can act as the middleman while the TUI gives you continuous visibility into what the system is doing.

The advisor does not need to remain online for the daemon to keep running.

## How Research And Execution Relate In The TUI

Millrace has one engine with two logical planes:

- execution
- research

The TUI reflects that model.

What this means operationally:

- you do not run separate manual shell loops for research and orchestration here
- research and execution can both be visible during the same daemon lifetime
- the event stream is unified, while the `Research` panel gives a research-focused slice of state

If you want execution-centric visibility, use:

- `Overview`
- `Queue`
- `Runs`
- `Logs`

If you want research-centric visibility, use:

- `Research`
- `Logs`
- `Runs`, when research participation shows up in run selection context

## How To Test The TUI In Real Operation

If you want to see the TUI actually doing work, use this workflow:

1. Launch the TUI.
2. Let health pass.
3. If prompted, choose `Start Daemon`.
4. Press `t` and add a simple task.
5. Watch:
   - `Overview` for daemon and backlog state
   - `Queue` for active and pending work
   - `Logs` for event flow
   - `Runs` for new runs
6. Press `i` and add an idea file if you want to exercise research-side intake.
7. Use `Pause Runtime`, `Resume Runtime`, and `Stop Runtime` from the command palette to confirm lifecycle control.

You can also test a mixed workflow:

1. Open the TUI in one terminal.
2. In another terminal, run CLI commands such as `add-task`, `add-idea`, or `status`.
3. Confirm the TUI reflects the resulting changes.

## Detailed Keyboard Reference

### Global

- `1` through `7`: open the main panels
- `s`: focus sidebar
- `c`: focus content
- `d`: toggle operator/debug display mode
- `e`: toggle expanded stream mode for the active panel
- `t`: open Add Task
- `i`: open Add Idea
- `Ctrl+P`: open command palette
- `?`: open or close keyboard help

### Expanded Stream

- `Escape`: return from expanded mode to the compact panel body
- `l`: jump the expanded stream back to the live tail

### Queue

- `Up`, `Down`, `Home`, `End`: move selection
- `Enter`: begin or apply reorder draft
- `r`: begin reorder
- `[`: move selected task earlier
- `]`: move selected task later
- `q`: quarantine the selected task after confirmation
- `x`: remove the selected task after confirmation
- `Escape`: cancel reorder
- `o`: open run detail for the current active run

### Runs

- `Up`, `Down`, `Home`, `End`: move selection
- `Enter`: open selected run detail

### Research

- `Up`, `Down`, `Home`, `End`: move between pending interview questions
- `Enter`: open the selected interview workflow
- `o`: open the selected interview workflow

### Logs

- `Up`, `Down`, `Home`, `End`: move selection
- `Enter`: open run detail when available
- `f`: toggle follow or frozen mode
- `Ctrl+Left`, `Ctrl+Right`: cycle source filter
- `Ctrl+Up`, `Ctrl+Down`: cycle event-type filter

### Config

- `Up`, `Down`, `Home`, `End`: move selection
- `e`: edit selected field
- `Enter`: edit selected field
- `r`: reload config

### Publish

- `r`: refresh preflight
- `g`: sync staging
- `n`: commit locally without push
- `p`: commit and push

## What The TUI Reads And Writes

The TUI is built around the same file-backed Millrace workspace as the CLI.

In normal operation it reads from runtime-owned surfaces such as:

- `agents/.runtime/state.json`
- `agents/engine_events.log`
- queue files
- run artifacts
- research state
- publish and config state

When you take action from the TUI, it uses supported control operations rather than editing these files by hand.

That distinction matters:

- the TUI is a control plane, not a generic text editor
- the daemon remains the live owner of mutable runtime state while it is running
- the workspace files remain the durable truth surface underneath the UI

## Troubleshooting

### The TUI Opens But The Runtime Is Not Running

That is normal.

Launching the TUI does not silently start the engine. Use the startup prompt or the command palette to choose `Start Daemon`, or use `Start Once` if you only want one cycle.

### The Health Gate Fails

Use:

- `Open Config` to inspect the resolved config file
- `Open Logs` to inspect the runtime event log preview
- `Retry` after fixing the underlying issue

If health does not pass, the shell will not open.

### I Want To Watch The System While Another Client Drives It

Keep the TUI open and use either:

- the CLI in another terminal
- the advisor workflow

The TUI will continue to reflect workspace state and runtime events as they change.

### I Expected Two Separate Logs Or Two Separate Loop Windows

The Python runtime is built around one engine with two logical planes, not two separately operated shell loops. Use:

- `Logs` for the unified event stream
- `Research` for research-specific visibility
- `Runs` for concise run and provenance inspection

### A Panel Looks Stale

Check:

- the notices area for recent failures
- the status bar refresh line
- the `Logs` panel for recent runtime events

If needed, use the command palette to retry config or publish actions, or restart the TUI if the problem is strictly in the shell layer.

## Recommended Day-To-Day Pattern

For normal interactive local use, this is the cleanest operating pattern:

1. Launch the TUI.
2. Let health pass.
3. Start daemon mode if the workspace is idle.
4. Use the TUI as the primary cockpit.
5. Add tasks or ideas from inside the TUI when convenient.
6. Use the CLI or advisor only when you want scripted actions or delegated operation.
7. Watch `Overview`, `Queue`, `Research`, `Logs`, and `Runs` rather than inspecting raw files by hand.

That pattern gives you the simplest operator experience while still preserving the underlying file-backed Millrace contract.
