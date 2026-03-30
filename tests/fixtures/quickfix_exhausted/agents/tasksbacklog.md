# Task Backlog

## 2026-03-19 - Ship the happy path

- **Goal:** Execute the normal runtime cycle.
- **Context:** Run 06 needs an exhausted quickfix path.
- **Spec-ID:** SPEC-HAPPY-PATH
- **Dependencies:** none
- **Deliverables:**
  - Escalate after quickfix attempts are exhausted and then recover locally.
- **Acceptance:** The runtime reaches IDLE with the card archived.
- **Notes:** Keep the routing deterministic.
