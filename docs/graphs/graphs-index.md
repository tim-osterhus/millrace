# Millrace Shipped Graphs Index

This directory documents the graph-loop configurations that ship with
Millrace. These docs describe topology and runtime routing shape; runner
bindings such as Codex versus Pi are mode-level choices and do not change the
graph edges.

Graph-loop assets live under `src/millrace_ai/assets/graphs/`. Mode assets live
under `src/millrace_ai/assets/modes/` and compose those plane graphs into full
runtime configurations.

Graph topology is runtime authority only after it is selected by mode metadata
and frozen into the compiled plan. The pure graph-authority contract treats
missing compiled graph, scheduler, recovery, lifecycle, runtime-effect,
queue-family, extension, or artifact-contract metadata as an error rather than
falling back to shipped defaults.

## Plane Graphs

- `execution-standard.md`: default Execution plane for task implementation,
  verification, repair, update, troubleshooting, and planning escalation.
- `execution-with-integrator.md`: high-assurance Execution plane that inserts
  Integrator between Builder and Checker.
- `planning-standard.md`: default Planning plane for Recon, Planner, Manager,
  Auditor, Mechanic recovery, and Arbiter closure.
- `planning-blueprint.md`: Blueprint Planning plane for strict draft-packet
  decomposition before generated tasks enter Execution.
- `learning-standard.md`: Learning plane for Analyst, Professor, Curator, and
  targeted Librarian work.

## Shipped Full Configurations

| Mode | Runner Family | Plane Graphs | Summary |
| --- | --- | --- | --- |
| `default_codex` | Codex | `execution.standard`, `planning.standard` | Baseline Codex-backed runtime. It runs standard Execution and Planning without Learning or Integrator. |
| `default_pi` | Pi | `execution.standard`, `planning.standard` | Pi-backed equivalent of `default_codex`. The graph topology is identical; only the stage runner bindings change. |
| `learning_codex` | Codex | `execution.standard`, `planning.standard`, `learning.standard` | Adds the Learning plane to the Codex baseline. Learning may run concurrently with one foreground Execution or Planning stage and receives Analyst/Librarian trigger work. |
| `efficient_learning_mixed` | Mixed Codex/Pi | `execution.standard`, `planning.standard`, `learning.standard` | Uses the same topology as `learning_codex`, keeps Integrator off by default, and ships a mode-local alias profile that assigns Codex stages and Pi/DeepSeek stages explicitly. |
| `learning_pi` | Pi | `execution.standard`, `planning.standard`, `learning.standard` | Pi-backed equivalent of `learning_codex`. The selected plane graphs and learning trigger rules are identical; the mode binds stages to `pi_rpc`. |
| `default_codex_integrated` | Codex | `execution.with_integrator`, `planning.standard` | Uses the high-assurance Execution graph with Integrator after Builder. Planning remains standard and Learning is not selected. |
| `learning_codex_integrated` | Codex | `execution.with_integrator`, `planning.standard`, `learning.standard` | Combines Integrator-backed Execution with the standard Learning plane. Learning trigger rules and concurrency policy match other learning-enabled modes. |
| `blueprint_codex` | Codex | `execution.standard`, `planning.blueprint` | Uses standard Execution plus Blueprint Planning. Planner output is decomposed into Blueprint drafts and approved generated tasks before Execution claims them. |
| `blueprint_learning_codex` | Codex | `execution.standard`, `planning.blueprint`, `learning.standard` | Adds Learning to the Blueprint mode. Planner completion still triggers Librarian while Blueprint Planning continues toward Manager Blueprint and draft-packet work. |

Compatibility alias:

- `standard_plain -> default_codex`
- `standard_millrace -> default_pi`
- `learning_enabled_millrace -> learning_pi`

## Runner-Level Differences

Codex and Pi modes use the same graph-loop topology when they select the same
plane graph ids. The mode-level runner binding changes which adapter executes
the stage and how runner-neutral settings such as thinking level are translated.
It does not add, remove, or reroute graph nodes.

The current Pi shipped modes cover the standard and learning standard
topologies. Integrated and Blueprint modes are currently Codex-backed shipped
modes.

## Learning Concurrency And Triggers

Learning-enabled modes add this scheduler policy:

- Execution and Planning are mutually exclusive foreground planes.
- Learning may run concurrently with Execution.
- Learning may run concurrently with Planning.

They also ship these learning triggers:

- `DOUBLECHECK_PASS` from `execution.doublechecker` requests Analyst
  improvement work.
- `TROUBLESHOOT_COMPLETE` or `BLOCKED` from `execution.troubleshooter` requests
  Analyst improvement work.
- `CONSULT_COMPLETE`, `NEEDS_PLANNING`, or `BLOCKED` from
  `execution.consultant` requests Analyst improvement work.
- `PLANNER_COMPLETE` from `planning.planner` requests targeted Librarian
  optional-skill preparation.

## Discovery-Only Fixture Configurations

Millrace ships discovery-only fixture mode assets that are discoverable through
asset discovery but are **not** shipped product modes. They are not listed in
`SHIPPED_MODE_IDS` and do not appear in the shipped configurations table above.

| Fixture | Runner Family | Plane Graphs | Summary |
| --- | --- | --- | --- |
| `minimal_three_plane` | Pi | `execution.minimal_three_plane`, `planning.minimal_three_plane`, `learning.minimal_three_plane` | Three-plane architecture proof fixture using one custom stage kind per plane (`basic_worker`/`basic_planner`/`basic_learner` bound to canonical `runtime_stage` values `builder`/`planner`/`analyst`) and only generic lifecycle terminal actions. Contains no domain-specific workflow identifiers (no Recon, Blueprint, closure, Arbiter, Manager, Mechanic, planner disposition, candidate evaluation, or learning promotion). Proves custom graph-defined workflows can compile and execute against the current canonical plane infrastructure. Arbitrary plane IDs and arbitrary runtime stages remain deferred future work. |
| `recovery_heavy_millrace` | Pi | `execution.standard`, `planning.standard` | Fixture using the standard two-plane topology and standard extensions with Pi runner bindings. Its mode asset declares `recovery_policy_ids` selecting `src/millrace_ai/assets/registry/recovery_policies/recovery_heavy_policies.json`, whose execution and planning blocked-recovery thresholds are lower than the default policies. Compilation applies those mode-selected recovery-heavy policy thresholds before runtime threshold resolution. |
| `generic_two_plane_fixture` | Pi | `execution.minimal_three_plane`, `planning.minimal_three_plane` | Minimal generic proof fixture using two planes (execution + planning) with `basic_worker` / `basic_planner` stage kinds. The `basic_worker` → `builder` and `basic_planner` → `planner` runtime-stage bindings are a runner-contract and workspace-contract compatibility layer, not arbitrary stage support. Declares only `millrace.generic` and avoids Recon, Blueprint, closure, Arbiter, Manager, Mechanic, planner-disposition, and Learning vocabulary. It is the smallest config the current framework permits. True single-plane mode validation remains intentionally deferred. |

## Related References

- `../runtime/millrace-modes-and-loops.md`
- `../runtime/millrace-compiler-and-frozen-plans.md`
- `../runtime/millrace-compiled-stage-graphs-and-run-traces.md`
- `../runtime/millrace-blueprint-planning.md`
- `config-mapping.md`
