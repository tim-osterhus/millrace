# Advisor Architecture Sanity Check Examples

## EX-2026-02-07-01: Premature Microservices With No Operability Plan

**Trigger phrases**
- "Should we split this into microservices?"
- "Let's use queues + events for everything."

**Cause**
- Architecture expanded service boundaries without a clear constraint forcing it, and without an operability plan (deploy/rollback, metrics/logs, incident response), creating reliability and debugging risk.

**Fix**
- Recommend a simpler boundary-first design:
  - Keep a modular monolith (or minimal service split) until scale/ownership constraints force decomposition.
  - Define explicit interfaces and data ownership.
  - Add baseline observability (structured logs + key metrics) and a rollback strategy.
  - Create ADRs for the irreversible decisions (service split, data store choice, auth model).

**Prevention**
- Run an architecture sanity-check before committing to irreversible complexity:
  - constraints first, then boundaries, then operability/security, then optimization

