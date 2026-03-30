# Taskmaster Decomposition Contract

Use this scaffold only for the `agents/_taskmaster.md` stage.

Purpose:
- convert queue/golden/phase specs into execution-sized task cards
- preserve phase coverage without stopping at one card per phase step
- keep mutation deterministic and validation-driven

Source of truth:
- `agents/ideas/specs/*.md`
- `agents/specs/stable/golden/*.md`
- `agents/specs/stable/phase/*.md`

Hard rules:
- Cover every stable phase Work Plan step.
- Phase-step coverage is necessary but not sufficient.
- If one Work Plan step is still too large for one Builder cycle, keep splitting it into deterministic execution cards.
- Never stop decomposing merely because each phase step has at least one card.
- Do not invent net-new requirements; only split and trace existing scope.

Execution-card contract:
- bounded change surface
- bounded verification surface
- bounded exit condition
- no open-ended reducer loops
- no whole-project or whole-suite closure as one card unless the card is purely final verification and the prerequisite fix work is already split elsewhere

Red flags that require further splitting:
- `iterate until pass`
- `fix until green`
- `implement until project passes`
- `run all gates and fix failures`
- one card spanning multiple subsystem outcomes plus final harness closure

Card-count guidance:
- Do not assume a legacy `5-15` envelope.
- Use the active decomposition profile and configured card floors/targets.
- Large campaigns are expected to produce materially larger pending sets.

Traceability:
- every card must preserve `Spec-ID`, `Requirement IDs`, `Acceptance IDs`, `Phase Step IDs`, and `Contract Trace`
- use deterministic suffixes when splitting one phase step into multiple cards, for example `PHASE_02.3a`, `PHASE_02.3b`
