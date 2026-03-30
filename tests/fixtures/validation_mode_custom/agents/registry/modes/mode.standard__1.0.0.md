# Validation Standard Runtime Shadow

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `mode`
- Canonical ID: `mode.standard`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `workspace_defined`
- Source Ref: `agents/registry/modes/mode.standard__1.0.0.json`
- Aliases: `standard`, `default-autonomous`, `validation-runtime-custom`
- Labels: `validation`, `mode`, `workspace`
- Extends: _none_
- Created At: 2026-03-23T00:00:00Z
- Updated At: 2026-03-23T00:00:00Z

## Summary

Workspace shadow routing the standard runtime path into the validation custom loop.

## Payload

```json
{
  "composition_rules": null,
  "execution_loop_ref": {
    "id": "execution.validation_mode_custom",
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
