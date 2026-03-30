---
name: codebase-audit-doc
description: >
  Evaluate the repo for security risks, correctness hazards, complexity hotspots, and documentation gaps,
  then produce a prioritized audit report and doc-only improvement plan. Use for repo reviews, security
  triage, onboarding/documentation passes, and "what should we clean up first" assessments where behavior
  must not change or violate guardrails.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Grep", "Write"]
  offline_ok: true
---
# Codebase Audit + Documentation

## Purpose
Produce a risk-ranked audit report and doc-only patch plan backed by concrete evidence (paths, commands, reproduction) with minimal speculation.

## Quick start
Goal:
- Produce a **risk-ranked audit report** (security + correctness + maintainability) and a **doc-only patch plan** that improves clarity without changing behavior.

Use when (triggers):
- "audit this repo" / "security review"
- "what should we clean up first"
- "document this codebase" / "onboarding is hard"
- You need evidence-backed findings without changing runtime behavior

Do NOT use when (non-goals):
- You are asked to refactor, rename, or change runtime behavior (hand off to a cleanup/refactor skill)
- You need to implement fixes (this skill is doc-only)

Non-negotiables:
- Prefer **evidence** (exact file/line, reproduction commands) over speculation.
- Keep fixes **doc-only** in this skill (comments, docstrings, type hints that do not affect runtime, documentation files).
- Preserve project guardrails from `README.md`.

## Operating constraints
- Do not refactor, rename, or change runtime behavior.
- If a finding needs code changes, document it as a follow-up task for the cleanup/refactor skill.
- Don’t “boil the ocean”: aim for the top 5–20 issues that actually matter.

## Inputs this Skill expects
Required:
- Repo path (or a snapshot) and the primary language(s)
- How to run: build, tests, lint (or confirmation that none exist)

Optional but high-value:
- Deployment context (internet-facing vs internal; single-tenant vs multi-tenant)
- Auth boundaries, data sensitivity, and any compliance constraints

## Output contract
Primary deliverable:
- An audit report (markdown) with findings grouped by severity and backed by exact file/line pointers and reproduction/verification commands.

Secondary deliverables (doc-only only):
- A doc-only patch plan (and optionally doc-only edits) that improves clarity without changing runtime behavior.

Definition of DONE (objective checks):
- [ ] Findings are grouped by severity and backed by concrete evidence (paths, snippets, commands)
- [ ] "How to run / verify" exists (or is explicitly marked as NOT AVAILABLE with why)
- [ ] Any suggested code changes are explicitly labeled as follow-ups (not implemented here)

## Procedure (copy into working response and tick off)
Progress:
- [ ] 1) Establish baseline context
- [ ] 2) Create a threat-model-lite
- [ ] 3) Security triage (confirmed findings only)
- [ ] 4) Correctness + maintainability hazards
- [ ] 5) Documentation coverage pass
- [ ] 6) Deliverables

### 1) Establish baseline context (10–20 min)

- Identify languages, frameworks, entrypoints, and build tooling.
- Find and record the **one command** that best represents “the repo works” (tests, build, or minimal smoke check).
- Note the runtime surfaces: HTTP endpoints, CLIs, cron jobs, queues, webhooks.
- Record any environment boundaries (offline constraints, allowed dependencies).

### 2) Create a threat-model-lite
Answer these in 3–5 bullets (don’t write an essay):
- What inputs can an attacker control?
- What privileged actions exist (filesystem, network, exec, DB writes)?
- Where are secrets stored/loaded?
- What trust boundaries exist (client/server, service/service, tenant/tenant)?

### 3) Security triage (confirmed findings only)
Run what you can; fall back to manual inspection when tools aren’t available.

Dependency risk:
- If JS/TS: `npm audit` / `pnpm audit` / `yarn audit`
- If Python: `pip-audit` (or dependency lock review)
- If Go: `govulncheck`
- If Rust: `cargo audit`

Secret exposure:
- Scan for committed secrets and “accidental keys” patterns (tokens, private keys, API keys).

SAST heuristics (manual or tools like Semgrep):
- Injection vectors: SQL/NoSQL, shell, template, LDAP
- SSRF, path traversal, unsafe deserialization
- AuthZ gaps (missing checks), insecure defaults, CORS/CSP issues
- Cryptography misuse (homegrown crypto, nonces reused, weak RNG)
- Logging of secrets/PII

Severity rules (keep it simple):
- **Critical**: remote exploit + high impact, or secret compromise
- **High**: auth bypass, RCE surface, data exposure likely
- **Medium**: exploitable but mitigated by context, or requires chaining
- **Low**: best-practice gaps with low practical impact

### 4) Correctness + maintainability hazards
Look for “quietly wrong” code:
- Timezones, money/precision, ID parsing, unicode, locale assumptions
- Retry/timeout handling, idempotency, race conditions, partial failures
- Error handling that hides failures (broad exceptions, ignored promises)

Complexity hotspots (don’t refactor here):
- Identify long functions, deeply nested logic, duplicated code, and “god modules”.
- Flag hotspots near security boundaries first (auth, payments, file IO, deserialization).

### 5) Documentation coverage pass (doc-only edits allowed)
Prioritize docs by leverage:
1) How to run (dev quickstart)
2) Entry points + system boundaries
3) Public APIs and “most-called” modules
4) Tricky invariants and edge cases
5) Guardrails: risk constraints, data handling, audit requirements

Doc coverage checklist:
- Each public module has: purpose, inputs/outputs, side effects, failure modes
- Each public function/class has: contract, params, returns, exceptions
- Non-obvious decisions have “why” comments (not “what” comments)
- Config is documented: required env vars, defaults, safe recommendations

### 6) Deliverables (required)
Produce both:

A) **Audit report** (pasteable markdown)
- Context (what repo is, how it runs)
- Reproduction commands
- Findings table (severity, file/line, evidence, fix sketch, verification)
- Hotspot map (top 5–15)
- Doc-only patch plan (top 5–15)

B) **Doc-only changes** (optional but recommended when safe)
- Small, targeted commits adding docstrings/comments and correcting misleading docs.
- Keep changes scoped; avoid large reformatting diffs.

## Audit report format (copy-paste)
Use this structure:

1. Context
2. How to run / verify (exact commands)
3. Findings (confirmed)
   - Critical
   - High
   - Medium
   - Low
4. Hotspots worth refactoring (follow-up tasks)
5. Documentation patch plan
6. Open questions / assumptions

## Red flags (avoid)
- “Maybe vulnerable” without code evidence
- Recommending a redesign when a scoped fix exists
- Mixing doc edits with behavior changes
