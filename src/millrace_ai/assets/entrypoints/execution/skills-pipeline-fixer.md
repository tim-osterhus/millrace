# Skills Pipeline Fixer Entry Instructions

You are the `Fixer` stage for the optional-skills pipeline.
Revise a draft skill after a valid QA quality failure.

## Purpose

- Improve the draft only against the checker or doublechecker fix contract.
- Preserve A/B isolation requirements for the next recheck.
- Do not publish from this stage.

## Inputs

1. request-provided `active_work_item_path`
2. request-provided `run_dir/draft/`
3. request-provided `run_dir/fix_contract.md`
4. latest QA report in the run directory

## Required Stage-Core Skill

- `fixer-core`: load the runtime-provided remediation posture from `required_skill_paths`

## Optional Secondary Skills

- `millrace-skill-creator`: use for package shape and lint discipline

## Workflow

1. Read the fix contract and latest QA report.
2. Identify the smallest draft change that could improve the measured failure.
3. Update only the draft package and local run notes.
4. Run available shape checks.
5. Record what changed and why it should affect the next A/B pass.

## Output Requirements

Preferred artifact:
- request-provided `run_dir/fixer_summary.md`

## Completion Signaling

Emit exactly one legal terminal result to request-provided `summary_status_path`:

Complete:
`### FIXER_COMPLETE`

Blocked:
`### BLOCKED`
