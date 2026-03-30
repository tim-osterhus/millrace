# Advisor Plan Pushback Examples

## EX-2026-02-07-01: Big-Bang Plan With No Rollback Or Evidence

**Trigger phrases**
- "Does this plan make sense?"
- "We will just build it end-to-end then test at the end."

**Cause**
- The plan had no measurable success criteria, no rollback path, and deferred integration/testing to the end, making failures late and expensive.

**Fix**
- Add a vertical-slice sequence with checkpoints:
  - Define DONE checks (one sentence each).
  - Introduce a minimal spike to validate the riskiest dependency first.
  - Add an ADR for the key architectural decision.
  - Add deterministic verification per milestone (unit + one integration smoke).
  - Add rollout/rollback notes (feature flag or reversible migration plan).

**Prevention**
- Always require:
  - 1-3 hard objections (stop-the-line issues) before approving the plan
  - explicit assumptions + a pre-mortem scan
  - an incremental build sequence with evidence gates

