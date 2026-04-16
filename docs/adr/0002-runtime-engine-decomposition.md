# ADR-0002: Decompose the runtime engine by owned seams

**Status**: Accepted  
**Date**: 2026-04-16  
**Deciders**: Millrace maintainers

## Context

`RuntimeEngine` had become the main concentration point for orchestration logic in the thin-core runtime. It owned startup, mailbox intake, watcher intake, queue activation, reconciliation, result application, lock handling, and stage-request construction in one file. The code was still testable and stable, but it had multiple reasons to change and required broad file context for even focused edits.

The main architectural risk was not size by itself. The real problem was that mailbox semantics, watcher semantics, queue activation, and stage-result application evolve for different reasons and should not require repeated edits to one monolithic engine module.

We needed a decomposition that reduced cognitive load while preserving the runtime contract, stage progression behavior, and current import surface for callers and tests.

## Decision

Millrace will refactor `RuntimeEngine` into a `millrace_ai.runtime` package whose public surface remains stable, while moving major owned concerns into dedicated modules such as mailbox intake, watcher intake, activation, reconciliation, result application, and stage-request construction.

`RuntimeEngine` itself remains the coordination facade. It is not being removed; it is being narrowed so it orchestrates other runtime-owned modules instead of implementing every concern inline.

## Alternatives considered

- **Keep the runtime module intact and tolerate the concentration**: Rejected because the file already had multiple reasons to change and would become harder to evolve as run inspection, control routing, and workspace packages grow around it.
- **Split aggressively into many tiny helper files immediately**: Rejected because weak seams would produce more navigation cost without improving ownership.
- **Push runtime logic up into CLI or down into persistence modules**: Rejected because that would blur boundaries rather than clarify them.

## Consequences

**Positive:**
- Runtime ownership boundaries become explicit and easier to reason about.
- Mailbox, watcher, reconciliation, and result-application changes can happen with narrower diffs.
- Later control-plane and workspace refactors have cleaner seams to build on.

**Negative / accepted costs:**
- Runtime refactoring has the highest blast radius in the package and must be treated as an extraction exercise with strong regression coverage.
- Same-name module-to-package transitions mean compatibility must come from package `__init__.py` surfaces, not file shims.
- The runtime package may temporarily carry thin facades while imports migrate.

**Neutral but notable:**
- The decomposition is guided by reasons to change, not by line-count budgets. Cohesive modules remain intact if they still read as one domain concern.

## Follow-up

- Keep `RuntimeEngine` exported from `millrace_ai.runtime`.
- Move run inspection into the runtime package as a first-class long-term home.
- Re-run runtime, handoff, and inspection tests after each extraction slice.
