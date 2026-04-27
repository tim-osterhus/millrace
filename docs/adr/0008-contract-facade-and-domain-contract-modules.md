# ADR-0008: Keep `millrace_ai.contracts` as a facade over domain contract modules

**Status**: Accepted
**Date**: 2026-04-27
**Deciders**: Millrace maintainers

## Context

`millrace_ai.contracts` is one of Millrace's most important Python surfaces.
Runtime snapshots, work documents, stage results, mailbox envelopes, mode
definitions, compile diagnostics, recovery counters, and shared enums all flow
through it.

As the project grew, the single `contracts.py` module stayed defensible because
it owned typed contracts, but it became cognitively expensive. A maintainer who
needed to inspect stage-result validation also had to scroll past work
documents, mode definitions, mailbox payloads, runtime snapshots, and recovery
models. That made the most stable public surface harder to review and made
small contract edits look riskier than they were.

The public import contract still matters. Many callers import from
`millrace_ai.contracts`, and published code should not be forced to know the
internal contract module layout.

## Decision

Millrace will keep `millrace_ai.contracts` as the stable public contract facade
and implement it as a package of named domain modules.

The package groups contracts by reason to change:

- `base.py` for the shared Pydantic base model
- `enums.py` for foundational enum identities
- `stage_metadata.py` for stage legality and marker policy
- `token_usage.py` for token accounting
- `work_documents.py` for task/spec/incident/learning-request and closure
  target documents
- `stage_results.py` for stage-result envelopes
- `loop_config.py` and `modes.py` for loop and mode assets
- `compile_diagnostics.py` for compiler diagnostics
- `runtime_snapshot.py` and `runtime_errors.py` for runtime state/error
  contracts
- `mailbox.py` for control-envelope payloads
- `recovery.py` for recovery counters

`contracts/__init__.py` explicitly re-exports the public names callers use.
Contract submodules must not import from runtime, workspace, CLI, compiler,
assets, or runners.

## Alternatives considered

- **Leave contracts in one large module**: Rejected because the module combined
  too many independent contract families and made focused review harder.
- **Require callers to import from individual contract modules immediately**:
  Rejected because it would create noisy migration work without improving
  external behavior.
- **Create many tiny compatibility facades at the package root**: Rejected
  because one explicit `millrace_ai.contracts` facade is enough and easier to
  characterize.
- **Move contracts near their consuming domains**: Rejected because these
  contracts describe persisted runtime artifacts shared across domains; putting
  them beside consumers would weaken the dependency direction.

## Consequences

**Positive:**
- Contract families can be reviewed and tested independently.
- Public imports remain stable for callers.
- Contract dependency direction is explicit and enforceable.
- Future schema work has clearer module homes.

**Negative / accepted costs:**
- `contracts/__init__.py` must be kept boring and explicit.
- Some names that were de facto importable needed to be made explicit facade
  exports for type-checking clarity.
- Moving Pydantic models requires strong serialization and validation tests to
  prevent subtle schema drift.

**Neutral but notable:**
- This decision is about code ownership, not schema migration. Persisted JSON
  shapes remain stable unless a separate migration is approved.

## Follow-up

- Keep public export tests for `millrace_ai.contracts`.
- Keep source hygiene tests preventing contract submodules from importing
  higher-level runtime domains.
- When adding a new persisted artifact family, choose the contract submodule by
  artifact ownership rather than by whichever runtime component currently
  consumes it.
