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

Author skill candidates from learning requests and research packets. Professor
turns researched operator behavior into reusable skill packages while keeping the
package narrow, testable, and compatible with the packaged skill-creator
expectations.

## Quick Start

1. Read the learning request and the accepted research packet.
2. Identify the exact operator behavior the skill candidate should improve.
3. Use skill-creator guidance for package shape and validation expectations.
4. Draft the smallest useful skill candidate with justified references only.
5. Record the checks Curator should run before adoption.

## Operating Constraints

- Author skill candidates, not runtime policy or queue behavior.
- Keep the trigger conditions explicit and narrow enough to be discoverable.
- Do not copy broad research packets into the skill body.
- Prefer references only when they reduce skill-body bloat.
- Preserve uncertainty as review notes for Curator instead of hiding it.

## Inputs This Skill Expects

- The active learning request.
- One or more research packets from Analyst.
- Existing skill packages that may be candidates for reuse or revamp.
- Any package-shape or validation rules supplied by skill-creator.

## Output Contract

- A skill candidate package or draft update ready for curation.
- Clear trigger language and operator workflow guidance.
- Validation notes, examples, or scripts when practical.
- A summary of evidence used and assumptions left for Curator.

## Procedure

1. Convert the research packet recommendation into a concrete skill scope.
2. Decide whether the output should be a new skill candidate or a draft update.
3. Use skill-creator conventions for `SKILL.md`, references, scripts, and assets.
4. Write operational guidance that changes agent behavior in the target task.
5. Keep examples tied to the evidence from the research packets.
6. Leave curation notes for unresolved scope, quality, or packaging concerns.

## Pitfalls And Gotchas

- Writing a general essay instead of an actionable skill candidate.
- Overfitting the skill to a single run artifact without naming the limit.
- Adding references that are not used by the workflow.
- Treating Professor approval as publication.

## Progressive Disclosure

Start with the research packet and target behavior. Open existing skill packages
or skill-creator details only when package shape, trigger language, or validation
depends on them.

## Verification Pattern

Check that the draft is a coherent skill candidate, names its trigger conditions,
uses evidence from research packets, follows skill-creator package discipline,
and leaves Curator with concrete review points.
