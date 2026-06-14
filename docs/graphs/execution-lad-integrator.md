# LAD Execution Integrator Graph

Source asset: `src/millrace_ai/assets/graphs/execution/lad_integrator.json`

Loop id: `execution.lad_integrator`
Plane: `execution`

`execution.lad_integrator` is the high-assurance Execution graph. It keeps the
LAD task intake, QA, repair, troubleshooting, and planning-escalation
shape, but inserts Integrator between Builder and Checker so Builder output is
reviewed before normal QA begins.

## Nodes

| Node | Stage Kind | Role |
| --- | --- | --- |
| `builder` | `builder` | Implements the queued task scope. |
| `integrator` | `integrator` | Reviews Builder output, integration surfaces, and required/discoverable gates. |
| `checker` | `checker` | Performs normal QA after Integrator completes. |
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
task -> builder -> integrator -> checker -> updater -> UPDATE_COMPLETE
```

The repair path after Checker remains:

```text
checker -> fixer -> doublechecker -> updater -> UPDATE_COMPLETE
```

Integrator is a quality gate, not a second Builder. It writes
`integration_report.md` and hands the same task forward to Checker when the
integrated output is ready for QA.

## Edges

| From | Outcome | To |
| --- | --- | --- |
| `builder` | `BUILDER_COMPLETE` | `integrator` |
| `builder` | `BLOCKED` | `troubleshooter` |
| `integrator` | `INTEGRATION_COMPLETE` | `checker` |
| `integrator` | `BLOCKED` | `troubleshooter` |
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
| `troubleshooter` | `BLOCKED` | `consultant` |
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

The Integrator graph inherits the LAD Execution resume policies:

- Troubleshooter can resume to a metadata-selected stage through `resume_stage`,
  defaulting to `builder`; it cannot resume to `consultant`.
- Consultant can resume through `target_stage` or `resume_stage`, defaulting to
  `troubleshooter`; it cannot resume to itself.

The same fix-needed exhaustion policy applies to `checker` and
`doublechecker` with threshold `2`, routing exhausted fix cycles to
`troubleshooter`.

Blocked recovery also uses threshold `2`, routing exhausted blockage to
`consultant`. In this graph the blocked-recovery source set includes
`integrator` in addition to the LAD Execution nodes.

The Troubleshooter `BLOCKED` edge also targets `consultant` directly, so a
blocked Troubleshooter hands preserved recovery evidence to Consultant instead
of re-entering Troubleshooter.

## Selected By

- `lad_codex_integrated`
- `learning_lad_codex_integrated`
