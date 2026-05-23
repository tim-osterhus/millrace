# Runtime Docs Index

This directory is the maintained technical reference surface for the
`millrace_ai` package and the `millrace-agents` workspace model.

Use this index when you already know you are in Millrace and need authoritative
runtime behavior, command, compiler, or authoring details.

If you want one top-level synthesis document before drilling into the narrower
runtime references, start with `../millrace-technical-overview.md`.

## Maintained Runtime Docs

- `millrace-runtime-architecture.md`: workspace ownership model, artifact model, module topology, and tick lifecycle.
- `millrace-arbiter-and-completion-behavior.md`: closure-target lineage model, Arbiter artifacts, compiler-driven backlog-drain behavior, and the shipped broad-audit posture used when narrow evidence is not enough.
- `millrace-cli-reference.md`: current CLI command surface, aliases, and operator-facing command groups.
- `millrace-usage-governance.md`: shipped v1 default-off runtime-owned usage accounting, automatic pause/resume behavior, subscription telemetry, config-reload next-tick behavior, and operator visibility.
- `millrace-execution-capabilities.md`: typed execution capability grants, runtime pre-dispatch gating, approval-required grants, advisory enforcement language, and inspection output.
- `millrace-compiler-and-frozen-plans.md`: mode resolution, asset loading, compiled-plan freezing, workflow primitive authority, scheduler lane policy, workspace schema epoch checks, compile-input fingerprints, current-vs-stale plan status, baseline manifest identity, stale-plan refusal, and `compile validate` / `compile show`.
- `millrace-compiled-stage-graphs-and-run-traces.md`: compiled topology exports, per-run `run_trace.json` artifacts, CLI trace inspection, fallback behavior for older runs, and Web UI Flow data sources.
- `millrace-modes-and-loops.md`: shipped mode ids, loop ids, stage topology, opt-in integrated quality loops, learning plane, Librarian optional-skill preparation, no-op learning terminals, compiled concurrency policy, learning triggers, and compile-time stage maps.
- `millrace-blueprint-planning.md`: opt-in Blueprint Planning loop behavior, runtime effects, draft/packet/evaluation/repair artifacts, closure suppression, and operator inspection.
- `millrace-loop-authoring.md`: maintainer rules for changing loop and mode assets without violating compiler and contract boundaries.
- `millrace-runner-architecture.md`: runner dispatch, adapter contract, artifact model, deterministic request-context artifacts, compiled request identity, and Codex/Pi adapter behavior.
- `millrace-workspace-baselines-and-upgrades.md`: explicit workspace initialization, baseline manifest identity, schema epoch archive/reset behavior, upgrade preview/apply classifications, and the managed workspace baseline lifecycle.
- `millrace-entrypoint-mapping.md`: packaged-source-to-deployed-workspace entrypoint mapping and skill-only advisory expectations.
- `millrace-runtime-error-codes.md`: runtime-owned post-stage failure codes and failure-origin diagnostics consumed by repair-oriented stages.
- `../source-package-map.md`: old-to-new module mapping and intentionally preserved compatibility facades for maintainers.
- `../adr/`: accepted architecture decisions, including the compiled-plan,
  workflow-primitive authority, workspace-baseline,
  runtime-authority-package, contract-facade, and stage-metadata decisions.

## Suggested Reading Order

- Start with `millrace-runtime-architecture.md` if you need the overall runtime model.
- Read `millrace-arbiter-and-completion-behavior.md` next if you need to understand how backlog drain now reaches true completion.
- Use `millrace-cli-reference.md` if you need commands.
- Use `millrace-compiled-stage-graphs-and-run-traces.md` when you need to distinguish legal compiled topology from what one concrete run actually did.
- Use `millrace-usage-governance.md` before enabling automatic runtime pause/resume rules for token or subscription quota protection.
- Use `millrace-compiler-and-frozen-plans.md` and `millrace-modes-and-loops.md` if you need to understand what the compiler is freezing, which planes are selected, and how current-vs-stale compiled identity is determined.
- Use `millrace-blueprint-planning.md` before selecting or troubleshooting
  `blueprint_codex` or `blueprint_learning_codex`.
- Use `millrace-modes-and-loops.md` before selecting `default_codex_integrated`
  or `learning_codex_integrated`; those modes intentionally add an Integrator
  pass after Builder for higher assurance.
- Use `millrace-workspace-baselines-and-upgrades.md` when you need the explicit `init` / `upgrade` workflow for managed workspace assets.
- Use `millrace-cli-reference.md` when you need `millrace skills`, daemon
  monitor, approvals, usage-governance, or status command details.
- Use `millrace-execution-capabilities.md` when a run blocks before dispatch
  because a required grant is denied, unsupported, or waiting on operator
  approval.
- Use `../../packages/millrace-web/README.md` when you need the optional local
  read-only dashboard. `millrace-web` is a separate package and is not part of
  the base `millrace-ai` wheel.
- Use `millrace-loop-authoring.md` before changing loop, mode, or stage-selection assets.
- Use `millrace-runner-architecture.md` if you are changing runner dispatch, adapter behavior, or the compiled identity carried through runtime requests and inspection.

## Verification Commands

```bash
uv run --extra dev python -m pytest -q
uv run --with ruff ruff check src/millrace_ai tests
uv run --with mypy mypy src/millrace_ai
```

During package-boundary refactors, also run the focused guardrail suite:

```bash
uv run --extra dev python -m pytest tests/test_import_cycles.py tests/test_source_hygiene.py -q
```
