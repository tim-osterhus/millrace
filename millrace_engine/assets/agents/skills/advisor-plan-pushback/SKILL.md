---
name: advisor-plan-pushback
description: >
  Forces hard pushback on plans/specs by surfacing brittle assumptions, failure modes, and a safer build sequence, with an explicit output contract.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Write", "Web"]
  offline_ok: true
---

# Advisor Plan Pushback

## Purpose
Prevent "vibe plans" from becoming expensive implementation traps by forcing clarity on goals, constraints, assumptions, and failure modes.

Use when (triggers):
- "Does this plan make sense?"
- "Where does this break?"
- "What assumptions must hold?"
- "What's the lowest-risk way to build this?"
- Any plan with unclear success criteria, unclear interfaces, or big-bang integration risk

Do NOT use when (non-goals):
- The user asked for implementation, not critique
- The plan is already a tight, validated task card with explicit DONE checks

## Inputs this Skill expects
Required:
- The plan/spec to critique (message or `agents/tasks.md`)
- Repo context (`README.md` + `agents/outline.md`)

Optional:
- Constraints (SLA/SLO, compliance, offline/online)
- Known unknowns and decision deadlines
- Prior incidents/regressions (from `agents/historylog.md`)

If required inputs are missing:
- Ask up to 3 clarifying questions, OR proceed with conservative assumptions and list them explicitly.

## Output contract
You MUST output these sections, in this order:

1) **Pushback (hard objections)** (1-3 items)
- Each item: what breaks, why it's risky, the smallest fix, and what to do next.

2) **Sanity check (assumptions + missing pieces)**
- Assumptions that must hold (explicit).
- Missing decisions or specs (interfaces, data contracts, auth, rollout, test strategy).

3) **Best-practice alignment (only what is relevant)**
- 3-7 bullets mapping to concrete principles (reliability, operability, security, config, rollback).

4) **Recommendation**
- The lowest-risk sequence to build (vertical slice + checkpoints).
- Fastest feedback loops (tests, probes, observability).
- If applicable: "stop-the-line" criteria (what would change your recommendation).

Definition of DONE (objective checks):
- [ ] 1-3 hard objections are stated clearly and are actionable
- [ ] Assumptions are explicit (not implied)
- [ ] Recommended sequence is incremental and verifiable (not big-bang)
- [ ] Advice is tailored to the stated constraints (not generic best-practice spam)

## Procedure (copy into working response and tick off)
Progress:
- [ ] 1) Re-state the goal and constraints
- [ ] 2) Extract the plan's shape (sequence + dependencies)
- [ ] 3) Run a pre-mortem (failure-mode scan)
- [ ] 4) Emit hard objections (smallest fixes)
- [ ] 5) Propose the lowest-risk build sequence

### 1) Re-state the goal and constraints
- Goal in one sentence.
- Constraints in bullets (security/compliance, offline/online, latency/cost, rollout).
- What would count as success (measurable, if possible).

### 2) Extract the plan's shape (sequence + dependencies)
- List each step as a numbered sequence.
- For each step, identify:
  - Dependencies (data, services, credentials, environments).
  - Reversibility (easy rollback vs irreversible migration).
  - Primary risk (correctness, security, operability, schedule).

### 3) Run a pre-mortem (failure-mode scan)
Assume the project shipped and failed. List the top 5 likely causes. For each:
- Symptom (what goes wrong)
- Root cause (why it went wrong)
- Mitigation (what to change in the plan now)
- Fastest test/probe to validate the mitigation

### 4) Emit hard objections (smallest fixes)
- Choose 1-3 "stop-the-line" issues.
- For each: propose the smallest plan edit that removes the risk (no rewrites).

### 5) Propose the lowest-risk build sequence
Rules:
- Prefer a vertical slice with end-to-end evidence over parallel big-bang work.
- Front-load the highest-uncertainty dependency with a small spike/probe.
- Require at least one deterministic verification gate per milestone (tests, scripts, checks).
- If a major decision is unsettled, recommend an ADR to freeze it before implementation.

## Pitfalls / gotchas
- Turning critique into a wall of generic principles instead of making 1-3 concrete objections.
- Ignoring rollout/rollback and observability (the plan "works" until it doesn't).
- Letting ambiguous requirements slip through without making assumptions explicit.

## References (lightweight)
- Premortem technique (Gary Klein): https://www.gary-klein.com/project-premortem.html
- Architecture Decision Records (ADR): https://adr.github.io/ and https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions.html
- 12-Factor App (configuration/logging/process model): https://12factor.net/

