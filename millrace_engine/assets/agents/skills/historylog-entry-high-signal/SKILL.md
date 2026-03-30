---
name: historylog-entry-high-signal
description: >
  Adds a concise, forensic `agents/historylog.md` entry that captures what changed, why it changed, how it was verified, and what is risky or next.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Write"]
  offline_ok: true
---

# Historylog Entry (High Signal)

## Purpose
Make changes auditable and reproducible by forcing every run to leave a concise, copy/paste-verifiable historylog entry.

## Quick start
Goal:
- Write a historylog entry that lets a future agent reproduce the state and verification in minutes.

Use when (triggers):
- End of any Builder/QA cycle
- Any change touching retrieval logic, infra, or UI wiring
- After resolving a regression or unclear bug

Do NOT use when (non-goals):
- Essay-style narration of the whole session
- Duplicating full diffs or logs (link/point to commands and files instead)

## Operating constraints
- No secrets: never embed API keys, tokens, passwords, private URLs.
- Be minimal: smallest diffs; no drive-by refactors unless required.
- Be explicit: state assumptions and verify them where possible.
- Be deterministic: prefer exact commands/checklists over vague guidance.
- Keep SKILL.md short: if you exceed ~500 lines, split details into linked files.

## Inputs this Skill expects
Required:
- The exact commands run (and whether they passed)
- List of files changed (git diff --name-only)

Optional:
- Key error messages (1–3 lines max) if something failed
- Any new env vars or toggles introduced/changed

If required inputs are missing:
- Ask for the MINIMUM missing info OR proceed with safest defaults and list assumptions.

## Output contract
Primary deliverable:
- A new entry prepended to the top of `agents/historylog.md` (newest first) following the repo’s existing format.

Secondary deliverables (only if needed):
- Optional: update `agents/tasks.md` status (Done / Blocked) with a pointer to the historylog entry

Definition of DONE (objective checks):
- [ ] Entry includes: What/Why/How to verify + file list
- [ ] All commands are copy-paste runnable
- [ ] Risks and next step are explicit (1–3 bullets)

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
  - historylog
  - DONE
  - How to verify
  - docker compose
  - pytest
- Files to inspect first (prioritized):
   1) agents/historylog.md
   2) agents/tasks.md

### 3) Plan smallest safe change set
Rules:
- Touch the minimum number of files.
- Prefer additive changes over rewrites.
- If multiple approaches exist, choose the simplest that satisfies DONE checks.
- If risk is medium/high, write a micro-plan artifact before coding (see “Verification pattern”).
- Keep entries short: aim for <25 lines.
- If retrieval quality changes, include a before/after note and cite the retrieval report file (if any).

### 4) Implement changes
Implementation checklist:
- [ ] Follow repo conventions (naming, formatting, module boundaries).
- [ ] Add or adjust any needed configuration.
- [ ] Update tests where applicable.
- [ ] Update docs where user-facing behavior changes.
- [ ] Include the exact compose invocation (all `-f` files) if you started the stack.

### 5) Validate locally (choose what exists in the repo)
Run validations in this order:
1) Fast static checks:
   - git diff --name-only
2) Unit/targeted tests:
   - pytest tests/test_database.py tests/test_rag_api.py -v (if applicable)
3) Integration smoke test:
   - docker compose -f infra/compose/docker-compose.pgvector.yml -f infra/compose/docker-compose.mongo.yml -f infra/compose/docker-compose.ollama.yml -f infra/compose/docker-compose.rag.yml -f infra/compose/docker-compose.librechat.yml up -d --build (if applicable)

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
- Leaving out verification commands → always include them, even if you didn’t run them (mark as NOT RUN).
- Vague statements (“fixed retrieval”) → name the file/function and the observed behavior change.
- No risk note → always state what could break and what to watch in QA.

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
- bash scripts/test_chat_formats.sh

Expected output:
- All formats pass (exit 0)

Common failures:
- Test expects running stack → run compose up first

## Example References (concise summaries only)

**How to reference examples:**
- Keep summaries SHORT (1-2 sentences max)
- Reference by stable Example ID (line numbers are brittle across editors/formatters)
- Agents will load full examples only when symptoms match

**Example summaries:**

1. **LibreChat RAG API integration** - Document exact config/endpoint changed with verification commands. See EXAMPLES.md (EX-2025-12-28-01)
2. **Chunking defaults change** - Include old/new values, backfill requirement, corpus impact. See EXAMPLES.md (EX-2025-12-28-02)

**Note:** Full examples with tags and trigger phrases are in `./EXAMPLES.md`.
Agents search that file only when they encounter matching symptoms (context-efficient).
