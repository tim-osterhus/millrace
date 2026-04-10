# Examples - Broad Software Shapes

Append-only style is recommended for future examples.

---

## EX-CONT-001: Minecraft magic mod prompt

**Tags**: `shape`, `platform_extension`, `gameplay_mod`

**Prompt**:
Build a Minecraft magic mod with aura collection, routing, infusion, and one aura-powered weapon.

**Good classification**:
- `shape_class = platform_extension`
- `archetype = gameplay_mod`
- `host_platform = minecraft`
- `stack_hints = []` if no loader/build details are explicit yet
- `specificity_level = L3`

**Why**:
This is obviously not a standalone app. It is a host-loaded extension attached to Minecraft.

**Bad classification**:
- top-level `minecraft_fabric_mod`
- `loader=fabric` with no evidence

---

## EX-CONT-002: Church management portal

**Tags**: `shape`, `network_application`, `crud_business_system`

**Prompt**:
Build church management software for pastors to track members, attendance, groups, care notes, and follow-up.

**Good classification**:
- `shape_class = network_application`
- `archetype = crud_business_system`
- `host_platform = church_ops`
- `specificity_level = L3`

**Why**:
The prompt implies a networked business system with records, workflow, and multi-user access patterns.

**Bad classification**:
- `service_backend` only
- `generic_product`

---

## EX-CONT-003: Autonomous compiler project

**Tags**: `shape`, `automation_tool`, `compiler_toolchain`

**Prompt**:
Create a self-hosting C compiler with a deterministic test harness and large-project build validation.

**Good classification**:
- `shape_class = automation_tool`
- `archetype = compiler_toolchain`
- `specificity_level = L2`

**Why**:
The primary deliverable is a toolchain, not a portal, service, or plugin.

**Bad classification**:
- `library_framework`
- `interactive_application`

---

## EX-CONT-004: Python SDK for billing integrations

**Tags**: `shape`, `library_framework`, `sdk_library`

**Prompt**:
Build a Python SDK for interacting with our billing and subscription APIs.

**Good classification**:
- `shape_class = library_framework`
- `archetype = sdk_library`
- `stack_hints = ["python_package"]`
- `specificity_level = L4`

**Why**:
The product is primarily a reusable library surface.

**Bad classification**:
- `service_backend`
- `network_application`

---

## EX-CONT-005: Batch sync pipeline

**Tags**: `shape`, `data_system`, `etl_pipeline`

**Prompt**:
Build a nightly pipeline that ingests order data, normalizes it, and loads it into analytics tables.

**Good classification**:
- `shape_class = data_system`
- `archetype = etl_pipeline`
- `specificity_level = L2`

**Why**:
The primary shape is data movement and transformation.
