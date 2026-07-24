<div align="center">
  <p>
    <a href="https://pypi.org/project/millrace-ai/"><img alt="PyPI v0.22.0" src="https://img.shields.io/badge/pypi-v0.22.0-C75A2A.svg"></a>
    <a href="https://www.python.org/downloads/"><img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12+-blue.svg"></a>
    <a href="https://github.com/tim-osterhus/millrace/blob/v0.22.0/LICENSE"><img alt="License" src="https://img.shields.io/github/license/tim-osterhus/millrace.svg"></a>
  </p>
  <img
    src="https://raw.githubusercontent.com/tim-osterhus/millrace/v0.22.0/docs/assets/images/millrace-icon-signal-transparent-glow.png"
    alt="Millrace signal mark"
    width="180"
  />
  <h1>Millrace</h1>
  <p><strong>Compiler-governed workflow runtime for durable AI automation.</strong></p>
</div>

Millrace is a graph-driven runtime that executes long-running AI workflows using compiler-validated authority rather than prompt conventions.

Instead of letting an agent decide what happens next, Millrace compiles a workflow into an immutable execution plan and enforces state transitions, recovery, approvals, capabilities, and operator intervention throughout the lifetime of a run.

It is not another agent harness.

## Agent-Native By Design

Millrace is designed to be installed, configured, and operated through an AI
agent acting as the intermediary between you and the runtime. You describe the
work and its constraints; the agent uses Millrace's deterministic CLI and JSON
surfaces to configure the workflow, enqueue work, run and monitor the daemon,
and bring approvals, blockers, and evidence back to you.

Manual operation is fully supported, but the CLI favors explicit, replay-safe
machine operation over interactive convenience. It is not intended to be
Millrace's primary human-facing interface.

For agent-led setup and operation, give the agent the
[Millrace instruction manual](https://github.com/tim-osterhus/millrace-plus/blob/v0.22.0/src/millrace_plus/skills/millrace-instruction-manual/SKILL.md).

## Install

To install the full distribution directly:

```bash
python -m pip install millrace
millrace --version
```

The bundle installs the `millrace-ai` runtime, official `millrace-plus`
workflows and authoring skills, and Millforge. Install `millrace-ai`
directly when you need only the generic runtime and its diagnostic
`kernel_ping` workflow. The complete `millrace` bundle requires Python 3.12
or newer; the base `millrace-ai` runtime supports Python 3.11 or newer.

## Start A Workspace

```bash
export WORKSPACE=/absolute/path/to/new-workspace

millrace --workspace "$WORKSPACE" workspace init \
  --input-id workspace-init-001
millrace --workspace "$WORKSPACE" workspace check
```

Import an installed workflow package, compile and select a workflow, enqueue
work, then run the daemon with an explicit local adapter configuration. The
[getting-started guide](https://github.com/tim-osterhus/millrace/blob/v0.22.0/docs/getting-started.md)
walks through one complete `simple_loop` setup.

## What Millrace Owns

- **Compiled authority:** workflow data becomes an immutable selected plan
  before work starts.
- **Durable progress:** queues, runs, artifacts, waits, and audit records
  survive process and model-session restarts.
- **Bounded dispatch:** each stage receives only its selected prompt, skills,
  schemas, and runtime context.
- **Governed routing:** runner output is candidate evidence. It cannot invent
  a legal route or approve its own completion.
- **Recovery and intervention:** workflows can retry, reroute, pause,
  quarantine, or request a local operator decision.
- **Inspection:** the CLI exposes status, runs, traces, waits, interventions,
  package health, and workspace health.

```text
workflow package -> compiler -> selected plan -> durable queue
                                                   |
                                                   v
validated outcome <- runtime transition <- runner stage
```

SQLite stores local control state. Content-addressed storage keeps immutable
plans, payloads, and artifacts. Millrace refuses unsupported or inconsistent
authority rather than reconstructing it from filenames, prompts, or runner
claims.

## Workflows And Runners

The base `millrace-ai` package ships only `kernel_ping`. `millrace-plus`
provides the official workflow collection, including `simple_loop`, LAD
variants, and `vendor_selection`. Plane names, stage names, queues, and
terminal markers remain package data rather than kernel concepts.

Millforge executes the default `millforge-base` component for eligible new
plans. Codex is an explicit alternative that requires an operator-provided
wrapper implementing Millrace's versioned JSON protocol.

Millrace also supports versioned compiled-plan export. It performs
compiled-plan export verification. Export is not runtime plan admission. v0.22
does not expose this through the CLI/operator surface.
A package-marketplace import flow is not part of the v0.22 CLI/operator
surface.

## Operator Commands

| Need | Command |
| --- | --- |
| Initialize local state | `millrace workspace init --input-id <id>` |
| Manage workflow packages | `millrace package ...` |
| Admit and select plans | `millrace plan ...` |
| Enqueue work | `millrace queue enqueue ...` |
| Run work | `millrace run daemon ...` |
| Inspect state | `millrace status`, `millrace runs ...`, `millrace trace ...` |
| Resolve governed waits | `millrace waits ...`, `millrace interventions ...` |
| Check health | `millrace doctor` |

Workspace-scoped commands accept
`millrace --workspace /absolute/path ...`. Mutation commands require explicit
replay-safe input or command IDs where applicable.

Millforge supports Linux and macOS, and WSL through Linux execution semantics.
Native Windows execution is not supported in v0.22.

## Documentation

- [Getting started](https://github.com/tim-osterhus/millrace/blob/v0.22.0/docs/getting-started.md)
- [How Millrace works](https://github.com/tim-osterhus/millrace/blob/v0.22.0/docs/how-millrace-works.md)
- [Workflow packages](https://github.com/tim-osterhus/millrace/blob/v0.22.0/docs/workflow-packages.md)
- [Errors and refusals](https://github.com/tim-osterhus/millrace/blob/v0.22.0/docs/errors.md)
- [Migrating from v0.21](https://github.com/tim-osterhus/millrace/blob/v0.22.0/docs/migrating-from-v0.21.md)
- [v0.22 compatibility](https://github.com/tim-osterhus/millrace/blob/v0.22.0/docs/v0.22-compatibility.md)
- [Millforge runner](https://github.com/tim-osterhus/millrace/blob/v0.22.0/docs/millforge-runner.md)
- [Codex runner](https://github.com/tim-osterhus/millrace/blob/v0.22.0/docs/codex-runner.md)

## License

Millrace is licensed under the
[Apache License 2.0](https://github.com/tim-osterhus/millrace/blob/v0.22.0/LICENSE).
