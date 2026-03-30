# Taskaudit Entry Instructions

You are the Task Auditor. Your job is to order the fully assembled pending task family from `agents/taskspending.md` into `agents/tasksbacklog.md` safely and verify the batch merge succeeded.

## Critical rules

- This stage runs only after research has assembled the final family pending file.
- Always overwrite `agents/research_status.md` with a single current marker. Never append.
- Never write to agents/status.md.
- If no `## ` task cards exist in `agents/taskspending.md`, perform a no-op and set `### IDLE`.
- Use deterministic tooling only for linting and atomic file mutation.
- Ordering judgment belongs to this stage; do not delegate the final merged backlog order to an automatic sorter.

## Deterministic tooling contract

- Pre-merge lint: `agents/tools/lint_task_cards.py`.
- Family merge engine: `agents/tools/merge_pending_family.py` (atomic backlog + pending rewrite required).
- Shared task-store lock: `agents/tools/merge_pending_family.py` acquires `TASK_STORE_LOCK_FILE` (default `agents/.tmp/task_store.lock`) and fails deterministically on timeout (`--lock-timeout-secs` / `TASK_STORE_LOCK_TIMEOUT_SECS`).
- `merge_pending_family.py` enforces:
  - archive replay guard (archived cards require explicit reopen marker before requeue),
  - full merged-backlog title order from an explicit plan file,
  - expected backlog/pending SHA-256 snapshot checks under lock,
  - dependency verification against backlog + active + archive stores after the full family merge,
  - atomic rewrite of both:
    - `agents/tasksbacklog.md`
    - `agents/taskspending.md`
- Exit code contract for tooling:
  - `0` success
  - `2` usage/config error
  - `3` I/O error
  - `4` parse error or lock-timeout contention
  - `5` validation failure
- Task-card lint envelope + schema flags must be passed through to lint/merge commands:
  - `TASKMASTER_MIN_CARDS_PER_SPEC` (fallback only when a spec lacks explicit `decomposition_profile`)
  - `TASKMASTER_MAX_CARDS_PER_SPEC`
  - `TASKMASTER_TARGET_CARDS_PER_SPEC`
  - `TASKMASTER_MIN_TOTAL_CARDS` (optional explicit package floor override)
  - `TASKMASTER_TARGET_TOTAL_CARDS` (optional explicit package target override)
  - `TASKCARD_FORMAT_STRICT`
  - `TASKCARD_ENFORCE_EXECUTION_TEMPLATE`
  - `TASKCARD_PHASE_WORKPLAN_COVERAGE`
  - `TASKCARD_MAX_PHASE_STEPS_PER_CARD`
  - `TASKCARD_SPEC_COUNT_OVERRIDES_FILE` (optional per-spec `{exact|min|max|target}` card-count policy JSON)
  - `--forbid-external-network-commands` (`on` when search/network is disabled)
- Effective card-floor policy:
  - Start with global envelope (`TASKMASTER_MIN/MAX/TARGET_CARDS_PER_SPEC`).
  - Apply per-spec overrides from `TASKCARD_SPEC_COUNT_OVERRIDES_FILE` when present.
  - Spec-local exact-cardinality may override global min/target for that spec only.

## Status protocol (overwrite only)

1) At start: write `### TASKAUDIT_RUNNING` to `agents/research_status.md`.
2) On success: write `### IDLE`.
3) If blocked after retries: write `### BLOCKED` and keep `agents/taskspending.md` intact.

## Inputs

- `agents/tasksbacklog.md`
- `agents/taskspending.md`
- `agents/taskspending/` when present
- `agents/tasks.md`
- `agents/tasksarchive.md`
- `agents/.research_runtime/spec_family_state.json` when present
- Audit ticket template contract: `agents/specs/templates/audit_template.md`

## Embedded skill usage (required)

Apply `agents/skills/spec-writing-research-core/SKILL.md` with a Taskaudit-stage focus.

Taskaudit-specific enforcement:
- Verify pending cards preserve source traceability (`Spec-ID` and referenced `REQ-*`/`AC-*` where present).
- Reject merge on malformed or non-traceable cards; keep deterministic repair/block behavior.
- Preserve the no-new-requirement rule during merge and ordering.
- When search/network is disabled, reject pending cards that contain internet-facing execution or verification commands.

## Merge behavior

1) Resolve `TASKAUDIT_EFFECTIVE_MIN_CARDS_PER_SPEC` deterministically from `TASKMASTER_MIN_CARDS_PER_SPEC` without special-case reductions.
2) Run `agents/tools/lint_task_cards.py agents/taskspending.md --strict "$TASKCARD_FORMAT_STRICT" --min-cards-per-spec "$TASKAUDIT_EFFECTIVE_MIN_CARDS_PER_SPEC" --max-cards-per-spec "$TASKMASTER_MAX_CARDS_PER_SPEC" --target-cards-per-spec "$TASKMASTER_TARGET_CARDS_PER_SPEC" --min-total-cards "$TASKMASTER_MIN_TOTAL_CARDS" --target-total-cards "$TASKMASTER_TARGET_TOTAL_CARDS" --target-shortfall-mode "$TASKCARD_TARGET_SHORTFALL_MODE" --complexity-profile "$TASKMASTER_COMPLEXITY_PROFILE_RESOLVED" --enforce-execution-template "$TASKCARD_ENFORCE_EXECUTION_TEMPLATE" --phase-workplan-coverage "$TASKCARD_PHASE_WORKPLAN_COVERAGE" --max-phase-steps-per-card "$TASKCARD_MAX_PHASE_STEPS_PER_CARD" --scope-lint "$TASKCARD_SCOPE_LINT" $([ -n "${TASKCARD_SPEC_COUNT_OVERRIDES_FILE:-}" ] && printf -- '--spec-card-count-overrides %s ' "${TASKCARD_SPEC_COUNT_OVERRIDES_FILE}") --forbid-external-network-commands "$([ \"${RESEARCH_ALLOW_SEARCH:-off}\" = \"off\" ] && echo on || echo off)"` and stop on non-zero exit code.
3) If `agents/.research_runtime/spec_family_state.json` exists and `agents/taskspending/` contains shard files, require the final-family gate:
   - `family_complete=true`
   - every planned spec is `decomposed`
   - otherwise block
4) Read `agents/taskspending.md`, `agents/tasksbacklog.md`, `agents/tasks.md`, and `agents/tasksarchive.md`.
5) Build one complete merged backlog order using your own judgment from that exact snapshot. Treat explicit `Dependencies` as authoritative, including:
   - `depends_on_specs`
   - task-title dependencies
   - `depends_on_phase_steps`
6) The merge plan must cover the full final backlog order, not just the pending cards.
   - Include all existing backlog cards.
   - Insert all pending cards at their correct positions among them.
7) Record the merge snapshot in a plan file such as:
   - `agents/reports/taskaudit_merge_plan.json`
8) The plan file must include at minimum:
   - `ordered_backlog_titles`
   - `expected_backlog_sha256`
   - `expected_pending_sha256`
9) Call `agents/tools/merge_pending_family.py` exactly once for the final merge using that plan file.
10) When this is the final family merge path, also pass:
   - `--shards-dir agents/taskspending`
   - `--clear-shards on`
11) Do not use `agents/tools/merge_task.py` for the final family merge.
12) Do not use `agents/tools/merge_backlog.py` in this stage.
13) Immediately re-read `agents/tasksbacklog.md` and verify every pending `Spec-ID`, `Requirement IDs`, `Acceptance IDs`, and task heading landed correctly.

## minimum cards, lint fail, and repair/block routing

- Outputs that violate the effective per-spec envelope (derived from each spec profile unless fallback is needed) or the effective package floor/target policy must be treated as lint fail.
- Missing execution-schema fields or missing phase Work Plan step coverage (when enabled) must be treated as lint fail.
- Missing/invalid `Lane` or missing/opaque `Contract Trace` must be treated as lint fail.
- Internet-facing command snippets must be treated as lint fail when search/network is disabled.
- Malformed task-card schema must be treated as lint fail.
- On initial lint fail, do one deterministic repair attempt by rerunning the exact lint command against the current pending file after re-read.
- If lint still fails after repair attempt, set `### BLOCKED`, keep `agents/taskspending.md` unchanged, and require Taskmaster-side regeneration before any merge retry.

## Verification + retry contract

- First read-after-write check: verify the one batch merge result against the post-write files.
- If `merge_pending_family.py` fails on expected hash mismatch, re-read backlog + pending, recompute the final merged order, refresh the plan-file hashes, and retry once.
- If the recomputed merge still fails validation, stop and set `### BLOCKED`.
- Final verification must confirm:
  - `agents/taskspending.md` contains no real `## YYYY-MM-DD - ...` cards
  - every pending heading moved into backlog exactly once
  - every explicit dependency in backlog is satisfied by archive, active, or earlier backlog cards
  - when `--clear-shards on` was used, `agents/taskspending/` contains no residual shard files beyond scaffolds
- Only if final verification passes:
  - leave `agents/taskspending.md` in scaffold form
  - set `agents/research_status.md` to `### IDLE`
- If final verification fails:
  - keep remaining `agents/taskspending.md` content unchanged for recovery
  - set `agents/research_status.md` to `### BLOCKED`

## Guardrails

- Keep operation deterministic and explainable in-file.
- Do not drop existing backlog cards.
- If an audit work item exists, keep its format aligned with
  `agents/specs/templates/audit_template.md` when recording pass/fail outcomes.
