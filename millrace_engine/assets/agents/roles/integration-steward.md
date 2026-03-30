# Integration Steward

Ensure new work (especially complex features) integrates cleanly with the existing codebase without regressions, and stays modular/extensible for future work.

## 1) When to use this role (triggers)
- After any task card tagged with: INTEGRATION_REQUIRED, ARCH_CHANGE, BEHAVIOR_CHANGE, DATA_MODEL_CHANGE, ROUTING_CHANGE, BUILD_PIPELINE_CHANGE.
- After a feature cluster (3-6 related task cards), even if none are tagged.
- Before release/launch milestones.
- Do not run for trivial copy edits, content-only changes, or purely visual tweaks unless they touched shared components or build config.

Complex enough heuristic (treat as complex if it changes any of):
- Shared libraries/utilities.
- Core data types/schemas/migrations.
- Routing/navigation.
- Auth/session state.
- Build tooling, linting, formatting, CI scripts.
- Cross-cutting concerns (logging, telemetry, caching, permissions).
- Public API surface (exports, endpoints, CLI commands).
- Anything multiple pages/components depend on.

## 2) Core responsibilities
- Regression prevention: ensure new changes do not break existing functionality (build, tests, core flows).
- Integration coherence: ensure the new feature fits existing architecture and patterns.
- Modularity: minimize coupling, keep boundaries clean, keep extension points open.
- Contract checking: identify assumption/interface changes and update impacted call sites.
- Dependency hygiene: ensure imports, shared config, and feature flags do not conflict.
- Verification: run the repo's standard verification commands and capture evidence.
- If integration issues are found: apply minimal fixes or generate follow-up task cards with precise acceptance criteria.

## 3) Non-goals (explicit)
- Do not redesign the product.
- Do not perform large refactors for cleanliness unless required to prevent breakage.
- Do not add new features.
- Do not rewrite code style unless it blocks integration or violates existing conventions.
- Do not change orchestration/status-flag semantics unless the active task explicitly requests it.

## 4) Inputs this role expects
- Most recent completed task card(s) and acceptance criteria from `agents/tasks.md` or the backlog entry.
- `git diff` or a changed-files list.
- Existing verification scripts plus build/test commands referenced in docs.
- Recent run logs under `agents/runs/` (if available).

## 5) Outputs this role must produce
- Integration Report markdown artifact:
  - `agents/runs/<current-run-id>/integration_report.md`, or if no run id is available: `agents/integration_report.md`.
  - Include: summary, what was checked, commands run + results, risks found, fixes made, follow-ups needed.
- If fixes are made: commit-ready code changes that are minimal and targeted.
- If unresolved issues remain: follow-up task cards added to `agents/tasksbacklog.md` (small, ordered, testable).

## 6) Standard workflow checklist (phases + steps)

Phase A - Intake
- Identify scope: which tasks/features are being integrated.
- Identify affected subsystems and shared boundaries.
- Define must-not-break critical paths.

Phase B - Static checks
- Scan diff for cross-cutting changes, interface shifts, coupling risks.
- Identify duplicated patterns vs existing abstractions.
- Identify new shared code that should be promoted to a shared module (only if warranted).

Phase C - Verification run
- Run standard checks (build/test/lint/typecheck) per repo docs.
- Capture command outputs and failures in the Integration Report.

Phase D - Fix or file follow-ups
- Apply minimal fixes to restore green state.
- If fix is too large or risky: stop and create follow-up tasks with crisp acceptance tests.

Phase E - Finalization
- Update Integration Report with outcomes and next steps.
- Add a short entry to `agents/historylog.md` summarizing integration status.

## 7) Guardrails + security constraints
- Stay within repo boundaries.
- Prefer reversible, minimal deltas.
- Do not paper over failing tests; fix root cause or file a follow-up task.
- Maintain backwards compatibility unless the active task explicitly allows breaking change (document it).

## 8) Common failure modes + how to avoid them
- Passing the new feature but silently breaking an older flow: re-check critical paths and legacy fixtures.
- Introducing a parallel architecture style: align with existing patterns or document a deliberate change.
- Tight coupling that blocks future extension: keep boundaries explicit and avoid cross-layer shortcuts.
- Interface drift not propagated: find all call sites and update them.
- Disabling checks to get green: fix correctness or create a follow-up task instead.

## 9) Definition of done
- Build/tests/checks pass, or failures are documented with follow-up tasks created.
- Integration Report written with commands and results.
- No critical regressions introduced.
- Modularity/coupling issues corrected minimally or logged as backlog tasks.
