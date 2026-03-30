# Examples - Spec Writing Research Core

This file stores detailed, real-world examples for the `spec-writing-research-core` skill.

Append new examples to the END of this file and never change existing Example IDs.

---

## EX-2026-02-26-01: Clarify stage writes deterministic golden + phase specs

**Tags**: `clarify`, `goal-spec`, `traceability`, `verification`

**Trigger phrases**:
- "convert staging idea into stable spec"
- "missing Req-ID traceability"
- "spec has requirements but no acceptance mapping"

**Date**: 2026-02-26

**Problem**:
The staging idea was rich in narrative but missing deterministic requirement structure. There were no stable REQ/AC IDs, and verification language used vague terms like "quickly" and "as needed."

**Cause**:
The draft skipped requirement form gates and moved directly to decomposition, which made Taskmaster output non-repeatable and hard to lint.

**Fix**:
1) Run Clarify with the core skill gates:
- Convert requirement statements to EARS forms.
- Add stable IDs (`REQ-*`, `AC-*`).
- Add verification method and explicit pass signals per requirement.
- Add assumption and decision records (`ASM-*`, `DEC-*`) for unresolved facts and design choices.
2) Emit:
- `agents/specs/stable/golden/<spec_id>__<slug>.md`
- `agents/specs/stable/phase/<spec_id>__phase-01.md`
- Queue copy in `agents/ideas/specs/`.
3) Validate:
- `rg -n "^REQ-" <golden_spec>`
- `rg -n "^AC-" <golden_spec>`
- `rg -n -i "and/or|etc\\.|as appropriate|as needed|eventually|soon|later" <golden_spec>`

**Prevention**:
Keep Clarify entrypoint embedding enabled so spec form and traceability gates run before any task decomposition stage.

**References**:
- `agents/_clarify.md`
- `agents/skills/spec-writing-research-core/SKILL.md`

---

## EX-2026-02-26-02: Incident fix_spec blocked due invented requirements

**Tags**: `incident`, `fix-spec`, `blocked`, `decomposition`

**Trigger phrases**:
- "incident fix_spec keeps changing scope"
- "task cards introduce new requirements"
- "cannot trace fix tasks back to source spec"

**Date**: 2026-02-26

**Problem**:
Incident Resolve generated a `fix_spec`, then Taskmaster emitted cards with obligations not present in the source incident artifact. QA could not verify completion because there was no source REQ/AC basis for those cards.

**Cause**:
The no-new-requirement gate was skipped during decomposition, and unresolved unknowns were written as facts instead of assumptions.

**Fix**:
1) Stop decomposition and mark stage `### BLOCKED`.
2) Update incident + fix_spec with:
- explicit REQ/AC IDs
- assumption records for unknown dependencies
- decision records for selected remediation path
3) Regenerate pending cards only after traceability is restored.
4) Re-run:
- `python3 agents/tools/lint_task_cards.py agents/taskspending.md --strict "$TASKCARD_FORMAT_STRICT" --min-cards-per-spec "$TASKMASTER_MIN_CARDS_PER_SPEC"`

**Correct stop behavior**:
Do not keep questioning indefinitely. If no material delta emerges after bounded interrogation rounds, early-stop, record unresolved gaps, and block with one deterministic unblocking action.

**Prevention**:
Keep Incident Resolve + Taskmaster embeddings active so `fix_spec` artifacts use the same requirement rigor as GoalSpecs.

**References**:
- `agents/_incident_resolve.md`
- `agents/_taskmaster.md`
- `agents/_taskaudit.md`
- `agents/skills/spec-writing-research-core/SKILL.md`

---

<!--
Add new examples below this line.
DO NOT insert examples above existing ones (breaks Example ID stability).
-->
