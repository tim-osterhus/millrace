# Examples - Tools, Libraries, SDKs, and Toolchains

---

## EX-CONT-301: Python CLI utility

**Tags**: `automation_tool`, `developer_cli`, `python_package`

**Prompt**:
Build a Python CLI that validates staging manifests, summarizes diffs, and emits release-ready reports.

**Good classification**:
- `shape_class = automation_tool`
- `archetype = developer_cli`
- `stack_hints = ["python_package"]`
- `specificity_level = L4`

---

## EX-CONT-302: C compiler

**Tags**: `automation_tool`, `compiler_toolchain`

**Prompt**:
Create a C compiler with deterministic diagnostics, GCC torture-test coverage, and large-project build validation.

**Good classification**:
- `shape_class = automation_tool`
- `archetype = compiler_toolchain`
- `specificity_level = L2`

**Why**:
The primary product is a toolchain, not a service or app.

---

## EX-CONT-303: JavaScript bundler plugin SDK

**Tags**: `library_framework`, `sdk_library`, `platform_extension`

**Prompt**:
Create a JavaScript SDK and plugin API for third parties to build custom bundler transformations.

**Good classification**:
- primary `shape_class = library_framework`
- `archetype = sdk_library`
- `specificity_level = L2`

**Why**:
Even if it plugs into another system, the deliverable here is mainly a reusable developer-facing surface.

---

## EX-CONT-304: Build orchestration tool

**Tags**: `automation_tool`, `build_orchestrator`

**Prompt**:
Build a local orchestration tool that coordinates plan, build, QA, and publish steps across long-running coding sessions.

**Good classification**:
- `shape_class = automation_tool`
- `archetype = build_orchestrator`
- `specificity_level = L2`

---

## EX-CONT-305: Billing SDK

**Tags**: `library_framework`, `sdk_library`, `python_package`

**Prompt**:
Build a typed Python SDK for billing APIs with pagination helpers and strong error contracts.

**Good classification**:
- `shape_class = library_framework`
- `archetype = sdk_library`
- `stack_hints = ["python_package"]`
- `specificity_level = L4`
