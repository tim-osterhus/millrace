---
name: codebase-safe-cleanup
description: >
  Behavior-preserving code cleanup and compaction with strict verification gates.
  Only runs when the repo has a runnable build/test command set. Refactors safely, removes dead code,
  reduces duplication, and hardens scripts without introducing breakage or violating guardrails.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Grep", "Bash", "Write"]
  offline_ok: true
---
# Codebase Safe Cleanup (Strict)

## Purpose
Make the codebase smaller, clearer, and safer without changing externally observable behavior, using strict verification gates.

## Quick start
Goal:
- Make the codebase **smaller, clearer, safer** without changing externally observable behavior.

Strict mode (non-negotiables):
- **No refactor unless verification commands exist and pass.**
- Run verification **before** changes (baseline) and **after every change batch**.
- If verification cannot be run locally/CI, **STOP** and output a plan + what you need (do not modify code).

Project constraints (must preserve):
- Guardrails in `README.md` (data handling, safety, review, deployment limits).
- Avoid touching legacy/archived paths unless explicitly requested (not part of runtime path).

Use when (triggers):
- "clean up this repo" / "refactor for readability" BUT behavior must not change
- The repo has (or can provide) runnable build/test/smoke commands
- The change request is primarily compaction: dedupe, dead code removal, simplification, hygiene

Do NOT use when (non-goals):
- There is no runnable verification contract (tests/build/smoke) and none can be provided
- The request requires behavior changes (API/schema changes, new features, rewrites)
- The user wants style churn (mass renames/reformatting) instead of risk-reduced improvements

## What counts as "verification"
You must identify a minimal, repeatable command set that proves the repo is healthy.
Examples (use repo-native equivalents):
- Build: `make build`, `npm run build`, `go build ./...`, `cargo build`, `dotnet build`
- Test: `make test`, `npm test`, `pytest`, `go test ./...`, `cargo test`, `dotnet test`
- Lint/typecheck (optional but recommended): `ruff`, `eslint`, `mypy`, `golangci-lint`, `cargo clippy`

**Acceptance rule:** at least **one** of:
- a real test suite, OR
- a build + smoke check that exercises the program entrypoint (documented and repeatable)

If none exist, do not “guess.” Create a short **Verification Bootstrapping Plan** and stop.

## Allowed changes (when strict gates are satisfied)
Behavior-preserving:
- Extract helper functions, reduce nesting, simplify conditionals
- Remove unreachable/dead code, unused vars/imports, redundant wrappers
- Replace copy-paste blocks with shared utilities
- Tighten error handling without changing error surfaces (same status codes/messages/contracts)
- Improve dependency hygiene (lockfiles, pinning) *only if verification still passes*
- Add/adjust comments/docstrings/type hints that do not alter runtime behavior

## Disallowed changes
- Any API/CLI/DB schema changes unless explicitly required and covered by tests
- “Refactors” that rely on intuition instead of verification
- Large rewrites, framework migrations, stylistic churn, mass renaming
- Security “fixes” that change auth/permissions semantics without explicit sign-off + tests
- Any change that weakens guardrails from `README.md`

## Inputs this Skill expects
Required:
- Repo root (working tree) with read access to build/test/run instructions
- A runnable verification contract (tests/build/smoke) OR explicit user-provided commands to mirror CI

Optional:
- Active task card (`agents/tasks.md`) and/or scope notes
- `agents/expectations.md` (what QA will verify)
- CI config / pipeline logs (to mirror authoritative checks)

If required inputs are missing:
- Do not guess. Stop and request the minimum missing info (exact commands or CI job to mirror).

## Output contract
Primary deliverable:
- A focused, behavior-preserving cleanup diff with evidence that verification passed after each batch.

Required artifacts in the response:
1) **Verification Contract** (commands + baseline result)
2) **Cleanup Plan** (<= 5 batches + risk)
3) **Change log** (what changed, where, why)
4) **Evidence** that verification passed after each batch (command output summaries)

Definition of DONE (objective checks):
- [ ] Baseline verification was run and passed before any refactor batch
- [ ] Verification passed after every change batch (no stacked failures)
- [ ] No externally observable behavior changes introduced (within the limits of the verification contract)
- [ ] Cleanup plan executed in small batches (<= ~200 LOC per batch unless purely mechanical)
- [ ] Any remaining risk/unknowns are documented with the exact follow-up needed

## Procedure (strict, repeatable)
### 0) Establish the Verification Contract
1. Locate how the repo is run/tested:
   - read `README`, `CONTRIBUTING`, `Makefile`, `package.json`, `pyproject.toml`, CI config, `docker-compose.yml`
2. Write down:
   - `VERIFY_BASELINE`: commands to run on clean checkout
   - `VERIFY_AFTER`: commands to run after each batch (usually same)
3. Execute baseline and capture output.

If baseline fails, **do not refactor**. Fix the baseline first (only the minimal fix) or escalate.

### 1) Map cleanup targets (risk-ranked)
Identify and rank:
- Hotspots: largest/most complex files, longest functions, high churn areas
- Duplication clusters: repeated logic in multiple places
- Security risk surfaces: input parsing, auth boundaries, deserialization, shell exec, path joins, SQL/ORM raw queries
- Script fragility: brittle bash/PowerShell, missing `set -euo pipefail`, unchecked return codes

Output a **Cleanup Plan** with ≤ 5 batches, each batch:
- exact files/symbols
- intent (“reduce cyclomatic complexity”, “dedupe parser”, “remove dead module”)
- risk level (low/med/high)
- expected verification commands

### 2) Execute in small, testable batches
Rules:
- One batch = one coherent refactor theme, ≤ ~200 LOC touched (unless purely mechanical)
- Prefer mechanical transforms (safe, reversible) over cleverness
- After each batch:
  - run verification
  - if failing: revert or fix immediately; do not stack failures

### 3) Compaction techniques that don’t break things
- Guard-clauses to reduce indentation
- Replace repeated literals with constants in the same module (avoid cross-module churn)
- Collapse duplicated control flow into one well-named helper
- Convert long `if/else` ladders to dispatch tables only when behavior is trivially provable and tested
- Remove unused feature flags / code paths only when proven unreachable (search + config check + tests)

### 4) Security hardening within behavior constraints
Do **not** redesign security. Instead:
- Add missing validation checks where they already *should* exist (and are implied by current contract)
- Ensure dangerous operations have explicit allowlists (paths, commands) when already intended
- Reduce exposure: limit shelling out, centralize sanitization, avoid string-built SQL
- Prefer “fail closed” only if the current behavior already fails closed (tests must confirm)

### 5) Documentation touch-ups (only after code is stable)
- Update docstrings/comments for changed functions
- Update `README` run/test instructions if discovered
- Add short “Why” notes for non-obvious refactors

## Outputs this skill must produce
1. **Verification Contract** (commands + baseline result)
2. **Cleanup Plan** (batches + risk)
3. **Change log** (what changed, where, why)
4. **Evidence** that verification passed after each batch (command output summaries)

## Failure modes (what to do)
- No runnable verification: stop, write bootstrapping plan, request commands.
- Tests flaky/slow: shrink batches, add focused smoke tests (only with explicit permission).
- Security issue found that requires semantics change: hand off to audit skill for spec + approval.

## Example References (concise summaries only)
- EX-2026-02-04-01: Small refactor allowed only after baseline + per-batch verification gates.
- EX-2026-02-04-02: Refactor refused when no verification contract exists; produce a bootstrapping plan instead.
- EX-2026-02-04-03: Dead code removal only with proof (routing/config/search + verification).
- EX-2026-02-04-04: Security surface hardening without semantics changes, backed by tests/verification.
