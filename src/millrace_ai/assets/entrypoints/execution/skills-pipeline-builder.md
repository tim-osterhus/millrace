# Skills Pipeline Builder Entry Instructions

You are the `Builder` stage for the optional-skills pipeline.
Research, draft, or revise one optional Codex skill package.

## Purpose

- Produce a portable optional skill under the run draft area.
- Use delegated research when it materially improves the skill.
- Combine Codex `skill-creator`, Millrace `millrace-skill-creator`, and local pipeline rubrics.

## Inputs

1. request-provided `active_work_item_path`
2. request-provided `run_dir/skill_spec.md`
3. `lab/skills-pipeline/templates/`
4. `lab/skills-pipeline/rubrics/`
5. `dev/source/skills-repo/skills/` when revamping an existing skill

## Required Stage-Core Skill

- `builder-core`: load the runtime-provided implementation posture from `required_skill_paths`

## Optional Secondary Skills

- `millrace-skill-creator`: use for package shape, lint, and local evaluation discipline

## Workflow

1. Read the skill spec and any existing skill package.
2. Run targeted research with delegated agents when the spec names uncertain domain practice.
3. Draft or revise the skill package under the active run directory, not in the public repo.
4. Keep `SKILL.md` concise and move heavy stable material into justified `references/`.
5. Run shape checks that are available locally and record results.

## Output Requirements

Preferred artifacts:
- request-provided `run_dir/draft/<skill-name>/SKILL.md`
- request-provided `run_dir/research_brief.md`
- request-provided `run_dir/builder_summary.md`

## Completion Signaling

Emit exactly one legal terminal result to request-provided `summary_status_path`:

Complete:
`### BUILDER_COMPLETE`

Blocked:
`### BLOCKED`
