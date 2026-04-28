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

### Changed

- Clarified `millrace upgrade` documentation and the shipped ops skill so
  operators do not confuse workspace baseline refreshes with installed
  `millrace-ai` package updates.
- Made the basic daemon monitor more human-readable by compacting redundant
  stage identity, shortening long live run ids, rendering route transitions
  directly, suppressing routine terminal-to-idle status noise, and omitting
  unknown token filler.

## [0.15.8] - 2026-04-27

`0.15.8` hardens upgrade/config surfaces and serializes bulk watcher root-spec
intake behind the v1 one-open-closure-target policy.

### Added

- Added `millrace --version` and `millrace version` for package-version
  visibility in installed and module-entrypoint environments.
- Added `millrace run daemon --monitor-log PATH` to write basic monitor output
  to a file while keeping stdout monitor mode independently selectable.
- Added first-class Codex `model_reasoning_effort` config on
  `runners.codex` and per-stage `stages.<stage>.model_reasoning_effort`, with
  compile, request, runner artifact, and run-inspection visibility.

### Changed

- Bulk watcher intake now respects the v1 one-open-closure-target policy as
  queue backpressure: unrelated root specs remain queued while the active root
  lineage runs or reaches Arbiter, status reports the deferred-root count, and
  direct stale-state recovery preserves the open closure target.
- `stages.<stage>` and `runners.codex.permission_by_stage` now accept learning
  stages such as `professor`.
- Workspace doctor now resolves the selected mode against workspace-local
  deployed assets, matching runtime compile/startup behavior for custom modes.
- `millrace upgrade` can intentionally localize removed managed assets through
  `--localize-removed` or `--localize-removed-from`, and runtime asset
  manifests now ignore cache artifacts such as `__pycache__` and `*.pyc`.
- Runtime shutdown now clears `process_running`, `status` suppresses stale
  `process_running` truth when no active ownership lock exists, and
  `clear-stale-state` reports applied when it only clears that stale process
  bit.

## [0.15.7] - 2026-04-27

`0.15.7` completes the post-usage-governance package-boundary cleanup, keeping
public imports stable while splitting high-risk runtime, compiler, contract,
entrypoint, and runner internals into focused owners.

### Added

- Added ADR-0007, ADR-0008, and ADR-0009 to record the runtime authority
  package, contract facade, and stage metadata decisions behind the cleanup.
- Added source hygiene guardrails for dependency direction, contract-layer
  imports, path-only workspace modeling, generic helper modules, and wildcard
  imports.

### Changed

- Clarified usage-governance documentation now that the auto-pause/resume
  feature has shipped, including the next-tick status/monitor visibility model
  for governance changes after `config reload`.
- Split workspace bootstrap payloads and asset deployment out of
  `workspace.paths`, and split CLI status/run/config/compile views out of
  `cli.formatting`, preserving public imports while removing the real workspace
  and CLI import cycles that blocked later cleanup work.
- Moved the shared `RuntimeTickOutcome` contract behind
  `runtime/outcomes.py`, keeping the public runtime facade stable while
  removing the remaining concrete runtime submodule cycle.
- Added an import-cycle guardrail test so future concrete `millrace_ai.*`
  module cycles are caught by the normal pytest suite.
- Converted `runtime/usage_governance.py` into a package facade with separate
  ownership for models, state persistence, ledger repair, runtime-token window
  evaluation, subscription quota telemetry, monitor events, and engine-facing
  pause application.
- Converted `runtime/graph_authority.py` into a package facade with separate
  ownership for activation, validation, policy lookup, recovery counters, stage
  mapping, and execution/planning/learning routing.
- Split compiler internals into the `compilation/` package while keeping
  `millrace_ai.compiler` as the stable public facade for compile, preview, and
  currentness APIs.
- Converted `contracts.py` into the `contracts/` package facade, preserving
  `millrace_ai.contracts` imports while separating enums, stage metadata,
  work documents, stage results, loop/mode contracts, runtime snapshots,
  mailbox envelopes, compiler diagnostics, and recovery counters.
- Centralized stage plane, legal terminal marker, running marker, and
  result-class policy truth in `contracts/stage_metadata.py`; runner requests,
  normalization helpers, entrypoint linting, graph stage lookup, and built-in
  stage-kind asset validation now derive from that registry.
- Converted `assets/entrypoints.py` into the `assets/entrypoints/` package,
  preserving the public facade while separating manifest models, path
  discovery, markdown parsing, advisory skill-reference checks, lint policy,
  and diagnostic rendering.
- Split Codex CLI runner internals so the public `CodexCliRunnerAdapter`
  delegates command construction, artifact materialization/timeout
  reconciliation, and token-usage extraction to focused adapter modules.

## [0.15.6] - 2026-04-27

`0.15.6` tightens daemon monitor idle output and adds architecture records for
the compiled-plan and workspace-baseline decisions that now govern the runtime.

### Added

- Added ADR-0005 for `compiled_plan.json` as the runtime-authoritative graph
  plan.
- Added ADR-0006 for explicit workspace baselines and managed upgrade
  behavior.

### Changed

- Throttled repeated `runtime_idle reason=no_work` basic monitor lines into a
  120-second heartbeat while preserving the first idle line and resetting after
  runtime activity or a different idle reason.
- Expanded CLI reference coverage for every `millrace skills` subcommand and
  its core options.
- Updated the operator manual with `compile show`, queue inspection, modes,
  skills commands, and basic-monitor idle-heartbeat guidance.

## [0.15.5] - 2026-04-27

`0.15.5` carries forward the runtime docs, entrypoint assets, and asset-policy
coverage that were intended to ship with the usage-governance release, and fixes
the type annotations caught by the main-branch guardrails.

### Added

- Added opt-in usage governance configuration and runtime evaluation for
  between-stage token and subscription-quota pause rules.
- Added `pause_sources` so operator pauses and usage-governance pauses can
  coexist without one clearing the other accidentally.
- Added `usage_governance_state.json` and `usage_governance_ledger.jsonl` state
  artifacts for durable usage-governance status and token accounting.
- Added usage-governance fields to `millrace status`, `millrace config show`,
  and the basic daemon monitor.

### Changed

- Updated shipped entrypoint advisory text so it no longer references unshipped
  optional skills.
- Updated the runtime skills index to list only packaged skills and to point
  operators at the supported downloadable optional-skills directory:
  `https://github.com/tim-osterhus/millrace-skills/blob/main/index.md`.
- Clarified that stage-core skills are runtime-assigned by their compiled
  entrypoints, while optional secondary skills must be present in the packaged
  or installed skills surface before entrypoints can reference them.
- Refreshed public docs for learning modes, explicit workspace init/upgrade,
  daemon monitoring, `millrace skills`, and the optional skills directory.

### Fixed

- Tightened entrypoint asset lint so unknown optional secondary skill
  references fail instead of being accepted as placeholders.
- Fixed usage-governance pause-source type annotations so the package passes
  the repository MyPy guardrail on Python 3.11 and 3.12.

## [0.15.4] - 2026-04-27

`0.15.4` adds default-off runtime-owned usage governance so operators can let
Millrace automatically pause between stages when configured token or subscription
quota limits are reached.

### Added

- Added `[usage_governance]` config with runtime token rules, optional
  subscription quota rules, auto-resume behavior, and next-tick apply
  boundaries.
- Added durable usage governance state and ledger artifacts under
  `millrace-agents/state/`.
- Added runtime-owned pause source tracking so operator pauses and governance
  pauses can coexist without overwriting each other.
- Added status and live daemon monitor output for active usage blockers,
  subscription telemetry degradation, governance pause, and governance resume
  events.

### Changed

- Runtime ticks now evaluate usage governance before launching a stage and after
  persisting a stage result, preserving the between-stage execution boundary.
- `millrace control resume` now clears operator pause intent without bypassing an
  active usage-governance blocker.
- Stage-result token usage can be reconciled back into the governance ledger
  after restart if a ledger write was missing.

## [0.15.3] - 2026-04-26

`0.15.3` adds an opt-in live terminal monitor for daemon operators without
changing the quiet default daemon behavior.

### Added

- Added `millrace run daemon --monitor basic` for concise live lifecycle,
  status, stage, router, elapsed-time, and token-usage output.
- Added a runtime monitor event contract so daemon progress is emitted from the
  runtime path that owns lifecycle, status-marker, stage, and routing state.
- Added learning-plane and compiled concurrency-policy visibility to daemon
  monitor startup and stage output.

### Changed

- Centralized daemon-owned status marker updates so live monitor events and
  persisted marker files stay aligned across execution, planning, and learning.
- Documented the explicit `--monitor [none|basic]` daemon option in the CLI
  reference, including the quiet default behavior.

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

[Unreleased]: https://github.com/tim-osterhus/millrace/compare/v0.15.8...HEAD
[0.15.8]: https://github.com/tim-osterhus/millrace/compare/v0.15.7...v0.15.8
[0.15.7]: https://github.com/tim-osterhus/millrace/compare/v0.15.6...v0.15.7
[0.15.6]: https://github.com/tim-osterhus/millrace/compare/v0.15.5...v0.15.6
[0.15.5]: https://github.com/tim-osterhus/millrace/compare/v0.15.4...v0.15.5
[0.15.4]: https://github.com/tim-osterhus/millrace/compare/v0.15.3...v0.15.4
[0.15.3]: https://github.com/tim-osterhus/millrace/compare/v0.15.2...v0.15.3
[0.15.2]: https://github.com/tim-osterhus/millrace/compare/v0.15.1...v0.15.2
[0.15.1]: https://github.com/tim-osterhus/millrace/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/tim-osterhus/millrace/compare/v0.14.1...v0.15.0
[0.14.1]: https://github.com/tim-osterhus/millrace/compare/v0.14.0...v0.14.1
[0.14.0]: https://github.com/tim-osterhus/millrace/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/tim-osterhus/millrace/compare/v0.12.5...v0.13.0
