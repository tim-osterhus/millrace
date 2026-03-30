# Research Incident Loop

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `loop_config`
- Canonical ID: `research.incident`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/loops/research/research.incident__1.0.0.json`
- Aliases: `incident`, `research-incident`
- Labels: `baseline`, `incident`, `research`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged incident-response research loop scaffold for deterministic incident handling.

## Payload

```json
{
  "edges": [
    {
      "condition": null,
      "description": null,
      "edge_id": "research.incident.intake.success.resolve",
      "from_node_id": "incident_intake",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "incident_resolve"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.incident.intake.failure.blocked",
      "from_node_id": "incident_intake",
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
      "edge_id": "research.incident.resolve.success.archive",
      "from_node_id": "incident_resolve",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "incident_archive"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.incident.resolve.failure.blocked",
      "from_node_id": "incident_resolve",
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
      "edge_id": "research.incident.archive.success.idle",
      "from_node_id": "incident_archive",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": "idle",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.incident.archive.failure.blocked",
      "from_node_id": "incident_archive",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    }
  ],
  "entry_node_id": "incident_intake",
  "model_profile_ref": {
    "id": "model.default",
    "kind": "model_profile",
    "version": "1.0.0"
  },
  "nodes": [
    {
      "artifact_bindings": [],
      "kind_id": "research.incident-intake",
      "node_id": "incident_intake",
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
      "artifact_bindings": [
        {
          "input_artifact": "incident_record",
          "source_artifact": "incident_record",
          "source_node_id": "incident_intake"
        }
      ],
      "kind_id": "research.incident-resolve",
      "node_id": "incident_resolve",
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
      "artifact_bindings": [
        {
          "input_artifact": "incident_resolution",
          "source_artifact": "incident_resolution",
          "source_node_id": "incident_resolve"
        }
      ],
      "kind_id": "research.incident-archive",
      "node_id": "incident_archive",
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
  "outline_policy": null,
  "plane": "research",
  "task_authoring_profile_ref": null,
  "task_authoring_required": false,
  "terminal_states": [
    {
      "emits_artifacts": [
        "stage_summary"
      ],
      "ends_plane_run": true,
      "terminal_class": "success",
      "terminal_state_id": "idle",
      "writes_status": "IDLE"
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
