# Examples (Codebase Safe Cleanup - Strict)

Append new examples to the END of this file and never change existing Example IDs.

---

## EX-2026-02-04-01: Refactor allowed (verification exists and passes)

**Tags**: `refactor`, `verification-gates`

**Trigger phrases**:
- "simplify the parser module; it's a mess"
- "refactor for readability, but do not change behavior"

**Date**: 2026-02-04

**Problem**:
The user wants a cleanup refactor, but the repo must remain behavior-identical.

**Cause**:
Refactors tend to drift behavior when they are not gated by repeatable build/test/smoke commands.

**Fix**:
- Find runnable verification commands (e.g., `make test`, `make lint`) in repo docs/config.
- Run baseline verification and record the result.
- Execute cleanup in small batches (e.g., extract helper, reduce nesting, remove dead branches).
- Re-run verification after every batch; stop and fix immediately if it fails.

**Prevention**:
- Never start a "cleanup" batch until baseline verification passes.
- Keep batches small enough that failures are attributable to one change theme.

**References**:
- `agents/skills/codebase-safe-cleanup/SKILL.md` (strict gates + batching rules)

---

## EX-2026-02-04-02: Refactor refused (no verification contract)

**Tags**: `blocked`, `verification-missing`

**Trigger phrases**:
- "clean up these scripts and shorten functions"
- "refactor the codebase" (but repo has no run/test commands)

**Date**: 2026-02-04

**Problem**:
The request is to refactor, but the repo provides no runnable verification (tests/build/smoke).

**Cause**:
Without a verification contract, "behavior-preserving" becomes guesswork and is likely to introduce regressions.

**Fix**:
- Do not modify code.
- Produce a short **Verification Bootstrapping Plan**:
  - minimal run command (how to start the main entrypoint)
  - minimal smoke check (how to confirm it works)
  - recommended baseline checks (lint/typecheck) if the stack supports it
- Ask for the exact command(s) to mirror CI if CI exists.

**Prevention**:
- Treat "no verification" as a hard stop for cleanup/refactor work.
- Require at least one authoritative check (tests OR build+smoke) before editing.

**References**:
- `agents/skills/codebase-safe-cleanup/SKILL.md` (acceptance rule + stop conditions)

---

## EX-2026-02-04-03: Dead code removal with proof

**Tags**: `dead-code`, `risk-reduction`

**Trigger phrases**:
- "remove the legacy v1 API code"
- "delete unused modules"

**Date**: 2026-02-04

**Problem**:
The user wants to delete legacy code paths without breaking runtime behavior.

**Cause**:
"Dead" code is often still referenced via routing, feature flags, config, or dynamic imports.

**Fix**:
- Prove the code is unreachable:
  - search routing/config for references
  - confirm feature flags are removed/disabled
  - confirm no dynamic import paths point to the module
- Remove the dead modules and associated tests/config (only if still unreachable).
- Run the full verification contract after the removal.
- Document the evidence (what you searched + what verification was run).

**Prevention**:
- Do not remove code based on "looks unused"; require proof and verification.

**References**:
- `agents/skills/codebase-safe-cleanup/SKILL.md` (proof-first dead code rules)

---

## EX-2026-02-04-04: Security surface hardening without semantics change

**Tags**: `security`, `hardening`, `no-behavior-change`

**Trigger phrases**:
- "I'm worried about path traversal"
- "harden this handler but keep behavior the same"

**Date**: 2026-02-04

**Problem**:
The user suspects a security risk, but the request is still scoped as behavior-preserving cleanup.

**Cause**:
Security improvements often require semantics changes (fail-closed vs fail-open), which can break existing behavior if not covered by tests.

**Fix**:
- Identify the exact risky surface (e.g., a file download handler).
- Only apply hardening that is already implied by current contracts/tests/docs (no redesign).
- Add a regression test only if a test harness already exists (and is in scope).
- Run verification; ship only if verification passes.

**Prevention**:
- If risk mitigation requires semantics changes, stop and hand off to an audit/spec task.

**References**:
- `agents/skills/codebase-safe-cleanup/SKILL.md` (hardening constraints + escalation rule)
