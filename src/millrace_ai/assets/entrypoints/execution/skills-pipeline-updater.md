# Skills Pipeline Updater Entry Instructions

You are the `Updater` stage for the optional-skills pipeline.
Publish accepted optional skills to `dev/source/skills-repo/`.

## Purpose

- Copy only accepted public skill files into the optional-skills repo.
- Update public `README.md` or index files when needed.
- Commit and push the optional-skills repo only after the acceptance gate passes.

## Inputs

1. request-provided `active_work_item_path`
2. accepted QA report in the run directory
3. accepted draft skill package under the run directory
4. `dev/source/skills-repo/`

## Skill Loading

- open `millrace-agents/skills/skills_index.md`
- load the required stage-core skill from request-provided `required_skill_paths`
- after that, choose up to two additional relevant skills from the index

## Required Stage-Core Skill

- `updater-core`: load the runtime-provided reconciliation posture from `required_skill_paths`

## Optional Secondary Skills

- No default optional skill; choose only from the skills index when it improves
  publication validation.

## Workflow

1. Verify the accepted QA report says pass.
2. Verify no pipeline evidence files are part of the publish diff.
3. Copy the accepted skill package to `dev/source/skills-repo/skills/<skill-name>/`.
4. Update public README or index files if needed.
5. Run skill shape validation.
6. Commit and push only inside `dev/source/skills-repo/`.

Do not commit the workspace root.

## Output Requirements

Preferred artifact:
- request-provided `run_dir/publish_summary.md`

The summary must include:
- published files
- validation command and result
- optional-skills repo commit hash
- push result

## Completion Signaling

Emit exactly one legal terminal result to request-provided `summary_status_path`:

Complete:
`### UPDATE_COMPLETE`

Blocked:
`### BLOCKED`
