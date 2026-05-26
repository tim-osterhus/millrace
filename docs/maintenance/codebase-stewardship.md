# Millrace Codebase Stewardship

This directory is the maintainer-facing map for repository structure,
documentation ownership, refactor candidates, and advisory maintainability
checks. Use it before broad runtime, compiler, graph, or package-boundary work.

The stewardship docs are evidence and coordination material. They should make
future changes easier to review, but they do not replace the runtime, graph, or
operator docs that own shipped behavior.

## Current Stewardship References

- `documentation-ownership.md`: canonical owner document for each major
  architecture topic, plus duplicated or stale sections to clean up in later
  passes.

## Planned Additions

The maintainability refactor wave will add these pages as their packets land:

- `documentation-freshness-matrix.md`: source areas mapped to docs that should
  be checked when those areas change.
- `refactor-candidate-register.md`: single source of truth for candidate ids,
  reasons to change, risk, tests, and extraction strategy.
- `blueprint-effect-behavior-matrix.md`: Blueprint runtime-effect behavior and
  parity requirements before declarative migration.
- behavior contract inventories for compiler validation, workflow primitives,
  request context, recovery, status, Doctor, and runner normalization.

## Stewardship Rules

- Treat line counts and import counts as prompts for review, not as refactor
  conclusions.
- Identify one reason to change before splitting a module.
- Add or identify direct characterization tests before moving subtle runtime or
  compiler behavior.
- Preserve public compatibility facades unless an ADR explicitly changes the
  public API.
- Keep runtime mutation authority in runtime-owned paths: stages produce
  artifacts, while the runtime mutates durable queue, lifecycle, and workspace
  state.
- Keep AutoLab implementation deferred until the post-refactor architecture is
  documented and the AutoLab spec is rebaselined.

