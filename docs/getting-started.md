# Getting started with Millrace 0.23

Install Core with Python 3.11 or newer. This example launches only a local command.

```sh
python -m pip install "millrace-ai==0.23.0"
mkdir workflow project
```

Save this graph as `workflow/workflow.json`:

```json
{
  "format": "millrace-router/v3",
  "limits": {"max_invocations": 2, "max_elapsed_seconds": 60},
  "bindings": {
    "python": {"adapter": "command", "contract": "millrace-command/v1", "executable": "python3", "config": {}}
  },
  "control_files": [],
  "defaults": {"timeout_seconds": 30},
  "planes": {
    "main": {
      "workspace": ".", "start": "step",
      "nodes": {
        "step": {
          "invoke": {"binding": "python", "config": {"args": ["-c", "print('{\"outcome\":\"DONE\",\"summary\":\"Command completed\",\"refs\":[]}')"]}},
          "result": "json-v1", "outcomes": ["DONE"],
          "on": {"DONE": {"finish": "succeeded"}},
          "on_error": {"pause": {"reason": "execution_error", "resume": "step"}},
          "on_unknown": {"pause": {"reason": "unknown", "resume": "step"}}
        }
      }
    }
  }
}
```

Create an instance using a fresh state home:

```sh
millrace router instance create --state-home ./router-state --workflow ./workflow/workflow.json --project ./project
```

Copy the returned `instance_id` into `INSTANCE`. Validate, start, and inspect it:

```sh
millrace router validate --state-home ./router-state --instance INSTANCE
millrace router start --state-home ./router-state --instance INSTANCE
millrace router status --state-home ./router-state --instance INSTANCE
```

Mutations print a request envelope before their result. Retain it for exact replay if transport outcome is unknown.
A controller may remain in the foreground until work pauses or finishes.
The first stop signal drains active work. A second stop requests cancellation.
Use [public controls](maintainers/router-controls.md) for exact request replay, edits and recovery.

For native adapters and workflow copies, install `millrace-plus[router]==0.23.0` and use its public CLI.
Provider execution requires its own configured runtime and credentials.
