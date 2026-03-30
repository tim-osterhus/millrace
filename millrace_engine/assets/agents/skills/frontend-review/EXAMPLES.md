# Examples (Frontend Review)

Append new examples to the END of this file and never change existing Example IDs.

---

## EX-2026-02-03-01: Phase 1 - Missing states and mobile

**Tags**: `phase-1`, `handoff`, `responsive`, `states`

**Trigger phrases**:
- "missing hover state"
- "no mobile design provided"
- "button states incomplete"
- "design handoff incomplete"

**Date**: 2026-02-03

**Problem**:
A landing page handoff arrived as desktop-only mockups. Buttons had no hover or disabled states and the form error message styling was not specified.

**Impact**:
Engineering could not implement reliable interactive states or responsive layout, risking rework and inconsistent UX.

**Cause**:
The design handoff omitted mobile/tablet breakpoints and did not include a style guide for states.

**Fix**:
- Flagged missing states and breakpoints in `ui_review.md` under Forms and Buttons and Responsive sections.
- Requested mobile and tablet frames plus state variants for primary and secondary buttons.
- Documented evidence with annotated screenshots.

**Prevention**:
- Require state coverage (hover, focus, active, disabled, loading) before implementation.
- Require mobile design delivery alongside desktop.

**References**:
- `ui_review.md`
- `ui_review.json`

---

## EX-2026-02-03-02: Phase 1 - Blocked due to font licensing / missing webfont deliverables

**Tags**: `phase-1`, `blocked`, `fonts`, `licensing`

**Trigger phrases**:
- "woff2 not provided"
- "font license"
- "missing webfont files"
- "typography handoff"

**Date**: 2026-02-03

**Problem**:
The design depended on a custom font, but the handoff did not include webfont-ready files (WOFF/WOFF2) or licensing clarity for web use.

**Impact**:
Implementation could not proceed safely: using an unlicensed or unavailable webfont risks legal/compliance issues and layout shifts when a fallback is substituted.

**Cause**:
The handoff treated typography as "design complete" without providing the webfont formats and usage constraints needed for production.

**Fix**:
- Marked the review BLOCKED under Fonts and Text.
- Requested: WOFF2 (and WOFF if needed), licensing/terms for web hosting, and the intended fallback font stack.
- Recorded a fallback risk note: if the font cannot be used, the design must be rechecked for longer strings and different metrics.

**Prevention**:
- Enforce a typography handoff checklist (formats + licensing + fallback stacks) before implementation.

**References**:
- `ui_review.md` (blocked status)

---

## EX-2026-02-03-03: Phase 2 - Token cleanup for new hero section

**Tags**: `phase-2`, `tokens`, `tailwind`, `components`

**Trigger phrases**:
- "hardcoded color"
- "raw spacing"
- "design token violation"
- "token cleanup"

**Date**: 2026-02-03

**Problem**:
A PR added a new hero section using raw hex colors and custom spacing utilities not in the token system.

**Impact**:
The design system became inconsistent and harder to maintain.

**Cause**:
The author bypassed existing semantic tokens and component variants.

**Fix**:
- Replaced raw values with existing semantic tokens (colors + spacing) using search patterns from `./reference.md` Section 2.
- Reused the existing button component variant instead of a bespoke class set.
- Renamed new assets to match conventions (`hero-*`, `bg-*`, `icon-*`).
- Added `conformance_notes.md` listing replacements and any remaining exceptions.

**Prevention**:
- Enforce token usage in code review. Search patterns in `./reference.md` Section 2 make violations quick to find.

**References**:
- `conformance_notes.md`

---

## EX-2026-02-03-04: Phase 2 - Blocked due to conflicting token systems

**Tags**: `phase-2`, `blocked`, `design-system`, `decision-needed`

**Trigger phrases**:
- "conflicting tokens"
- "tailwind vs css variables"
- "semantic token mapping"
- "two token systems"

**Date**: 2026-02-03

**Problem**:
A PR introduced raw values, but the repo had two competing token sources (Tailwind semantic colors and a separate CSS variable theme) with mismatched naming and no documented source of truth.

**Impact**:
Any automatic replacement risked entrenching the wrong token system and creating long-term inconsistency.

**Cause**:
The repo did not define which token system was canonical or how semantic names map across systems.

**Fix**:
- Marked the task BLOCKED.
- Produced a short note showing the conflicting token sources and example mismatches.
- Requested an explicit decision: "Tailwind tokens are canonical" vs "CSS variables are canonical" (or a migration plan).

**Prevention**:
- Document the canonical token source and naming scheme before enforcement begins.

**References**:
- `conformance_notes.md` (blocked status)

---

## EX-2026-02-04-01: Combined - Phase 1 gap surfaced again in Phase 2

**Tags**: `phase-1`, `phase-2`, `states`, `tokens`, `combined`

**Trigger phrases**:
- "missing state in design became raw value in code"
- "phase 1 gap caught in phase 2"
- "disabled state fallback"
- "handoff gap in implementation"

**Date**: 2026-02-04

**Problem**:
Phase 1 flagged that the design handoff was missing a `disabled` state for the primary button. The flag was noted but implementation proceeded with a placeholder. Phase 2 then found a raw color value where the disabled style should have been — the developer had used a hardcoded gray instead of the `disabled` token because no design spec existed.

**Impact**:
The raw value would have shipped as a one-off, breaking token consistency and making the disabled state visually inconsistent with other components.

**Cause**:
The Phase 1 gap (missing state spec) was not blocked before implementation. The developer filled the gap with a raw value instead of escalating.

**Fix**:
- Phase 2 caught the raw value using the hex-color search pattern from `./reference.md` Section 2.
- Replaced `#9ca3af` with the existing `disabled:opacity-50` + `disabled:pointer-events-none` utility pattern used across other components.
- Updated `conformance_notes.md` with the replacement and a note linking back to the Phase 1 finding.
- Flagged the workflow gap: Phase 1 BLOCKED items must be resolved before Phase 2 runs.

**Prevention**:
- Phase 1 BLOCKED items are hard stops. Do not proceed to implementation until they are resolved or explicitly accepted with a documented fallback.
- Phase 2 search patterns catch raw values regardless — they are the last line of defense.

**References**:
- `ui_review.md` (Phase 1, disabled state flag)
- `conformance_notes.md` (Phase 2, replacement + cross-reference)

<!--
Add new examples below this line.
-->
