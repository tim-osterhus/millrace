# ADR-0009: Make stage metadata the single source of stage legality

**Status**: Accepted, amended by ADR-0013 and the JSON-backed shipped-stage registry implementation
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

Millrace needed one typed registry surface for shipped stages, with tests that
fail if assets or runner behavior drift from it. ADR-0013 later narrowed that
surface: `stage_metadata.py` is the shipped registry instance, not universal
runtime authority for custom graph or stage-kind configurations.

## Decision

Millrace treats `src/millrace_ai/contracts/stage_metadata.py` as the shipped
registry instance for shipped stage legality. The implementation now loads its
metadata from the canonical JSON stage-kind assets under
`src/millrace_ai/assets/registry/stage_kinds/` for known public stage enum
members.

The registry owns:

- stage to plane mapping
- stage enum lookup by value and plane
- legal terminal results and prompt terminal markers
- running status markers
- blocked terminal results by plane
- result-class policy by terminal result

Contracts, runner request defaults, terminal-result normalization, entrypoint
linting, and graph stage lookup derive shipped-stage defaults from that facade.
Stage-kind assets remain the compiled graph materialization input and are now
the data source for the shipped facade, while fixture/custom stage kinds remain
discoverable outside the shipped-stage lookup surface.

## Alternatives considered

- **Keep metadata duplicated near each consumer**: Rejected because stage
  legality drift is a runtime correctness risk.
- **Use stage-kind JSON as the only source of truth**: Initially rejected
  because Python contracts and type checking still need enum-level stage
  identity and helpers. The implemented compromise keeps public enum helpers
  while loading shipped-stage legality data from JSON assets.
- **Generate Python enums from JSON assets**: Rejected for now because it would
  add build-time complexity without solving the immediate ownership problem.
- **Move all stage legality into prompt entrypoints**: Rejected because prompt
  prose is advisory and cannot be the durable runtime authority.

## Consequences

**Positive:**
- Adding or changing shipped-stage legality has one obvious data location: the
  shipped stage-kind JSON asset.
- Runner prompts, normalization, contracts, graph lookup, and built-in
  stage-kind validation share one legality source.
- Tests can prove stage-kind assets and prompt defaults remain aligned.
- Unknown or wrong-plane stage lookups fail loudly.

**Negative / accepted costs:**
- The registry facade still depends on public Python stage enums, so adding a
  shipped runtime stage remains a code-and-asset change.
- Fixture/custom stage-kind assets are filtered out of the shipped facade; they
  must be validated through asset discovery and compiled-plan paths.
- Custom discovered stage kinds still require care because the shipped metadata
  registry only covers built-in stage identities.

**Neutral but notable:**
- The compiled plan remains runtime authority. Stage metadata defines shipped
  stage legality that feeds assets and request normalization; it does not
  replace compiled graph topology.

## Follow-up

- Keep tests asserting every shipped stage has one metadata entry and one
  plane loaded from stage-kind assets.
- Keep tests proving shipped stage-kind assets, prompt defaults, and registry
  lookups remain aligned.
- If custom external stage kinds become a supported product feature, define how
  their metadata is registered without weakening shipped-stage guarantees.
