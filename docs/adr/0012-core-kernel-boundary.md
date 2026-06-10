# ADR-0012: Core Kernel Boundary

Status: Accepted

Date: 2026-06-04

## Context

The Millrace runtime has grown a layered architecture where the runtime kernel
(the code under `src/millrace_ai/runtime/`, `src/millrace_ai/compilation/`, and
their stable facades) owns tick orchestration, graph-authority routing,
result application, recovery, and workspace mutation. It also directly
understands workflow semantics such as Recon classification, Blueprint drafting,
Learning promotion, Arbiter closure judgment, Planner decomposition, stage
names, plane names, queue ordering, terminal outcomes, retry thresholds,
prompt/context policy, and lifecycle route meaning.

That coupling makes the kernel responsible for too much domain knowledge. It
cannot be reused for a second workflow engine without dragging along every
shipped work-family concept, and it makes extension packages fragile because
any new workflow concept must be understood by the kernel before it can be
used.

This ADR formalises the boundary between the runtime kernel and the
workflow-semantic layer that should live in extension packages, graph/config
assets, or workflow primitive contracts.

## Decision

The runtime kernel (the packages under `src/millrace_ai/runtime/`,
`src/millrace_ai/compilation/`, `src/millrace_ai/workspace/`, and their stable
public facades) must not own or hard-code workflow semantic knowledge beyond
the minimum needed to dispatch stages, apply compiled results, and persist
durable state.

Specifically, the runtime kernel is forbidden from owning, by native enum,
hard-coded branch, or built-in constant, the following workflow semantics:

- Recon classification families and their routing targets
- Blueprint manifest, draft, packet, evaluation, or promotion semantics
- Closure target lifecycle beyond "active root lineage exists; evaluate when
  backlog is drained"
- Learning promotion rules (Analyst → Professor → Curator ordering; Librarian
  triggering)
- Arbiter rubric, verdict, or closure-judgment semantics beyond "present
  closure target evidence to an external stage"
- Planner decomposition strategies or decomposition-stage sequencing
- Stage names as anything other than opaque identifiers in compiled node plans
- Plane names as anything other than opaque identifiers in compiled node plans
- Queue ordering or priority rules beyond what the compiled plan's claim policy
  declares
- Terminal outcome values beyond their string-backed serialization contract
- Retry thresholds or retry-budget policies beyond what compiled recovery
  policies declare
- Prompt/context policy such as which providers are active, which profiles
  apply, or which redactions are in effect
- Lifecycle route meaning such as which terminal results imply completion,
  blocking, or handoff

These semantics must live in one of the following layers:

1. **Graph and config assets**: JSON-declared graph topologies, stage-kind
   registries, runtime-effect rules, lifecycle mutation plans, terminal actions,
   recovery policies, and claim policies that ship with the runtime or are
   authored by operators.
2. **Extension packages**: Python packages that register new runtime-effect
   operation runners, new workflow primitive contracts, new request-context
   providers, or new terminal actions without modifying the kernel.
3. **Workflow primitive contracts**: Typed Pydantic schemas under
   `src/millrace_ai/architecture/workflow_primitives/` that define the contract
   surface for work families, document adapters, lifecycle mutation, runtime
   effects, and recovery policies.

The kernel retains ownership of:

- Tick orchestration (mailbox drain, intake, claim, dispatch, result
  application)
- Graph-authority routing from compiled node metadata
- Compiled plan loading, currentness checking, and staleness rejection
- Stage-result normalization and application
- Durable state persistence (snapshots, counters, run traces, events,
  artifacts)
- Lane scheduling and plane concurrency policy
- Execution capability gate evaluation and operator approval routing
- Runtime-owned error recovery (post-stage exceptions, pre-dispatch failures)
- Counter mutation as declared by compiled policy metadata
- Workspace initialization, baseline management, and schema epoch tracking
- Usage governance and token accounting

## Consequences

Workflow semantics are no longer implicit in kernel enums or hard-coded
branches. Adding a new workflow family, evaluation strategy, or closure model
does not require kernel changes; it requires new graph assets, primitive
contracts, or extension packages.

The kernel is smaller, more testable, and reusable across workflow engines.

The immediate cost is discipline: existing kernel modules that still carry
workflow-semantic branches (especially in `runtime/completion_behavior.py`,
`runtime/recovery/`, and `runtime/result_application.py`) must be refactored
to consume their semantics from compiled metadata rather than hard-coded
stage/plane names. This ADR does not mandate that refactor in one pass; it
establishes the boundary so future work can migrate incrementally.

Prospective boundary packages such as `millrace_ai.kernel` or
`millrace_ai.engine` are not yet created. This ADR names the conceptual
boundary only.

Package names mentioned as prospective, such as `kernel/` and `engine/`, are
boundary descriptors for future migration. `src/millrace_ai/extensions/` now
exists for extension manifest contracts, built-in domain boundary Protocols,
the lazy boundary registry, and built-in adapter modules. Extension package
manifest validation remains governed by ADR-0015, and the active
compatibility-facade bridge status is recorded in ADR-0016.
