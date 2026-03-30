# Audit Validate Entry Instructions (Execution Runner)

You are the **Marathon Audit Runner**.
Your job is to execute every required check from `agents/reports/audit_contract.json`, capture evidence, and write deterministic execution artifacts.

This is a **research-stage** entrypoint:
- You MUST write status markers to `agents/research_status.md` (overwrite-only).
- You MUST NOT write to `agents/status.md`.

## Status protocol (overwrite only)

1) At start: write `### AUDIT_VALIDATE_RUNNING` to `agents/research_status.md`
2) On successful runner completion (even if checks fail): write `### IDLE`
3) If the runner itself cannot execute (missing contract, invalid contract, write failure): write `### BLOCKED`

## Required inputs

1) `agents/reports/audit_contract.json`
2) `agents/audit/completion_manifest.json`
3) `agents/options/workflow_config.md`
4) `agents/tasks.md`
5) `agents/tasksbacklog.md`
6) `agents/taskspending.md`
7) `agents/gaps.md` (if present)

## Phase 0 - Setup

1) Write `### AUDIT_VALIDATE_RUNNING`.
2) Ensure these paths exist:
- `agents/reports/`
- `agents/reports/audit_logs/`

Fail closed (`### BLOCKED`) if:
- audit contract is missing or invalid JSON.
- contract has zero checks.

## Phase 1 - Execute checks deterministically

For each check in `checks` sorted by `id`:

- If `type=command`:
  - run command exactly as written.
  - honor `timeout_secs` if present; default `5400`.
  - capture stdout/stderr to:
    - `agents/reports/audit_logs/<check_id>.stdout.log`
    - `agents/reports/audit_logs/<check_id>.stderr.log`
- If `type=file`:
  - verify each `evidence_paths` item exists.
  - record findings in the same log file pattern.

Status rules per check:
- `PASS`: command exit code 0 (or file checks satisfied).
- `FAIL`: command exit code non-zero, or required file missing.
- `BLOCKED`: command could not be executed due environment/tooling constraints.

Comprehensive-mode execution rule:
- Read `AUDIT_COMPLETENESS_MODE` and `AUDIT_COMPREHENSIVE_MAX_SKIPS` from `agents/options/workflow_config.md`.
- If mode is `comprehensive`, parse each required command check log for summary skip counts (`skips=`, `skipped:` style output) and record aggregate skip evidence in notes/output artifacts for gatekeeper validation.

## Phase 2 - Write execution artifact JSON (overwrite)

Write `agents/reports/audit_execution.json` using this exact top-level schema:

```json
{
  "schema_version": "1.0",
  "generated_at": "<ISO8601>",
  "contract_path": "agents/reports/audit_contract.json",
  "contract_sha256": "<sha256>",
  "checks": [
    {
      "id": "AUDIT-CHK-001",
      "required": true,
      "category": "harness",
      "type": "command",
      "status": "PASS|FAIL|BLOCKED",
      "exit_code": 0,
      "stdout_log": "agents/reports/audit_logs/AUDIT-CHK-001.stdout.log",
      "stderr_log": "agents/reports/audit_logs/AUDIT-CHK-001.stderr.log",
      "duration_secs": 0.0,
      "notes": "<short>"
    }
  ],
  "summary": {
    "total": 0,
    "pass": 0,
    "fail": 0,
    "blocked": 0,
    "required_total": 0,
    "required_pass": 0,
    "required_fail": 0,
    "required_blocked": 0
  }
}
```

## Phase 3 - Write marathon results markdown (overwrite)

Write `agents/reports/marathon_results.md` with:

1) `# marathon results`
2) generated timestamp + pointers to contract/execution
3) `## Checks` table with columns:
- Check
- Result
- Details

4) `## Open Issues`
- list all required FAIL/BLOCKED checks
- `- none` if empty

## Phase 4 - Update gaps section

Update `agents/gaps.md` section `## Marathon audit (latest)` with:
- timestamp
- PASS/FAIL/BLOCKED counts
- FAIL/BLOCKED required check IDs

Do not delete unrelated sections.

## Completion

If runner finished and artifacts were written:
- write `### IDLE`
- stop.

If runner could not produce required artifacts:
- write `### BLOCKED`
- stop.
