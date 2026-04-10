# Examples - Platform Extensions and Host-Loaded Systems

---

## EX-CONT-101: Minecraft mod without loader details

**Tags**: `platform_extension`, `minecraft`, `unknown_specialization`

**Prompt**:
Build a Minecraft mod that adds an aura collector, conduit, reservoir, infuser, and infused weapon.

**Good classification**:
- `shape_class = platform_extension`
- `archetype = gameplay_mod`
- `host_platform = minecraft`
- `stack_hints = ["jvm"]` only if that assumption is justified by context
- `specializations = {}`
- `unresolved_specializations = ["loader"]`
- `specificity_level = L3` or `L4`

**Why**:
Minecraft is justified. Fabric vs Forge vs NeoForge is not.

---

## EX-CONT-102: Explicit Forge mod

**Tags**: `platform_extension`, `minecraft`, `forge`

**Prompt**:
Build a Forge 1.20.1 Minecraft progression mod with new magical machines and advancement-based progression.

**Good classification**:
- `shape_class = platform_extension`
- `archetype = gameplay_mod`
- `host_platform = minecraft`
- `stack_hints = ["jvm", "gradle"]`
- `specializations = {"loader": "forge"}`
- `specificity_level = L5`

**Why**:
The loader is explicit.

**Bad classification**:
- resolved `loader=fabric`
- top-level class `minecraft_fabric_mod`

---

## EX-CONT-103: Obsidian plugin

**Tags**: `platform_extension`, `plugin_integration`, `obsidian`

**Prompt**:
Create an Obsidian plugin that turns highlighted notes into structured flashcards and study queues.

**Good classification**:
- `shape_class = platform_extension`
- `archetype = plugin_integration`
- `host_platform = obsidian`
- `specificity_level = L3`

**Why**:
The product is loaded into another app's runtime and uses its extension model.

---

## EX-CONT-104: Shopify app with backend

**Tags**: `mixed`, `platform_extension`, `network_application`, `shopify`

**Prompt**:
Build a Shopify app that helps merchants manage post-purchase upsells with an embedded dashboard and background sync.

**Good classification**:
- primary `shape_class = platform_extension`
- `archetype = plugin_integration`
- `host_platform = shopify`
- note that networked backend components are likely, but the primary product unit is still a host-linked platform app
- `specificity_level = L3`

**Why**:
The host platform is part of the core product identity.

---

## EX-CONT-105: Discord moderation bot

**Tags**: `platform_extension`, `discord`, `automation_tool`

**Prompt**:
Create a Discord bot for moderation workflows, escalation rules, and incident summaries.

**Good classification**:
- usually `shape_class = platform_extension`
- `archetype = plugin_integration`
- `host_platform = discord`
- `specificity_level = L3`

**Why**:
Even if it has service-like components, the primary product shape is still host-attached automation within Discord.
