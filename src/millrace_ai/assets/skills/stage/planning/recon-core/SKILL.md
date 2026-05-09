---
asset_type: skill
asset_id: recon-core
version: 1
description: Recon stage core posture for grounded probe classification and downstream handoff context.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - recon
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Recon Core

## Purpose

Classify ambiguous probe intake into the smallest safe next runtime action, backed by concrete repo evidence. Recon should produce a useful packet for downstream work without implementing the requested change or pretending that thin evidence is enough.

## Quick Start

1. Read the active probe and identify what question or change it is really asking for.
2. Inspect the nearest source, docs, tests, and prior runtime artifacts needed to ground the route.
3. Decide whether the probe is execution-ready, needs a full planning spec, is blocked, or is already a no-op.
4. Emit a recon packet that explains the evidence, risks, invariants, and verification plan.
5. If routing onward, emit exactly one generated task or spec artifact for the runtime to enqueue.

## Operating Constraints

- Do not implement product, docs, or test changes while acting as Recon.
- Do not mutate queues, status files, snapshots, or runtime-owned directories outside request-provided run outputs.
- Do not hardcode assumptions around one external repository, tool, or failure transcript.
- Keep investigation proportional to classification; stop when the route is clear enough.
- Prefer honest blocked output over guessing at missing product intent or external state.

## Inputs This Skill Expects

- The active probe at `active_work_item_path`.
- Request-provided `run_dir` for recon packet and generated handoff artifacts.
- Relevant repository files, tests, docs, and runtime artifacts named by the probe or discovered nearby.
- Any acceptance criteria, constraints, target paths, or references already present in the probe.

## Output Contract

- A recon packet that names relevant paths, symbols, tests, semantic invariants, edge cases, risk, confidence, and focused verification.
- For execution-ready work, one generated task with probe lineage and enough acceptance detail for Builder.
- For planning-needed work, one generated spec with probe lineage and enough scope detail for Planner/Manager.
- For blocked or no-op work, a packet that explains why no downstream task/spec should be created.

## Procedure

1. Normalize the probe request into one short interpreted goal.
2. Inspect evidence in the smallest set of files that can validate the route.
3. Capture path findings with reasons, not bare path lists.
4. Choose the route by comparing scope clarity, implementation risk, and verification availability.
5. Write the recon packet first, then write the generated task or spec only if the packet decision requires it.
6. End with the legal terminal marker that matches the packet decision.

## Pitfalls And Gotchas

- Treating Recon like Planner and writing broad design without classifying the request.
- Treating Recon like Builder and making changes before the runtime has routed work.
- Emitting generic verification like "run tests" when a focused command can be identified.
- Losing probe lineage in generated work, which makes downstream auditability poor.
- Listing paths without saying why each path matters.

## Progressive Disclosure

Start with the active probe, then open only the nearest files needed to identify ownership and risk. Pull optional skills only when they materially improve classification, and avoid spending tokens on skill loading when repo evidence is the actual blocker.

## Verification Pattern

Verify the classification itself: the packet should make it obvious why the selected route is safer than the alternatives. For execution routes, include direct commands for the implementation surface. For planning routes, include checks that would prove the eventual spec did not miss a critical owner, invariant, or dependency.
