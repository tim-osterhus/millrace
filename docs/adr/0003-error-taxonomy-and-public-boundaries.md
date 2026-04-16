# ADR-0003: Root service-layer failures in a shared MillraceError hierarchy

**Status**: Accepted  
**Date**: 2026-04-16  
**Deciders**: Millrace maintainers

## Context

The thin-core runtime already had strong model validation, but many service-layer boundaries still raised raw `ValueError` or `RuntimeError`. That was acceptable inside validators, yet it made higher-level runtime, CLI, queue, and control code harder to reason about because unrelated failures shared the same generic exception types.

At the same time, the package had already started to accumulate domain-specific error types such as mode and runner failures. Those exceptions existed as isolated islands rather than as a coherent project-level taxonomy. As the package moves into more explicit domain packages, leaving error boundaries ad hoc would make that refactor harder, not easier.

We wanted a shared error vocabulary that distinguishes service-layer boundaries from validation semantics without forcing an immediate rewrite of every raise site.

## Decision

Millrace will define a root `MillraceError` hierarchy for service-layer and operator-facing failures. The foundation includes specific categories for configuration, workspace state, queue state, runtime lifecycle, control routing, and asset validation concerns.

Existing domain-specific exceptions will subclass that hierarchy where they naturally fit. Validator-oriented `ValueError` usage remains valid inside Pydantic or schema-validation boundaries when those semantics are part of the contract.

## Alternatives considered

- **Keep using raw built-in exceptions everywhere**: Rejected because it hides boundary meaning and makes caller-side handling coarse and error-prone.
- **Wrap every failure immediately in one umbrella exception**: Rejected because it would destroy useful distinctions and make debugging harder.
- **Replace validator `ValueError` usage too**: Rejected because it would fight the existing validation framework and blur the line between schema errors and service errors.

## Consequences

**Positive:**
- Public/runtime-facing failures become more legible to callers and tests.
- Later refactor slices can adopt a shared taxonomy instead of inventing new exception islands.
- Exception chains can preserve lower-level causes while still surfacing meaningful top-level categories.

**Negative / accepted costs:**
- The hierarchy must be adopted incrementally; for a time the package will contain both legacy raw raises and new typed boundaries.
- Poorly chosen subclasses could become decorative rather than meaningful, so migrations must be driven by real boundary semantics.
- Tests need to shift from generic exception assertions toward intentional boundary assertions.

**Neutral but notable:**
- This ADR does not claim that every failure should become a custom type. The boundary matters more than the count of classes.

## Follow-up

- Introduce `millrace_ai.errors` as the shared home for the base taxonomy.
- Rehome existing domain exceptions under the hierarchy.
- Migrate service-layer raw raises in later slices, leaving validation-native exceptions alone where appropriate.
