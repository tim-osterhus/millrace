# ADR-0010: Make compiler-validated workflow primitives runtime authority

**Status**: Accepted  
**Date**: 2026-05-19  
**Deciders**: Millrace maintainers

## Context

ADR-0005 made the compiled graph plan the runtime authority for stage topology,
request binding, recovery, completion behavior, and post-stage routing. That
was necessary, but not sufficient for fully custom workflow loops.

The graph can say which stage runs next, but custom loops also need the runtime
to understand which work-item families a plane may claim, how markdown
documents are parsed, how terminal outcomes mutate source artifacts, which
runtime effects are allowed, whether effect handlers are implemented, how
failure policy is classified, and whether a workspace's mutable state matches
the schema expected by the installed runtime.

Leaving those behaviors in scattered Python tables would recreate the same
authority drift ADR-0005 was designed to remove. A loop configuration could
compile as graph-valid while still crashing or mutating the wrong durable state
because a terminal marker had no coherent lifecycle action, effect handler, or
schema epoch compatibility story.

The Blueprint Planning loop made that risk concrete. It needed custom Planning
stage kinds, a new Blueprint draft work-item family, queue claim policy changes,
runtime-owned packet/evaluation/promotion effects, closure suppression across
same-lineage generated tasks, and operator-visible request context. Those are
runtime contracts, not prompt prose.

## Decision

Millrace will treat compiler-validated workflow primitives as part of the
runtime-authoritative compiled plan.

Workflow primitive assets live under `src/millrace_ai/assets/registry/` and
define:

- work-item families
- document adapters
- queue claim policies
- terminal actions
- lifecycle mutation plans
- runtime effect handlers
- runtime effect operations, stores, and validators
- runtime effect rules
- recovery policies
- runtime failure policies
- workspace schema epochs

The compiler loads those assets from the active asset root, includes their
hashes in compile-input fingerprints, validates cross-references, and persists
the selected definitions into `compiled_plan.json`. Runtime modules consume the
compiled primitive selections instead of re-deriving queue family ownership,
terminal lifecycle behavior, effect selection, or schema compatibility from
loose assets or hard-coded tables.

Runtime effect rules may reference only declared handlers and known operation
ids. A selected operation must declare the rule's handler as a legacy alias,
and runtime dispatch must provide a matching registry entry before execution.
Duplicate stage/terminal effect bindings are invalid because runtime effect
dispatch must be unambiguous.

Scheduler lane policy and request-context generation are also compiler-owned
runtime surfaces. Default shipped modes remain conservative, but any mode that
declares multi-lane behavior must satisfy lane conflict validation before the
daemon can dispatch those lanes. Each runner request receives a deterministic
request-context bundle so stage output can be traced back to the compiled node,
launch plan, work item, lane, and relevant lineage state.

Workspace schema epoch compatibility is enforced before mutable runtime state
is loaded. Epoch-crossing reset is a supported archive/reset operation, not an
implicit best-effort migration.

## Alternatives considered

- **Keep workflow behavior hard-coded in runtime modules**: Rejected because it
  would preserve hidden runtime authority and make custom loops require source
  edits even when their graph and mode assets compile.
- **Let graph-loop assets define every workflow behavior directly**: Rejected
  because queue families, document adapters, effects, failure policy, and
  schema epochs have different reasons to change than graph topology.
- **Let stage entrypoints declare lifecycle and effect semantics**: Rejected
  because prompt prose is advisory and cannot be the durable authority for
  queue mutation or runtime-owned effects.
- **Allow unknown effect handlers and fail only at runtime**: Rejected because
  invalid custom configurations should be rejected by the compiler before a
  daemon can start.
- **Perform implicit schema migrations whenever startup detects drift**:
  Rejected because mutable long-running workspace state should not be parsed or
  rewritten under an unknown epoch. Archive/reset is safer and more
  diagnosable.

## Consequences

**Positive:**
- Custom workflow loops can be represented as data-driven contracts instead of
  source patches scattered through runtime tables.
- Invalid routing/effect/lifecycle combinations fail during compile instead of
  during a long-running daemon session.
- Runtime state mutation remains single-writer and runtime-owned while still
  becoming configurable through validated assets.
- Operators can inspect one compiled artifact for topology, queue claim policy,
  lifecycle behavior, effects, lanes, schema epoch, and request identity.
- Blueprint Planning proves the model without changing the standard Execution
  loop.

**Negative / accepted costs:**
- The compiler now owns more runtime-critical validation and must be tested as
  a safety boundary.
- Adding a new primitive kind requires contract models, asset loaders,
  compiler validation, runtime consumers, tests, and docs.
- Packaged effect handlers are still Python implementations; assets select
  them, but assets alone cannot invent arbitrary new runtime code.
- The compiled plan shape is larger and more semantically dense than a pure
  graph plan.

**Neutral but notable:**
- ADR-0005 remains true. The compiled plan is still the runtime authority; this
  ADR expands what the compiled plan must contain.
- ADR-0009 remains true for shipped stage legality. Workflow primitives do not
  replace stage metadata; they complement it.
- Default shipped modes remain conservative. Multi-lane support exists behind
  compiler validation, not as a default scheduling posture.

## Follow-up

- Keep `millrace compile validate` and `millrace compile show` aligned with the
  primitive fields runtime consumers actually use.
- Keep tests proving runtime effect dispatch uses compiled effect rules rather
  than static stage/terminal tables.
- Keep schema epoch archive/reset behavior documented and covered by workspace
  tests.
- When a new runtime-owned behavior is added, decide explicitly whether it
  belongs in graph topology, workflow primitive assets, next-tick runtime
  config, or Python implementation.
