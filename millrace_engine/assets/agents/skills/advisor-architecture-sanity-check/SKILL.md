---
name: advisor-architecture-sanity-check
description: >
  Opinionated architecture sanity check: identify missing decisions, high-risk failure modes, and the simplest viable architecture with operability/security baked in.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Write", "Web"]
  offline_ok: true
---

# Advisor Architecture Sanity Check

## Purpose
Keep architecture discussions grounded: surface the real constraints, propose the simplest viable design, and call out risks that will cause rework or operational pain.

Use when (triggers):
- "What architecture should we use?"
- "Sanity-check this design"
- "Will this scale/reliably operate?"
- New service boundaries, data stores, auth models, queue/event designs

Do NOT use when (non-goals):
- Detailed implementation planning for a well-understood, existing architecture
- Pure UX/content/design feedback without backend or operational implications

## Inputs this Skill expects
Required:
- A short description of the system/change (message or `agents/tasks.md`)
- Repo context (`README.md` + `agents/outline.md`)

Optional:
- Deployment target and constraints (offline/online, SLAs/SLOs, compliance)
- Current architecture diagram/notes (if present)
- Existing incident history (`agents/historylog.md`)

If required inputs are missing:
- Ask for the minimum missing info, OR proceed with conservative defaults and list assumptions.

## Output contract
You MUST output these sections, in this order:

1) **System sketch**
- Components/services, data stores, key APIs, and trust boundaries (short bullets).

2) **Risks (top 5)**
- Each risk: impact, likelihood, and the smallest mitigation.

3) **Missing decisions**
- Concrete decisions that must be made before implementation (interfaces, data contracts, auth, rollout, observability, test strategy).

4) **Simplest viable architecture**
- What to build first (vertical slice), and what to defer.
- Explicit tradeoffs (what you are not optimizing for).

5) **Recommendation**
- Next 1-3 actions (example: write ADRs, define SLOs, add logging/metrics, threat model).

Definition of DONE (objective checks):
- [ ] Risk list is specific to the described system (not generic)
- [ ] Mitigations are minimal and actionable
- [ ] Proposed design is the simplest that satisfies constraints
- [ ] Observability + rollout/rollback are addressed explicitly

## Procedure (copy into working response and tick off)
Progress:
- [ ] 1) Establish constraints and non-negotiables
- [ ] 2) Draw the system sketch (boundaries + data)
- [ ] 3) Run an operability/reliability/security scan
- [ ] 4) Choose the simplest viable architecture
- [ ] 5) Convert into next actions (ADRs + gates)

### 1) Establish constraints and non-negotiables
- Identify: users, data sensitivity, runtime constraints, and required uptime/recovery.
- If no SLO exists: propose one (even rough) and label it as a placeholder.

### 2) Draw the system sketch (boundaries + data)
- List:
  - Public entrypoints (UI/API)
  - Internal services/modules
  - Data stores and ownership
  - Integration points (3rd party, queues, webhooks)
  - Trust boundaries (authN/authZ, secrets, network)

### 3) Run an operability/reliability/security scan
Use these lenses and only report what matters:
- Reliability: single points of failure, retries/timeouts, backpressure, data integrity, migrations.
- Operability: logs/metrics/tracing, on-call/debuggability, runbooks, safe deploys.
- Performance/cost: hot paths, caching, rate limits, capacity risks.
- Security: authZ model, input validation, secret handling, least privilege, auditability.
- Testability: deterministic unit/integration tests, smoke gates, fixtures.

### 4) Choose the simplest viable architecture
Rules:
- Prefer fewer moving parts until a constraint forces decomposition.
- Prefer explicit interfaces and contracts over implicit coupling.
- Prefer reversible migrations/feature flags over one-way changes.
- If a key decision is contentious or irreversible, recommend an ADR.

### 5) Convert into next actions (ADRs + gates)
- Recommend 1-3 ADRs (only for consequential decisions).
- Recommend the first validation gates (tests + minimal observability).
- Recommend a rollout/rollback plan (what happens if this fails in prod?).

## Pitfalls / gotchas
- Over-designing for scale before confirming the constraints that require it.
- Ignoring operability (debugging, logging, rollout) until after launch.
- Treating "security" as a later hardening step instead of a design constraint.

## References (lightweight)
- AWS Well-Architected Framework: https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html
- Google SRE (free online books): https://sre.google/books/
- OWASP ASVS: https://owasp.org/www-project-application-security-verification-standard/
- NIST SSDF: https://csrc.nist.gov/Projects/ssdf
- ADR basics: https://adr.github.io/

