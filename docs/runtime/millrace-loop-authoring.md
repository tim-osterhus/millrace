# Millrace Loop Authoring

This document is for maintainers extending or changing Millrace loop, stage, or
mode assets.

Use it when you need to change:

- legacy loop JSON under `src/millrace_ai/assets/loops/`
- graph-loop JSON under `src/millrace_ai/assets/graphs/`
- stage-kind JSON under `src/millrace_ai/assets/registry/stage_kinds/`
- workflow primitive JSON under `src/millrace_ai/assets/registry/`
- mode JSON under `src/millrace_ai/assets/modes/`
- stage entrypoint selection behavior
- per-stage model or runner bindings that should be frozen by compile

## Start From The Actual Contract

Do not author loops from memory or from prompt prose.

The authoritative sources are:

- `src/millrace_ai/contracts/`
- `src/millrace_ai/architecture/stage_kinds.py`
- `src/millrace_ai/architecture/loop_graphs.py`
- `src/millrace_ai/architecture/workflow_primitives.py`
- `src/millrace_ai/architecture/materialization.py`
- `src/millrace_ai/compiler.py`
- `src/millrace_ai/assets/modes.py`
- `src/millrace_ai/assets/loops/execution/default.json`
- `src/millrace_ai/assets/loops/execution/with_integrator.json`
- `src/millrace_ai/assets/loops/planning/default.json`
- `src/millrace_ai/assets/loops/learning/default.json`
- `src/millrace_ai/assets/graphs/execution/standard.json`
- `src/millrace_ai/assets/graphs/execution/with_integrator.json`
- `src/millrace_ai/assets/graphs/planning/standard.json`
- `src/millrace_ai/assets/graphs/planning/blueprint.json`
- `src/millrace_ai/assets/graphs/learning/standard.json`
- `src/millrace_ai/assets/registry/stage_kinds/`
- `src/millrace_ai/assets/registry/work_item_families/`
- `src/millrace_ai/assets/registry/document_adapters/`
- `src/millrace_ai/assets/registry/queue_claim_policies/`
- `src/millrace_ai/assets/registry/terminal_actions/`
- `src/millrace_ai/assets/registry/lifecycle_mutation_plans/`
- `src/millrace_ai/assets/registry/runtime_effect_handlers/`
- `src/millrace_ai/assets/registry/recovery_policies/`
- `src/millrace_ai/assets/registry/runtime_failure_policies/`
- `src/millrace_ai/assets/registry/workspace_schema_epochs/`
- `src/millrace_ai/assets/modes/default_codex.json`
- `src/millrace_ai/assets/modes/default_pi.json`
- `src/millrace_ai/assets/modes/learning_codex.json`
- `src/millrace_ai/assets/modes/learning_pi.json`
- `src/millrace_ai/assets/modes/default_codex_integrated.json`
- `src/millrace_ai/assets/modes/learning_codex_integrated.json`
- `src/millrace_ai/assets/modes/blueprint_codex.json`
- `src/millrace_ai/assets/modes/blueprint_learning_codex.json`

Loop and mode docs should describe those contracts, not override them.
For the architecture decision behind workflow primitive authority, read
`docs/adr/0010-compiler-validated-workflow-primitives-as-runtime-authority.md`.

## Shipped Authoring Surfaces

Millrace currently ships three authoring layers that must not drift apart:

1. legacy loop assets under `src/millrace_ai/assets/loops/`
2. graph-loop and stage-kind assets under
   `src/millrace_ai/assets/graphs/` and
   `src/millrace_ai/assets/registry/stage_kinds/`
3. workflow primitive assets under the registry folders listed above

Today the runtime executes request binding and control flow from
`compiled_plan.json`, which is built from graph loops, stage kinds, and
workflow primitives.

The graph-loop path exists to:

- prove the shipped topology can be represented as typed node graphs over stage kinds
- emit `compiled_plan.json` during compile
- drive runtime request binding, intake, recovery, closure-target activation,
  learning-trigger activation, and post-stage routing
- support preview materialization of discovered graph loops without modifying
  the shipped runtime plan contract

For shipped defaults, maintainers should keep all three surfaces aligned even
though the graph surface is the canonical topology source of truth.
Workflow primitive assets are also canonical runtime authority once compiled:
they define queue families, document adapters, claim policy, terminal actions,
lifecycle mutation, runtime effects, and schema epoch compatibility.

## Legacy Loop JSON Rules

A legacy loop must validate as `LoopConfigDefinition` from the
`millrace_ai.contracts` facade.

That means:

- `entry_stage` must appear in `stages`
- `stages` must be unique
- every stage in `stages` must belong to the loop plane
- every edge source must appear in `stages`
- every edge terminal must be legal for that `source_stage`
- every non-terminal edge must have `target_stage`
- every terminal edge must have `terminal_result`
- at least one edge path must terminate into one of the loop `terminal_results`

For `LoopEdgeDefinition`, exactly one of `target_stage` or `terminal_result`
must be set.

If `edge_kind = terminal`, the edge must terminate. If the edge is not terminal,
it must point at another stage.

## Mode JSON Rules

A mode must validate as `ModeDefinition` from the `millrace_ai.contracts`
facade.

Today the important authoring rule is scope:

- `loop_ids_by_plane`
- `stage_entrypoint_overrides`
- `stage_skill_additions`
- `stage_model_bindings`
- `stage_runner_bindings`
- `stage_thinking_bindings`
- `concurrency_policy`
- `learning_trigger_rules`

may only reference planes, loops, or stages that exist in the selected plane
loops.

The compiler enforces that by building the set of selected stages first and then
rejecting mode maps that refer outside that set.

Learning-trigger authoring has one additional safety rule: a rule that targets
`curator` directly must include `target_skill_id` or
`preferred_output_paths`. Vague learning evidence should target `analyst`
instead so the learning plane can research, no-op, or escalate without asking
Curator to infer a mutation destination.
Planner-to-Librarian trigger rules are allowed in learning-enabled modes
because Librarian installs bounded remote optional skills into the workspace
from the supported index; it does not edit source-packaged skills or promote
workspace assets.

Legacy `execution_loop_id` and `planning_loop_id` fields are still accepted for
compatibility, but new mode assets should use `loop_ids_by_plane`.

## Stage-Kind And Graph-Loop Rules

A stage-kind asset must validate as `RegisteredStageKindDefinition`.

For shipped defaults, that means at minimum:

- plane membership is declared there, not inferred from prose
- legal outcomes must cover the outcomes used by any graph edges that leave the node
- `allowed_work_item_families` declares the work-item families that nodes of
  that stage kind may own
- default entrypoint and required stage-core skills must remain real packaged assets

A graph-loop asset must validate as `GraphLoopDefinition`.

That means:

- every node references a declared stage kind
- every edge references a valid source node and a valid target node or terminal state
- every edge outcome is legal for the source node's stage kind
- planning intake can be modeled through multiple `entry_nodes`
- learning intake is modeled through `learning_request`
- completion behavior may target only a closure-role stage kind

Blueprint graph authoring has two additional invariants:

- custom Blueprint stage kinds must keep their runtime role narrow. Manager
  emits manifests/drafts, Contractor emits candidate packets, Evaluator emits
  evaluations plus either critiques or generated tasks, and no Blueprint stage
  directly mutates queues.
- every Blueprint terminal outcome that creates durable work must have a
  matching runtime effect rule and lifecycle mutation plan. The compiler can
  validate cross-references, and the compiled runtime effect operation plus
  lifecycle mutation plan own destination-before-source ordering.

## Workflow Primitive Rules

Workflow primitive assets must validate against the primitive definition models
in
`src/millrace_ai/architecture/workflow_primitives.py`.

For the shipped foundation slice, primitives define:

- work-item families for task, probe, spec, incident, and learning request
- document adapters that map those families to the built-in markdown document
  contracts
- plane queue claim policies that decide which families a plane may claim and
  in what order
- terminal actions and lifecycle mutation plans that explain how terminal
  outcomes become source lifecycle intents
- runtime effect operations, stores, validators, effect rules, and temporary
  legacy handler aliases that let terminal results request additional
  runtime-owned effects without mutating queues directly from stage code
- recovery and failure policies used by compiler validation and future runtime
  interpretation
- the active workspace schema epoch

Runtime-effect `route_to_node` recovery must satisfy the generic repair-closure
contract, regardless of loop domain:

- source operations declare `repair_closure_contracts` keyed by failure class
- each contract declares `repair_operation_id`, `target_node_id`,
  `target_terminal_outcome`, required repair evidence artifacts, affected source
  family, lifecycle behavior, and partial/resume guard flags
- policies that can resolve more than one operation or failure class must
  declare explicit `repair_closure_mappings`; otherwise compile fails as
  ambiguous
- `applies_to_families` must exactly match the closure-affected source families
- the target node/outcome must actually invoke the declared repair operation
  through a runtime effect rule
- every required repair evidence artifact must exist, be emitted by the target
  stage kind, and be listed in that repair effect rule's `required_run_artifacts`
- policies that can match `partial_mutation` require closure contracts with
  `supports_partial_mutation=true`
- contracts that require resume guards must target outcomes with a compiled
  resume policy that carries `resume_stage` metadata and disallows self-routing

The compiler validates primitive cross-references before any runtime start. A
mode or graph is invalid if an entry, stage kind, terminal action, lifecycle
plan, queue policy, runtime effect rule, or schema epoch reference cannot be
resolved coherently.

## Entrypoint Override Rules

Entrypoint overrides are intentionally narrow.

A valid override must be:

- relative
- under `entrypoints/`
- a markdown file path ending in `.md`
- free of parent-directory escapes

The compiler rejects absolute paths, parent traversal, empty strings, and paths
outside the entrypoint asset tree.

## Stage Bindings And Recompile Behavior

Authoring decisions that change the compiled plan contract require recompile.

That includes:

- changing `runtime.default_mode`
- changing stage-level `runner`
- changing stage-level `model`
- changing stage-level `thinking_level` or legacy `model_reasoning_effort`
- changing stage-level `timeout_seconds`
- changing legacy loop stage topology
- changing graph-loop topology
- changing graph-loop node `thinking_level`
- changing stage-kind contracts used by graph loops
- changing mode stage maps

The runtime may apply some other config changes on the next tick, but anything
that changes the compiled plan should be treated as a compile concern.

## Runtime-Owned Vs Advisory Content

This distinction is the main authoring guardrail.

Runtime-owned behavior includes:

- queue state transitions
- stage routing
- retry thresholds
- recovery escalation
- terminal result semantics
- persisted runtime status

Advisory content includes:

- stage instructions in entrypoint markdown
- stage-core skill posture
- optional skill guidance
- external docs that explain how to operate or extend Millrace

Do not move runtime-owned behavior into docs, prompt prose, or skill text just
because it feels easier to describe there.

## Authoring Workflow

When you change loops or modes:

1. update the asset JSON first
2. run `millrace compile validate`
3. run `millrace compile show`
4. check that the compiled plan reflects the intended entrypoints, skills,
   runner names, model names, and loop ids
5. inspect `compiled_plan.json` when the change also touches stage kinds or
   graph loops
6. update docs that describe the changed contract

If the new structure changes what operators or stage agents need to know, update
the relevant runtime docs and external agent docs in the same slice.

## Tests To Touch

At minimum, expect to review and possibly update:

- `tests/assets/test_modes.py`
- `tests/assets/test_stage_kinds.py`
- `tests/assets/test_loop_graphs.py`
- `tests/integration/test_compiler.py`
- `tests/assets/test_entrypoints.py`
- runtime docs that describe mode and loop behavior

If you changed entrypoint assets or advisory skill surfaces, also inspect:

- `tests/assets/test_packaging_runtime_assets.py`
- `tests/runners/test_runner.py`
- `tests/runners/test_runners_codex_adapter.py`

## What Good Authoring Looks Like

Good loop and mode authoring is:

- concrete
- compiler-valid
- explicit about `terminal_results`
- explicit about stage topology
- explicit about whether you changed the legacy loop inspection surface,
  the runtime-authoritative graph/stage-kind surface, or both
- explicit about what is runtime-owned and what is advisory

Bad authoring:

- invents new stage names without adding the matching stage-kind and graph
  contract support
- invents new terminal meanings in doc prose only
- uses `stage_entrypoint_overrides` as a generic escape hatch without updating
  the surrounding documentation and tests
- blurs runtime-owned routing with agent-authored reasoning

If a loop or mode change cannot be explained cleanly in terms of contracts,
compiled-plan freezing, and runtime-owned boundaries, it is probably not ready to
ship.
