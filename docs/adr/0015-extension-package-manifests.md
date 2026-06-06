# ADR-0015: Extension Package Manifests

Status: Accepted

Date: 2026-06-04

## Context

Millrace ships a fixed set of runtime stages, graph topologies, runtime-effect
operations, request-context providers, and terminal actions. The compiler
validates and freezes these into a compiled plan, and the runtime executes
against that plan.

As the generic engine boundary takes shape (ADR-0012, ADR-0013, ADR-0014),
extension packages will need a way to contribute new runtime vocabulary without
modifying the kernel. "Runtime vocabulary" includes:

- New runtime-effect operation runners
- New terminal actions
- New request-context providers
- New work-family document adapters
- New claim policies
- New recovery policies
- New failure policies

However, extensions must not own graph topology, route policy, or scheduling
order. Those remain runtime-owned or operator-owned concerns because they
determine control flow, concurrency, and resource allocation, which must be
auditable and stable across extensions.

## Decision

Extension packages may declare manifests that register new runtime vocabulary
items. Each manifest is a JSON or Python metadata file that the compiler or
runtime loader discovers and validates before the item can be used in a
compiled plan.

The manifest contract:

1. **Type declaration**: Each manifest declares what type of vocabulary item it
   registers (operation runner, terminal action, context provider, document
   adapter, etc.).
2. **Item ID**: A unique string identifier used in compiled plans to reference
   the item.
3. **Implementation path**: A Python module path or adapter class that the
   runtime loader can import to execute the item.
4. **Contract schema**: A reference to a Pydantic schema or JSON Schema that
   defines the item's input/output contract and validation rules.
5. **Dependencies**: Optionally, other extension items or shipped items that
   this item depends on.
6. **Version**: A semver version string for the extension item.

Extension packages are explicitly forbidden from owning:

- **Graph topology**: The ordering and transitions between graph nodes.
  Extensions may declare node types and terminal states, but the connecting
  edges, entry nodes, and recovery cycles are graph assets owned by the runtime
  or operator.
- **Route policy**: The mapping from terminal results to next-node decisions.
  Route policy is compiled from graph assets and lifecycle metadata, not
  declared by extension manifests.
- **Scheduling order**: The plane, lane, and concurrency policy that controls
  when and in what order stages execute. Scheduling is a runtime-owned concern
  derived from the compiled plan and lane configuration.

Extension packages may, however, declare new:

- **Operation runners**: Python implementations that the runtime dispatches
  when a compiled plan references their operation id (see ADR-0014).
- **Terminal actions**: Lifecycle mutation plan entries that describe how a
  terminal outcome mutates source work items.
- **Context providers**: Request-context implementations that render prompt
  context for specific runtime stages.
- **Work-family document adapters**: Loaders and renderers for queue documents
  of a specific work family.
- **Claim policies**: Eligibility and ordering rules for claiming work from the
  queue, subject to compiled plan authority.
- **Recovery policies**: Retry-budget, repair-route, and exhausted-state rules
  for specific failure classes.
- **Failure policies**: Failure-class-to-router-decision mappings for specific
  operation steps.

The compiler validates extension manifests at compile time. A manifest that
declares an item with a missing implementation path, an invalid contract
schema, a version conflict, or an undeclared dependency must produce a
compiler diagnostic and prevent plan freezing until resolved.

## Consequences

Extension packages can add new runtime behavior without kernel changes, making
the generic engine boundary meaningful for third-party or custom workflow
authors. The compiler remains the validation gate, preserving the safety
properties of compiled-plan authority (ADR-0005, ADR-0010).

The constraint on graph topology, route policy, and scheduling order keeps the
control flow auditable and stable. Two extensions cannot silently create a
cycle, change a routing decision, or compete for the same lane. Those concerns
stay in operator-controlled graph assets and runtime config.

A prospective `src/millrace_ai/extensions/` package that would host manifest
loading, validation, and item registration is not yet created. This ADR defines
the manifest contract so that when the extension framework is built, the
contract surface is already agreed.

Package names mentioned as prospective (such as `extensions/`,
`extension_registry/`) are not yet created runtime modules. They are boundary
descriptors for future implementation.
