# Setup and installation evidence

The published install remains the matched v0.22.3 bundle. The coordinated Core,
Plus, and meta source candidate targets 0.22.4 but is unpublished and held for
real-model qualification and final release checks. The current demo identity
requires exact Core and Plus 0.22.4 components with matching retained wheel
evidence. Older development versions in the setup inspection allowlist are not
a compatible-bundle promise; predecessor Pi proof artifacts are not the final
0.22.4 demo bundle.

For private candidate validation, use only the exact matched 0.22.4 Core, Plus,
and meta wheels supplied by the release owner in an absolute wheelhouse. Include
all required dependencies if installing offline:

    python3.12 -m venv .venv
    . .venv/bin/activate
    python -m pip install --no-index --find-links /absolute/path/to/wheelhouse "millrace==0.22.4"
    millrace setup --interactive --wheelhouse /absolute/path/to/wheelhouse
    millrace demo

The wheelhouse install command is for the unpublished candidate only; it does
not make v0.22.4 available from PyPI or establish qualification. The
millrace demo recipe is not the older S02 bundle recipe.


Interactive setup shows the exact installed Core/Plus versions, wheel filenames,
local source hashes, installation roots, evidence destination, and journal root.
It asks a separate default-no question before acquiring evidence, and another
before accepting the isolated demo execution disclosure. Decline or EOF performs
no effect from that decision. No JSON editing is needed. Without `--wheelhouse`,
explicit acquisition downloads the exact installed versions from fixed HTTPS
PyPI origins after consent. It uses no ambient proxy, credentials or package
manager configuration. It never installs or repairs packages.

The owner verifies both complete wheels against installed members and retains
whole-wheel SHA-256 evidence under the active environment's
`share/millrace/install-artifacts`. Missing, changed, unsafe, corrupt or
conflicting evidence refuses. Transfers have size and time bounds. Complete
wheel hashes correlate operator-selected bytes; they are not producer signatures.

`millrace setup` and `millrace --json setup` remain read-only. Exit 0 means
inspection completed; check `data.status` and typed blockers for readiness.
Interactive setup requires a text terminal and cannot be combined with `--json`.
OS, tmux, a backend and model credentials are not demo prerequisites. Readiness
covers only this installed isolated synthetic demo, not a useful provider run.

Setup prints the consent receipt before dispatch. After interruption use:

```sh
millrace setup --resume-receipt consent:RETURNED_ID
```

Exact recovery returns the same durable action/result IDs. A pending or uncertain
effect becomes `unknown` and is never repeated automatically. A new acquisition
consent cannot bypass an unresolved acquisition to the same destination. Retain
the receipt and files for operator inspection; do not delete a journal or partial
evidence to manufacture a clean retry. A completed acquisition invalidates the
old selection; rerun interactive setup for a fresh demo disclosure.

To inspect an existing useful selection:

```sh
millrace --json --workspace /path/to/workspace setup --action useful_recipe
millrace --json --workspace /path/to/workspace setup --action useful_recipe --plan-fingerprint sha256:...
```

The default admitted plan is used when no fingerprint is supplied. Inspection
uses a read-only store transaction and current package projection; it does not
initialize a missing store, update schema, append command audits, compile a plan,
or repair the workspace. Existing `workspace`, `package` and `plan` commands
remain the ways to prepare selections. Global workspace/db/CAS overrides are
refused for the isolated demo action. `--management-mode managed`, unknown
`--setup-schema-version`, `--bounded setup` refuses. Mutations require the separate consent operations below.

The emitted v1 fields retain the C01 selection identity, fixed Core check owners
and producer bindings, evidence correlations, blockers, and staged action/trust
fields. `SetupService(SetupRequest(...)).inspect()` supplies immutable typed data.
`service.validate_response(wire)` compares the complete transport with that
session's independently resolved observations and rereads actual selection and
installation state. Recomputed hashes, reassigned owners, stale observations,
missing checks and fabricated readiness cannot grant authority. This is a local
inspection session, not a durable receipt or authorization to execute.

Installation inventory is available through `installed_component` in
`millrace.adapters.cli.setup_inventory`. It reads installer metadata and actual
package files, verifies supported RECORD hashes, and reports an
`installed_bytes_digest` for correlation. It does not import executable package
plugins. RECORD is locally editable and does not authenticate a producer. The
installed-byte digest is never put in `artifact_sha256`; it is null until explicit whole-wheel acquisition and verification succeeds. Editable/overlaid code cannot establish
installed command identity. Missing, incompatible and unverifiable distributions
are distinct observations. No credentials or local runner config are read or
hashed. Optional meta/runner absence does not gate demo; OS and tmux are not
requirements of the headless route.

| Distribution | Supported versions | Compatibility label |
| --- | --- | --- |
| millrace-ai | 0.22.3, 0.22.4.dev0+s01, 0.22.4.dev0+s02, 0.22.4.dev0+s02mac01, 0.22.4.dev6+pi.local01, 0.22.4 | setup.v1/store.11/plan.18; 0.22.4 is the held private candidate |
| millrace-plus | 0.22.3, 0.22.4.dev0+s01, 0.22.4.dev0+s02, 0.22.4.dev0+s02mac01, 0.22.4.dev5+pi.local01, 0.22.4 | workflow-package.v1; 0.22.4 is the held private candidate |
| millrace | 0.22.3, 0.22.4.dev0+s01, 0.22.4.dev0+s02, 0.22.4.dev0+s02mac01, 0.22.4 | dependency-only.v1; 0.22.4 is the held private candidate |
| millforge | 0.1.1 | runner.0.1.1 |

These are exact setup-inspection allowlist entries, not a compatible Core/Plus/meta
bundle, demo availability, backend qualification, or release authentication.
Only the coordinated 0.22.4 candidate is the current demo source identity; its
final matched artifacts still need validation. Monitor-retained Core dev6 and
Plus dev5 wheels are predecessor scripted-provider evidence, not the 0.22.4
candidate demo pair. Unknown versions refuse component readiness.

Useful-recipe inspection resolves every context binding from the admitted plan.
It covers a conservative superset of reachable stages, including conditional
recovery. Coverage preserves binding and router IDs, checkout roots, source
bounds, empty policies, write rules, and writeback identifiers. Hydration limits remain enforced by runtime catalog selection and its cumulative
receipts. Capturing a catalog does not hydrate all entries. Stage order follows
the selected plan. Consumers should identify stages by `stage_id`.

Core uses the runtime's read-only path and bounded capture helpers. Missing or
empty required workspace sources block. Symlinks, unsafe files, invalid UTF-8,
exceeded bounds, and unstable reads also block. Optional omissions have a
non-gating `context.optional_omitted.<stage-index>.<source-index>` check. The
indices refer to the emitted coverage. No marker files are created. Future
dispatch material, selected artifacts, and selected attempts remain deferred.
`omit_if_absent` is preserved for the runtime to evaluate against an actual
attempt. Deferred runtime evidence cannot establish current context readiness.

Each source evidence reference binds its complete selected binding, source
metadata, disposition, and bounded capture observation. Captured paths, lengths,
and content hashes enter that observation. Omitted or deferred sources contain
no invented bytes. The domain is `Core.setup.context-source.v1`. Core hashes
coverage into `selection.context_digest`, then hashes the complete selection.
Equivalent captures retain the same identity. Changed bytes invalidate cached
inspection. The historical C01 planning fixture hashed only metadata. Production
also binds content to satisfy its root-content invalidation requirement.

`local_config_digest` identifies only resolved CLI workspace, database and CAS
paths, plus headless management mode. Its domain is
`Core.setup.local-path-config.v1`. Runner configuration and credentials are not
read or hashed. Runner/backend readiness remains unavailable. This digest does
not qualify a runner or claim a complete provider configuration identity.

`service.inspect_context_write(ProposedContextWrite(stage_id, path, disposition))`
revalidates the original owner observation before inspecting a proposed path.
It distinguishes selected direct-write roots from protected-proposal roots and
checks selected writeback linkage. Traversal, runtime paths, and symlinks refuse.
Writes outside all selected context roots return
`outside_selected_context_authority`. This result grants no permission and does
not prohibit unrelated task-source work. The method does not accept file content,
apply writes, or create a receipt.

Preflight is advisory. Runtime capture and writeback must recheck actual state
before use. Ordinary inspection leaves trust disclosure, acceptance and action receipts null.
Executable setup proposals require the separate explicit consent flow below. Managed-provider qualification is
unsupported. A useful plan or context check can pass while runner, installation
and trust gates keep the response blocked. The isolated demo does not inherit useful-recipe context requirements.

Resolved owner data is validated again before serialization, including all C01
string/digest types, collection bounds, enum values and boolean fields. A short
relative workspace path that resolves beyond the wire bound refuses with
`setup_wire_unrepresentable`; identities are never truncated. Missing, empty,
overlong or malformed installed Version metadata uses the explicit `unavailable`
wire marker and `component_version_unverifiable` diagnostic. That marker is not a
package version or a compatible pin.

Console declaration and loaded origin are separate observations. The response
includes `command.millrace.declaration` and `command.millrace.origin` checks and
separate `command_declaration_*` and `command_origin_*` blockers. Declaration can
be missing, incompatible, declared or unverifiable. Loaded origin can be a source
overlay, an installed path match or unverifiable. A path match can coexist with
unverifiable installed bytes; a declaration can coexist with a source overlay.
Neither alone establishes executable readiness. Exact wheel, package, command
and plan checks must also pass for the isolated demo.

## Explicit local setup actions

Useful-recipe setup can disclose and record consent for two bounded effects:
initializing a fresh workspace and creating a user-authored note inside an actual
selected missing or empty required context root. Inspection still creates no
journal. These useful-recipe effects do not qualify provider execution readiness.

Use the same `--workspace`, `--action useful_recipe`, and (for a note)
`--plan-fingerprint` selection throughout. The public transport is
`setup --setup-operation OPERATION --request-json JSON`; `--json` returns the
usual command envelope with its typed object in `data`.

1. `disclose_trust` accepts an intent with exactly `operation`, `target`,
   `content`, and `idempotency_key`. For `create_project_note`, target is a safe
   workspace-relative path and content is the user's nonblank UTF-8 task and
   acceptance criteria (at most 16 KiB and the selected source's capture bound).
   For `initialize_workspace`, target is the absolute selected workspace and
   content is null. The response contains disclosure and no executable actions.
2. `accept_trust` accepts exactly `intent`, `selection_digest`,
   `disclosure_digest`, a separate consent `idempotency_key`, and `accepted`.
   Copy the actual disclosure digests. False records nothing; true durably
   records consent and returns `{trust_acceptance, action, action_result}`.
   These are the frozen v1 records: the consent receipt, a distinct
   `accept_trust` action, and that action's durable applied result.
3. `propose` accepts exactly `{"receipt_id":"the-consent-receipt-id"}` and
   independently rechecks the selection and disclosure. Use `trust_acceptance.receipt_id` from acceptance. Copy the returned
   `proposed_actions[0]` exactly; it includes the real accepted consent ID.
4. `resume` accepts the consent receipt selector and executes or recovers its
   admitted action. `apply` accepts that exact action object. It rechecks the current owner
   observation immediately before the effect. `lookup` accepts the same
   one-field receipt selector for either a consent receipt or result receipt.

The Python API exposes `SetupActionService`, `SetupIntent` and
`SetupConsentRequest` with the same explicit sequence. Callers supply neither
actor identity nor owner facts. The journal assigns consent, acceptance-action, acceptance-result, intended-effect
action and intended-effect result IDs separately. A result's `action_id` equals the action ID; its
`consent_receipt_id` equals the independently resolved consent's own receipt ID.
The result's own `receipt_id` identifies the durable result.

Trust acceptance, its action/result, and admission of the intended effect are
one atomic journal commit. The acceptance action idempotency key is a stable
hash of the consent key and the `accept_trust` operation; it is distinct from
the intended effect's supplied key. Exact acceptance replay returns the same
three-record envelope, including after a lost response. Before the commit none
of these records exists; after it all exist, so acceptance has no ambiguous
partially applied workspace effect. Its result preserves the original selection,
invalidates no checks and grants no progress/readiness. The returned acceptance
result can be looked up or exactly replayed through `apply` without repeating
anything. Note/init effects still require the explicit `propose` and `apply`
steps described above.

The private journal lives at `~/.millrace/setup-journal`. It requires owner-only
files/directories, rejects symlinks and unsafe writable ancestors, and serializes
updates with a lock and fsynced atomic replacement. Read-only lookup does not
create it. This is a single-local-operator filesystem permission boundary;
hostile code running as that same principal is not sandboxed.

Intent is durable before any workspace effect. A completed exact replay returns
the same result without repeating the effect. Conflicting bytes refuse. A
pending intent after interruption becomes `unknown`, with no automatic repeat
and `retry_meaningful=false`; inspect the workspace and retained receipt before
any separate remediation. A proven pre-effect refusal is `blocked`. Applied
requires the public effect to return and completion evidence to be durably
recorded. Partial workspace initialization is never retried automatically.
Notes use exclusive creation and never overwrite an existing path, including a
path created after consent. Parent directories may remain after interruption.

Any actual selection change invalidates all checks from the original proposal
and sets progress false. This includes changes observed after interrupted
effects and proven pre-effect drift; neither is mislabeled as applied. The
journal retains the original and independently observed resulting selection.
It cannot promise a result while owner inspection or durable storage is failing.

Setup orchestration for `import_package` and `compile_plan` is unsupported.
Prepare that selection with the existing public commands documented in
[Getting started](getting-started.md): `package import-installed`, `package
enable`, `package verify`, and `plan admit-package` (then retain the returned
`authority_fingerprint`). Their action-specific setup consent, durable effect
recovery and automation remain a separate dependency. Their refusal here is not
execution proof. Managed session configuration is also unsupported. Useful-recipe setup never launches a runner, process, provider, paid operation
or network request, and never reads credential contents. Disclosure's content hash binds the explicitly
submitted note, not secret configuration. No placeholder criteria are generated.

## Demo automation

JSON automation uses the same typed owner and one CLI envelope. Disclose an
intent with `operation: "acquire_install_evidence"`, `target: "pypi"` or an
absolute wheelhouse path, `content: null`, and your stable `idempotency_key`.
Use `--setup-operation disclose_trust --request-json JSON`, accept the exact
returned selection/disclosure, then propose/apply or resume as described above.
After acquisition, inspect again and separately disclose the intent
`{"operation":"disclose_trust","target":"millrace demo","content":null,"idempotency_key":"YOUR_DEMO_KEY"}`.
A current accepted proposal reports `ready` and `millrace demo`. The runtime is
`planned_isolated` until that command creates it; setup never reports demo work
as completed. Stale or conflicting requests refuse rather than widening consent.


## Demo entry consumes accepted trust

`millrace demo` resolves accepted scope from the same private owner journal before
creating or acquiring any demo workspace, including on resume. It independently
revalidates the complete installed selection and current disclosure roots.
Installation-evidence acquisition consent does not authorize demo execution.
Absent or stale demo consent blocks JSON/noninteractive entry; `--auto-confirm`
never supplies onboarding consent. Text terminal entry can show and accept the
demo disclosure inline when verified wheel evidence is already present. A decline
or EOF creates neither a consent journal nor demo state from that decision.
A decline does not revoke an earlier accepted receipt; explicit revocation is
not part of this contract.

The read-only owner API `SetupActionService(SetupRequest()).resolve_demo_trust()`
returns the actual current `TrustAcceptance` or null when no current scope is
accepted. It validates the atomic acceptance action/result and the exact intended
action linkage. An admitted demo handoff or its completed applied result is
eligible; corrupt, pending, unknown or blocked demo actions refuse. It never
repairs the journal or upgrades uncertain recovery into acceptance. Changed roots,
installation or disclosure invalidate the affected receipt; retained old records
remain evidence. Demo output includes `trust_acceptance` with the real receipt ID,
selection digest and disclosure digest. Ordinary inspection continues to report
its existing staged trust blocker; inspection itself grants no execution authority.

Unchanged accepted scope can resume the same owned demo ID without a new prompt.
The existing retained-identity, lock, package, plan and runtime checks still apply;
consent does not make an incompatible retained runtime resumable. Refusal before
acquisition leaves that retained runtime unchanged.

The demo command does not acquire missing whole-wheel evidence. The fully
one-command, default-offline route after ordinary pip installation remains an
open identity-contract decision; this candidate preserves nonnull verified
artifact digests and never substitutes installed RECORD hashes for wheel hashes.
