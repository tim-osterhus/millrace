# Default Model Profile

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `model_profile`
- Canonical ID: `model.default`
- Version: `1.0.0`
- Tier: `golden`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/model_profiles/model.default__1.0.0.json`
- Aliases: `default`
- Labels: `baseline`, `model`
- Extends: _none_
- Created At: 2026-03-18T00:00:00Z
- Updated At: 2026-03-18T00:00:00Z

## Summary

Packaged baseline model selection profile.

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
        "allow_search": true,
        "effort": "high",
        "model": "gpt-5.3-codex",
        "runner": "codex"
      },
      "kind_id": "execution.builder"
    }
  ]
}
```
