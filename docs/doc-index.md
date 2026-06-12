# Millrace Documentation Index

This is the top-level map for Millrace documentation. Use it when you need to
choose the right reference before drilling into runtime, agent, maintainer, or
architecture material.

## Start Here

- `../README.md`: public landing page, install path, quick proof, and journey
  links.
- `millrace-technical-overview.md`: high-density implementation-oriented system
  map.
- `runtime/README.md`: index for runtime behavior, CLI, compiler, modes,
  runners, governance, and workspace lifecycle references.
- `graphs/graphs-index.md`: shipped mode-to-plane graph configurations and
  per-plane graph references.

## Runtime References

Use `runtime/` when you are already working with Millrace runtime behavior or
workspace operation.

- `runtime/millrace-runtime-architecture.md`: workspace ownership model,
  artifact model, module topology, and tick lifecycle.
- `runtime/millrace-runtime-authority-map.md`: trace-by-trace mutation
  authority for intake, queue selection, runner requests, artifacts, result
  normalization, and durable runtime state.
- `runtime/millrace-cli-reference.md`: current CLI command surface, aliases,
  and operator-facing command groups.
- `runtime/millrace-compiler-and-frozen-plans.md`: mode resolution, compiled
  plan freezing, workflow primitive authority, scheduler policy, workspace
  schema epoch checks, compile fingerprints, stale-plan behavior, and the
  breaking pure graph-authority contract that treats missing compiled policy as
  an error.
- `runtime/millrace-modes-and-loops.md`: shipped mode ids, loop ids, stage
  topology, integrated quality loops, Learning, Librarian, and concurrency
  policy.
- `runtime/millrace-blueprint-planning.md`: opt-in Blueprint Planning loop
  behavior and artifacts.
- `runtime/millrace-arbiter-and-completion-behavior.md`: closure-target
  lineage, Arbiter artifacts, backlog-drain behavior, and closure safety.
- `runtime/millrace-workspace-baselines-and-upgrades.md`: explicit workspace
  initialization, managed baseline refresh, schema epoch behavior, and upgrade
  classifications.
- `runtime/millrace-usage-governance.md`: opt-in runtime-owned usage accounting
  and automatic pause/resume behavior.
- `runtime/millrace-execution-capabilities.md`: compiled execution capability
  grants, approval gates, support decisions, and inspection output.
- `runtime/millrace-compiled-stage-graphs-and-run-traces.md`: compiled topology
  exports and per-run trace artifacts.
- `runtime/millrace-runner-architecture.md`: runner dispatch, adapter
  contracts, request-context artifacts, and Codex/Pi adapter behavior.
- `runtime/millrace-entrypoint-mapping.md`: packaged-source to deployed
  workspace entrypoint mapping and skill-only advisory expectations.
- `runtime/millrace-loop-authoring.md`: maintainer rules for loop, mode, and
  stage-selection assets.
- `runtime/millrace-runtime-error-codes.md`: runtime-owned post-stage failure
  codes and failure-origin diagnostics.
- `runtime/millrace-runtime-lifecycle-diagram.md`: standalone lifecycle chart.

## Graph References

Use `graphs/` when you need the shipped graph topology rather than the broader
runtime behavior around it.

- `graphs/graphs-index.md`: full shipped mode configurations and runner-family
  differences.
- `graphs/config-mapping.md`: conceptual config aliases, fixture mappings, and
  product-mode versus fixture distinctions.
- `graphs/execution-standard.md`: default Execution graph.
- `graphs/execution-with-integrator.md`: high-assurance Execution graph with
  Integrator.
- `graphs/planning-standard.md`: default Planning graph.
- `graphs/planning-blueprint.md`: Blueprint Planning graph.
- `graphs/learning-standard.md`: standard Learning graph.

## Agent And Skill Docs

Use `skills/` when an external agent needs instructions for deciding whether to
delegate work to Millrace, operate a workspace, or author loop/stage changes.

- `skills/README.md`: public agent-skill docs index.
- `skills/millrace-autonomous-delegation/SKILL.md`: decision layer for trusted
  sessions that may choose between direct work and Millrace.
- `skills/millrace-ops-agent-manual/SKILL.md`: operator runbook for deploying,
  configuring, monitoring, and intervening in Millrace workspaces.
- `skills/millrace-loop-authoring/SKILL.md`: guidance for extending Millrace
  loop and stage assets.

These docs are public repo guidance. Runtime-shipped stage skills live under
`src/millrace_ai/assets/skills/`.

## Maintainer And Architecture Docs

- `source-package-map.md`: package ownership map and compatibility facade
  boundaries after the follow-up refactor wave.
- `maintenance/codebase-stewardship.md`: maintainer-facing map for
  documentation ownership, maintainability gates, refactor candidates, and
  characterization work.
- `maintenance/documentation-ownership.md`: canonical owner document for each
  major architecture topic and duplicated sections to reconcile.
- `maintenance/documentation-freshness-matrix.md`: source areas mapped to docs
  that should be checked when those areas change.
- `maintenance/refactor-candidate-register.md`: candidate ids, reasons to
  change, risk, tests, and extraction strategy for targeted refactor work.
- `maintenance/public-api-compatibility-inventory.md`: frozen import and symbol
  surfaces, plus final compatibility-facade status for the follow-up refactor
  and retained pure graph-authority shims.
- `maintenance/blueprint-effect-behavior-matrix.md`: current Blueprint
  runtime-effect behavior and parity gaps before declarative migration.
- `maintenance/compiler-validation-contracts.md`: validator-family contracts
  and diagnostic stability notes before compiler validation decomposition.
- `maintenance/workflow-primitive-contract-family-inventory.md`: workflow
  primitive contract-family inventory before package decomposition.
- `maintenance/request-context-contracts.md`: generic and Blueprint-specific
  request-context contracts before runtime request-context decomposition.
- `maintenance/recovery-status-doctor-runner-contracts.md`: recovery, status,
  Doctor, and runner-normalization contracts before Batch 3 and Batch 5 splits.
- `adr/README.md`: accepted architecture-decision records.
- `adr/0001-adopt-src-layout-and-domain-packages.md`
- `adr/0002-runtime-engine-decomposition.md`
- `adr/0003-error-taxonomy-and-public-boundaries.md`
- `adr/0004-release-verification-contract.md`
- `adr/0005-compiled-graph-plan-as-runtime-authority.md`
- `adr/0006-explicit-workspace-baselines-and-managed-upgrades.md`
- `adr/0007-runtime-internal-authority-packages.md`
- `adr/0008-contract-facade-and-domain-contract-modules.md`
- `adr/0009-stage-metadata-single-source-of-truth.md`
- `adr/0010-compiler-validated-workflow-primitives-as-runtime-authority.md`
- `adr/0011-declarative-runtime-effect-operations.md`
- `adr/0012-core-kernel-boundary.md`
- `adr/0013-generic-stage-and-plane-registry.md`
- `adr/0014-runtime-operation-step-interpreter.md`
- `adr/0015-extension-package-manifests.md`
- `adr/0016-extension-boundary-compatibility-facades.md`

## Optional Sidecar

The local read-only dashboard is shipped separately from the base runtime.

- `../packages/millrace-web/README.md`: `millrace-web` package usage and
  development notes.
