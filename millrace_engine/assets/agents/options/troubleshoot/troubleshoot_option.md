# Troubleshoot Option (Troubleshoot-on-Blocker)

This option is intended to be used by `agents/_customize.md`.

Goal: when the Orchestrator/Runner hits a blocker, it can optionally spawn a dedicated Troubleshooter run to diagnose and apply the smallest fix that unblocks orchestration before requiring manual intervention.

This is expensive. It is intended to run on `gpt-5.3-codex` with **xhigh reasoning**.

## When this option is OFF (default)

- `agents/_orchestrate.md` must not mention Troubleshooting.
- Do not install `agents/_troubleshoot.md`.

If you use the local foreground runner (`agents/orchestrate_loop.sh`), do **not** use the Troubleshooter-enabled variant unless you also install `agents/_troubleshoot.md`.
Instead, use the no-troubleshooter variant at:
- `agents/options/troubleshoot/orchestrate_loop_no_ts.sh`
In this mode, hard blockers are auto-demoted to `agents/tasksbackburner.md`.

## When this option is ON

You must:
1) Install the Troubleshooter entrypoint into `agents/`
2) Patch `agents/_orchestrate.md` to invoke it on blockers (single attempt per blocker)

If you use the local foreground runner:
- Use `agents/orchestrate_loop.sh` (it will attempt a single Troubleshooter run per blocker).
- The no-troubleshooter variant (`agents/options/troubleshoot/orchestrate_loop_no_ts.sh`) remains available if you prefer to disable auto-remediation even with this option installed.
In either variant, hard blockers are auto-demoted to `agents/tasksbackburner.md` so the loop can continue.

## Files this option may touch

- `agents/_orchestrate.md`
- `agents/_troubleshoot.md` (installed by copying from `agents/options/troubleshoot/_troubleshoot.md`)

## Step 1: Install the entrypoint

Copy:
- from: `agents/options/troubleshoot/_troubleshoot.md`
- to:   `agents/_troubleshoot.md`

## Step 2: Patch `agents/_orchestrate.md`

Make minimal, mechanical edits:

1) Allowed sub-agent prompts list:
   - Add a new allowed entry for the Troubleshooter.
   - For Troubleshooter only, allow context to be appended in this exact format:

     `Open agents/_troubleshoot.md and follow instructions. For context: "<paste the orchestrator's blocker summary here>"`

2) Repo assumptions (required entrypoints list):
   - Add `agents/_troubleshoot.md`

3) State flags list (status.md contract):
   - Add `### TROUBLESHOOT_COMPLETE`

4) Blocker handling:
   - On any blocker condition:
     - create the diagnostics bundle (same as the normal blocker handler)
     - run Troubleshooter ONCE (single attempt per blocker) with a context string that includes:
       - where it blocked and why
       - diagnostics bundle path
   - If Troubleshooter writes `### TROUBLESHOOT_COMPLETE`:
     - clear status back to `### IDLE`
     - resume orchestration (re-run the blocked cycle)
   - If Troubleshooter writes `### BLOCKED`:
     - proceed with your normal "blocked" stop behavior:
       - create the diagnostics PR and stop

5) Prevent infinite loops:
   - Ensure the Orchestrator does not repeatedly re-run Troubleshooter for the same blocker.
   - Default behavior: 1 Troubleshooter attempt per blocker.

## Optional: Headless invocation template

See the Troubleshooter example in your headless templates file (the one referenced by `agents/_orchestrate.md`).

If your runner supports an explicit "xhigh reasoning" flag/setting, enable it for this run.
