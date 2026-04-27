# Millrace Entrypoint Mapping

This document maps the current entrypoint source of truth to the deployed
workspace paths used at runtime.

Millrace currently authors packaged entrypoints directly under
`src/millrace_ai/assets/entrypoints/`. This repo does not maintain a separate
pre-packaging draft tree for entrypoints.

## Packaged Source -> Workspace

### Execution Plane

- `src/millrace_ai/assets/entrypoints/execution/builder.md` -> `millrace-agents/entrypoints/execution/builder.md`
- `src/millrace_ai/assets/entrypoints/execution/checker.md` -> `millrace-agents/entrypoints/execution/checker.md`
- `src/millrace_ai/assets/entrypoints/execution/fixer.md` -> `millrace-agents/entrypoints/execution/fixer.md`
- `src/millrace_ai/assets/entrypoints/execution/doublechecker.md` -> `millrace-agents/entrypoints/execution/doublechecker.md`
- `src/millrace_ai/assets/entrypoints/execution/updater.md` -> `millrace-agents/entrypoints/execution/updater.md`
- `src/millrace_ai/assets/entrypoints/execution/troubleshooter.md` -> `millrace-agents/entrypoints/execution/troubleshooter.md`
- `src/millrace_ai/assets/entrypoints/execution/consultant.md` -> `millrace-agents/entrypoints/execution/consultant.md`

### Planning Plane

- `src/millrace_ai/assets/entrypoints/planning/planner.md` -> `millrace-agents/entrypoints/planning/planner.md`
- `src/millrace_ai/assets/entrypoints/planning/manager.md` -> `millrace-agents/entrypoints/planning/manager.md`
- `src/millrace_ai/assets/entrypoints/planning/mechanic.md` -> `millrace-agents/entrypoints/planning/mechanic.md`
- `src/millrace_ai/assets/entrypoints/planning/auditor.md` -> `millrace-agents/entrypoints/planning/auditor.md`
- `src/millrace_ai/assets/entrypoints/planning/arbiter.md` -> `millrace-agents/entrypoints/planning/arbiter.md`

### Learning Plane

- `src/millrace_ai/assets/entrypoints/learning/analyst.md` -> `millrace-agents/entrypoints/learning/analyst.md`
- `src/millrace_ai/assets/entrypoints/learning/professor.md` -> `millrace-agents/entrypoints/learning/professor.md`
- `src/millrace_ai/assets/entrypoints/learning/curator.md` -> `millrace-agents/entrypoints/learning/curator.md`

## Entrypoint Contract Expectations

- Entrypoints are plain markdown instruction files (no required YAML frontmatter).
- Stage agents are invoked against deployed workspace paths, for example:
  - `millrace-agents/entrypoints/execution/builder.md`
- Entrypoints read active work from request-provided paths such as:
  - `active_work_item_path`: `millrace-agents/tasks/active/<TASK_ID>.md`
  - `active_work_item_path`: `millrace-agents/specs/active/<SPEC_ID>.md`
  - `active_work_item_path`: `millrace-agents/incidents/active/<INCIDENT_ID>.md`
  - `active_work_item_path`: `millrace-agents/learning/requests/active/<REQUEST_ID>.md`
- Closure-target-driven audits read request-provided closure state such as:
  - `closure_target_path`: `millrace-agents/arbiter/targets/<ROOT_SPEC_ID>.json`
- Planning outputs that should be re-ingested target markdown queue surfaces:
  - `millrace-agents/specs/queue/<SPEC_ID>.md`
  - `millrace-agents/incidents/incoming/<INCIDENT_ID>.md`
- Run-scoped summaries and diagnostics belong under request-provided `run_dir`.
- Historical summary output goes to `millrace-agents/historylog.md`.

## Skill-Only Advisory Expectations

- Runtime ships `millrace-agents/skills/skills_index.md`, shared skill docs, and per-stage core skills.
- Each entrypoint includes `Required Stage-Core Skill`, `Optional Secondary Skills`, and `Suggested Operating Approach` sections.
- Required stage-core skills are runtime-assigned by compiled stage requests; they are not discretionary operator choices.
- Optional secondary skills are advisory additions and must exist in the packaged or installed skills surface before an entrypoint can reference them.
- `millrace-agents/skills/skills_index.md` lists packaged skills and points to the supported downloadable optional-skills directory at `https://github.com/tim-osterhus/millrace-skills/blob/main/index.md`.
- Entrypoints should direct stage agents to consult the deployed skills index first, then load only relevant optional skills that are actually present.
- Required stage-core skills and optional attached skills are compile-time
  surfaces and can be inspected via `millrace compile show` as
  `required_skills` and `attached_skills`.

## Bootstrap Behavior

During `millrace init`, packaged entrypoints are copied to `<workspace>/millrace-agents/entrypoints/`.
During `millrace upgrade --apply`, safe managed entrypoint updates are applied according to the workspace baseline manifest.
Compiled stage requests always reference deployed workspace entrypoint paths so invoked agents can be told to open files from the workspace runtime tree directly.
