---
name: spec-writing-research-core
description: >
  Enforces Millrace research-loop spec quality for GoalSpec and incident fix_spec artifacts using verifiable requirements, ambiguity bans, and deterministic traceability.
  This skill should be used when Clarify, Critic, Designer, Taskmaster, Taskaudit, or Incident Resolve stages create, interrogate, or decompose specs under `agents/specs/` and `agents/ideas/specs/`.
compatibility:
  runners: ["codex-cli", "claude-code"]
  tools: ["Read", "Grep", "Bash", "Write"]
  offline_ok: true
---

# Spec Writing Research Core

## Purpose
Apply one shared, deterministic quality contract for research-loop specs so downstream task generation is executable without interpretation.

## Quick start
Goal:
- Produce or review specs that are unambiguous, verifiable, and traceable into task cards.

Use when (triggers):
- Authoring or updating golden specs, phase specs, or incident `fix_spec` artifacts
- Interrogating spec quality in Critic or Designer rounds
- Generating or auditing task cards from spec artifacts
- Resolving offline uncertainty via explicit assumptions and decisions

Do NOT use when (non-goals):
- Pure brainstorming with no requirement/verification contract yet
- UI verification execution or unrelated implementation coding

Concrete usage examples (user-style requests):
- "Turn this staging idea into a golden spec plus phase specs with REQ/AC traceability."
- "Critique this spec for ambiguity and missing verification before task decomposition."
- "Generate task cards from this fix_spec without inventing new requirements."

## Operating constraints
- Write requirements as obligations, not preferences.
- Keep one requirement per statement and one `SHALL`/`SHALL NOT` per requirement.
- Keep wording explicit; ban vague escape clauses and undefined pronouns.
- Keep unknowns explicit as assumptions; never harden unknowns into fake certainty.
- Keep traceability complete from REQ -> AC -> phase/task.

## Inputs this Skill expects
Required:
- Stage entrypoint contract (`agents/_spec_synthesis.md`, `agents/_spec_review.md`, `agents/_critic.md`, `agents/_designer.md`, `agents/_taskmaster.md`, `agents/_taskaudit.md`, `agents/_incident_resolve.md`)
- Target spec artifact(s) in `agents/specs/` or `agents/ideas/specs/`

Optional:
- `agents/specs/templates/golden_spec_template.md`
- `agents/specs/templates/phase_spec_template.md`
- `agents/specs/templates/incident_spec_template.md`
- Existing interrogation artifacts under `agents/specs/questions/` and `agents/specs/decisions/`

If required inputs are missing:
- Stop and mark the stage `### BLOCKED` with the minimum missing artifacts.

## Output contract
Primary deliverable:
- Stage-appropriate spec artifact(s) that satisfy the quality gates below.

Secondary deliverables:
- Critique/decision artifacts with explicit delta instructions and traceability impacts.

Definition of DONE (objective checks):
- [ ] Every requirement statement uses an EARS form and contains exactly one `SHALL` or `SHALL NOT`.
- [ ] Every requirement has stable IDs and links to acceptance criteria IDs.
- [ ] Every requirement has a verification method and measurable pass signal.
- [ ] Ambiguity-banned phrases are absent or replaced with measurable wording.
- [ ] Unknowns are captured as assumption records with confidence/risk/validation.
- [ ] Material design choices are captured as decision records with rationale.
- [ ] Queue/golden specs carry an explicit `decomposition_profile`.
- [ ] Phase Work Plan items are bounded and free of epic loop language.
- [ ] Decomposition does not introduce net-new requirements at task-card stage.

## Procedure (copy into working response and tick off)
Progress:
- [ ] 1) Lock stage scope
- [ ] 2) Extract requirement set
- [ ] 3) Apply requirement form gates
- [ ] 4) Apply ambiguity gates
- [ ] 5) Apply verification and traceability gates
- [ ] 6) Apply assumption and decision governance
- [ ] 7) Apply stage-specific handoff rules
- [ ] 8) Emit artifact or BLOCKED outcome

### 1) Lock stage scope
- Read the active stage entrypoint and follow its routing/status contract exactly.
- Limit edits to the single artifact(s) that stage is allowed to touch.

### 2) Extract requirement set
- Enumerate `REQ-*` and `AC-*` IDs already present.
- If IDs are missing in an authoring stage, add stable IDs before proceeding.

### 3) Apply requirement form gates
- Write requirements in EARS form:
  - `The <SOI> SHALL ...`
  - `WHEN <trigger> the <SOI> SHALL ...`
  - `IF <condition>, THEN the <SOI> SHALL ...`
  - `WHILE <state> the <SOI> SHALL ...`
  - `WHERE <optional feature> the <SOI> SHALL ...`
- Keep one thought per requirement line.

### 4) Apply ambiguity gates
- Remove or rewrite ambiguous terms such as:
  - `and/or`, `etc.`, `as appropriate`, `as needed`, `where possible`
  - `fast`, `optimal`, `user-friendly`, `eventually`, `soon`, `later`
- Replace with measurable thresholds, explicit conditions, or explicit actor references.

### 5) Apply verification and traceability gates
- Ensure each requirement has:
  - verification method (`Test|Analysis|Inspection|Demonstration`)
  - linked acceptance criteria IDs
  - concrete evidence expectation (command/artifact/output)
- Ensure decomposition artifacts preserve REQ/AC links.

### 6) Apply assumption and decision governance
- Record unresolved facts as `ASM-*` with:
  - statement, confidence, risk-if-wrong, validation plan, deadline/status
- Record material design choices as `DEC-*` with:
  - context, options, choice, rationale, consequences

### 7) Apply stage-specific handoff rules
- Clarify: emit golden + phase spec artifacts with measurable verification sections.
- Goal Intake / Spec Synthesis / Spec Review: preserve or set explicit `decomposition_profile` metadata and keep the package decomposition-ready.
- Critic: emit actionable question set tied to REQ/AC and assumptions.
- Designer: resolve questions and produce deterministic design decisions.
- Taskmaster/Taskaudit: enforce no-new-requirement rule; keep task cards traceable to existing REQ/AC.
- Incident Resolve: ensure `fix_spec` uses the same REQ/AC + verification discipline.

### 8) Emit artifact or BLOCKED outcome
- If gates pass, write artifacts and keep status protocol intact.
- If a hard gate fails and cannot be repaired deterministically, stop and mark `### BLOCKED` with precise failure reason.

## Verification
Use the smallest deterministic checks available:

1) Requirement ID presence:
```bash
rg -n "^REQ-[A-Za-z0-9_-]+" <target_spec.md>
```

2) Acceptance ID presence:
```bash
rg -n "^AC-[A-Za-z0-9_-]+" <target_spec.md>
```

3) Normative keyword checks:
```bash
rg -n "SHALL|SHALL NOT" <target_spec.md>
```

4) Ambiguity phrase scan (must be reviewed and resolved):
```bash
rg -n -i "and/or|etc\\.|as appropriate|as needed|where possible|eventually|soon|later" <target_spec.md>
```

5) Task-card contract checks (decomposition stages):
```bash
python3 agents/tools/lint_task_cards.py agents/taskspending.md \
  --strict "$TASKCARD_FORMAT_STRICT" \
  --min-cards-per-spec "$TASKMASTER_MIN_CARDS_PER_SPEC" \
  --max-cards-per-spec "$TASKMASTER_MAX_CARDS_PER_SPEC" \
  --target-cards-per-spec "$TASKMASTER_TARGET_CARDS_PER_SPEC" \
  --min-total-cards "$TASKMASTER_MIN_TOTAL_CARDS" \
  --target-total-cards "$TASKMASTER_TARGET_TOTAL_CARDS" \
  --target-shortfall-mode "$TASKCARD_TARGET_SHORTFALL_MODE" \
  --complexity-profile "$TASKMASTER_COMPLEXITY_PROFILE_RESOLVED" \
  --enforce-execution-template "$TASKCARD_ENFORCE_EXECUTION_TEMPLATE" \
  --phase-workplan-coverage "$TASKCARD_PHASE_WORKPLAN_COVERAGE" \
  --max-phase-steps-per-card "$TASKCARD_MAX_PHASE_STEPS_PER_CARD" \
  --scope-lint "$TASKCARD_SCOPE_LINT"
```

Evidence to retain:
- Updated spec artifacts with REQ/AC/ASM/DEC sections
- Stage-generated critique/decision files when interrogation stages run
- Lint/validation command output for decomposition stages

## Guardrails + anti-patterns
- Do not write requirement bundles with multiple obligations in one sentence.
- Do not leave baselined requirements with unresolved TBD/TBR text.
- Do not convert assumptions into facts without evidence.
- Do not approve phase Work Plan items that are still whole-project epics.
- Do not generate task cards that introduce requirements absent from the source spec.
- Do not continue interrogation rounds when no material delta exists; early-stop and record why.

## Escalation
Stop and mark `### BLOCKED` when:
- Required templates or source artifacts are missing.
- Traceability cannot be established from requirements to acceptance criteria.
- Decomposition would require inventing requirements not present in source specs.
- A stage contract conflicts with this skill and the conflict cannot be reconciled deterministically.

When blocked:
- Name the minimal missing artifact or contract conflict.
- Provide one deterministic unblocking action.

## Example References (concise summaries only)
1. **Clarify success path** - Builds golden+phase specs with full REQ/AC/ASM/DEC coverage and measurable verification. See `EXAMPLES.md` (EX-2026-02-26-01).
2. **Blocked fix_spec path** - Stops when incident fix_spec cannot meet traceability and verification gates. See `EXAMPLES.md` (EX-2026-02-26-02).
