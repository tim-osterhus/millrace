# Status Contract (Canonical)

This file is the canonical marker contract for Millrace control planes.

- Execution plane status file: `agents/status.md`
- Research plane status file: `agents/research_status.md`

## Core Rules

1. Overwrite-only: each status file must contain one authoritative `### ...` marker line.
2. Ownership is file-scoped and non-overlapping:
- Execution stages write only `agents/status.md`.
- Research stages write only `agents/research_status.md`.
3. New markers are not implicit. If a new marker is introduced, update this contract and loop consumers in the same change.

## Execution Markers (`agents/status.md`)

These markers are consumed by `agents/orchestrate_loop.sh` and execution entrypoints.

| Marker | Primary emitter(s) | Purpose |
| --- | --- | --- |
| `### IDLE` | Orchestrator/reset paths | Neutral state between stages. |
| `### BUILDER_COMPLETE` | Builder/Hotfix paths | Builder-stage success (non-LARGE standard flow). |
| `### HOTFIX_COMPLETE` | Legacy `_orchestrate.md` compatibility paths | Legacy hotfix completion marker (not used by primary loop). |
| `### INTEGRATION_COMPLETE` | `_integrate.md` | Integration success. |
| `### QA_COMPLETE` | `_check.md`, `_doublecheck.md`, `_qa_execute.md` | QA success. |
| `### QUICKFIX_NEEDED` | `_check.md`, `_doublecheck.md`, `_qa_execute.md` | QA found fixable gaps. |
| `### UPDATE_COMPLETE` | `_update.md` or orchestrator synthesis | Update stage success. |
| `### TROUBLESHOOT_COMPLETE` | `_troubleshoot.md` | Local blocker remediation success. |
| `### CONSULT_COMPLETE` | `_consult.md` | Consult found local recovery path. |
| `### NEEDS_RESEARCH` | `_consult.md` / orchestrator recovery | Local path exhausted; quarantine + research handoff. |
| `### BLOCKED` | Any execution stage when hard-stopped | Deterministic stop with blocker handling. |
| `### LARGE_PLAN_COMPLETE` | `_start_large_plan.md` or orchestrator synthesis | LARGE stage 1 complete. |
| `### LARGE_EXECUTE_COMPLETE` | `_start_large_execute.md` or orchestrator synthesis | LARGE stage 2 complete. |
| `### LARGE_REASSESS_COMPLETE` | `prompts/reassess.md` or orchestrator synthesis | LARGE stage 3 complete. |
| `### LARGE_REFACTOR_COMPLETE` | `_refactor.md` or orchestrator synthesis | LARGE stage 4 complete. |

### Execution Transitions (Primary Loop)

- Standard path: `### IDLE` -> `### BUILDER_COMPLETE` -> `### INTEGRATION_COMPLETE` -> (`### QA_COMPLETE` or `### QUICKFIX_NEEDED`) -> `### IDLE`.
- Quickfix loop: `### QUICKFIX_NEEDED` -> Builder hotfix (`### BUILDER_COMPLETE`) -> QA recheck (`### QA_COMPLETE` or `### QUICKFIX_NEEDED`).
- Escalation path: hard failures -> `### BLOCKED` -> Troubleshoot (`### TROUBLESHOOT_COMPLETE`) or Consult (`### CONSULT_COMPLETE` / `### NEEDS_RESEARCH`).
- `### NEEDS_RESEARCH` always indicates quarantine + research handoff before resuming execution.

## LARGE Mode Marker Policy (FA-010)

Millrace uses FA-010 Option A (distinct stage markers) in `agents/status.md`:

1. `### LARGE_PLAN_COMPLETE`
2. `### LARGE_EXECUTE_COMPLETE`
3. `### LARGE_REASSESS_COMPLETE`
4. `### LARGE_REFACTOR_COMPLETE`

`agents/orchestrate_loop.sh` treats these as ordered stage-complete signals and determines the next LARGE stage from the current marker.

## Research Markers (`agents/research_status.md`)

These markers are consumed by `agents/research_loop.sh` and research entrypoints.

| Marker | Primary emitter(s) | Purpose |
| --- | --- | --- |
| `### IDLE` | Research loop + research stages | Neutral/no-work/success-complete state. |
| `### BLOCKED` | Research loop + research stages | Deterministic stop in research control plane. |
| `### GOAL_INTAKE_RUNNING` | `_goal_intake.md` | Condensed GoalSpec intake stage active. |
| `### COMPLETION_MANIFEST_RUNNING` | `_contractor.md` | Project-local completion manifest drafting active. |
| `### OBJECTIVE_PROFILE_SYNC_RUNNING` | `_objective_profile_sync.md` | Goal-derived acceptance/profile sync stage active. |
| `### SPEC_SYNTHESIS_RUNNING` | `_spec_synthesis.md` | Condensed GoalSpec synthesis stage active. |
| `### SPEC_INTERVIEW_RUNNING` | `_spec_interview.md` | Optional GoalSpec interview hardening stage active. |
| `### SPEC_REVIEW_RUNNING` | `_spec_review.md` | Condensed GoalSpec review stage active. |
| `### CLARIFY_RUNNING` | `_clarify.md` | Legacy GoalSpec clarify compatibility stage active. |
| `### TASKMASTER_RUNNING` | `_taskmaster.md` | GoalSpec taskmaster stage active. |
| `### TASKAUDIT_RUNNING` | `_taskaudit.md` | Task audit stage active. |
| `### CRITIC_RUNNING` | `_critic.md` | Critic stage active. |
| `### DESIGNER_RUNNING` | `_designer.md` | Designer stage active. |
| `### INCIDENT_INTAKE_RUNNING` | `_incident_intake.md` | Incident intake stage active. |
| `### INCIDENT_RESOLVE_RUNNING` | `_incident_resolve.md` | Incident resolution stage active. |
| `### INCIDENT_ARCHIVE_RUNNING` | `_incident_archive.md` | Incident archive stage active. |
| `### AUDIT_INTAKE_RUNNING` | `_audit_intake.md` | Audit intake stage active. |
| `### AUDIT_VALIDATE_RUNNING` | `_audit_validate.md` | Audit validate stage active. |
| `### AUDIT_RUNNING` | `research_loop.sh` audit orchestration | Marathon/task audit flow active. |
| `### AUDIT_PASS` | `research_loop.sh` | Audit flow passed. |
| `### AUDIT_FAIL` | `research_loop.sh` | Audit flow failed or needs remediation. |

### Research Transitions (Primary Loop)

- Stage contract: `<STAGE>_RUNNING` -> (`### IDLE` on success or `### BLOCKED` on failure).
- Audit contract: `### AUDIT_RUNNING` -> (`### AUDIT_PASS` or `### AUDIT_FAIL`), then loop normalization back to `### IDLE` when appropriate.

## Auxiliary Size Markers (`agents/size_status.md`)

These are orchestrator routing markers, not execution/research status ownership markers:

- `### SMALL`
- `### LARGE`

## unknown marker Behavior (Deterministic)

Execution loop (`agents/orchestrate_loop.sh`):
- If status is an unknown marker in the `### LARGE_*` namespace, the loop routes through blocker handling (no silent fallback).
- If status is an unexpected non-LARGE marker, the loop escalates via deterministic blocker handling.

Research loop (`agents/research_loop.sh`):
- After each stage run, only `### IDLE` and `### BLOCKED` are accepted as terminal stage outcomes.
- Any other terminal value is treated as an unknown marker / unexpected status and is coerced to `### BLOCKED`.

## Implementation References

- `agents/orchestrate_loop.sh` (execution status machine and LARGE stage routing)
- `agents/research_loop.sh` (research status machine and stage validation)
- LARGE stage entrypoints:
  - `agents/_start_large_plan.md`
  - `agents/_start_large_execute.md`
  - `agents/prompts/reassess.md`
  - `agents/_refactor.md`
