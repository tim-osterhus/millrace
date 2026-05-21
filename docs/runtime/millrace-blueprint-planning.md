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

`mechanic_blueprint` handles blocked Blueprint Planning work without taking
over normal execution repair duties.

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

Effect dispatch is data-driven. A mode or graph cannot run if a Blueprint
terminal effect rule references an unknown handler, duplicates another
stage/terminal binding, or targets a packaged handler id without a packaged
runtime implementation.

Manager Blueprint runtime-effect failures are policy-routed by class and
mutation phase. Missing, malformed, schema-invalid, or semantically mismatched
Manager artifacts fail before mutation and route to `mechanic_blueprint` under
the shipped recovery policy. Duplicate manifest ids, duplicate draft ids,
invalid source lifecycle state, and partial mutations block conservatively for
operator inspection. `runs show`, status JSON, and monitor events expose the
handler id, failure class/message, mutation phase, matched policy id, and
recovery action.

Mechanic Blueprint receives the failed Manager run directory, stage-result
path, runtime-effect failure class/message, and implicated Manager artifacts in
request context. Its initial recovery contract is diagnosis plus clean rerun:
emit `MECHANIC_BLUEPRINT_COMPLETE` with `resume_stage: manager_blueprint` only
when rerunning Manager against the still-active source is safe. Repaired
Manager artifacts are inert unless a future runtime effect explicitly consumes
them. If a clean rerun is not safe, Mechanic Blueprint emits `BLOCKED`.

## Durable Artifacts

Blueprint state is runtime-owned workspace state. Operators should use status
and run inspection first, then open raw files only when detailed diagnosis is
needed.

Lineage ids are not storage ids. `root_spec_id` and `root_idea_id` identify the
closure lineage used for inventory and Arbiter readiness; they do not own
Blueprint artifact paths. Blueprint manifests are keyed by
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
- `millrace runs show` should expose runtime-effect handler, decision,
  source-lifecycle, and created-path details for Blueprint stage runs
- same-root remediation manifests are expected when their `manifest_id` values
  differ; diagnose duplicate manifest failures by comparing `manifest_id`, not
  `root_spec_id`
- old root-keyed manifest files are read by embedded `manifest_id`; do not
  rename or overwrite them by hand while a daemon owns the workspace

## Related Docs

- `docs/runtime/millrace-modes-and-loops.md`
- `docs/runtime/millrace-compiler-and-frozen-plans.md`
- `docs/runtime/millrace-loop-authoring.md`
- `docs/runtime/millrace-arbiter-and-completion-behavior.md`
- `docs/adr/0010-compiler-validated-workflow-primitives-as-runtime-authority.md`
