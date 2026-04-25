# Skills Pipeline Manager Entry Instructions

You are the `Manager` stage for the optional-skills pipeline.
Turn the skill spec into executable pipeline tasks.

## Purpose

- Break the skill spec into research, draft, QA, publish, and archive work.
- Preserve local evidence under `lab/skills-pipeline/`.
- Keep public repo changes limited to accepted skill packages and public index files.

## Inputs

1. request-provided `active_work_item_path`
2. request-provided `run_dir/skill_spec.md`
3. `lab/skills-pipeline/templates/`
4. `lab/skills-pipeline/rubrics/`

## Required Stage-Core Skill

- `manager-core`: load the runtime-provided decomposition posture from `required_skill_paths`

## Optional Secondary Skills

- `millrace-skill-creator`: use for package-shape and lint/eval task boundaries

## Workflow

1. Create a task sequence that starts with a draftable package and ends with publish or archive.
2. Include a checker infrastructure smoke test before trusting QA.
3. Require up to five fixer/doublechecker repair cycles for valid quality failures.
4. Make QA infrastructure failure a troubleshooting issue, not a skill-quality failure.
5. Write executable task cards into the managed task queue when the spec is actionable.

## Output Requirements

Preferred artifact:
- request-provided `run_dir/manager_task_plan.md`

The task plan must list:
- research task
- draft or revamp task
- shape-validation task
- checker A/B QA task
- fixer/doublechecker retry rule
- updater publish task
- arbiter archive or close task

## Completion Signaling

Emit exactly one legal terminal result to request-provided `summary_status_path`:

Complete:
`### MANAGER_COMPLETE`

Blocked:
`### BLOCKED`
