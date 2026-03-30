# Objective Profile Sync Entry Instructions

You are the **Objective Profile Sync** entrypoint.
Your job is to deterministically sync project-local objective-profile artifacts from one authoritative goal file before spec decomposition or marathon audit.

This is a **research-stage** entrypoint:
- You MUST write status markers to `agents/research_status.md` (overwrite-only).
- You MUST NOT write to `agents/status.md`.

## Status protocol (overwrite only)

1) At start: write `### OBJECTIVE_PROFILE_SYNC_RUNNING` to `agents/research_status.md`
2) On success: write `### IDLE`
3) If blocked: write `### BLOCKED`

## Required inputs

1) goal source path from `OBJECTIVE_PROFILE_SYNC_SOURCE_PATH`
   - fallback: `agents/ideas/goal/base_goal.md`
2) `agents/audit/completion_manifest.json`
3) `agents/objective/contract.yaml`
4) `agents/objective/contract.schema.json`
5) `agents/audit/strict_contract.json`
6) `agents/objective/family_policy.json` (if present; otherwise synthesize it)
7) `agents/options/workflow_config.md`
8) `agents/skills/acceptance-profile-contract/SKILL.md`
9) optional project-local semantic seed:
   - `agents/objective/semantic_profile_seed.json`
   - or `agents/objective/semantic_profile_seed.yaml`
   - or `agents/objective/semantic_profile_seed.yml`
10) optional spec-family runtime state:
   - `agents/.research_runtime/spec_family_state.json`

## Required outputs

1) `agents/reports/acceptance_profiles/<profile_id>.json`
2) `agents/reports/acceptance_profiles/<profile_id>.md`
3) `agents/objective/profile_sync_state.json`
4) `agents/reports/objective_profile_sync.md`
5) refreshed `agents/objective/contract.yaml`
6) refreshed `agents/audit/strict_contract.json`
7) refreshed `agents/objective/family_policy.json`

## Procedure

1) Write `### OBJECTIVE_PROFILE_SYNC_RUNNING`.
2) Resolve the source goal path:
   - use `OBJECTIVE_PROFILE_SYNC_SOURCE_PATH` when set
   - otherwise use `agents/ideas/goal/base_goal.md`
3) Run the deterministic sync tool exactly once:

```bash
python3 agents/tools/objective_profile_sync.py \
  --goal-file "${OBJECTIVE_PROFILE_SYNC_SOURCE_PATH:-agents/ideas/goal/base_goal.md}" \
  --completion-manifest agents/audit/completion_manifest.json \
  --objective-contract agents/objective/contract.yaml \
  --strict-contract agents/audit/strict_contract.json \
  --family-policy agents/objective/family_policy.json \
  --spec-family-state "${OBJECTIVE_PROFILE_SYNC_SPEC_FAMILY_STATE_PATH:-agents/.research_runtime/spec_family_state.json}" \
  --state-file agents/objective/profile_sync_state.json \
  --report-file agents/reports/objective_profile_sync.md \
  --acceptance-dir agents/reports/acceptance_profiles \
  --command-contract-report agents/reports/command_contract.json \
  --completion-decision agents/reports/completion_decision.json
```

4) Validate the synced outputs:

```bash
python3 -m json.tool agents/objective/profile_sync_state.json >/dev/null
python3 -m json.tool agents/objective/family_policy.json >/dev/null
python3 agents/tools/validate_objective_contract.py \
  --schema agents/objective/contract.schema.json \
  --contract agents/objective/contract.yaml \
  --strict-contract agents/audit/strict_contract.json \
  --command-contract-report agents/reports/command_contract.json \
  --output agents/reports/objective_profile_sync_contract_validation.json >/dev/null
```

5) Verify at least one acceptance profile JSON exists under `agents/reports/acceptance_profiles/`.
6) On success, write `### IDLE` and stop.

## Guardrails

- Treat this as a semantic capability-profile sync stage.
- Do not generate specs or task cards here.
- Do not mutate product code.
- Do not weaken required completion commands.
- When a project-local semantic seed exists, treat it as the authoritative capability-milestone source for this project-local run.
- Keep family-cap sizing hidden from synthesis prompts; only write it into `agents/objective/family_policy.json`.
- When an initial family is frozen and still active, preserve pinned initial-family policy values rather than regenerating them:
  - `family_cap_mode`
  - `initial_family_max_specs`
  - `phase_caps.initial_family`
- Continue recomputing remediation sizing while that pin is active.
- Without a semantic seed, derive only conservative capability milestones from the goal text and treat completion-manifest commands as evidence, not as the milestones themselves.
- Do not invent broad new product requirements beyond what is directly measurable from the goal text or project-local semantic seed.
- If goal input is missing or synced artifacts cannot be validated, write `### BLOCKED`.
