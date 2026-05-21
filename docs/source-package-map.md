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
  separate source distribution; it is not imported by or packaged into the base
  `millrace-ai` wheel

## Old-To-New Module Map

| Legacy surface | Current source home | Notes |
| --- | --- | --- |
| `millrace_ai/cli.py` | `src/millrace_ai/cli/app.py`, `src/millrace_ai/cli/shared.py`, `src/millrace_ai/cli/errors.py`, `src/millrace_ai/cli/status_view.py`, `src/millrace_ai/cli/runs_view.py`, `src/millrace_ai/cli/config_view.py`, `src/millrace_ai/cli/compile_view.py`, `src/millrace_ai/cli/formatting.py`, `src/millrace_ai/cli/monitoring.py`, `src/millrace_ai/cli/commands/*` | `millrace_ai.cli` is now a package surface; command groups live in dedicated modules, daemon monitor formatting is isolated, and status/run/config/compile views own their filesystem-backed data loading instead of feeding back through shared command helpers. |
| `millrace_ai/runtime.py` | `src/millrace_ai/runtime/engine.py` plus owned modules for lifecycle, tick cycle, active runs, compiled-plan archives, mailbox intake, watcher intake, activation, reconciliation, result application, runtime effects, lifecycle interpretation, lane scheduling, lane conflicts, request context, Blueprint effects, failure policy, run traces, learning triggers, skill evidence, snapshot state, approvals, capability gates, usage governance, graph authority, closure transitions, stage requests, and inspection | `millrace_ai.runtime` is now a package that re-exports `RuntimeEngine`, `RuntimeTickOutcome`, and daemon supervisor surfaces. `engine.py` remains the stable facade while collaborators own runtime lifecycle, lane-keyed scheduling, immutable launch-plan authority, request-context artifacts, compiled workflow effect dispatch, Blueprint-specific mutations, and routed post-stage mutation details. |
| `millrace_ai/control.py` | `src/millrace_ai/runtime/control.py`, `src/millrace_ai/runtime/control_mailbox.py`, `src/millrace_ai/runtime/control_mutations.py` | Root `control.py` remains a thin compatibility facade. |
| `millrace_ai/config.py` | `src/millrace_ai/config/models.py`, `src/millrace_ai/config/loading.py`, `src/millrace_ai/config/boundaries.py` | `millrace_ai.config` is now a package surface; usage-governance config models live in `models.py` and apply on next-tick boundaries. |
| `millrace_ai/contracts.py` | `src/millrace_ai/contracts/__init__.py`, `base.py`, `blueprint.py`, `capabilities.py`, `enums.py`, `stage_metadata.py`, `token_usage.py`, `work_documents.py`, `recon.py`, `stage_results.py`, `graph_exports.py`, `run_trace.py`, `loop_config.py`, `modes.py`, `compile_diagnostics.py`, `runtime_snapshot.py`, `runtime_errors.py`, `mailbox.py`, `recovery.py` | `millrace_ai.contracts` remains the public facade for canonical typed contracts; named submodules own contract families, including execution capability contracts, Blueprint packet/evaluation contracts, Recon packet contracts, compiled-stage-graph exports, run-trace graph artifacts, and `stage_metadata.py` as the typed registry for shipped stage plane membership, legal terminal results, runner prompt markers, and result-class policy. |
| `millrace_ai/compiler.py` | `src/millrace_ai/compiler.py`, `src/millrace_ai/compilation/` | `millrace_ai.compiler` remains the public facade; compiler outcomes, workspace compile orchestration, graph preview/export, mode/path resolution, graph and node materialization, policy compilation, workflow primitive resolution/validation, scheduler lane validation, execution-capability grant resolution, asset resolution, fingerprints, persistence, and currentness inspection live in `compilation/`. |
| `millrace_ai/entrypoints.py` | `src/millrace_ai/assets/entrypoints/__init__.py`, `models.py`, `discovery.py`, `parsing.py`, `advisory.py`, `linting.py`, `rendering.py` | Root `entrypoints.py` remains a thin compatibility facade; packaged markdown entrypoint assets live in the same `assets/entrypoints/` directory under `execution/`, `planning/`, and `learning/`. |
| `millrace_ai/modes.py` | `src/millrace_ai/assets/modes.py` | Root `modes.py` remains a thin compatibility facade. |
| `millrace_ai/stage_kinds.py` | `src/millrace_ai/assets/architecture.py`, `src/millrace_ai/architecture/stage_kinds.py` | Root `stage_kinds.py` is the thin public facade for stage-kind registry loading. |
| `millrace_ai/loop_graphs.py` | `src/millrace_ai/assets/loop_graphs.py`, `src/millrace_ai/architecture/loop_graphs.py` | Root `loop_graphs.py` is the thin public facade for graph-loop loading. |
| `millrace_ai/runner.py` | `src/millrace_ai/runners/requests.py`, `src/millrace_ai/runners/normalization.py`, `src/millrace_ai/runners/adapters/codex_cli.py`, `codex_cli_command.py`, `codex_cli_artifacts.py`, `codex_cli_tokens.py` | Root `runner.py` remains a thin compatibility facade over the `runners` package; Codex adapter command construction, artifact handling, and token extraction have focused modules behind the public adapter class. |
| `millrace_ai/run_inspection.py` | `src/millrace_ai/runtime/inspection.py` | Root `run_inspection.py` remains a thin compatibility facade. |
| `millrace_ai/paths.py` | `src/millrace_ai/workspace/paths.py`, `src/millrace_ai/workspace/initialization.py` | Root `paths.py` remains a thin compatibility facade for `WorkspacePaths`, `workspace_paths`, and workspace initialization helpers. |
| workspace initialization/baseline/schema epoch | `src/millrace_ai/workspace/initialization.py`, `src/millrace_ai/workspace/bootstrap_files.py`, `src/millrace_ai/workspace/asset_deployment.py`, `src/millrace_ai/workspace/baseline.py`, `schema_epoch.py`, `schema_epoch_marker.py` | Explicit `millrace init`, default runtime file payloads, runtime asset deployment, managed baseline upgrade classification, schema epoch markers, and archive/reset helpers live in workspace-owned modules with path modeling kept separate from bootstrap behavior. |
| workspace idea source artifacts | `src/millrace_ai/workspace/idea_sources.py` | Runtime-owned durable source markdown for watcher-seeded idea specs lives under `millrace-agents/intake/ideas/`, separate from transient operator inbox files. |
| `millrace_ai/runtime_lock.py` | `src/millrace_ai/workspace/runtime_lock.py` | Root `runtime_lock.py` remains a thin compatibility facade. |
| `millrace_ai/mailbox.py` | `src/millrace_ai/workspace/mailbox.py` | Root `mailbox.py` remains a thin compatibility facade. |
| `millrace_ai/events.py` | `src/millrace_ai/workspace/events.py` | Root `events.py` remains a thin compatibility facade. |
| `millrace_ai/work_documents.py` | `src/millrace_ai/workspace/work_documents.py` | Root `work_documents.py` remains a thin compatibility facade. |
| `millrace_ai/recon_packets.py` | `src/millrace_ai/recon_packets.py` | Recon packet markdown parsing/rendering is a root helper because runtime transitions and stage tests share the same artifact contract. |
| `millrace_ai/queue_store.py` | `src/millrace_ai/workspace/queue_store.py`, `queue_claims.py`, `queue_selection.py`, `queue_transitions.py`, `queue_lifecycle.py`, `queue_reconciliation.py`, `task_lifecycle_integrity.py`, `work_item_adapters.py`, `blueprint_state.py` | Root `queue_store.py` remains a thin compatibility facade over the workspace queue package. Queue claim policy, compiled lifecycle mutation interpretation, family-aware work-item adapters, and Blueprint artifact state have explicit workspace-owned homes. |
| `millrace_ai/state_store.py` | `src/millrace_ai/workspace/state_store.py`, `state_reconciliation.py` | Root `state_store.py` remains a thin compatibility facade over the workspace state package. |

## Compiled Architecture And Workflow Primitive Authority

The configurable runtime authority now has dedicated packages and asset
families:

- `src/millrace_ai/architecture/stage_kinds.py` defines typed stage-kind contracts
- `src/millrace_ai/architecture/loop_graphs.py` defines typed graph-loop contracts
- `src/millrace_ai/architecture/materialization.py` defines the graph-plan materialization contracts, including normalized compiled entry/transition indexes, runtime-authority flags, and legacy-equivalence compatibility reporting
- `src/millrace_ai/architecture/workflow_primitives.py` defines work-item
  family, document adapter, claim policy, terminal action, lifecycle mutation,
  runtime effect, recovery policy, failure policy, and schema epoch contracts
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

- `src/millrace_ai/doctor.py`
- `src/millrace_ai/router.py`
- `src/millrace_ai/watchers.py`
- `src/millrace_ai/errors.py`

Additional thin compatibility or public API facades also exist at the root:

- `src/millrace_ai/compiler.py`
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
- `workspace/queue_lifecycle.py` applies compiled lifecycle mutation plans to
  source queue documents.
- `workspace/blueprint_state.py` owns durable Blueprint manifest, draft,
  packet, critique, evaluation, and promotion state helpers.
- `workspace/schema_epoch.py` and `workspace/schema_epoch_marker.py` own schema
  epoch markers and daemon-safe archive/reset helpers.
- `workspace/initialization.py` orchestrates initialization and keeps
  `bootstrap_workspace` as the compatibility alias used by older callers.
- `cli/errors.py` owns operator error output.
- `cli/status_view.py`, `cli/runs_view.py`, `cli/config_view.py`, and
  `cli/compile_view.py` own command-specific view assembly that reads workspace
  state.
- `cli/formatting.py` is limited to rendering already-collected run/control
  values and small shared value formatting.
- Runtime submodules import concrete sibling modules directly, and
  `runtime/outcomes.py` holds `RuntimeTickOutcome` so tick/request helpers do
  not depend back on `runtime/engine.py`; the public `millrace_ai.runtime`
  package facade remains the stable `RuntimeEngine` / `RuntimeTickOutcome`
  import surface.
- `runtime/supervisor.py`, `runtime/lanes.py`, `runtime/lane_conflicts.py`,
  and `runtime/active_runs.py` own daemon lane dispatch, durable lane state,
  conflict policy checks, and active-run projection.
- `runtime/compiled_plans.py` preserves active-run launch-plan authority when
  config reload compiles a newer pending plan.
- `runtime/request_context.py` owns deterministic per-request context bundles
  and rendered prompt-context artifacts.
- `runtime/effects.py`, `runtime/effect_execution.py`, and
  `runtime/lifecycle_interpreter.py` own generic compiled runtime-effect and
  source-lifecycle application.
- `runtime/blueprint_effects.py` owns Blueprint-specific runtime effects while
  using the same compiled effect-selection path.
- `runtime/failure_policy.py` keeps runtime failure classification aligned with
  compiled failure-policy identifiers.
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
