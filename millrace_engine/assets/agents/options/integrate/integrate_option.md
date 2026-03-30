# Integrate Option (Orchestrated Integration Cycles)

This option is intended to be used by `agents/_customize.md`.

Goal: optionally enable Integration cycles in `agents/_orchestrate.md` (using `agents/_integrate.md`) and align task authoring guidance so tasks only use the `INTEGRATION` gate when the orchestrator actually supports it.

Notes:
- `agents/_integrate.md` stays available for **manual** runs regardless of this option.
- The orchestrator should not mention Integration unless this option is enabled (Low/Medium/High).
- Integration counters and mode live in `agents/options/workflow_config.md` (flags only).

## User choice (from _customize)

Integration mode:
- Manual (opt out): no orchestrated Integration cycles
- Low: Integration runs only when a task is gated `INTEGRATION`
- Medium: Integration runs on `INTEGRATION` tasks and periodically every 3-6 tasks
- High: Integration runs every other task

## Files this option may touch

- `agents/options/workflow_config.md` (flags only)
- `agents/_orchestrate.md` (install Integration cycle + signals + prompt allowlist)
- `agents/prompts/decompose.md` (add/remove Integration gating guidance)
- `agents/skills/task-card-authoring-repo-exact/SKILL.md` (add/remove `INTEGRATION` as an allowed gate)

## Step 1: Set workflow flags (always)

Edit only the `## KEY=value` lines in `agents/options/workflow_config.md`:

- Set `## INITIALIZED=true`
- Set `## INTEGRATION_MODE=<Manual|Low|Medium|High>`
- Reset `## INTEGRATION_COUNT=0`
- Set `## INTEGRATION_TARGET`:
  - Manual: 0
  - Low: 0
  - Medium: 4 (periodic target rotates 3->4->5->6->3 when Integration runs)
  - High: 1

## Step 2: Apply mode behavior

### Manual (opt out)

Leave the repo in "no Integration" orchestrator mode:
- Do NOT add Integration instructions to `agents/_orchestrate.md`.
- Do NOT add the `INTEGRATION` gate guidance to task authoring prompts/skills.

This ensures tasks created under this mode do not accidentally require Integration Reports that the orchestrator will not produce.

### Low / Medium / High (enable orchestrated Integration)

You must align three surfaces:
1) `agents/_orchestrate.md` (runner behavior)
2) task authoring guidance (`agents/prompts/decompose.md`)
3) task card gate vocabulary (`agents/skills/task-card-authoring-repo-exact/SKILL.md`)

#### A) Update `agents/_orchestrate.md` (install Integration)

Make minimal, mechanical edits:

1) Allowed sub-agent prompts list:
   - Add:
     `Open agents/_integrate.md and follow instructions.`

2) Repo assumptions (required entrypoints list):
   - Add `agents/_integrate.md`

3) State flags list (status.md contract):
   - Add `### INTEGRATION_COMPLETE`

4) Main loop:
   - Insert an Integration cycle between Builder and QA.
   - Mode logic:
     - Low: run Integration only when the active task has `**Gates:** INTEGRATION`
     - Medium: run Integration when `INTEGRATION` gate is present OR when `INTEGRATION_COUNT >= INTEGRATION_TARGET`
     - High: run Integration when `INTEGRATION_COUNT >= 1` (every other task); keep `INTEGRATION_TARGET=1`
   - When Integration runs:
     - wait for `### INTEGRATION_COMPLETE` or `### BLOCKED`
     - clear status back to `### IDLE`
     - confirm an Integration Report exists at `agents/runs/<RUN_ID>/integration_report.md` or `agents/integration_report.md`

5) Finalize (after QA_COMPLETE):
   - Update `agents/options/workflow_config.md` counters:
     - If Integration ran this cycle:
       - set `INTEGRATION_COUNT=0`
       - if mode is Medium, advance `INTEGRATION_TARGET` 3->4->5->6->3
     - Else: increment `INTEGRATION_COUNT` by 1

#### B) Update `agents/prompts/decompose.md` (task authoring guidance)

Add (or re-add) Integration gating guidance so the Advisor can tag tasks appropriately:
- Add `INTEGRATION` for cross-cutting tasks or feature clusters.

#### C) Update `agents/skills/task-card-authoring-repo-exact/SKILL.md` (gate vocabulary)

Ensure `INTEGRATION` is an allowed gate only when Integration is enabled.

Add back:
- `INTEGRATION` - run an integration sweep/report after implementing a feature cluster or cross-cutting change

Update the task card template gate line to allow `INTEGRATION` again.

## Verification checklist (quick)

- `agents/options/workflow_config.md` has updated flags (and no long prose).
- If mode is Manual:
  - `agents/_orchestrate.md` does not mention Integration.
  - Task authoring docs do not recommend the `INTEGRATION` gate.
- If mode is Low/Medium/High:
  - `agents/_orchestrate.md` includes Integration cycle + `### INTEGRATION_COMPLETE`.
  - Task authoring docs and gate vocabulary include `INTEGRATION`.
