# Runtime Docs Index

This directory contains the maintained Millrace runtime docs for the `millrace_ai` package and the `millrace-agents` workspace model.

## Current Source Layout

- source files live under `src/millrace_ai/`
- the CLI implementation lives under `src/millrace_ai/cli/`
- the runtime engine and control seams live under `src/millrace_ai/runtime/`
- workspace persistence helpers live under `src/millrace_ai/workspace/`
- tests mirror those ownership boundaries under `tests/`

## Maintained Runtime Docs

- `millrace-runtime-architecture.md`: module topology, ownership boundary, and runtime lifecycle.
- `millrace-cli-reference.md`: current CLI command surface and defaults.
- `millrace-entrypoint-mapping.md`: canonical draft entrypoint mapping to packaged and deployed paths.
- `millrace-runner-architecture.md`: stage runner resolution, adapter contract, and Phase 2 shim compatibility seam.
- `../source-package-map.md`: old-to-new module mapping and preserved compatibility facades.
- `docs/RUNTIME_DEEP_DIVE.md`: stable runtime deep-dive portal.

## Verification Commands

```bash
uv run --extra dev python -m pytest -q
uv run --with ruff ruff check src/millrace_ai tests
uv run --with mypy mypy src/millrace_ai
```

## Cleanup Note

Legacy deep docs from the prior runtime were removed during v0.11.0 remediation cleanup to avoid stale guidance.
