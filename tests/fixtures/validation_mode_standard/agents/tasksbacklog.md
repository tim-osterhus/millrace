# Task Backlog

## 2026-03-23 - Validate the shadowed autonomous loop

- **Goal:** Prove the workspace shadow reroutes `mode.default_autonomous` through the packaged standard loop.
- **Context:** Validation should materialize a checked-in registry overlay and execute a minimal runtime cycle.
- **Spec-ID:** SPEC-VALIDATION-STANDARD-001
- **Dependencies:** none
- **Deliverables:**
  - Compile and report `mode.default_autonomous` with `execution.standard`.
  - Run a minimal execution cycle through builder, integration, QA, and update.
- **Acceptance:** The task archives cleanly and run provenance reports the workspace-shadowed mode plus packaged standard loop.
- **Notes:** Keep the scenario deterministic and fixture-backed.
