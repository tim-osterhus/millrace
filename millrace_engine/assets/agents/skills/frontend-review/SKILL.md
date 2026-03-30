---
name: frontend-review
description: >
  Design system compliance in two phases: Phase 1 validates a design handoff (pre-dev), Phase 2
  enforces tokens and components in code (post-dev). Use when a design handoff needs validation or
  a UI PR needs design-system compliance checks.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Grep", "Write"]
  offline_ok: true
---

# Frontend Review

## Purpose
Enforce design system compliance across the full design -> implementation pipeline via (1) pre-dev design handoff review and (2) post-dev code conformance review.

Design system compliance across the full design -> implementation pipeline. Two phases, either can run independently.

Use when (triggers):
- Phase 1 (Design Handoff Review): design files/mockups arrive and must be validated before implementation.
- Phase 2 (Code Compliance Review): UI changes land and must be checked for token/component conformance.
- You need a deterministic checklist-driven report (not "vibes") with clear BLOCKED conditions.

Do NOT use when (non-goals):
- The task is automated a11y, perf, or visual gating (use your project gate runner/tooling).
- The task is a usability evaluation (run a dedicated heuristics review workflow).

## Operating constraints
- No secrets: never embed API keys, tokens, passwords, private URLs.
- Be minimal: smallest diffs; avoid style rewrites unless required.
- Be explicit: state assumptions and document deviations.
- Be deterministic: apply the checklists and rubric in `./reference.md`.
- Keep SKILL.md short: checklist details and token rubric live in `./reference.md`.

## Inputs this Skill expects
Phase 1 required:
- Design deliverables (Figma link, Sketch/PSD, or exported mockups)
- Target pages/components list and breakpoints (mobile/tablet/desktop)

Phase 2 required:
- Files or PR diff to review
- Token source of truth (Tailwind config, CSS variables, or design system docs in repo)

Optional:
- Component library location and usage guidelines
- Screenshots for UI state verification
- Asset bundle (icons/images/fonts) and delivery notes

If required inputs are missing:
- Ask for the minimum missing deliverables, or proceed with assumptions and mark BLOCKED sections.

## Output contract
Phase 1 output:
- `ui_review.md` (section-by-section pass/fail with evidence references)
- `ui_review.json` (machine-readable mirror with section status and issue counts)

Phase 2 output:
- `conformance_notes.md` (rules applied, replacements made, and any deviations or BLOCKED items)

Definition of DONE (objective checks):
- [ ] Phase 1 (if run): `ui_review.md` and `ui_review.json` include all checklist sections (from `./reference.md` Section 1)
- [ ] Phase 2 (if run): `conformance_notes.md` documents applied rules and any BLOCKED items
- [ ] Every FAIL/BLOCKED item includes evidence or a precise missing-input note

## Shared contract
Both phases enforce the same principle: **the design system is the source of truth.** Tokens are mandatory. Raw values are violations. States (hover/focus/active/disabled) must be covered. Components must be reused before new ones are created.

If Phase 1 flags a BLOCKED item, do not proceed to implementation until it is resolved or explicitly accepted with a documented fallback.

## Procedure (copy into working response and tick off)
Progress:
- [ ] 1) Confirm which phase(s) apply + restate scope
- [ ] 2) Collect evidence and required inputs
- [ ] 3) Run Phase 1 checklist (if applicable)
- [ ] 4) Run Phase 2 compliance review (if applicable)
- [ ] 5) Write artifacts + summarize fixes/next steps

### Phase 1 - Design Handoff Review (pre-dev)
- [ ] 1) Confirm scope and deliverables
- [ ] 2) Capture evidence (screenshots per breakpoint, missing states)
- [ ] 3) Run checklist review against `./reference.md` Section 1
- [ ] 4) Write `ui_review.md` and `ui_review.json`
- [ ] 5) Summarize priority fixes and required handoff items

### Phase 2 - Code Compliance Review (post-dev)
- [ ] 1) Confirm token and component sources (locations in `./reference.md` Section 2)
- [ ] 2) Review diffs against token/component rubric
- [ ] 3) Apply minimal fixes (raw → token, reuse components, enforce state coverage)
- [ ] 4) Write `conformance_notes.md`
- [ ] 5) Validate with smallest available build or smoke check

---

## Pitfalls / gotchas
- **Phase 1:** Skipping state checks (hover/focus/active/disabled) causes rework later. Missing mobile breakpoints hides layout bugs. Missing webfont files or licensing blocks implementation.
- **Phase 2:** Creating new tokens without documenting semantic purpose fragments the design system. One-off values bypass the system silently. New components when a variant would suffice adds maintenance burden.
- **Both:** If two competing token systems exist (e.g., Tailwind colors vs CSS variables), do not pick one silently — escalate.

## Progressive disclosure
- Design handoff checklist: `./reference.md` Section 1
- Token/component rubric + search patterns + asset naming: `./reference.md` Section 2

## Example References (concise summaries only)
1. **Phase 1: Missing states and mobile** — Handoff review caught missing hover states and no mobile design. See EXAMPLES.md (EX-2026-02-03-01).
2. **Phase 1: Blocked - webfont deliverables** — Review blocked due to missing WOFF/WOFF2 and licensing. See EXAMPLES.md (EX-2026-02-03-02).
3. **Phase 2: Token cleanup** — Raw hex and spacing replaced with semantic tokens; assets renamed. See EXAMPLES.md (EX-2026-02-03-03).
4. **Phase 2: Blocked - conflicting token systems** — Review blocked until canonical token source was decided. See EXAMPLES.md (EX-2026-02-03-04).
5. **Combined: Phase 1 gap surfaced again in Phase 2** — Missing state from handoff became a raw value in code. See EXAMPLES.md (EX-2026-02-04-01).

**Note:** Full examples with tags and trigger phrases are in `./EXAMPLES.md`.
