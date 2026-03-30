# Examples (Codebase Audit + Documentation)

Append new examples to the END of this file and never change existing Example IDs.

---

## EX-2025-12-28-01: Security + dependency audit with reproducible findings

**Tags**: `security`, `dependencies`, `reporting`

**Trigger phrases**:
- "check for security vulnerabilities"
- "audit this repo"
- "are there any obvious security issues"

**Date**: 2025-12-28

**Problem**:
User hands over a repo and wants a practical security audit (not a theoretical essay), but without behavior changes.

**Cause**:
- Findings get reported as "possible" without concrete file/line evidence.
- The audit omits reproduction commands, so nobody can confirm the risk.
- The audit recommends architecture redesigns instead of scoped mitigations.

**Fix**:
Produce an audit report that includes:
- "How to run / verify" commands (tests/build/smoke), or explicitly mark as NOT AVAILABLE with why.
- Findings grouped by severity, each with:
  - exact file/line pointers
  - a short evidence excerpt
  - a scoped fix sketch (doc-only here; code changes are follow-ups)
  - verification steps
- Dependency risk section:
  - list direct dependencies and any tool output available (e.g., `npm audit`, `pip-audit`, `govulncheck`, `cargo audit`)

**Prevention**:
- Only mark as a finding when you can point to concrete code/config.
- Separate "confirmed findings" vs "suspicious patterns to investigate".

**References**:
- `agents/skills/codebase-audit-doc/SKILL.md` (audit report format + severity rules)

---

## EX-2025-12-28-02: Documentation coverage pass without changing behavior

**Tags**: `documentation`, `api`, `safety`

**Trigger phrases**:
- "document this codebase"
- "add docstrings and comments"
- "make it easier to onboard engineers"

**Date**: 2025-12-28

**Problem**:
Repo works, but is hard to understand. The request is "better docs" without refactoring.

**Cause**:
- Entry points and public APIs are undocumented or misleading.
- Docs don't state run/test commands, env vars, or operational constraints.

**Fix**:
Deliver doc-only improvements:
- Identify and prioritize the top modules to document first (entrypoints + public APIs).
- Add consistent docstrings and minimal "why" comments at tricky invariants.
- Add/update a short developer quickstart snippet if missing/incorrect.
- Re-run the repo's verification command(s) after doc edits (or at least import/compile).

**Prevention**:
- Keep doc changes scoped (avoid reformatting the entire repo).
- Record the exact verification command(s) run in the report.

**References**:
- `agents/skills/codebase-audit-doc/SKILL.md` (doc coverage checklist)

---

## EX-2025-12-28-03: Complexity hotspot map for a mixed-language monorepo

**Tags**: `analysis`, `complexity`, `prioritization`

**Trigger phrases**:
- "where is the tech debt"
- "what should we clean up first"
- "functions are too long"

**Date**: 2025-12-28

**Problem**:
Monorepo with multiple services. The user wants the fastest path to risk reduction.

**Cause**:
- Work is prioritized by vibes instead of evidence (churn, centrality, risk boundaries).
- Large refactors are suggested without a clear "why this first" map.

**Fix**:
Create a hotspot map (no refactors in this skill):
- Top files by churn if git history exists; otherwise by LOC + centrality + dependency fan-in.
- Call out hotspots near security boundaries (auth, payments, deserialization, file IO, shell-outs).
- Propose follow-up tasks (hand off to cleanup/refactor skill) with clear scope + verification gates.

**Prevention**:
- Keep the hotspot list short (top 5-15).
- Separate "confirmed hazards" vs "candidates to refactor."

**References**:
- `agents/skills/codebase-audit-doc/SKILL.md` (hotspot identification guidance)

---

## EX-2025-12-28-04: Hardening documentation for deployment + configuration

**Tags**: `configuration`, `ops`, `security`

**Trigger phrases**:
- "how do I deploy this"
- "what env vars does this need"
- "we keep misconfiguring it"

**Date**: 2025-12-28

**Problem**:
Incidents are caused by unclear config (CORS, auth keys, debug flags, ports).

**Cause**:
- Required env vars and defaults are undocumented (or scattered).
- "Safe defaults" are unclear, so operators guess.

**Fix**:
Doc-only hardening:
- Create a single source of truth for required configuration (near the config schema).
- Document defaults + safe recommended values.
- Add a "misconfig traps" section: what breaks and what becomes unsafe.
- If a default is unsafe, document it as unsafe and open a follow-up task (do not change behavior here).

**Prevention**:
- Keep docs tied to the actual config schema (avoid drift).
- Require every env var doc entry to include: purpose, default, safe value, and how to verify.

**References**:
- `agents/skills/codebase-audit-doc/SKILL.md` (config audit checklist)

