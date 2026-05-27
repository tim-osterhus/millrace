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
- `documentation-freshness-matrix.md`: source areas mapped to docs that should
  be checked when those areas change.
- `refactor-candidate-register.md`: single source of truth for candidate ids,
  reasons to change, risk, tests, and extraction strategy.
- `blueprint-effect-behavior-matrix.md`: Blueprint runtime-effect behavior and
  parity requirements before declarative migration.
- `compiler-validation-contracts.md`: compiler validator groups, diagnostic
  substrings, direct tests, and extraction order for MR-MAINT-003.
- `workflow-primitive-contract-family-inventory.md`: public primitive contract
  families, consumers, tests, proposed destinations, and compatibility needs.
- `request-context-contracts.md`: generic request-context contracts separated
  from Blueprint-specific request/repair context coupling.
- `recovery-status-doctor-runner-contracts.md`: recovery, status, Doctor, and
  runner-normalization contracts for low-risk and recovery refactors.
- `public-api-compatibility-inventory.md`: import and symbol surfaces that must
  survive the follow-up source-to-package refactors.

## Follow-Up Refactor Baseline

Observed on Windows before follow-up Batch 0 source movement:

- branch: `main`
- commit: `18fd1339c15427552d4107f3f6c5a15c28391ab6`
- `uv run --extra dev python -m pytest -q`: `1168 passed` in `1:30:18`
- `uv run --with ruff ruff check src/millrace_ai tests scripts`: passed
- `uv run --with mypy mypy src/millrace_ai`: passed across `254` source files
- `uv run python scripts/maintenance/repo_shape_report.py`: passed

Largest remaining source modules at this baseline:

- `src/millrace_ai/runtime/effects/operations.py`: 2433 lines
- former `compilation/validation.py`: 1382 lines
- `src/millrace_ai/architecture/workflow_primitives/`: 1277 lines before the
  package scaffold split
- `src/millrace_ai/runtime/blocked_recovery.py`: 1159 lines
- `src/millrace_ai/runtime/request_context.py`: 987 lines
- `src/millrace_ai/runtime/completion_behavior.py`: 892 lines
- `src/millrace_ai/runtime/supervisor.py`: 846 lines
- `src/millrace_ai/workspace/operator_interventions.py`: 778 lines
- `src/millrace_ai/runtime/error_recovery.py`: 775 lines
- `src/millrace_ai/workspace/queue_selection.py`: 773 lines

These metrics are not split criteria by themselves. They identify where the
follow-up packet wave should look for cohesive reasons to change.

## Advisory Report

Run the repository shape report when source ownership or documentation shape
changes:

```bash
uv run python scripts/maintenance/repo_shape_report.py
```

The report is advisory for module size, import breadth, suspicious names, and
ignored local artifacts. It fails only on objective integrity issues: concrete
import cycles, tracked generated/local artifacts, or Markdown docs that
reference source paths missing from tracked source.

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
