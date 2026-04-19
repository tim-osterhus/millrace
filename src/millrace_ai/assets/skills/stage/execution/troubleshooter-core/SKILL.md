---
asset_type: skill
asset_id: troubleshooter-core
version: 1
description: Troubleshooter stage core diagnosis and smallest-safe-fix heuristics.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - troubleshooter
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Troubleshooter Core

## Purpose

Diagnose the visible symptom first, classify the blocker before repair, and prefer the smallest safe local fix that plausibly restores forward movement. If local recovery is not trustworthy, preserve the evidence instead of forcing a fragile repair.

Treat prompt-contract mismatches and similar runtime/source defects as locally repairable when grounded evidence points to a narrow patch that restores the run.

## Quick Start

1. Read the failure symptom and the current runtime evidence.
2. Classify the blocker before touching code or environment.
3. Decide whether a narrow local fix is credible or whether evidence should be preserved for later recovery.
4. Make the smallest safe intervention that still addresses the blocker.
5. Verify only enough to support the recovery claim.

## Operating Constraints

- Stay symptom-first and blocker-driven.
- Do not broaden the task into implementation work.
- Prefer the smallest local change that can actually unblock the run.
- Treat prompt-contract mismatches as fixable source defects when the repair is narrow, local, and evidence-backed.
- Preserve logs, reports, and other evidence when recovery confidence is low.
- Do not claim a repair is trustworthy without a direct check.

## Inputs This Skill Expects

- the current failure symptom
- `runtime_snapshot_path`
- `runtime_error_code` and `runtime_error_report_path` when present
- the active task when it helps identify the blocker
- current run logs, reports, and diagnostics

## Output Contract

- A concise blocker classification.
- The smallest safe local fix, or a clear statement that no safe local fix was possible.
- Evidence that supports the diagnosis and any repair.
- Enough preserved context for a later stage when local recovery is not trustworthy.

## Procedure

1. Start from the symptom, not from an abstract theory of failure.
2. Classify the blocker before editing files or environment state.
3. If the blocker is a prompt-contract mismatch or similar runtime/source seam, patch the local source of the defect and retry path rather than preserving a known-fixable failure.
4. Choose the narrowest repair that could restore forward motion.
5. Preserve evidence when the repair path is uncertain or brittle.
6. Verify the smallest observable change that demonstrates recovery.
7. Stop once the blocker is cleared or local recovery is no longer trustworthy.

## Pitfalls And Gotchas

- Repairing before diagnosis.
- Reaching for a broad fix when a smaller one is available.
- Destroying evidence that the next stage may need.
- Treating an unverified fix as trustworthy.

## Progressive Disclosure

Start with the nearest symptom and the narrowest supporting artifacts. Expand to adjacent logs or state only when the first pass does not explain the blocker well enough to choose a safe repair.

## Verification Pattern

Prefer the smallest command, file check, or run artifact that proves the blocker changed state. If trust in the recovery path is low, preserve the evidence cleanly and report the repair as not safely completed.
