# Runtime Deep Docs Information Architecture

This directory is the canonical information architecture for Millrace runtime deep documentation.

Its job is to keep deep technical guidance discoverable without collapsing back into a single monolith. Each deep doc in this tree owns one runtime contract boundary, names the code and operator truth surfaces behind that boundary, and stays intentionally bounded in scope.

`docs/RUNTIME_DEEP_DIVE.md` remains the stable top-level entrypoint and compatibility path. This `docs/runtime/` tree exists to hold the detailed boundary docs that the portal points to; it does not replace the portal path.

## Boundary Catalog

Every runtime deep doc in this tree must map to one primary runtime contract boundary.

| Deep doc | Boundary owner | Primary truth surfaces | Notes |
| --- | --- | --- | --- |
| `README.md` | Runtime docs IA and writing contract | this file, `docs/RUNTIME_DEEP_DIVE.md`, `tests/test_package_parity.py`, `tests/test_baseline_assets.py` | Batch 37 Run 01 |
| `runtime-loop-lifecycle-and-supervisor-authority.md` | runtime lifecycle and external authority boundary | `millrace_engine/engine.py`, `millrace_engine/engine_runtime.py`, `millrace_engine/engine_runtime_loop.py`, `millrace_engine/control.py` | Batch 37 Run 02 |
| `control-plane-command-surface-and-mailbox-semantics.md` | command/control mutation boundary | `millrace_engine/cli.py`, `millrace_engine/control.py`, `millrace_engine/control_actions.py`, `millrace_engine/control_runtime_surface.py`, `millrace_engine/engine_mailbox_command_handlers.py` | Batch 37 Run 03 |
| `runtime-state-status-markers-and-stale-recovery-semantics.md` | persisted runtime state and stale-state recovery boundary | `millrace_engine/status.py`, `millrace_engine/paths.py`, `millrace_engine/control_models.py`, `millrace_engine/control_reports.py`, `millrace_engine/engine_runtime.py`, `millrace_engine/control_actions.py` | Batch 37 Run 04 |
| `stage-execution-pipeline-and-plane-handoff-contracts.md` | stage execution and inter-plane handoff boundary | `millrace_engine/planes/execution.py`, `millrace_engine/planes/execution_runtime.py`, `millrace_engine/stages/base.py`, `millrace_engine/contract_runtime.py`, `tests/test_execution_plane.py` | Batch 37 Run 05 |
| `runner-adapters-model-selection-and-permission-profile-semantics.md` | runner adapter and stage-config-to-command boundary | `millrace_engine/config_runtime.py`, `millrace_engine/config.py`, `millrace_engine/contract_runtime.py`, `millrace_engine/stages/base.py`, `millrace_engine/runner.py`, `tests/test_runner.py`, `tests/test_config.py` | Batch 37 Run 06 |
| `configuration-surfaces-apply-boundaries-and-live-reload-semantics.md` | config ownership and apply-boundary governance | `millrace_engine/config.py`, `millrace_engine/config_runtime.py`, `millrace_engine/engine_config_coordinator.py`, `millrace_engine/control_runtime_surface.py`, `tests/test_config.py`, `tests/test_cli.py` | Batch 37 Run 07 |
| `observability-reporting-and-tui-truth-surfaces.md` | operator visibility boundary | `millrace_engine/events.py`, `millrace_engine/control_reports.py`, `millrace_engine/telemetry.py`, `millrace_engine/tui/`, runtime report files under `agents/` | Batch 37 Run 08 |
| `failure-modes-and-operator-unwedge-playbook.md` | operator recovery boundary | failure and recovery sections across engine/control/research helpers, operator docs, diagnostics surfaces | Batch 37 Run 09 |
| `portal-migration-map.md` | portal compatibility and migration boundary | `docs/RUNTIME_DEEP_DIVE.md`, packaged mirror docs, doc-proof tests, operator-facing navigation surfaces | Batch 37 Run 10 |

## Boundary Rules

Use these rules to decide whether a topic belongs in an existing deep doc or needs a new one.

1. Split by runtime contract boundary, not by arbitrary chapter count or heading size.
2. Keep one primary owner per document. If the topic needs two unrelated owners to explain it honestly, split again.
3. Put operator workflow summaries in the portal or operator docs. Put implementation-oriented boundary truth here.
4. Cross-link instead of duplicating. If a second doc needs the same detail, link to the owner boundary and summarize only the dependency.
5. Prefer code-truth ownership over team ownership. The document boundary should follow runtime seams that tests and modules can prove.

## Mandatory Deep-Doc Template

Every runtime deep doc in this tree must use the same section contract.

```md
# <Boundary Title>

## 1. Purpose And Scope
- What boundary this doc owns
- What it explicitly does not own
- Why the boundary exists

## 2. Source-Of-Truth Surfaces
- Python modules, packaged assets, operator docs, and tests that define the real contract
- Which surface wins when two views differ

## 3. Lifecycle And State Transitions
- Startup, steady-state, transition, and terminal behavior for this boundary
- Important persisted artifacts, latches, or state markers

## 4. Failure Modes And Recovery
- Expected failure classes
- Detection surfaces
- Recovery or unwedge paths
- Explicit non-goals and escalation boundaries

## 5. Operator And Control Surfaces
- CLI, TUI, supervisor, sentinel, file-backed, or packaged surfaces relevant to this boundary
- Safe mutation paths versus read-only observation paths

## 6. Proof Surface
- Tests, parity checks, manifest checks, smoke commands, or validation artifacts that prove the boundary stays truthful
- What drift should cause a failure

## 7. Change Guidance
- Where future updates should land
- What changes require a new sibling deep doc instead of expanding this one
```

The section titles may be expanded for clarity, but every deep doc must cover all seven sections. The template is mandatory because later docs need stable shape for navigation, review, and proof.

## Size And Scope Guidance

- Target length is roughly `800-1800` words per deep doc.
- Treat `1800` words as a pressure signal, not a challenge target. If the doc exceeds that range because it explains more than one owner boundary, split it.
- Keep examples short and boundary-specific. Long procedural walkthroughs belong in operator-facing docs unless they are required to explain the runtime contract itself.
- Do not restate the entire runtime package map in every doc. Name only the modules and files that are real truth surfaces for that boundary.
- Do not use this tree for changelog-style history, release notes, or broad architecture marketing. This tree is for durable technical contracts.

## Portal Compatibility Contract

- `docs/RUNTIME_DEEP_DIVE.md` stays in place as the stable public and packaged entrypoint.
- Do not delete, rename, or hollow out `docs/RUNTIME_DEEP_DIVE.md` without an explicit portal run that updates both the public file and packaged mirror together.
- New deep docs belong under `docs/runtime/` and the identical packaged mirror under `millrace_engine/assets/docs/runtime/`.
- The portal should link into this tree by boundary. The boundary docs should link back to the portal when a reader needs top-level orientation.
- If a new topic only needs one paragraph of portal guidance and no boundary contract, keep it in the portal instead of creating a shallow deep doc.

Current linked boundary docs:

- `runtime-loop-lifecycle-and-supervisor-authority.md`
- `control-plane-command-surface-and-mailbox-semantics.md`
- `runtime-state-status-markers-and-stale-recovery-semantics.md`
- `stage-execution-pipeline-and-plane-handoff-contracts.md`
- `runner-adapters-model-selection-and-permission-profile-semantics.md`
- `configuration-surfaces-apply-boundaries-and-live-reload-semantics.md`

## Contribution Rules

When adding or changing runtime documentation:

1. Start from the boundary catalog above and choose the single best owner document.
2. If no owner fits, add a new boundary only when the topic introduces a durable runtime contract seam with its own source-of-truth modules and proof surface.
3. Update the packaged mirror in the same change as the public doc.
4. Update manifest or parity proof surfaces whenever a new packaged runtime doc path is introduced.
5. Keep cross-links truthful and avoid copying large sections between sibling docs.
6. If a change alters portal navigation or top-level compatibility guarantees, route that work through the dedicated portal/migration boundary instead of smuggling it into another doc.

## Acceptance Checklist For Future Runtime Deep Docs

Before a new deep doc lands, confirm all of the following:

- The doc owns exactly one runtime contract boundary.
- The mandatory template sections are present.
- The source-of-truth modules, lifecycle, failure/recovery, operator surfaces, and proof sections are explicit.
- The doc fits the size guidance or includes a justified split follow-up.
- The public doc and packaged mirror are byte-for-byte identical.
- The portal compatibility contract remains intact.
