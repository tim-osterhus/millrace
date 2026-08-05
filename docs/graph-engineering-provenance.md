# Graph Authority And Prior Art

> Status: Maintained provenance record
>
> Last audited: August 4, 2026 HST
>
> Scope: Public, open-source repository evidence available by the audit date

This document is the evidence ledger for Millrace's graph-authority history. It
records the OLAD lineage, the earlier Dagu implementation, Millrace's dated
cutover, and the architectural boundary between them.

## Findings

> **Millrace made its compiled workflow graph authoritative over agent execution
> on April 23, 2026, months before "graph engineering" became a category. The
> prior-art audit found only one earlier comparable open-source implementation:
> Dagu v2.5.0.**

The audit supports three findings.

1. **OLAD established the Millrace lineage.** Its January history defined a
   branching, outcome-routed coding workflow with specialized Codex and Claude
   stages. OLAD v1.7.0 moved that authority into a persistent deterministic
   shell runtime in February. Neither version compiled the workflow into graph
   data.
2. **Dagu came first under the broad compiled-graph definition.** Dagu v2.5.0
   shipped an external durable DAG runtime with several coding-harness providers
   on April 12.
3. **Millrace followed and extended the architecture.** Millrace v0.14.0 made
   a persisted compiled cyclic graph authoritative over typed stage outcomes.
   That authority covered recovery, escalation, and closure through a
   harness-neutral runner boundary.

No earlier implementation of Millrace's narrower authority boundary appeared
in the audited corpus. That is a secondary technical finding, not the headline
claim.

## Category Boundaries

In this document, compilation means mechanically transforming a workflow
definition into a validated concrete execution plan before that plan governs
execution. It does not imply native-code generation.

The broad comparison requires all of the following:

1. **Public and open source.** The implementation and dated history must be
   inspectable without relying on private deployments or later descriptions.
2. **External control plane.** A process outside the worker harness must retain
   execution authority.
3. **Compiled graph authority.** The controller must mechanically transform the
   workflow definition into a validated concrete graph plan and use that plan
   to select executable work.
4. **Coding-agent execution.** Graph nodes must invoke coding-agent harnesses,
   not only ordinary commands or API calls.
5. **Multiple harness implementations.** The same control-plane architecture
   must support at least two distinct external coding-agent harnesses.

Dagu and Millrace both meet this broad definition. Dagu shipped first.

Millrace's narrower boundary adds three properties:

- The persisted graph supports explicit cycles.
- Typed agent-stage outcomes select transitions and terminal states.
- The graph governs recovery, escalation, resume, and evidence-backed closure.

`Multiple harness implementations` has a specific historical meaning for
Millrace v0.14.0. The release shipped the same topology under Codex CLI and Pi
RPC modes, with runner bindings frozen per stage. The public presets did not
demonstrate one run mixing both harnesses.

## OLAD Lineage

Millrace did not begin with a graph compiler. It grew out of Orchestrative Lean
Agentic Development, or OLAD.

### January 24: graph-shaped orchestration protocol

OLAD's [initial commit](https://github.com/tim-osterhus/olad-framework/commit/c74ae75bc1ee493f61b25280fa75a1941379d6fd)
is dated January 24, 2026 at 07:21:24 HST. It defined distinct CCC,
Integration, Builder, QA, Hotfix, and Doublecheck stages. Status markers such as
`BUILDER_COMPLETE`, `QA_COMPLETE`, `QUICKFIX_NEEDED`, and `BLOCKED` controlled
the next step.

The default configuration assigned Codex to CCC, Integration, Builder, and
Hotfix. It assigned Claude to QA and Doublecheck. The Hotfix and Doublecheck
path formed an explicit recovery cycle.

An orchestrator agent followed the runbook and launched the worker harnesses.
The topology lived in instructions and shell templates, not in compiled graph
data or a deterministic runtime program.

The commit date proves when the implementation entered the current repository
history. It does not independently prove when the repository became public.
The first tagged publication receipt is
[OLAD v1.2.0](https://github.com/tim-osterhus/olad-framework/releases/tag/v1.2.0),
released January 29, 2026 at 06:48:59 UTC.

Within the audited corpus, this is the earliest identified open-source
orchestration protocol that assigned a predefined, branching, outcome-routed
coding workflow to heterogeneous standalone agent CLIs.

### February 13: deterministic persistent control plane

[OLAD v1.7.0](https://github.com/tim-osterhus/olad-framework/releases/tag/v1.7.0)
was released February 14, 2026 at 05:27:03 UTC, or February 13 at 19:27:03
HST. It added `agents/orchestrate_loop.sh`, a deterministic backlog-draining
runner outside any chat session.

The script persisted status in files, selected runners per stage, and resumed
interrupted work. It also handled repair, troubleshooting, archival, blocker
demotion, and daemon waits for new work.

Within the audited corpus, OLAD v1.7.0 is the earliest identified open-source
deterministic external control plane for this workflow class. It persistently
executed branching stages across heterogeneous standalone agent CLIs.

### Why Ralph does not qualify as graph prior art

Geoffrey Huntley's original
[Ralph technique](https://ghuntley.com/ralph/) repeatedly sent the same prompt
to one agent process. That is loop engineering.

Ian Nuttall's January 12
[Ralph implementation](https://github.com/iannuttall/ralph/commit/8d146f47e8f609113c8a15a669db8acb9ea9502c)
described itself as a simple, portable, single-agent loop. Each build iteration
selected one incomplete story, rendered the same build prompt, ran one selected
harness, and repeated.

Its planning and PRD-generation prompts were separate user-selected modes. The
runtime did not route between them. The January 13
[JSON PRD update](https://github.com/iannuttall/ralph/commit/445540020410750591bf42ee7303b2e4579fbf9b)
added story dependencies, locks, and statuses. Those fields formed a work-item
selection DAG, not a multi-stage agent-execution graph.

Ralph is relevant loop-engineering prior art. It does not defeat the OLAD
finding because it lacks stage-specific prompts, outcome-routed execution, and
heterogeneous harness assignments within one workflow.

## Chronology

- **January 24, 2026 HST:** OLAD's graph-shaped orchestration protocol entered
  the current Git history.
- **January 29, 2026 UTC:** OLAD v1.2.0 provided the first tagged release
  receipt for that protocol.
- **February 14, 2026 UTC:** OLAD v1.7.0 shipped its deterministic persistent
  orchestration loop.
- **April 12, 2026:** Dagu v2.5.0 shipped its coding-harness DAG executor.
- **April 21, 2026 HST:** Millrace emitted its compiled graph sidecar.
- **April 23, 2026 HST:** Millrace cut runtime authority over to that graph.
- **April 24, 2026 UTC:** Millrace v0.14.0 supplied the public release receipt.

## Millrace Evidence

### April 21: compiled graph sidecar

Commit [`0d11b98`](https://github.com/tim-osterhus/millrace/commit/0d11b9861e5c9389a3465472705d819598925663),
dated April 21, 2026 HST, added explicit graph nodes, outcome-labeled edges,
entry mappings, terminal states, and graph compilation.

The commit intentionally kept the graph non-authoritative. The legacy router
still controlled execution. This milestone proves that the compiled graph
existed, not that it governed the runtime.

### April 23: graph authority cutover

Commit [`5cbdb7f`](https://github.com/tim-osterhus/millrace/commit/5cbdb7fa181e4a27bc3b6b118a78afaed438a2c7)
is dated April 23, 2026 at 21:31:33 HST. It moved intake activation and
post-stage routing onto the compiled graph plan. Recovery, closure-target
activation, and terminal decisions moved with them.

The implementation evidence includes:

- The persisted [`compiled_graph_plan.json` contract](https://github.com/tim-osterhus/millrace/blob/5cbdb7fa181e4a27bc3b6b118a78afaed438a2c7/docs/runtime/millrace-compiler-and-frozen-plans.md).
- The [cyclic execution graph](https://github.com/tim-osterhus/millrace/blob/5cbdb7fa181e4a27bc3b6b118a78afaed438a2c7/src/millrace_ai/assets/graphs/execution/standard.json), including repair, troubleshooting, and resume paths.
- The [runtime graph-authority module](https://github.com/tim-osterhus/millrace/blob/5cbdb7fa181e4a27bc3b6b118a78afaed438a2c7/src/millrace_ai/runtime/graph_authority.py).
- The [graph-authority test suite](https://github.com/tim-osterhus/millrace/blob/5cbdb7fa181e4a27bc3b6b118a78afaed438a2c7/tests/runtime/test_graph_authority.py).
- Semantic terminal results and the normalized [`StageResultEnvelope`](https://github.com/tim-osterhus/millrace/blob/5cbdb7fa181e4a27bc3b6b118a78afaed438a2c7/src/millrace_ai/contracts.py).
- The [Codex CLI and Pi RPC adapter contract](https://github.com/tim-osterhus/millrace/blob/5cbdb7fa181e4a27bc3b6b118a78afaed438a2c7/docs/runtime/millrace-runner-architecture.md).
- Compile-time [runner bindings for each stage](https://github.com/tim-osterhus/millrace/blob/5cbdb7fa181e4a27bc3b6b118a78afaed438a2c7/docs/runtime/millrace-modes-and-loops.md).

The graph routes semantic outcomes such as `CHECKER_PASS`, `FIX_NEEDED`,
`BLOCKED`, and `TROUBLESHOOT_COMPLETE`. This differs from a DAG that schedules
the next dependency after a process succeeds.

### April 24: independently recorded public release

Git commit dates do not establish the exact time that code became public.
Millrace has a separate publication receipt.

[GitHub release `v0.14.0`](https://github.com/tim-osterhus/millrace/releases/tag/v0.14.0)
was published April 24, 2026 at 10:04:44 UTC, or 00:04:44 HST. The release tag
contains the April 23 authority commit. PyPI artifacts followed about one minute
later.

The historical implementation was open source under the
[AGPL-3.0 license](https://github.com/tim-osterhus/millrace/blob/5cbdb7fa181e4a27bc3b6b118a78afaed438a2c7/LICENSE).

## Dagu: The Earlier Broad Implementation

The initial review of repositories tagged `graph-engineering` did not surface
Dagu because the project did not use that label. The broader prior-art audit
found Dagu during preparation of this record. We revised the README wording
before publication.

Commit [`c8ed7c1`](https://github.com/dagucloud/dagu/commit/c8ed7c1dc9933997e4420df83ad439e22ad6ba2e),
dated April 10, 2026, added coding-harness providers for Claude, Codex, Copilot,
OpenCode, and Pi. The feature shipped in
[`v2.5.0`](https://github.com/dagucloud/dagu/releases/tag/v2.5.0) on April 12 at
15:01:02 UTC, or 05:01:02 HST.

At that release, Dagu did the following:

- Normalized YAML into a concrete workflow DAG.
- Persisted an attempt-level [`dag.json`](https://github.com/dagucloud/dagu/blob/080cbf01c0fd22e61d8e7b98c5058a8b7c2d6916/internal/persis/filedagrun/attempt.go).
- Constructed an immutable-after-build [`runtime.Plan`](https://github.com/dagucloud/dagu/blob/080cbf01c0fd22e61d8e7b98c5058a8b7c2d6916/internal/runtime/plan.go).
- Used that plan to select runnable nodes in its [execution loop](https://github.com/dagucloud/dagu/blob/080cbf01c0fd22e61d8e7b98c5058a8b7c2d6916/internal/runtime/runner.go).
- Resolved a harness provider for each step through its [harness executor](https://github.com/dagucloud/dagu/blob/080cbf01c0fd22e61d8e7b98c5058a8b7c2d6916/internal/runtime/builtin/harness/harness.go).

That is an earlier external control plane executing a compiled agent workflow
graph across heterogeneous harnesses. Calling Millrace first at that level
would be false.

Dagu's `runtime.Plan` rejects cycles, and its graph schedules dependencies from
generic node state. Millrace persists a cyclic transition plan whose semantic
agent-stage outcomes select recovery, resume, escalation, closure, and terminal
paths. This is the narrower architectural distinction recorded by the audit.
It is not a quality ranking.

## Prior-Art Comparison

The following candidates provided the strongest prior art before or near the
Millrace cutoff.

- **[Dagu v2.5.0](https://github.com/dagucloud/dagu/releases/tag/v2.5.0):**
  External scheduling, durable state, approvals, retries, and five harness
  providers. Its dependency-driven plan is acyclic and lacks a semantic
  stage-result transition contract.
- **[LangGraph 0.1.2](https://github.com/langchain-ai/langgraph/releases/tag/0.1.2):**
  Compiled cyclic state graphs, conditional routing, interrupts, and
  checkpoints. Its application-defined nodes execute in-process without the
  inspected external harness adapter and result boundary.
- **[Semantic Kernel Process 1.21.0](https://github.com/microsoft/semantic-kernel/releases/tag/dotnet-1.21.0):**
  Serializable process graphs, cyclic events, and local or Dapr execution.
  The audit found no stable harness-neutral compiled plan and semantic result
  contract across external coding harnesses.
- **[Temporal 1.0](https://github.com/temporalio/temporal/releases/tag/v1.0.0):**
  External durable control, histories, retries, signals, and worker boundaries.
  Workflow code and replayed history remain authoritative, not a persisted
  compiled agent transition graph.
- **[Mastra 0.1.0](https://github.com/mastra-ai/mastra/releases/tag/v0.1.0):**
  JSON workflow blueprints and external Inngest-backed execution. The audit
  found no generic harness adapter contract or semantic cyclic recovery
  authority.
- **[AutoGen GraphFlow 0.5.6](https://github.com/microsoft/autogen/releases/tag/python-v0.5.6):**
  Serializable graphs, conditional routing, and cycles. The graph and agents
  remain inside one AutoGen team without an external harness-neutral result
  boundary.
- **[Agentspan 0.1.8](https://github.com/agentspan-ai/agentspan/releases/tag/v0.1.8):**
  Server-generated Conductor workflows, durable approvals, distributed
  workers, and framework adapters. External framework graphs can remain opaque
  inside one task.
- **[AgentFlow](https://github.com/fuzzland/agentflow/tree/4cfcc886876c55ad2f21decf293d9de578dca0c4):**
  External scheduling, cycles, retries, and multiple runners. The audit found
  no immutable compiled graph or single authority over semantic recovery and
  closure.
- **[Bernstein](https://github.com/sipyourdrink-ltd/bernstein/tree/239c537c47f1486f98a3835dd0a8ef945ca05450):**
  Frozen DAGs, approval-bearing phases, and multiple runners. Its DAG, phase
  executor, live task graph, and retry scheduler remain separate authorities.
- **[`dagent`](https://github.com/RobotSe7en/dagent/commit/5b2088b599cea7d70264a05286e9480176400133):**
  Its DAG executor, scheduling, traces, and bounded node loops arrived later,
  on April 29. They also operate inside its own harness and runtime.
- **[Gold-Band](https://github.com/diodeme/Gold-Band/commit/f221a10377656e3000c94ba49d80b967ece5b46e):**
  Its relevant graph interface arrived later, on May 11. It does not implement
  the same compiled lifecycle-authority boundary.
- **[`OpenCode-GraphAgent`](https://github.com/LeXwDeX/OpenCode-GraphAgent/commit/c8e7b676fdee1a1f3dcec140e1a52c9a2a285872):**
  Its DAG engine, API, plugins, and runtime arrived later, on July 2. The engine
  is embedded in an OpenCode fork.

Generic workflow engines establish substantial prior art for durable
orchestration. Graph libraries establish substantial prior art for cycles and
conditional routing. Multi-agent frameworks establish substantial prior art
for coordinating agents. The narrower Millrace finding concerns the
conjunction of those properties at a specific agent-runtime authority boundary.

## Audit Method

The August 4 audit covered:

- All 41 repositories visible under GitHub's
  [`graph-engineering` topic](https://github.com/topics/graph-engineering).
- Direct repository and code searches for earlier graph, DAG, state-machine,
  agent-runtime, and multi-harness implementations.
- Older systems that could qualify retroactively without using the term
  `graph engineering`.
- The OLAD lineage and earlier loop-engineering systems that could challenge
  its multi-stage workflow boundary.
- Immutable commits, tags, releases, and source snapshots for the strongest
  candidates.

The audit tested candidate implementations, not marketing descriptions or
theoretical capability. A generic workflow engine that could launch an agent
did not qualify without public agent-specific implementation evidence.
Multiple model providers did not count as multiple agent harnesses.

## Limits And Corrections

`Earliest identified` and `found only one earlier` describe the audited public
record. They do not prove a universal negative. Private systems, deleted
repositories, rewritten histories, or missed public code may change the
conclusion.

The audit corpus and cutoff are stated so the claim can be falsified. To submit
earlier qualifying prior art, [open an issue](https://github.com/tim-osterhus/millrace/issues/new)
with:

1. The project and license.
2. An immutable commit or release dated before April 24, 2026 HST.
3. Source evidence for each qualifying criterion.
4. An explanation of which artifact held runtime authority.

If stronger evidence appears, this document and the README claim should be
updated.

## Revision History

- **August 4, 2026:** Initial publication. The broader audit identified Dagu
  v2.5.0 as the only earlier comparable open-source implementation. The
  document records Dagu's priority, adds the OLAD lineage, and distinguishes
  loop engineering from graph-shaped orchestration and compiled graph
  authority.
