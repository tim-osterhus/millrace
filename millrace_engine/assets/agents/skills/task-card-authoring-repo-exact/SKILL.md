---
name: task-card-authoring-repo-exact
description: >
  Writes a single, repo-accurate task card in `agents/tasks.md` (or queues one in `agents/tasksbacklog.md`) that is executable by the Builder/QA cycles without interpretation.
  Includes explicit metadata (Complexity/Assigned skills/Tags/Gates) so the pipeline is deterministic and auditable.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Grep", "Write"]
  offline_ok: true
---

# Task Card Authoring (Repo-Exact)

## Purpose
Turn ambiguous requests into one executable, repo-accurate task card with objective DONE checks and explicit file targets.

## Quick start
Goal:
- Produce one unambiguous task card that a Builder agent can execute end-to-end in a single cycle.

Use when (triggers):
- Creating or promoting a task in `agents/tasks.md`
- Turning vague user requests into repo-scoped work items
- Splitting a big initiative into 1-cycle deliverable slices
- The QA cycle produced gaps that need a new, tighter task card

Do NOT use when (non-goals):
- Multi-week roadmaps or broad brainstorming (use `agents/tasksbacklog.md` instead)
- Refactoring the agent framework itself

## Operating constraints
- No secrets: never embed API keys, tokens, passwords, private URLs.
- Be minimal: smallest diffs; no drive-by refactors unless required.
- Be explicit: state assumptions and verify them where possible.
- Be deterministic: prefer exact commands/checklists over vague guidance.
- Treat Gates as executable requirements; treat Complexity as metadata unless a configured orchestration option explicitly routes by complexity.
- Keep SKILL.md short: if you exceed ~500 lines, split details into linked files.

## Inputs this Skill expects
Required:
- `outline.md` (repo map) and at least one relevant file path to change
- Current `agents/tasks.md` (to avoid conflicting/duplicate tasks)

Optional:
- `agents/tasksbacklog.md` candidate task to promote
- `agents/expectations.md` and `agents/quickfix.md` if this is a QA-follow-up task
- Relevant logs or failing test output

If required inputs are missing:
- Ask for the MINIMUM missing info OR proceed with safest defaults and list assumptions.

## Output contract
Primary deliverable:
- Edits to `agents/tasks.md` that define ONE active task with clear DONE checks and file targets.

Secondary deliverables (only if needed):
- Optional: move the chosen backlog item from `agents/tasksbacklog.md` into `agents/tasks.md` (do not duplicate)
- Optional: add a 1–3 line note at the top of `agents/historylog.md` (newest first) explaining why the task exists

Definition of DONE (objective checks):
- [ ] Task card includes required metadata: Complexity / Assigned skills / Tags / Gates
- [ ] Card has explicit file paths + numbered steps (no interpretation)
- [ ] DONE checks are objective and runnable (commands included)
- [ ] If Gates != NONE, required gate artifacts are named + located
- [ ] Task scope fits one Builder cycle (or explicitly calls out a safe cutoff + follow-up card)
- [ ] Card has a bounded change surface, bounded verification surface, and bounded exit condition
- [ ] Card does not rely on open-ended phrasing like `iterate until pass` or `fix until green`

---

## Task card metadata: Complexity / Assigned skills / Tags / Gates

These fields live at the top of every task card.

### Complexity (metadata only)
Allowed values:
- `MODERATE` — localized feature work with some coupling/risk
- `INVOLVED` — cross-cutting/shared interfaces (auth/routing/schema/build/etc.)
- `COMPLEX` — high-risk or broad-surface change that may need stronger models

Rules:
- Complexity is metadata, but orchestration options may route models by this field.
- Prefer `MODERATE` when uncertain.
- Legacy pre-3-tier values should be normalized to `MODERATE`; use `COMPLEX` when the legacy card clearly indicates broad/high-risk scope.

### Assigned skills (optional context optimization)
Use this metadata to constrain context on small tasks.

Rules:
- Optional for `MODERATE`/`INVOLVED`/`COMPLEX`.
- If present, provide exactly 2 skill identifiers (folder names) from `agents/skills/`.

### Tags (search + policy)
Space-separated tokens. Keep them consistent and short.

Suggested vocabulary (pick what applies):
- Change type: `COPY_CHANGE`, `STYLE_CHANGE`, `BEHAVIOR_CHANGE`, `ARCH_CHANGE`, `REFACTOR`
- Risk areas: `AUTH`, `ROUTING`, `DATA_MODEL`, `BUILD_PIPELINE`, `SECURITY`, `PERF`
- Scope: `LOCAL_ONLY`, `CROSS_CUTTING`

### Gates (executable)
Gates are the *only* automation triggers. They are explicit.

Allowed gates:
- None by default. Optional gates may be installed via `agents/options/` (e.g., Integration).

Rules:
- If a gate is present, the task card must name the expected artifact + location.
- Do not infer gates from Complexity. Write gates explicitly.

### Lane + Contract Trace
Required fields for execution cards:
- `Lane`: one of `OBJECTIVE`, `RELIABILITY`, `INFRA`, `DOCUMENTATION`, `EXTERNAL_BLOCKED`.
- `Contract Trace`: include explicit trace tokens (for example `objective:<id>`, `REQ-*`, `AC-*`, `OUTCOME-*`).

Rules:
- Use `OBJECTIVE` only when the card directly advances target behavior/outcomes.
- Use `RELIABILITY`/`INFRA`/`DOCUMENTATION` for framework/support work.
- Keep `Contract Trace` concrete and source-linked; do not use placeholder text.

### Shared execution-card contract
Every execution card must satisfy all three:
- Bounded change surface: the change is scoped to a small, explicit file set or one tightly coupled slice.
- Bounded verification surface: the card has a short, concrete verification list rather than a whole-project reducer loop.
- Bounded exit condition: the card ends at one concrete deliverable or one bounded gate closure.

Reject or split cards that read like:
- `iterate until pass`
- `fix until green`
- `implement until project X passes`
- `run all gates and fix failures`

---

## Task card template (copy/paste)

```md
## <DATE> — <Short imperative title>

**Complexity:** <MODERATE|INVOLVED|COMPLEX>
**Lane:** <OBJECTIVE|RELIABILITY|INFRA|DOCUMENTATION|EXTERNAL_BLOCKED>
**Contract Trace:** <objective:<id> REQ-* AC-* OUTCOME-*>
**Assigned skills:** <skill-a, skill-b>
**Tags:** <TAG1 TAG2 TAG3>
**Gates:** <NONE>

### Goal:
- <One sentence objective>

######Scope:
- In: <what is included>
- Out: <what is explicitly excluded>

### Files to touch (explicit):
- <path1>
- <path2>

### Steps (numbered, deterministic):
1) <exact change 1>
2) <exact change 2>
3) <run commands / update docs>

### Acceptance (objective checks; prefer binary):
- [ ] <yes/no check>
- [ ] Run: `<command>` and confirm: `<expected result>`

### Prompt artifact (always):
- Prompt artifact at: <agents/prompts/tasks/###-slug.md>

### Verification commands (copy/paste):
- <command 1>
- <command 2>

### Rollback plan (minimal):
- <how to revert safely>

### Notes / assumptions:
- <assumption 1>
```


## Procedure (copy into working response and tick off)
Progress:
- [ ] 1) Confirm scope + constraints
- [ ] 2) Locate entrypoints + relevant files
- [ ] 3) Assign Complexity + Tags + Gates
- [ ] 4) Plan smallest safe change set
- [ ] 5) Implement changes
- [ ] 6) Validate locally
- [ ] 7) Summarize changes + next steps

### 1) Confirm scope + constraints
- Restate objective in ONE sentence.
- List constraints (security, production offline/LAN-only, no new deps, etc.).
- List assumptions explicitly.

### 2) Locate entrypoints + relevant files
Run targeted searches:
- Search terms:
  - agents/tasks.md
  - tasksbacklog.md
  - expectations.md
  - quickfix.md
  - historylog.md
- Files to inspect first (prioritized):
   1) agents/tasks.md
   2) agents/tasksbacklog.md
   3) agents/_start.md or agents/_advisor.md (depending on task)
   4) agents/prompts/builder_cycle.md

### 3) Assign Complexity + Tags + Gates (if enabled)
- Pick **Complexity**: MODERATE / INVOLVED / COMPLEX.
- `Assigned skills` is optional. If used, set it to exactly two relevant skill ids.
- Add **Tags** that describe what changes and where risk lives.
- Decide **Gates** explicitly (do not infer from Complexity):
  - Use `INTEGRATION` only if Integration was enabled during customization; otherwise set `**Gates:** NONE`.
- If Gates != NONE, add the expected artifact locations in the task card.


### 4) Plan smallest safe change set
Rules:
- Touch the minimum number of files.
- Prefer additive changes over rewrites.
- If multiple approaches exist, choose the simplest that satisfies DONE checks.
- If risk is medium/high, write a micro-plan artifact before coding (see “Verification pattern”).
- If the task touches retrieval/infra/security, add a “Verification pattern” subsection in the task card.
- Reference concrete paths under `rag_api/`, `infra/`, or `librechat/` (no “update the backend” phrasing).

### 5) Implement changes
Implementation checklist:
- [ ] Follow repo conventions (naming, formatting, module boundaries).
- [ ] Add or adjust any needed configuration.
- [ ] Update tests where applicable.
- [ ] Update docs where user-facing behavior changes.
- [ ] Task card includes the exact validation commands the Builder must run.

### 6) Validate locally (choose what exists in the repo)
Run validations in this order:
1) Fast static checks:
   - markdown lint (if configured) OR manual scan for broken headings/checkboxes
2) Unit/targeted tests:

3) Integration smoke test:


If validation fails:
- Do not guess. Inspect error output, fix, re-run until green.
- If blocked by missing deps or environment, document the exact missing item and minimal install/run step.

### 7) Summarize changes + next steps
Include:
- What changed (bullets)
- Why (bullets)
- How to verify (exact commands)
- Next steps (1–3 max)

## Pitfalls / gotchas (keep this brutally honest)
- Writing tasks as narratives instead of checklists → enforce explicit file targets + numbered steps.
- DONE checks that are subjective (“looks good”) → require commands + expected outputs.
- Packing multiple unrelated changes into one card → split by subsystem (RAG API vs LibreChat vs infra).

## Progressive disclosure (one level deep)
If this Skill needs detail, link it directly from HERE (avoid chains of references).

## Verification pattern (recommended for medium/high risk)
Use this when changes touch infra, retrieval logic, licensing, or security:
1) Analyze
2) Write a machine-checkable plan artifact (e.g. report.md / changes.json)
3) Validate assumptions (paths, versions, config)
4) Execute changes
5) Verify DONE checks

## Optional: scripts section (ONLY if repo already uses scripts)
Run:
- bash scripts/run_cycle.sh builder

Expected output:
- Builder produces code + verification commands

Common failures:
- Script missing/permissions → `chmod +x scripts/run_cycle.sh`

## Example References (concise summaries only)

**How to reference examples:**
- Keep summaries SHORT (1-2 sentences max)
- Reference by stable Example ID (line numbers are brittle across editors/formatters)
- Agents will load full examples only when symptoms match

**Example summaries:**

1. **Promote backlog item into active task** - Move ONE item from backlog, rewrite with explicit paths and commands. See EXAMPLES.md (EX-2025-12-28-01)
2. **Split vague multi-feature request** - Split "hybrid retrieval and reranking" into separate cards with precision@k reports. See EXAMPLES.md (EX-2025-12-28-02)

**Note:** Full examples with tags and trigger phrases are in `./EXAMPLES.md`.
Agents search that file only when they encounter matching symptoms (context-efficient).
