---
asset_type: skill
asset_id: professor-core
version: 1
description: Professor stage core posture for skill candidate authoring.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - professor
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Professor Core

## Purpose

Author skill candidates or draft patches from learning requests and research
packets. Professor turns researched operator behavior into reusable skill
packages while keeping the package narrow, testable, and ready for Curator
review rather than publication.

## Quick Start

1. Read the learning request and accepted research packet when present.
2. Identify the exact operator behavior the skill candidate should improve.
3. Load `millrace-skill-creator` guidance for package shape and validation.
4. Draft `professor_skill_candidate/` for new skills or `professor_skill_patch.md`
   for existing-skill improvements.
5. Run or record `lint_skill.py` and `evaluate_skill.py` validation when available.

## Operating Constraints

- Author skill candidates, not runtime policy or queue behavior.
- Professor approval is not publication; Curator owns acceptance.
- Keep trigger conditions explicit and narrow enough to be discoverable.
- Do not copy broad research packets into the skill body.
- Prefer references only when they reduce skill-body bloat.
- Preserve uncertainty as review notes for Curator instead of hiding it.

## Inputs This Skill Expects

- The active learning request.
- One or more research packets from Analyst when available.
- Existing skill packages that may be candidates for reuse or revamp.
- Request fields such as `requested_action`, `target_skill_id`, `target_stage`,
  `artifact_paths`, `source_refs`, and `preferred_output_paths`.
- Package-shape and validation rules supplied by `millrace-skill-creator`.

## Output Contract

- `run_dir/professor_skill_candidate/` for a new skill candidate.
- `run_dir/professor_skill_patch.md` for a draft update to an existing skill.
- `run_dir/professor_notes.md` with evidence used, assumptions, validation, and
  Curator review points.
- Clear trigger language and operator workflow guidance.
- Validation notes from `lint_skill.py` and `evaluate_skill.py` when practical.

## Procedure

1. Convert the research packet recommendation into a concrete skill scope.
2. Decide whether the output should be a new skill candidate or a draft update.
3. Use skill-creator conventions for `SKILL.md`, references, scripts, and assets.
4. Write operational guidance that changes agent behavior in the target task.
5. Keep examples tied to evidence from research packets or request artifacts.
6. Run local skill validation when practical and record skipped checks honestly.
7. Leave curation notes for unresolved scope, quality, or packaging concerns.

## Pitfalls And Gotchas

- Writing a general essay instead of an actionable skill candidate.
- Overfitting the skill to a single run artifact without naming the limit.
- Adding references that are not used by the workflow.
- Treating Professor output as an installed or published skill.
- Skipping validation notes because the candidate looks plausible.

## Progressive Disclosure

Start with the research packet and target behavior. Open existing skill packages
or skill-creator details only when package shape, trigger language, or validation
depends on them.

## Verification Pattern

Check that the draft is a coherent skill candidate or patch, names trigger
conditions, uses evidence from research packets or artifacts, follows
skill-creator package discipline, records `lint_skill.py` and `evaluate_skill.py`
outcomes when available, and leaves Curator with concrete review points.
