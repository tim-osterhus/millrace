# Runtime Docs Index

This directory is the maintained technical reference surface for runtime
behavior in the `millrace_ai` package and the `millrace-agents` workspace
model.

Use this index when you already know you are working with Millrace runtime
behavior, command surfaces, compiler behavior, mode topology, runner dispatch,
or workspace lifecycle details.

If you need the whole repository documentation map first, start with
`../doc-index.md`. If you want one dense system overview before drilling into
runtime references, start with `../millrace-technical-overview.md`.
If you need the shipped mode-to-plane graph matrix or per-plane topology, use
`../graphs/graphs-index.md`. If you need conceptual config aliases and fixture
mappings, use `../graphs/config-mapping.md`.

## Maintained Runtime Docs

- `millrace-runtime-architecture.md`: workspace ownership model, artifact model, module topology, and tick lifecycle.
- `millrace-runtime-authority-map.md`: trace-by-trace ownership for intake,
  including idea-intake normalization and legacy compatibility, queue
  selection, runner requests, artifacts, result normalization, and durable
  runtime mutation.
- `millrace-arbiter-and-completion-behavior.md`: closure-target lineage model, Arbiter artifacts, compiler-driven backlog-drain behavior, and the shipped broad-audit posture used by closure-capable LAD and Blueprint Planning modes when narrow evidence is not enough.
- `millrace-cli-reference.md`: current CLI command surface, aliases, and operator-facing command groups.
- `millrace-usage-governance.md`: shipped v1 default-off runtime-owned usage accounting, automatic pause/resume behavior, subscription telemetry, config-reload next-tick behavior, and operator visibility.
- `millrace-execution-capabilities.md`: typed execution capability grants, runtime pre-dispatch gating, approval-required grants, advisory enforcement language, and inspection output.
- `millrace-compiler-and-frozen-plans.md`: mode resolution, asset loading, compiled-plan freezing, workflow primitive authority, required-extension validation, scheduler lane policy, workspace schema epoch checks, compile-input fingerprints, current-vs-stale plan status, baseline manifest identity, stale-plan refusal, and `compile validate` / `compile show`.
- `millrace-compiled-stage-graphs-and-run-traces.md`: compiled topology exports, per-run `run_trace.json` artifacts, CLI trace inspection, and graph-resolved versus inferred terminal metadata.
- `millrace-modes-and-loops.md`: shipped mode ids, loop ids, stage topology, opt-in integrated quality loops, learning plane, Librarian optional-skill preparation, no-op learning terminals, compiled concurrency policy, learning triggers, and compile-time stage maps.
- `millrace-blueprint-planning.md`: opt-in Blueprint Planning loop behavior, runtime effects, draft/packet/evaluation/repair artifacts, closure suppression, and operator inspection.
- `millrace-loop-authoring.md`: maintainer rules for changing loop and mode assets without violating compiler and contract boundaries.
- `millrace-runner-architecture.md`: runner dispatch, adapter contract, artifact model, deterministic request-context artifacts, compiled request identity, and Codex/Pi adapter behavior.
- `millrace-workspace-baselines-and-upgrades.md`: explicit workspace initialization, baseline manifest identity, schema epoch archive/reset behavior, upgrade preview/apply classifications, and the managed workspace baseline lifecycle.
- `millrace-entrypoint-mapping.md`: packaged-source-to-deployed-workspace entrypoint mapping and skill-only advisory expectations.
- `millrace-runtime-error-codes.md`: runtime-owned post-stage failure codes and failure-origin diagnostics consumed by repair-oriented stages.

## Workspace Memory Boundaries

Runtime artifacts live under `millrace-agents/` in each initialized
workspace. Source edits live in the repository source tree and should not be
treated as runtime state just because a stage produced them. Generated
workspace-map files under `millrace-agents/workspace-map/generated/` plus
`millrace-agents/workspace-map/manifest.json` are runtime refresh artifacts;
`millrace-agents/workspace-map/index.md` is seeded starter/index guidance; and
curated wiki pages under `millrace-agents/workspace-map/wiki/` are
operator-maintained workspace memory that upgrades must preserve unless an
operator chooses otherwise.

Stages may propose history updates by writing run-local artifacts such as
`history_entry.json`. The runtime owns the durable history log append and
rendering path, so stages should not rewrite the canonical history log
directly. `millrace-agents/MILLRACE.md` is shared operator guidance: Millrace
seeds and preserves it, the runtime wrapper includes it when stages run, and
the operator owns its content after seeding as workspace-local instructions.
Stage entrypoints do not carry a local duplicate of those shared instructions.

Updater may emit `workspace_map_update_request.json` when generated
workspace-map output looks stale, but in M1 that artifact is advisory only and
the runtime does not consume it automatically. Explicit
`millrace workspace-map refresh` remains the refresh mechanism.

New idea intake is staged under `millrace-agents/intake/ideas/inbox/`.
Normalization writes durable source markdown under
`millrace-agents/intake/sources/idea/`, normalized metadata under
`millrace-agents/intake/ideas/normalized/`, and archives consumed inputs.
Legacy idea-file compatibility remains available so older watcher-style intake
can still be normalized, archived, or diagnosed without becoming the preferred
write location.

## Suggested Reading Order

- Start with `millrace-runtime-architecture.md` if you need the overall runtime model.
- Read `millrace-arbiter-and-completion-behavior.md` next if you need the Arbiter-style completion model used by closure-capable LAD and Blueprint Planning modes.
- Use `millrace-cli-reference.md` if you need commands.
- Use `millrace-compiled-stage-graphs-and-run-traces.md` when you need to distinguish legal compiled topology from what one concrete run actually did.
- Use `millrace-usage-governance.md` before enabling automatic runtime pause/resume rules for token or subscription quota protection.
- Use `millrace-compiler-and-frozen-plans.md` and `millrace-modes-and-loops.md` if you need to understand what the compiler is freezing, which planes are selected, and how current-vs-stale compiled identity is determined.
- Use `../graphs/graphs-index.md` when you need the concrete shipped graph
  configurations and per-plane node/edge references.
- Use `../graphs/config-mapping.md` when you need the conceptual config
  mapping for `standard_millrace`, `learning_enabled_millrace`,
  `minimal_three_plane`, `recovery_heavy_millrace`, or
  `generic_two_plane_fixture`.
- Use `millrace-blueprint-planning.md` before selecting or troubleshooting
  `blueprint_lad_codex` or `blueprint_learning_lad_codex`.
- Use `millrace-modes-and-loops.md` before selecting `lad_codex_integrated`
  or `learning_lad_codex_integrated`; those modes intentionally add an Integrator
  pass after Builder for higher assurance.
- Use `millrace-modes-and-loops.md` before selecting
  `efficient_learning_lad_mixed`; it keeps LAD topology but carries a
  mode-local mixed Codex/Pi model/depth profile.
- Use `millrace-workspace-baselines-and-upgrades.md` when you need the explicit `init` / `upgrade` workflow for managed workspace assets.
- Use `millrace-cli-reference.md` when you need `millrace skills`, daemon
  monitor, approvals, usage-governance, or status command details.
- Use `millrace-execution-capabilities.md` when a run blocks before dispatch
  because a required grant is denied, unsupported, or waiting on operator
  approval.
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

For advisory source/documentation shape review, run:

```bash
uv run python scripts/maintenance/repo_shape_report.py
```
