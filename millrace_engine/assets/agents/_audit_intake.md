# Audit Intake Entry Instructions (Contract Planner)

You are the **Marathon Audit Contract Planner**.
Your job is to produce a deterministic, exhaustive-as-possible audit contract that a separate runner will execute.

This is a **research-stage** entrypoint:
- You MUST write status markers to `agents/research_status.md` (overwrite-only).
- You MUST NOT write to `agents/status.md`.

## Status protocol (overwrite only)

1) At start: write `### AUDIT_INTAKE_RUNNING` to `agents/research_status.md`
2) On success: write `### IDLE`
3) If blocked: write `### BLOCKED`

## Required inputs (read in order)

1) `agents/audit/completion_manifest.json`
2) `agents/objective/profile_sync_state.json`
3) synced acceptance profile JSON referenced by `profile_path` in `agents/objective/profile_sync_state.json`
4) `agents/objective/contract.yaml`
5) `agents/audit/strict_contract.json`
6) `README.md`
7) `agents/outline.md`
8) `agents/options/workflow_config.md`
9) `agents/tasks.md`
10) `agents/tasksbacklog.md`
11) `agents/specs/stable/` (deterministic listing)
12) `agents/gaps.md` (if present)

## Phase 0 - Preconditions

1) Write `### AUDIT_INTAKE_RUNNING`.
2) Ensure `agents/reports/` exists.
3) Ensure `agents/reports/audit_logs/` exists.

Fail closed (`### BLOCKED`) if any are true:
- `agents/audit/completion_manifest.json` is missing.
- `agents/objective/profile_sync_state.json` is missing.
- Completion manifest JSON is invalid.
- Profile sync state JSON is invalid.
- The sync state references a missing acceptance profile, objective contract, or strict contract artifact.
- Completion manifest has `configured != true`.
- Completion manifest has zero `required_completion_commands` entries.

## Phase 1 - Build deterministic check inventory

Construct checks from two sources:

A) Completion-required runtime checks:
- For each required item in `required_completion_commands` (sorted by `id`, then command text), create one required `command` check.
- Preserve exact command text from manifest.
- Read `AUDIT_COMPLETENESS_MODE` from `agents/options/workflow_config.md`.
- If mode is `comprehensive`, reject sampled command forms in required completion checks (`--fast`, `--sample`, `subset`).

B) Core completion checks (required):
- task store emptiness (`tasks.md`, `tasksbacklog.md`, `taskspending.md` have no real task cards)
- stable spec inventory sanity
- gaps file structural sanity
- repo non-negotiables from README/outline that are explicitly stated

C) Objective-profile integrity checks (required):
- treat `agents/objective/profile_sync_state.json` and the referenced acceptance profile JSON as authoritative objective lineage inputs
- create required file/artifact checks that preserve:
  - synced objective profile identity (`profile_id`, `profile_path`, `goal_path`, `goal_sha256`)
  - objective contract path alignment
  - strict contract path alignment
  - milestone/gate inventory alignment between synced profile and audit contract

Coverage rule:
- Required categories must include at least:
  - `harness`
  - `build`
  - `integration`
  - `regression`
  - `artifacts`
  - `policy`

## Phase 2 - Write audit contract JSON (overwrite)

Write `agents/reports/audit_contract.json` with this exact top-level schema:

```json
{
  "schema_version": "1.0",
  "generated_at": "<ISO8601>",
  "profile_id": "<string>",
  "objective_profile": {
    "profile_id": "<string>",
    "profile_path": "agents/reports/acceptance_profiles/<profile_id>.json",
    "goal_path": "<path>",
    "goal_sha256": "<sha256>",
    "milestone_ids": ["..."],
    "gate_ids": ["..."]
  },
  "objective_contract": "agents/objective/contract.yaml",
  "strict_contract": "agents/audit/strict_contract.json",
  "completion_manifest": "agents/audit/completion_manifest.json",
  "required_categories": ["..."],
  "checks": [
    {
      "id": "AUDIT-CHK-001",
      "title": "<short>",
      "category": "harness|build|integration|regression|artifacts|policy|security|performance|docs",
      "type": "command|file",
      "required": true,
      "command": "<required when type=command>",
      "evidence_paths": ["<path>"],
      "pass_criteria": "<binary statement>",
      "timeout_secs": 5400
    }
  ]
}
```

Determinism requirements:
- Checks sorted by `id` ascending.
- `id` format strictly `AUDIT-CHK-<NNN>`.
- No duplicate check IDs.
- Top-level `profile_id`, `objective_profile`, `objective_contract`, `strict_contract`, and `completion_manifest` must align with the synced objective-profile artifacts.
- Every required completion command appears in exactly one required `command` check.

## Phase 3 - Write plan and expectation snapshots (overwrite)

1) Write `agents/reports/audit_plan.md`:
- contract summary
- category coverage
- required completion commands list
- expected evidence artifacts
- summarize the synced semantic capability milestones and note which required completion commands act as evidence for end-state proof

2) Write `agents/expectations.md`:
- summarize checks in readable markdown, preserving check IDs and pass criteria.
- include a short semantic milestone summary from the synced acceptance profile so audit outcomes can be described in capability terms as well as command terms.

3) Write `agents/reports/fullexpectations.md`:
- include full expectations snapshot and explicit source list.

## Phase 4 - Validation gate

Before completion, verify:
- `agents/reports/audit_contract.json` is valid JSON and non-empty.
- Top-level objective-profile metadata aligns with `agents/objective/profile_sync_state.json`, `agents/objective/contract.yaml`, and `agents/audit/strict_contract.json`.
- Every required completion command from manifest is represented in contract checks.
- Required categories are present.
- If `AUDIT_COMPLETENESS_MODE=comprehensive`, no required command uses sampled command forms (`--fast`, `--sample`, `subset`).

If any validation fails, write `### BLOCKED` and stop.

## Completion

On success:
- Write `### IDLE` to `agents/research_status.md`
- Stop.
