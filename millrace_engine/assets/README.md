# Millrace

Millrace is a local autonomous software-delivery runtime for long-running coding work. It runs governed execution and research loops inside a file-backed workspace with durable state, explicit recovery surfaces, per-run provenance, and publish controls.

It is built for unattended or semi-attended work that needs more than a chat session or a thin agent wrapper. You get one engine, one control plane, and multiple operator surfaces over the same on-disk truth: CLI, TUI, and agent-guided operation.

That same control plane also exposes an explicit one-workspace compatibility seam for OpenClaw-style supervisor agents: `supervisor report --json` for observation, plus issuer-attributed `supervisor ... --issuer <name>` actions for safe control.

## Install

```bash
python3 -m pip install millrace-ai
```

This installs the `millrace` command. Create a workspace with `millrace init ...`; `millrace.toml` and `agents/` live inside that initialized workspace, not at the public repo root.
If you need the exact `v0.10.0` release, install `millrace-ai==0.10.0`.

Fresh-workspace research is explicit by default: the shipped baseline config starts with `[research] mode = "stub"` and `interview_policy = "off"`. A new workspace still includes the research plane and its reporting surfaces, but first-run research only records deferred breadcrumbs until you deliberately reconfigure research to a non-stub mode.

It gives you:

- a Python CLI and a Textual TUI, plus an agent-facing advisor surface
- an explicit OpenClaw-compatible supervisor contract over the same CLI and JSON control plane
- a single runtime engine
- an execution plane for delivery work
- a research plane for idea intake, audit, and governed handoff
- durable runtime state, event logs, diagnostics, provenance, and publish surfaces inside the workspace

Read next:

- `OPERATOR_GUIDE.md` for the human operator workflow
- `ADVISOR.md` for the agent-facing operator prompt
- `docs/RUNTIME_DEEP_DIVE.md` for architecture and failure-model detail
- `docs/runtime/README.md` for the runtime boundary catalog
- `docs/runtime/recovery-failure-modes-timeouts-and-unwedge-playbook.md` for the supported timeout and unwedge playbook

`docs/RUNTIME_DEEP_DIVE.md` is intentionally a stable portal now, not a second monolith. Use `docs/runtime/README.md` when you need the full deep-doc map, `docs/runtime/control-plane-command-surface-and-mailbox-semantics.md` for mailbox-safe daemon mutation rules, and `docs/runtime/recovery-failure-modes-timeouts-and-unwedge-playbook.md` when the question is "what is the supported recovery step?"

Millrace ships real default model ids in its packaged config and model profiles. A fresh workspace starts from Codex/OpenAI defaults such as `gpt-5.3-codex` and `gpt-5.2`; they are not placeholder values. Execution still depends on local runner readiness, so treat `doctor` as the final check for `codex` availability and auth before `start --once`.

Native runner permissions are configured in `millrace.toml`. For Codex-backed stages, set `stages.<stage>.permission_profile` to `normal`, `elevated`, or `maximum` when a stage needs a wider sandbox than the default `--full-auto` profile.

## Why Millrace Exists

Interactive coding tools are good at short sessions. They are weak at long-running governed work that needs queue discipline, durable state, recoverable execution, research-to-delivery handoff, and publish controls.

Millrace exists to make those runtime concerns first-class instead of leaving them implicit in shell history, chat transcripts, or one-off wrapper scripts.

## How Millrace Is Different

- Runtime, not wrapper: Millrace owns lifecycle, queue mutation, watch surfaces, status, events, and publish flow.
- File-backed truth: runs, queues, status, diagnostics, and provenance live in the workspace, not in opaque process memory.
- Governed autonomy: execution and research share one engine but keep distinct state, reporting, and handoff surfaces.
- Frozen per-run plans: each run resolves its effective mode and loop, then records the exact plan used for that run.
- Multiple operator surfaces: CLI, TUI, and agent-guided operation all sit over the same control plane.

## Design Philosophy

- Local and inspectable: the workspace is the source of operational truth.
- Honest failure over hidden magic: blocked work, partial progress, and degraded state should stay legible.
- Explicit boundaries: lifecycle, runtime state, research, execution, and publish are separate product surfaces.
- Recoverability matters: long-running systems need durable ledgers, diagnostics, and restart-safe state.

## Repo Layout

The public Millrace repo is package-first. Its main surfaces are:

- `millrace_engine/`: the runtime package, CLI, control layer, and engine
- `docs/`: longer-form reference material, including the runtime deep dive and TUI reference
- `tests/`: unit and integration tests
- `pyproject.toml`: packaging metadata and console entrypoint
- `millrace_engine/assets/`: the packaged baseline bundle used by `millrace init`

`millrace.toml` and `agents/` are part of an initialized Millrace workspace. They are seeded by `millrace init`; they are not expected at the public repo root.

## Initialized Workspace Layout

An initialized workspace created by `millrace init /absolute/path/to/workspace` contains:

- `millrace.toml`: the active workspace config
- `agents/`: queues, runs, logs, prompts, state, and provenance
- `docs/`: copied reference material that travels with the workspace

## Core Runtime Model

- One CLI: `millrace ...` from the workspace root, or `millrace --config /absolute/path/to/workspace/millrace.toml ...` from elsewhere
- One module-equivalent CLI: `python3 -m millrace_engine ...` from the workspace root
- One TUI shell: `python3 -m millrace_engine.tui` from the workspace root
- One engine: the daemon owns lifecycle, watchers, mailbox commands, runtime state, and event emission.
- Two logical planes: execution work and research work share the same engine but keep separate status and reporting surfaces.
- File-backed truth: queues, runs, status, logs, diagnostics, and provenance live under `agents/`.
- Workspace-first assets: workspace files override packaged defaults when present; packaged assets are the fallback.
- Frozen per-run plans: before a standard execution run, Millrace resolves the selected mode and loop, applies workspace overrides, and freezes the effective plan under `agents/runs/<run_id>/`.

The runtime is also split by ownership internally, not just by product surface:

- `millrace_engine/engine.py` is the composition shell; config reload/apply lives in `engine_config_coordinator.py`, mailbox dispatch lives in `engine_mailbox_processor.py` plus `engine_mailbox_command_handlers.py`, and daemon loop/watcher coordination lives in `engine_runtime_loop.py`.
- `millrace_engine/planes/execution.py` keeps cycle composition while `planes/execution_flows/` owns the quickfix, QA, builder-success, and full-task cycle families.
- `millrace_engine/research/goalspec_stage_support.py` is a thin GoalSpec facade over per-stage executors such as `goalspec_goal_intake.py`, `goalspec_objective_profile_sync.py`, `goalspec_completion_manifest_draft.py`, `goalspec_spec_synthesis.py`, `goalspec_spec_interview.py`, and `goalspec_spec_review.py`, with shared rendering in `goalspec_stage_rendering.py`.
- `tools/repo_guardrails.py` enforces size budgets and same-change ratchets for orchestration-heavy files so those seams do not silently collapse back into long-lived exception zones.

## Governed Compounding Model

Millrace uses an explicit `raw -> compiled -> query -> lint` loop for governed compounding. It is not a transcript-memory or wiki-first subsystem.

- `raw`: the runtime starts from file-backed evidence it already owns, such as stage outputs, `agents/runs/<run_id>/transition_history.jsonl`, diagnostics bundles, and governed harness benchmark/search artifacts. Packaged `agents/skills` remain shipped operating playbooks, not runtime compounding authority.
- `compiled`: Millrace derives typed primary artifacts under `agents/compounding/`, including procedures, context facts, lifecycle records, harness candidates, benchmark results, and recommendations. Those governed artifacts carry scope, provenance, and lifecycle state; they are the source of truth.
- `query`: stage-aware retrieval injects only eligible governed procedures and context facts into the stages that allow them, under explicit budgets. Operators query the same stores through `millrace compounding ...`. `millrace compounding orient` also writes `agents/compounding/indexes/governed_store_index.json` and `agents/compounding/indexes/relationship_summary.json` as secondary orientation aids.
- `lint`: `millrace compounding lint` and the `health`/`doctor` surfaces validate stale governed artifacts, broken cross-artifact references, and stored-orientation drift. Fix the primary governed artifacts when lint fails; the orientation files are derived outputs, not the authority.

Millrace-specific boundary to remember:

- packaged `agents/skills` are the shipped operator/agent guidance surface
- transcript summaries are not promoted into runtime authority by default
- `Derived orientation surface only; governed compounding artifacts remain the source of truth.`

## Quick Start

Install the published package:

```bash
python3 -m pip install millrace-ai
```

For a version-pinned install of this release, use `python3 -m pip install millrace-ai==0.10.0`.

Create and inspect a workspace:

```bash
millrace init /absolute/path/to/workspace
cd /absolute/path/to/workspace
millrace health --json
millrace doctor
millrace status --detail --json
python3 -m millrace_engine.tui
```

If you prefer not to change directories, pass `--config /absolute/path/to/workspace/millrace.toml` explicitly.

Add work and execute one foreground cycle:

```bash
cd /absolute/path/to/workspace
millrace add-task "Example task"
millrace start --once
```

If you are developing Millrace itself from a source checkout instead of installing the package, use:

```bash
cd /absolute/path/to/millrace
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

Release verification is narrower than source-checkout contributor verification: release CI smoke-installs the built wheel into a clean virtualenv, runs `millrace init` against freshly generated workspaces, verifies `health --json`, verifies `doctor --json` under a controlled runner-prerequisite shim, proves one shimmed `start --once` execution pass, proves one configured non-stub research path, proves `supervisor report --json`, and proves `publish preflight --json` after syncing a local staging repo. Broader pytest coverage remains a source-checkout path with dev dependencies; it is not advertised as a full wheel or sdist contributor-test gate.

## TUI Shell

The TUI is the fastest way to operate an initialized workspace when you want a dense control panel instead of a stream of CLI calls.

Launch it with:

```bash
python3 -m millrace_engine.tui
```

What it provides:

- a startup health gate before the shell becomes interactive
- a dual-mode shell with summary-first `operator` mode and detail-forward `debug` mode
- one toggleable expanded stream mode that takes over the main content area while leaving the sidebar, status strip, inspector, notices, and footer discovery surfaces visible
- a persistent shell with sidebar navigation, a compact status strip, a right-hand inspector, a real footer, and widget-composed overview/queue/runs/research/logs/config/publish panels
- explicit lifecycle signaling across the sidebar daemon badge, top status strip, inspector context, and notices rail
- guided task, idea, queue cleanup/reorder, research interview, config edit, and publish confirmation flows
- lifecycle actions for `start --once`, `start --daemon`, `pause`, `resume`, and `stop`
- a command palette for common actions, including display-mode toggle, lifecycle controls, focus movement, and expanded-stream actions
- built-in keyboard help toggle (`?`) and panel shortcuts (`1` through `7`)
- read-only governance visibility for recent compounding knowledge usage and pending review items
- concise run-detail drilldown that now includes procedure and context-fact usage summaries when present
- the same runtime semantics as the CLI, including mailbox-safe daemon mutations and the same file-backed truth surfaces

Use the TUI for day-to-day observation and control. Use the CLI when you want scriptable, one-shot commands or JSON output.

The repo also ships a dedicated TUI reference in its `docs/` directory for the full panel, control, and interaction map.

Expanded stream behavior to know:

- press `e` to toggle the active panel into expanded mode and `Escape` to return to the normal panel body
- in `operator` mode, expanded view renders a narrated activity feed from runtime events
- in `debug` mode, expanded view renders the raw structured event lines, close to `logs --follow`
- if you scroll upward, live follow disengages until you jump back with `l` or the command palette action

## Start A Fresh Workspace

Use `init` when you want a new Millrace workspace without copying files by hand:

```bash
millrace init /absolute/path/to/workspace
millrace init --force /absolute/path/to/workspace
cd /absolute/path/to/workspace
millrace upgrade
millrace upgrade --apply
```

Important behavior:

- Without `--force`, the destination must be absent or empty.
- With `--force`, Millrace overwrites manifest-tracked baseline files in place. It does not wipe the directory.
- `millrace upgrade` previews both the manifest-tracked baseline refresh and any supported persisted-state migrations for an existing workspace without modifying files.
- `millrace upgrade` also reports any missing runtime-owned bootstrap paths that will be materialized, such as newer `agents/compounding/...` and `agents/lab/...` family directories or starter workspace files.
- `millrace upgrade --apply` writes the manifest-tracked baseline refresh, materializes those missing runtime-owned bootstrap paths, and applies supported persisted-state migrations in place while preserving existing runtime-owned and operator-owned artifacts.
- The current persisted-state migration seam is explicit and inspectable: when needed, upgrade canonicalizes or materializes `agents/research_state.json` from the supported research-state helpers instead of relying on silent fallback or manual deletion.
- Use `upgrade` and `upgrade --apply` for supported workspace evolution; use `init --force` only when you intentionally want the scaffold write path.
- Workspace prompt and registry overlays can override packaged defaults after scaffolding.
- Missing workspace prompt files fall back to packaged assets when the resolver supports that family.

After scaffolding, run the workspace preflight before you rely on the runtime:

```bash
millrace health --json
millrace doctor
```

`health` confirms bootstrap/config/assets truth. `doctor` is the execution-readiness check that tells you whether required external runner CLIs such as `codex` are available before `start --once`.
The shipped model ids in that scaffold are real defaults, but `doctor` is still the command that tells you whether the current machine can actually execute them.
Those preflight surfaces also echo the active research bootstrap contract so you can see that fresh-workspace research is stubbed by default instead of assuming GoalSpec, incident, or audit automation is already active.

## Daily Operator Flow

The rest of this section assumes you are operating inside an initialized workspace root that contains the active `millrace.toml`.

1. Preflight the workspace:

```bash
millrace health --json
millrace doctor
```

Or launch the TUI and let the health gate run automatically:

```bash
python3 -m millrace_engine.tui
```

2. Inspect current state:

```bash
millrace status --detail --json
millrace queue inspect --json
millrace research --json
millrace logs --tail 50 --json
```

In the TUI, the Overview, Queue, Research, Logs, and Runs panels expose the same runtime state without leaving the shell.

Execution `IDLE` is the execution plane's neutral state: no execution stage is active right now. It does not mean the daemon is stopped, and it does not mean research activity or queued work are absent elsewhere in the workspace.

3. Add work:

```bash
millrace add-task "Example task"
millrace add-idea /absolute/path/to/idea.md
```

`add-task` appends an execution task to the backlog. `add-idea` copies a source markdown file into `agents/ideas/raw/` for research-side intake.

When research is active, one raw idea follows a deterministic staged funnel: `goal_intake -> objective_profile_sync (begins with inline Contractor classification) -> completion_manifest_draft -> spec_synthesis -> optional spec_interview -> spec_review -> taskmaster`, and `taskaudit` merges only when the current initial-family declaration is complete. The shipped Contractor bundle is packaged scaffolding, not a separate top-level stage: `agents/_contractor.md` runs inline at the start of `objective_profile_sync`, `agents/_completion_manifest_draft.md` remains the dedicated completion-manifest draft asset, and the Contractor guidance surface also ships `agents/objective/contractor_profile.schema.json`, `agents/objective/contractor_profile.example.json`, and `agents/skills/contractor-classification/`. `spec_synthesis` can now realize the full bounded initial-family cap instead of hard-clamping broad goals to three siblings, and frozen family plans can preserve sibling-specific decomposition profiles. `spec_review` is a bounded one-spec agentic review/edit pass followed by a deterministic structural validator before Taskmaster promotion. A pending shard or prepared Taskaudit finalization record is healthy transitional handoff state for that family, not automatic fatal recycling. The completion manifest keeps governance artifacts separate from implementation surfaces and verification surfaces so the emitted queue spec, phase spec, and task cards stay product-grounded. Projects can also pin semantic milestones with `agents/objective/semantic_profile_seed.json`, `.yaml`, or `.yml`.

In `AUTO` mode, mixed-ready GoalSpec, incident, and audit queues follow deterministic family precedence and reevaluate after each defer boundary instead of silently draining a whole family inline. Blocked GoalSpec review can first emit a remediation bundle, retry through bounded local Mechanic repair for the same structural failure signature, and then stage a separate GoalSpec-owned `goal_gap_remediation` family if local repair exhausts. Queue-empty completion failures can also continue into marathon audit, goal-gap review, and bounded audit-owned goal-gap remediation-family staging when semantic milestones remain unsatisfied.

If GoalSpec interview mode is enabled, research can pause after synthesis with one durable pending interview question under `agents/specs/questions/`. The feature is optional and file-backed: Millrace records the question and recommended answer on disk, then waits for operator input before continuing review/task decomposition.

Resolve interview pauses with either surface:

```bash
millrace interview list
millrace interview show <question-id>
millrace interview answer <question-id> --text "..."
millrace interview accept <question-id>
millrace interview skip <question-id> --reason "..."
```

Or open the TUI Research panel, select the pending interview question, and press `Enter` to answer it, accept the recommended answer, or skip it from the modal workflow.

4. Run the engine:

```bash
millrace start --once
millrace start --daemon
```

`start --once` is a foreground single pass, not a guaranteed full research-plus-execution roundtrip. If startup research sync creates new execution backlog while the execution queue was empty, that invocation stops after the research pass; run `start --once` a second time to execute the newly generated task.

The TUI exposes the same actions through the command palette and keyboard-driven panel flows.

5. Inspect outcomes:

```bash
millrace logs --follow
millrace run-provenance <run_id> --json
millrace research history --json
millrace compounding --json
millrace compounding orient --query builder
millrace compounding lint
millrace compounding facts --json
millrace compounding procedures --json
millrace compounding harness recommendations --json
```

The TUI keeps recent runs, filtered logs, and concise run-provenance drilldown in one place, including a run-detail modal from the Runs and Logs panels.
The Overview panel also surfaces a compact governance card for pending compounding review work and the most recent governed knowledge usage visible from local run artifacts.

When you want the old foreground-feed feel without leaving the shell, use TUI expanded mode instead of opening a second terminal just to run `logs --follow`.

6. Control a long-running daemon:

```bash
millrace pause
millrace resume
millrace stop
```

When the daemon is running, mutating commands route through the mailbox so one owner process stays in control of live state.

## External Supervisor Workflow

Millrace supports explicit OpenClaw and external-supervisor compatibility one workspace at a time through a CLI-first, JSON-first contract. An OpenClaw Supervisor agent or similar harness should start with:

```bash
millrace --config millrace.toml supervisor report --json
```

That report is the supported first-pass decision surface for one workspace. It collapses health, readiness, runtime status, research status, queue depth, recent events, and machine-readable attention reasons into one payload without requiring raw runtime-file synthesis. External harnesses should treat `attention_reason`, `attention_summary`, and `allowed_actions` as the supported machine-readable trigger for wait/message/escalate/act decisions.

When local Sentinel is enabled, that same `supervisor report --json` payload also exposes a compact `sentinel` summary sourced from persisted Sentinel artifacts. External harnesses can read that one-workspace monitor state from the report, while the local TUI Overview and inspector surfaces render the same summary for operators.

If the harness decides action is needed, use the supported supervisor-safe CLI path with explicit issuer attribution:

```bash
millrace --config millrace.toml supervisor pause --issuer <name> --json
millrace --config millrace.toml supervisor resume --issuer <name> --json
millrace --config millrace.toml supervisor stop --issuer <name> --json
millrace --config millrace.toml supervisor add-task "Example task" --issuer <name> --json
millrace --config millrace.toml supervisor queue-reorder <task-id> <task-id> ... --issuer <name> --json
millrace --config millrace.toml supervisor cleanup remove <task-id> --issuer <name> --reason "Invalid queued work" --json
millrace --config millrace.toml supervisor cleanup quarantine <task-id> --issuer <name> --reason "Needs operator follow-up" --json
```

Scheduling, messaging, wakeups, and multi-workspace registry stay outside Millrace core. Millrace owns one-workspace runtime truth, event history, and safe action semantics; the external harness owns cadence, heartbeat policy, portfolio ordering, and outbound communication.

Optional adapters may translate `supervisor report`, research/status surfaces, or structured events into wakeups, webhooks, or inbox items, but they remain edge layers over Millrace-owned truth. This keeps the OpenClaw/Supervisor compatibility seam attributable and file-safe. Do not write `agents/.runtime/commands/incoming/` or other engine-owned runtime files directly during normal supervision.

## Sentinel Companion Monitor

Millrace also ships a local one-workspace Sentinel companion monitor layered over the same runtime and supervisor truth. The supported local surface is:

```bash
millrace --config millrace.toml sentinel check --json
millrace --config millrace.toml sentinel status --json
millrace --config millrace.toml sentinel watch --json
millrace --config millrace.toml sentinel acknowledge --issuer <name> --reason "..." --json
millrace --config millrace.toml sentinel incident --failure-signature <token> --summary "..." --json
millrace --config millrace.toml recovery request troubleshoot --issuer <name> --reason "..." --force-queue --json
```

Sentinel persists its own bounded monitor artifacts under `agents/.runtime/sentinel/` and `agents/reports/sentinel/`, exports a machine-readable summary through `supervisor report --json`, and surfaces that same summary in the local TUI. This repo does not ship a hosted `live.millrace.ai` dashboard or multi-workspace Sentinel portfolio supervisor; external harnesses consume the exported one-workspace reports instead.

## Publish And Staging

Millrace includes a staging and publish surface for preparing a release worktree:

```bash
millrace publish sync --json
millrace publish preflight --json
millrace publish commit --no-push --json
```

`publish preflight` is read-only. It reports the resolved staging repo, manifest source, git readiness, and changed paths without mutating git state.

The TUI Publish panel uses that same staging workflow. It acts on the resolved staging repo and explains blocked commit versus blocked push states separately so operators can see the next safe action before starting a publish flow.

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
