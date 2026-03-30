# Headless Permissions Option

This option is intended to be used by `agents/_customize.md`.

Goal: select the permission level for headless sub-agents launched by the orchestrator templates.

The chosen level is stored in:
- `agents/options/workflow_config.md` as `## HEADLESS_PERMISSIONS=<Normal|Elevated|Maximum>`

## Permission Levels (Codex CLI)

Normal (recommended):
- Uses: `--full-auto`
- Lowest risk. Best for repos that do not need port binds, IPC sockets, or Docker access.

Elevated:
- Uses: `--full-auto --sandbox danger-full-access`
- Allows more OS capabilities (ports, IPC, docker socket access) while still sandboxed.

Maximum:
- Uses: `--full-auto --dangerously-bypass-approvals-and-sandbox`
- Removes sandbox protections. Use only in trusted environments.

## Permission Levels (Claude Code CLI)

Normal:
- No extra permission flags (default behavior).

Elevated:
- Uses: `--permission-mode acceptEdits`
- Accepts file edits automatically, but still prompts for higher-risk tool actions.

Maximum:
- Uses: `--dangerously-skip-permissions`
- Skips permission prompts. Use only in trusted environments.

## Required Edits

1) Set the flag:
   - Update `agents/options/workflow_config.md`:
     - `## HEADLESS_PERMISSIONS=<Normal|Elevated|Maximum>`

2) Apply to the headless templates:
   - Edit the headless templates file referenced in `agents/_orchestrate.md` so that:
     - Codex commands include the matching permission flags.
     - Claude commands include the matching permission flags.

## Notes

- This option affects **headless** runs only. It does not change interactive sessions.
- Keep changes minimal and auditable.
