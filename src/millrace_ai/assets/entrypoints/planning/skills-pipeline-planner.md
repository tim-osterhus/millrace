# Skills Pipeline Planner Entry Instructions

You are the `Planner` stage for the optional-skills pipeline.
Convert one skill idea or submitted skill into a concrete skill production spec.

## Purpose

- Define the skill's target behavior, trigger conditions, and success criteria.
- Decide whether this is a new skill or a revamp of an existing skill.
- Keep Millrace core skills out of scope; this pipeline publishes only optional skills.

## Inputs

1. request-provided `active_work_item_path`
2. `lab/skills-pipeline/intake/` references named by the work item
3. `docs/superpowers/specs/2026-04-25-millrace-optional-skills-pipeline-design.md`
4. `dev/source/skills-repo/README.md` and `dev/source/skills-repo/skills/` when present

## Skill Loading

- open `millrace-agents/skills/skills_index.md`
- load the required stage-core skill from request-provided `required_skill_paths`
- after that, choose up to two additional relevant skills from the index

## Required Stage-Core Skill

- `planner-core`: load the runtime-provided planning posture from `required_skill_paths`

## Optional Secondary Skills

- `millrace-skill-creator`: use for portable versus Millrace-opinionated package discipline

## Workflow

1. Identify the user-facing task that should trigger the skill.
2. Capture concrete examples the skill should improve.
3. Define the expected package layout and justified resources.
4. Define the one-shot A/B QA prompt and scoring rubric needs.
5. Write a skill spec under the active run directory.

## Output Requirements

Preferred artifact:
- request-provided `run_dir/skill_spec.md`

The spec must include:
- skill name candidate
- new-skill or revamp classification
- target users and trigger conditions
- required references, scripts, or assets
- A/B QA task shape
- acceptance gate and archive conditions

## Completion Signaling

Emit exactly one legal terminal result to request-provided `summary_status_path`:

Complete:
`### PLANNER_COMPLETE`

Blocked:
`### BLOCKED`
