# Examples Index - Contractor Classification

Load only the shards that match the current ambiguity.
Do not load every example file by default.

## Routing rules

1. Start with `EXAMPLES_SHAPES.md` when the top-level software shape is not obvious.
2. Add `EXAMPLES_PLATFORM_EXTENSIONS.md` when the goal attaches to a host platform, plugin system, or game/runtime.
3. Add `EXAMPLES_WEB_AND_NETWORK.md` when the goal sounds like a web app, portal, CRM, dashboard, or other networked business system.
4. Add `EXAMPLES_TOOLS_AND_LIBRARIES.md` when the goal sounds like a CLI, compiler, SDK, library, or other developer-facing tool.
5. Add `EXAMPLES_AMBIGUOUS_AND_EDGE_CASES.md` when the goal is mixed, underspecified, or likely to trigger false precision.

## Index table

| File | Use when | Typical tags |
|---|---|---|
| `EXAMPLES_SHAPES.md` | broad software shape is unclear | `shape`, `baseline`, `top-level` |
| `EXAMPLES_PLATFORM_EXTENSIONS.md` | host-loaded or plugin-like system | `platform_extension`, `plugin`, `minecraft`, `obsidian`, `shopify` |
| `EXAMPLES_WEB_AND_NETWORK.md` | portal, CRM, dashboard, support system, web product | `network_application`, `crud`, `dashboard`, `business_system` |
| `EXAMPLES_TOOLS_AND_LIBRARIES.md` | compiler, CLI, SDK, package, build tool | `automation_tool`, `library_framework`, `compiler`, `developer_cli` |
| `EXAMPLES_AMBIGUOUS_AND_EDGE_CASES.md` | prompt is mixed or underspecified | `ambiguous`, `mixed-shape`, `abstain`, `fallback` |

## Fast selection heuristics

### If the goal says...
- "plugin", "extension", "mod", "app for X", "bot for X" -> start with `EXAMPLES_PLATFORM_EXTENSIONS.md`
- "web app", "CRM", "dashboard", "portal", "support tool" -> start with `EXAMPLES_WEB_AND_NETWORK.md`
- "CLI", "SDK", "library", "compiler", "package" -> start with `EXAMPLES_TOOLS_AND_LIBRARIES.md`
- almost nothing concrete -> start with `EXAMPLES_AMBIGUOUS_AND_EDGE_CASES.md`

### If still unsure after one shard
- add `EXAMPLES_SHAPES.md`
- then add one domain-specific shard only if needed

## Reminder
Examples are calibration aids.
They are not permission to hallucinate certainty.
