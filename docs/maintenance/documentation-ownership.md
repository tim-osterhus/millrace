# Documentation Ownership

This page names the canonical owner document for each major Millrace
architecture topic. Related docs may summarize or link to the topic, but the
owner document is the place maintainers should update first when behavior or
source boundaries change.

## Canonical Owners

| Topic | Canonical owner | Related references |
| --- | --- | --- |
| Runtime architecture and mutation authority | `docs/runtime/millrace-runtime-architecture.md` | `docs/runtime/README.md`, `docs/millrace-technical-overview.md`, planned `docs/runtime/millrace-runtime-authority-map.md` |
| Compiler and frozen plans | `docs/runtime/millrace-compiler-and-frozen-plans.md` | `docs/runtime/millrace-loop-authoring.md`, `docs/source-package-map.md`, `docs/adr/0010-compiler-validated-workflow-primitives-as-runtime-authority.md` |
| Graph and loop authoring | `docs/runtime/millrace-loop-authoring.md` | `docs/graphs/graphs-index.md`, `docs/skills/millrace-loop-authoring/SKILL.md`, `docs/runtime/millrace-modes-and-loops.md` |
| Shipped graph configurations | `docs/graphs/graphs-index.md` | `docs/graphs/execution-lad.md`, `docs/graphs/execution-lad-integrator.md`, `docs/graphs/planning-lad.md`, `docs/graphs/planning-blueprint.md`, `docs/graphs/learning-standard.md` |
| Workspace baselines and upgrades | `docs/runtime/millrace-workspace-baselines-and-upgrades.md` | `docs/runtime/millrace-runtime-architecture.md`, `docs/skills/millrace-ops-agent-manual/SKILL.md` |
| Usage governance | `docs/runtime/millrace-usage-governance.md` | `docs/runtime/millrace-cli-reference.md`, `docs/skills/millrace-ops-agent-manual/SKILL.md` |
| Execution capabilities and approvals | `docs/runtime/millrace-execution-capabilities.md` | `docs/runtime/millrace-cli-reference.md`, `docs/runtime/millrace-runtime-error-codes.md`, `docs/skills/millrace-ops-agent-manual/SKILL.md` |
| Runner architecture and request contracts | `docs/runtime/millrace-runner-architecture.md` | `docs/runtime/millrace-compiled-stage-graphs-and-run-traces.md`, planned `docs/maintenance/request-context-contracts.md` |
| Blueprint planning behavior | `docs/runtime/millrace-blueprint-planning.md` | `docs/graphs/planning-blueprint.md`, planned `docs/maintenance/blueprint-effect-behavior-matrix.md` |
| Arbiter and completion behavior | `docs/runtime/millrace-arbiter-and-completion-behavior.md` | `docs/runtime/millrace-runtime-architecture.md`, `docs/runtime/millrace-modes-and-loops.md` |
| Runtime error codes and recovery classes | `docs/runtime/millrace-runtime-error-codes.md` | planned `docs/maintenance/recovery-status-doctor-runner-contracts.md`, `docs/skills/millrace-ops-agent-manual/SKILL.md` |
| CLI command surface | `docs/runtime/millrace-cli-reference.md` | `docs/skills/millrace-ops-agent-manual/SKILL.md` |
| Operator manual | `docs/skills/millrace-ops-agent-manual/SKILL.md` | `docs/runtime/millrace-cli-reference.md`, `docs/runtime/millrace-runtime-error-codes.md` |
| Source package map | `docs/source-package-map.md` | `docs/maintenance/codebase-stewardship.md`, `docs/maintenance/refactor-candidate-register.md` |
| Release verification | `docs/adr/0004-release-verification-contract.md` | `docs/source-package-map.md`, `docs/runtime/README.md`, planned `docs/maintenance/release-readiness-maintainability-refactor.md` |

## Duplicated Or Stale Sections To Reconcile

These sections are useful today but should be shortened, linked, or rewritten
as the maintainability packets land:

- `docs/runtime/README.md` repeats the verification command block that is owned
  by `docs/adr/0004-release-verification-contract.md` and summarized in
  `docs/source-package-map.md`. Keep the runtime index concise and link to the
  release-verification owner when Batch 6 updates release notes.
- `docs/millrace-technical-overview.md` is intentionally broad. When source
  boundaries move, update it as a high-level map and put detailed module
  ownership in `docs/source-package-map.md`.
- `docs/runtime/millrace-runtime-architecture.md` currently carries much of the
  mutation-authority explanation. The planned authority map should become the
  trace-oriented owner, while the architecture page remains the conceptual
  owner.
- `docs/runtime/millrace-blueprint-planning.md` describes handler-centric
  Blueprint runtime effects. Batch 4 should either update that language to
  operation-centric declarative effects or explicitly mark remaining legacy
  handler behavior.
- `docs/graphs/planning-blueprint.md` should remain a graph-topology reference.
  Durable Blueprint state and runtime effects belong in
  `docs/runtime/millrace-blueprint-planning.md` and the planned behavior
  matrix.
- `docs/skills/millrace-ops-agent-manual/SKILL.md` should link to canonical
  runtime docs for deep mechanics instead of duplicating architecture detail in
  operator instructions.

## Update Rule

When a change affects a major topic, update the canonical owner first, then
refresh related indexes and skill docs only where the reader needs a pointer or
operator-facing consequence.
