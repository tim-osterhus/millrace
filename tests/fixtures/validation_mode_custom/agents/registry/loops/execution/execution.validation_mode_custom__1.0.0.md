# Validation Mode Custom Execution Loop

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `loop_config`
- Canonical ID: `execution.validation_mode_custom`
- Version: `1.0.0`
- Tier: `ad_hoc`
- Status: `active`
- Source Kind: `workspace_defined`
- Source Ref: `agents/registry/loops/execution/execution.validation_mode_custom__1.0.0.json`
- Aliases: `validation-mode-custom`
- Labels: `validation`, `execution`, `workspace`
- Extends: _none_
- Created At: 2026-03-23T00:00:00Z
- Updated At: 2026-03-23T00:00:00Z

## Summary

Workspace-defined validation loop for fixture coverage.

## Payload

```json
{
  "edges": [
    {
      "condition": null,
      "description": null,
      "edge_id": "validation.builder.success.update",
      "from_node_id": "builder",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "update"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "validation.update.success.idle",
      "from_node_id": "update",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": "idle",
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
        "model_profile_ref": null,
        "prompt_asset_ref": null,
        "runner": null,
        "timeout_seconds": null
      }
    },
    {
      "artifact_bindings": [],
      "kind_id": "execution.update",
      "node_id": "update",
      "overrides": {
        "allow_search": null,
        "effort": null,
        "model": null,
        "model_profile_ref": null,
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
      "terminal_state_id": "idle",
      "writes_status": "UPDATE_COMPLETE"
    }
  ]
}
```
