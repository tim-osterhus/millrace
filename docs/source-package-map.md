# Millrace Source Package Map

This document records the post-refactor source layout under
`src/millrace_ai/`, the mirrored test tree under `tests/`, and the
intentionally preserved compatibility facades that keep older imports stable
during the transition. It also calls out the v0.20 compiler/runtime authority
packages that make workflow configuration data-driven.

## Current Layout

- importable package code lives under `src/millrace_ai/`
- tests mirror ownership under `tests/architecture/`, `tests/assets/`,
  `tests/blueprint/`, `tests/cli/`, `tests/compilation/`, `tests/config/`,
  `tests/runners/`, `tests/runtime/`, `tests/workspace/`, and
  `tests/integration/`
- the package entrypoints are `src/millrace_ai/__main__.py` and the `src/millrace_ai/cli/` package
- optional web dashboard code lives under `packages/millrace-web/` as a
  separate source distribution with its own `pyproject.toml`, `millrace_web`
  package, tests, changelog, and README; it is not imported by or packaged into
  the base `millrace-ai` wheel

## Package Ownership Snapshot

The current `src/millrace_ai/` package tree is intentionally split by ownership:

- `architecture/` owns typed architecture contracts for graph loops, stage
  kinds, materialized plans, workflow primitives, and shared architecture
  contract helpers.
- `assets/` owns packaged runtime assets and public asset-loading helpers,
  including modes, loops, graphs, registries, entrypoint markdown, and bundled
  skills.
- `cli/` owns the Typer operator surface, command modules, command-specific
  view assembly, status view-model collection/rendering, and CLI-only
  formatting/error presentation.
- `compilation/` owns compiler internals behind `millrace_ai.compiler`,
  including workspace plan compilation, graph materialization/export,
  validation, mode resolution, policy compilation, capabilities, model aliases,
  completion behavior, fingerprints, persistence, and currentness checks. The
  validation surface is now a package facade
  (`compilation/validation/__init__.py`) with focused validator modules for
  graphs, stages, modes/model-assignment maps, artifact/document contracts,
  work-family queue policy checks, request-context profile authority, lifecycle
  checks, lane conflict coverage, runtime-effect handlers/rules/operations,
  operation-runner registry checks, runtime failure/recovery policy checks, and
  generic repair-closure validation.
- `config/` owns runtime config models, loading, TOML-preserving mutations, and
  config-change boundary classification.
- `contracts/` owns canonical typed runtime contracts behind the
  `millrace_ai.contracts` facade, including work documents, work references,
  stage results, stage metadata, graph exports, runtime snapshots, mailbox
  payloads, Blueprint contracts, Recon contracts, recovery counters, and token
  usage.
- `doctor/` owns read-only workspace Doctor diagnostics, including the public
  Doctor facade, result models, check registry, workspace/runtime state checks,
  queue parseability checks, asset/runner posture checks, and deterministic
  issue ordering.
- `runners/` owns runner contracts, request rendering, result normalization,
  registry/dispatcher/process helpers, typed runner errors, and built-in Codex
  CLI and Pi RPC adapters.
- `runtime/` owns daemon execution and runtime state transitions. Its subdomains
  include lifecycle, tick cycle, active runs, compiled plans, activation,
  mailbox/watcher intake, graph authority, usage governance, request context,
  runtime effects, focused runtime-effect operation runners, result
  persistence/application, completion behavior, recovery/blocking/error
  handling, lane and plane concurrency, approvals, monitoring, snapshots, run
  traces, closure/recon/work-item transitions, and handoff incidents.
- `workspace/` owns durable filesystem state and mutation helpers, including
  paths, initialization, baselines, schema epochs, queue storage/selection/
  lifecycle/reconciliation, task integrity, work inventory, work documents,
  state reconciliation, mailbox/events, remote skills, operator interventions,
  lineage integrity, Arbiter state, Blueprint state, runtime locks, and packaged
  asset deployment.

## Old-To-New Module Map

| Legacy surface | Current source home | Notes |
| --- | --- | --- |
| `millrace_ai/cli.py` | `src/millrace_ai/cli/app.py`, `src/millrace_ai/cli/shared.py`, `src/millrace_ai/cli/errors.py`, `src/millrace_ai/cli/status_view.py`, `src/millrace_ai/cli/runs_view.py`, `src/millrace_ai/cli/config_view.py`, `src/millrace_ai/cli/compile_view.py`, `src/millrace_ai/cli/formatting.py`, `src/millrace_ai/cli/monitoring.py`, `src/millrace_ai/cli/commands/*` | `millrace_ai.cli` is now a package surface; command groups live in dedicated modules, daemon monitor formatting is isolated, and status/run/config/compile views own their filesystem-backed data loading instead of feeding back through shared command helpers. |
| `millrace_ai/runtime.py` | `src/millrace_ai/runtime/engine.py` plus owned modules for lifecycle, tick cycle, active runs, compiled-plan archives, mailbox intake, watcher intake, activation, reconciliation, result persistence/application, runtime effects, lifecycle interpretation, completion behavior, error recovery, blocked recovery, lane scheduling, plane concurrency, lane conflicts, request context, Blueprint effects, Planner effects, Blueprint recovery diagnostics, failure policy, run traces, learning triggers/promotions, skill evidence, snapshot state, approvals, capability gates, usage governance, graph authority, closure transitions, Recon transitions, work-item transitions, stage requests, handoff incidents, monitoring, and inspection | `millrace_ai.runtime` is now a package that re-exports `RuntimeEngine`, `RuntimeTickOutcome`, runtime monitor types, and daemon supervisor surfaces. `engine.py` remains the stable facade while collaborators own runtime lifecycle, lane-keyed scheduling, immutable launch-plan authority, request-context artifacts, compiled workflow effect dispatch, Blueprint/Planner-specific mutations, repair diagnostics, recovery policy, and routed post-stage mutation details. |
| `millrace_ai/control.py` | `src/millrace_ai/runtime/control.py`, `src/millrace_ai/runtime/control_mailbox.py`, `src/millrace_ai/runtime/control_mutations.py` | Root `control.py` remains a thin compatibility facade. |
| `millrace_ai/config.py` | `src/millrace_ai/config/models.py`, `src/millrace_ai/config/loading.py`, `src/millrace_ai/config/boundaries.py`, `src/millrace_ai/config/toml_editing.py` | `millrace_ai.config` is now a package surface; usage-governance config models live in `models.py` and apply on next-tick boundaries. Model alias config and assignment policy are recompile boundaries, with TOML-preserving CLI mutation helpers in `toml_editing.py`. |
| `millrace_ai/contracts.py` | `src/millrace_ai/contracts/__init__.py`, `base.py`, `blueprint.py`, `capabilities.py`, `enums.py`, `stage_metadata.py`, `token_usage.py`, `work_documents.py`, `work_refs.py`, `recon.py`, `stage_results.py`, `graph_exports.py`, `run_trace.py`, `loop_config.py`, `modes.py`, `compile_diagnostics.py`, `runtime_snapshot.py`, `runtime_errors.py`, `mailbox.py`, `recovery.py` | `millrace_ai.contracts` remains the public facade for canonical typed contracts; named submodules own contract families, including execution capability contracts, Blueprint packet/evaluation contracts, Recon packet contracts, work-family reference normalization, compiled-stage-graph exports, run-trace graph artifacts, and `stage_metadata.py` as the typed registry for shipped stage plane membership, legal terminal results, runner prompt markers, and result-class policy. |
| `millrace_ai/compiler.py` | `src/millrace_ai/compiler.py`, `src/millrace_ai/compilation/` | `millrace_ai.compiler` remains the public facade; compiler outcomes, workspace compile orchestration, graph preview/export, mode/path resolution, graph and node materialization, policy compilation, workflow primitive resolution/validation, scheduler lane validation, execution-capability grant resolution, asset resolution, fingerprints, persistence, and currentness inspection live in `compilation/`. |
| `millrace_ai/entrypoints.py` | `src/millrace_ai/assets/entrypoints/__init__.py`, `models.py`, `discovery.py`, `parsing.py`, `advisory.py`, `linting.py`, `rendering.py` | Root `entrypoints.py` remains a thin compatibility facade; packaged markdown entrypoint assets live in the same `assets/entrypoints/` directory under `execution/`, `planning/`, and `learning/`. |
| `millrace_ai/modes.py` | `src/millrace_ai/assets/modes.py` | Root `modes.py` remains a thin compatibility facade. |
| `millrace_ai/stage_kinds.py` | `src/millrace_ai/assets/architecture.py`, `src/millrace_ai/architecture/stage_kinds.py` | Root `stage_kinds.py` is the thin public facade for stage-kind registry loading. |
| `millrace_ai/loop_graphs.py` | `src/millrace_ai/assets/loop_graphs.py`, `src/millrace_ai/architecture/loop_graphs.py` | Root `loop_graphs.py` is the thin public facade for graph-loop loading. |
| `millrace_ai/runner.py` | `src/millrace_ai/runners/requests.py`, `src/millrace_ai/runners/normalization/`, `src/millrace_ai/runners/base.py`, `contracts.py`, `dispatcher.py`, `errors.py`, `process.py`, `registry.py`, `src/millrace_ai/runners/adapters/codex_cli.py`, `codex_cli_command.py`, `codex_cli_artifacts.py`, `codex_cli_tokens.py`, `pi_rpc.py`, and `pi_rpc_client.py` | Root `runner.py` remains a thin compatibility facade over the `runners` package; runner registration/dispatch, normalization, process helpers, typed errors, Codex adapter command construction, artifact handling, token extraction, and Pi RPC integration have focused modules behind the public adapter classes. |
| `millrace_ai/doctor.py` | `src/millrace_ai/doctor/__init__.py`, `checks.py`, `models.py`, `output.py`, `workspace_checks.py`, `queue_checks.py`, and `asset_checks.py` | `millrace_ai.doctor` is now a package facade that preserves `DoctorIssue`, `DoctorReport`, and `run_workspace_doctor` while keeping check families in focused modules. |
| `millrace_ai/run_inspection.py` | `src/millrace_ai/runtime/inspection.py` | Root `run_inspection.py` remains a thin compatibility facade. |
| `millrace_ai/paths.py` | `src/millrace_ai/workspace/paths.py`, `src/millrace_ai/workspace/initialization.py` | Root `paths.py` remains a thin compatibility facade for `WorkspacePaths`, `workspace_paths`, and workspace initialization helpers. |
| workspace initialization/baseline/schema epoch | `src/millrace_ai/workspace/initialization.py`, `src/millrace_ai/workspace/bootstrap_files.py`, `src/millrace_ai/workspace/asset_deployment.py`, `src/millrace_ai/workspace/baseline.py`, `schema_epoch.py`, `schema_epoch_marker.py` | Explicit `millrace init`, default runtime file payloads, runtime asset deployment, managed baseline upgrade classification, schema epoch markers, and archive/reset helpers live in workspace-owned modules with path modeling kept separate from bootstrap behavior. |
| workspace idea source artifacts | `src/millrace_ai/workspace/idea_sources.py` | Runtime-owned durable source markdown for watcher-seeded idea specs lives under `millrace-agents/intake/ideas/`, separate from transient operator inbox files. |
| `millrace_ai/runtime_lock.py` | `src/millrace_ai/workspace/runtime_lock.py` | Root `runtime_lock.py` remains a thin compatibility facade. |
| `millrace_ai/mailbox.py` | `src/millrace_ai/workspace/mailbox.py` | Root `mailbox.py` remains a thin compatibility facade. |
| `millrace_ai/events.py` | `src/millrace_ai/workspace/events.py` | Root `events.py` remains a thin compatibility facade. |
| `millrace_ai/work_documents.py` | `src/millrace_ai/workspace/work_documents.py` | Root `work_documents.py` remains a thin compatibility facade. |
| `millrace_ai/recon_packets.py` | `src/millrace_ai/recon_packets.py` | Recon packet markdown parsing/rendering is a root helper because runtime transitions and stage tests share the same artifact contract. |
| `millrace_ai/queue_store.py` | `src/millrace_ai/workspace/queue_store.py`, `queue_claims.py`, `queue_selection.py`, `queue_transitions.py`, `queue_lifecycle.py`, `queue_reconciliation.py`, `task_lifecycle_integrity.py`, `work_inventory.py`, `work_item_adapters.py`, `operator_interventions.py`, `lineage_integrity.py`, `arbiter_state.py`, `remote_skills.py`, `blueprint_state.py` | Root `queue_store.py` remains a thin compatibility facade over the workspace queue package. Queue claim policy, compiled lifecycle mutation interpretation, family-aware work-item adapters, work inventory, intervention/lineage integrity, Arbiter state, remote skill metadata, and Blueprint artifact state have explicit workspace-owned homes. |
| `millrace_ai/state_store.py` | `src/millrace_ai/workspace/state_store.py`, `state_reconciliation.py` | Root `state_store.py` remains a thin compatibility facade over the workspace state package. |

## Stable Public Facades And Exports

These import surfaces are deliberate compatibility contracts and should be
preserved unless an ADR or release note explicitly removes them:

- `src/millrace_ai/compiler.py` re-exports the public compiler API from
  `compilation/`, including `CompileOutcome`, `CompilerValidationError`,
  `compile_and_persist_workspace_plan`, plan-currentness inspection, and graph
  preview.
- `src/millrace_ai/runner.py` re-exports stable runner request/result and
  normalization helpers from `runners/`.
- `src/millrace_ai/doctor/__init__.py` re-exports stable Doctor result models
  and `run_workspace_doctor` from the Doctor check package.
- `src/millrace_ai/queue_store.py` re-exports `QueueStore`, `QueueClaim`, and
  `StaleActiveState` from workspace queue modules.
- `src/millrace_ai/control.py` re-exports `RuntimeControl` and
  `ControlActionResult` from `runtime/control.py`.
- `src/millrace_ai/__init__.py` intentionally exposes only `__version__`; richer
  surfaces live in named modules or package facades.
- `src/millrace_ai/compilation/validation/__init__.py` is the stable compiler
  validation facade; focused sibling modules own validator families.
- `src/millrace_ai/compilation/effect_operations.py` remains a thin
  compatibility facade for runtime-effect operation validation.
- Package `__init__.py` files are public facades when they define `__all__`:
  `architecture`, `assets`, `assets.entrypoints`, `cli.status`, `compilation`,
  `config`, `contracts`, `doctor`, `runners`, `runners.normalization`,
  `runtime`, `runtime.graph_authority`,
  `runtime.usage_governance`, and `workspace`. Empty CLI package initializers
  remain package markers, not API expansion points.

## Compiled Architecture And Workflow Primitive Authority

The configurable runtime authority now has dedicated packages and asset
families:

- `src/millrace_ai/architecture/stage_kinds.py` defines typed stage-kind contracts
- `src/millrace_ai/architecture/loop_graphs.py` defines typed graph-loop contracts
- `src/millrace_ai/architecture/materialization.py` defines the graph-plan materialization contracts, including normalized compiled entry/transition indexes, runtime-authority flags, and legacy-equivalence compatibility reporting
- `src/millrace_ai/architecture/workflow_primitives/__init__.py` is the public
  facade for workflow primitive contracts. Family modules now live in
  `workflow_primitives/work_families.py`,
  `document_adapters.py`, `artifact_contracts.py`,
  `request_context_profiles.py`, `lifecycle.py`, `concurrency.py`,
  `completion.py`, `runtime_effects.py`, `recovery_policies.py`,
  `operator_controls.py`, and `schema_epochs.py`. Shared aliases and private
  normalization helpers remain in
  `src/millrace_ai/architecture/workflow_primitives/identifiers.py` and
  `src/millrace_ai/architecture/workflow_primitives/_validation.py`
- `src/millrace_ai/assets/architecture.py` loads stage-kind registry assets
- `src/millrace_ai/assets/loop_graphs.py` loads graph-loop assets
- `src/millrace_ai/assets/workflows.py` loads workflow primitive registry
  assets
- `src/millrace_ai/assets/registry/stage_kinds/` ships the stage-kind registry JSON
- `src/millrace_ai/assets/registry/work_item_families/` ships claimable work
  family definitions, including Blueprint drafts
- `src/millrace_ai/assets/registry/document_adapters/` ships built-in markdown
  adapters
- `src/millrace_ai/assets/registry/queue_claim_policies/` ships plane claim
  ordering and eligibility policy
- `src/millrace_ai/assets/registry/terminal_actions/`,
  `lifecycle_mutation_plans/`, `runtime_effect_handlers/`, and
  `runtime_effect_rules/` ship compiled post-stage mutation authority
- `src/millrace_ai/assets/registry/runtime_effect_operations/`,
  `runtime_effect_runners/`, `effect_stores/`, and `effect_validators/` ship
  compiler-validated declarative runtime-effect operation catalogs and runner
  ownership used as the runtime dispatch identity during the legacy-handler
  migration
- `src/millrace_ai/assets/registry/recovery_policies/` and
  `runtime_failure_policies/` ship compiler-validated recovery/failure policy
  hooks
- `src/millrace_ai/assets/registry/workspace_schema_epochs/` ships active
  schema epoch compatibility data
- `src/millrace_ai/assets/graphs/` ships the graph-loop JSON
- `src/millrace_ai/assets/graphs/planning/blueprint.json` ships the opt-in
  Blueprint Planning loop
- `src/millrace_ai/assets/loops/learning/default.json` and
  `src/millrace_ai/assets/graphs/learning/standard.json` ship the learning
  loop alongside execution and planning, including the
  Analyst/Professor/Curator chain and the targeted Librarian stage for
  post-Planner remote optional-skill preparation
- `src/millrace_ai/assets/loops/execution/with_integrator.json` and
  `src/millrace_ai/assets/graphs/execution/with_integrator.json` ship the
  opt-in high-assurance execution loop
- `src/millrace_ai/assets/modes/blueprint_codex.json` selects
  `planning.blueprint` with standard execution
- `src/millrace_ai/assets/modes/blueprint_learning_codex.json` selects
  `planning.blueprint`, `learning.standard`, and standard execution
- `src/millrace_ai/assets/modes/learning_codex.json` and
  `src/millrace_ai/assets/modes/learning_pi.json` select execution, planning,
  and learning loops with compiler-frozen learning trigger rules, including
  Planner-to-Librarian optional-skill preparation

This asset set now owns runtime control-flow and workflow mutation authority
after compilation. Legacy loop assets and root router modules still remain in
the package as compatibility and inspection surfaces, but runtime execution
reads the persisted compiled plan.

## Intentionally Preserved Root Modules

These modules remain at the package root because they still have one coherent reason to change or they define foundational errors/adapters used across the package:

- `src/millrace_ai/router.py`
- `src/millrace_ai/watchers.py`
- `src/millrace_ai/errors.py`

Additional thin compatibility or public API facades also exist at the root:

- `src/millrace_ai/compiler.py`
- `src/millrace_ai/control.py`
- `src/millrace_ai/queue_store.py`
- `src/millrace_ai/runner.py`
- `src/millrace_ai/stage_kinds.py`
- `src/millrace_ai/loop_graphs.py`

## Current Cleanliness Refactor Notes

The current cleanup sequence preserves public imports while reducing ownership
cycles:

- `workspace/paths.py` now owns only the workspace path model and resolution.
- `workspace/bootstrap_files.py` owns default state/status/config payload
  construction for newly initialized workspaces.
- `workspace/asset_deployment.py` owns packaged runtime asset source resolution
  and deployment.
- `workspace/idea_sources.py` owns durable runtime copies of idea source
  markdown used by closure-target creation.
- `workspace/work_item_adapters.py` owns family-aware loading/rendering for
  built-in queue documents.
- `workspace/family_adapters.py` owns work-family queue adapter contracts and
  registry lookup for claim/lifecycle/requeue/lineage operations bound to
  workflow family contracts.
- `workspace/queue_lifecycle.py` applies compiled lifecycle mutation plans to
  source queue documents.
- `workspace/blueprint_state.py` owns durable Blueprint manifest, draft,
  packet, critique, evaluation, and promotion state helpers.
- `workspace/schema_epoch.py` and `workspace/schema_epoch_marker.py` own schema
  epoch markers and daemon-safe archive/reset helpers.
- `workspace/initialization.py` orchestrates initialization and keeps
  `bootstrap_workspace` as the compatibility alias used by older callers.
- `cli/errors.py` owns operator error output.
- `cli/status_view.py` is a compatibility facade over `cli/status/`, where
  `collection.py` owns status view-model assembly and `rendering.py` owns text
  and JSON output. `cli/runs_view.py`, `cli/config_view.py`, and
  `cli/compile_view.py` continue to own their command-specific view assembly.
- `cli/commands/model_aliases.py` owns operator commands for model alias CRUD
  and global/loop/stage assignment CRUD. It stays thin by delegating TOML
  mutation to `config/toml_editing.py` and reload routing to `RuntimeControl`.
- `cli/formatting.py` is limited to rendering already-collected run/control
  values and small shared value formatting.
- Runtime submodules import concrete sibling modules directly, and
  `runtime/outcomes.py` holds `RuntimeTickOutcome` so tick/request helpers do
  not depend back on `runtime/engine.py`; the public `millrace_ai.runtime`
  package facade remains the stable `RuntimeEngine` / `RuntimeTickOutcome`
  import surface.
- `runtime/supervisor.py`, `runtime/lanes.py`, `runtime/lane_conflicts.py`,
  `runtime/plane_concurrency.py`, and `runtime/active_runs.py` own daemon lane
  dispatch, durable lane state, conflict policy checks, plane concurrency, and
  active-run projection. FU-8 Batch 8 Packet 02 added direct boundary tests
  for supervisor cancellation/reload/drain behavior and landed cancellation
  cleanup in `runtime/supervisor.py`; `runtime/supervisor_parts/` remains
  deferred until lifecycle/event seams are safer to separate.
- `runtime/monitoring.py`, `runtime/pause_state.py`, and
  `runtime/handoff_incidents.py` own runtime event sink contracts, pause-source
  projection, and operator handoff incident state.
- `runtime/compiled_plans.py` preserves active-run launch-plan authority when
  config reload compiles a newer pending plan.
- `runtime/request_context.py` is the compatibility facade for deterministic
  per-request context rendering imports.
- `runtime/stage_requests.py`, `runtime/completion_behavior.py`,
  `runtime/blocked_recovery.py`, and `workspace/operator_interventions.py`
  consume family adapter resolution for family-scoped active-path, lineage,
  and retry/cancellation location behavior instead of hard-coded family path
  branches.
- `runtime/context/` owns deterministic per-request context bundles, compiled
  node/profile/render-plan authority resolution, provider-registry dispatch,
  rendered prompt-context artifacts, generic provider behavior, and Blueprint
  provider behavior isolated from the generic facade.
- `runtime/effects/`, `runtime/effect_execution.py`, and
  `runtime/lifecycle_interpreter.py` own generic compiled runtime-effect and
  source-lifecycle application. `runtime/effects/registry.py` provides the
  operation-indexed handler registry seam, and `runtime/effects/legacy.py` is
  the temporary registry for legacy Python effect handlers while declarative
  operation migration proceeds. `runtime/effects/operation_runners/` owns the
  generic operation helper modules plus focused Blueprint operation runners for
  Manager, Contractor, Evaluator, and Mechanic effects.
  `runtime/effects/operations.py` is a compatibility forwarding facade for old
  imports. Implementation patching for diagnostics belongs in the focused
  runner modules. Stage results and runtime events carry operation id and runner
  id as authority metadata; legacy handler id is optional compatibility
  metadata.
- `runtime/blueprint_effects.py` is a compatibility facade for old imports and
  handler-id names. It no longer owns Blueprint durable mutation.
- `runtime/planner_effects.py` owns Planner-specific runtime effects while
  registering on the same compiled effect-selection path as Blueprint effects.
- `runtime/completion_behavior.py` owns compiled completion behavior, while
  `runtime/recovery/` now owns focused recovery subdomains:
  `blocked_metadata.py`, `retry_policy.py`, `environmental.py`,
  `queue_mutation.py`, `error_context.py`, `reports.py`, `repair_routes.py`,
  and recovery event helpers. `runtime/blocked_recovery.py` and
  `runtime/error_recovery.py` remain compatibility facades so existing runtime
  and CLI imports stay stable during the decomposition.
- `runtime/artifact_contracts.py`, `runtime/stage_result_persistence.py`,
  `runtime/result_counters.py`, `runtime/recon_transitions.py`, and
  `runtime/work_item_transitions.py` keep post-stage artifact validation,
  durable result writes, counters, and family-specific transition helpers out of
  the central tick cycle.
- `runtime/blueprint_recovery_diagnostics.py` remains an explicit Blueprint
  compatibility module for shared runtime-effect repair context rendering in
  status and doctor, including preservation of the original repairable
  Evaluator approval failure context after Mechanic repair apply.
- `runtime/failure_policy.py` keeps runtime failure classification aligned with
  compiled failure-policy identifiers and matches runtime-effect policies by
  operation id before falling back to legacy handler aliases.
- `runtime/usage_governance/` is a package-level authority domain. Its facade
  preserves the previous `millrace_ai.runtime.usage_governance` imports while
  models, state persistence, ledger reconciliation, runtime-token windows,
  subscription-quota telemetry, monitor events, and pause-source application
  live in named modules.
- `runtime/graph_authority/` is a package-level authority domain. Its facade
  preserves the previous `millrace_ai.runtime.graph_authority` imports while
  activation, validation, policy lookup, counters, stage mapping, and
  plane-specific routing live in named modules.
- `runtime/capability_gates.py` and `runtime/approvals.py` own the
  pre-dispatch execution-capability gate plus the minimal operator approval
  storage/control path.
- `compilation/` is the compiler-internals package behind the stable
  `millrace_ai.compiler` facade. Workspace compile orchestration, graph preview,
  graph export projection, materialization, validation, policy compilation,
  execution-capability grant resolution, asset/fingerprint handling,
  persistence, and currentness inspection now have separate module ownership.
- `compilation/model_aliases.py` owns compiler-only model alias validation,
  precedence, fallback, and warning generation. Pydantic config intentionally
  accepts invalid alias payloads so compile diagnostics can warn and fall back
  without blocking config load.
- `compilation/validation/` is the compiler-validation package facade that
  preserves the public `millrace_ai.compilation.validation` import surface.
  `compilation/validation/diagnostics.py` owns tiny shared diagnostics helpers,
  while focused modules own graph, stage, mode, artifact, work-family,
  lifecycle, request-context, lane-conflict, runtime-effect, failure-policy,
  and repair-closure checks. Generic cross-asset compile checks for
  `route_to_node` repair closure resolution (operation/failure scope,
  target-node outcome binding, evidence artifacts, family scope, resume guards,
  and partial-mutation support) live in `compilation/validation/repair_closures.py`;
  Blueprint recovery routes are one instance of that generic validator, not a
  separate architectural authority path.
- `contracts/` is the typed contract package behind the stable
  `millrace_ai.contracts` facade. Enums, stage metadata, work documents,
  execution-capability grants, stage-result envelopes, compiled graph exports,
  Blueprint contracts, run-trace graphs, loop/mode definitions, compiler
  diagnostics, runtime snapshots, runtime error contexts, mailbox payloads, and
  recovery counters live in named modules with shared validators kept at the
  contract layer.
- `contracts/stage_metadata.py` is the canonical stage metadata registry.
  Runner request defaults, terminal-result normalization, entrypoint stage
  linting, graph stage lookup, and built-in stage-kind asset validation derive
  plane, marker, and result-class truth from that registry.
- `assets/entrypoints/` is both the packaged entrypoint asset directory and the
  entrypoint asset parsing package. Models, path discovery, markdown
  frontmatter parsing, advisory skill-reference checks, lint policy, and
  diagnostic rendering now have separate module ownership behind the stable
  `millrace_ai.assets.entrypoints` facade.
- `workspace/work_inventory.py`, `workspace/operator_interventions.py`,
  `workspace/lineage_integrity.py`, `workspace/arbiter_state.py`, and
  `workspace/remote_skills.py` keep workspace inventory, operator intervention
  records, lineage checks, Arbiter closure state, and remote skill metadata out
  of the queue-store facade.
- `doctor/checks.py`, `doctor/workspace_checks.py`,
  `doctor/queue_checks.py`, and `doctor/asset_checks.py` keep Doctor check
  registration, workspace/runtime diagnostics, queue parseability, and asset/
  runner posture checks separate while preserving `millrace_ai.doctor`.
- `runners/normalization/` keeps terminal parsing, artifact safety, failure
  classification, terminal-result mapping, and request provenance projection
  behind the stable `millrace_ai.runners.normalization.normalize_stage_result`
  import.

## Runner Package Notes

The built-in runner package now contains two first-class adapter paths:

- `src/millrace_ai/runners/adapters/codex_cli.py`
- `src/millrace_ai/runners/adapters/pi_rpc.py`

Shared runner-owned helpers live alongside them:

- `src/millrace_ai/runners/adapters/_prompting.py`
- `src/millrace_ai/runners/adapters/codex_cli_command.py`
- `src/millrace_ai/runners/adapters/codex_cli_artifacts.py`
- `src/millrace_ai/runners/adapters/codex_cli_tokens.py`
- `src/millrace_ai/runners/adapters/pi_rpc_client.py`

Mode assets in `src/millrace_ai/assets/modes/` freeze those built-in harness
presets through canonical mode ids:

- `default_codex`
- `default_pi`
- `learning_codex`
- `learning_pi`
- `default_codex_integrated`
- `learning_codex_integrated`
- `blueprint_codex`
- `blueprint_learning_codex`

`standard_plain` is preserved only as a compatibility alias in the asset-loading
layer, not as a third duplicated mode asset file.

## Test Ownership Map

| Source area | Mirrored tests |
| --- | --- |
| `src/millrace_ai/assets/` | `tests/assets/` |
| `src/millrace_ai/architecture/` | `tests/architecture/` |
| `src/millrace_ai/cli/` | `tests/cli/` |
| Blueprint contracts/effects/state | `tests/blueprint/`, `tests/runtime/test_blueprint_request_context.py`, `tests/integration/test_blueprint_planning_loop.py` |
| `src/millrace_ai/compilation/` | `tests/compilation/`, `tests/integration/test_compiler.py` |
| `src/millrace_ai/config/` | `tests/config/` |
| `src/millrace_ai/contracts/` | `tests/runtime/test_contracts.py` |
| `src/millrace_ai/runners/` | `tests/runners/` |
| `src/millrace_ai/runtime/` | `tests/runtime/` |
| `src/millrace_ai/workspace/` | `tests/workspace/` |
| Cross-cutting operator/runtime flows | `tests/integration/` |
| Import graph hygiene | `tests/test_import_cycles.py` |
| Source ownership hygiene | `tests/test_source_hygiene.py` |

## Verification Commands

Use the same commands locally, in review artifacts, and in CI:

```bash
uv run --extra dev python -m pytest -q
uv run --with ruff ruff check src/millrace_ai tests
uv run --with mypy mypy src/millrace_ai
```

For fast architecture-guardrail checks during source-layout refactors, run:

```bash
uv run --extra dev python -m pytest tests/test_import_cycles.py tests/test_source_hygiene.py -q
```
