# Update-on-Empty Option (Backlog Drain Finalizer)

This option is intended to be used by `agents/_customize.md`.

Goal: optionally run a final documentation maintenance cycle when
`agents/orchestrate_loop.sh` finds no remaining cards in `agents/tasksbacklog.md`.

## What this option does

- Enables/Disables update-on-empty behavior in the local foreground loop.
- Uses `agents/_update.md` as the final cycle entrypoint.
- Requires the Update cycle to signal `### UPDATE_COMPLETE` (or `### BLOCKED`) in `agents/status.md`.

## Workflow flag

Set in `agents/options/workflow_config.md`:
- `## RUN_UPDATE_ON_EMPTY=On` (enable)
- `## RUN_UPDATE_ON_EMPTY=Off` (disable)

Default is `On`.

## Model config keys

Configure in `agents/options/model_config.md` (Active config block):
- `UPDATE_RUNNER` (`codex|claude|openclaw`)
- `UPDATE_MODEL` (runner-specific model id/alias)

Default:
- `UPDATE_RUNNER=codex`
- `UPDATE_MODEL=gpt-5.3-codex`

The local loop runs Update with medium reasoning effort by default.

## Validation checklist

- `bash -n agents/orchestrate_loop.sh`
- Confirm `agents/_update.md` exists.
- Confirm a no-backlog loop run executes Update once and exits cleanly.
