# Examples - Ambiguous and Edge Cases

---

## EX-CONT-401: Mixed prompt with no clear primary unit

**Tags**: `ambiguous`, `mixed-shape`, `conservative`

**Prompt**:
Build a platform with a website, mobile app, backend, AI assistant, marketplace, and hardware sync.

**Good classification**:
- `shape_class = unknown` or the best safe high-level class if one clearly dominates
- `specificity_level = L0` or `L1`
- explicit note that the prompt is multi-product and underspecified

**Why**:
The prompt bundles multiple distinct product shapes without a clear primary unit of work.

---

## EX-CONT-402: Minecraft mod with repo clues but not explicit loader

**Tags**: `ambiguous`, `minecraft`, `safe-upgrade`

**Prompt**:
Build a Minecraft magic mod.

**Additional local evidence**:
- repo contains `build.gradle`
- repo contains `src/main/resources/fabric.mod.json`

**Good classification**:
- `shape_class = platform_extension`
- `archetype = gameplay_mod`
- `host_platform = minecraft`
- `stack_hints = ["jvm", "gradle"]`
- `specializations = {"loader": "fabric"}`
- `specificity_level = L5`

**Why**:
The specialization is now supported by repo evidence, not guesswork.

---

## EX-CONT-403: Web product vs backend service ambiguity

**Tags**: `ambiguous`, `network_application`, `service_backend`

**Prompt**:
Build a customer notifications system.

**Good first pass**:
- `shape_class = service_backend`
- `specificity_level = L1`
- abstain on user-facing portal or workflow system unless the prompt says so

**Why**:
"System" alone does not imply a dashboard or portal.

---

## EX-CONT-404: Niche framework term

**Tags**: `micro-browse`, `unknown-term`

**Prompt**:
Build a plugin for FrostHook that adds reactive command routing.

**Good behavior**:
- use local reasoning first
- if FrostHook is unfamiliar and materially affects classification, do one or two small authoritative lookups
- classify only after disambiguation

**Bad behavior**:
- pretend FrostHook is obviously a CLI library or obviously a game engine without checking

---

## EX-CONT-405: Unsupported specialization

**Tags**: `unsupported-overlay`, `resolved-vs-unresolved`

**Prompt**:
Build a NeoForge Minecraft progression mod.

**Good classification**:
- broad layers resolved
- `specializations = {"loader": "neoforge"}`
- if runtime has no supported `loader.neoforge` overlay yet, keep it in `unresolved_specializations`
- do not invent a resolved profile ID that does not exist
