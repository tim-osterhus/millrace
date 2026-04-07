# Millrace Operator Intake Control Examples

## EX-2026-04-07-01: Over-specified product idea collapsed into GoalSpec administration

**Tags**: `intake`, `goalspec`, `contamination`, `product-scope`

**Trigger phrases**:
- "tell GoalSpec how to process this"
- "mention the runtime stages in the idea body"
- "the output only touches agents/*"

**Date**: 2026-04-07

**Problem**: A concrete product request was seeded with process-language contamination and then reproduced even after reseeding, revealing that over-specified intake was a real operator hazard and an easy first place for scope collapse to start.

**Impact**: The runtime produced GoalSpec-administration artifacts instead of product-grounded work, and tasks targeted `agents/*` surfaces instead of the actual repo surface the user cared about.

**Cause**: The intake body mixed product intent with internal-routing language, which made it easier for downstream synthesis to preserve Millrace-process nouns instead of product nouns.

**Fix**: Rewrite the idea as a clean product-only seed that states what should exist or be validated, with no stage names, no routing advice, and no instructions about how Millrace should decompose it.

**Prevention**: If an idea body mentions GoalSpec, Spec Review, Taskmaster, audit, or size labels, stop and rewrite before seeding.

**References**: `work/failure-mode.md`, `work/SKILL.md`

## EX-2026-04-07-02: Task seed phrased as acceptance criteria instead of a runbook

**Tags**: `task-intake`, `acceptance-criteria`, `execution`

**Trigger phrases**:
- "write the task like a runbook"
- "force SMALL or LARGE"
- "tell the runtime which stages to run"

**Date**: 2026-04-07

**Problem**: A task draft tried to prescribe stage order, size routing, and implementation steps instead of describing the desired outcome.

**Impact**: The task became brittle, harder for the runtime to route correctly, and more likely to confuse execution planning with operator instructions.

**Cause**: The author treated the task as a script for Millrace instead of a bounded execution goal with acceptance criteria.

**Fix**: Rewrite the task so it states the user-visible or repo-visible outcome, the acceptance criteria, and any hard constraints, while leaving staging and routing to Millrace.

**Prevention**: For tasks, ask "what does done look like?" If the answer is a sequence of internal runtime steps, the draft is wrong.

**References**: `millrace/ADVISOR.md`, `millrace/millrace_engine/assets/ADVISOR.md`

## EX-2026-04-07-03: External harness stayed on the supervisor seam

**Tags**: `supervisor`, `role-boundary`, `issuer`, `control-plane`

**Trigger phrases**:
- "external harness wants to pause or add work"
- "should I use advisor commands from outside the workspace"
- "thinking about editing mailbox files directly"

**Date**: 2026-04-07

**Problem**: An external supervisor-style caller needed to observe state, queue work, and clean up invalid queued work but was about to use local-operator commands or raw runtime files instead of the supported supervisor contract.

**Impact**: Bypassing the supervisor seam would have lost issuer attribution and risked unsafely mutating runtime-owned state.

**Cause**: The role boundary between local Advisor operation and external Supervisor control was not treated as a first-class command-selection rule.

**Fix**: Start with `millrace --config millrace.toml supervisor report --json`, then use the matching `supervisor ... --issuer <name> --json` command for any mutation, including `supervisor cleanup remove|quarantine` when queued work must be corrected.

**Prevention**: External harnesses should treat `supervisor report --json` plus issuer-attributed supervisor actions as the only normal mutation surface.

**References**: `millrace/SUPERVISOR.md`, `millrace/millrace_engine/assets/SUPERVISOR.md`
