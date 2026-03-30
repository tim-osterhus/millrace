# Default Autonomous Mode Validation Overlay

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `mode`
- Canonical ID: `mode.default_autonomous`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `workspace_defined`
- Source Ref: `agents/registry/modes/mode.default_autonomous__1.0.0.json`
- Aliases: `default-autonomous`
- Labels: `baseline`, `mode`, `validation`
- Extends: _none_
- Created At: 2026-03-23T00:00:00Z
- Updated At: 2026-03-23T00:00:00Z

## Summary

Workspace validation overlay that reroutes the packaged autonomous mode to the packaged standard loop.

## Payload

```json
{
  "composition_rules": null,
  "execution_loop_ref": {
    "id": "execution.standard",
    "kind": "loop_config",
    "version": "1.0.0"
  },
  "model_profile_ref": {
    "id": "model.default",
    "kind": "model_profile",
    "version": "1.0.0"
  },
  "outline_policy": {
    "mode": "hybrid",
    "shard_glob": null
  },
  "policy_toggles": null,
  "research_loop_ref": null,
  "research_participation": "none",
  "task_authoring_profile_ref": {
    "id": "task_authoring.narrow",
    "kind": "task_authoring_profile",
    "version": "1.0.0"
  }
}
```
