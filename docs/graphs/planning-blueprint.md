# Planning Blueprint Graph

Source asset: `src/millrace_ai/assets/graphs/planning/blueprint.json`

Loop id: `planning.blueprint`
Plane: `planning`

`planning.blueprint` is the opt-in Planning graph for strict draft-packet
decomposition. It keeps Recon, Planner, Auditor, and Arbiter, but replaces the
standard Manager handoff with Manager Blueprint, Contractor Blueprint,
Evaluator Blueprint, and Mechanic Blueprint.

Builder still performs implementation in the Execution plane. Blueprint
Planning produces approved generated tasks; it does not directly edit the
source repo.

## Nodes

| Node | Stage Kind | Role |
| --- | --- | --- |
| `recon` | `recon` | Classifies probe intake and emits execution, planning, no-op, or blocked packets. |
| `planner` | `planner` | Produces the root Planning disposition before Blueprint decomposition. |
| `manager_blueprint` | `manager_blueprint` | Creates a Blueprint manifest and ordered draft records. |
| `contractor_blueprint` | `contractor_blueprint` | Proposes or revises one Blueprint packet for one active draft. |
| `evaluator_blueprint` | `evaluator_blueprint` | Approves a packet into a generated task or rejects it with critique. |
| `mechanic_blueprint` | `mechanic_blueprint` | Handles blocked Blueprint Planning work and supported runtime-effect repair. |
| `auditor` | `auditor` | Interprets incidents before returning them to Planner. |
| `arbiter` | `arbiter` | Performs closure-target judgment after same-lineage backlog drain. |

## Entries

| Work Item Family | Entry Node |
| --- | --- |
| `probe` | `recon` |
| `spec` | `planner` |
| `incident` | `auditor` |
| `blueprint_draft` | `contractor_blueprint` |

## Primary Paths

Spec planning:

```text
spec -> planner -> manager_blueprint -> MANAGER_BLUEPRINT_COMPLETE
```

Draft packet loop:

```text
blueprint_draft -> contractor_blueprint -> evaluator_blueprint
```

Evaluator approval is terminal for that draft and promotes a generated task.
Evaluator rejection routes the same draft back to Contractor with critique:

```text
evaluator_blueprint --BLUEPRINT_REJECTED--> contractor_blueprint
```

Incident planning:

```text
incident -> auditor -> planner -> manager_blueprint
```

Probe classification uses the same Recon terminal handoffs as
`planning.standard`.

## Edges

| From | Outcome | To |
| --- | --- | --- |
| `recon` | `RECON_TO_EXECUTION` | terminal `recon_to_execution` |
| `recon` | `RECON_TO_PLANNING` | terminal `recon_to_planning` |
| `recon` | `RECON_NOOP` | terminal `recon_noop` |
| `recon` | `RECON_BLOCKED` | terminal `recon_blocked` |
| `recon` | `BLOCKED` | terminal `recon_blocked` |
| `planner` | `PLANNER_COMPLETE` | `manager_blueprint` |
| `planner` | `BLOCKED` | `mechanic_blueprint` |
| `manager_blueprint` | `MANAGER_BLUEPRINT_COMPLETE` | terminal `manager_blueprint_complete` |
| `manager_blueprint` | `BLOCKED` | `mechanic_blueprint` |
| `contractor_blueprint` | `BLUEPRINT_CANDIDATE_READY` | `evaluator_blueprint` |
| `contractor_blueprint` | `BLOCKED` | `mechanic_blueprint` |
| `evaluator_blueprint` | `BLUEPRINT_APPROVED` | terminal `blueprint_approved` |
| `evaluator_blueprint` | `BLUEPRINT_REJECTED` | `contractor_blueprint` |
| `evaluator_blueprint` | `BLOCKED` | `mechanic_blueprint` |
| `mechanic_blueprint` | `MECHANIC_BLUEPRINT_COMPLETE` | `planner` by default, or a valid metadata-selected resume node |
| `mechanic_blueprint` | `BLOCKED` | `mechanic_blueprint`, until the blocked-recovery threshold is exhausted |
| `auditor` | `AUDITOR_COMPLETE` | `planner` |
| `auditor` | `BLOCKED` | `mechanic_blueprint` |
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
| `manager_blueprint_complete` | `MANAGER_BLUEPRINT_COMPLETE` | `success` | Manager Blueprint persisted manifest/drafts and queued draft work. |
| `blueprint_approved` | `BLUEPRINT_APPROVED` | `success` | Evaluator approved a packet and the runtime promoted a generated task. |
| `arbiter_complete` | `ARBITER_COMPLETE` | `success` | Arbiter judged the closure target complete. |
| `remediation_needed` | `REMEDIATION_NEEDED` | `followup_needed` | Arbiter found closure gaps and the runtime creates remediation work. |
| `blocked` | `BLOCKED` | `blocked` | Blueprint Planning could not recover autonomously. |

## Runtime Effects

Blueprint terminal states rely on compiled runtime-effect rules:

- `MANAGER_BLUEPRINT_COMPLETE` persists the manifest and drafts, queues draft
  records, and completes or resolves the source spec or incident.
- `BLUEPRINT_CANDIDATE_READY` persists the candidate packet and routes the
  active draft to Evaluator.
- `BLUEPRINT_REJECTED` persists evaluation and critique artifacts, marks the
  draft for revision, and routes the same draft back to Contractor.
- `BLUEPRINT_APPROVED` persists the approved packet, evaluation, promotion
  record, and generated execution task.

Use `../runtime/millrace-blueprint-planning.md` for the detailed artifact,
idempotency, and runtime-effect failure model.

## Recovery Policies

Mechanic Blueprint resume:

- Source node: `mechanic_blueprint`
- Outcome: `MECHANIC_BLUEPRINT_COMPLETE`
- Default target: `planner`
- Metadata key: `resume_stage`
- Disallowed target: `mechanic_blueprint`

Blocked recovery:

- Source nodes: `planner`, `manager_blueprint`, `contractor_blueprint`,
  `evaluator_blueprint`, `auditor`, `mechanic_blueprint`
- Outcome: `BLOCKED`
- Counter: `mechanic_attempt_count`
- Threshold: `2`
- Exhausted terminal state: `blocked`

## Completion Behavior

`planning.blueprint` ships the same Arbiter completion behavior shape as
`planning.standard`:

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

Blueprint closure readiness also accounts for same-lineage Blueprint drafts,
candidate packets, approved-but-unpromoted packets, and generated tasks.

## Selected By

- `blueprint_codex`
- `blueprint_learning_codex`

