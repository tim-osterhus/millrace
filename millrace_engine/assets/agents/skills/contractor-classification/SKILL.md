---
name: contractor-classification
description: >
  Classifies GoalSpec seeds into layered planning profiles based on software shape first,
  then justified specialization. This skill should be used by the Contractor substage
  before objective-profile sync, completion-manifest drafting, spec synthesis, spec review,
  and task decomposition rely on repo topology, proof, or decomposition assumptions.
compatibility:
  runners: ["codex-cli", "claude-code", "openclaw"]
  tools: ["Read", "Grep", "Bash", "Write", "Web"]
  offline_ok: false
---

# Contractor Classification

## Purpose
Classify one goal into a layered planning profile that improves topology grounding, proof standards, and decomposition vocabulary without faking precision.

## Quick start
Goal:
- Turn one seed prompt into a bounded planning profile that downstream deterministic GoalSpec code can trust.

Use when (triggers):
- GoalSpec begins from a new raw idea or staged goal.
- Planning is drifting into generic or synthetic repo surfaces.
- Proof standards and decomposition vocabulary need early shaping.
- The goal mentions a host platform, framework, plugin target, runtime, ecosystem, or product archetype.

Do NOT use when (non-goals):
- Writing the final spec or task cards.
- Mutating runtime governance or planning code directly.
- Performing open-ended market or implementation research.
- Trying to infer narrow stack details from weak evidence.

## Core doctrine
- **Shape first, specialization second.**
- **Selector, not mutator.** The Contractor emits profile artifacts; it does not rewrite the system's truth.
- **The lowest adequate specificity wins.**
- **Abstention beats bluffing.**
- **Concrete ecosystem examples stay in `EXAMPLES_*.md` only.** The non-example skill surface stays generic.
- **Examples calibrate judgment; they do not act as templates to copy blindly.**

## Classification axes

### 1) Shape class (top-level, broad, symmetric)
Use one of:
- `platform_extension`
- `interactive_application`
- `network_application`
- `service_backend`
- `automation_tool`
- `library_framework`
- `data_system`
- `content_system`
- `unknown`

### 2) Archetype (product pattern)
Examples:
- `gameplay_mod`
- `crud_business_system`
- `dashboard_portal`
- `developer_cli`
- `compiler_toolchain`
- `etl_pipeline`
- `plugin_integration`
- `sdk_library`
- `content_pipeline`

### 3) Host / domain
Examples:
- `named_host_platform`
- `commerce_platform`
- `knowledge_workspace`
- `collaboration_runtime`
- `operations_domain`

### 4) Stack hints
Examples:
- `jvm`
- `gradle`
- `python_package`
- `react_frontend`
- `node_service`
- `postgres_backed`

### 5) Specializations
Examples:
- `auth=required`
- `delivery=realtime`
- `deployment=self_hosted`
- `extension_mode=host_loaded`
- `integration_scope=workspace_local`

## Specificity ladder
- `L0`: abstain / unknown
- `L1`: shape only
- `L2`: shape + archetype
- `L3`: add host/domain
- `L4`: add stack hints
- `L5`: add specialization overlays

Rule:
- never emit a higher specificity level than the evidence supports
- never infer a narrow specialization just because one nearby specialization is common

## What Contractor should optimize for

### A) Topology selection
Give downstream planning better repo-root assumptions.
Examples:
- host-loaded platform extension vs standalone product
- JVM/Gradle hints vs generic src/tests placeholders
- plugin host expectations vs first-party service assumptions

### B) Proof contract shaping
Give downstream planning better evidence expectations.
Examples:
- host-platform integration proof
- repo-native test/build commands
- runnable verification vs grep-only or file-existence-only proof

### C) Decomposition vocabulary
Give downstream synthesis and review the right nouns.
Examples:
- commands, assets, rules, progression, integration checks
- auth, CRUD, migrations, inbox, workflow, permissions
- command surface, exit codes, config, stdout contract

## Inputs this Skill expects
Required:
- staged goal or canonical goal source
- `_contractor.md`
- `EXAMPLES_INDEX.md`
- only the relevant example shard files selected via the index
- contractor profile schema

Optional:
- semantic seed files
- README or package/build metadata when local disambiguation is needed
- tiny, targeted web lookups when local evidence is insufficient

If required inputs are missing:
- stop and return the minimal missing artifact

## Output contract
Primary deliverable:
- `agents/objective/contractor_profile.json`

Secondary deliverable:
- `agents/reports/contractor_profile.md`

Definition of DONE (objective checks):
- [ ] The profile classifies software **shape** even when narrow specialization is unknown.
- [ ] The reported specificity level matches the actual evidence.
- [ ] The profile selects only approved downstream profile IDs.
- [ ] Unsupported specializations are recorded as unresolved instead of hallucinated into resolved overlays.
- [ ] Evidence, abstentions, and contradictions are explicit.
- [ ] Browsing, if used, is small, targeted, and justified.

## Procedure (copy into working response and tick off)
Progress:
- [ ] 1) Lock the source goal
- [ ] 2) Select relevant examples
- [ ] 3) Do local-only first pass
- [ ] 4) Decide whether micro-browsing is warranted
- [ ] 5) Set specificity level
- [ ] 6) Select downstream profile IDs
- [ ] 7) Record abstentions and contradictions
- [ ] 8) Write artifact and report

### 1) Lock the source goal
- Read the authoritative staged goal.
- Prefer canonical goal text over downstream derivative prose when the two differ.
- Ignore generic planning boilerplate unless the source goal itself explicitly endorses it.

### 2) Select relevant examples
- Use `EXAMPLES_INDEX.md` to select only the most relevant shards.
- Prefer examples that match the same **shape** first.
- Use host/domain examples only when the goal mentions a known host or domain.
- Load ambiguous/edge-case examples when the seed is unclear or mixed.

### 3) Do local-only first pass
- Identify the top-level shape.
- Identify any justified archetype.
- Identify host/domain only if the goal actually implies it.
- Identify stack hints only if they are directly stated or strongly implied by the goal.
- Do not guess narrow overlays yet.

### 4) Decide whether micro-browsing is warranted
Browse only if one or two targeted lookups can materially reduce ambiguity.
Valid reasons:
- unfamiliar term
- named framework/host ambiguity
- specialization materially changes topology or proof
- classification confidence remains below threshold after local reasoning

Bad reasons:
- curiosity
- broad product research
- fishing for extra detail not required for classification

### 5) Set specificity level
- `L0` when almost nothing can be justified
- `L1` when only broad shape is trustworthy
- `L2` when a product pattern is also trustworthy
- `L3` when host/domain is justified
- `L4` when stack hints are justified
- `L5` only when narrow specialization overlays are directly supported

### 6) Select downstream profile IDs
Select only IDs that are supported by the runtime.
Typical pattern:
- one shape profile
- zero or one archetype profile
- zero or one host/domain profile
- zero or more stack overlays
- zero or more specialization overlays

If the runtime does not support a justified specialization:
- keep the broad supported layers
- record the specialization as unresolved
- do not invent a new resolved profile ID

### 7) Record abstentions and contradictions
Always record:
- what you are not willing to claim yet
- what evidence would upgrade the profile
- any contradictions between the goal text, staged brief, and repo hints

### 8) Write artifact and report
- Write machine-readable JSON first.
- Write the short Markdown report second.
- Keep the report concise and audit-friendly.

## Browse policy
Contractor may use the web, but only for **conditional micro-browsing**.

Rules:
- Start local-first.
- Keep lookups small and authoritative.
- Use browsing for disambiguation, not for speculative design work.
- Record whether browsing actually changed the result.
- If browsing adds no real signal, say so and keep the earlier classification.

## Guardrails + anti-patterns
- Do not flatten narrow platform details into top-level categories.
- Do not treat `named_host_variant` as a peer of `web_app`, `library`, or `service`.
- Do not collapse to `generic_product` when a broader shape classification is justified.
- Do not let one framework keyword force a stack guess that the prompt never made.
- Do not convert example patterns into mandatory templates.
- Do not browse by default just because the web is available.
- Do not write the completion manifest, spec, or task cards here.

## Escalation
Stop and return a conservative profile when:
- the goal is too ambiguous to justify more than `L0` or `L1`
- the seed prompt mixes multiple incompatible software shapes with no clear primary unit of work
- no supported downstream profiles match the justified classification layers

When blocked or conservative:
- emit the best safe lower-specificity profile
- say what evidence would unlock the next level

## Progressive disclosure
- Example routing: `./EXAMPLES_INDEX.md`
- Broad-shape calibration: `./EXAMPLES_SHAPES.md`
- Hosted/plugin/platform-extension calibration: `./EXAMPLES_PLATFORM_EXTENSIONS.md`
- Web/network/business-system calibration: `./EXAMPLES_WEB_AND_NETWORK.md`
- Tooling/library/compiler calibration: `./EXAMPLES_TOOLS_AND_LIBRARIES.md`
- Edge-case handling: `./EXAMPLES_AMBIGUOUS_AND_EDGE_CASES.md`
