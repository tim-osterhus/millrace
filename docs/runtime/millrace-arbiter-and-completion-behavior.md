# Millrace Arbiter And Completion Behavior

This document describes the shipped completion model for modes that select
`planning.standard` or `planning.blueprint`, including `default_codex`,
`default_pi`, `learning_codex`, `efficient_learning_mixed`, `learning_pi`,
`default_codex_integrated`, `learning_codex_integrated`, `blueprint_codex`,
and `blueprint_learning_codex`.

Millrace no longer treats backlog drain as automatic completion. When a root
lineage has an open closure target and no queued, active, or blocked work
remains for that lineage, the compiled planning-loop `completion_behavior`
dispatches the `arbiter` stage through the normal runner contract.

## Root Source Model

Closure behavior pairs two durable contracts:

- `root_spec_id`
- `root_source`

`root_spec_id` identifies the implementation contract that decomposed the
workline. `root_source` identifies the original intake artifact that caused the
workline to exist, such as an idea, probe, manual/spec intake, or incident.
The legacy `root_idea_id` field remains supported for idea-rooted work and maps
to `root_source.kind = idea`.

Watcher-seeded root specs are expected to initialize both fields immediately,
and Planner/Manager are expected to preserve them when refining specs or
emitting tasks.

Watcher-seeded idea specs also preserve the original idea markdown under
`millrace-agents/intake/ideas/<root_idea_id>.md`. Generated specs reference
that runtime-owned copy before the transient `ideas/inbox/` source so closure
target creation does not depend on an inbox file remaining in place.

## Canonical Contract Sources

Arbiter judges against canonical copies under its own workspace subtree:

- `millrace-agents/arbiter/contracts/root-sources/<kind>/<source_id>.md`
- `millrace-agents/arbiter/contracts/ideas/<root_idea_id>.md`
- `millrace-agents/arbiter/contracts/root-specs/<root_spec_id>.md`

The generic `root-sources/` path is authoritative for new code. The
`contracts/ideas/` path remains a compatibility mirror for idea-rooted closure
targets. Runtime snapshots source contracts immediately from durable intake
storage when available, then from supported lifecycle folders and workspace
relative references. Arbiter does not search arbitrary local files later.
If the root source cannot be resolved during backlog-drain recovery, Planning
is marked blocked with a precise failure class such as
`root_source_unresolved`, `root_source_ambiguous`, or
`root_source_kind_unsupported`.

## Closure Target State

The runtime owns one closure-target state file per root spec:

- `millrace-agents/arbiter/targets/<root_spec_id>.json`

The shipped v1 policy is one open closure target per workspace. The target file
records:

- root source kind/id/path
- root spec id/path
- legacy idea id/path for idea-rooted targets
- canonical contract paths
- rubric path
- latest verdict/report paths
- whether closure is still open
- whether remaining lineage work still blocks closure
- the last Arbiter run id

## Backlog-Drain Behavior

The compiled planning-loop `completion_behavior` for `planning.standard` is:

- trigger: `backlog_drained`
- readiness rule: `no_open_lineage_work`
- root source policy: accepted kinds `idea`, `probe`, `manual`, `spec`, and
  `incident` with `runtime_inventory` resolution
- stage: `arbiter`
- request kind: `closure_target`
- target selector: `active_closure_target`
- blocked-work policy: `suppress`

Runtime behavior is:

1. if no closure target is open, claim normal planning/execution/learning work
2. if one closure target is open, defer unrelated queued root specs and claim
   only same-lineage execution/planning work
3. if no same-lineage work remains, inspect the compiled completion behavior
4. locate the single open closure target
5. if no open target exists, try to backfill one from the latest root spec by
   resolving its generic root source deterministically
6. scan queued, active, and blocked work for matching `root_spec_id`, including
   Blueprint drafts, candidate packets, approved-unpromoted packets, promotion
   records, and generated tasks when the selected Planning graph uses
   `planning.blueprint`
7. suppress Arbiter if lineage work still remains
8. dispatch Arbiter when the target is eligible

If no open target exists and the latest root spec is missing `root_spec_id` or
has no resolvable root source, the runtime marks planning blocked and emits a
diagnosable runtime event instead of silently idling through required closure
behavior.

## Bulk Root-Spec Intake Backpressure

Watcher intake may enqueue several independent root specs at once. That is
valid input pressure. While the v1 one-open-closure-target policy is active,
Millrace serializes those independent root lineages instead of opening a second
target.

When one closure target is open:

- queued root specs for other lineages stay in `millrace-agents/specs/queue/`
- same-lineage tasks and remediation planning items remain claimable
- runtime-created execution-to-planning handoff incidents inherit root lineage
  from their source work item before enqueue, so Consultant `NEEDS_PLANNING`
  escalation stays visible to closure-scoped planning selection
- Arbiter is activated before an unrelated root spec is claimed once same-lineage work drains
- `closure_target_backpressure` events record the open root spec and deferred root specs
- `millrace status` reports `planning_root_specs_deferred_by_closure_target`

If an older workspace is already in a half-claimed state after a closure-target
invariant failure, use `millrace control clear-stale-state --workspace <workspace>`
while no daemon owns the workspace. The command requeues active work items and
preserves the open closure target so the next daemon start can continue the
current lineage.

## Arbiter Request Contract

Arbiter is a real planning-stage run. It does not receive a fake queue item.

Its entrypoint always loads `arbiter-core` first and may additionally load the
shipped shared `marathon-qa-audit` skill when Arbiter is creating a rubric for
the first time or when the available evidence surface is too weak for an honest
narrow pass.

The stage request uses `request_kind = closure_target` and includes:

- `closure_target_path`
- `closure_target_root_spec_id`
- `closure_target_root_source_kind`
- `closure_target_root_source_id`
- `closure_target_root_source_path`
- `closure_target_root_idea_id` for legacy idea-rooted work
- `canonical_root_spec_path`
- `canonical_seed_idea_path` for legacy idea-rooted work
- `preferred_rubric_path`
- `preferred_verdict_path`
- `preferred_report_path`

The normalized stage result still projects onto `work_item_kind = spec` and
`work_item_id = <root_spec_id>` so the result envelope stays typed and stable.

## Arbiter Artifact Layout

Arbiter-owned durable artifacts live under:

- `millrace-agents/arbiter/rubrics/<root_spec_id>.md`
- `millrace-agents/arbiter/verdicts/<root_spec_id>.json`
- `millrace-agents/arbiter/reports/<run_id>.md`

The per-run report is copied into the Arbiter reports directory by runtime
result application so the durable report path is stable even though the stage
itself writes inside the run directory during execution.

## Runtime-Owned Outcomes

Arbiter may emit only:

- `ARBITER_COMPLETE`
- `REMEDIATION_NEEDED`
- `BLOCKED`

Runtime result application owns the workflow consequences:

- `ARBITER_COMPLETE`: close the target, stamp `closed_at`, persist latest
  verdict/report paths, and return the runtime to idle.
- `REMEDIATION_NEEDED`: keep the target open, persist latest verdict/report
  paths, and enqueue a planning incident under
  `millrace-agents/incidents/incoming/`.
- `BLOCKED`: keep the target open, persist the latest run/report context, and
  leave the planning status blocked without fabricating queue work.

Arbiter does not mutate closure-target workflow authority directly. It produces
artifacts and a terminal result; the runtime applies the authoritative state
change.

## Operator Inspection Surfaces

The current operator-facing surfaces expose this behavior directly:

- `millrace compile show` prints frozen `completion_behavior`
- `millrace status` prints the active open closure target root source,
  deferred-root count,
  latest verdict/report paths, and Blueprint draft/packet/critique/evaluation
  and promotion summaries when those artifacts exist
- `millrace runs show` prints request kind, closure-target root source for Arbiter
  runs, and runtime-effect lifecycle intent plus created paths for Blueprint
  stages

Use those surfaces before opening raw JSON files unless you need the full
artifact payload.
