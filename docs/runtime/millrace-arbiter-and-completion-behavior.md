# Millrace Arbiter And Completion Behavior

This document describes the shipped completion model for `default_codex`.

Millrace no longer treats backlog drain as automatic completion. When a root
lineage has an open closure target and no queued, active, or blocked work
remains for that lineage, the frozen planning-loop `completion_behavior`
dispatches the `arbiter` stage through the normal runner contract.

## Root Lineage Model

Closure behavior is keyed by explicit root-lineage fields carried through work
documents:

- `root_spec_id`
- `root_idea_id`

Those fields live on canonical task, spec, and incident markdown documents. The
immediate provenance fields still exist, but Arbiter uses root lineage so it
does not guess which spec family it is judging after remediation churn.

Watcher-seeded root specs are expected to initialize both fields immediately,
and Planner/Manager are expected to preserve them when refining specs or
emitting tasks.

## Canonical Contract Sources

Arbiter judges against canonical copies under its own workspace subtree:

- `millrace-agents/arbiter/contracts/ideas/<root_idea_id>.md`
- `millrace-agents/arbiter/contracts/root-specs/<root_spec_id>.md`

Those copies are opened when the root spec first enters the managed lineage.
The runtime snapshots them immediately instead of making Arbiter search the
operator-authored workspace for mutable source files later.

## Closure Target State

The runtime owns one closure-target state file per root spec:

- `millrace-agents/arbiter/targets/<root_spec_id>.json`

The shipped v1 policy is one open closure target per workspace. The target file
records:

- root lineage ids
- canonical contract paths
- rubric path
- latest verdict/report paths
- whether closure is still open
- whether remaining lineage work still blocks closure
- the last Arbiter run id

## Backlog-Drain Behavior

The frozen planning-loop `completion_behavior` for `default_codex` is:

- trigger: `backlog_drained`
- readiness rule: `no_open_lineage_work`
- stage: `arbiter`
- request kind: `closure_target`
- target selector: `active_closure_target`
- blocked-work policy: `suppress`

Runtime behavior is:

1. claim planning work if available
2. claim execution work if available
3. if no claimable work remains, inspect the frozen completion behavior
4. locate the single open closure target
5. if no open target exists, try to backfill one from the latest root spec that
   already carries root-lineage ids
6. scan queued, active, and blocked work for matching `root_spec_id`
7. suppress Arbiter if lineage work still remains
8. dispatch Arbiter when the target is eligible

If no open target exists and the latest root spec is still missing root-lineage
metadata, the runtime marks planning blocked and emits a diagnosable runtime
event instead of silently idling through required closure behavior.

## Arbiter Request Contract

Arbiter is a real planning-stage run. It does not receive a fake queue item.

Its entrypoint always loads `arbiter-core` first and may additionally load the
shipped shared `marathon-qa-audit` skill when Arbiter is creating a rubric for
the first time or when the available evidence surface is too weak for an honest
narrow pass.

The stage request uses `request_kind = closure_target` and includes:

- `closure_target_path`
- `closure_target_root_spec_id`
- `closure_target_root_idea_id`
- `canonical_root_spec_path`
- `canonical_seed_idea_path`
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
- `millrace status` prints the active open closure target and latest verdict/report paths
- `millrace runs show` prints request kind and closure-target lineage for Arbiter runs

Use those surfaces before opening raw JSON files unless you need the full
artifact payload.
