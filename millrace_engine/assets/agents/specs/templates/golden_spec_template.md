# Golden Spec Template

Use this template for canonical feature specs saved under:
- `agents/ideas/specs/<spec_id>__<slug>.md` (queue)
- `agents/specs/stable/golden/<spec_id>__<slug>.md` (immutable stable copy)

```yaml
---
spec_id: SPEC-<id>
idea_id: IDEA-<id>
title: <short title>
status: proposed|approved|superseded
golden_version: <integer>
base_goal_sha256: <sha256-or-empty>
effort: 1-5
decomposition_profile: trivial|simple|moderate|involved|complex|massive
depends_on_specs: []
owner: <role-or-team>
created_at: <ISO8601>
updated_at: <ISO8601>
---
```

## Summary
- Problem in one paragraph.
- Why this matters now.

## Goals
- Primary outcomes this spec must deliver.

## Non-Goals
- Explicitly out-of-scope work.

## Scope
### In Scope
- Concrete deliverables.

### Out of Scope
- Boundaries that keep implementation focused.

## Capability Domains
- Domain 1
- Domain 2

## Decomposition Readiness
- Why this scope fits one queue-spec envelope, or why it is intentionally split across multiple dependent queue specs.
- Expected phase density for the declared `decomposition_profile`.
- Any phase-step areas that must be split before Taskmaster.

## Constraints
- Contract/schema constraints.
- Runtime/security constraints.
- Operational constraints.

## Implementation Plan
1. Checkpoint 1
2. Checkpoint 2
3. Checkpoint 3

## Requirements Traceability (Req-ID Matrix)
- `Req-ID: REQ-001` | Requirement statement | Verification evidence path
- `Req-ID: REQ-002` | Requirement statement | Verification evidence path

## Assumptions Ledger
- Assumption A (source: confirmed|inferred|offline-only)
- Validation plan or risk-acceptance note

## Structured Decision Log
| decision_id | phase_key | phase_priority | status | owner | rationale | timestamp |
| --- | --- | --- | --- | --- | --- | --- |
| DEC-001 | PHASE_01 | P1 | proposed | <owner> | <why this decision exists> | <ISO8601> |

## Interrogation Record
- Round log for critic/designer passes.
- Open questions that changed spec scope or wording.

## Verification
- Commands QA/Builder can run.
- Expected observable outcomes.

## Dependencies
- Required prerequisites and owner.
- Whether dependency is already present in repo.

## Risks and Mitigations
- Risk
- Mitigation

## Rollout and Rollback
- Rollout order.
- Rollback trigger and fallback behavior.

## Open Questions
- Question
- Owner

## References
- Related tasks/specs/docs.
- Phase planning files should use `PHASE_<nn>` keys and `phase_priority: P0|P1|P2|P3` in `agents/specs/stable/phase/`.
