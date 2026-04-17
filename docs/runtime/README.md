# Runtime Docs Index

This directory is the maintained technical reference surface for the
`millrace_ai` package and the `millrace-agents` workspace model.

Use this index when you already know you are in Millrace and need authoritative
runtime behavior, command, compiler, or authoring details.

## Maintained Runtime Docs

- `millrace-runtime-architecture.md`: workspace ownership model, artifact model, module topology, and tick lifecycle.
- `millrace-cli-reference.md`: current CLI command surface, aliases, and operator-facing command groups.
- `millrace-compiler-and-frozen-plans.md`: mode resolution, asset loading, stage-plan freezing, persisted compile artifacts, and `compile validate` / `compile show`.
- `millrace-modes-and-loops.md`: shipped mode ids, loop ids, stage topology, `terminal_results`, and compile-time stage maps.
- `millrace-loop-authoring.md`: maintainer rules for changing loop and mode assets without violating compiler and contract boundaries.
- `millrace-runner-architecture.md`: runner dispatch, adapter contract, artifact model, and Codex adapter behavior.
- `millrace-entrypoint-mapping.md`: canonical draft-to-packaged-to-deployed entrypoint mapping and skill-only advisory expectations.
- `millrace-runtime-error-codes.md`: runtime-owned post-stage failure codes consumed by repair-oriented stages.
- `../source-package-map.md`: old-to-new module mapping and intentionally preserved compatibility facades for maintainers.

## Suggested Reading Order

- Start with `millrace-runtime-architecture.md` if you need the overall runtime model.
- Use `millrace-cli-reference.md` if you need commands.
- Use `millrace-compiler-and-frozen-plans.md` and `millrace-modes-and-loops.md` if you need to understand what the compiler is freezing.
- Use `millrace-loop-authoring.md` before changing loop, mode, or stage-selection assets.
- Use `millrace-runner-architecture.md` if you are changing runner dispatch or adapter behavior.

## Verification Commands

```bash
uv run --extra dev python -m pytest -q
uv run --with ruff ruff check src/millrace_ai tests
uv run --with mypy mypy src/millrace_ai
```
