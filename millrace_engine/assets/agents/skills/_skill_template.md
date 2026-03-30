# Skill Template (SKILL.md)

NOTES (keep as-is unless you have a reason):
- This template uses strict YAML frontmatter for discovery, followed by a short, procedural body.
- Keep the body concise. If details get long, link to a sibling file (examples.md, reference.md, etc.).
- Prefer checklists, contracts, and commands over essays.

---
name: <kebab-case-skill-name>               # REQUIRED. <=64 chars. lowercase letters/numbers/hyphens only.
description: >                              # REQUIRED. <=1024 chars. Third-person. What it does + when to use it.
  <One sentence: what this skill does. One sentence: when to use it, with concrete triggers/keywords.>
# Compatibility metadata (recommended; used by Millrace skill tooling):
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]  # Edit as needed.
  tools: ["Read", "Grep", "Bash", "Write"]           # Edit as needed.
  offline_ok: true                                  # True if no internet/web browsing is required.
# Optional metadata (keep only what fits your environment):
# version: "0.1.0"
# owner: "<team-or-handle>"
# tags: ["rag", "infra", "qa"]
# license: "See LICENSE.txt"
# Claude Code only (if supported in your environment):
# allowed-tools: Read, Grep, Glob, Bash, Write
---

# <Human-readable Skill Title>

## Purpose
<1–2 sentences max. What this skill exists to do (not a full re-explanation of the task).>

## Quick start
Goal:
- <single-line outcome>

Use when (triggers):
- <trigger 1: very concrete; e.g., “adding a new FastAPI endpoint”>
- <trigger 2>
- <trigger 3>
- <trigger 4>

Do NOT use when (non-goals):
- <clear disqualifier 1>
- <clear disqualifier 2>

## Operating constraints
- No secrets: never embed API keys, tokens, passwords, private URLs.
- Be minimal: smallest diffs; no drive-by refactors unless required.
- Be explicit: state assumptions and verify them where possible.
- Be deterministic: prefer exact commands/checklists over vague guidance.
- Keep SKILL.md short: if you exceed ~500 lines, split details into linked files.

## Inputs this Skill expects
Required:
- <input 1: file/folder/config that must exist>
- <input 2>

Optional:
- <input A: logs, screenshots, sample payloads>
- <input B>

If required inputs are missing:
- Ask for the MINIMUM missing info OR proceed with safest defaults and list assumptions.

## Output contract
Primary deliverable:
- <exact output shape; e.g., “edits to X/Y files + updated docs + validation commands run”>

Secondary deliverables (only if needed):
- <e.g., diff summary, report.md, checklist ticked, new task card>

Definition of DONE (objective checks):
- [ ] <check 1: verifiable; e.g., “tests pass: `pytest -q`”>
- [ ] <check 2: verifiable; e.g., “service starts: `docker compose up` and healthcheck OK”>
- [ ] <check 3: verifiable; e.g., “docs updated: README section X reflects new behavior”>

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
  - <term/pattern 1>
  - <term/pattern 2>
- Files to inspect first (prioritized):
  1) <file/path>
  2) <file/path>
  3) <file/path>

### 3) Plan smallest safe change set
Rules:
- Touch the minimum number of files.
- Prefer additive changes over rewrites.
- If multiple approaches exist, choose the simplest that satisfies DONE checks.
- If risk is medium/high, write a micro-plan artifact before coding (see “Verification pattern”).

### 4) Implement changes
Implementation checklist:
- [ ] Follow repo conventions (naming, formatting, module boundaries).
- [ ] Add or adjust any needed configuration.
- [ ] Update tests where applicable.
- [ ] Update docs where user-facing behavior changes.

### 5) Validate locally (choose what exists in the repo)
Run validations in this order:
1) Fast static checks:
   - <lint/typecheck command>
2) Unit/targeted tests:
   - <test command>
3) Integration smoke test:
   - <build/run command>

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
- <pitfall 1 + how to avoid>
- <pitfall 2 + how to avoid>
- <pitfall 3 + how to avoid>

## Progressive disclosure (one level deep)
If this Skill needs detail, link it directly from HERE (avoid chains of references):
- Reference/specs: ./reference.md
- Advanced workflow: ./advanced.md

## Verification pattern (recommended for medium/high risk)
Use this when changes touch infra, retrieval logic, licensing, or security:
1) Analyze
2) Write a machine-checkable plan artifact (e.g. report.md / changes.json)
3) Validate assumptions (paths, versions, config)
4) Execute changes
5) Verify DONE checks

## Optional: scripts section (ONLY if repo already uses scripts)
Run:
- <command>

Expected output:
- <what success looks like>

Common failures:
- <failure + fix>

## Example References (concise summaries only)

**How to reference examples:**
- Keep summaries SHORT (1-2 sentences max)
- Reference by stable Example ID (line numbers are brittle across editors/formatters)
- Agents will load full examples only when symptoms match

**Example summaries:**

1. **[Short title]** - Brief description of the issue/fix. See EXAMPLES.md (EX-YYYY-MM-DD-01)
2. **[Another issue]** - One-liner summary. See EXAMPLES.md (EX-YYYY-MM-DD-02)
3. **[Edge case]** - Quick note. See EXAMPLES.md (EX-YYYY-MM-DD-03)

**Note:** Full examples with tags and trigger phrases are in `./EXAMPLES.md`.
Agents search that file only when they encounter matching symptoms (context-efficient).
