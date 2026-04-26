# Changelog

All notable user-facing changes to Millrace are documented in this file.

This changelog is written for people first. It uses newest-first release order,
ISO `YYYY-MM-DD` dates, and change categories inspired by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version tags follow
SemVer-style numbering per [Semantic Versioning](https://semver.org/) while
Millrace is still in the pre-1.0 stabilization period; compatibility notes call
out operator-visible contract changes when they matter.

This file starts at `0.13.0`, the current documented public baseline.

## [Unreleased]

No unreleased changes recorded yet.

## [0.15.2] - 2026-04-26

`0.15.2` hardens compiled-plan authority and workspace lifecycle behavior while
moving repository-local skills-pipeline infrastructure out of the core Millrace
package.

### Added

- Added explicit workspace initialization and baseline manifest tracking for
  deployed runtime assets.
- Added workspace baseline upgrade preview/apply support through the CLI.
- Added compile-input fingerprinting and currentness reporting so operators can
  see whether a persisted compiled plan still matches current config and assets.
- Added richer compiled node contracts, including allowed result classes by
  outcome and frozen skill/entrypoint asset references.
- Added generic workspace-local mode and graph discovery so specialized
  workflows can provide their own assets without shipping them in the Millrace
  package.

### Changed

- Runtime consumers now drive stage requests, routing, recovery, and result
  validation from the compiled plan instead of reconstructing authority from
  mutable source assets.
- `millrace status` now surfaces compile currentness and baseline lifecycle
  metadata.
- Repository-local skills-pipeline mode, loop, graph, and entrypoint assets are
  no longer packaged with Millrace; they belong in their owning workspace or
  lab overlay.

### Fixed

- Fixed stage-result normalization and validation so runner outputs must match
  the compiled stage request contract.
- Fixed baseline manifest seeding and stale compile handling so a failed
  recompile preserves the last known-good plan.

## [0.15.1] - 2026-04-25

`0.15.1` completes the Learning plane control surface by making runtime
learning requests fully distinguishable, targetable, auditable, and visible from
operator status output.

### Added

- Added `Target-Stage` and `Trigger-Metadata` fields to learning request
  documents so runtime-generated learning work can target a specific learning
  stage with durable trigger context.
- Added runtime evaluation of compiler-frozen learning trigger rules, including
  automatic enqueueing of targeted learning requests after matching execution
  stage outcomes.
- Added direct activation for targeted learning requests, allowing a generated
  Curator request to start at Curator instead of replaying the full learning
  loop.
- Added learning queue depth and learning status marker output to
  `millrace status show`.

### Fixed

- Fixed learning stage runner requests so they use
  `request_kind = "learning_request"` instead of the generic active-work-item
  request kind.
- Fixed skill revision evidence persistence so each stage request writes a
  request-specific evidence file instead of overwriting a single run-level file.

## [0.15.0] - 2026-04-25

`0.15.0` introduces the Learning plane with Analyst, Professor, and Curator
stages, packaged learning modes, and the `millrace skills` operator commands.

### Added

- Added the learning queue, learning status, and learning request document
  surfaces.
- Added learning graph, loop, stage-kind, entrypoint, and stage-core skill
  assets.
- Added `learning_codex` and `learning_pi` built-in modes.
- Added `millrace skills` commands for install, create, improve, promote,
  export, list, show, and search workflows.
- Added skill revision evidence snapshots for stage requests when a compiled
  learning graph is active.

## [0.14.1] - 2026-04-25

`0.14.1` packages the repository-local skills pipeline mode alongside a smaller,
more sustainable Pi event-log contract and cleans up asset-policy lint so
`millrace doctor` reports a clean workspace by default.

### Added

- Added the specialized `skills_pipeline_codex` built-in mode.
- Added `execution.skills_pipeline` and `planning.skills_pipeline` loop assets.
- Added pipeline-specific planning and execution entrypoints for the skills
  production flow.
- Added integration and asset coverage proving the compiler materializes the
  skills pipeline mode contract.

### Changed

- Added `runners.pi.event_log_policy` so Pi raw event-log retention is
  configurable.
- Changed Pi runner persistence to keep full raw `runner_events` only on failed
  runs by default.
- Filtered redundant Pi `message_update` snapshots out of persisted
  `runner_events` even when full logging is enabled.
- Updated runner and compiler documentation to describe the new skills pipeline
  mode and the slimmer Pi event-log behavior.

### Fixed

- Fixed asset-policy lint warnings in the `mechanic` planning entrypoint and the
  skill-creator reference assets so `millrace doctor` no longer reports those
  false-positive or incomplete-manifest warnings.

## [0.14.0] - 2026-04-24

`0.14.0` expands the compiler from a frozen stage-plan generator into the
runtime-authoritative graph compiler. The runtime still ships the same
`default_codex` and `default_pi` harness modes introduced in `0.13.0`, but the
compiled plan now owns the loop graph, node bindings, activation entries,
recovery policy, completion behavior, and post-stage routing semantics.

### Added

- Added the typed `millrace_ai.architecture` contract package for stage kinds,
  graph loops, and compiled graph materialization.
- Added packaged stage-kind registry assets for every shipped execution and
  planning stage.
- Added packaged graph-loop assets for `execution.standard` and
  `planning.standard`.
- Added `CompiledRunPlan` as the canonical persisted plan model in
  `compiled_plan.json`.
- Added compiled graph entry surfaces for task, spec, incident, and
  closure-target activation.
- Added compiled resume and threshold policy surfaces for fix-cycle exhaustion,
  blocked-stage recovery, consultant escalation, mechanic recovery, and
  closure-target completion behavior.
- Added `preview_graph_loop_plan()` so maintainers can materialize discovered
  graph loops without promoting them into the shipped runtime plan.

### Changed

- Rebuilt the compiler to materialize `execution_graph` and `planning_graph`
  from built-in mode, graph-loop, and stage-kind assets.
- Moved live runtime activation, stage-request binding, closure-target
  activation, recovery decisions, and post-stage routing onto the compiled graph
  plan.
- Kept legacy loop assets in the package as aligned reference assets, while the
  graph-loop and stage-kind surfaces are now the runtime authority for shipped
  defaults.
- Updated `millrace compile show` to print compiled entries, closure activation,
  completion behavior, node entrypoint contracts, required skills, attached
  skills, runner bindings, model bindings, and timeouts from the compiled graph.
- Canonicalized `standard_plain` to `default_codex` before diagnostics,
  persisted plan IDs, and runtime snapshot state are written.
- Updated compiler and runtime documentation around the single compiled-plan
  authority model.

### Removed

- Removed the old `FrozenRunPlan` / `FrozenStagePlan` contract surface from the
  public runtime contracts module.
- Removed the temporary shadow graph-plan artifact path; `compiled_plan.json` is
  now the single canonical compiled runtime plan.

### Compatibility Notes

- Existing operator mode selection remains compatible: `default_codex` and
  `default_pi` are still the canonical modes, and `standard_plain` still aliases
  to `default_codex`.
- Tooling that reads `compiled_plan.json` directly must expect the
  `CompiledRunPlan` graph shape instead of the old frozen stage-plan list.
- Workspaces should be recompiled after upgrading so runtime state points at the
  current compiled graph plan.

## [0.13.0] - 2026-04-20

`0.13.0` is the baseline described by this changelog. At this point Millrace
already bootstrapped workspaces, compiled selected modes and loops into a
persisted plan, and executed stages through the runner dispatcher. Codex CLI
remained the default runtime harness, and this release packaged the Pi harness
as a first-class alternative instead of treating it as an out-of-band runner.

### Added

- Added canonical `default_codex` and `default_pi` built-in modes.
- Added the `pi_rpc` runner adapter as a first-class built-in runner.
- Added a focused Pi JSONL RPC client that invokes `pi --mode rpc --no-session`,
  sends Millrace-owned stage prompts, persists streamed events, reads final
  assistant text, captures session stats when available, and maps transport,
  provider, timeout, and empty-output failures into standard runner results.
- Added `[runners.pi]` configuration for the Pi command, extra args, provider,
  thinking posture, environment, and deterministic context/skill-discovery
  defaults.
- Added Pi runner artifacts alongside the existing runner artifact family,
  including persisted invocation, completion, stdout/stderr, prompt, and event
  log files.
- Added workspace doctor checks that warn when the runner binary required by
  the resolved mode is unavailable.

### Changed

- New workspaces now bootstrap with `runtime.default_mode = "default_codex"`.
- `default_codex` binds every shipped stage to `codex_cli`; `default_pi` binds
  every shipped stage to `pi_rpc`.
- Shared stage prompt construction between Codex and Pi runners so both harnesses
  receive the same Millrace-owned request context and legal terminal-marker
  contract.
- Updated `millrace modes list` and `millrace modes show` to surface canonical
  modes and compatibility aliases.
- Updated runner and mode documentation to describe Codex and Pi as supported
  packaged harness postures.

### Removed

- Removed `standard_plain` as the canonical packaged mode asset.

### Compatibility Notes

- Existing `standard_plain` configs continue to work because `standard_plain`
  resolves as a compatibility alias for `default_codex`.
- Switching from `default_codex` to `default_pi` changes only compiled runner
  bindings. The shipped execution and planning loop topology remains the same.

[Unreleased]: https://github.com/tim-osterhus/millrace/compare/v0.15.2...HEAD
[0.15.2]: https://github.com/tim-osterhus/millrace/compare/v0.15.1...v0.15.2
[0.15.1]: https://github.com/tim-osterhus/millrace/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/tim-osterhus/millrace/compare/v0.14.1...v0.15.0
[0.14.1]: https://github.com/tim-osterhus/millrace/compare/v0.14.0...v0.14.1
[0.14.0]: https://github.com/tim-osterhus/millrace/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/tim-osterhus/millrace/compare/v0.12.5...v0.13.0
