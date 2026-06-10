# Extension Boundary Compatibility Facades

## Purpose

This document records the remaining direct kernel-to-domain imports in the
Millrace runtime that act as compatibility facades.  Each entry is tagged with
a maintenance guardrail that describes when and how it should be migrated to an
extension-owned interface.

These facades exist because per ADR-0012, the runtime kernel must not own
workflow semantics.  The extension boundary interfaces defined in
`src/millrace_ai/extensions/interfaces.py` and registered in
`src/millrace_ai/extensions/builtin/` are the contract surface for each domain.
The facades recorded here bridge existing code to those interfaces until the
underlying domain modules migrate to the runtime-operation-step model
(ADR-0014).

## Active Extension Boundary Bridges

These callsites already route through the `BuiltInExtensionBoundaryRegistry` by
extension interface ID.  The built-in implementations in
`extensions/builtin/` are thin adapters that delegate to the legacy domain
modules listed below.

| Call site | Interface ID | Delegates to | Allowed Callers | Mode Scope |
|-----------|-------------|-------------|-----------------|------------|
| `runtime/result_application.py` | `recon_transition_handler` | `runtime/recon_transitions.py` | Kernel post-stage result routing | All modes declaring `millrace.recon` |
| `runtime/result_application.py` | `closure_transition_handler` | `runtime/closure_transitions.py` | Kernel post-stage result routing | All modes declaring `millrace.closure` |
| `runtime/tick_cycle.py` | `learning_trigger_handler` | `runtime/learning_triggers.py` | Tick-cycle stage completion | Learning-enabled modes only (`learning_codex`, `learning_pi`, `efficient_learning_mixed`, `learning_codex_integrated`, `blueprint_learning_codex`) |
| `runtime/tick_cycle.py` | `learning_promotion_handler` | `runtime/learning_promotions.py` | Tick-cycle stage completion | Learning-enabled modes only |
| `runtime/supervisor.py` | `learning_trigger_handler` | `runtime/learning_triggers.py` | Supervisor stage-worker outcomes | Learning-enabled modes only |
| `runtime/supervisor.py` | `learning_promotion_handler` | `runtime/learning_promotions.py` | Supervisor stage-worker outcomes | Learning-enabled modes only |

## Remaining Direct Kernel-to-Domain Imports

### Lazy Context Provider Registration (`runtime/context/providers.py`)

- **File**: `src/millrace_ai/runtime/context/providers.py`
- **Symbol**: `default_request_context_provider_registry()` (internal thunk)
- **Import**: `from .blueprint import built_in_blueprint_provider_registrations`
- **Domain**: Blueprint
- **Allowed Callers**: Request-context provider registry construction (not module-import-time)
- **Mode Scope**: Blueprint-declaring modes only (`blueprint_codex`, `blueprint_learning_codex`);
  generic-only and non-Blueprint modes do not trigger the lazy thunk
- **Retention Reason**: Blueprint context provider registration must remain
  available for request-context profiles that select Blueprint provider
  implementations; generic modes never construct the Blueprint-aware
  registry
- **Status**: Lazy compatibility facade
- **Guardrail**: The `default_request_context_provider_registry()` function
  imports Blueprint context providers inside the registry-construction thunk,
  not at module import time. Subprocess guardrails in
  `tests/maintenance/test_generic_engine_boundary_guardrails.py`
  (`test_subprocess_import_millrace_ai_runtime_does_not_load_forbidden_prefixes`,
  `test_subprocess_import_runtime_engine_does_not_load_forbidden_prefixes`, and
  `test_subprocess_generic_only_runtime_startup_does_not_load_forbidden_prefixes`)
  prove zero forbidden prefixes loaded.
  **Migration path**: Replace registry-wide Blueprint provider registration
  with selection through the `BlueprintContextProvider` extension interface so
  generic request-context registry construction does not need to load
  Blueprint provider implementation modules.

### Blueprint Context Provider Rendering (`runtime/context/blueprint.py`)

- **File**: `src/millrace_ai/runtime/context/blueprint.py`
- **Import**: Direct imports of `BlueprintDraftDocument`, `BlueprintPacketDocument`,
  `BlueprintEvaluationDocument`, `BlueprintCritiqueDocument`, and
  `workspace/blueprint_state` helpers.
- **Domain**: Blueprint
- **Allowed Callers**: Blueprint request-context provider interface only
- **Mode Scope**: Blueprint-declaring modes only
- **Retention Reason**: Contains domain-specific rendering logic that
  should live in a Blueprint extension package; kept as compatibility
  facade until migration to operation-step model
- **Status**: Compatibility facade
- **Guardrail**: This module contains domain-specific rendering logic that
  should live in a Blueprint extension package.  It is no longer loaded by
  package-level runtime imports, but it is still loaded when the built-in
  request-context provider registry is constructed. Subprocess import and
  startup probes in
  `tests/maintenance/test_generic_engine_boundary_guardrails.py`
  confirm this module is not eagerly loaded.
  **Migration path**: Extract the per-stage context plan functions into a
  Blueprint extension package registered under the
  `blueprint_context_provider` interface ID.  The built-in adapter at
  `extensions/builtin/blueprint_context_provider.py` already provides the
  interface boundary; move the implementation there.

### Planner Disposition Effects (`runtime/planner_effects.py`)

- **File**: `src/millrace_ai/runtime/planner_effects.py`
- **Import**: Direct imports of `PlannerDisposition`, `TaskDocument`,
  `SpecDocument`, and queue mutation helpers.
- **Domain**: Sharing
- **Allowed Callers**: Compiled runtime-effect operation catalog (`effect_execution.py`)
- **Mode Scope**: All modes with Planning plane (standard, learning,
  Blueprint, integrated, and fixture modes)
- **Retention Reason**: Planner disposition effect execution registers
  through the compiled runtime-effect operation catalog but the handler
  itself contains domain-specific decomposition logic
- **Status**: Compatibility facade
- **Guardrail**: Planner disposition effect execution registers through the
  compiled runtime-effect operation catalog (`effect_execution.py`) but the
  handler itself contains domain-specific decomposition logic. Guardrail
  tests in `tests/maintenance/test_kernel_import_guardrails.py` allowlist
  this module as known-eager; subprocess startup probes confirm it is not
  loaded by generic-only modes.
  **Migration path**: Planner effects are already dispatched by compiled
  `effect_operation_id`.  When the legacy handler-backed runner is replaced
  with interpreted operation steps, the disposition logic should move to
  primitives registered under the shared extension domain.

### Blueprint Effect Operation Runners (`runtime/effects/operation_runners/`)

- **Files**: `src/millrace_ai/runtime/effects/operation_runners/artifacts.py`,
  `candidate_evaluation.py`, `candidate_packet.py`,
  `decomposition_manifest.py`, `repair_application.py`, `stores.py`,
  `work_items.py`
- **Import**: Direct imports of Blueprint contract models, artifact workflow
  helpers, and workspace state readers/writers.
- **Domain**: Blueprint
- **Allowed Callers**: Compiled runtime-effect dispatch via legacy handler registry
- **Mode Scope**: Blueprint-declaring modes only
- **Retention Reason**: Contain Blueprint-specific artifact mutation logic
  dispatched by compiled `effect_operation_id`; kept until interpreted
  operation-step migration
- **Status**: Compatibility facade
- **Guardrail**: These operation runners are dispatched by compiled
  `effect_operation_id` through the legacy handler registry.  They contain
  Blueprint-specific artifact mutation logic that should live in a Blueprint
  extension package. Guardrails in
  `tests/maintenance/test_kernel_import_guardrails.py` ensure these are not
  imported by generic kernel paths.
  **Migration path**: When the legacy handler-backed runner is replaced with
  interpreted operation steps (ADR-0014), each operation should register as
  a Blueprint-domain primitive.  The built-in validator at
  `extensions/builtin/blueprint_validator.py` provides the validation
  boundary interface.

### Closure Target Boundary (`runtime/closure_boundary.py` -> `runtime/completion_behavior.py`)

- **File**: `src/millrace_ai/runtime/closure_boundary.py` (public boundary);
  `src/millrace_ai/runtime/completion_behavior.py` (internal implementation)
- **Import**: Kernel callers import `runtime/closure_boundary.py`, which uses
  function-body imports into `runtime/completion_behavior.py`. The internal
  implementation still imports `ClosureTargetState`, workspace arbiter state
  helpers, lineage integration, and family adapters.
- **Domain**: Closure
- **Allowed Callers**: Kernel modules (`engine.py`, `activation.py`,
  `result_application.py`, `effect_execution.py`) via lazy `_LazyModule` or
  function-body import; no direct `completion_behavior` imports from kernel
  paths
- **Mode Scope**: All modes; lazy delegation ensures closure domain code loads
  only when closure behavior is actually needed
- **Retention Reason**: Named closure boundary provides separable closure
  target lifecycle, lineage gating, backpressure policy, and
  result-normalization responsibilities per ADR-0012; internal implementation
  migration to operation-step model is pending
- **Status**: Named compatibility boundary with an internal implementation
  facade
- **Guardrail**: `closure_boundary.py` is the only kernel-facing boundary for
  closure target lifecycle, lineage gating, backpressure policy, and
  result-normalization boundary responsibilities. It delegates lazily to
  `completion_behavior.py`, so `import millrace_ai.runtime` and
  `from millrace_ai.runtime import RuntimeEngine` do not eagerly load the
  closure implementation. Subprocess guardrails in
  `tests/maintenance/test_generic_engine_boundary_guardrails.py` prove
  `completion_behavior` and `arbiter_state` are not loaded on public import
  or generic-only startup. Completion behavior still owns closure target
  creation, lineage resolution, and backlog-drain triggering internally; these
  remain closure domain concerns per ADR-0012.
  **Migration path**: When the runtime-operation-step model is extended to
  cover closure target lifecycle operations, extract the domain-specific
  behaviour into closure extension primitives.  The built-in closure
  transition handler at `extensions/builtin/closure_transition_handler.py`
  provides the transition boundary interface.

### Recon Packet Persistence (`runtime/recon_transitions.py`)

- **File**: `src/millrace_ai/runtime/recon_transitions.py`
- **Import**: Direct imports of `ReconDecision`, `ReconPacketDocument`,
  `SpecDocument`, `TaskDocument`, and `RootIntakeKind`.
- **Domain**: Recon
- **Allowed Callers**: `ReconTransitionHandler` extension interface only;
  kernel callers route through `BuiltInExtensionBoundaryRegistry`
- **Mode Scope**: All modes declaring `millrace.recon`
- **Retention Reason**: Recon packet validation and enqueue logic bridges
  through the extension interface but the implementation still contains
  domain-specific Recon enums and contract model imports
- **Status**: Compatibility facade (bridged through extension interface)
- **Guardrail**: This module is already bridged through the
  `ReconTransitionHandler` extension interface but the implementation itself
  contains domain-specific Recon enums and contract model imports that should
  live in a Recon extension package. Subprocess import and startup probes
  confirm `recon_transitions` and `recon_packets` are not eagerly loaded.
  **Migration path**: When Recon transitions migrate to the
  runtime-operation-step model, the packet validation and enqueue logic
  should register as Recon-domain primitives.  The built-in Recon handler
  adapter provides the boundary; move the implementation behind it.

### Stage Metadata Registry (`contracts/stage_metadata.py`)

- **File**: `src/millrace_ai/contracts/stage_metadata.py`
- **Symbol**: `stage_plane()`, `stage_name_for_plane()`, `legal_terminal_markers_for()`,
  `result_class_for_outcome()`, `running_marker_for_stage()`
- **Import**: Shipped-stage metadata facade loaded from JSON stage-kind assets
  for known built-in stage enum members.
- **Domain**: Sharing
- **Allowed Callers**: Runner request normalization, entrypoint linting,
  graph stage lookup, built-in stage-kind asset validation; compatibility
  consumers only, not active runtime dispatch
- **Mode Scope**: All modes (compatibility surface for 18 shipped stages)
- **Retention Reason**: Preserves public lookup and enum compatibility for
  shipped stages; custom graph and fixture stage kinds derive their authority
  from JSON stage-kind assets and the compiled plan
- **Status**: Documented compatibility surface per ADR-0013
- **Guardrail**: This module is explicitly documented as a compatibility
  surface for shipped stages, not universal runtime authority. Custom stage
  kinds derive their authority from JSON stage-kind assets and the compiled
  plan. Shipped-stage hardwiring guardrail in
  `tests/maintenance/test_generic_engine_boundary_guardrails.py` forbids
  25 shipped stage kind IDs as hard-coded string comparisons in active
  kernel paths (allowlist excludes stage-kind assets, compilation
  validation, extension built-ins, and the stage metadata registry itself).
  **Migration path**: No kernel changes needed.  New stages should be
  declared in stage-kind JSON assets rather than added to this registry.

## Domain-Specific Context Provider Built-in Adapters

The `extensions/builtin/` package contains thin adapter modules for each
domain-specific context provider.  Each adapter delegates to the existing
runtime implementation and is registered with the
`BuiltInExtensionBoundaryRegistry`.

| Adapter module | Interface | Delegates to | Allowed Callers | Mode Scope |
|---------------|-----------|-------------|-----------------|------------|
| `builtin/recon_transition_handler.py` | `ReconTransitionHandler` | `runtime/recon_transitions.py` | `BuiltInExtensionBoundaryRegistry` | Modes declaring `millrace.recon` |
| `builtin/closure_transition_handler.py` | `ClosureTransitionHandler` | `runtime/closure_transitions.py` | `BuiltInExtensionBoundaryRegistry` | Modes declaring `millrace.closure` |
| `builtin/learning_trigger_handler.py` | `LearningTriggerHandler` | `runtime/learning_triggers.py` | `BuiltInExtensionBoundaryRegistry` | Learning-enabled modes only |
| `builtin/learning_promotion_handler.py` | `LearningPromotionHandler` | `runtime/learning_promotions.py` | `BuiltInExtensionBoundaryRegistry` | Learning-enabled modes only |
| `builtin/blueprint_validator.py` | `BlueprintValidator` | `contracts/blueprint.py` (Pydantic validation) | `BuiltInExtensionBoundaryRegistry` | Blueprint-declaring modes only |
| `builtin/blueprint_context_provider.py` | `BlueprintContextProvider` | `runtime/context/blueprint.py` | `BuiltInExtensionBoundaryRegistry` | Blueprint-declaring modes only |
| `builtin/generic_context_provider.py` | `ContextProvider` | `runtime/context/generic.py` | `BuiltInExtensionBoundaryRegistry` | All modes |
| `builtin/generic_artifact_adapter.py` | `ArtifactAdapter` | workspace document loaders | `BuiltInExtensionBoundaryRegistry` | All modes |

## Lazy Loading Boundary

The `BuiltInExtensionBoundaryRegistry` uses lazy `importlib.import_module()`
calls so that domain-specific code (Recon, closure, Blueprint, Learning) is
only loaded when an active extension actually references its interface ID.
Minimal fixture configs and generic modes that never request a domain-specific
interface will not trigger imports of those domain modules.

The `default_request_context_provider_registry()` function in
`runtime/context/providers.py` now defers the Blueprint provider registration
import until the registry-construction thunk runs. This closes the former
package-import eager-loading gap for `millrace_ai.runtime` and `RuntimeEngine`.
The remaining compatibility gap is that registry construction still imports
the built-in Blueprint provider registrations; a full migration should route
Blueprint context provider selection through the extension interface when the
compiled plan selects a Blueprint provider.

The `runtime/closure_boundary.py` module also uses function-body imports for
its completion-behavior delegation. Kernel modules import the boundary, not the
closure implementation, preserving public runtime import laziness while the
remaining closure lifecycle migration is still pending.

## Verification

The full residual remediation chain (runs 14759-75 through 14759-80) passed:

**Import/startup laziness (run-14759-75):**
```bash
uv run --extra dev pytest -q tests/runtime/test_runtime.py tests/runtime/test_supervisor.py tests/runtime/test_error_recovery.py
uv run --extra dev pytest -q tests/workspace/test_state_reconciliation.py tests/workspace/test_queue_family_interpreter.py tests/workspace/test_queue_selection.py
uv run --extra dev pytest -q tests/maintenance/
uv run --extra dev ruff check src tests
```

**Named closure boundary (run-14759-76):**
```bash
uv run --extra dev pytest -q tests/runtime/test_completion_behavior.py
uv run --extra dev pytest -q tests/runtime/test_runtime.py tests/runtime/test_supervisor.py tests/runtime/test_error_recovery.py
uv run --extra dev pytest -q tests/maintenance/
uv run --extra dev ruff check src tests
uv build
```

**Active authority removal (run-14759-77):**
```bash
uv run --extra dev pytest -q tests/runtime/test_runtime.py tests/runtime/test_supervisor.py tests/runtime/test_error_recovery.py tests/runtime/test_completion_behavior.py tests/runtime/test_request_context_providers.py
uv run --extra dev pytest -q tests/workspace/test_state_reconciliation.py tests/workspace/test_queue_family_interpreter.py tests/workspace/test_queue_selection.py
uv run --extra dev pytest -q tests/maintenance/
uv run --extra dev ruff check src tests
```

**Guardrails and probes (run-14759-79):**
```bash
uv run --extra dev pytest -q tests/maintenance/test_kernel_import_guardrails.py tests/maintenance/test_generic_engine_boundary_guardrails.py tests/maintenance/test_graph_runtime_authority_boundaries.py tests/maintenance/test_documentation_claim_guardrails.py
uv run --extra dev pytest -q tests/compilation/test_extension_validation.py tests/maintenance/test_required_extension_validation.py
uv run --extra dev pytest -q tests/runtime/test_runtime.py tests/runtime/test_supervisor.py tests/runtime/test_completion_behavior.py tests/runtime/test_recon_transitions.py tests/runtime/test_request_context_providers.py tests/runtime/test_error_recovery.py
uv run --extra dev pytest -q tests/workspace/test_state_reconciliation.py tests/workspace/test_queue_family_interpreter.py tests/workspace/test_queue_selection.py
uv run --extra dev ruff check src tests
```

**Documentation and full verification (this run, 14759-80):**
```bash
uv run --extra dev ruff check src tests
uv run --extra dev pytest -q tests/maintenance/test_kernel_import_guardrails.py tests/maintenance/test_generic_engine_boundary_guardrails.py tests/maintenance/test_graph_runtime_authority_boundaries.py tests/maintenance/test_documentation_claim_guardrails.py
uv run --extra dev pytest -q tests/compilation/test_extension_validation.py tests/maintenance/test_required_extension_validation.py
uv run --extra dev pytest -q tests/assets/test_modes.py tests/compilation/test_config_swap.py tests/runtime/test_config_swap_runtime.py tests/runtime/test_recovery_counter_thresholds.py
uv run --extra dev pytest -q tests/runtime/test_runtime.py tests/runtime/test_supervisor.py tests/runtime/test_completion_behavior.py tests/runtime/test_recon_transitions.py tests/runtime/test_request_context_providers.py tests/runtime/test_error_recovery.py
uv run --extra dev pytest -q tests/workspace/test_state_reconciliation.py tests/workspace/test_queue_family_interpreter.py tests/workspace/test_queue_selection.py
uv run --extra dev pytest -q
uv build
git diff --check
```

Clean subprocess import/startup probes (all passes):
- `import millrace_ai.runtime` — zero forbidden prefixes loaded
- `from millrace_ai.runtime import RuntimeEngine` — zero forbidden prefixes loaded
- Generic-only runtime startup (`generic_two_plane_fixture`) — zero forbidden prefixes loaded
