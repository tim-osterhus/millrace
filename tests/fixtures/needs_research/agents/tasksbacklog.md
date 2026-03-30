# Task Backlog

## 2026-03-19 - Ship the happy path

- **Goal:** Execute the normal runtime cycle.
- **Context:** Run 06 needs the research handoff path.
- **Spec-ID:** SPEC-HAPPY-PATH
- **Dependencies:** none
- **Deliverables:**
  - Quarantine the active task when local execution cannot proceed.
- **Acceptance:** The runtime reaches IDLE with the task frozen for research.
- **Notes:** Keep the routing deterministic.

## 2026-03-20 - Research follow-up task

- **Goal:** Preserve queue state during research handoff.
- **Context:** Run 06 freezes backlog state when execution needs research.
- **Spec-ID:** SPEC-RESEARCH-HANDOFF
- **Dependencies:** SPEC-HAPPY-PATH
- **Deliverables:**
  - Keep this task in the frozen batch until thaw occurs.
- **Acceptance:** It remains visible in backburner freeze state.
- **Notes:** This card should not be lost.
