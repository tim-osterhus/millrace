---
name: compose-stack-change-protocol
description: >
  Modifies Docker Compose definitions for a multi-service stack while preserving service names,
  volumes, production defaults, and reproducible bring-up. Use when changing infrastructure
  manifests that impact runtime behavior.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Grep", "Bash", "Write"]
  offline_ok: true
---

# Compose Stack Change Protocol

## Purpose
Make compose changes safely and reproducibly by enforcing a minimal-diff protocol and required bring-up verification.

## Quick start
Goal:
- Change compose safely and prove the stack still boots and passes smoke checks.

Use when (triggers):
- Editing files under `infra/compose/`
- Adding/removing services or changing ports
- Changing volumes, networks, or env wiring between services

Do NOT use when (non-goals):
- Changing runtime behavior that belongs in app code
- Introducing new infrastructure dependencies without explicit approval

## Operating constraints
- No secrets: never embed API keys, tokens, passwords, private URLs.
- Be minimal: smallest diffs; no drive-by refactors unless required.
- Be explicit: state assumptions and verify them where possible.
- Be deterministic: prefer exact commands/checklists over vague guidance.
- Keep SKILL.md short: if you exceed ~500 lines, split details into linked files.

## Inputs this Skill expects
Required:
- The exact compose file list used to launch the stack (keep stable across docs/tests)
- The current `infra/compose/docker-compose.*.yml` you intend to modify

Optional:
- Deployment constraints from `README.md`
- Smoke checklist results or existing runbooks

If required inputs are missing:
- Ask for the MINIMUM missing info OR proceed with safest defaults and list assumptions.

## Output contract
Primary deliverable:
- A minimal compose diff + verified stack bring-up using the standard multi-file invocation.

Secondary deliverables (only if needed):
- Optional: update docs that show compose commands (README/docs)
- Optional: add/adjust healthchecks to make failures obvious

Definition of DONE (objective checks):
- [ ] `docker compose ... config` succeeds (no schema errors)
- [ ] Stack comes up (`up -d --build`) and containers are healthy
- [ ] Critical endpoints are reachable locally (or via configured network)

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
- List constraints (security, offline/LAN-only, no new deps, etc.).
- List assumptions explicitly.

### 2) Locate entrypoints + relevant files
Run targeted searches:
- Search terms:
  - infra/compose
  - docker-compose
  - healthcheck
  - network_mode
- Files to inspect first (prioritized):
   1) infra/compose/docker-compose.*.yml
   2) docs/ (if they reference compose commands)

### 3) Plan smallest safe change set
Rules:
- Touch the minimum number of files.
- Prefer additive changes over rewrites.
- If multiple approaches exist, choose the simplest that satisfies DONE checks.
- If risk is medium/high, write a micro-plan artifact before coding.
- Preserve service names used by scripts/tests (treat them as API).
- Prefer adding an override file rather than editing multiple base files.

### 4) Implement changes
Implementation checklist:
- [ ] Follow repo conventions (naming, formatting, module boundaries).
- [ ] Add or adjust any needed configuration.
- [ ] Update tests where applicable.
- [ ] Update docs where user-facing behavior changes.
- [ ] Run `docker compose ... config` and inspect the rendered output for unintended changes.

### 5) Validate locally (choose what exists in the repo)
Run validations in this order:
1) Fast static checks:
   - `docker compose -f infra/compose/<file>.yml config >/dev/null`
2) Unit/targeted tests:
   - Run repo-native tests if service wiring changed
3) Integration smoke test:
   - `docker compose -f infra/compose/<file1>.yml -f infra/compose/<file2>.yml up -d --build`
   - `docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | head`

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
- Breaking scripts/tests by renaming services → treat names as API; don’t change them casually.
- Silent misconfig via env vars → document any new/changed vars and defaults.
- Accidentally enabling outbound network access → re-run no-egress verification after compose changes.

## Progressive disclosure (one level deep)
If this Skill needs detail, link it directly from HERE (avoid chains of references):

- Examples: ./EXAMPLES.md

## Verification pattern (recommended for medium/high risk)
Use this when changes touch infra, retrieval logic, licensing, or security:
1) Analyze
2) Write a machine-checkable plan artifact (e.g. report.md / changes.json)
3) Validate assumptions (paths, versions, config)
4) Execute changes
5) Verify DONE checks

## Example References (concise summaries only)

**How to reference examples:**
- Keep summaries SHORT (1-2 sentences max)
- Reference by stable Example ID (line numbers are brittle across editors/formatters)
- Agents will load full examples only when symptoms match

**Example summaries:**

1. **Port change** - Change only published port mapping, verify at new port. See EXAMPLES.md (EX-2026-02-04-01)
2. **Optional helper service** - Add new compose file without altering base stack. See EXAMPLES.md (EX-2026-02-04-02)

**Note:** Full examples with tags and trigger phrases are in `./EXAMPLES.md`.
