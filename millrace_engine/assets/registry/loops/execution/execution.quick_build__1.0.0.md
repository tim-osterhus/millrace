# Quick Build

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `loop_config`
- Canonical ID: `execution.quick_build`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/loops/execution/execution.quick_build__1.0.0.json`
- Aliases: `quick-build`
- Labels: `baseline`, `execution`
- Extends: _none_
- Created At: 2026-03-18T00:00:00Z
- Updated At: 2026-03-18T00:00:00Z

## Summary

Packaged single-stage execution loop.

## Payload

```json
{
  "edges": [
    {
      "condition": null,
      "description": null,
      "edge_id": "builder_to_completed",
      "from_node_id": "builder",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": "completed",
      "to_node_id": null
    }
  ],
  "entry_node_id": "builder",
  "model_profile_ref": {
    "id": "model.default",
    "kind": "model_profile",
    "version": "1.0.0"
  },
  "nodes": [
    {
      "artifact_bindings": [],
      "kind_id": "execution.builder",
      "node_id": "builder",
      "overrides": {
        "allow_search": null,
        "effort": null,
        "model": null,
        "model_profile_ref": {
          "id": "model.default",
          "kind": "model_profile",
          "version": "1.0.0"
        },
        "prompt_asset_ref": null,
        "runner": null,
        "timeout_seconds": null
      }
    }
  ],
  "outline_policy": {
    "mode": "hybrid",
    "shard_glob": null
  },
  "plane": "execution",
  "task_authoring_profile_ref": {
    "id": "task_authoring.narrow",
    "kind": "task_authoring_profile",
    "version": "1.0.0"
  },
  "task_authoring_required": true,
  "terminal_states": [
    {
      "emits_artifacts": [
        "stage_summary"
      ],
      "ends_plane_run": true,
      "terminal_class": "success",
      "terminal_state_id": "completed",
      "writes_status": "BUILDER_COMPLETE"
    }
  ]
}
```
