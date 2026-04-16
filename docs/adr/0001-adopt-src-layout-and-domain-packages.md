# ADR-0001: Adopt src layout and domain packages

**Status**: Accepted  
**Date**: 2026-04-16  
**Deciders**: Millrace maintainers

## Context

The thin-core Millrace runtime had already become a real Python package with packaging, tests, wheel builds, and type checking, but it still lived at the repository root. That layout made local imports easier to get accidentally right, especially during refactors, because `pytest`, ad hoc scripts, and editable installs could all succeed while importing directly from the checkout root rather than from the installed package boundary.

At the same time, the package directory itself had become a flat collection of top-level modules. The code was materially healthier than the directory shape suggested, but the package tree did not tell a reader where CLI, runtime orchestration, workspace persistence, configuration, and assets were meant to live over time.

We wanted a refactor sequence that improved import discipline first and then made room for more legible ownership boundaries without forcing a large one-shot rewrite.

## Decision

Millrace will use a `src/` package layout, with importable code under `src/millrace_ai/`. Packaging, wheel builds, and local verification commands will treat `src/millrace_ai` as the source root.

Future structural refactors will also move from a flat top-level package toward named domain packages such as `cli`, `runtime`, `workspace`, `assets`, and `config`, but the `src/` move itself is kept separate from those deeper decompositions so failures remain easy to classify.

## Alternatives considered

- **Keep the package at the repository root**: Rejected because it preserves accidental-import ambiguity and weakens confidence that local verification is exercising the installed package boundary.
- **Move to `src/` and split all major domains in the same slice**: Rejected because it would conflate packaging failures with architectural extraction failures and make regressions harder to localize.
- **Leave the package flat and rely on documentation alone**: Rejected because the directory tree is itself a high-signal architectural surface and should tell a coherent story without requiring a separate explainer.

## Consequences

**Positive:**
- Local verification now exercises the installed package boundary more honestly.
- Wheel packaging, type metadata, and runtime assets are forced through the same structure that downstream users see.
- The package tree has a stable root for later domain-oriented moves.

**Negative / accepted costs:**
- Tests, packaging metadata, and build scripts must be updated together or the move will break in subtle ways.
- Some path references in tests and docs become noisier during the transition because they must acknowledge `src/millrace_ai`.
- The `src/` move alone does not improve runtime module ownership; it only creates the safer foundation for later slices.

**Neutral but notable:**
- Public import paths remain `millrace_ai.*`; the layout change is internal to the repository structure rather than a consumer-facing namespace rename.

## Follow-up

- Move CLI, runtime, workspace, assets, and config concerns into domain packages incrementally.
- Keep wheel-asset and smoke verification tests in place so later topology work cannot silently break packaging.
