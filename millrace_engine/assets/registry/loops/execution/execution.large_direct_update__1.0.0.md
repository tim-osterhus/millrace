# Large Direct Update Loop

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `loop_config`
- Canonical ID: `execution.large_direct_update`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/loops/execution/execution.large_direct_update__1.0.0.json`
- Aliases: `large-direct-update`, `large-no-qa`
- Labels: `direct-update`, `execution`, `large`, `skip-post-refactor-qa`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged alternate LARGE loop that completes via update after refactor, including non-blocking refactor recovery and skipping post-refactor QA participation.

## Payload

```json
{
  "edges": [
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.plan.success.execute",
      "from_node_id": "large_plan",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "large_execute"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.plan.blocked",
      "from_node_id": "large_plan",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.execute.success.reassess",
      "from_node_id": "large_execute",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "reassess"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.execute.blocked",
      "from_node_id": "large_execute",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.reassess.success.refactor",
      "from_node_id": "reassess",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "refactor"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.reassess.blocked",
      "from_node_id": "reassess",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.refactor.success.update",
      "from_node_id": "refactor",
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
      "edge_id": "execution.large.refactor.blocked.update",
      "from_node_id": "refactor",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "update"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.update.success.archive",
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
  "entry_node_id": "large_plan",
  "model_profile_ref": {
    "id": "model.default",
    "kind": "model_profile",
    "version": "1.0.0"
  },
  "nodes": [
    {
      "artifact_bindings": [],
      "kind_id": "execution.large-plan",
      "node_id": "large_plan",
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
      "kind_id": "execution.large-execute",
      "node_id": "large_execute",
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
      "kind_id": "execution.reassess",
      "node_id": "reassess",
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
      "kind_id": "execution.refactor",
      "node_id": "refactor",
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
        "run_bundle",
        "stage_summary"
      ],
      "ends_plane_run": true,
      "terminal_class": "success",
      "terminal_state_id": "idle",
      "writes_status": "UPDATE_COMPLETE"
    },
    {
      "emits_artifacts": [
        "stage_summary"
      ],
      "ends_plane_run": true,
      "terminal_class": "blocked",
      "terminal_state_id": "blocked",
      "writes_status": "BLOCKED"
    }
  ]
}
```
