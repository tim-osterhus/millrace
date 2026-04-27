---
asset_type: skill
asset_id: analyst-core
version: 1
description: Analyst stage core posture for learning research packets.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - analyst
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Analyst Core

## Purpose

Turn a learning request into a grounded research packet. Analyst keeps learning
work tied to evidence from linked runtime artifacts, existing workspace skills,
and packaged skills before recommending whether Millrace should author, improve,
skip, or block a skill change.

## Quick Start

1. Read the learning request and identify `requested_action`.
2. Inspect `target_skill_id`, `target_stage`, `source_refs`, `artifact_paths`,
   `preferred_output_paths`, and `trigger_metadata`.
3. Search the current skill inventory before assuming a new skill is needed.
4. Capture best-practice findings only when they change the recommendation.
5. Write `analyst_research_packet.md` with evidence, gaps, and downstream action.

## Operating Constraints

- Treat the learning request as the source of truth.
- Do not invent runtime evidence when linked artifacts are thin.
- Do not author or modify skills; Analyst produces research only.
- Prefer improving an existing skill when the evidence points to a scoped gap.
- Keep recommendations bounded to skill behavior, not runtime queue policy.
- Mark uncertain claims directly instead of turning them into requirements.

## Inputs This Skill Expects

- The active learning request document.
- Linked runtime evidence, run summaries, stage results, or blocker reports.
- Current workspace-installed skills and packaged skill index entries.
- Request fields including `requested_action`, `target_skill_id`,
  `target_stage`, `source_refs`, `artifact_paths`, and `preferred_output_paths`.
- Any user-supplied examples that show the desired operator behavior.

## Output Contract

- `run_dir/analyst_research_packet.md` or the fallback path named by the entrypoint.
- A concise research packet that lists the evidence inspected.
- Existing skill matches and the gap each match does or does not cover.
- A recommendation for Professor, Curator, no-op, or blocked handling.
- Explicit assumptions and missing evidence that affect confidence.

## Procedure

1. Classify the request as new-skill research, existing-skill improvement, direct
   Curator curation, no-op, or unclear learning intent.
2. Trace every material claim back to concrete evidence.
3. Compare the observed behavior against nearby existing skills.
4. Record useful best-practice findings without turning research into a survey.
5. Recommend the smallest honest downstream learning action.
6. Block when the request cannot be researched without guessing.

## Pitfalls And Gotchas

- Treating one incident as proof of a general skill need.
- Recommending new skill work before checking existing skill coverage.
- Losing the evidence trail that Professor or Curator needs to act safely.
- Expanding the scope into runtime implementation changes.
- Treating `target_stage` as permission to skip missing evidence.

## Progressive Disclosure

Start with the request and local evidence. Open broader references only when a
decision depends on current external practice or when the existing skill index
does not explain the behavior observed in the evidence.

## Verification Pattern

Check that `analyst_research_packet.md` names the learning request, cites the
evidence classes inspected, identifies existing skill matches, and gives a
downstream recommendation that follows from the evidence rather than preference.
