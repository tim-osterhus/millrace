# Millrace Entrypoint Mapping

This document maps canonical draft entrypoints to packaged runtime assets and deployed workspace paths.

## Source -> Package -> Workspace

### Execution Plane

- `lab/specs/drafts/entrypoints/execution/builder.md` -> `millrace_ai/assets/entrypoints/execution/builder.md` -> `millrace-agents/entrypoints/execution/builder.md`
- `lab/specs/drafts/entrypoints/execution/checker.md` -> `millrace_ai/assets/entrypoints/execution/checker.md` -> `millrace-agents/entrypoints/execution/checker.md`
- `lab/specs/drafts/entrypoints/execution/fixer.md` -> `millrace_ai/assets/entrypoints/execution/fixer.md` -> `millrace-agents/entrypoints/execution/fixer.md`
- `lab/specs/drafts/entrypoints/execution/doublechecker.md` -> `millrace_ai/assets/entrypoints/execution/doublechecker.md` -> `millrace-agents/entrypoints/execution/doublechecker.md`
- `lab/specs/drafts/entrypoints/execution/updater.md` -> `millrace_ai/assets/entrypoints/execution/updater.md` -> `millrace-agents/entrypoints/execution/updater.md`
- `lab/specs/drafts/entrypoints/execution/troubleshooter.md` -> `millrace_ai/assets/entrypoints/execution/troubleshooter.md` -> `millrace-agents/entrypoints/execution/troubleshooter.md`
- `lab/specs/drafts/entrypoints/execution/consultant.md` -> `millrace_ai/assets/entrypoints/execution/consultant.md` -> `millrace-agents/entrypoints/execution/consultant.md`

### Planning Plane

- `lab/specs/drafts/entrypoints/planning/planner.md` -> `millrace_ai/assets/entrypoints/planning/planner.md` -> `millrace-agents/entrypoints/planning/planner.md`
- `lab/specs/drafts/entrypoints/planning/manager.md` -> `millrace_ai/assets/entrypoints/planning/manager.md` -> `millrace-agents/entrypoints/planning/manager.md`
- `lab/specs/drafts/entrypoints/planning/mechanic.md` -> `millrace_ai/assets/entrypoints/planning/mechanic.md` -> `millrace-agents/entrypoints/planning/mechanic.md`
- `lab/specs/drafts/entrypoints/planning/auditor.md` -> `millrace_ai/assets/entrypoints/planning/auditor.md` -> `millrace-agents/entrypoints/planning/auditor.md`

## Entrypoint Contract Expectations

- Entrypoints are plain markdown instruction files (no required YAML frontmatter).
- Stage agents are invoked against deployed workspace paths, for example:
  - `millrace-agents/entrypoints/execution/builder.md`
- Entrypoints read active work from request-provided paths such as:
  - `active_work_item_path`: `millrace-agents/tasks/active/<TASK_ID>.md`
  - `active_work_item_path`: `millrace-agents/specs/active/<SPEC_ID>.md`
  - `active_work_item_path`: `millrace-agents/incidents/active/<INCIDENT_ID>.md`
- Planning outputs that should be re-ingested target markdown queue surfaces:
  - `millrace-agents/specs/queue/<SPEC_ID>.md`
  - `millrace-agents/incidents/incoming/<INCIDENT_ID>.md`
- Run-scoped summaries and diagnostics belong under request-provided `run_dir`.
- Historical summary output goes to `millrace-agents/historylog.md`.

## Skill-Only Advisory Expectations

- Runtime ships `millrace-agents/skills/skills_index.md`, shared skill docs, and per-stage core skills.
- Each entrypoint includes `Required Stage-Core Skill`, `Optional Secondary Skills`, and `Suggested Operating Approach` sections.
- Entrypoints should direct stage agents to consult the skills index first, then load only relevant optional skills.
- Required stage-core skills and attached skill additions are compile-time surfaces and can be inspected via `millrace compile show`.

## Bootstrap Behavior

During workspace bootstrap, packaged entrypoints are copied to `<workspace>/millrace-agents/entrypoints/`.
Compiled stage requests always reference deployed workspace entrypoint paths so invoked agents can be told to open files from the workspace runtime tree directly.
