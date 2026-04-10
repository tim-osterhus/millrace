# LEGACY — Chat Orchestrator (Do Not Use)

This file is legacy and is not the authoritative orchestrator for Millrace.

Use:
- `agents/orchestrate_loop.sh` for execution orchestration
- `agents/research_loop.sh` for research orchestration

This stub remains at this path because local loop scripts use
`agents/_orchestrate.md` as a repo-root anchor.

Legacy reference content:
- `agents/legacy/_orchestrate.md`

Runtime contract note (current local loop):
- `agents/orchestrate_loop.sh` enforces runtime governance via deterministic gates and
  contract artifacts owned by the active objective profile.
- Project-specific gates (for example, repo-specific verification suites) must stay in objective
  contracts/skills/artifacts rather than framework core entrypoints.
