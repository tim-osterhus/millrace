---
name: small-diff-discipline
description: >
  Enforces minimal, reviewable changes by constraining scope, file touches, and commit-like diff size to reduce breakage in a production offline, multi-service stack.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Grep", "Bash", "Write"]
  offline_ok: true
---

# Small-Diff Discipline

## Purpose
Reduce regressions and merge pain by forcing the smallest possible change set that still meets the task's DONE checks.

## Quick start
Goal:
- Ship the smallest change that satisfies the task’s DONE checks, with zero drive-by edits.

Use when (triggers):
- Any change that crosses service boundaries (RAG API + LibreChat + infra)
- Touching Docker compose, schema, or retrieval logic
- When previous cycles have produced regressions or merge pain

Do NOT use when (non-goals):
- Large refactors, rewrites, or broad renames
- “Clean up” work not required by the current task card

## Operating constraints
- No secrets: never embed API keys, tokens, passwords, private URLs.
- Be minimal: smallest diffs; no drive-by refactors unless required.
- Be explicit: state assumptions and verify them where possible.
- Be deterministic: prefer exact commands/checklists over vague guidance.
- Keep SKILL.md short: if you exceed ~500 lines, split details into linked files.

## Inputs this Skill expects
Required:
- Active task card in `agents/tasks.md`
- Git status/diff for the current work tree

Optional:
- `agents/expectations.md` (what QA will verify)
- Known failing tests or smoke checklist items

If required inputs are missing:
- Ask for the MINIMUM missing info OR proceed with safest defaults and list assumptions.

## Output contract
Primary deliverable:
- A focused diff that touches the minimum files and passes the task’s validation commands.

Secondary deliverables (only if needed):
- Optional: a short diff summary at the top of `agents/historylog.md` (newest first) (what/why/how verify)

Definition of DONE (objective checks):
- [ ] No unrelated formatting/renames outside the task scope
- [ ] All validations in the task card pass
- [ ] Diff can be explained in <10 bullets

## Procedure (copy into working response and tick off)
Progress:
- [ ] 1) Confirm scope + constraints
- [ ] 2) Locate entrypoints + relevant files
- [ ] 3) Plan smallest safe change set
- [ ] 4) Implement changes
- [ ] 5) Validate locally
- [ ] 6) Summarize changes + next steps

### 1) Confirm scope + constraints
- Restate objective in ONE sentence.
- List constraints (security, production offline/LAN-only, no new deps, etc.).
- List assumptions explicitly.

### 2) Locate entrypoints + relevant files
Run targeted searches:
- Search terms:
  - git diff
  - agents/tasks.md
  - infra/compose
  - rag_api/
  - librechat/
- Files to inspect first (prioritized):
   1) agents/tasks.md
   2) agents/prompts/builder_cycle.md
   3) scripts/SMOKE_TEST_CHECKLIST.md

### 3) Plan smallest safe change set
Rules:
- Touch the minimum number of files.
- Prefer additive changes over rewrites.
- If multiple approaches exist, choose the simplest that satisfies DONE checks.
- If risk is medium/high, write a micro-plan artifact before coding (see “Verification pattern”).
- Hard rule: if you touch >10 files, justify each file in the task summary.
- Prefer feature flags/toggles (existing env vars) over behavior changes that can’t be rolled back.

### 4) Implement changes
Implementation checklist:
- [ ] Follow repo conventions (naming, formatting, module boundaries).
- [ ] Add or adjust any needed configuration.
- [ ] Update tests where applicable.
- [ ] Update docs where user-facing behavior changes.
- [ ] If touching compose, run `docker compose config` and include the diff excerpt in the summary.

### 5) Validate locally (choose what exists in the repo)
Run validations in this order:
1) Fast static checks:
   - git diff --stat (sanity check diff size)
   - docker compose -f infra/compose/docker-compose.rag.yml config >/dev/null
2) Unit/targeted tests:
   - pytest tests/test_database.py tests/test_rag_api.py -v (if relevant)
3) Integration smoke test:
   - docker compose -f infra/compose/docker-compose.pgvector.yml -f infra/compose/docker-compose.mongo.yml -f infra/compose/docker-compose.ollama.yml -f infra/compose/docker-compose.rag.yml -f infra/compose/docker-compose.librechat.yml up -d --build

If validation fails:
- Do not guess. Inspect error output, fix, re-run until green.
- If blocked by missing deps or environment, document the exact missing item and minimal install/run step.

### 6) Summarize changes + next steps
Include:
- What changed (bullets)
- Why (bullets)
- How to verify (exact commands)
- Next steps (1–3 max)

## Pitfalls / gotchas (keep this brutally honest)
- Bundling multiple “nice-to-haves” → reject anything not needed for DONE checks.
- Editing generated/third-party files (LibreChat submodule) directly → use the submodule workflow instead.
- Fixing symptoms with bigger changes → identify the smallest root-cause fix and prove it with a test.

## Progressive disclosure (one level deep)
If this Skill needs detail, link it directly from HERE (avoid chains of references):
- Reference/specs: ./reference.md

## Verification pattern (recommended for medium/high risk)
Use this when changes touch infra, retrieval logic, licensing, or security:
1) Analyze
2) Write a machine-checkable plan artifact (e.g. report.md / changes.json)
3) Validate assumptions (paths, versions, config)
4) Execute changes
5) Verify DONE checks

## Optional: scripts section (ONLY if repo already uses scripts)
Run:
- bash scripts/run_reviewer.sh

Expected output:
- QA summary + quickfix list generated

Common failures:
- Missing script deps → document minimal install/run step

## Example References (concise summaries only)

**How to reference examples:**
- Keep summaries SHORT (1-2 sentences max)
- Reference by stable Example ID (line numbers are brittle across editors/formatters)
- Agents will load full examples only when symptoms match

**Example summaries:**

1. **Failing test without changing behavior** - Fixed `/query` test by touching only the failing function and test. See EXAMPLES.md (EX-2025-12-28-01)
2. **Retrieval quality improvement scope creep** - Added toggle + minimal scoring adjustment with precision@k report. See EXAMPLES.md (EX-2025-12-28-02)

**Note:** Full examples with tags and trigger phrases are in `./EXAMPLES.md`.
Agents search that file only when they encounter matching symptoms (context-efficient).
