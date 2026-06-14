# Learning Standard Graph

Source asset: `src/millrace_ai/assets/graphs/learning/standard.json`

Loop id: `learning.standard`
Plane: `learning`

`learning.standard` is the optional Learning plane graph. Generic learning
requests start at Analyst and can flow through Professor and Curator.
Targeted Librarian requests are one-off Learning work used to prepare relevant
remote optional skills after Planner output.

Learning does not replace Planning or Execution. In learning-enabled modes it
can run concurrently with one foreground Planning or Execution stage while
runtime-owned mutation remains serialized.

## Nodes

| Node | Stage Kind | Role |
| --- | --- | --- |
| `analyst` | `analyst` | Reviews runtime evidence and decides whether learning work is warranted. |
| `professor` | `professor` | Produces evidence-backed skill improvement guidance. |
| `curator` | `curator` | Applies accepted workspace-installed skill updates or exits no-op. |
| `librarian` | `librarian` | Checks installed and remote skill indexes and installs relevant optional skills. |

## Entry

| Work Item Family | Entry Node |
| --- | --- |
| `learning_request` | `analyst` |

Generic learning requests enter at `analyst`. Runtime-triggered requests may
also carry a target stage; targeted Librarian requests start at `librarian`
instead of replaying the Analyst-to-Curator path.

## Paths

Generic improvement path:

```text
learning_request -> analyst -> professor -> curator -> CURATOR_COMPLETE
```

No-op terminals allow reviewed learning work to close cleanly when no useful
patch, candidate, or optional skill install is warranted.

Targeted Librarian path:

```text
learning_request(target=librarian) -> librarian -> LIBRARIAN_COMPLETE | LIBRARIAN_NOOP
```

## Edges

| From | Outcome | To |
| --- | --- | --- |
| `analyst` | `ANALYST_COMPLETE` | `professor` |
| `analyst` | `ANALYST_NOOP` | terminal `analyst_noop` |
| `analyst` | `BLOCKED` | terminal `blocked` |
| `professor` | `PROFESSOR_COMPLETE` | `curator` |
| `professor` | `PROFESSOR_NOOP` | terminal `professor_noop` |
| `professor` | `BLOCKED` | terminal `blocked` |
| `curator` | `CURATOR_COMPLETE` | terminal `learning_complete` |
| `curator` | `CURATOR_NOOP` | terminal `curator_noop` |
| `curator` | `BLOCKED` | terminal `blocked` |
| `librarian` | `LIBRARIAN_COMPLETE` | terminal `librarian_complete` |
| `librarian` | `LIBRARIAN_NOOP` | terminal `librarian_noop` |
| `librarian` | `BLOCKED` | terminal `blocked` |

## Terminal States

| Terminal State | Status | Class | Meaning |
| --- | --- | --- | --- |
| `analyst_noop` | `ANALYST_NOOP` | `no_op` | Analyst reviewed evidence and found no useful downstream learning work. |
| `professor_noop` | `PROFESSOR_NOOP` | `no_op` | Professor found no safe or useful skill-improvement guidance to curate. |
| `learning_complete` | `CURATOR_COMPLETE` | `success` | Curator applied or prepared an accepted skill update. |
| `curator_noop` | `CURATOR_NOOP` | `no_op` | Curator reviewed the candidate and made no mutation. |
| `librarian_complete` | `LIBRARIAN_COMPLETE` | `success` | Librarian installed at least one relevant optional skill. |
| `librarian_noop` | `LIBRARIAN_NOOP` | `no_op` | Librarian found no relevant uninstalled remote skill or no install was needed. |
| `blocked` | `BLOCKED` | `blocked` | Learning could not safely proceed. |

## Triggered Requests

Learning-enabled modes ship these trigger rules:

- `DOUBLECHECK_PASS` from `execution.doublechecker` requests Analyst
  improvement work.
- `TROUBLESHOOT_COMPLETE` or `BLOCKED` from `execution.troubleshooter` requests
  Analyst improvement work.
- `CONSULT_COMPLETE`, `NEEDS_PLANNING`, or `BLOCKED` from
  `execution.consultant` requests Analyst improvement work.
- `PLANNER_COMPLETE` from `planning.planner` requests targeted Librarian
  optional-skill preparation.

## Selected By

- `learning_lad_codex`
- `efficient_learning_lad_mixed`
- `learning_lad_pi`
- `learning_lad_codex_integrated`
- `blueprint_learning_lad_codex`
