# Millrace Source Package Map

This document records the post-refactor source layout under
`src/millrace_ai/`, the mirrored test tree under `tests/`, and the
intentionally preserved compatibility facades that keep older imports stable
during the transition. It also calls out the v0.20 compiler/runtime authority
packages that make workflow configuration data-driven.

## Authority Layer Vocabulary

The source layout maps to four authority layers defined by ADR-0012 through
ADR-0015:

1. **Compiler** — `src/millrace_ai/compilation/` and the `millrace_ai.compiler`
   facade. Freezes operator config, modes, graphs, and workflow primitives into
   a compiled run plan.
2. **Runtime kernel** — `src/millrace_ai/runtime/`, `src/millrace_ai/workspace/`,
   and their stable facades. Owns tick orchestration, graph-authority routing,
   result application, recovery, and durable state persistence, but must not
   own workflow semantics (ADR-0012).
3. **Extension and primitive packages** —
   `src/millrace_ai/architecture/workflow_primitives/`,
   `src/millrace_ai/extensions/`, and registered extension package manifests.
   See ADR-0014 and ADR-0015 for the step-interpreter and manifest contract.
4. **Graph and config packages** — `src/millrace_ai/assets/graphs/`,
   `src/millrace_ai/assets/registry/`, `src/millrace_ai/assets/modes/`,
   and `src/millrace_ai/assets/loops/`. These JSON assets declare topology,
   stage kinds, lifecycle plans, runtime-effect rules, and recovery policies
   that the compiler validates and freezes.

The authority layers complement the five-layer operational model described in
`docs/millrace-technical-overview.md`. This document uses the four-layer
vocabulary for package ownership. Prospective boundary packages such as
`millrace_ai.kernel` or `millrace_ai.engine` are **not yet created** — they are
boundary descriptors named in the ADRs for future migration. The
`millrace_ai.extensions` package now exists for extension manifest contracts,
domain boundary Protocols, the built-in boundary registry, and built-in
adapter modules.

## Current Layout

- importable package code lives under `src/millrace_ai/`
- tests mirror ownership under `tests/architecture/`, `tests/assets/`,
  `tests/cli/`, `tests/compilation/`, `tests/config/`, `tests/runners/`,
  `tests/runtime/`, `tests/workspace/`, and `tests/integration/`
- the package entrypoints are `src/millrace_ai/__main__.py` and the `src/millrace_ai/cli/` package
- optional web dashboard code lives under `packages/millrace-web/` as a
  separate source distribution with its own `pyproject.toml`, `millrace_web`
  package, tests, changelog, and README; it is not imported by or packaged into
  the base `millrace-ai` wheel

## Package Ownership Snapshot

The current `src/millrace_ai/` package tree is intentionally split by ownership:

- `architecture/` owns typed architecture contracts for graph loops, stage
  kinds, materialized plans, workflow primitives, runtime-effect operation and
  primitive models, and shared architecture contract helpers.
- `assets/` owns packaged runtime assets and public asset-loading helpers,
  including modes, loops, graphs, registries, extension package manifests,
  entrypoint markdown, and bundled skills.
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
  work-family queue policy checks, request-context profile/provider/render-plan
  authority, lifecycle
  checks, lane conflict coverage, runtime-effect handlers/rules/operations,
  operation-runner registry checks, required-extension availability checks,
  runtime failure/recovery policy checks, and generic repair-closure
  validation.
- `config/` owns runtime config models, loading, TOML-preserving mutations, and
  config-change boundary classification.
- `contracts/` owns canonical typed runtime contracts behind the
  `millrace_ai.contracts` facade, including work documents, work references,
  Arbiter verdict contracts, closure freshness contracts, stage results,
  terminal outcome contracts, router contracts, stage metadata, graph
  exports, runtime snapshots, mailbox payloads, Recon contracts,
  required-extension contracts, recovery counters, history-entry contracts,
  and token usage.
  `contracts/router.py` owns the neutral `RouterAction`/
  `RouterDecision` contracts shared by runtime routing surfaces.
  `contracts/recovery.py` exposes the generic recovery-counter contract whose
  `counters` mapping is the canonical counter surface. Blueprint contract
  models are implemented under `extensions/builtin/blueprint/contracts.py`;
  `contracts/blueprint.py` is a deliberate removal stub. Generic contract
  packages do not export Blueprint contract compatibility APIs.
- `extensions/` owns extension package manifest contracts and built-in
  extension boundary interfaces, including extension domain and item-kind
  vocabulary, package/item manifests, implementation-path syntax validation
  without compile-time imports, semver validation, package/item dependency
  validation, built-in manifest item ownership across the registry-owned
  extension vocabulary used by required-extension checks,
  generic/Recon/closure/Blueprint/Learning Protocol contracts, the lazy
  `BuiltInExtensionBoundaryRegistry`, and thin built-in adapters under
  `extensions/builtin/`. Blueprint implementation code now lives under
  `extensions/builtin/blueprint/`; the root `contracts/blueprint.py`,
  `workspace/blueprint_state.py`, `workspace/families/blueprint.py`,
  `runtime/context/blueprint.py`, and
  `runtime/effects/operation_runners/__init__.py` modules are deliberate
  removal stubs.
- `doctor/` owns read-only workspace Doctor diagnostics, including the public
  Doctor facade, result models, check registry, manifest-discovered extension
  diagnostics, workspace/runtime state checks, queue parseability checks,
  asset/runner posture checks, and deterministic issue ordering.
- `runners/` owns runner contracts, request rendering, result normalization,
  registry/dispatcher/process helpers, typed runner errors, and built-in Codex
  CLI and Pi RPC adapters.
- `runtime/` owns daemon execution and runtime state transitions. Its subdomains
  include lifecycle, tick cycle, active runs, compiled plans, activation,
  mailbox/watcher intake, graph authority, usage governance, request context,
  runtime effects, generic runtime-effect helper/registry/interpreter/journal
  modules, result persistence/application, closure boundary, completion
  behavior, recovery/blocking/error handling, lane and plane concurrency,
  shared compiled scheduler-policy interpretation, approvals, monitoring,
  snapshots, run traces, closure/recon/work-item transitions, and handoff
  incidents.
  `runtime/effect_execution.py` also applies effect-rule-declared source
  completion/blocking lifecycle intent for `REQUEST_COMPLETE_SOURCE` and
  `REQUEST_BLOCK_SOURCE`, with repair-route exceptions still handled by
  failure policy. Blueprint-specific request-context, state, and
  operation-runner code now delegates to `extensions/builtin/blueprint/`; the
  generic runtime-effect helper modules live under `runtime/effects/`
  (`primitives.py`, `registry.py`, `interpreter.py`, `journal.py`,
  `legacy.py`, `models.py`, plus the helper modules under
  `runtime/effects/operation_runners/`), while the Blueprint operation-runner
  implementation lives under
  `extensions/builtin/blueprint/operation_runners/`. The legacy
  `runtime/context/blueprint.py` and `runtime/effects/operation_runners/__init__.py`
  modules are deliberate removal stubs.
  `runtime/scheduler_policy.py` centralizes the compiled scheduler-policy
  interpreter used by `activation.py`, `tick_cycle.py`, `supervisor.py`, and
  `lanes.py` for predicate-backed foreground order, closure-target inversion,
  scalar fallback compatibility, and learning dispatch, including typed
  `LearningStageName` returns for scheduler-policy-selected Learning targets.
  `runtime/closure_transitions.py` validates typed Arbiter verdict provenance
  against the closure freshness window and requires every deciding criterion
  to be fresh or revalidated after newer same-lineage remediation; explicit
  context-only criteria may remain historical.
- `workspace/` owns durable filesystem state and mutation helpers, including
  paths, initialization, baselines, schema epochs, queue storage/selection/
  lifecycle/reconciliation, task integrity, work inventory, work documents,
  state reconciliation, mailbox/events, remote skills, operator interventions,
  lineage integrity, Arbiter state, Blueprint state, runtime-owned history-log
  append/render helpers, runtime locks, and packaged asset deployment.
  Blueprint family and state helpers now delegate to
  `extensions/builtin/blueprint/`; the legacy `workspace/blueprint_state.py`
  and `workspace/families/blueprint.py` modules are deliberate removal stubs.
- `workspace_map/` owns the stdlib-only workspace map surface behind
  `millrace workspace-map refresh`, `validate`, and `show`. It performs full
  deterministic M1 rebuilds, writes generated/runtime-owned files under
  `millrace-agents/workspace-map/generated/` plus `manifest.json`, keeps
  seeded starter guidance in `workspace-map/index.md`, and keeps curated local
  knowledge in `workspace-map/wiki/` outside the generator's write set.

## Old-To-New Module Map

| Legacy surface | Current source home | Notes |
| --- | --- | --- |
| `millrace_ai/cli.py` | `src/millrace_ai/cli/app.py`, `src/millrace_ai/cli/shared.py`, `src/millrace_ai/cli/errors.py`, `src/millrace_ai/cli/status_view.py`, `src/millrace_ai/cli/runs_view.py`, `src/millrace_ai/cli/config_view.py`, `src/millrace_ai/cli/compile_view.py`, `src/millrace_ai/cli/formatting.py`, `src/millrace_ai/cli/monitoring.py`, `src/millrace_ai/cli/commands/*` | `millrace_ai.cli` is now a package surface; command groups live in dedicated modules, daemon monitor formatting is isolated, and status/run/config/compile views own their filesystem-backed data loading instead of feeding back through shared command helpers. |
| `millrace_ai/runtime.py` | `src/millrace_ai/runtime/engine.py` plus owned modules for lifecycle, tick cycle, active runs, compiled-plan archives, mailbox intake, watcher intake, activation, reconciliation, result persistence/application, runtime effects, lifecycle interpretation, closure boundary, completion behavior, error recovery, blocked recovery, lane scheduling, plane concurrency, lane conflicts, request context, generic runtime-effect operation runners, Planner effects, failure policy, run traces, learning triggers/promotions, skill evidence, snapshot state, approvals, capability gates, usage governance, graph authority, closure transitions, Recon transitions, work-item transitions, stage requests, handoff incidents, monitoring, and inspection | `millrace_ai.runtime` is now a package that re-exports `RuntimeEngine`, `RuntimeTickOutcome`, runtime monitor types, and daemon supervisor surfaces. `engine.py` remains the stable facade while collaborators own runtime lifecycle, lane-keyed scheduling, shared compiled scheduler-policy interpretation, immutable launch-plan authority, request-context artifacts, compiled workflow effect dispatch, runtime-effect mutations selected by compiled operation metadata, repair diagnostics, recovery policy, and routed post-stage mutation details. Ordinary source work-item mutation now resolves terminal-action lifecycle metadata from compiled lifecycle plans through `result_application.py`, `work_item_transitions.py`, `lifecycle_interpreter.py`, and `handoff_incidents.py`, while graph-authority terminal decisions can carry explicit non-mutating terminal-action or runtime-operation authority instead of source mutation. `runtime/closure_boundary.py` is the named kernel-facing boundary for closure lifecycle, lineage gating, backpressure behavior, closure-lineage claiming, closure-target result validation, and closure Arbiter request-field construction, delegating lazily to `completion_behavior.py`. `runtime/closure_transitions.py` validates typed Arbiter verdict provenance against the closure freshness window and requires every deciding criterion to be fresh or revalidated after newer same-lineage remediation; explicit context-only criteria may remain historical. `runtime/effect_execution.py` also applies effect-rule-declared source completion/blocking lifecycle intent for `REQUEST_COMPLETE_SOURCE` and `REQUEST_BLOCK_SOURCE`, with repair-route exceptions remaining failure-policy-owned. |
| `millrace_ai/control.py` | `src/millrace_ai/runtime/control.py`, `src/millrace_ai/runtime/control_mailbox.py`, `src/millrace_ai/runtime/control_mutations.py` | Root `control.py` remains a thin compatibility facade. |
| `millrace_ai/config.py` | `src/millrace_ai/config/models.py`, `src/millrace_ai/config/loading.py`, `src/millrace_ai/config/boundaries.py`, `src/millrace_ai/config/toml_editing.py` | `millrace_ai.config` is now a package surface; usage-governance config models live in `models.py` and apply on next-tick boundaries. `models.py` owns the default durable idle-event heartbeat constant used by runtime config and the engine no-config fallback. Model alias config and assignment policy are recompile boundaries, with TOML-preserving CLI mutation helpers in `toml_editing.py`. |
| `millrace_ai/contracts.py` | `src/millrace_ai/contracts/__init__.py`, `base.py`, `arbiter_verdict.py`, `closure_evidence.py`, `blueprint.py`, `capabilities.py`, `enums.py`, `stage_metadata.py`, `terminal_outcomes.py`, `token_usage.py`, `work_documents.py`, `work_refs.py`, `recon.py`, `stage_results.py`, `graph_exports.py`, `run_trace.py`, `loop_config.py`, `modes.py`, `compile_diagnostics.py`, `runtime_snapshot.py`, `runtime_errors.py`, `mailbox.py`, `recovery.py`, `extensions.py` | `millrace_ai.contracts` remains the public facade for canonical typed contracts; named submodules own contract families, including execution capability contracts, Arbiter verdict contracts, closure freshness contracts, Blueprint packet/evaluation contracts, Recon packet contracts, required-extension declarations, work-family reference normalization, compiled-stage-graph exports, run-trace graph artifacts, and `stage_metadata.py` as the shipped registry instance for built-in stage plane membership, legal terminal results, runner prompt markers, and result-class policy. It is a compatibility surface for shipped stages, not universal runtime authority (see ADR-0013). Custom graphs and stage-kind assets derive their authority from their own JSON declarations and the compiled plan. `terminal_outcomes.py` carries the string-backed terminal outcome contract shared by stage results, runtime snapshots, runtime error contexts, and runner normalization. |
| `millrace_ai/compiler.py` | `src/millrace_ai/compiler.py`, `src/millrace_ai/compilation/` | `millrace_ai.compiler` remains the public facade; compiler outcomes, workspace compile orchestration, graph preview/export, mode/path resolution, graph and node materialization, policy compilation, scheduler policy resolution/validation, workflow primitive resolution/validation, required-extension validation, scheduler lane validation, execution-capability grant resolution, asset resolution, fingerprints, persistence, and currentness inspection live in `compilation/`. |
| `millrace_ai/entrypoints.py` | `src/millrace_ai/assets/entrypoints/__init__.py`, `models.py`, `discovery.py`, `parsing.py`, `advisory.py`, `linting.py`, `rendering.py` | Root `entrypoints.py` remains a thin compatibility facade; packaged markdown entrypoint assets live in the same `assets/entrypoints/` directory under `execution/`, `planning/`, and `learning/`. |
| `millrace_ai/modes.py` | `src/millrace_ai/assets/modes.py` | Root `modes.py` remains a thin compatibility facade. |
| `millrace_ai/stage_kinds.py` | `src/millrace_ai/assets/architecture.py`, `src/millrace_ai/architecture/stage_kinds.py` | Root `stage_kinds.py` is the thin public facade for stage-kind registry loading. |
| `millrace_ai/loop_graphs.py` | `src/millrace_ai/assets/loop_graphs.py`, `src/millrace_ai/architecture/loop_graphs.py` | Root `loop_graphs.py` is the thin public facade for graph-loop loading. |
| `millrace_ai/runner.py` | `src/millrace_ai/runners/requests.py`, `src/millrace_ai/runners/normalization/`, `src/millrace_ai/runners/base.py`, `contracts.py`, `dispatcher.py`, `errors.py`, `process.py`, `registry.py`, `src/millrace_ai/runners/adapters/codex_cli.py`, `codex_cli_command.py`, `codex_cli_artifacts.py`, `codex_cli_tokens.py`, `pi_rpc.py`, and `pi_rpc_client.py` | Root `runner.py` remains a thin compatibility facade over the `runners` package; runner registration/dispatch, normalization, process helpers, typed errors, Codex adapter command construction, artifact handling, token extraction, and Pi RPC integration have focused modules behind the public adapter classes. |
| `millrace_ai/doctor.py` | `src/millrace_ai/doctor/__init__.py`, `checks.py`, `models.py`, `output.py`, `workspace_checks.py`, `queue_checks.py`, and `asset_checks.py` | `millrace_ai.doctor` is now a package facade that preserves `DoctorIssue`, `DoctorReport`, and `run_workspace_doctor` while keeping check families in focused modules. |
| `millrace_ai/run_inspection.py` | `src/millrace_ai/runtime/inspection.py` | Root `run_inspection.py` remains a thin compatibility facade. |
| `millrace_ai/paths.py` | `src/millrace_ai/workspace/paths.py`, `src/millrace_ai/workspace/initialization.py` | Root `paths.py` remains a thin compatibility facade for `WorkspacePaths`, `workspace_paths`, and workspace initialization helpers. |
| workspace initialization/baseline/schema epoch | `src/millrace_ai/workspace/initialization.py`, `src/millrace_ai/workspace/bootstrap_files.py`, `src/millrace_ai/workspace/asset_deployment.py`, `src/millrace_ai/workspace/baseline.py`, `schema_epoch.py`, `schema_epoch_marker.py` | Explicit `millrace init`, default runtime file payloads, runtime asset deployment, managed baseline upgrade classification, schema epoch markers, and archive/reset helpers live in workspace-owned modules with path modeling kept separate from bootstrap behavior. |
| workspace idea source artifacts | `src/millrace_ai/workspace/idea_sources.py` | Runtime-owned idea intake helpers stage operator input under `millrace-agents/intake/ideas/inbox/`, preserve durable source markdown under `millrace-agents/intake/sources/idea/`, write normalized metadata under `millrace-agents/intake/ideas/normalized/`, archive consumed legacy inbox files under `millrace-agents/intake/ideas/archived/legacy/`, and capture invalid legacy markdown under `millrace-agents/intake/ideas/invalid/` with diagnostic metadata. |
| runtime-owned history log artifacts | `src/millrace_ai/contracts/history_log.py`, `src/millrace_ai/runtime/result_application.py`, `src/millrace_ai/workspace/history_log.py` | Runtime-owned `history_entry.json` proposals are normalized by runtime-authoritative identity, appended once into `millrace-agents/history-log/entries/`, and rendered to `daily/`, `latest.md`, and `index.md`; `historylog.md` remains deprecated compatibility. |
| workspace map surfaces | `src/millrace_ai/workspace_map/`, `src/millrace_ai/cli/commands/workspace_map.py` | `millrace workspace-map refresh` owns `workspace-map/generated/*` and `workspace-map/manifest.json`; `workspace-map/index.md` is seeded starter/index guidance; `validate` checks missing/stale/malformed/schema/path issues without writes; `show` renders a compact summary. Curated wiki pages under `workspace-map/wiki/` are operator/Updater-maintained and are not generator output. |
| `millrace_ai/runtime_lock.py` | `src/millrace_ai/workspace/runtime_lock.py` | Root `runtime_lock.py` remains a thin compatibility facade. |
| `millrace_ai/mailbox.py` | `src/millrace_ai/workspace/mailbox.py` | Root `mailbox.py` remains a thin compatibility facade. |
| `millrace_ai/events.py` | `src/millrace_ai/workspace/events.py` | Root `events.py` remains a thin compatibility facade. `iter_runtime_events()`, `read_recent_runtime_events()`, and `find_latest_runtime_event()` are the normal bounded/streaming helpers for status, closure, and web views; `read_runtime_events()` is the explicit full-history audit/debug API. |
| `millrace_ai/work_documents.py` | `src/millrace_ai/workspace/work_documents.py` | Root `work_documents.py` remains a thin compatibility facade. |
| `millrace_ai/recon_packets.py` | `src/millrace_ai/recon_packets.py` | Recon packet markdown parsing/rendering is a root helper because runtime transitions and stage tests share the same artifact contract. |
| `millrace_ai/queue_store.py` | `src/millrace_ai/workspace/queue_store.py`, `queue_claims.py`, `queue_selection.py`, `queue_transitions.py`, `queue_lifecycle.py`, `queue_reconciliation.py`, `task_lifecycle_integrity.py`, `work_inventory.py`, `work_item_adapters.py`, `operator_interventions.py`, `lineage_integrity.py`, `arbiter_state.py`, `remote_skills.py`, `blueprint_state.py` | Root `queue_store.py` remains a thin compatibility facade over the workspace queue package. Queue claim policy, repeated-remediation incident quarantine, compiled lifecycle mutation interpretation, family-aware work-item adapters, work inventory, intervention/lineage integrity, Arbiter state, remote skill metadata, and Blueprint artifact state have explicit workspace-owned homes. |
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
  `config`, `contracts`, `doctor`, `extensions`, `runners`,
  `runners.normalization`, `runtime`, `runtime.graph_authority`,
  `runtime.usage_governance`, and `workspace`. Empty CLI package initializers
  remain package markers, not API expansion points.

## Compiled Architecture And Workflow Primitive Authority

The configurable runtime authority now has dedicated packages and asset
families:

- `src/millrace_ai/architecture/stage_kinds.py` defines typed stage-kind contracts
- `src/millrace_ai/architecture/loop_graphs.py` defines typed graph-loop contracts
- `src/millrace_ai/architecture/materialization.py` defines the graph-plan materialization contracts, including normalized compiled entry/transition indexes, runtime-authority flags, and legacy-equivalence compatibility reporting
- `src/millrace_ai/architecture/effect_operations.py` defines runtime
  operation and runtime-effect operation/store/validator/primitive contracts,
  including `RuntimeEffectPrimitiveDefinition` (with
  `non_interpreted_compatibility` marker and `required_capabilities`),
  `RuntimeEffectOperationStepDefinition` (with `input_bindings`,
  `params`, `output_context_key`, `context_read_key`, and binding grammar
  validation for `$artifact.<id>`, `$context.<key>`, `$store.<id>` syntax),
  and `RuntimeEffectOperationRunnerDefinition` (with `operation_ids`,
  `required_runtime_capabilities_by_operation_id`, and
  `legacy_handler_ids`)
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
  assets, including queue claim policies and scheduler policies
- `src/millrace_ai/assets/effect_operations.py` loads runtime-operation and
  runtime-effect operation/store/validator/primitive registry assets
- `src/millrace_ai/assets/extensions.py` loads extension package manifests
  from `registry/extensions/`
- `src/millrace_ai/extensions/interfaces.py` defines built-in extension
  boundary Protocols for generic context/artifact behavior, Recon transitions,
  closure transitions, Blueprint validation/context behavior, and Learning
  trigger/promotion behavior
- `src/millrace_ai/extensions/boundaries.py` owns the lazy
  `BuiltInExtensionBoundaryRegistry`, which resolves built-in implementations
  by interface ID without importing domain modules until requested
- `src/millrace_ai/extensions/builtin/` ships thin built-in adapter modules
  that bridge existing generic, Recon, closure, Blueprint, and Learning
  implementations to those boundary interfaces
- `src/millrace_ai/assets/registry/stage_kinds/` ships the stage-kind registry JSON
- `src/millrace_ai/assets/registry/extensions/` ships extension package
  manifest JSON assets
- `src/millrace_ai/assets/registry/work_item_families/` ships claimable work
  family definitions, including Blueprint drafts
- `src/millrace_ai/assets/registry/document_adapters/` ships built-in markdown
  adapters
- `src/millrace_ai/assets/registry/queue_claim_policies/` ships plane claim
  ordering and eligibility policy
- `src/millrace_ai/assets/registry/scheduler_policies/` ships compiled
  scheduler-policy definitions, predicate-backed claim rules, and plane/lane
  concurrency policy
- `src/millrace_ai/assets/registry/terminal_actions/`,
  `lifecycle_mutation_plans/`, `runtime_operations/`,
  `runtime_effect_handlers/`, and `runtime_effect_rules/` ship compiled
  post-stage mutation authority and runtime-operation definitions
- `src/millrace_ai/assets/registry/runtime_effect_operations/`,
  `runtime_effect_runners/`, `runtime_effect_primitives/`, `effect_stores/`,
  and `effect_validators/` ship compiler-validated declarative runtime-effect
  operation catalogs, primitive metadata, and runner ownership used as the
  runtime dispatch identity during the legacy-handler migration; this catalog
  remains distinct from `registry/runtime_operations/`
- `src/millrace_ai/assets/registry/recovery_policies/` and
  `runtime_failure_policies/` ship compiler-validated recovery/failure policy
  hooks
- `src/millrace_ai/assets/registry/workspace_schema_epochs/` ships active
  schema epoch compatibility data
- `src/millrace_ai/assets/graphs/` ships the graph-loop JSON
- `src/millrace_ai/assets/loops/execution/lad.json` and
  `src/millrace_ai/assets/graphs/execution/lad.json` ship the LAD Execution
  loop
- `src/millrace_ai/assets/loops/planning/lad.json` and
  `src/millrace_ai/assets/graphs/planning/lad.json` ship the LAD Planning
  loop
- `src/millrace_ai/assets/graphs/planning/blueprint.json` ships the opt-in
  Blueprint Planning loop
- `src/millrace_ai/assets/loops/learning/default.json` and
  `src/millrace_ai/assets/graphs/learning/standard.json` ship the learning
  loop alongside execution and planning, including the
  Analyst/Professor/Curator chain and the targeted Librarian stage for
  post-Planner remote optional-skill preparation
- `src/millrace_ai/assets/loops/execution/lad_integrator.json` and
  `src/millrace_ai/assets/graphs/execution/lad_integrator.json` ship the
  opt-in high-assurance execution loop
- `src/millrace_ai/assets/modes/blueprint_lad_codex.json` selects
  `planning.blueprint` with LAD execution
- `src/millrace_ai/assets/modes/blueprint_learning_lad_codex.json` selects
  `planning.blueprint`, `learning.standard`, and LAD execution
- `src/millrace_ai/assets/modes/learning_lad_codex.json` and
  `src/millrace_ai/assets/modes/learning_lad_pi.json` select execution, planning,
  and learning loops with compiler-frozen learning trigger rules, including
  Planner-to-Librarian optional-skill preparation
- `src/millrace_ai/assets/modes/efficient_learning_lad_mixed.json` selects the
  same LAD plus Learning topology as `learning_lad_codex` while carrying a
  mode-local mixed Codex/Pi model/depth alias profile and leaving Integrator inactive

This asset set now owns runtime control-flow and workflow mutation authority
after compilation. Legacy loop assets and root router modules still remain in
the package as compatibility and inspection surfaces, but runtime execution
reads the persisted compiled plan.

Prospective boundary packages referenced by the ADRs are **not yet created**:
a unified `registry/` package (ADR-0013) that would consolidate stage-kind,
graph-loop, and workflow-primitive asset loading, and a
`runtime/operations/` package (ADR-0014) that would host the unified step
interpreter for all runtime operation steps. These are boundary descriptors
for future migration, not shipped runtime modules.

## Intentionally Preserved Root Modules

These modules remain at the package root because they still have one coherent reason to change or they define foundational errors/adapters used across the package:

- `src/millrace_ai/router.py` — contract-only compatibility facade for legacy
  imports. `RouterAction` and `RouterDecision` live in
  `src/millrace_ai/contracts/router.py`; active dispatch runs through
  `millrace_ai.runtime.graph_authority.routing` and `generic_router`, and the
  root facade is not wired into the active tick cycle.
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

- `workspace/paths.py` now owns only the workspace path model and resolution,
  including the `runtime_effect_journal_dir` property that resolves to
  `millrace-agents/state/runtime-effect-journal/`.
- `workspace/bootstrap_files.py` owns default state/status/config payload
  construction for newly initialized workspaces, including the shared
  `MILLRACE.md` starter, the seeded `workspace-map/index.md` starter guidance,
  and curated `workspace-map/wiki/` seed pages.
- `workspace/asset_deployment.py` owns packaged runtime asset source resolution
  and deployment.
- `workspace/idea_sources.py` owns durable runtime copies of idea source
  markdown used by closure-target creation, plus normalized metadata and
  legacy archive/invalid handling for idea intake.
- `workspace/work_item_adapters.py` owns family-aware loading/rendering for
  built-in queue documents.
- `workspace/family_adapters.py` owns work-family queue adapter contracts and
  registry lookup for claim/lifecycle/requeue/lineage operations bound to
  workflow family contracts.
- `workspace/queue_family_interpreter.py` owns the generic family-agnostic
  local filesystem queue interpreter driven by compiled family definitions
  and document adapter definitions. No family-ID branch dispatch is
  performed.
- `workspace/queue_lifecycle.py` applies compiled lifecycle mutation plans to
  source queue documents with explicit source, outcome, family, and
  applicability-context metadata.
- `workspace/blueprint_state.py` is a deliberate removal stub for the durable
  Blueprint manifest, draft, packet, critique, evaluation, and promotion state
  helpers, plus read-only manifest/draft diagnostic helpers used by Doctor.
  The live Blueprint state helpers live under
  `extensions/builtin/blueprint/state.py` and
  `extensions/builtin/blueprint/doctor.py`.
- `workspace/schema_epoch.py` and `workspace/schema_epoch_marker.py` own schema
  epoch markers and daemon-safe archive/reset helpers.
- `workspace/initialization.py` orchestrates initialization and keeps
  `bootstrap_workspace` as the compatibility alias used by older callers.
- `cli/errors.py` owns operator error output.
- `cli/status_view.py` is a compatibility facade over `cli/status/`, where
  `collection.py` owns status view-model assembly and `rendering.py` owns text
  and JSON output. `collection.py` and `rendering.py` now route registered
  extension status projections through `cli/status/projections.py`, which
  loads manifest-declared projection modules. Blueprint status collection,
  default payload, default lines, and rendering live in
  `extensions/builtin/blueprint/status.py`, while `cli/status/blueprint.py`
  is a deliberate removal stub and generic status collection stays free of
  Blueprint implementation imports at import time. `cli/runs_view.py`,
  `cli/config_view.py`, and
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
- `runtime/status_projections.py` owns shared family-keyed queue-depth,
  scope-keyed status, and lane-keyed active-run projection helpers, including
  the legacy plane-keyed compatibility projections used by status, monitor, and
  snapshot consumers.
- `runtime/monitoring.py`, `runtime/pause_state.py`, and
  `runtime/handoff_incidents.py` own runtime event sink contracts, pause-source
  projection, and operator handoff incident state. Runtime-created
  closure-target Arbiter remediation incidents carry durable
  `created_by=millrace-runtime` plus `trigger_metadata` provenance and dedupe
  by root spec plus previous Arbiter identity when available; non-closure
  handoffs keep their existing path.
- `runtime/compiled_plans.py` preserves active-run launch-plan authority when
  config reload compiles a newer pending plan.
- `runtime/request_context.py` is the compatibility facade for deterministic
  per-request context rendering imports.
- `runtime/stage_requests.py`, `runtime/closure_boundary.py`,
  `runtime/completion_behavior.py`,
  `runtime/blocked_recovery.py`, and `workspace/operator_interventions.py`
  consume family adapter resolution for family-scoped active-path, lineage,
  and retry/cancellation location behavior instead of hard-coded family path
  branches.
- `runtime/context/` owns deterministic per-request context bundles, compiled
  node/profile/render-plan authority resolution, provider-registry dispatch,
  selected provider_id propagation, rendered prompt-context artifacts,
  generic provider behavior, and Blueprint provider behavior isolated from the
  generic facade.
- `runtime/effects/`, `runtime/effect_execution.py`, and
  `runtime/lifecycle_interpreter.py` own generic compiled runtime-effect and
  source-lifecycle application. Source lifecycle intents now come from resolved
  terminal-action metadata and compiled lifecycle plans with explicit source,
  outcome, family, and applicability-context scope for ordinary idle, blocked,
  and handoff mutation. Runtime-effect rules now also declare source
  completion/blocking lifecycle consequence metadata for
  `REQUEST_COMPLETE_SOURCE` and `REQUEST_BLOCK_SOURCE`.
  Runtime-effect operations are dispatched through one of two paths:
  interpreted (`runner_id = "interpreted_runtime_effect"`) or legacy
  handler-backed (`runner_id = "legacy_python_handler"`). All shipped
  operations use the legacy path; the interpreted path is activated only by
  test fixture operations.
  `runtime/effects/interpreter.py` provides the constrained step interpreter
  that walks compiled operation step lists, resolves `$artifact.<id>` /
  `$context.<key>` / `$store.<id>` bindings, dispatches each step's
  `primitive_id` to `primitives.py`, enforces idempotent resume via
  `journal.py`, and returns a `RuntimeEffectResult`.
  `runtime/effects/primitives.py` provides the `PrimitiveExecutorRegistry`
  with five built-in interpreted executors: `artifact_presence`,
  `artifact_model_parse`, `persist_record`, `enqueue_work_items`, and
  `emit_event`. Executors receive the `InterpreterContext` and return a
  result dict with `decision`, optional `failure_class`, and
  `created_paths`.
  `runtime/effects/journal.py` provides durable JSONL journal helpers:
  `write_started_record` (before mutation), `write_completed_record` (after
  mutation with SHA-256 idempotency hash), `write_failed_record` (mutating
  primitive failure records with failure class/message), and
  `completed_hashes_by_step` (authoritative completed hashes for resume).
  Journal records carry runner/source/run/request/step/primitive context and
  explicit status. Journal files live at
  `millrace-agents/state/runtime-effect-journal/<operation_id>.jsonl`.
  `runtime/effects/registry.py` provides the operation-indexed handler
  registry seam, and `runtime/effects/legacy.py` is the temporary registry for
  legacy Python effect handlers while declarative operation migration proceeds.
  `runtime/effects/` owns the generic runtime-effect contracts, registry,
  interpreter, journal, legacy handler registry, and helper modules, while
  `extensions/builtin/blueprint/operation_runners/` owns the Blueprint
  operation-runner implementation. Blueprint planning modes select those
  generic operations through compiled runtime-effect rule assets; the retired
  `runtime/effects/operation_runners/__init__.py` path now raises
  `ImportError` and no runtime module is a dedicated Blueprint loop
  dispatcher.
  Stage results and runtime events carry operation id and runner id as
  authority metadata; legacy handler id is
  optional compatibility metadata. `runtime/effect_execution.py` also
  routes default runtime-effect repair exhaustion through shared terminal-action
  resolution when the active graph declares an exhausted terminal state.
- `runtime/planner_effects.py` owns Planner-specific runtime effects while
  registering on the same compiled effect-selection path as Blueprint effects.
- `runtime/closure_boundary.py` owns the named kernel-facing closure boundary
  for target lifecycle, lineage gating, backpressure policy, and
  result-normalization boundary responsibilities. `runtime/completion_behavior.py`
  remains the internal implementation of compiled completion behavior behind
  that boundary, while
  `runtime/recovery/` now owns focused recovery subdomains:
  `blocked_metadata.py`, `retry_policy.py`, `environmental.py`,
  `queue_mutation.py`, `error_context.py`, `reports.py`, `repair_routes.py`,
  and recovery event helpers. `runtime/blocked_recovery.py` remains a
  compatibility facade so existing runtime and CLI imports stay stable, while
  `runtime/error_recovery.py` remains the public runtime recovery orchestration
  entry module over those focused recovery helpers, including shared
  terminal-action exhaustion decisions for post-stage and pre-dispatch
  recovery exhaustion.
- `runtime/artifact_contracts.py`, `runtime/stage_result_persistence.py`,
  `runtime/result_counters.py`, `runtime/recon_transitions.py`, and
  `runtime/work_item_transitions.py` keep post-stage artifact validation,
  durable result writes, normalized terminal-outcome counters, and
  terminal-action/runtime-operation-driven family-specific transition helpers
  and explicit non-mutating terminal-action clearing out of the central tick
  cycle.
- `runtime/runtime_effect_status.py` extracts generic latest runtime-effect
  metadata for status output without mode-specific diagnostic reconstruction.
- `runtime/failure_policy.py` keeps runtime failure classification aligned with
  compiled failure-policy identifiers and matches runtime-effect policies by
  operation id before falling back to legacy handler aliases.
- `runtime/usage_governance/` is a package-level authority domain. Its facade
  preserves the previous `millrace_ai.runtime.usage_governance` imports while
  models, state persistence, ledger reconciliation, runtime-token windows,
  subscription-quota telemetry, monitor events, and pause-source application
  live in named modules.
- `runtime/graph_authority/` is a package-level authority domain — the
  **generic-router home for all planes**. Its facade preserves the previous
  `millrace_ai.runtime.graph_authority` imports while activation, validation,
  policy lookup, counters, stage mapping, and terminal-state/action and
  runtime-operation resolution live in named modules.
  `runtime/graph_authority/generic_router.py` owns the active compiled-graph
  routing logic (`route_generic_stage_result_from_graph`);
  `runtime/graph_authority/routing.py` is the single active dispatch entrypoint
  (`route_stage_result_from_graph`) that validates stage-result identity and
  routes every plane directly through the generic router with plane-agnostic
  formatter callbacks. Fallback route reasons and failure classes derive from
  compiled ``node_id`` rather than runtime stage-name strings. `execution.py`,
  `planning.py`, and `learning.py` are thin compatibility wrappers over the
  generic router (no plane-enum routing branches, per-plane wrapper dispatch,
  or route-time max-cycle recovery knobs remain in active dispatch).
  `runtime/graph_authority/counters.py`
  applies declared recovery-counter mutation intent from compiled policy
  metadata instead of inferring it from destination stage names.
- `runtime/graph_authority/terminal_actions.py` resolves terminal-state/action
  metadata, including runtime-operation ids, for terminal transitions,
  threshold exhaustion, runtime-failure recovery exhaustion, explicit
  non-mutating terminal-action authority, and lifecycle-action validation.
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
  required-extension, and repair-closure checks. Generic cross-asset compile
  checks for `route_to_node` repair closure resolution (operation/failure scope,
  target-node outcome binding, evidence artifacts, family scope, resume guards,
  and partial-mutation support) live in `compilation/validation/repair_closures.py`;
  Blueprint recovery routes are one instance of that generic validator, not a
  separate architectural authority path.
- `contracts/` is the typed contract package behind the stable
  `millrace_ai.contracts` facade. Enums, stage metadata, work documents,
  execution-capability grants, stage-result envelopes, compiled graph exports,
  Blueprint contracts, run-trace graphs, loop/mode definitions, compiler
  diagnostics, runtime snapshots, runtime error contexts, mailbox payloads,
  required-extension declarations, and recovery counters live in named modules
  with shared validators kept at the contract layer.
- `contracts/stage_metadata.py` is the shipped stage-metadata registry
  instance loaded from JSON stage-kind assets (a compatibility surface for
  built-in stages, not universal runtime authority — see ADR-0013). Runner
  request defaults, terminal-result normalization, entrypoint stage linting,
  and graph stage lookup derive plane, marker, and result-class truth for
  shipped stages from that facade. Custom graphs derive their authority from
  their own JSON stage-kind assets and the compiled plan.
- `assets/entrypoints/` is both the packaged entrypoint asset directory and the
  entrypoint asset parsing package. Models, path discovery, markdown
  frontmatter parsing, advisory skill-reference checks, lint policy, and
  diagnostic rendering now have separate module ownership behind the stable
  `millrace_ai.assets.entrypoints` facade. Path discovery now indexes
  discovered stage-kind assets and their `default_entrypoint_path` values so
  custom stage kinds can resolve non-canonical entrypoint filenames without
  hardcoded stage-name sets.
- `workspace/work_inventory.py`, `workspace/operator_interventions.py`,
  `workspace/lineage_integrity.py`, `workspace/arbiter_state.py`, and
  `workspace/remote_skills.py` keep workspace inventory, operator intervention
  records, lineage checks, Arbiter closure state, and remote skill metadata out
  of the queue-store facade.
- `doctor/checks.py`, `doctor/workspace_checks.py`,
  `doctor/queue_checks.py`, and `doctor/asset_checks.py` keep Doctor check
  registration, manifest-discovered extension diagnostics, workspace/runtime
  diagnostics, queue parseability, and asset/runner posture checks separate
  while preserving `millrace_ai.doctor`. `doctor/checks.py` now loads
  `doctor_diagnostic` extension items and dispatches a conventional
  `run_doctor_diagnostics(context)` hook, while
  `extensions/builtin/blueprint/doctor.py` owns the Blueprint manifest
  diagnostics behind that extension hook.
  `doctor/queue_checks.py` now resolves queue-artifact models from schema IDs
  and registered adapters instead of Blueprint-specific model branches.
  `src/millrace_ai/workspace/blueprint_state.py` is a deliberate removal stub
  for existing Blueprint state and Doctor diagnostic helper imports;
  authoritative Blueprint state lives under `extensions/builtin/blueprint/state.py`,
  and Blueprint Doctor diagnostics live under `extensions/builtin/blueprint/doctor.py`.
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

- `lad_codex`
- `lad_pi`
- `learning_lad_codex`
- `efficient_learning_lad_mixed`
- `learning_lad_pi`
- `lad_codex_integrated`
- `learning_lad_codex_integrated`
- `blueprint_lad_codex`
- `blueprint_learning_lad_codex`

`standard_plain`, `standard_millrace`, `learning_enabled_millrace`, old
`default_*` IDs, old unqualified `learning_*` IDs, and
`efficient_learning_mixed` are preserved only as compatibility aliases in the
asset-loading layer, not as duplicated mode asset files.

## Pure Graph-Authority Retired Facades and Retained Shims

The pure graph-authority cleanup is intentionally breaking for runtime
decisions. The Blueprint-specific entries below are retired removal stubs;
the remaining non-Blueprint rows are the live retained shims. These entries
are file-scoped and must not become active generic-kernel authority:

Old Python imports of the retired Blueprint paths may now raise `ImportError`.
Use the extension-owned Blueprint modules instead of generic package paths.

| Shim file or package | Allowed callers | Retention rationale | Guardrail coverage |
| --- | --- | --- | --- |
| `src/millrace_ai/contracts/blueprint.py` | Former Blueprint contract import path | Deliberate removal stub; Blueprint contract implementations live under `extensions/builtin/blueprint/` | `tests/maintenance/test_pure_graph_authority_guardrails.py` and generic import/startup guardrails ensure generic paths do not load Blueprint implementation modules or export Blueprint contract names |
| `src/millrace_ai/cli/status/blueprint.py` | Former public import path for the historical Blueprint CLI status module; generic status assembly must use manifest-discovered `status_projection` items | Deliberate removal stub; Blueprint status projection collection, defaults, and rendering live under `extensions/builtin/blueprint/status.py` | Pure graph-authority guardrails fail direct generic calls to Blueprint status APIs, generic `blueprints` projection branches, `blueprint_status` in generic status models, and domain-owned status projection manifest items that point into generic CLI/Doctor/workspace modules |
| `src/millrace_ai/runtime/context/blueprint.py` | Former Blueprint request-context provider import path | Deliberate removal stub; implementation lives under `extensions/builtin/blueprint/context.py` | Generic runtime import/startup guardrails and pure graph-authority guardrails prevent eager generic loading |
| `src/millrace_ai/workspace/blueprint_state.py` and `src/millrace_ai/workspace/families/blueprint.py` | Former Blueprint state/family adapter and Doctor diagnostic helper import paths; generic Doctor checks must use manifest-discovered `doctor_diagnostic` items | Deliberate removal stubs; Blueprint state/family behavior lives under `extensions/builtin/blueprint/` and Doctor diagnostics live under `extensions/builtin/blueprint/doctor.py` | Pure graph-authority guardrails forbid generic family-id branching, direct Blueprint implementation imports outside allowed facades, direct Doctor registration of Blueprint diagnostics, domain-owned Doctor diagnostic manifest items that point into generic CLI/Doctor/workspace modules, and eager generic loading of Blueprint implementation modules |
| `src/millrace_ai/runtime/effects/operation_runners/__init__.py` | Former legacy Blueprint runner registration import path | Deliberate removal stub; current implementation lives in `extensions/builtin/blueprint/operation_runners/` | Runtime-effect registry and generic-kernel guardrails ensure the export stays empty and generic paths do not select Blueprint branches |
| `src/millrace_ai/contracts/stage_metadata.py` | Runner normalization, entrypoint linting, and compatibility lookups for shipped stages | Shipped-stage facade backed by JSON stage-kind assets; custom stage kinds derive authority from assets and compiled plans | Shipped-stage hardwiring and entrypoint discovery guardrails protect fixture/custom stage-kind behavior |

The detailed compatibility inventory lives in
`docs/maintenance/public-api-compatibility-inventory.md` and
`docs/adr/0016-extension-boundary-compatibility-facades.md`.

## Test Ownership Map

| Source area | Mirrored tests |
| --- | --- |
| `src/millrace_ai/assets/` | `tests/assets/` |
| `src/millrace_ai/architecture/` | `tests/architecture/` |
| `src/millrace_ai/cli/` | `tests/cli/` |
| Blueprint/custom graph contracts/effects/state | `tests/runtime/test_effect_execution.py`, `tests/runtime/test_runtime_effects.py`, `tests/runtime/test_runtime_effect_status.py`, `tests/runtime/test_request_context.py`, `tests/runtime/test_effect_operation_external_fixture.py`, `tests/integration/test_custom_graph_runtime_authority.py` |
| `src/millrace_ai/compilation/` | `tests/compilation/`, `tests/integration/test_compiler.py` |
| `src/millrace_ai/config/` | `tests/config/` |
| `src/millrace_ai/contracts/` | `tests/runtime/test_contracts.py`, `tests/runtime/test_closure_transitions.py`, `tests/compilation/test_extension_validation.py` |
| `src/millrace_ai/extensions/` | `tests/compilation/test_extension_validation.py` |
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
