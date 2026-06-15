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
  <p><strong>Millrace is a local loop engineering/runtime framework for governed, long-running agentic workflows.</strong></p>
</div>

Millrace is for operators and engineers who want agent work to run from a
clear loop contract instead of an open-ended chat transcript. It turns a folder
into a managed workspace with durable queues, bounded stage requests, recovery
rules, operator controls, and inspectable evidence that survives any one
harness session.

The runtime owns the project state, not the agent. Millrace compiles workflow
graphs, runner bindings, stage contracts, recovery rules, approvals, and
completion behavior into one inspectable plan. A small daemon dispatches agents
through that plan, applies their results through runtime-owned rules, and keeps
the evidence needed to resume, repair, inspect, or finish the loop later.

That makes Millrace a runtime layer, not a replacement for a coding harness.
Tools such as Claude Code, Codex, and Pi can already edit repositories, run
commands, call tools, use hooks or skills, keep session context, and delegate
side work. Millrace's job is different: it decides which bounded stage is
allowed to run next, gives that stage a compiled contract, records the result,
and keeps durable workspace state outside any one chat or terminal session.

Status: Millrace is pre-1.0 and maintained. The current `0.21.x` line is still
stabilizing, so pin minor or patch versions when behavior matters.

Compatibility note: current `0.21.x` builds expect modern compiled-plan
metadata. Older workspaces may need `millrace upgrade --apply`, a fresh
`millrace init`, or reinitialization before daemon startup will validate.

```bash
pip install millrace-ai
millrace init --workspace /path/to/workspace
millrace compile validate --workspace /path/to/workspace
millrace run daemon --max-ticks 1 --workspace /path/to/workspace
millrace status --workspace /path/to/workspace
```

For the full system explanation, read `docs/millrace-technical-overview.md`.
For the human operating model, read `QUICKSTART.md`.

## How Millrace Is Different

Millrace is not another chat UI, coding harness, or graph library. It is the
local runtime layer that decides what agent work is allowed to run, what state
is durable, how recovery happens, and how a workflow records that it is done.

The comparison is about layers, not whether another tool has useful agent
features. A direct coding agent can run tests, react to failure messages,
invoke hooks, spawn subagents, and remember project instructions. Millrace adds
an external runtime owner: the compiled plan, queue lineage, daemon scheduler,
operator controls, and workflow evidence live in the workspace and remain
inspectable after a single agent context ends.

| Compared with | What that tool is good at | What Millrace adds or changes |
| --- | --- | --- |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) | A full coding harness: repo editing, shell/tool use, IDE and terminal surfaces, hooks, skills, subagents, memory, and conversation history. | Millrace is not the coding model or chat surface. It sits above harness runs with durable queues, compiled plans, a restartable daemon, runtime-owned mutation, operator controls, and evidence-backed workflow state. The compiled plan, not the model's next self-directed step, owns stage routing. |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | A low-level framework and runtime for building long-running, stateful agents and custom orchestration graphs. | Millrace ships a local workspace runtime with included workflow configurations, queue intake, CLI operations, run artifacts, approval gates, and recovery rules. LAD is the best-tested shipped workflow family, but it is not the whole runtime. |
| [Archon](https://archon.diy/) | A workflow engine for packaging AI coding workflows as YAML and running them across tools and channels. | Millrace is less about portable workflow recipes and more about runtime truth inside a managed workspace: one compiled plan, one daemon-owned mutation path, durable recovery, and inspectable completion state. |

Use a direct coding agent when one session and its built-in workflow features
are enough. Use Millrace when the work needs an external runtime around it: a
durable queue, predeclared stage contracts, graph-selected recovery, visible
operator intervention, and workflow state with receipts.

## What Millrace Does

Millrace turns a governed agentic workflow into explicit runtime state:

- **Queue intake:** tasks, specs, probes, incidents, ideas, learning requests, approvals, and operator commands enter through supported files or CLI commands.
- **Compiled plans:** the selected mode, graph assets, runtime config, stage
  bindings, scheduler authority, selected recovery policies, and completion
  behavior compile into a persisted plan before work runs.
- **Stage execution:** a daemon claims one eligible stage at a time, hands the stage a contract, records artifacts, and routes the result through runtime-owned logic.
- **Recovery:** retryable environmental failures can requeue through audited paths; real blocked states stay visible until an operator or recovery stage handles them.
- **Completion rules:** each workflow can define how finished work is checked and recorded. LAD uses Arbiter-style completion because software tasks benefit from a final evidence check, but other loops can use different rules.
- **Inspection:** status, monitor output, run artifacts, traces, compile diagnostics, and CLI commands make runtime state visible after each handoff.

The base package is intentionally local and lightweight. Runtime observation is
available through CLI/status/run/trace surfaces, and the optional
`millrace-web` sidecar provides a separate read-only local dashboard without
adding web dependencies to `millrace-ai`.

```bash
pip install millrace-web
millrace-web serve --workspace /path/to/workspace --view flow
```

## The Runtime Model

Millrace runs three kinds of work planes:

- **Planning:** turns specs, probes, and incidents into bounded executable work or remediation.
- **Execution:** builds, checks, fixes, double-checks, escalates, and updates task work.
- **Learning:** optionally reviews runtime evidence, prepares remote optional skills after Planner, and curates accepted skill improvements.

The shipped LAD modes keep Planning and Execution serial. Learning-enabled LAD
modes can run one Learning stage alongside one foreground Planning or Execution
stage. Runtime-owned mutation remains serialized by the daemon.

## Included Workflow Configurations

Millrace ships with LAD as a first-class supported workflow, but LAD is
separate from Millrace itself. Millrace is the runtime; LAD is one workflow
family that runs on it.

The current shipped workflow configurations are:

- **LAD:** the main and most tested software-development workflow family. It
  uses Planning and Execution planes, with Arbiter-style completion for final
  evidence checks.
- **LAD variants:** Codex and Pi runner profiles, Learning-enabled modes,
  Integrator modes for higher-assurance execution, and
  `efficient_learning_lad_mixed` for a mixed Codex/Pi profile.
- **Blueprint-style Planning:** a structured Planning variant for longer agent
  and harness work. It was inspired by a conversation with an Anthropic
  engineer about long-running agents and agent harnesses. Current Blueprint
  modes keep LAD Execution and replace the Planning graph.

Planned shipped-by-default work:

- **Auto-Lab:** a future configuration for more automated experiment and
  evaluation loops. It is planned work, not current stable runtime behavior.

See `docs/runtime/millrace-modes-and-loops.md` and
`docs/graphs/graphs-index.md` for the exact mode and graph matrix. See
`docs/runtime/millrace-runtime-architecture.md` for the runtime model behind
those configurations.

Millrace supports custom graph nodes and custom stage kinds over canonical
runtime stages. Workspace-local assets for modes, graphs, stage kinds, and
entrypoints can declare new node types without altering the core package.
Those custom behaviors are active only after their metadata compiles into the
selected plan; runtime kernel code does not infer custom behavior from stage
names, work-family ids, or path substrings.

Extension-backed domains (Recon, closure, Blueprint, and Learning) ship with
the base runtime but are activated only when the selected mode declares their
required extensions. The minimal and generic fixture configs prove the kernel
runs without those domains. Active-kernel code dispatches domain behavior
through compiled extension boundary interfaces; it does not branch on domain
string literals or family-ID enums.

Compatibility surface:

- Package-root modules (`router.py`, `compiler.py`, `queue_store.py`,
  `runner.py`, `paths.py`, `state_store.py`, `stage_kinds.py`,
  `loop_graphs.py`) remain importable as compatibility facades but derive
  active runtime authority from the compiled plan.
- `standard_plain`, `standard_millrace`, and `learning_enabled_millrace` are
  compatibility aliases for `lad_codex`, `lad_pi`, and `learning_lad_pi`.
  Legacy `default_*`, old unqualified `learning_*`, and old
  `efficient_learning_mixed` mode names are compatibility names for the LAD
  mode family, not recommended names for new configs.

Unsupported topologies:

- Arbitrary plane IDs and arbitrary runtime stages (stage kinds without a
  declared canonical `runtime_stage`) are not yet supported.
- True single-plane mode configurations are deferred; the current mode
  validator requires at least two planes.
- The fixture-only `minimal_three_plane`, `recovery_heavy_millrace`, and
  `generic_two_plane_fixture` modes are discoverable proof assets that are
  not listed as shipped product mode IDs.

See `docs/runtime/millrace-modes-and-loops.md` and
`docs/graphs/graphs-index.md` for the authoritative stage-kind, graph-node,
and config-mapping contracts.

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

The default daemon is quiet. `--monitor basic` prints a compact human-facing stream and throttles repeated `idle reason=no_work` lines to a long heartbeat so idle daemons do not flood logs. Durable `runtime_tick_idle` records are throttled separately and default to a 6-hour heartbeat.

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

## If You Are An AI Agent

Many users will ask an AI agent to operate Millrace for them. If that is your
role, treat Millrace as an operator-controlled runtime, not as a background
detail.

Start with:

- `docs/skills/millrace-ops-agent-manual/SKILL.md`
- `docs/skills/millrace-autonomous-delegation/SKILL.md`
- `docs/runtime/README.md`
- `docs/runtime/millrace-cli-reference.md`

Keep the daemon in a durable terminal session when the run should outlast the
current chat. Report workspace state, approvals, blockers, and evidence instead
of only summarizing your own actions.

## Early Proof

Millrace's strongest early public proof is self-referential: Python `millrace-ai` drove the first released Rust parity implementation of Millrace.

That campaign used Python `millrace-ai` in `learning_lad_codex` mode to move from seeded parity ideas through planning, execution, QA, Arbiter closure, remediation, and release-ready state. After the operator started the daemon, there were no pause/resume cycles, continuation prompts, or external code interventions. Publication to GitHub and crates.io happened after the completed workspace state was produced.

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
- the workflow needs an explicit completion check with evidence;
- an operator or ops agent is managing intake and runtime control.

Do not use Millrace when:

- the task is small enough for one direct coding session;
- raw iteration speed matters more than durable state;
- the work is pure exploration;
- nobody will manage workspace setup, config, approvals, or queue hygiene.

## Read Next

Start here:

- `QUICKSTART.md`: the high-level human operator guide.
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
