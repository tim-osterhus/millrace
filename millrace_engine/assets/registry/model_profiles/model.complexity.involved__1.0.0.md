# Involved Complexity Model Profile

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `model_profile`
- Canonical ID: `model.complexity.involved`
- Version: `1.0.0`
- Tier: `golden`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/model_profiles/model.complexity.involved__1.0.0.json`
- Aliases: `complexity-involved`, `involved-routing`
- Labels: `complexity`, `involved`, `model`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged involved-band complexity-routing profile for LARGE-capable execution.

## Payload

```json
{
  "default_binding": {
    "allow_search": false,
    "effort": "medium",
    "model": "gpt-5.3-codex",
    "runner": "codex"
  },
  "scoped_defaults": [],
  "stage_overrides": [
    {
      "binding": {
        "allow_search": false,
        "effort": "high",
        "model": "gpt-5.3-codex",
        "runner": "codex"
      },
      "kind_id": "execution.builder"
    },
    {
      "binding": {
        "allow_search": false,
        "effort": "high",
        "model": "gpt-5.3-codex",
        "runner": "codex"
      },
      "kind_id": "execution.large-plan"
    },
    {
      "binding": {
        "allow_search": false,
        "effort": "high",
        "model": "gpt-5.3-codex",
        "runner": "codex"
      },
      "kind_id": "execution.large-execute"
    },
    {
      "binding": {
        "allow_search": false,
        "effort": "high",
        "model": "gpt-5.3-codex",
        "runner": "codex"
      },
      "kind_id": "execution.reassess"
    },
    {
      "binding": {
        "allow_search": false,
        "effort": "medium",
        "model": "gpt-5.3-codex",
        "runner": "codex"
      },
      "kind_id": "execution.refactor"
    },
    {
      "binding": {
        "allow_search": false,
        "effort": "xhigh",
        "model": "gpt-5.3-codex",
        "runner": "codex"
      },
      "kind_id": "execution.qa"
    },
    {
      "binding": {
        "allow_search": false,
        "effort": "medium",
        "model": "gpt-5.3-codex",
        "runner": "codex"
      },
      "kind_id": "execution.hotfix"
    },
    {
      "binding": {
        "allow_search": false,
        "effort": "high",
        "model": "gpt-5.3-codex",
        "runner": "codex"
      },
      "kind_id": "execution.doublecheck"
    }
  ]
}
```
