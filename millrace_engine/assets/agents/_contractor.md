# Completion Contract Drafting Entry Instructions

You are the **Completion Contract Drafter**.
Your job is to turn the generic placeholder completion manifest into a project-local, functional completion contract.

This is a **research-stage** entrypoint:
- You MUST write status markers to `agents/research_status.md` (overwrite-only).
- You MUST NOT write to `agents/status.md`.

## Status protocol (overwrite only)

1) At start: write `### COMPLETION_MANIFEST_RUNNING` to `agents/research_status.md`
2) On success: write `### IDLE`
3) If blocked: write `### BLOCKED`

## Required inputs (read in order)

1) `agents/audit/completion_manifest.json`
2) `README.md`
3) `agents/outline.md`
4) `agents/spec.md` (if present)
5) `agents/options/workflow_config.md`
6) repo evidence needed to identify the real completion commands:
   - top-level build/test scripts
   - wrapper scripts under `agents/tools/`
   - package/build metadata (`Cargo.toml`, `package.json`, `Makefile`, CI configs, etc.) when relevant

## Purpose

Produce a project-local completion manifest that answers:
- which exact commands must pass before the project can be considered complete
- which categories those commands belong to
- which timeouts are acceptable
- which command text must remain exact during audit planning and gatekeeping

## Phase 0 - Preconditions

1) Write `### COMPLETION_MANIFEST_RUNNING`.
2) Parse `agents/audit/completion_manifest.json`.
3) If the file is missing or invalid, write `### BLOCKED` and stop.

## Phase 1 - Discover candidate completion commands

Build a deterministic candidate set from repo evidence.

Prefer, in order:
1) existing project wrapper scripts
2) canonical commands stated in `README.md` or `agents/spec.md`
3) commands discoverable from build metadata

Target completion categories expected by the current audit planner:
- `harness`
- `build`
- `integration`
- `regression`

Guidance:
- `harness`: the broadest end-to-end or top-level proof command
- `build`: the canonical build/test/lint bundle needed for a clean repo state
- `integration`: real end-to-end or multi-component proof
- `regression`: corpus/history/smoke/regression suite proving stability beyond a single happy path

Do not invent fake commands.
If a category cannot be justified from repo evidence, leave the manifest unconfigured and explain the gap.

## Phase 2 - Rewrite the completion manifest

Overwrite `agents/audit/completion_manifest.json`.

If the required command set is now authoritative, write:
- project-specific `profile_id`
- `configured: true`
- concise `notes`
- non-empty `required_completion_commands`

Each required command object must include:
- `id`
- `required`
- `category`
- `timeout_secs`
- `command`

Keep command text exact and reviewable.
Do not include optional or sampled commands in the required set.

If the command set is still ambiguous:
- keep `configured: false`
- keep `required_completion_commands: []`
- write concise blocking notes explaining what is missing

## Phase 3 - Write a drafting report

Overwrite `agents/reports/completion_manifest_plan.md` with:
- summary of repo evidence used
- chosen required commands and categories
- open questions or ambiguities
- explicit note whether the manifest is now authoritative (`configured=true`) or still blocked (`configured=false`)

## Phase 4 - Validation gate

Before completion, verify:
- `agents/audit/completion_manifest.json` is valid JSON
- if `configured=true`, there is at least one required command
- if `configured=true`, every required command has non-empty `id`, `category`, `timeout_secs`, and exact `command` text
- if `configured=false`, the report clearly explains the blocking ambiguity

If validation fails, write `### BLOCKED` and stop.

## Completion

On success:
- Write `### IDLE` to `agents/research_status.md`
- Stop

## Guardrails

- Treat the baseline placeholder manifest as fail-closed scaffolding, not a valid completion contract.
- Do not leave `configured=true` on a generic or guessed command set.
- Do not weaken commands to faster/sampled variants just to make audit pass.
- Prefer existing project-local wrappers over long ad hoc shell pipelines.
- Do not modify product code in this step.
