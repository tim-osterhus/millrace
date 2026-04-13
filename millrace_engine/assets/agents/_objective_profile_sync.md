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
7) `agents/skills/acceptance-profile-contract/SKILL.md`
9) optional project-local semantic seed:
   - `agents/objective/semantic_profile_seed.json`
   - or `agents/objective/semantic_profile_seed.yaml`
   - or `agents/objective/semantic_profile_seed.yml`
9) optional spec-family runtime state:
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
3) Run the shipped Objective Profile Sync stage exactly once through the Python GoalSpec runtime surface.
   - packaged runtime ownership: `millrace_engine/research/goalspec_objective_profile_sync.py`
   - semantic-profile extraction and optional seed loading: `millrace_engine/research/goalspec_semantic_profile.py`
   - family-policy derivation and initial-family pinning: `millrace_engine/research/goalspec_family_policy.py` and `millrace_engine/research/governance.py`
   - the stage must materialize the required outputs listed above, including refreshed `agents/objective/contract.yaml` and `agents/audit/strict_contract.json`

4) Validate the synced outputs against the shipped runtime contract:

```bash
python3 -m json.tool agents/objective/profile_sync_state.json >/dev/null
python3 -m json.tool agents/objective/family_policy.json >/dev/null
python3 -c "from pathlib import Path; assert Path('agents/objective/contract.yaml').exists(); assert Path('agents/audit/strict_contract.json').exists()"
python3 -c "from pathlib import Path; assert Path('agents/reports/objective_profile_sync.md').exists(); assert any(Path('agents/reports/acceptance_profiles').glob('*.json'))"
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
