# How Millrace Works

Millrace separates agent execution from workflow control.

An agent runner can edit files, call tools, and produce a result. It cannot
choose an undeclared route or make its own completion claim authoritative.
Millrace applies those decisions from a compiled workflow and records the
result in durable local state.

## The Main Pieces

### Workflow package

A workflow package contains one or more workflow definitions and their
selected assets. A definition can declare:

- stages and graph nodes;
- queue families and external intake routes;
- legal terminal markers and actions;
- artifact schemas and handoff projections;
- runner bindings and timeouts;
- retry, recovery, wait, quarantine, and completion behavior;
- entrypoint prompts and stage-core skills.

The runtime does not infer behavior from names such as `Planning`, `Worker`, or
`Review`. Those names mean only what the selected package declares.

### Compiler

The compiler validates the complete decision structure before the workflow can
run. It resolves references, checks schemas and routes, selects the requested
entrypoint, and produces an immutable plan with an authority fingerprint.

The fingerprint identifies the exact behavior selected for a run. Mutable
package metadata, local paths, and unselected workflows are not part of that
authority.

### Kernel

The kernel is the state-change boundary. It accepts explicit transition inputs,
checks them against the selected plan and current state, and returns a
deterministic decision. Applying that decision rechecks its expectations so a
stale result cannot overwrite newer state.

Prompts, runner output, and external callbacks are inputs. They do not mutate
state directly.

### Durable storage

SQLite stores the local control state. Content-addressed storage keeps
immutable plans, payloads, and artifacts. Stored records use explicit versions
rather than serialized Python object layouts.

Restart loading validates references, fingerprints, and relationships. Millrace
refuses unsupported or corrupt state instead of rebuilding authority from
whatever files happen to be present.

### Operator and adapters

The operator layer turns CLI requests into audited runtime inputs and projects
read-only status. Runner adapters translate selected dispatches into external
process calls and return evidence to the runtime.

Neither layer may bypass the kernel. Within Millrace, runner configuration
cannot enlarge the selected capabilities, timeout, routes, or assets. This is
not an operating-system sandbox: a local runner wrapper still executes with
the permissions of the operator account that launched it.

Runner-session cancellation is durable and coordinator-owned. The coordinator
first requests cooperative cancellation, waits 5.0 seconds, then terminates
owned work and waits another 5.0 seconds before using a hard kill when needed.
These two finite grace periods are local runtime safety mechanics, not compiled
workflow policy. `millrace doctor` projects their effective values.

## One Stage At A Time

For a normal agent stage, the daemon:

1. finds work eligible under the selected plan;
2. claims one activation with a fencing token;
3. materializes only the selected prompt and skill assets;
4. sends a dispatch envelope to the selected runner;
5. authenticates the returned evidence against that dispatch;
6. validates the marker and any artifact payload;
7. applies the selected route and records the transition.

Fanout and join behavior follow the same rule. The graph declares which work
is created, what evidence each branch must carry, and when a join is legal.
There is no workflow-specific orchestration script hidden behind the daemon.

## Blocked Work And Operator Waits

A workflow can declare different consequences for different blocked states.
For example, it may retry, route to a recovery stage, pause one lineage,
quarantine work, or create a durable operator wait.

The operator can inspect waits and interventions through the CLI and submit one
of the options selected by the workflow. The runtime records who submitted the
decision and applies it through the same transition boundary as agent results.

## Completion

Completion is also workflow data. A simple loop may close after a review stage
accepts the work. A software-development loop may require additional evidence
or remediation. A purchasing loop may stop at an operator decision rather than
act autonomously.

The common invariant is that completion comes from a legal selected action
applied to durable state, not from an agent saying that it is finished.

## Package Boundaries

| Package | Responsibility |
| --- | --- |
| `millrace-ai` | Compiler, kernel, storage, adapters, daemon, and CLI |
| `millrace-plus` | Official workflow definitions, prompt assets, stage skills, and authoring guides |
| `millforge` | Independently owned execution harness that supplies `millforge-base` |
| `millrace` | Dependency-only bundle that pins `millrace-ai==0.22.0`, `millrace-plus==0.22.0`, and `millforge==0.1.0` |

This split keeps the runtime generic. Adding a new workflow should add compiled
data and assets, not a new branch in the kernel.

`millrace-ai`, `millrace-plus`, and `millforge` require Python 3.11 or newer.
The dependency-only `millrace` distribution requires Python 3.12 or newer and
contains no runtime code. Installing one member distribution alone does not
install the other members.
