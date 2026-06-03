# Execution Standard Graph

Source asset: `src/millrace_ai/assets/graphs/execution/standard.json`

Loop id: `execution.standard`
Plane: `execution`

`execution.standard` is the default task-execution graph. It turns queued
tasks into bounded Builder work, sends successful builds through QA, routes
repairable failures into Fixer and Doublechecker, and escalates repeated or
unrecoverable blockage through Troubleshooter and Consultant.

## Nodes

| Node | Stage Kind | Role |
| --- | --- | --- |
| `builder` | `builder` | Implements the queued task scope. |
| `checker` | `checker` | Verifies Builder output against task acceptance and required checks. |
| `fixer` | `fixer` | Repairs Checker or Doublechecker failures. |
| `doublechecker` | `doublechecker` | Rechecks Fixer output before update. |
| `updater` | `updater` | Writes final completion evidence and terminal update output. |
| `troubleshooter` | `troubleshooter` | Diagnoses blocked execution work and chooses a resume target. |
| `consultant` | `consultant` | Handles exhausted execution recovery and may escalate to Planning. |

## Entry

| Work Item Family | Entry Node |
| --- | --- |
| `task` | `builder` |

## Primary Success Path

```text
task -> builder -> checker -> updater -> UPDATE_COMPLETE
```

When Checker finds work that needs repair, the graph uses the repair path:

```text
checker -> fixer -> doublechecker -> updater -> UPDATE_COMPLETE
```

Doublechecker can route back to Fixer on `FIX_NEEDED`, so repair is a bounded
cycle rather than a single retry.

## Edges

| From | Outcome | To |
| --- | --- | --- |
| `builder` | `BUILDER_COMPLETE` | `checker` |
| `builder` | `BLOCKED` | `troubleshooter` |
| `checker` | `CHECKER_PASS` | `updater` |
| `checker` | `FIX_NEEDED` | `fixer` |
| `checker` | `BLOCKED` | `troubleshooter` |
| `fixer` | `FIXER_COMPLETE` | `doublechecker` |
| `fixer` | `BLOCKED` | `troubleshooter` |
| `doublechecker` | `DOUBLECHECK_PASS` | `updater` |
| `doublechecker` | `FIX_NEEDED` | `fixer` |
| `doublechecker` | `BLOCKED` | `troubleshooter` |
| `updater` | `UPDATE_COMPLETE` | terminal `update_complete` |
| `updater` | `BLOCKED` | `troubleshooter` |
| `troubleshooter` | `TROUBLESHOOT_COMPLETE` | `builder` by default, or a valid metadata-selected resume node |
| `troubleshooter` | `BLOCKED` | `troubleshooter`, until the blocked-recovery threshold is exhausted |
| `consultant` | `CONSULT_COMPLETE` | `troubleshooter` by default, or a valid metadata-selected resume node |
| `consultant` | `NEEDS_PLANNING` | terminal `needs_planning` |
| `consultant` | `BLOCKED` | terminal `blocked` |

## Terminal States

| Terminal State | Status | Class | Meaning |
| --- | --- | --- | --- |
| `update_complete` | `UPDATE_COMPLETE` | `success` | The task completed and Updater wrote completion evidence. |
| `needs_planning` | `NEEDS_PLANNING` | `escalate_planning` | Execution needs Planning help; the runtime creates a planning handoff incident. |
| `blocked` | `BLOCKED` | `blocked` | Execution could not recover autonomously and needs operator or later planning attention. |

## Recovery Policies

Troubleshooter resume:

- Source node: `troubleshooter`
- Outcome: `TROUBLESHOOT_COMPLETE`
- Default target: `builder`
- Metadata key: `resume_stage`
- Disallowed target: `consultant`

Consultant resume:

- Source node: `consultant`
- Outcome: `CONSULT_COMPLETE`
- Default target: `troubleshooter`
- Metadata keys: `target_stage`, `resume_stage`
- Disallowed target: `consultant`

Fix-needed exhaustion:

- Source nodes: `checker`, `doublechecker`
- Outcome: `FIX_NEEDED`
- Counter: `fix_cycle_count`
- Threshold: `2`
- Exhausted target: `troubleshooter`

Blocked recovery:

- Source nodes: `builder`, `checker`, `fixer`, `doublechecker`, `updater`,
  `troubleshooter`
- Outcome: `BLOCKED`
- Counter: `troubleshoot_attempt_count`
- Threshold: `2`
- Exhausted target: `consultant`

## Selected By

- `default_codex`
- `default_pi`
- `learning_codex`
- `efficient_learning_mixed`
- `learning_pi`
- `blueprint_codex`
- `blueprint_learning_codex`
