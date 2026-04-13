# Millrace Runtime Deep Dive

This file is the stable runtime deep-dive portal and compatibility entrypoint.

It stays concise on purpose. The detailed runtime contracts now live under `docs/runtime/`, and the packaged mirror keeps the same public path under `millrace_engine/assets/docs/`.

Use this portal when you need to:

- find the right runtime boundary doc quickly
- understand which doc owns a behavior before editing code or docs
- confirm the proof surfaces that keep the modular doc split truthful

For setup and daily operator workflow, use `README.md` and `OPERATOR_GUIDE.md`. For the runtime deep-doc catalog and writing contract, start with `docs/runtime/README.md`. For the TUI shell itself, use `docs/TUI_DOCUMENTATION.md`.

## 1. Boundary Map

| Boundary | Start here | Primary truth surfaces | Read this when |
| --- | --- | --- | --- |
| Lifecycle and supervisor authority | `docs/runtime/runtime-loop-lifecycle-and-supervisor-authority.md` | `millrace_engine/engine.py`, `millrace_engine/engine_runtime.py`, `millrace_engine/engine_runtime_loop.py`, supervisor tests | you need to understand one-engine-per-workspace authority, `once` versus `daemon`, or liveness ownership |
| Control-plane mutation and mailbox semantics | `docs/runtime/control-plane-command-surface-and-mailbox-semantics.md` | `millrace_engine/cli.py`, `millrace_engine/control.py`, `millrace_engine/control_actions.py`, mailbox handlers | you need to know which commands apply immediately, which defer through mailbox, and which are intentionally blocked |
| Runtime state, markers, and stale recovery | `docs/runtime/runtime-state-status-markers-and-stale-recovery-semantics.md` | `millrace_engine/status.py`, `millrace_engine/control_reports.py`, `millrace_engine/control_actions.py`, state/status tests | you are debugging degraded state, stale snapshots, status legality, or persisted recovery markers |
| Stage pipeline and plane handoff | `docs/runtime/stage-execution-pipeline-and-plane-handoff-contracts.md` | `millrace_engine/planes/execution.py`, `millrace_engine/planes/execution_runtime.py`, `millrace_engine/stages/base.py` | you are changing execution sequencing, handoff shape, or transition-history ownership |
| Runner adapters, models, and permission profiles | `docs/runtime/runner-adapters-model-selection-and-permission-profile-semantics.md` | `millrace_engine/config.py`, `millrace_engine/config_runtime.py`, `millrace_engine/runner.py`, runner/config tests | you need the exact runner command contract, model selection rules, or `normal` / `elevated` / `maximum` permission semantics |
| Configuration apply boundaries and live reload | `docs/runtime/configuration-surfaces-apply-boundaries-and-live-reload-semantics.md` | `millrace_engine/config.py`, `millrace_engine/engine_config_coordinator.py`, config/runtime surfaces | you are changing native TOML ownership, reload timing, or pending-config apply rules |
| Observability, reports, TUI, and audit truth surfaces | `docs/runtime/observability-reports-tui-and-audit-truth-surfaces.md` | `millrace_engine/control_models.py`, `millrace_engine/control_reports.py`, `millrace_engine/control_runtime_surface.py`, `millrace_engine/tui/` | you are debugging CLI JSON/text, supervisor/report shaping, run provenance, or TUI snapshot narrowing |
| Recovery, failure modes, timeouts, and unwedge playbook | `docs/runtime/recovery-failure-modes-timeouts-and-unwedge-playbook.md` | `millrace_engine/engine_runtime_loop.py`, `millrace_engine/engine_outage_recovery.py`, `millrace_engine/control_actions.py`, `millrace_engine/sentinel_runtime.py` | you are investigating `NET_WAIT`, `RUNNER_TIMEOUT`, degraded state, deferred clear behavior, or Sentinel cap and acknowledgment flows |

## 2. Suggested Reading Paths

Use the shortest path that matches the question.

### Operator-first path

Start with:

1. `README.md` for package-level setup and quick start
2. `OPERATOR_GUIDE.md` for the supported local operator workflow
3. `docs/runtime/recovery-failure-modes-timeouts-and-unwedge-playbook.md` when the question is "what is the supported unwedge step?"

### Runtime-state and control path

Start with:

1. `docs/runtime/runtime-loop-lifecycle-and-supervisor-authority.md`
2. `docs/runtime/control-plane-command-surface-and-mailbox-semantics.md`
3. `docs/runtime/runtime-state-status-markers-and-stale-recovery-semantics.md`

That path answers most questions about who owns live state, why a daemon command is pending, and why a snapshot can be intentionally degraded or paused.

### Implementation-owner path

Start with:

1. `docs/runtime/README.md` for the boundary catalog
2. the owning boundary doc from the table above
3. `tests/test_package_parity.py` and `tests/test_baseline_assets.py` before or alongside any doc-shape change

That path is the right entry when you are changing code and want the doc contract, packaged mirror, and proof surfaces to move together.

## 3. Cross-Surface Compatibility Contract

This portal owns the stable navigation contract for the modular deep-doc set.

- `docs/RUNTIME_DEEP_DIVE.md` must remain the stable public and packaged path.
- `docs/runtime/README.md` owns the boundary catalog and writing contract for the deep-doc tree.
- `README.md` and `OPERATOR_GUIDE.md` should link here for top-level orientation and link directly to the specific deep docs operators need most often.
- `docs/TUI_DOCUMENTATION.md` remains the dedicated TUI reference; this portal points to it but does not re-document the shell in full.
- Adding a new runtime deep doc means updating the public file, the packaged mirror, `millrace_engine/assets/manifest.json`, and the parity/baseline proof surfaces in the same change.

The practical rule is simple: deep detail belongs in the owning boundary doc, while this file stays a map and compatibility seam.

## 4. Docs-Proof Commands

Run these commands whenever the runtime deep-doc tree, portal links, or packaged mirrors change:

```bash
cd /Users/timinator/Desktop/Millrace-2.0/millrace
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_package_parity.py -k 'packaged_docs_and_operator_assets_exist'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_baseline_assets.py -k 'packaged_baseline_includes_required_bundle_families or packaged_runtime_docs_reflect_current_resolver_behavior'
```

When a specific runtime doc path or portal link changes, also run a targeted parity-plus-manifest check for the touched files so the public copy, packaged copy, and `millrace_engine/assets/manifest.json` stay aligned.

## 5. Change Guidance

- Do not move deep implementation detail back into this portal.
- Add new docs under `docs/runtime/` only when they introduce a durable runtime contract boundary with its own source-of-truth modules and proof surface.
- If a topic needs only a short navigation note, keep it here or in `README.md` / `OPERATOR_GUIDE.md` instead of minting a shallow sibling deep doc.
- If a future change is mainly about mailbox-safe mutation, link readers to `docs/runtime/control-plane-command-surface-and-mailbox-semantics.md`.
- If it is mainly about timeouts, degraded state, or operator recovery actions, link readers to `docs/runtime/recovery-failure-modes-timeouts-and-unwedge-playbook.md`.
- If it is mainly about report shaping or TUI truth surfaces, link readers to `docs/runtime/observability-reports-tui-and-audit-truth-surfaces.md`.

This file should remain a portal, not a second monolith.
