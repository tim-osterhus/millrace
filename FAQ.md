# Frequently Asked Questions

## Is Millrace an AI agent or model?

No. Millrace does not reason or generate work itself. It dispatches bounded
work to supported runners, validates the returned evidence, and applies only
the transitions allowed by the selected plan.

## Is it an agent harness?

No. Millrace is a framework around agentic harnesses.

However, [Millforge](https://github.com/tim-osterhus/millforge) is. If you
download the bundled package, then Millforge will come shipped by default.
Millforge is designed to allow configuration of agent guardrails at the
harness level, as a supported part of a broader Millrace graph.

## What does Millrace actually do?

It turns complex workflows that typically require human-in-the-loop for
reliable execution into multi-step loops. These loops are defined as graphs,
then compiled, validated, and executed by the Millrace runtime.

Diligent agentic engineering requires multiple steps. Outlining the spec,
decomposing into scoped tasks, writing tests for each task, implementing
them, checking completed work, patching discovered bugs, updating relevant
documentation, etc.

Each individual step can be done fairly reliably by an agent. The only issue
is the seams: knowing when to execute each stage, depending on the results of
the previous one. By codifying this logic into deterministic code, you get a
runtime that keeps agents on track forever.

## Is it only for software development?

No. A workflow may govern any agent-driven process that can be expressed as a
decision tree. If an agent can execute each step individually, they can be
strung together into a Millrace configuration. Software development was only
the first use case. Research, evaluation, operations, and vendor selection are
others.

## How is Millrace different from Claude Code, Codex, or Aider?

Those tools are agents or agent runners: they perform work. Millrace is the
runtime around the work. It decides which stage is eligible, supplies the
selected instructions, records the result, validates evidence, and applies the
next legal route.

## Can't I just do this with LangGraph?

Yeah, you could. But you'd have to test every edge case and validate that the
workflow you've created isn't missing anything, and then you'd have to run the
process to actually execute the workflow. And you'd have to repeat that for
every new workflow you create.

With Millrace, all of that is first class. Its built-in compiler prevents an
invalid workflow from executing, and it also runs the workflow as a durable
daemon-based process with minimal memory overhead.

They're not mutually exclusive either. If you already have a LangGraph
workflow, that could become integrated as a stage inside a compiled Millrace
configuration.

## How is Millrace different from Temporal or other workflow engines?

General workflow engines provide durable execution for application code.
Millrace is designed specifically for governed agent work: selected prompts
and skills, evidence-bearing stage results, agent-output authentication,
operator intervention, and plan-constrained routing are first-class concepts.

Millrace is not intended to replace a general distributed workflow platform.

## Can I collaborate with others using Millrace?

Sort of. Currently, Millrace is designed as a local runtime with a single
operator. If you have it hosted on the cloud or a VPS where others have
access, then it can be used collaboratively out of the box.

## Can I use Millrace for everything?

Technically, yes. But for simple one-off tasks that most agents can do in
under 30 minutes, Millrace is overkill.

Unlike most frameworks, Millrace shines brightest the more complex its job
is. The bigger the project is, the more complicated a workflow is, the
better Millrace will perform relative to other frameworks.

## What is a workflow package?

A workflow package contains a workflow definition and its selected assets. It
can declare stages, queue families, routes, result markers, artifact contracts,
runner bindings, timeouts, retries, recovery behavior, waits, quarantine rules,
completion rules, prompts, and skills.

See [Workflow packages](docs/workflow-packages.md).

## What is a selected plan?

The compiler resolves and validates a workflow package, then produces an
immutable plan with a content fingerprint. The operator selects that exact
plan for execution. The runtime follows the selected plan rather than
re-reading mutable workflow files while work is running.

## Does a planning task card authorize implementation?

No. A planning result or exported task-card artifact records proposed work. It
does not grant implementation authority. To execute that work, an operator
must select an execution-capable compiled plan and later enqueue the task
explicitly into an external queue family declared by that plan.

## Can an agent change the workflow or choose its own next step?

No. An agent may return a result and supporting evidence, but it cannot add a
route, approve its own completion, or make an undeclared transition. Changing
workflow authority requires compiling and selecting a different plan through
the operator surface.

## What counts as completion?

Completion is a legal terminal action defined by the selected plan and applied
to durable runtime state. An agent saying that work is complete is only an
input. Millrace accepts completion only when the required result, evidence, and
route conditions are satisfied. It's considered best practice to configure a QA
step for every "execution" step to ensure autonomous review occurs as part of
the process.

## What happens when work fails or becomes blocked?

The selected workflow decides. It may retry, route to recovery, wait for an
operator, quarantine work, or terminate with a declared outcome. Millrace does
not invent a recovery path that is absent from the plan. Recommendations for
how to construct these recovery paths are documented inside the loop-authoring
[skill.](https://github.com/tim-osterhus/millrace-plus/blob/main/src/millrace_plus/skills/millrace-loop-configuration/SKILL.md)

## Can workflows run stages concurrently?

Yes, when the compiled plan declares fan-out, join, and concurrency behavior.
Concurrency is workflow-defined; it is not an implicit permission for agents
to create undeclared work.

## Which runners are supported?

Millrace v0.22.0 supports Millforge and Codex runner integrations. Claude Code,
OpenCode, OpenHands, and other runner kinds will be supported in future
releases.

See the [Millforge runner](docs/millforge-runner.md) and
[Codex runner](docs/codex-runner.md) documentation.

## Is Millrace safe to run unattended?

It is designed for durable, long-running execution, but unattended does not
mean unrestricted. Review the selected workflow, runner configuration,
credentials, and operating-system permissions first. Start with bounded work
and inspect the resulting state and artifacts before increasing autonomy.

## Is Millrace a security sandbox?

No. Millrace limits workflow authority, not operating-system authority. A
runner executes with the permissions of the operator account that launched it.
Use normal OS isolation, credential hygiene, and least-privilege practices.

## Does work survive a restart?

Millrace persists control state in SQLite and immutable plans, payloads, and
artifacts in content-addressed storage. After a restart, it validates that
state and resumes eligible workflow work. It does not pretend that an external
runner process survived if that process actually stopped.

## What is the difference between pause and dispatch suspension?

A workflow pause is selected workflow behavior. Operator dispatch suspension
is a separate global control that temporarily refuses new claims:

```bash
millrace dispatch suspend --plan-fingerprint FINGERPRINT \
  --input-id ID --reason TEXT
millrace dispatch resume --plan-fingerprint FINGERPRINT \
  --suspension-id ID --input-id ID --reason TEXT
```

Suspension does not clear a workflow pause, close queued work, abandon an
accepted claim, or cancel active work. Status, runs, trace, and doctor expose
the exact suspension and a bounded list of accepted work that may still start.

## What is the difference between queue cancellation and runner cancellation?

`millrace queue cancel WORK_ITEM_ID` and `millrace queue cancel-lineage
LINEAGE_ID` close eligible workflow work atomically through Millrace's existing
close-work transition. They preserve the queue, closure receipt, governance
event, trace, and queue-closure audit record. They do not signal an adapter.

`millrace runs cancel RUN_ID --input-id ID` controls a potentially live runner
session. Queue cancellation refuses accepted work that may still start and any
live, lost, cleanup-pending, or orphan-risk session aftermath. After a clean
terminal session, an operator may explicitly close the workflow work.

## Do I need Millrace OS, Millforge, or Millrace Plus?

No single companion product is required for every use:

- `millrace-ai` is the core compiler, runtime, CLI, and daemon.
- `millrace-plus` provides installable workflow packages and assets.
- Millforge is a supported execution harness.
- Millrace OS is a separate management interface for Millrace instances.
- The `millrace` package is the bundled installation path.

## How should I install Millrace?

The intended path is agent-first: give your agent the repository URL and ask
it to install and configure Millrace for your environment. Manual commands and
verification steps are in [Getting started](docs/getting-started.md).

Credentials belong in local adapter or environment configuration, never in a
workflow package, prompt asset, or evidence record.

## What platforms are supported?

Millrace v0.22 supports Linux, macOS, and Windows through WSL. Native Windows
operation is not supported at this time.

## Does Millrace guarantee that an agent's work is correct?

No. Millrace can enforce process, routing, evidence, review, and completion
rules. It cannot make a weak model correct or prove that arbitrary evidence is
true. Workflow quality, evaluator quality, runner capability, and operator
judgment still matter.

## Where can I learn how the runtime works?

Start with [How Millrace works](docs/how-millrace-works.md). For operational
failures and stable error codes, see [Errors and refusals](docs/errors.md).
