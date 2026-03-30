# Outline

**Purpose:** Generic seed-state map for a fresh Millrace workspace before any project-local seed artifacts are layered in.

## Seed State

The generic baseline starts with the Millrace control plane and framework docs only.

Expected seed-time surfaces:

- `agents/` for roles, prompts, loops, tools, policies, reports, and runtime state
- root framework docs such as `README.md`, `ADVISOR.md`, and `OPERATOR_GUIDE.md`, plus reference docs under `docs/` such as `docs/RUNTIME_DEEP_DIVE.md`
- root runtime config in `millrace.toml`

Project overlays may add additional seed artifacts such as:

- a base goal
- harness entrypoints
- project-local prompts, policies, or specialized tools

## Important Boundary

Do not assume any project implementation layout exists at seed time.

In particular, this generic baseline does **not** imply the presence of:

- `crates/`
- `scripts/`
- `tests/`
- `docs/`
- `third_party/`
- `artifacts/`
- `target/`

Those are project-defined delivery surfaces, not framework assumptions.

## Control Plane

The always-present control-plane contract is:

- current execution task: `agents/tasks.md`
- queued backlog: `agents/tasksbacklog.md`
- research status: `agents/research_status.md`
- execution status: `agents/status.md`
- generic objective contract: `agents/objective/contract.yaml`

This file starts as the generic pre-deployment outline and may be updated later as the actual project repo takes shape.
