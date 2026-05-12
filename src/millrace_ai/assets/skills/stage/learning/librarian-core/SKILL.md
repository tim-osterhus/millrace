---
asset_type: skill
asset_id: librarian-core
version: 1
description: Librarian stage core posture for bounded remote optional-skill discovery and workspace installation after Planner output.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - librarian
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Librarian Core

## Purpose

Select and install relevant optional remote skills for a workspace after Planner
has produced or refined a spec. Librarian keeps remote skill discovery bounded
to the supported remote index, avoids duplicate installs, and records why each
candidate was selected or skipped.

## Quick Start

1. Read the learning request and locate the Planner spec or summary.
2. Read the installed `skills_index.md` and collect already installed skill ids.
3. Refresh or inspect `remote_skills_index.md`.
4. Match remote skills against the spec's goals, target paths, entrypoints,
   risks, assumptions, and required skills.
5. Install up to eight relevant uninstalled remote skills, or no-op cleanly.
6. Write `librarian_selection_report.md` with all considered and installed ids.

## Operating Constraints

- Use only the supported remote index as the candidate source.
- Do not search arbitrary GitHub repositories or install unlisted skills.
- Do not reinstall skills that are already installed locally.
- Install no more than eight remote skills in one run.
- Prefer clearly relevant skills over broad or speculative matches.
- Do not edit source-packaged skills, commit, push, publish, export, or promote.
- Do not claim a skill is available until it is installed locally.

## Inputs This Skill Expects

- The active learning request targeting Librarian.
- Planner stage result artifacts and `planner_summary.md` when available.
- The generated or refined Planner spec.
- `millrace-agents/skills/skills_index.md`.
- `millrace-agents/skills/remote_skills_index.md` after refresh when useful.
- Command output from `millrace skills refresh-remote-index` and
  `millrace skills install <skill_id>`.

## Output Contract

- `run_dir/librarian_selection_report.md`.
- A list of local installed skill ids considered.
- A list of remote candidates considered.
- A list of selected remote skill ids with relevance rationale.
- A list of skipped candidates with reasons.
- Installed skill ids and command outcomes, when installs occur.
- A no-op rationale when no relevant uninstalled remote skill is present.
- The `LIBRARIAN_NOOP` path is used when inspection completes but no relevant
  uninstalled remote skill exists.

## Procedure

1. Resolve the Planner spec from explicit artifact paths first, then Planner
   summary paths, then source metadata from the learning request.
2. Parse the spec for goals, scope, target paths, entrypoints, required skills,
   risks, assumptions, and acceptance criteria.
3. Read the installed skill index and collect installed skill ids before looking
   at remote candidates.
4. Refresh the remote skill index when missing, stale, or likely incomplete.
5. Inspect remote candidate ids, names, descriptions, and tags.
6. Exclude candidates that are already installed.
7. Score relevance by direct overlap with the Planner spec's domain, target
   paths, entrypoints, risk profile, or required skill needs.
8. Select up to eight high-confidence uninstalled candidates.
9. Install selected skills with the supported `millrace skills install
   <skill_id> --workspace .` command.
10. Record selected, installed, skipped, unavailable, and no-op outcomes in the
    selection report.

## Pitfalls And Gotchas

- Installing broadly interesting skills that do not clearly match the spec.
- Reinstalling an already installed skill because the remote index still lists it.
- Treating remote index visibility as local availability.
- Searching GitHub directly instead of using the supported remote index.
- Hiding command failures instead of recording them in the report.
- Installing more than up to eight skills from one Planner-triggered pass.

## Progressive Disclosure

Start with the Planner spec and installed skill index. Open remote candidate
details only after a candidate appears relevant from the remote index summary.
Do not spend context on remote skills that cannot plausibly help the active spec.

## Verification Pattern

Check that the selection report names the Planner spec, installed skill ids,
remote index source, all selected and skipped candidates, install command
outcomes, the up to eight limit, and the no-op reason when nothing relevant was
installed.
