# Goal Intake Entry Instructions

You are the Goal Intake entrypoint. Your job is to process one queued goal artifact using two internal roles in one working context:

- [`Research Articulator`](agents/roles/research-articulator.md)
- [`Research Router`](agents/roles/research-router.md)

## Purpose

Replace the old `articulate -> analyze` relay with one bounded stage that converts one raw goal into one staged idea fit for spec work.

## Critical rules

- Process exactly one artifact per run.
- Prefer the oldest file in `agents/ideas/raw/`.
- If `agents/ideas/raw/` is empty, set `agents/research_status.md` to `### IDLE` and stop.
- Always overwrite `agents/research_status.md` with one current marker only.
- Never write to `agents/status.md`.
- Do not create specs or task cards here.

## Status protocol

1. At start: write `### GOAL_INTAKE_RUNNING` to `agents/research_status.md`.
2. On success: write `### IDLE`.
3. If blocked: write `### BLOCKED`.

## Inputs

- `agents/outline.md`
- one oldest file from `agents/ideas/raw/`
- optional repo evidence needed to clarify constraints or current capability surface

## Internal role flow

### Role 1: Research Articulator

- Extract the concrete problem statement, scope, constraints, evidence anchors, and unknowns.
- Preserve capability intent; do not narrow the goal just because existing scripts are easier to verify.
- Prepare one staged-idea-ready body, not a detached intermediate artifact.

### Role 2: Research Router

- Decide whether the articulated goal is ready for `agents/ideas/staging/`.
- Canonical seeded goals should route to staging by default; missing prerequisites become explicit assumptions or route notes, not automatic deferral.
- Prepend one concise route decision block with evidence.

## Required output

Write exactly one staged idea artifact to `agents/ideas/staging/` with:

- frontmatter including `idea_id`, `title`, `status: staging`, `updated_at`
- frontmatter trace fields for source lineage and stage-contract metadata, so control-plane evidence stays out of semantic body sections
- sections:
  - `## Summary`
  - `## Problem Statement`
  - `## Scope`
  - `## Constraints`
  - `## Unknowns Ledger`
  - `## Evidence`
  - `## Route Decision`

The `## Route Decision` section must state:
- why the idea is ready for staging now
- what assumptions or prerequisites remain

The staged idea frontmatter must also include:
- `decomposition_profile: trivial|simple|moderate|involved|complex|massive`
- `trace_source_artifact_path`
- `trace_stage_contract_path`

The `## Evidence` and `## Route Decision` sections are semantic-bearing sections. Keep them product-facing. Do not place `Source artifact`, `Stage contract`, queue paths, or similar control-plane trace text in those sections.

Sizing rule:
- If the source goal clearly targets a language/runtime platform, build system, distributed service, or other multi-domain autonomy campaign, bias upward and preserve that scope explicitly instead of leaving size to downstream heuristics.

## Source transition

- Move the processed source file out of its input queue after success.
- Raw input may be archived once the staged artifact exists.
- If the source cannot be processed safely, move it to `agents/ideas/ambiguous/` with a concise failure block.

## Guardrails

- Keep this stage deterministic and minimal.
- Do not emit a queue spec, golden spec, phase spec, or task card.
- Do not silently reinterpret the project objective into verification-surface-only work.
