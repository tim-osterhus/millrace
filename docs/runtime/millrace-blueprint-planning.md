# Millrace Blueprint Planning

Blueprint Planning is an opt-in Planning loop selected by `blueprint_codex`
or `blueprint_learning_codex`. It is designed for implementation work where a
normal Planner-to-Manager handoff is too coarse: the Planning plane decomposes
a spec into strict draft packets, critiques one proposed implementation plan at
a time, and promotes only approved packets into Execution tasks.

Blueprint Planning does not move implementation into Planning. Contractor
Blueprint proposes a plan; Builder still performs source edits only after
Evaluator Blueprint approves a generated task.

## Mode And Graph

`blueprint_codex` selects:

- `execution.standard`
- `planning.blueprint`

`blueprint_learning_codex` selects the same Execution and Planning graph plus:

- `learning.standard`

Both Blueprint modes omit the Integrator execution loop. The Blueprint graph
replaces the standard Planner-to-Manager path with:

```text
planner -> manager_blueprint -> contractor_blueprint -> evaluator_blueprint
```

The graph also includes `mechanic_blueprint` as the Planning recovery role for
blocked Blueprint work.

When `blueprint_learning_codex` is active, Learning still triggers after
Planner completes. The Planner-complete trigger enqueues Librarian on the
Learning plane before Planning continues into Manager Blueprint.

The runtime maps Blueprint stage kinds onto concrete Planning stage identities
for runner compatibility while preserving compiled node id and stage-kind id
for routing, request context, run inspection, and effect selection.

## Stage Roles

Planner emits a structured `planner_disposition.json` with each
`PLANNER_COMPLETE` result. `active_source_ready_for_manager` continues the
same active source into Manager Blueprint. `emitted_child_specs` resolves or
completes the active source after validating the child specs exist in the spec
queue, and does not also decompose the source through Manager Blueprint.
`blocked` preserves normal blocked Planning recovery. A missing or mismatched
Planner disposition blocks the source instead of guessing.

`manager_blueprint` receives a Planner-completed spec or Auditor-completed
incident and emits a Blueprint manifest plus ordered draft records. It does not
enqueue execution tasks directly.

`contractor_blueprint` receives exactly one active Blueprint draft and emits a
candidate Blueprint packet. When Evaluator rejects a packet, Contractor
receives the critique packet and emits a revised candidate for the same draft.

`evaluator_blueprint` reviews one candidate packet. It either approves the
packet and emits one generated execution task, or rejects the packet with a
critique that routes the same draft back to Contractor.

`mechanic_blueprint` handles blocked Blueprint Planning work and structured
runtime-effect recovery decisions without taking over normal execution repair
duties.

## Runtime Effects

Blueprint stages do not mutate queue state by editing files directly. They emit
typed terminal outcomes, and the runtime selects compiled runtime effect rules
from `compiled_plan.json`.

The shipped Blueprint outcomes are:

- `MANAGER_BLUEPRINT_COMPLETE`: persist manifest and drafts, queue draft work,
  and complete the source spec or incident
- `BLUEPRINT_CANDIDATE_READY`: persist a candidate packet and route the active
  draft to Evaluator
- `BLUEPRINT_REJECTED`: persist the evaluation and critique, mark the draft for
  revision, and route it back to Contractor
- `BLUEPRINT_APPROVED`: persist the approved packet, evaluation, and promotion
  record, enqueue the generated execution task, and approve the source draft

Effect dispatch is operation-driven. A mode or graph cannot run if a Blueprint
terminal effect rule references an unknown operation id, duplicates another
stage/terminal binding, or binds a legacy handler id that is not declared as an
operation alias. Runtime runner registration is now separate compatibility
plumbing, not the architectural source of Blueprint behavior.

Manager Blueprint runtime-effect failures are policy-routed by class and
mutation phase, and the shipped policy blocks them conservatively for operator
inspection. That includes missing, malformed, schema-invalid, or semantically
mismatched Manager artifacts, duplicate manifest ids, duplicate draft ids,
invalid source lifecycle state, and partial mutations. `runs show`, status
JSON, and monitor events expose the operation id, runner id, legacy handler id,
failure class/message, mutation phase, matched policy id, and recovery action.

Contractor Blueprint candidate persistence checks existing same-id candidate
packet and markdown artifacts before writing. Equivalent normalized packet
payloads and line-ending-normalized markdown are replay-safe. Divergent packet
or markdown collisions block with `blueprint_candidate_duplicate_conflict` or
`blueprint_candidate_markdown_conflict` and do not overwrite existing
artifacts.

Mechanic Blueprint receives the failed runtime-effect run context,
stage-result path, failure class/message, and implicated Blueprint artifacts in
request context. Runtime-effect recovery is structured: a
`MECHANIC_BLUEPRINT_COMPLETE` recovery result must be backed by
`blueprint_repair_decision.json` as a `BlueprintRepairDecisionDocument`;
`mechanic_report.md` alone is evidence, not runtime-owned repair state. The
repair decision JSON uses `next_resume_stage` for evaluator, contractor, or
manager rerun actions, while terminal-result metadata may still use
`resume_stage` for router handoff. Current shipped runtime failure policies
still route the Evaluator generated-task missing/invalid class to Mechanic
Blueprint automatically. The Blueprint Planning graph also declares
`mechanic_blueprint` as its default runtime failure repair node, so
unclassified Planning runtime blockers can be diagnosed there when no more
specific policy blocks first. Manager pre-mutation artifact failures that are
safe to diagnose may route through that default repair path; partial mutations
and explicit conservative policies still block for operator inspection.
Mechanic must not write corrected `blueprint_manifest.json` or
`blueprint_drafts.json`.
`repaired_generated_task.json` is valid only with
`repair_action=apply_repaired_generated_task` and validates as a task document.
`mechanic_blueprint_repair_apply` is the packaged repair-apply operation for
this artifact pair. It consumes the structured decision, Mechanic report, and
repaired generated task through artifact contracts, validates the Mechanic
stage result, failed
runtime-effect stage-result metadata, draft, packet, evaluation, root lineage,
and repaired task identity/scope before mutation, then reuses the
runtime-owned approval promotion path. Missing, invalid, mismatched, or
out-of-scope repair inputs fail before durable mutation. The packaged
`MECHANIC_BLUEPRINT_COMPLETE` effect rule requires `blueprint_repair_decision`,
`mechanic_report`, and `repaired_generated_task`, and requires repair
capabilities for `apply_repaired_generated_task`, `generated_task_missing`, and
`generated_task_invalid`. Compiler validation rejects recoverable
runtime-effect routes to `mechanic_blueprint` unless the selected Blueprint
graph has the closed repair operation, a non-Mechanic resume guard, required
repair artifacts, and operation/compatibility metadata alignment. Unsafe
recovery emits `BLOCKED`.
Blueprint uses the same generic repair-closure contract as non-Blueprint loops:
`evaluator_blueprint_approved_to_task` declares closure metadata in
`repair_closure_contracts`, and the `blueprint_approval_pre_mutation_effect_validation`
policy is validated against those contracts instead of a Blueprint-only
compiler path.

## Durable Artifacts

Blueprint state is runtime-owned workspace state. Operators should use status
and run inspection first, then open raw files only when detailed diagnosis is
needed.

Lineage ids are not storage ids. `root_spec_id` and generic `root_source`
metadata identify the closure lineage used for inventory and Arbiter
readiness; legacy `root_idea_id` is only the idea-rooted compatibility field.
They do not own Blueprint artifact paths. Blueprint manifests are keyed by
`BlueprintManifestDocument.manifest_id` and new manifests are written to
`millrace-agents/blueprints/manifests/<manifest_id>.json`.

Legacy workspaces may still contain root-keyed manifest files such as
`millrace-agents/blueprints/manifests/<root_spec_id>.json`. The runtime reads
those files by their embedded `manifest_id`. Same-root multi-manifest state is
expected during Arbiter remediation when a follow-up Manager Blueprint pass
shares the original `root_spec_id` but emits a distinct `manifest_id`. That is
healthy lineage, not a duplicate. A duplicate is the same `manifest_id` with
divergent normalized content, or a draft id reused with incompatible content.

Blueprint artifacts include:

- manifest records for a source spec or incident
- ordered draft records
- candidate packets
- critique packets
- evaluation records
- promotion records
- generated execution tasks
- structured repair decisions and repaired generated-task artifacts emitted by
  Mechanic Blueprint recovery

Status exposes Blueprint counters and current artifact counts so operators can
see whether drafts, packets, critiques, evaluations, promotions, and generated
tasks are present without manually walking workspace directories.

## Closure Semantics

Blueprint drafts are same-lineage Planning work. While a closure target is
open, the runtime may execute an approved generated task before claiming the
next eligible Blueprint draft.

Arbiter remains suppressed until same-lineage Blueprint work has drained. That
includes queued drafts, active drafts, blocked drafts, candidate packets,
approved-but-unpromoted packets, and generated execution tasks.

This is intentional: closure should evaluate the full lineage state produced by
the source spec or incident, not just the first approved draft.

## Request Context

Blueprint stage requests receive deterministic request-context artifacts. The
context ties each run back to:

- compiled plan id and fingerprint
- graph node id and stage-kind id
- work-item family and source lineage
- active lane and launch-plan authority
- Blueprint manifest/draft/packet/evaluation state relevant to the current
  stage

Evaluator Blueprint resolves the manifest for an active draft by
`draft.manifest_id`, not by `draft.root_spec_id`. This lets legacy root-keyed
manifests and new manifest-id-keyed remediation manifests coexist under the
same closure root while each draft receives the correct manifest context.

Mechanic Blueprint request context includes preferred output refs for
`blueprint_repair_decision.json`, `repaired_generated_task.json`, and
`mechanic_report.md`. These refs tell the stage where to write declared
artifacts; they do not grant direct queue mutation authority.
For Evaluator approval pre-mutation repair, context also includes the failed
stage result, the failed `blueprint_evaluation.json` and `generated_task.md`
refs, runtime-effect policy metadata, and the required
`apply_repaired_generated_task` action. Mechanic writes repair artifacts only;
the runtime remains the owner of queue movement and canonical Blueprint state.

This keeps Blueprint critique and approval decisions inspectable after the run,
even when a later config reload compiles a newer pending plan.

## Idempotent Replay

Manager Blueprint promotion is replay-safe when durable outputs are
model-equivalent. If the manifest and all draft outputs already exist with the
same normalized content and the source spec or incident is still active, the
effect completes or resolves the source normally. If those outputs already
exist and the source is already in the target lifecycle state (`specs/done` or
`incidents/resolved`), the effect returns idempotent no-op success. If the
source is blocked, missing, or otherwise incompatible, the failure class is
`blueprint_source_lifecycle_invalid`. Partial or divergent output state remains
conservative and blocks as `blueprint_partial_mutation` or a duplicate-id
class.

Contractor Blueprint candidate replay is also idempotent only after proving
equivalence. Full replay reports no created paths; partial replay writes only
missing packet or markdown artifacts after every existing artifact is proven
equivalent, then keeps the active draft `latest_blueprint_id` coherent.

Evaluator Blueprint approval replay is idempotent only after proving the
existing evaluation, approved packet, approved markdown, generated task, and
promotion record are equivalent. Existing approved markdown must match the
candidate markdown or the replay run's `blueprint.md`; missing or divergent
approval state blocks with a precise conflict class instead of returning source
completion. Partial replay can reuse an equivalent generated task and write a
missing promotion record.

Only Evaluator approval pre-mutation failures for `generated_task_missing` and
`generated_task_invalid` route to `mechanic_blueprint` under the shipped
runtime failure policy. Other approval replay conflicts and partial mutations
block unless a declared reconciliation handler proves the durable state is
equivalent and safe to continue.

## Operator Inspection

Before opening raw files, use:

```bash
millrace status --workspace <workspace>
millrace runs ls --workspace <workspace>
millrace runs show <run_id> --workspace <workspace>
millrace runs trace <run_id> --workspace <workspace>
millrace compile show --mode blueprint_codex --workspace <workspace>
millrace compile show --mode blueprint_learning_codex --workspace <workspace>
millrace compile graph --mode blueprint_codex --workspace <workspace>
millrace compile graph --mode blueprint_learning_codex --workspace <workspace>
```

Important operator expectations:

- a rejected Blueprint packet is normal loop behavior, not a failed daemon run
- Contractor Blueprint should never be treated as an implementation role
- an approved Blueprint should create an approved packet, evaluation, promotion
  record, and generated execution task
- Arbiter should stay suppressed while same-lineage Blueprint drafts or
  generated tasks remain queued, active, blocked, or unpromoted
- `millrace runs show` should expose runtime-effect operation, runner, legacy
  handler, decision, source-lifecycle, and created-path details for Blueprint
  stage runs
- `millrace status` and `millrace doctor` should surface the latest
  recoverable Evaluator approval repair context, including the structured
  repair contract, replay conflict classes, inert-artifact guard, and runtime
  ownership boundary
- Mechanic Blueprint recovery should emit structured
  `blueprint_repair_decision.json`; `repaired_generated_task.json` is only
  expected with `repair_action=apply_repaired_generated_task`, and the
  repair-apply handler validates failed context and repaired task scope before
  runtime-owned promotion
- same-root remediation manifests are expected when their `manifest_id` values
  differ; diagnose duplicate manifest failures by comparing `manifest_id`, not
  `root_spec_id`
- Contractor candidate replay accepts only equivalent packet and markdown
  artifacts; divergent same-id packet or markdown collisions are blockers, not
  files to overwrite manually
- Evaluator approval replay accepts only equivalent evaluation, approved
  packet, approved markdown, generated task, and promotion artifacts; divergent
  or unverifiable approval collisions are blockers, not files to overwrite
  manually
- old root-keyed manifest files are read by embedded `manifest_id`; do not
  rename or overwrite them by hand while a daemon owns the workspace

## Related Docs

- `docs/graphs/planning-blueprint.md`
- `docs/graphs/graphs-index.md`
- `docs/runtime/millrace-modes-and-loops.md`
- `docs/runtime/millrace-compiler-and-frozen-plans.md`
- `docs/runtime/millrace-loop-authoring.md`
- `docs/runtime/millrace-arbiter-and-completion-behavior.md`
- `docs/adr/0010-compiler-validated-workflow-primitives-as-runtime-authority.md`
