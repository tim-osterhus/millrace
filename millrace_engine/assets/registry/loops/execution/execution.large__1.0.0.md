# Large Thorough Execution Loop

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `loop_config`
- Canonical ID: `execution.large`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/loops/execution/execution.large__1.0.0.json`
- Aliases: `large`, `large-thorough`, `thorough-execution`
- Labels: `execution`, `large`, `qa-verified`, `thorough`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged default LARGE loop with the ordered plan/execute/reassess/refactor chain plus post-refactor QA participation, including non-blocking refactor recovery.

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
      "edge_id": "execution.large.refactor.success.qa",
      "from_node_id": "refactor",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "qa"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.refactor.blocked.qa",
      "from_node_id": "refactor",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "qa"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.qa.success.update",
      "from_node_id": "qa",
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
      "edge_id": "execution.large.qa.quickfix.hotfix",
      "from_node_id": "qa",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "quickfix_needed"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "hotfix"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.qa.blocked",
      "from_node_id": "qa",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.hotfix.success.doublecheck",
      "from_node_id": "hotfix",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "doublecheck"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.hotfix.blocked",
      "from_node_id": "hotfix",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.doublecheck.success.update",
      "from_node_id": "doublecheck",
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
      "edge_id": "execution.large.doublecheck.quickfix.hotfix",
      "from_node_id": "doublecheck",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "quickfix_needed"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "hotfix"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.large.doublecheck.blocked",
      "from_node_id": "doublecheck",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
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
      "kind_id": "execution.qa",
      "node_id": "qa",
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
      "kind_id": "execution.hotfix",
      "node_id": "hotfix",
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
      "kind_id": "execution.doublecheck",
      "node_id": "doublecheck",
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
