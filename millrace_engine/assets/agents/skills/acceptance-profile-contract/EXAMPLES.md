# Examples - Acceptance Profile Contract

Append new examples to the end of this file. Keep Example IDs stable.

---

## EX-2026-03-02-01: Product pipeline profile normalized and reused

**Tags**: `acceptance-profile`, `milestones`, `traceability`

**Trigger phrases**:
- "normalize a reusable milestone contract with deterministic gates"
- "put acceptance in prompt but keep reusable"
- "map acceptance checkpoints to task milestones"

**Date**: 2026-03-02

**Problem**:
The seed prompt described acceptance expectations, but criteria were mixed with narrative text and were not consistently reused by Clarify/Taskmaster.

**Cause**:
No normalized profile artifact existed, so milestone IDs and gate semantics drifted between cycles.

**Fix**:
1) Extracted prompt acceptance block into a normalized profile:
- `agents/reports/acceptance_profiles/bootstrap_profile_v1.json`
2) Included deterministic fields:
- `profile_id`, `objective`, `hard_blockers`, `milestones[].verify`, `objective_gates`, `updated_at`
3) Mapped profile IDs into downstream spec/task outputs:
- milestone IDs tied to `REQ-*`/`AC-*` and task-card verification commands.

**Prevention**:
Require profile normalization before decomposition when prompt contains `ACCEPTANCE_PROFILE`.

**References**:
- `agents/skills/acceptance-profile-contract/SKILL.md`
- `agents/_clarify.md`
- `agents/_taskmaster.md`

---

## EX-2026-03-02-02: Ambiguous acceptance profile blocked safely

**Tags**: `acceptance-profile`, `blocked`, `ambiguity`, `verification`

**Trigger phrases**:
- "eventually pass large codebases"
- "optimize as needed"
- "acceptance is mostly descriptive"

**Date**: 2026-03-02

**Problem**:
The profile draft used vague milestone language and lacked concrete verification evidence.

**Cause**:
Milestones were written as goals without deterministic checks or expected pass signals.

**Fix**:
1) Stopped stage and marked `### BLOCKED`.
2) Returned one deterministic unblock action:
- replace each milestone with explicit `verify` command/artifact + expected result.
3) Re-ran profile normalization only after measurable verification entries were provided.

**Prevention**:
Reject profile milestones that do not include command/artifact evidence and remove vague phrases before acceptance.

**References**:
- `agents/skills/acceptance-profile-contract/SKILL.md`
- `agents/skills/spec-writing-research-core/SKILL.md`

---

<!-- Append new examples below this line. -->
