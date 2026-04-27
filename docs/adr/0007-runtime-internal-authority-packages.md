# ADR-0007: Use runtime internal authority packages for high-risk domains

**Status**: Accepted
**Date**: 2026-04-27
**Deciders**: Millrace maintainers

## Context

Millrace's runtime has several domains that are not ordinary helper logic.
Usage governance can pause and resume execution. Graph authority decides which
node activates next and how stage results mutate durable work state. These
domains affect long-running correctness, not just local formatting or adapter
behavior.

Keeping that logic in single large modules made the public import surface easy
to find, but it also mixed unrelated reasons to change: state models,
persistence, policy evaluation, telemetry, monitor events, activation, policy
lookup, counters, and plane-specific routing. That made review harder and made
it easier for future changes to cross authority boundaries accidentally.

At the same time, existing callers already imported from stable surfaces such
as `millrace_ai.runtime.usage_governance` and
`millrace_ai.runtime.graph_authority`. Removing those surfaces would create
unnecessary migration churn.

## Decision

Millrace will keep public runtime authority surfaces as stable package facades
while moving implementation details into named internal modules.

`runtime/usage_governance/` owns usage governance models, state persistence,
ledger reconciliation, runtime-token windows, subscription quota telemetry,
monitor event emission, and engine-facing pause-source application.

`runtime/graph_authority/` owns compiled-graph activation decisions, validation,
policy lookup, recovery counters, stage mapping, and execution, planning, and
learning routing.

The package `__init__.py` files remain explicit compatibility facades. Runtime
callers should import public authority functions from those facades unless they
are intentionally working inside the authority package.

## Alternatives considered

- **Keep each authority domain in one large module**: Rejected because the
  modules had too many independent reasons to change and were becoming harder
  to review safely.
- **Create a generic runtime helpers package**: Rejected because these are
  authority domains with durable behavior, not undifferentiated helpers.
- **Expose every internal module as public API**: Rejected because external
  callers need stable behavior, not direct access to every internal policy
  function.
- **Move authority decisions back into `RuntimeEngine`**: Rejected because that
  would reverse the engine-facade cleanup and concentrate unrelated behavior in
  the runtime state holder.

## Consequences

**Positive:**
- High-risk runtime policy can be reviewed by domain instead of by scrolling a
  large mixed module.
- Public imports stay stable while internal ownership becomes clearer.
- Tests can target activation, policy, counter, ledger, and evaluation behavior
  independently.
- Future runtime authority work has obvious package homes.

**Negative / accepted costs:**
- Tracing one full runtime tick now crosses more files.
- Maintainers must keep facade exports intentional and avoid turning
  `__init__.py` into implementation code.
- Authority-package tests must prove behavior, not just importability.

**Neutral but notable:**
- This decision does not make runtime policy configurable by itself. It only
  defines code ownership for authority domains that already exist.

## Follow-up

- Keep public export characterization tests around package facades.
- Keep source hygiene checks preventing accidental high-level imports into
  contract or lower-level packages.
- Add new authority domains as named packages only when they own durable
  runtime behavior, not routine helper logic.
