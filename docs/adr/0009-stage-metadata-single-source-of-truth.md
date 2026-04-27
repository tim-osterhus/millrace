# ADR-0009: Make stage metadata the single source of stage legality

**Status**: Accepted
**Date**: 2026-04-27
**Deciders**: Millrace maintainers

## Context

Millrace stage identity is foundational. Stage names determine plane
membership, legal terminal markers, running status markers, result-class
policy, prompt instructions, normalization behavior, stage-kind asset
validation, and graph routing.

Before the stage metadata registry, those facts were represented in several
places: contract validators, runner request defaults, normalization helpers,
entrypoint linting, stage-kind assets, and graph lookup helpers. Most copies
matched, but duplication in this area is a correctness risk. If one surface
accepts a marker that another surface does not, the runtime may ask a stage for
one result while normalizing or routing a different one.

Millrace needed one typed registry that future maintainers can edit when adding
or changing a shipped stage, with tests that fail if assets or runner behavior
drift from it.

## Decision

Millrace will treat `src/millrace_ai/contracts/stage_metadata.py` as the
canonical source for shipped stage legality.

The registry owns:

- stage to plane mapping
- stage enum lookup by value and plane
- legal terminal results and prompt terminal markers
- running status markers
- blocked terminal results by plane
- result-class policy by terminal result

Contracts, runner request defaults, terminal-result normalization, entrypoint
linting, graph stage lookup, and built-in stage-kind asset validation derive
from that registry.

Stage-kind assets remain the compiled graph materialization input, but built-in
stage-kind assets must align with the typed metadata registry for shipped stage
identities.

## Alternatives considered

- **Keep metadata duplicated near each consumer**: Rejected because stage
  legality drift is a runtime correctness risk.
- **Use stage-kind JSON as the only source of truth**: Rejected because Python
  contracts and type checking still need enum-level stage identity and helpers
  before assets are loaded.
- **Generate Python enums from JSON assets**: Rejected for now because it would
  add build-time complexity without solving the immediate ownership problem.
- **Move all stage legality into prompt entrypoints**: Rejected because prompt
  prose is advisory and cannot be the durable runtime authority.

## Consequences

**Positive:**
- Adding or changing a shipped stage has one obvious metadata location.
- Runner prompts, normalization, contracts, graph lookup, and built-in
  stage-kind validation share one legality source.
- Tests can prove stage-kind assets and prompt defaults remain aligned.
- Unknown or wrong-plane stage lookups fail loudly.

**Negative / accepted costs:**
- The registry is a visible source file that must be updated for any shipped
  stage change.
- Built-in stage-kind asset validation now has one more failure mode when assets
  drift from typed metadata.
- Custom discovered stage kinds still require care because the shipped metadata
  registry only covers built-in stage identities.

**Neutral but notable:**
- The compiled plan remains runtime authority. Stage metadata defines shipped
  stage legality that feeds assets and request normalization; it does not
  replace compiled graph topology.

## Follow-up

- Keep tests asserting every shipped stage has one metadata entry and one
  plane.
- Keep tests comparing built-in stage-kind assets to the metadata registry.
- If custom external stage kinds become a supported product feature, define how
  their metadata is registered without weakening shipped-stage guarantees.
