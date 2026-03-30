---
name: acceptance-profile-contract
description: >
  Normalizes structured acceptance profiles from seed prompts into deterministic milestone/gate artifacts and traceable task metadata.
  This skill should be used when a goal prompt includes an `ACCEPTANCE_PROFILE` block or when research stages need reusable objective milestone criteria.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Grep", "Bash", "Write"]
  offline_ok: true
---

# Acceptance Profile Contract

## Purpose
Define one reusable, machine-checkable acceptance profile that maps prompt intent to deterministic milestones, hard blockers, and verification evidence.

## Quick start
Goal:
- Emit a reusable acceptance profile artifact and keep spec/task outputs traceable to it.

Use when (triggers):
- Seed goal/prompt contains `ACCEPTANCE_PROFILE` (or equivalent milestone/gate block).
- Research stages need objective, reusable milestone criteria across projects.
- Task decomposition is drifting into narrative-only acceptance wording.
- You need to separate framework-level acceptance logic from one-off prompt prose.

Do NOT use when (non-goals):
- Running harness/gate execution commands directly (use gate-specific skills/tools).
- Inventing new product requirements that are not present in the source prompt/spec.

## Operating constraints
- No secrets: never embed API keys, tokens, passwords, private URLs.
- Keep acceptance criteria objective: every milestone must map to explicit command/artifact evidence.
- Keep hard blockers minimal and category-based (runtime/toolchain readiness + baseline gate readiness).
- Keep profile IDs stable once published.
- Keep SKILL.md short; put long edge cases in `EXAMPLES.md`.

## Inputs this Skill expects
Required:
- One source goal/prompt artifact (for example `agents/ideas/goal/base_goal.md`, `agents/ideas/raw/*.md`, or a prompt file under `ref-docs/`).
- Active stage contract (`agents/_objective_profile_sync.md`, `agents/_spec_synthesis.md`, and/or `agents/_taskmaster.md`).

Optional:
- Existing profile artifacts under `agents/reports/acceptance_profiles/`.
- Existing stable specs/tasks for traceability backfill.

If required inputs are missing:
- Stop and mark `### BLOCKED` with the missing source artifact path.

## Output contract
Primary deliverable:
- `agents/reports/acceptance_profiles/<profile_id>.json` (machine source of truth)

Secondary deliverables:
- `agents/reports/acceptance_profiles/<profile_id>.md` (human summary)
- Stage-specific traceability updates (spec/task references to profile milestone IDs and gate IDs)

Required JSON shape:
- `profile_id` (stable identifier)
- `objective` (one concise statement)
- `hard_blockers` (list of blocker IDs + deterministic checks)
- `milestones` (ordered list):
  - `id`
  - `outcome`
  - `verify` (list of commands/artifacts with expected pass signals)
- `objective_gates` (optional but recommended)
- `updated_at` (UTC ISO)

Definition of DONE (objective checks):
- [ ] Profile JSON exists and parses via `python3 -m json.tool`.
- [ ] Every milestone has at least one deterministic verification entry.
- [ ] Hard blockers are explicit and category-based (no vague blockers).
- [ ] Clarify/Taskmaster outputs preserve milestone/gate traceability where applicable.
- [ ] No ambiguous wording (`eventually`, `as needed`, `where possible`) remains in profile criteria.

## Procedure (copy into working response and tick off)
Progress:
- [ ] 1) Identify source prompt and profile intent
- [ ] 2) Extract/normalize profile schema
- [ ] 3) Write canonical profile artifacts
- [ ] 4) Map profile IDs into spec/task outputs
- [ ] 5) Validate determinism + traceability
- [ ] 6) Emit completion or BLOCKED status

### 1) Identify source prompt and profile intent
- Select one authoritative source prompt/goal file.
- Confirm whether `ACCEPTANCE_PROFILE` exists explicitly.
- If absent, extract only clearly measurable acceptance lines; do not infer broad new requirements.

### 2) Extract/normalize profile schema
- Normalize to stable keys:
  - `profile_id`
  - `objective`
  - `hard_blockers`
  - `milestones`
  - `objective_gates`
- Ensure milestone IDs are stable and ordered (`M1`, `M2`, ... or domain-specific stable IDs).
- Ensure each milestone contains measurable `verify` evidence.

### 3) Write canonical profile artifacts
- Write JSON first:
  - `agents/reports/acceptance_profiles/<profile_id>.json`
- Optionally write markdown companion:
  - `agents/reports/acceptance_profiles/<profile_id>.md`
- Stamp UTC `updated_at`.

### 4) Map profile IDs into spec/task outputs
- In Clarify outputs, map profile milestones to `REQ-*`/`AC-*` sections or verification sections.
- In Taskmaster outputs, preserve milestone/gate traceability in card fields (`Requirement IDs`, `Acceptance IDs`, `Tags`, `Gates`, or Notes).
- Do not create acceptance criteria disconnected from the profile.

### 5) Validate determinism + traceability
Run:
- `python3 -m json.tool agents/reports/acceptance_profiles/<profile_id>.json >/dev/null`
- `rg -n "profile_id|hard_blockers|milestones|verify|updated_at" agents/reports/acceptance_profiles/<profile_id>.json -S`
- `rg -n -i "eventually|as needed|where possible|soon|later" agents/reports/acceptance_profiles/<profile_id>.json`

### 6) Emit completion or BLOCKED status
- If schema/verification/traceability checks pass, proceed with normal stage completion.
- If profile is ambiguous or unverifiable, mark `### BLOCKED` with one deterministic unblocking action.

## Pitfalls / gotchas
- Treating narrative intent as acceptance evidence without commands/artifacts.
- Defining hard blockers as open-ended prose instead of deterministic checks.
- Letting profile IDs drift between cycles, breaking traceability.

## Progressive disclosure (one level deep)
- Detailed scenarios and failure handling: `./EXAMPLES.md`

## Example References (concise summaries only)
1. **Product pipeline profile normalization** - Prompt profile was normalized into reusable JSON and mapped into staged tasking. See EXAMPLES.md (EX-2026-03-02-01)
2. **Ambiguous profile safely blocked** - Milestones lacked measurable verification and were blocked with deterministic repair guidance. See EXAMPLES.md (EX-2026-03-02-02)
