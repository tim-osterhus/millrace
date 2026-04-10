# Millrace Operator Guide

This guide is the human operator workflow for an initialized Millrace workspace. It assumes you are operating against a workspace created by `millrace init`, not browsing the package repo root.

Use it with:

- `README.md` for product overview and quick start
- `ADVISOR.md` for the agent-facing control surface
- `docs/RUNTIME_DEEP_DIVE.md` for implementation details

## Canonical Invocation

Run commands from the workspace root that contains your active `millrace.toml`, or pass an absolute config path directly.

CLI:

```bash
millrace --config millrace.toml ...
```

Module form:

```bash
python3 -m millrace_engine --config millrace.toml ...
```

TUI:

```bash
python3 -m millrace_engine.tui --config millrace.toml
```

## Supported Operating Surface

The supported control surfaces are the `millrace` CLI and the Textual TUI.

Use the CLI commands:

- `init`
- `health`
- `doctor`
- `status`
- `supervisor`
- `queue`
- `add-task`
- `add-idea`
- `interview`
- `start`
- `pause`
- `resume`
- `stop`
- `logs`
- `research`
- `run-provenance`
- `compounding`
- `publish`
- `config`

Or launch the TUI directly with:

- `python3 -m millrace_engine.tui --config millrace.toml`

Do not treat `bash agents/orchestrate_loop.sh ...` or `bash agents/research_loop.sh ...` as the supported operator entrypoints. Those shell assets are compatibility or reference material now, not the main runtime path.

## Governed Compounding Operating Model

Governed compounding follows an explicit `raw -> compiled -> query -> lint` loop. Millrace does not treat transcript summaries or packaged `agents/skills` as runtime authority for this subsystem.

- `raw`: the input evidence is runtime-owned and file-backed, including run outputs, `agents/runs/<run_id>/transition_history.jsonl`, diagnostics bundles, and harness benchmark/search artifacts.
- `compiled`: the runtime turns selected evidence into typed governed artifacts under `agents/compounding/`, including procedures, context facts, lifecycle records, harness candidates, benchmark results, and recommendations. Those primary artifacts are the authority.
- `query`: stage-aware retrieval injects only eligible procedures and context facts under explicit stage rules and budgets, while the CLI exposes operator inspection through `millrace compounding ...`. `millrace compounding orient` writes `agents/compounding/indexes/governed_store_index.json` and `agents/compounding/indexes/relationship_summary.json` as secondary orientation aids.
- `lint`: `millrace compounding lint` plus `health` and `doctor` catch stale governed artifacts, broken references, and stored-orientation drift before those problems silently degrade reuse.

Keep this boundary straight:

- packaged `agents/skills` remain shipped operating playbooks
- governed compounding authority lives in typed artifacts under `agents/compounding/`
- `Derived orientation surface only; governed compounding artifacts remain the source of truth.`

## First Setup

Install the published package:

```bash
python3 -m pip install millrace-ai
millrace init /absolute/path/to/workspace
millrace --config /absolute/path/to/workspace/millrace.toml health --json
millrace --config /absolute/path/to/workspace/millrace.toml doctor
```

If you need the exact public release instead of the latest compatible publish, install `millrace-ai==0.8.0`.

The initialized workspace ships with real default model ids for the Codex runner, including `gpt-5.3-codex` and `gpt-5.2`. Those defaults are not placeholders, but they still rely on the local runner environment being usable.

The first-run research contract is also deliberate: a fresh workspace starts with `[research] mode = "stub"` and `interview_policy = "off"`. Research reporting and queues are present, but real GoalSpec, incident, and audit progression are not active until you intentionally reconfigure research away from the stub baseline.

If you prefer an interactive shell, launch the TUI against that workspace after the environment is ready:

```bash
python3 -m millrace_engine.tui --config /absolute/path/to/workspace/millrace.toml
```

If you are developing Millrace itself from a source checkout instead of using the published package:

```bash
cd /absolute/path/to/millrace
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

Release CI verifies a narrower contract than a contributor source checkout: it smoke-installs the built wheel into a clean virtualenv, initializes fresh workspaces with `millrace init`, verifies `health --json`, verifies `doctor --json` under a controlled runner-prerequisite shim, proves one shimmed `start --once` execution pass, proves one configured non-stub research path, proves `supervisor report --json`, and proves `publish preflight --json` after syncing a local staging repo. The broader pytest surface remains a source-checkout workflow with dev dependencies rather than a promised full wheel or sdist contributor-validation path.

## Initialize A New Workspace

Use `init` when you want a fresh workspace without copying files manually:

```bash
millrace init /absolute/path/to/new-workspace
millrace init --force /absolute/path/to/new-workspace
millrace --config /absolute/path/to/new-workspace/millrace.toml upgrade
millrace --config /absolute/path/to/new-workspace/millrace.toml upgrade --apply
```

Behavior to remember:

- Without `--force`, the destination must be absent or empty.
- With `--force`, Millrace overwrites manifest-tracked baseline files in place. It does not clean the directory.
- `millrace upgrade` is the non-destructive inspection path for existing workspaces: it previews manifest-tracked baseline deltas plus any supported persisted-state migrations and leaves runtime-owned plus operator-owned paths untouched.
- `millrace upgrade` also reports missing runtime-owned bootstrap paths that will be materialized for older workspaces, including newer `agents/compounding/...` and `agents/lab/...` families plus starter files.
- `millrace upgrade --apply` refreshes those manifest-tracked baseline files, materializes any missing runtime-owned bootstrap paths, and applies supported persisted-state migrations in place while preserving existing runtime-owned plus operator-owned paths.
- The current persisted-state migration seam covers research runtime state: upgrade can canonicalize or materialize `agents/research_state.json` from supported research-state helpers instead of expecting silent fallback or manual deletion.
- Treat `init --force` as the scaffold write path, not as a substitute for the supported upgrade flow.
- The new workspace still uses workspace-first asset resolution after init.

Always run the preflight after creating or updating a workspace:

```bash
millrace --config /absolute/path/to/new-workspace/millrace.toml health --json
millrace --config /absolute/path/to/new-workspace/millrace.toml doctor
```

The TUI runs the same workspace health check automatically before entering the shell.
Use `doctor` as the final execution-readiness gate: it verifies that the current machine can actually run the shipped defaults through the required external runner CLI.
`health` and `doctor` also surface the active research bootstrap contract, so operators can tell immediately whether research is stubbed by default or intentionally configured for an active mode.

## TUI Workflow

The TUI is a supported operator shell over the same control layer used by the CLI. It does not bypass the runtime contract.

What the shell gives you:

- a startup health gate with retry and local recovery context
- a sidebar-driven shell with a top status strip, widget-composed Overview/Queue/Runs/Research/Logs/Config/Publish panels, a right-hand inspector, a notices rail, and a real footer discovery strip
- dual display modes: `operator` for concise daily control and `debug` for denser diagnostics
- one expanded stream mode that swaps the current panel body for a full-height live feed while keeping the rest of the shell frame visible
- lifecycle state signals in multiple places: sidebar daemon badge, top status strip, inspector context, and notices
- a command palette for lifecycle, publish, config, panel, focus, and expanded-stream actions
- guided modals for add-task, add-idea, queue reorder, config edits, run detail, and publish confirmations
- read-only governance visibility in Overview plus run-detail compounding summaries for recent procedure/context-fact usage
- the same mailbox-safe daemon mutation rules used by the CLI

Useful TUI controls:

- `1` through `7` switch panels
- `s` focuses the sidebar, `c` focuses the active panel, and `Tab` or `Shift+Tab` cycles between shell regions
- `t` opens Add Task and `i` opens Add Idea
- `Ctrl+P` opens the command palette
- use the sidebar Mode toggle or command palette action to switch operator/debug views
- `e` toggles expanded mode, `Escape` exits it, and `l` jumps back to the live tail
- `?` opens keyboard help

Use the TUI when you want an always-on control panel. Use the CLI when you want scriptable JSON output or one-shot commands.

In shipped operator mode, the major panels are rendered as composed cards and structured lists rather than long pipe-delimited report text. Debug mode keeps the denser provenance-oriented detail.

Expanded mode follows the current display mode:

- `operator expanded` gives you a narrated runtime feed intended for continuous human monitoring
- `debug expanded` gives you the raw structured event stream, close to `millrace --config millrace.toml logs --follow`
- if you scroll upward, the feed stays in scrollback while new lines continue to append off-screen until you jump live again

## Normal Workflow

### 1. Preflight

```bash
millrace --config millrace.toml health --json
millrace --config millrace.toml doctor
```

Do not move on if `health` fails. It is the supported bootstrap and cutover check.

Do not move on if `doctor` fails. `health` tells you the workspace is scaffolded correctly; `doctor` tells you the configured execution stages can actually run with the external runner CLIs currently available on `PATH`.
That split is intentional: the default model ids are real packaged defaults, while runner availability remains an environment prerequisite.

### 2. Inspect State

```bash
millrace --config millrace.toml status --detail --json
millrace --config millrace.toml queue inspect --json
millrace --config millrace.toml research --json
millrace --config millrace.toml config show --json
millrace --config millrace.toml logs --tail 50 --json
```

Use these before touching files directly. In normal operation, the runtime is the authority for queue state, daemon state, status markers, and research state.

Execution `IDLE` is the execution plane's neutral state: no execution stage is active right now. It does not mean the daemon is stopped, and it does not imply research activity or queued work are absent elsewhere in the workspace.

In the TUI, use the Overview, Queue, Research, Logs, Runs, Config, and Publish panels for the same visibility without leaving the shell.

### 3. Add Work

```bash
millrace --config millrace.toml add-task "Example task"
millrace --config millrace.toml add-task "Example task" --body "# Notes"
millrace --config millrace.toml add-idea /absolute/path/to/idea.md
millrace --config millrace.toml queue reorder <task-id> <task-id> ...
```

Use `add-task` for execution backlog work. Use `add-idea` to feed research-side intake through `agents/ideas/raw/`.

When research is active, one raw idea follows a deterministic staged funnel: `goal_intake -> objective_profile_sync -> completion_manifest_draft -> spec_synthesis -> optional spec_interview -> spec_review -> taskmaster`, and `taskaudit` merges only when the current initial-family declaration is complete. A pending shard or prepared Taskaudit finalization record is healthy transitional handoff state for that family, not automatic fatal recycling. The completion manifest keeps governance artifacts separate from implementation surfaces and verification surfaces so the emitted queue spec, phase spec, and task cards stay product-grounded. Projects can also pin semantic milestones with `agents/objective/semantic_profile_seed.json`, `.yaml`, or `.yml`.

In `AUTO` mode, mixed-ready GoalSpec, incident, and audit queues follow deterministic family precedence and reevaluate after each defer boundary instead of silently draining a whole family inline. Queue-empty completion failures can also continue into marathon audit, goal-gap review, and bounded goal-gap remediation-family staging when semantic milestones remain unsatisfied.

In the TUI, use the Add Task and Add Idea modals. Queue reorder is available from the Queue panel.

### 3A. Resolve GoalSpec Interview Questions

When optional GoalSpec interview mode is enabled, research may pause after synthesis or during the integrated `spec_interview` stage with one durable pending question written under `agents/specs/questions/`. The runtime does not require a live external skill fetch here: the question, recommendation, and eventual resolution stay file-backed inside the workspace.

Use the CLI when you want explicit artifact-oriented control:

```bash
millrace --config millrace.toml interview list
millrace --config millrace.toml interview show <question-id>
millrace --config millrace.toml interview answer <question-id> --text "..."
millrace --config millrace.toml interview accept <question-id>
millrace --config millrace.toml interview skip <question-id> --reason "..."
```

Use the TUI when you want to stay inside the operator shell:

- open the Research panel
- select the pending interview question with the arrow keys
- press `Enter` to open the interview modal
- record an operator answer, accept the recommended answer, or skip the question

After the resolution is written, Millrace can resume the paused research progression on the next research pass or foreground cycle using the same file-backed state the CLI exposes.

### 4. Execute

```bash
millrace --config millrace.toml start --once
millrace --config millrace.toml start --daemon
```

`start --once` is the foreground single-pass path. If startup research sync creates new execution backlog while the execution queue was empty, that invocation stops after the research pass and leaves the new task in backlog for the next `start --once`. `start --daemon` is the long-running local runtime mode.

In the TUI, use the command palette or panel actions for the same lifecycle commands.

### 5. Control A Daemon

```bash
millrace --config millrace.toml pause
millrace --config millrace.toml resume
millrace --config millrace.toml stop
```

When the daemon is running, mutating commands become mailbox commands so the daemon stays the only live owner of runtime state.

The TUI follows the same rule. It does not mutate live daemon state directly.

## External Supervisor Workflow

Use this pattern when OpenClaw or another external supervisor harness is supervising a Millrace workspace. The supported compatibility flow is:

1. Poll one workspace through `millrace --config millrace.toml supervisor report --json`.
2. Decide whether to wait, message, escalate, or act based on `attention_reason`, `attention_summary`, and `allowed_actions` in the report.
3. If action is needed, use the supported supervisor-safe CLI surface with issuer attribution:

```bash
millrace --config millrace.toml supervisor pause --issuer <name> --json
millrace --config millrace.toml supervisor resume --issuer <name> --json
millrace --config millrace.toml supervisor stop --issuer <name> --json
millrace --config millrace.toml supervisor add-task "Example task" --issuer <name> --json
millrace --config millrace.toml supervisor queue-reorder <task-id> <task-id> ... --issuer <name> --json
millrace --config millrace.toml supervisor cleanup remove <task-id> --issuer <name> --reason "Invalid queued work" --json
millrace --config millrace.toml supervisor cleanup quarantine <task-id> --issuer <name> --reason "Needs operator follow-up" --json
```

Scheduling, messaging, wakeups, and multi-workspace registry remain external-harness concerns. The harness sets poll cadence and heartbeat policy; Millrace does not become the portfolio scheduler.

Optional adapters may translate supervisor reports or structured events into local wakeups, webhooks, or inbox delivery, but they stay at the edge. The TUI remains a local operator shell for one workspace; it is not the remote compatibility surface. Mailbox files remain runtime-owned. Do not write `agents/.runtime/commands/incoming/` directly during normal supervision.

### 6. Inspect Outcomes

```bash
millrace --config millrace.toml logs --follow
millrace --config millrace.toml run-provenance <run_id> --json
millrace --config millrace.toml research history --json
millrace --config millrace.toml compounding --json
millrace --config millrace.toml compounding orient --query builder
millrace --config millrace.toml compounding lint
millrace --config millrace.toml compounding facts --json
millrace --config millrace.toml compounding harness recommendations --json
```

Use:

- `logs` for the structured event stream
- `run-provenance` for one execution run's frozen plan, transitions, and evidence
- `research --json` and `research history` for research-side state and recent research events
- `compounding --json` for one compact governance summary across procedures, context facts, harness candidates, and recommendations
- `compounding orient` for a queryable secondary index and relationship summary over the governed stores
- `compounding lint` for explicit integrity checks before you trust governed reuse state
- `compounding facts` plus the existing compounding procedure/harness subcommands for artifact-level governed reuse inspection

In the TUI:

- the Logs panel tails and filters the event stream
- the Runs panel opens concise run detail backed by `run-provenance`, including governed procedure and context-fact usage when that run recorded it
- the Research panel surfaces queue, status, governance, and recent activity snapshots
- the Overview panel surfaces pending governed review work and the most recent governed knowledge usage summary without adding mutation controls

Use expanded mode when you want the live stream to dominate the shell body. Keep using the compact Logs panel when filters, selection, and run-detail handoff matter more than full-height output.

### 7. Publish

```bash
millrace --config millrace.toml publish sync --json
millrace --config millrace.toml publish preflight --json
millrace --config millrace.toml publish commit --no-push --json
millrace --config millrace.toml publish commit --push --json
```

Publish behavior:

- the staging repo defaults to `<workspace>/staging`
- the manifest defaults to `agents/staging_manifest.yml`
- `publish preflight` is read-only
- `publish commit` only pushes when `--push` is supplied

The TUI Publish panel exposes the same flow with explicit preflight refresh, sync, local commit, and higher-friction push confirmation.

## Config Behavior

Runtime config changes are validated first, then applied at one of four boundaries:

- `live_immediate`
- `stage_boundary`
- `cycle_boundary`
- `startup_only`

The config file may be watched by the daemon, but watched edits still go through the same reload and boundary rules. A file save is not an uncontrolled live mutation.

## Asset Resolution

Asset lookup is workspace-first:

- workspace files override packaged defaults when both exist
- packaged assets are the fallback when the workspace copy is absent
- additive families such as roles and skills can combine workspace and packaged entries
- `status --detail --json` and `config show --json` expose the active bundle and resolved prompt provenance

## Watched Inputs

By default the daemon watches:

- execution backlog and autonomy markers
- raw ideas under `agents/ideas/raw/`
- mailbox commands under `agents/.runtime/commands/incoming/`
- the config file `millrace.toml`

These roots and their debounce settings live under `[watchers]`.

## Read-Only Diagnosis

If the control surface is not enough, read these files without editing them manually:

- `agents/.runtime/state.json`
- `agents/engine_events.log`
- `agents/historylog.md`
- `agents/audit_history.md`
- `agents/audit_summary.json`
- `agents/tasks.md`
- `agents/tasksbacklog.md`
- `agents/tasksarchive.md`
- `agents/tasksblocker.md`
- `agents/tasksbackburner.md`
- `agents/status.md`
- `agents/research_status.md`

Manual file repair is outside the normal control path. Treat it as an exception, not routine operation.

## Verification

```bash
python3 -m compileall millrace_engine tests
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests
millrace --config millrace.toml health --json
millrace --config millrace.toml status --detail --json
```
