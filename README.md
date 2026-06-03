<div align="center">
  <p>
    <a href="https://pypi.org/project/millrace-ai/"><img alt="PyPI" src="https://img.shields.io/pypi/v/millrace-ai.svg"></a>
    <a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11+-blue.svg"></a>
    <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/tim-osterhus/millrace.svg"></a>
  </p>
  <img
    src="docs/assets/images/millrace-icon-signal-transparent-glow.png"
    alt="Millrace signal mark"
    width="180"
  />
  <h1>Millrace</h1>
  <p><strong>Millrace is a local runtime for governed, long-running agentic workflows.</strong></p>
</div>

Millrace is defined primarily by two core ideas. The first is that sequential orchestration is superior to parallelization in terms of long-running autonomy, reliability, efficiency, and simplicity. Since every additional agent decreases efficiency and increases overhead, why not focus on maximizing the capabilities of that first one?

The second core idea is that the runtime owns the project state, not the agent. Millrace compiles workflow graphs, runner bindings, stage contracts, recovery rules, approvals, and closure behavior into one inspectable plan. A small daemon dispatches coding agents through that plan, applies their results through runtime-owned rules, and persists the evidence needed to resume, repair, inspect, or close the work later.

Status: Millrace is pre-1.0 and maintained. The current `0.20.x` line is still stabilizing, so pin patch versions when behavior matters.

```bash
pip install millrace-ai
millrace init --workspace /path/to/workspace
millrace compile validate --workspace /path/to/workspace
millrace run daemon --max-ticks 1 --workspace /path/to/workspace
millrace status --workspace /path/to/workspace
```

For the full system explanation, read `docs/millrace-technical-overview.md`.

## How Millrace Is Different

Millrace is not another chat UI, coding harness, or graph library. It is the local runtime layer that decides what agent work is allowed to run, what state is durable, how recovery happens, and when the work can honestly close.

| Compared with | What that tool is good at | What Millrace adds or changes |
| --- | --- | --- |
| [Claude Code](https://code.claude.com/docs/en/overview) | A coding agent that reads a repo, edits files, runs commands, and works from surfaces such as terminal, IDE, desktop, and web. | Millrace is not the coding model or chat surface. It wraps stage work in durable queues, compiled plans, restartable daemon state, operator controls, and evidence-backed closure. |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | A low-level framework and runtime for building long-running, stateful agents and custom orchestration graphs. | Millrace ships an opinionated software-work runtime: Planning, Execution, optional Learning, queue intake, CLI operations, run artifacts, approval gates, and Arbiter closure are already part of the product. |
| [Archon](https://archon.diy/) | A workflow engine for packaging AI coding workflows as YAML and running them across tools and channels. | Millrace is less about portable workflow recipes and more about runtime truth inside a managed workspace: one compiled plan, one daemon-owned mutation path, durable recovery, and inspectable completion state. |

Use a direct coding agent when a single session is enough. Use Millrace when the work needs a real runtime around it.

## What Millrace Does

Millrace turns large AI-assisted software work into explicit runtime state:

- **Queue intake:** tasks, specs, probes, incidents, ideas, learning requests, approvals, and operator commands enter through supported files or CLI commands.
- **Compiled plans:** the selected mode, graph assets, runtime config, stage bindings, recovery policy, and completion behavior compile into a persisted plan before work runs.
- **Stage execution:** a daemon claims one eligible stage at a time, hands the stage a contract, records artifacts, and routes the result through runtime-owned logic.
- **Recovery:** retryable environmental failures can requeue through audited paths; real blocked states stay visible until an operator or recovery stage handles them.
- **Closure:** work does not close because an agent says it is done. Arbiter closure runs only when the relevant lineage has no queued, active, or blocked work left.
- **Inspection:** status, monitor output, run artifacts, traces, compile diagnostics, and CLI commands make the runtime state visible after each handoff.

The base package is intentionally local and lightweight. Optional surfaces, such as the read-only dashboard, ship separately.

## The Runtime Model

Millrace runs three kinds of work planes:

- **Planning:** turns specs, probes, and incidents into bounded executable work or remediation.
- **Execution:** builds, checks, fixes, double-checks, escalates, and updates task work.
- **Learning:** optionally reviews runtime evidence, prepares remote optional skills after Planner, and curates accepted skill improvements.

The default modes keep Planning and Execution serial. Learning-enabled modes can run one Learning stage alongside one foreground Planning or Execution stage. Runtime-owned mutation remains serialized by the daemon.

Current shipped modes include standard Codex and Pi modes, learning-enabled modes, opt-in Integrator quality modes, Blueprint Planning modes, and `efficient_learning_mixed`, which uses standard Learning topology with a mode-local mixed Codex/Pi model profile. See `docs/runtime/millrace-modes-and-loops.md` and `docs/graphs/graphs-index.md` for the exact mode and graph matrix.

Millrace supports custom graph nodes and custom stage kinds over canonical
runtime stages. Workspace-local assets for modes, graphs, stage kinds, and
entrypoints can declare new node types without altering the core package.
Arbitrary runtime stages (stage kinds without a declared `runtime_stage`) are
not yet supported. See `docs/runtime/millrace-modes-and-loops.md` for the
authoritative stage-kind and graph-node contract.

## First Useful Run

Create or choose a workspace, then initialize and inspect it:

```bash
export WORKSPACE=/absolute/path/to/your/workspace

millrace init --workspace "$WORKSPACE"
millrace compile validate --workspace "$WORKSPACE"
millrace compile show --workspace "$WORKSPACE"
millrace compile graph --workspace "$WORKSPACE"
```

Run one deterministic daemon tick:

```bash
millrace run daemon --max-ticks 1 --workspace "$WORKSPACE"
millrace status --workspace "$WORKSPACE"
```

Run a visible daemon session:

```bash
millrace run daemon --monitor basic --workspace "$WORKSPACE"
```

The default daemon is quiet. `--monitor basic` prints a compact human-facing stream and throttles repeated `idle reason=no_work` lines to a long heartbeat so idle daemons do not flood logs.

## Optional Dashboard

`millrace-web` is a separate package. It serves a read-only local dashboard with Detail and Flow views over one or more workspaces.

```bash
pip install millrace-web
millrace-web serve --workspace "$WORKSPACE"
```

The dashboard observes workspace state. It does not own the daemon, mutate queues, or replace CLI control commands.

## Operator Surface

Common commands:

| Need | Command |
| --- | --- |
| Initialize managed workspace assets | `millrace init --workspace <workspace>` |
| Preview or apply packaged asset updates | `millrace upgrade --workspace <workspace>` / `millrace upgrade --apply --workspace <workspace>` |
| Validate the selected compiled plan | `millrace compile validate --workspace <workspace>` |
| Inspect compiled node bindings | `millrace compile show --workspace <workspace>` |
| Export the compiled graph | `millrace compile graph --workspace <workspace>` |
| Inspect daemon state | `millrace status --workspace <workspace>` |
| Run the daemon | `millrace run daemon --workspace <workspace>` |
| Inspect queue state | `millrace queue ls --workspace <workspace>` |
| Inspect runs | `millrace runs ls --workspace <workspace>` / `millrace runs show <run_id> --workspace <workspace>` |
| Pause, resume, or stop a live daemon | `millrace control pause/resume/stop --workspace <workspace>` |
| Manage approval-gated capabilities | `millrace approvals ls/show/approve/deny --workspace <workspace>` |
| Manage optional skills | `millrace skills ls/search/install --workspace <workspace>` |

The full CLI reference is `docs/runtime/millrace-cli-reference.md`.

## Early Proof

Millrace's strongest early public proof is self-referential: Python `millrace-ai` drove the first released Rust parity implementation of Millrace.

That campaign used Python `millrace-ai` in `learning_codex` mode to move from seeded parity ideas through planning, execution, QA, Arbiter closure, remediation, and release-ready state. After the operator started the daemon, there were no pause/resume cycles, continuation prompts, or external code interventions. Publication to GitHub and crates.io happened after the completed workspace state was produced.

Headline evidence from the run:

| Metric | Value |
| --- | ---: |
| Seeded parity slices | `8` |
| Completed specs | `11` |
| Completed tasks | `57` |
| Recorded runs | `99` |
| Recorded stage results | `261` |
| Resolved incidents/remediations | `5` |
| Wall-clock campaign span | `28h 9m 49.5s` |
| Input plus output tokens | `730,406,757` |
| Cached-input share | `95.47%` |
| Release tag | `v0.1.0` |

The caveat matters: this proves Python Millrace could autonomously build the Rust parity runtime. It does not prove that every project can be completed without operator judgment.

Full evidence pack: [millrace-rs-port-docs](https://github.com/tim-osterhus/millrace-rs-port-docs)

## When To Use It

Use Millrace when:

- the work will outlast one agent session;
- you need explicit stage gates, not informal chat conclusions;
- recovery, restartability, and queue state matter;
- you want run artifacts under `<workspace>/millrace-agents/`;
- completion needs a closure pass with evidence;
- an operator or ops agent is managing intake and runtime control.

Do not use Millrace when:

- the task is small enough for one direct coding session;
- raw iteration speed matters more than durable state;
- the work is pure exploration;
- nobody will manage workspace setup, config, approvals, or queue hygiene.

## Read Next

Start here:

- `docs/millrace-technical-overview.md`: the dense system explainer.
- `docs/doc-index.md`: the documentation map.
- `docs/runtime/README.md`: runtime docs by topic.
- `docs/runtime/millrace-cli-reference.md`: supported commands.
- `docs/runtime/millrace-workspace-baselines-and-upgrades.md`: init and upgrade behavior.

Understand the architecture:

- `docs/runtime/millrace-compiler-and-frozen-plans.md`
- `docs/runtime/millrace-runtime-architecture.md`
- `docs/runtime/millrace-modes-and-loops.md`
- `docs/graphs/graphs-index.md`
- `docs/runtime/millrace-arbiter-and-completion-behavior.md`

Operate it as an agent:

- `docs/skills/millrace-ops-agent-manual/SKILL.md`
- `docs/skills/millrace-autonomous-delegation/SKILL.md`

## License

Millrace is licensed under Apache-2.0. See `LICENSE`.
