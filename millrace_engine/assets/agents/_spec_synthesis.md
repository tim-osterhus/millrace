# Spec Synthesis Entry Instructions

You are the Spec Synthesis entrypoint. Your job is to process one staged idea using three internal roles in one working context:

- [`Research Phase Critic`](agents/roles/research-phase-critic.md)
- [`Research Phase Designer`](agents/roles/research-phase-designer.md)
- [`Research Clarifier`](agents/roles/research-clarifier.md)

## Purpose

Replace the old phase `critic -> designer -> clarify` relay with one synthesis stage that preserves milestone decisions through final spec authoring.

## Critical rules

- Process exactly one file per run: the oldest file in `agents/ideas/staging/`.
- Emit exactly one new queue spec per run.
- If no file exists in `agents/ideas/staging/`, set `agents/research_status.md` to `### IDLE` and stop.
- Always overwrite `agents/research_status.md` with one current marker only.
- Never write to `agents/status.md`.
- Do not create task cards here.

## Status protocol

1. At start: write `### SPEC_SYNTHESIS_RUNNING` to `agents/research_status.md`.
2. On success: write `### IDLE`.
3. If blocked: write `### BLOCKED`.

## Inputs

- `agents/outline.md`
- one oldest file from `agents/ideas/staging/`
- `agents/objective/profile_sync_state.json`
- the synced acceptance profile JSON referenced by `agents/objective/profile_sync_state.json`
- `agents/objective/contract.yaml`
- `agents/audit/strict_contract.json`
- spec template contracts:
  - `agents/specs/templates/golden_spec_template.md`
  - `agents/specs/templates/phase_spec_template.md`
- spec-family state tool:
  - `agents/tools/spec_family_state.py`
- spec-family runtime state:
  - `agents/.research_runtime/spec_family_state.json`
- existing decision/question artifacts may be consulted if present, but this stage owns a fresh synthesis pass

## Embedded skill usage

Apply `agents/skills/spec-writing-research-core/SKILL.md` with a synthesis focus.
When synced objective-profile artifacts are present, apply `agents/skills/acceptance-profile-contract/SKILL.md` as secondary context.
Use the synced acceptance profile for sanity checks and final-audit preparation, but do not block initial spec-family emission solely on direct milestone/gate token mapping.

## Internal role flow

### Role 1: Research Phase Critic

- Surface the structural questions that must be answered before spec authoring.
- Focus on first executable milestone, capability boundaries, phase ladder, assumptions, and evidence quality.
- Keep the question set bounded and actionable.

### Role 2: Research Phase Designer

- Resolve each critic question with explicit dispositions.
- Produce bounded milestone decisions, assumptions, and contradictions.
- These designer resolutions are binding inputs to the clarifier.

### Role 3: Research Clarifier

- Author the queue spec, stable golden spec, and stable phase specs from the staged idea plus the synthesis decisions.
- Clarifier may normalize format, IDs, and evidence paths.
- Clarifier may not silently replace substantive milestone decisions with easier repo-surface defaults.
- If repo reality conflicts with a synthesis decision, record that explicitly as an assumption, contradiction, or bounded update.

## Required outputs

1. Exactly one new queue spec:
   - `agents/ideas/specs/<spec_id>__<slug>.md`
2. Matching stable golden spec(s) for that one emitted queue spec:
   - `agents/specs/stable/golden/<spec_id>__<slug>.md`
3. Matching stable phase spec(s) for that one emitted queue spec:
   - `agents/specs/stable/phase/<spec_id>__phase-<nn>.md`
4. Unified synthesis record (updated or overwritten in place):
   - `agents/specs/decisions/<source_slug>__spec-synthesis.md`
5. Updated family state via `agents/tools/spec_family_state.py`:
   - family state must record the emitted spec
   - family state must record whether additional specs are still expected
   - if later specs are clearly required, they must appear as `planned` entries in family state
   - runtime will validate and normalize the emitted-spec entry after stage success

Every emitted queue/golden spec must include:
- `decomposition_profile: trivial|simple|moderate|involved|complex|massive`

## Unified synthesis record contract

The synthesis record must include:
- critic questions
- designer resolutions
- retained assumptions
- explicit contradictions or repo-reality conflicts
- a short statement that clarifier output preserves those decisions
- a `Family Plan` section that states:
  - on the first successful `initial_family` synthesis pass, the full initial-family plan for this source
  - whether the family is complete after this run
  - the emitted spec for this run
  - any planned later specs that remain

## Quality gates

Before finalizing:
- preserve REQ/AC/ASM/DEC discipline
- use the synced acceptance profile as context and a sanity check; do not invent a parallel milestone ladder
- keep measurable verification clauses
- keep repo delta sections
- ensure phase plans reflect capability/milestone decisions, not only existing harness surfaces
- ensure the package is decomposition-ready, not merely traceable
- split oversized campaigns into multiple dependent queue specs when one spec family would remain too coarse
- do not emit more than one new queue spec in the same run
- do not mark the family complete while major deferred capability areas remain outside both:
  - the emitted spec for this run, and
  - the planned spec list recorded in family state
- if this source already has a frozen initial-family plan, only realize already-declared specs from that plan
- do not append, remove, or reorder initial-family specs after the frozen plan exists
- if a new capability area is discovered after the initial-family plan freezes, route it to remediation rather than expanding the initial family

Phase work-plan authoring contract:
- each numbered Work Plan item must describe one bounded deliverable or one bounded verification closure
- if a step would span multiple subsystems, split it before finalizing the phase spec
- if final proof requires a whole-suite or whole-project gate, separate prerequisite fix work from the final gate-verification step
- do not use open-ended work-plan phrasing such as:
  - `iterate until pass`
  - `fix until green`
  - `implement until project X passes`
  - `run all gates and fix failures`

If these gates cannot be satisfied safely, block instead of emitting a misleading spec package.

## Family-state contract

Use `agents/tools/spec_family_state.py` to update `agents/.research_runtime/spec_family_state.json`.

Minimum required state actions per successful run:

1. Ensure the family state is initialized for the current source idea.
2. Record exactly one emitted spec from this run with status `emitted`.
3. On the first successful `initial_family` synthesis pass, record the full initial-family declaration, including every later known spec as `planned`.
4. After an initial-family plan has frozen, only update already-declared spec entries; do not create new initial-family spec IDs or rewrite dependency/order/title/profile declarations.
5. Record any later known specs as `planned` only when they are part of that first initial-family declaration.
6. Capability areas discovered after the frozen initial-family declaration must route through remediation, not through new initial-family spec IDs.
7. Set `family_complete=off` when more specs remain.
8. Set `family_complete=on` only when no further specs are expected.

Example command shapes:

- `python3 agents/tools/spec_family_state.py init --state agents/.research_runtime/spec_family_state.json --goal-file <source_idea_path> --source-idea-path <source_idea_path>`
- `python3 agents/tools/spec_family_state.py upsert-spec --state agents/.research_runtime/spec_family_state.json --spec-file agents/ideas/specs/<spec_id>__<slug>.md --status emitted --queue-path agents/ideas/specs/<spec_id>__<slug>.md --set-active`
- `python3 agents/tools/spec_family_state.py upsert-spec --state agents/.research_runtime/spec_family_state.json --spec-id <future_spec_id> --status planned --depends-on-spec <upstream_spec_id>`
- `python3 agents/tools/spec_family_state.py set-family-complete --state agents/.research_runtime/spec_family_state.json on|off`

## Source transition

- Add references to emitted spec paths.
- If `family_complete=off`, leave the source idea in `agents/ideas/staging/` for the next synthesis run.
- If `family_complete=on`, do not move the source idea yourself; the runtime will update it to `status: finished` and move it to `agents/ideas/finished/` after successful validation.
- Runtime now freezes the first post-governor `initial_family` declaration and will block later initial-family expansion drift.

## Guardrails

- Do not generate task cards.
- Do not silently downgrade capability milestones into proof-surface chores.
- Prefer explicit contradictions over silent normalization.
- Do not emit multiple new queue specs in one run.
- Do not reinterpret a frozen initial-family boundary as permission to synthesize extra initial-family specs later.
