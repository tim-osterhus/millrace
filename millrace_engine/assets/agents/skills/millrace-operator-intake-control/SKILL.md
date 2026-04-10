---
name: millrace-operator-intake-control
description: >
  Guides Millrace agents on choosing the right command family, writing clean idea/task intake, and staying inside Advisor vs Supervisor role boundaries. Use it before `add-idea`, `add-task`, `supervisor add-task`, `queue reorder`, or when an agent starts prescribing Millrace internals instead of the desired outcome.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Grep", "Bash", "Write"]
  offline_ok: true
---

# Millrace Operator Intake Control

## Purpose
Use the correct Millrace command surface and keep intake bodies product-focused. This skill exists to stop the most common operating failure: agents over-specifying Millrace internals and corrupting the runtime's own staging and routing logic.

## Quick start
Goal:
- choose the right role/command family and hand Millrace a clean what-not-how seed

Use when (triggers):
- before `millrace add-idea`, `millrace add-task`, or `millrace supervisor add-task`
- before `millrace queue reorder` or `millrace supervisor queue-reorder`
- before `millrace queue cleanup ...` or `millrace supervisor cleanup ...`
- when deciding whether the caller is an Advisor or Supervisor
- when an intake draft starts naming stages like GoalSpec, Spec Review, Taskmaster, audit, or quickfix

Do NOT use when (non-goals):
- implementing new cleanup commands or changing runtime control-plane behavior
- deep role-specific onboarding beyond the shipped Advisor/Supervisor entrypoints

## Operating constraints
- Treat ideas and tasks as seeds, not scripts.
- Prefer the smallest supported command surface over raw file edits.
- Keep runtime-owned files read-only during normal operation.
- Use issuer-attributed supervisor commands for external harness mutations.
- Use shipped cleanup commands for queued-work correction, and do not invent broader file-deletion flows by editing runtime state directly.

## Inputs this Skill expects
Required:
- the role you are acting in: local Advisor or external Supervisor
- the draft idea/task body or the control action you are considering

Optional:
- `ADVISOR.md` or `SUPERVISOR.md`
- `work/failure-mode.md` or another concrete failure example

If required inputs are missing:
- assume the safest boundary: Advisor for local shell work, Supervisor for external report-polling harnesses, and ask for clarification before mutating runtime state.

## Output contract
Primary deliverable:
- a command choice that matches the current role plus an intake body that describes the desired outcome without prescribing Millrace internals

Secondary deliverables (only if needed):
- a short explanation of why a draft intake is invalid
- a corrected idea/task seed or a safer command recommendation

Definition of DONE (objective checks):
- [ ] The selected command family matches the current role boundary.
- [ ] The intake body describes what should exist or be verified, not how Millrace should stage or route it.
- [ ] No runtime-owned file edits are proposed when a shipped control surface should be used instead.

## Procedure (copy into working response and tick off)
Progress:
- [ ] 1) Confirm role and action type
- [ ] 2) Pick the supported command family
- [ ] 3) Lint the intake body for contamination
- [ ] 4) Rewrite to outcome-first wording if needed
- [ ] 5) Check cleanup and mutation boundaries
- [ ] 6) Hand off the final command/body

### 1) Confirm role and action type
- Advisor: local workspace operator shell, broader CLI/TUI diagnosis, normal CLI mutation.
- Supervisor: external one-workspace harness using `supervisor report --json` plus issuer-attributed supervisor actions.
- Decide whether this is observation, intake, queue ordering, lifecycle control, or unsupported cleanup.

### 2) Pick the supported command family
- Observation:
  - Advisor: `health`, `status --detail --json`, `queue inspect --json`, `research --json`, `logs`
  - Supervisor: `supervisor report --json` first, then supporting read-only inspection only if needed
- Intake:
  - idea seed: `millrace add-idea /absolute/path/to/idea.md`
  - local task seed: `millrace add-task "..."`
  - external task seed: `millrace --config millrace.toml supervisor add-task "..." --issuer <name> --json`
- Queue ordering:
  - local: `millrace queue reorder <task-id> <task-id> ...`
  - external: `millrace --config millrace.toml supervisor queue-reorder <task-id> <task-id> ... --issuer <name> --json`
- Cleanup:
  - local: `millrace queue cleanup remove|quarantine <task-id> --reason "..."`
  - external: `millrace --config millrace.toml supervisor cleanup remove|quarantine <task-id> --issuer <name> --reason "..." --json`
- Lifecycle:
  - local: `start`, `pause`, `resume`, `stop`
  - external: `supervisor pause`, `supervisor resume`, `supervisor stop` with `--issuer`

### 3) Lint the intake body for contamination
Reject or rewrite the draft if it:
- names Millrace stages or routing decisions
- tells the runtime to decompose itself in a particular way
- forces size labels like SMALL or LARGE
- reads like a runbook instead of an outcome
- asks for raw task-store or mailbox edits

### 4) Rewrite to outcome-first wording if needed
- Idea rule: one or two sentences of intent about what should be understood, investigated, or made possible.
- Task rule: describe the bounded outcome and acceptance criteria, not the internal sequence.
- Keep product nouns, capability nouns, repo surface, and validation obligations intact.

### 5) Check cleanup and mutation boundaries
- Do not edit `agents/.runtime/`, mailbox files, task stores, or runtime status files directly during normal operation.
- Use only the shipped cleanup commands for queued-work correction.
- If the requested action is cleanup without a supported command, stop at diagnosis and escalation instead of mutating files by hand.

### 6) Hand off the final command/body
- Return the exact supported command to use.
- Return the final idea/task text separately from any explanation.
- If you rejected a draft, name the contamination pattern briefly and point to the corrected version.

## Pitfalls / gotchas (keep this brutally honest)
- Helpful stage narration is still contamination if it enters the idea/task body.
- External harnesses that skip `supervisor report --json` first tend to act on stale assumptions.
- Manual file surgery on runtime-owned surfaces creates state drift that later commands cannot audit cleanly.

## Progressive disclosure (one level deep)
If this Skill needs detail, link it directly from HERE (avoid chains of references):
- Examples and failure patterns: `./EXAMPLES.md`

## Verification pattern (recommended for medium/high risk)
Use this when the intake or command choice feels ambiguous:
1) identify the role
2) identify the supported command family
3) strip all how-language from the draft
4) confirm the final text still preserves the actual product or task nouns
5) only then submit the command

## Example References (concise summaries only)

1. **Over-specified product idea collapsed into GoalSpec administration** - A product request that named internal handling language drifted into meta artifacts; the fix was a clean product-only seed. See EXAMPLES.md (EX-2026-04-07-01)
2. **Task seed phrased as acceptance criteria instead of a runbook** - The safe version describes a bounded outcome and tests, not the runtime stages. See EXAMPLES.md (EX-2026-04-07-02)
3. **External harness stayed on the supervisor seam** - Poll first, then use issuer-attributed supervisor commands instead of local operator or raw-file actions. See EXAMPLES.md (EX-2026-04-07-03)

**Note:** Full examples with tags and trigger phrases are in `./EXAMPLES.md`.
