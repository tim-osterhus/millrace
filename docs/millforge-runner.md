# Millforge Runner Setup

Millrace v0.22 supports `millforge` and `codex` runner bindings. Millforge is
the bounded compile-time default only for eligible newly compiled bindings;
Codex remains an explicit supported selection.

## Selection And Authority

The compiler may replace an unsupported but well-formed authored
`adapter_kind` with `millforge` only when the binding already contains the
required granted and supported capabilities, total stage/result mappings, and
this exact six-field component selector:

| Selector field | Required authored value |
| --- | --- |
| `component_kind` | `runner` |
| `component_id` | `millforge-base` |
| `component_version` | `2` |
| `provider_distribution` | `millforge` |
| `provider_version` | `0.1.0` |
| `descriptor_media_type` | `application/json` |

Default selection compares exactly these six fields. It does not compare a
fixed `descriptor_sha256`. The descriptor digest remains separate selected
component authority: the workflow authors it, plan admission validates it,
and the adapter checks it against the configured Millforge component before
provider work.

Explicit authored `codex` and `millforge` selections are preserved. Missing,
blank, or malformed adapter kinds are compiler errors, not defaults. After a
plan is admitted, neither a new compiler default nor a workspace default
remaps it. A requested adapter kind that differs from the admitted plan is
refused before a new claim.

## Installation

On Python 3.12 or newer, install the complete supported bundle:

```bash
python -m pip install "millrace==0.22.2"
```

The base runtime also supports Python 3.11. Install the exact member
distributions directly when you do not want the meta package:

```bash
python -m pip install \
  "millrace-ai==0.22.2" \
  "millrace-plus==0.22.2" \
  "millforge==0.1.0"
```

The v0.22 package set has these boundaries:

| Distribution | Python | Role |
| --- | --- | --- |
| `millrace-ai==0.22.2` | 3.11+ | Runtime and CLI |
| `millrace-plus==0.22.2` | 3.11+ | Official workflows and authoring skills |
| `millforge==0.1.0` | 3.11+ | Independently owned execution harness |
| `millrace==0.22.2` | 3.12+ | Dependency-only exact-pin bundle over the three members |

The `millrace` meta distribution contains no runtime code. Installing one
member distribution alone does not install the other members.

## Process Permissions

`millforge-base` is unrestricted and unsandboxed. It runs with the permissions
of the Millrace process and can read, write, delete, execute commands, access
the network, and access credentials available to that process. Use a trusted,
bounded workspace and operating-system account.

## Adapter Configuration

Pass a closed local configuration envelope to `millrace run daemon` with
`--adapter-config-json`:

```json
{
  "millforge": {
    "adapter_id": "local-millforge",
    "workspace_root": "/absolute/path/to/runner-workspace",
    "timeout_seconds": 120,
    "model_profile": {
      "profile_id": "operator-profile",
      "provider_id": "openai-compatible",
      "model_id": "tool-capable-model",
      "endpoint": {
        "base_url": "https://models.example/v1"
      },
      "authentication": {
        "scheme": "bearer",
        "secret_ref": {
          "secret_id": "model-api-key",
          "env_var": "MILLRACE_MODEL_API_KEY"
        }
      },
      "timeout_seconds": 120,
      "maximum_output_tokens": 4096,
      "reasoning": {
        "mode": "disabled"
      },
      "capabilities": {
        "support": {
          "system_messages": "supported",
          "tool_calls": "supported",
          "tool_result_messages": "supported"
        }
      },
      "request_options": {
        "allowed_options": [
          "parallel_tool_calls"
        ]
      },
      "source_name": "operator-config",
      "source_digest": "operator-profile-v1"
    },
    "secret_ref": {
      "secret_id": "model-api-key",
      "env_var": "MILLRACE_MODEL_API_KEY"
    },
    "redaction_policy": {
      "policy_id": "operator-local",
      "secret_tokens": []
    }
  }
}
```

Replace `provider_id`, `model_id`, and `endpoint.base_url` with exact values
for the selected provider and model. Millforge validates the complete public
`ResolvedModelProfile`; it does not discover a model, infer capabilities,
choose provider defaults, or fall back to another provider or model.

The three capability declarations state what the selected model supports; they
do not add selected workflow capabilities. The request-option allowlist
permits the provider request to carry the `parallel_tool_calls` control. Local
profile data cannot add routes, terminal outcomes, schemas, assets, mappings,
or capabilities to the admitted plan.

The provider-neutral baseline disables reasoning and therefore needs no wire
field/value or replay mapping. Enable or require reasoning only when you can
supply the exact provider-specific mode, effort, and replay mappings accepted
by that provider. Millforge does not infer those mappings.

The outer adapter timeout and profile timeout are local ceilings. The selected
plan retains invocation-timeout authority, and execution uses the lower
applicable limit.

## Environment Secret

Set the credential only in the environment variable named by `SecretRef`:

```bash
export MILLRACE_MODEL_API_KEY='<provider credential>'
```

The profile authentication `secret_ref` must exactly match the adapter's
top-level `secret_ref`. The JSON stores only the secret identifier and
environment-variable name. Do not put the secret value, an authorization
header, or credentials in a URL, workflow package, prompt, or adapter file.

## Run The Daemon

Run a bounded session first:

```bash
millrace --workspace /absolute/path/to/workspace run daemon \
  --max-ticks 8 \
  --adapter-kind millforge \
  --adapter-config-json /absolute/path/to/millforge-adapter.json \
  --monitor basic
```

Omit `--max-ticks` only when the daemon should continue polling until stopped.
Use `millrace run daemon --help` to inspect the command surface.

An invalid local envelope, unavailable Millforge package, missing environment
secret, selected component mismatch, requested adapter mismatch, or invalid
runner evidence is refused. See [Errors and refusals](errors.md) for the public
failure contract.

Millforge execution uses the same runner-session lifecycle as other adapters.
Factory-created facades receive a cooperative cancellation token and are
closed by Millrace exactly once when their worker ends. Millrace does not
invent terminate or kill support that the public facade does not expose.
Injected facades remain owned by their caller, so Millrace neither closes nor
claims cooperative cancellation control over them. Local worker-thread exit
alone does not prove external cleanup: if an injected facade times out, is
cancelled, or raises after execution begins, the session reports orphan risk
because residual provider work may remain. A normally returned facade result
proves the call ended and needs no caller-owned facade cleanup. A worker or
owned close that remains unresolved is likewise reported as orphan risk rather
than clean completion. Restart reconciliation is unsupported for these
in-process workers; a restart therefore fails closed through the generic
lost/orphan safety path.

Millforge output is candidate execution evidence. Millrace validates it
against the admitted plan and remains responsible for accepted routing, legal
terminal outcomes, operator waits, and workflow completion.

Inspect durable Millforge session identity, cancellation, cleanup,
completion/application status, and orphan risk with `runs show`, `trace show
RUN_ID`, `status`, and `doctor`. `runs follow RUN_ID --after-sequence N`
returns a finite bounded event page and durable final status. See
[Runner-session architecture](runner-session-architecture.md) and
[Daemon lifecycle](daemon-lifecycle.md).
