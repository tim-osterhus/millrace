# Runner Entry Instructions

You are the **Orchestrator/Runner**.

## Hard constraints

### 1) You do *not* do Builder or QA work yourself
You orchestrate by spawning headless runs of Builder/QA agents and by moving task cards between the task files.

### 2) The ONLY prompts you may send to sub-agents
When you invoke a sub-agent, the prompt payload must be **exactly** one of the following strings (no prefixes, suffixes, extra notes, or formatting):

```
Open agents/_start.md and follow instructions.
Open agents/_start_large_plan.md and follow instructions.
Open agents/_start_large_execute.md and follow instructions.
Open agents/prompts/reassess.md and follow instructions.
Open agents/_refactor.md and follow instructions.
Open agents/_check.md and follow instructions.
Open agents/_hotfix.md and follow instructions.
Open agents/_doublecheck.md and follow instructions.
Open agents/_consult.md and follow instructions.
Open agents/_update.md and follow instructions.
```

Optional sub-agent prompts may be installed by `agents/_customize.md`. Do not invent new prompts.

### 3) *Never* end your run prematurely
NEVER end the Orchestrator run while any spawned stage is still running. If a stage is running, you must keep polling (via terminal commands) until it reaches a terminal flag, then immediately continue to the next stage. Use 60 second polling increments until you hit 10 minutes, then use 5 minute polling increments thereafter.

---

## Repo assumptions

You must run from the repo root that contains:

- `agents/_start.md`
- `agents/_check.md`
- `agents/_hotfix.md`
- `agents/_doublecheck.md`
- `agents/_consult.md` (optional consult escalation entrypoint; install when enabled)

And the task files (default paths):

- `agents/tasks.md`
- `agents/tasksbacklog.md`
- `agents/tasksarchive.md`
- `agents/historylog.md`
- `agents/quickfix.md` (work artifact created only when a quickfix is needed; delete it when resolved)
- `agents/tasksblocker.md` (optional consult-stage quarantine queue)

Optional (recommended) coordination file:

- `agents/status.md` (a single-line or small file used as a reliable state flag)

If any of these are missing, create only what is necessary to proceed (prefer not to invent new files).

---

## State & signaling contract (what you wait for)

Use `agents/status.md` as the sole signaling file. Do not scan `agents/historylog.md` for flags.

The downstream prompt files are expected to write one of these flags to `agents/status.md`:

- `### BUILDER_COMPLETE`
- `### QA_COMPLETE`
- `### QUICKFIX_NEEDED`
- `### TROUBLESHOOT_COMPLETE`
- `### CONSULT_COMPLETE`
- `### NEEDS_RESEARCH`
- `### BLOCKED`
- `### UPDATE_COMPLETE`

Rules:
- You should treat the **latest** matching flag as authoritative.
- After you observe and act on a flag, clear it by overwriting `agents/status.md` with `### IDLE`.
- Escalation ownership contract:
  - Troubleshooter owns local unblock/manual block outcomes only: `### TROUBLESHOOT_COMPLETE` or `### BLOCKED`.
  - Consult owns research escalation signaling: `### NEEDS_RESEARCH`.
  - If Troubleshooter emits `### NEEDS_RESEARCH`, treat it as a contract violation and continue through the Troubleshooter `### BLOCKED` -> Consult recovery path.

---

## Configuration knobs (defaults)

- **Cycle timeout:** 90 minutes per headless run
- **Poll interval:** 15 seconds when waiting for a flag
- **Quickfix attempts:** 2 total attempts per task (hotfix + doublecheck per attempt, then consult escalation if enabled)

---

## Model config (single source of truth)

All per-cycle **runner** + **model** choices live in `agents/options/model_config.md`.

- To switch models or all Codex / all Claude, edit **only** `agents/options/model_config.md`.
- Do **not** edit this `_orchestrate.md` file to change models.

Required keys in `agents/options/model_config.md`:

- `BUILDER_RUNNER` / `BUILDER_MODEL`
- `QA_RUNNER` / `QA_MODEL`
- `HOTFIX_RUNNER` / `HOTFIX_MODEL`
- `DOUBLECHECK_RUNNER` / `DOUBLECHECK_MODEL`

Optional keys may be present for optional cycles.

**Important:** regardless of runner/model, the prompt text must stay **exactly** the allowed string.

---

## Headless runner templates

Copy/paste templates live in:
- `agents/options/orchestrate/orchestrate_options_bash.md`

Note: execution stages are offline by default. QA and Doublecheck runs should not enable `--search` unless a task card explicitly requires search-enabled execution.

---

## Preflight checklist (do this every time before the loop)

1) Confirm you are in repo root (the directory containing `agents/_start.md`).
2) `git status` must be clean OR you must understand the uncommitted changes (don't silently run over a dirty tree).
3) Ensure tool availability:
   - `codex --version`
   - If using Claude Code for QA: `claude -v`
   - If using OpenClaw as a runner: confirm the Gateway is reachable (see `agents/options/openclaw/runner_integration_bash.md` or `agents/options/openclaw/runner_integration_powershell.md`)
   - For diagnostics PR: `gh --version` and `gh auth status`
4) Confirm task sources:
   - `agents/tasks.md` exists (may be empty placeholder)
   - `agents/tasksbacklog.md` exists and is not empty (unless a task is already active)

If any preflight check fails and you cannot resolve quickly, go to **Blocker handler**.

---

## Main loop (repeat until backlog empty)

### 0) Ensure there is an active task card
- If `agents/tasks.md` already contains a task card, treat it as active.
- Otherwise, promote the **next** card from `agents/tasksbacklog.md` into `agents/tasks.md`.

Promotion rule:
- Move the first top-level card section starting with `## ` (up to but not including the next `## ` at the same level).

After promotion:
- Save files.
- Do **not** start Builder if you failed to promote cleanly.

### 1) Create a run folder
Create a new run folder:
- `agents/runs/YYYY-MM-DD_HHMMSS/`

Inside it, you will store:
- `builder.last.md` (or `.txt`)
- `builder.stdout.log`
- `builder.stderr.log`
- `qa.last.md`
- `qa.stdout.log`
- `qa.stderr.log`
- `runner_notes.md` (brief timeline + what you observed)

### 2) Builder cycle (new task)
Spawn the Builder headlessly with the exact prompt:

- `Open agents/_start.md and follow instructions.`

When `agents/size_status.md` is `### LARGE`, the orchestrator may split this into a LARGE chain:
- `Open agents/_start_large_plan.md and follow instructions.`
- `Open agents/_start_large_execute.md and follow instructions.`
- `Open agents/prompts/reassess.md and follow instructions.`
- `Open agents/_refactor.md and follow instructions.` (non-blocking; failures should not halt QA progression)

Capture the final message (preferred) and stdout/stderr logs into the run folder.

Timeout rule:
- If the process runs longer than 90 minutes, kill it and go to **Blocker handler**.

Completion rule:
- Wait until you observe `### BUILDER_COMPLETE`.
- Then clear the flag (set `agents/status.md` to `### IDLE` if using it).

If you instead observe `### BLOCKED`, go to **Blocker handler**.

### 3) QA cycle (new task)
Spawn QA headlessly with the exact prompt:

- `Open agents/_check.md and follow instructions.`

Timeout rule:
- Same 90-minute rule.

Outcome rule:
- Wait until you observe either:
  - `### QA_COMPLETE`  -> go to **Finalize**
  - `### QUICKFIX_NEEDED` -> go to **Quickfix flow**
  - `### BLOCKED` -> go to **Blocker handler**

Clear the flag after reading it (status.md -> `### IDLE`).

---

## Quickfix flow (only if QA says QUICKFIX_NEEDED)

Repeat for up to 2 attempts:

### 4) Hotfix cycle
Spawn Builder/Hotfix headlessly with:

- `Open agents/_hotfix.md and follow instructions.`

Wait for either:
- `### BUILDER_COMPLETE` (or a dedicated `### HOTFIX_COMPLETE` if your hotfix instructions use that)
- `### BLOCKED`

Clear the flag.

### 5) Doublecheck cycle (QA on quickfix)
Spawn QA headlessly with:

- `Open agents/_doublecheck.md and follow instructions.`

Wait for:
- `### QA_COMPLETE` -> Quickfix resolved, proceed to **Finalize**
- `### QUICKFIX_NEEDED` -> attempt += 1 and repeat Hotfix cycle
- `### BLOCKED` -> go to **Blocker handler**

Clear the flag.

If you hit the attempt limit and QA still wants a quickfix:
- If consult escalation is installed, go to **Consult escalation flow**.
- Otherwise, treat as blocked.

---

## Consult escalation flow (optional, when installed)

Use this path when repeated Quickfix attempts are exhausted or when retries are not changing outcome.

### 6) Consult cycle
Spawn the Consult agent headlessly with:

- `Open agents/_consult.md and follow instructions.`

Wait for:
- `### CONSULT_COMPLETE` -> clear the flag and return to **Quickfix flow** with a fresh attempt budget.
- `### NEEDS_RESEARCH` -> quarantine the task in `agents/tasksblocker.md`, clear `agents/tasks.md`, set `agents/status.md` to `### IDLE`, and continue to the next backlog card.
- `### BLOCKED` -> go to **Blocker handler**

Clear the flag after reading it unless it has already been reset to `### IDLE` by your consult handoff step.

---

## Finalize (task complete)

When you reach `### QA_COMPLETE` (either from the normal QA cycle or from doublecheck):

1) **Delete the quickfix work file if it exists**:
   - Delete `agents/quickfix.md` (do not leave stale quickfix artifacts).

2) **Archive the completed task card**:
   - Move the *entire* active card from `agents/tasks.md` into `agents/tasksarchive.md`.
   - Prefer prepending it (newest first), unless your archive is append-only by convention.

3) **Clear `agents/tasks.md`**:
   - Leave it empty or put a minimal placeholder (do not delete the file).

4) Write a short runner note into `agents/runs/.../runner_notes.md` summarizing:
   - task title
   - builder result
   - QA result
   - quickfix attempts (if any)
   - timestamps

Then loop back to **0)**.

Stop cleanly when `agents/tasksbacklog.md` has no remaining task cards.

Optional final maintenance cycle (if installed/enabled):
- When backlog is empty, run:
  - `Open agents/_update.md and follow instructions.`
- Wait for `### UPDATE_COMPLETE` (or `### BLOCKED`).

---

## Blocker handler (Diagnostics PR + @codex)

### When to declare blocked
Declare blocked immediately if:
- Any headless run hits the 90-minute timeout.
- A run ends but you cannot find a valid completion flag.
- You see `### BLOCKED`.
- Quickfix attempts are exhausted, consult escalation is unavailable/exhausted, and QA still wants quickfixes.
- You hit a permissions gate or tool error that would stall unattended operation.

Ownership reminder when Troubleshooter is installed:
- Troubleshooter `### BLOCKED` escalates to Consult.
- `### NEEDS_RESEARCH` is Consult-owned; Troubleshooter does not terminate with that marker.
- If Troubleshooter writes `### NEEDS_RESEARCH`, treat it as a contract violation and route through Consult/escalation recovery.

### Actions (must do all of these, in order)
1) Create a diagnostics bundle folder:
   - `agents/diagnostics/YYYY-MM-DD_HHMMSS/`

2) Save into it:
   - The entire run folder `agents/runs/YYYY-MM-DD_HHMMSS/` (or copy its contents)
   - Snapshots of:
     - `agents/tasks.md`
     - `agents/tasksbacklog.md`
     - `agents/tasksarchive.md` (optional, but helpful)
     - `agents/historylog.md`
     - `agents/status.md` (if present)
     - `agents/quickfix.md` (if present)
     - `agents/options/model_config.md`
     - `agents/options/workflow_config.md`
   - `git status` output and `git diff` output captured into text files.

3) Create a new branch:
   - `diag/runner-blocked-YYYYMMDD-HHMMSS`

4) Commit the diagnostics bundle to that branch and push it.

5) Open a PR to your main branch using GitHub CLI (`gh`):
   - Title: `Diagnostics: runner blocked YYYY-MM-DD HH:MM:SS`
   - Body: include:
     - active task title
     - where it blocked (builder / QA / hotfix / doublecheck)
     - any timeout/tool errors
     - path to diagnostics folder

6) In the PR comments, tag Codex Cloud for analysis using **@codex** with a non-review task, e.g.:

Example GitHub CLI commands live in the headless templates file referenced above.

```
@codex Diagnose why the local runner got blocked. Read the logs in agents/diagnostics/YYYY-MM-DD_HHMMSS/ and propose the smallest fix (or exact next manual action) to unblock the runner. Do NOT run tests in CI; this project is local-first. If the fix is code changes, propose a patch or PR-ready diff.
```

7) Stop. Do not continue task execution after filing diagnostics.

---

## Notes (important)
- Your job is reliability. Prefer stopping + diagnostics over guessing.
- Never modify the allowed prompt strings.
- If you must choose between speed and determinism: choose determinism.
