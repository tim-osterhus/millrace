# Large Thorough Mode

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `mode`
- Canonical ID: `mode.large`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/modes/mode.large__1.0.0.json`
- Aliases: `large`, `large-thorough`, `large-default`
- Labels: `large`, `mode`, `qa-verified`, `thorough-profile`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged default LARGE mode that selects the thorough QA-verified execution profile from registry data alone.

## Payload

```json
{
  "composition_rules": null,
  "execution_loop_ref": {
    "id": "execution.large",
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
