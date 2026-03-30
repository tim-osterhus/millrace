# Task Backlog

## 2026-03-23 - Validate workspace custom loop

- **Goal:** Prove a workspace-defined execution loop can run through the runtime.
- **Context:** This fixture shadows the packaged default mode selection with a local loop.
- **Spec-ID:** SPEC-VALIDATION-MODE-CUSTOM
- **Dependencies:** none
- **Deliverables:**
  - Run the custom builder plus update sequence once.
- **Acceptance:** The run archives the task after the custom loop reaches IDLE.
- **Notes:** Keep the loop minimal and deterministic.
