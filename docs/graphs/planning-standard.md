# Planning Standard Graph

Source asset: `src/millrace_ai/assets/graphs/planning/standard.json`

Loop id: `planning.standard`
Plane: `planning`

`planning.standard` is the default Planning graph. It classifies probes through
Recon, turns specs into executable tasks through Planner and Manager, routes
incidents through Auditor, recovers blocked Planning work through Mechanic, and
activates Arbiter when backlog drain makes a closure target eligible.

## Nodes

| Node | Stage Kind | Role |
| --- | --- | --- |
| `recon` | `recon` | Classifies probe intake and emits execution, planning, no-op, or blocked packets. |
| `planner` | `planner` | Turns a spec or audited incident into a concrete plan. |
| `manager` | `manager` | Decomposes Planner output into executable tasks. |
| `mechanic` | `mechanic` | Repairs blocked Planning work and chooses a resume target. |
| `auditor` | `auditor` | Interprets incidents before returning them to Planner. |
| `arbiter` | `arbiter` | Performs closure-target judgment after same-lineage backlog drain. |

## Entries

| Work Item Family | Entry Node |
| --- | --- |
| `probe` | `recon` |
| `spec` | `planner` |
| `incident` | `auditor` |

## Primary Paths

Spec planning:

```text
spec -> planner -> manager -> MANAGER_COMPLETE
```

Incident planning:

```text
incident -> auditor -> planner -> manager -> MANAGER_COMPLETE
```

Probe classification:

```text
probe -> recon -> RECON_TO_EXECUTION | RECON_TO_PLANNING | RECON_NOOP | RECON_BLOCKED
```

Closure judgment is not normal queue intake. Arbiter is activated by compiled
completion behavior after the runtime finds an eligible open closure target.

## Edges

| From | Outcome | To |
| --- | --- | --- |
| `recon` | `RECON_TO_EXECUTION` | terminal `recon_to_execution` |
| `recon` | `RECON_TO_PLANNING` | terminal `recon_to_planning` |
| `recon` | `RECON_NOOP` | terminal `recon_noop` |
| `recon` | `RECON_BLOCKED` | terminal `recon_blocked` |
| `recon` | `BLOCKED` | terminal `recon_blocked` |
| `planner` | `PLANNER_COMPLETE` | `manager` |
| `planner` | `BLOCKED` | `mechanic` |
| `manager` | `MANAGER_COMPLETE` | terminal `manager_complete` |
| `manager` | `BLOCKED` | `mechanic` |
| `mechanic` | `MECHANIC_COMPLETE` | `planner` by default, or a valid metadata-selected resume node |
| `mechanic` | `BLOCKED` | `mechanic`, until the blocked-recovery threshold is exhausted |
| `auditor` | `AUDITOR_COMPLETE` | `planner` |
| `auditor` | `BLOCKED` | `mechanic` |
| `arbiter` | `ARBITER_COMPLETE` | terminal `arbiter_complete` |
| `arbiter` | `REMEDIATION_NEEDED` | terminal `remediation_needed` |
| `arbiter` | `BLOCKED` | terminal `blocked` |

## Terminal States

| Terminal State | Status | Class | Meaning |
| --- | --- | --- | --- |
| `recon_to_execution` | `RECON_TO_EXECUTION` | `success` | Recon emitted a packet and generated execution task. |
| `recon_to_planning` | `RECON_TO_PLANNING` | `success` | Recon emitted a packet and generated planning spec. |
| `recon_noop` | `RECON_NOOP` | `no_op` | Recon determined no downstream queue work is needed. |
| `recon_blocked` | `RECON_BLOCKED` | `blocked` | Recon could not classify or safely hand off the probe. |
| `manager_complete` | `MANAGER_COMPLETE` | `success` | Manager produced executable work items. |
| `arbiter_complete` | `ARBITER_COMPLETE` | `success` | Arbiter judged the closure target complete. |
| `remediation_needed` | `REMEDIATION_NEEDED` | `followup_needed` | Arbiter found closure gaps with current or revalidated evidence, and the runtime creates remediation work. |
| `blocked` | `BLOCKED` | `blocked` | Planning could not recover autonomously. |

## Recovery Policies

Mechanic resume:

- Source node: `mechanic`
- Outcome: `MECHANIC_COMPLETE`
- Default target: `planner`
- Metadata key: `resume_stage`
- Disallowed target: `mechanic`

Blocked recovery:

- Source nodes: `planner`, `manager`, `auditor`, `mechanic`
- Outcome: `BLOCKED`
- Counter: `mechanic_attempt_count`
- Threshold: `2`
- Exhausted terminal state: `blocked`

## Completion Behavior

`planning.standard` ships compiled completion behavior:

- Trigger: `backlog_drained`
- Readiness rule: `no_open_lineage_work`
- Target node: `arbiter`
- Request kind: `closure_target`
- Target selector: `active_closure_target`
- Rubric policy: `reuse_or_create`
- Blocked-work policy: `suppress`
- Pass terminal: `arbiter_complete`
- Gap terminal: `remediation_needed`
- Gap behavior: create a planning incident

Arbiter closure requests include a runtime-authored closure evidence window.
Arbiter reads that window before old verdict/report artifacts and records
per-criterion evidence provenance as `fresh`, `revalidated`,
`historical_only`, or `missing`. When same-lineage remediation is newer than
the prior Arbiter verdict, historical-only evidence is context, not a current
pass/fail basis. On the gap terminal, Arbiter writes remediation guidance in
its verdict/report; runtime owns incident enqueueing, dedupe, and repeated
remediation suppression.

## Selected By

- `default_codex`
- `default_pi`
- `learning_codex`
- `efficient_learning_mixed`
- `learning_pi`
- `default_codex_integrated`
- `learning_codex_integrated`
