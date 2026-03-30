# Phase Spec Template

Use this template when a golden spec needs explicit phased execution notes.

Recommended path:
- `agents/specs/decisions/<spec_id>__phase-<nn>.md`

```yaml
---
phase_id: PHASE-<id>
phase_key: PHASE_<nn>
phase_priority: P0|P1|P2|P3
parent_spec_id: SPEC-<id>
title: <phase title>
status: planned|active|done|blocked
owner: <role-or-team>
created_at: <ISO8601>
updated_at: <ISO8601>
---
```

## Objective
- Single-sentence phase outcome.

## Entry Criteria
- Preconditions that must be true before starting.

## Scope
### In Scope
- Deliverables for this phase only.

### Out of Scope
- Deferred work for later phases.

## Work Plan
1. One bounded deliverable or bounded verification closure
2. Another bounded deliverable
3. Final bounded handoff or gate verification for this phase only

Work Plan rules:
- each numbered item must be execution-sized enough that Taskmaster can split it into one or a few execution cards without inventing hidden structure
- split multi-subsystem outcomes before finalizing the phase spec
- do not use open-ended phrasing such as `iterate until pass`, `fix until green`, `implement until project passes`, or `run all gates and fix failures`

## Requirements Traceability (Req-ID)
- `Req-ID: REQ-<nnn>` mapped to one concrete phase deliverable.
- Each Req-ID must reference verification evidence for this phase.

## Assumptions Ledger
- Assumption and confidence (`confirmed|inferred|offline-only`)
- Validation path or explicit accepted risk
- Keep entries within `PHASE_ASSUMPTIONS_BUDGET` (configured in `agents/options/workflow_config.md`).

## Structured Decision Log
| decision_id | phase_key | phase_priority | status | owner | rationale | timestamp |
| --- | --- | --- | --- | --- | --- | --- |
| DEC-PHASE-001 | PHASE_<nn> | P1 | proposed | <owner> | <phase-level decision rationale> | <ISO8601> |

## Interrogation Notes
- Critic challenge summary for this phase.
- Designer resolution summary for this phase.

## Verification
- Commands/checks for this phase.
- Expected PASS conditions.

## Exit Criteria
- Conditions required to mark phase complete.

## Handoff
- Artifacts and pointers the next phase needs.

## Risks
- Risk and mitigation for this phase.
