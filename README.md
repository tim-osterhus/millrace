# Millrace Runtime

Millrace is a local, file-backed automation runtime for software delivery workflows.

It gives you:

- a Python CLI and a Textual TUI
- a single runtime engine
- an execution plane for delivery work
- a research plane for idea intake, audit, and related governed workflows
- durable runtime state, event logs, provenance, and publish surfaces inside the workspace

For day-to-day operation, read `OPERATOR_GUIDE.md`. For the agent-facing operator prompt, read `ADVISOR.md`. For a deeper architecture walkthrough, read `RUNTIME_DEEP_DIVE.md`.

## What Lives Here

- `millrace_engine/`: the runtime package, CLI, control layer, and engine
- `millrace.toml`: native runtime configuration
- `agents/`: the live workspace surface for queues, runs, logs, prompts, and state
- `tests/`: unit and integration tests
- `millrace_engine/assets/`: the packaged baseline bundle used by `millrace init`

## Core Runtime Model

- One CLI: `python3 -m millrace_engine --config millrace.toml ...`
- One TUI shell: `python3 -m millrace_engine.tui --config millrace.toml`
- One engine: the daemon owns lifecycle, watchers, mailbox commands, runtime state, and event emission.
- Two logical planes: execution work and research work share the same engine but keep separate status and reporting surfaces.
- File-backed truth: queues, runs, status, logs, diagnostics, and provenance live under `agents/`.
- Workspace-first assets: workspace files override packaged defaults when present; packaged assets are the fallback.
- Frozen per-run plans: before a standard execution run, Millrace resolves the selected mode and loop, applies workspace overrides, and freezes the effective plan under `agents/runs/<run_id>/`.

## Quick Start

```bash
cd millrace
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
python3 -m millrace_engine --config millrace.toml health --json
python3 -m millrace_engine --config millrace.toml status --detail --json
python3 -m millrace_engine.tui --config millrace.toml
```

Add work and execute one foreground cycle:

```bash
python3 -m millrace_engine --config millrace.toml add-task "Example task"
python3 -m millrace_engine --config millrace.toml start --once
```

If the package entrypoint is installed, `millrace --config millrace.toml ...` is equivalent.

## TUI Shell

The TUI is the fastest way to operate a local workspace when you want a dense control panel instead of a stream of CLI calls.

Launch it with:

```bash
python3 -m millrace_engine.tui --config millrace.toml
```

What it provides:

- a startup health gate before the shell becomes interactive
- a dual-mode shell with summary-first `operator` mode and detail-forward `debug` mode
- one toggleable expanded stream mode that takes over the main content area while leaving the sidebar, status strip, and notices visible
- a persistent shell with sidebar navigation, a compact status strip, and widget-composed overview/queue/runs/research/logs/config/publish panels built from cards, structured sections, and list rows
- explicit lifecycle signaling across the sidebar daemon badge, top status strip, and notices rail
- guided task, idea, queue reorder, config edit, and publish confirmation flows
- lifecycle actions for `start --once`, `start --daemon`, `pause`, `resume`, and `stop`
- a command palette for common actions, including display-mode toggle and lifecycle controls
- built-in keyboard help (`?`) and panel shortcuts (`1` through `7`)
- the same runtime semantics as the CLI, including mailbox-safe daemon mutations and the same file-backed truth surfaces

Use the TUI for day-to-day observation and control. Use the CLI when you want scriptable, one-shot commands or JSON output.

Expanded stream behavior to know:

- press `e` to toggle the active panel into expanded mode and `Escape` to return to the normal panel body
- in `operator` mode, expanded view renders a narrated activity feed from runtime events
- in `debug` mode, expanded view renders the raw structured event lines, close to `logs --follow`
- if you scroll upward, live follow disengages until you jump back with `l`

## Start A Fresh Workspace

Use `init` when you want a new Millrace workspace without copying files by hand:

```bash
python3 -m millrace_engine init /absolute/path/to/workspace
python3 -m millrace_engine init --force /absolute/path/to/workspace
```

Important behavior:

- Without `--force`, the destination must be absent or empty.
- With `--force`, Millrace overwrites manifest-tracked baseline files in place. It does not wipe the directory.
- Workspace prompt and registry overlays can override packaged defaults after scaffolding.
- Missing workspace prompt files fall back to packaged assets when the resolver supports that family.

After scaffolding, run the workspace preflight before you rely on the runtime:

```bash
python3 -m millrace_engine --config /absolute/path/to/workspace/millrace.toml health --json
```

## Daily Operator Flow

1. Preflight the workspace:

```bash
python3 -m millrace_engine --config millrace.toml health --json
```

Or launch the TUI and let the health gate run automatically:

```bash
python3 -m millrace_engine.tui --config millrace.toml
```

2. Inspect current state:

```bash
python3 -m millrace_engine --config millrace.toml status --detail --json
python3 -m millrace_engine --config millrace.toml queue inspect --json
python3 -m millrace_engine --config millrace.toml research --json
python3 -m millrace_engine --config millrace.toml logs --tail 50 --json
```

In the TUI, the Overview, Queue, Research, Logs, and Runs panels expose the same runtime state without leaving the shell.

3. Add work:

```bash
python3 -m millrace_engine --config millrace.toml add-task "Example task"
python3 -m millrace_engine --config millrace.toml add-idea /absolute/path/to/idea.md
```

`add-task` appends an execution task to the backlog. `add-idea` copies a source markdown file into `agents/ideas/raw/` for research-side intake.

4. Run the engine:

```bash
python3 -m millrace_engine --config millrace.toml start --once
python3 -m millrace_engine --config millrace.toml start --daemon
```

The TUI exposes the same actions through the command palette and keyboard-driven panel flows.

5. Inspect outcomes:

```bash
python3 -m millrace_engine --config millrace.toml logs --follow
python3 -m millrace_engine --config millrace.toml run-provenance <run_id> --json
python3 -m millrace_engine --config millrace.toml research history --json
```

The TUI keeps recent runs, filtered logs, and concise run-provenance drilldown in one place, including a run-detail modal from the Runs and Logs panels.

When you want the old foreground-feed feel without leaving the shell, use TUI expanded mode instead of opening a second terminal just to run `logs --follow`.

6. Control a long-running daemon:

```bash
python3 -m millrace_engine --config millrace.toml pause
python3 -m millrace_engine --config millrace.toml resume
python3 -m millrace_engine --config millrace.toml stop
```

When the daemon is running, mutating commands route through the mailbox so one owner process stays in control of live state.

## Publish And Staging

Millrace includes a staging and publish surface for preparing a release worktree:

```bash
python3 -m millrace_engine --config millrace.toml publish sync --json
python3 -m millrace_engine --config millrace.toml publish preflight --json
python3 -m millrace_engine --config millrace.toml publish commit --no-push --json
```

`publish preflight` is read-only. It reports the resolved staging repo, manifest source, git readiness, and changed paths without mutating git state.

## Runtime Inputs

By default the daemon watches:

- backlog and autonomy marker files
- raw ideas under `agents/ideas/raw/`
- mailbox commands under `agents/.runtime/commands/incoming/`
- the config file `millrace.toml`

These roots are configured under `[watchers]`.

Remaining shell loop artifacts under `agents/` are compatibility or reference material only. The supported operating surfaces are the Python CLI and the Textual TUI.

## Verification

```bash
python3 -m compileall millrace_engine
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests
```

Use `health --json` as the first runtime preflight and `status --detail --json`, `research --json`, `logs`, and `run-provenance` as the main visibility surfaces once the engine is running.
