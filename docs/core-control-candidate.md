# Core control contracts

Millrace v0.22.3 implements exact operation reconciliation,
unstarted run pause/resume, the narrow native cooperative profile described below,
local macOS daemon lifecycle control, and bounded public read projections.
Native source evidence, independent review, installed artifact attribution and
release qualification remain separate gates. A package version alone proves
none of them.

## Exact control authority

`operations show`, `operations resolve`, `runs pause`, `runs resume`, and
`daemon stop` consume exact request JSON. Preserve the operation ID, caller,
action, target and payload when reconciling an uncertain outcome. A new key is
not a retry. Immutable receipts establish acceptance; append-only results record
settlement and aftermath. Historical replay remains separate from fresh state.
The store validates retained canonical records, identities, source ordering and
receipt/result linkage before returning control evidence. Reads never repair
missing or corrupt authority.

Targets bind workspace, instance, store epoch and source revision. Run targets
also bind the selected plan and exact run/session generation and fence. Daemon
targets bind incarnation, process identity and the returned `expected_runtime`
tuple. Copy that tuple exactly: it includes public contract ID/revision, runtime
distribution/version, build identity, store schema version and schema digest.
The read schema digest and daemon schema digest identify different declarations;
neither is an installed wheel hash. Build identity remains explicitly unavailable
in this release.

Caller observation has a five-second budget. CLI controls and bounded reads use
a real four-second interruption boundary and bounded SQLite work. Uncertain
commit delivery, host suspension or uninterruptible storage still requires
same-key reconciliation. Control transactions and operation reads use a 100 ms
SQLite busy timeout to leave scheduling headroom; it is a requested sleep budget,
not a wall-clock guarantee. Sustained contention may therefore return storage
unknown earlier, requiring the same-key reconciliation path. The prior connection
timeout is restored. No control transaction spans a provider/native wait.

## Run holds and budgets

`runs pause --request-json JSON` and `runs resume --request-json JSON` support
admitted open unobserved runs with no session, or a coherent created session
without start intent, native locator or reservation. Selected Codex and Millforge
bindings support this unstarted operation. Started Millforge invocations can also
use the exact cooperative profile described below when the production owner and
effect boundary pass fresh admission checks. Other started/native profiles remain
unsupported; source implementation does not establish installed or release
qualification.

A hold retains the run, claim, selected plan, session fence, context and resource
authority. Resume releases the exact pause ID; it does not drive work. Immutable
control history survives later state changes. Session creation, retry, context
attachment, starting and result application respect durable holds. Legitimate
cancellation or selected closure authority can supersede a hold; contradictory
completion remains unknown aftermath.

Atomic start stages CAS/context outside its write lock, then checks holds and the
explicit driving budget epoch before committing intent and reservation. An
unbudgeted session has a null binding; readers do not infer the current daemon
epoch. Pause/resume does not reset budget deadlines, totals or accepted starts.

The daemon retains accepted live owners across bounded service turns. It checks
controls, completion and the original absolute deadline for each owner on one
coordinator thread. Ordinary unheld work remains sequential.

A verified native
hold permits another eligible created session or queue candidate through the
existing claim and start guards. Resuming the first owner while the second runs
keeps both accepted attempts under supervision. Pending pause alone does not
permit another start. Explicit activation selection remains scoped to that run.

Held flags guide selection only. Before a new start, the coordinator validates
retained owner/session identities and verified holds against the start-intent
state snapshot. Existing transactional control-map and history guards reject a
resume or other conflicting write before intent and budget reservation commit.
A lost opportunity leaves the selected session created, unstarted and unbound.
The daemon services its retained owners before reconsidering that same session.
Resume accepted after the new start commits still permits both owners to progress.

Invocation or token exhaustion closes admission while accepted owners settle.
Final usage still contributes to the same epoch. Stop, wall expiry, errors and
max-tick exit drain all retained owners before lifecycle and lock cleanup.
Cancellation grace advances cooperatively, preserving each original phase deadline.

The daemon retains consumed outcomes across conflicting public writes and reloads
authority before retrying persistence. No continuation retains a closed runtime
connection, and no transaction spans native waiting or context I/O.

Source tests
exercise both preclaimed and later-enqueued independent work. Fresh installed
candidate proof and independent review remain separate qualification steps.

## Local daemon lifecycle

`run daemon` launches the local service. `daemon inspect` observes its exact
incarnation; `daemon stop` accepts an exact stop request. Readiness combines
retained state with a separately attributed fresh listener challenge. Stop blocks
new units, claims, sessions and starts while permitting accepted cleanup.
Immutable session-fence and cleanup collections retain all affected attempts,
including collections larger than one page. Final unknown cleanup stays unknown.
See [daemon-lifecycle.md](daemon-lifecycle.md) for process birth, peer, owner-path,
listener and caller-bound details. The supported process boundary is macOS;
unsupported platforms retain foreground `run daemon` execution without creating
a lifecycle incarnation or advertising native controls. They do not receive
positive lifecycle classification. macOS requires a short, owner-safe workspace
path: no symlink or group/world-writable ancestor, and the socket path must be
shorter than 104 encoded bytes. Shared temporary directories are unsuitable.

Daemon history is durable evidence, not a fresh process observation. A challenge
time is not provider or workflow progress. The retained session lifecycle times
can identify the last known creation/start-intent/start/end event, with exact
session scope and source field. Daemon progress now captures newly accepted
workflow transitions atomically with runtime persistence, with exact incarnation,
capture revisions, retained transition reference and applicable run/session
snapshot fences. Its wall timestamp is durable capture time, not provider or tool
liveness. New incarnations distinguish no work yet from legacy missing evidence;
unchanged polling and reads never advance this clock. See the lifecycle document
for retained validation, bounded digest references and crash limits.

## Bounded public reads

Use `--json --bounded` with `status`, `runs list`, `runs show RUN_ID`,
`runs follow RUN_ID`, `trace show`, `plan show FINGERPRINT`, `package list`,
`package inspect PACKAGE_ID`, `workspace check`, `workspace identity`, or `doctor`.
`plan graph FINGERPRINT`, `plan overlay FINGERPRINT`, `operations history`, and
`daemon history` select the bounded contract directly. Version discovery names
the commands, option, contract revision and field/type declaration digest.
Existing legacy JSON commands retain their existing interfaces; opting into this
surface is explicit. Bounded package reads do not append package audit events.

Pages default to 50 records and allow 1–100, with a 128 KiB serialized response
bound and 16 KiB required-record bound. Oversized required records refuse rather
than truncate. Continue with the opaque `--cursor`; preserve the same command,
filters and scope. Mutable collections fence the durable source revision and
refuse changed snapshots. Static selected graph topology fences the immutable
selected fingerprint. Mutable registry association is a separately labeled
observation, so it cannot silently become static topology.

Graph pages include independent graph, node and stage identities and every
selected relationship family. Canonical typed edges bind fingerprint,
declaration kind/ID, role and typed source/target. Node IDs are never inferred
from stage IDs. Dynamic targets, missing endpoints, conditional references and
unsupported topology retain explicit availability. Overlay node counts come
from observed runtime state. An edge traversal count requires a matching retained
accepted action trace; declarations alone never produce a traversal count.

Run/session reads expose exact fences, hold revision, operation receipt/result
references, selected binding/profile qualification, actual budget binding,
completion/application evidence and explicit unavailable aftermath. Cancellation
phase, last attempted operation/result and both grace intervals retain their
existing meanings; successful cancellation mechanics do not qualify native effect
certainty. Doctor pages include retained lost/orphan/cleanup/refused-application
and dispatch diagnostics. Session reads expose completion diagnostic status/digest.
Diagnostics preserve code, severity, source, status, digest, policy and omission
metadata without private source text. Prompt, selector,
raw provider diagnostic and filesystem path content is not a public default.

## Lossy session history and nonmutating reads

Durable control history and runner telemetry are separate evidence. Session
history cursors bind store epoch, run, session, dispatch generation, session fence
and sequence. They never switch to a newer attempt. Missing/pruned/corrupt history
has an explicit gap or unavailable/corrupt result; it does not imply an empty
successful run. Fully pruned retained streams terminate finite paging with no
continuation and unchanged after-sequence, preserving the gap and restart guidance. Durable final session evidence remains separately available.

The runner sidecar retains WAL and its original bounded lossy retention, replay,
sequence, rate-limit and drop behavior. Writers invalidate the prior public
capture before committing changed events, then serialize a bounded committed
capture under the existing SQLite writer lock and atomically replace a private
snapshot file. The short capture/publication lock includes serialization and
file sync. Readers never acquire that lock or open WAL, initialize, checkpoint,
repair, or publish. Publication failure leaves committed telemetry intact and
removes the capture; ordinary later writer activity can publish a new capture.

A public capture validates canonical integrity, bounded rows and exact stream
identity. Its capture time and age are separate from the runtime SQLite revision.
Backing-file presence/header checks are separately reported. A valid historical
capture does not prove complete/current backing history or deeper backing-store
health. Missing or known-corrupt backing/capture is unavailable/corrupt; a header
check alone leaves deeper health unknown. No fallback silently ignores committed
WAL records. Telemetry payloads are omitted with digest/redaction provenance.

Missing or digest-corrupt required CAS evidence refuses the whole bounded read
with a safe missing/corrupt status. A retained completion diagnostic whose content
is malformed or mismatches its exact run/session authority is explicitly corrupt;
it is not silently optional or healthy. Current and retained historical session
diagnostics are checked against their own exact fences.

Daemon progress source capture is implemented at the accepted runtime transaction
boundary. Independent installed active/idle progress proof remains a separate
candidate qualification step; session lifecycle timestamps alone do not supply it.

The bounded read surface alone does not establish installed/released OS
integration, provider health or current native quiescence. Native qualification
requires the exact runtime/profile evidence described below.

## Exact native cooperative control

CORE-I01E adds `millforge-base.cooperative-local-io.v1` for a production-owned
Millforge invocation using stock local read/write and owned loopback HTTP.
Caller labels do not establish eligibility. See `millforge-runner.md` and the
native `docs/core-pause-profile.md` for the enforced boundary.

Native control uses a distinct durable sequence: accepted pending intent, owner
checkpoint wait outside SQL, verified parked quiescence, then applied settlement.
The original accepted receipt remains immutable. The current control and later
results distinguish pending, held, resumed, unknown, unqualified and retired.
Direct unstarted controls retain their earlier atomic history validation.

Bounded run/session projections expose the recorded exact profile, witness digest,
control revision, owner identity, descendant boundary and explicit aftermath.
These records are historical evidence. Every admission needs the current exact
live owner. A historical applied pause cannot prove that its continuation survived
owner death, resume, cancellation or finalization. Native retirement requires a
fresh fenced request with `mode=retire_native_continuation`, exact owner identity,
source revision, pause identifier and witness digest.

Accepted native intent survives caller loss. Slow model and stock tool effects
can remain pending beyond five seconds. Lifecycle stop uses a separate bounded
listener lane and retains its existing first-cancellation authority under native
admission and short database contention. Existing deadlines and budget counters
continue during pause. The profile does not authorize replacement attempts.

After legal release, unsupported ordinary effects remove native qualification
before entry. This does not manufacture a new hold over known ordinary completion.
Fresh recovery refuses unknown effects or descendants. A qualifying absent held
owner can be locally retired with cleanup `not_required`, leaving its old session
lost and nonrunnable. Daemon stale-socket cleanup, relaunch, complete workflow
recovery and released OS/provider integration remain separate gates.


Recovery refusal and loss observation use separate authority. The observation
names the actual accepted control action, which can be pause or resume. A fresh unsafe
retirement request first validates store, source revision, run/session fences,
control revision, profile, pause, owner and witness under the database writer lock.
A stale target cannot change the native control or record a lost session.

For a valid absent owner with uncertain effects, the original accepted control
records owner-loss aftermath. The new retirement request receives an immutable
refusal receipt. Its initial result links the original control's exact aftermath
result and explicitly names that separate authority. The CLI reports both facts,
rather than saying that no state changed. Same-key replay can finish the retained
lost-session recording after caller loss, preserving the refusal receipt. This
observation does not accept native retirement or permit a replacement attempt.

### Bounded native witness representation (CORE-B02)

A bounded run item emits the complete retained native snapshot once at
`quiescence_evidence.witness`. The optional duplicate
`execution_hold.native.snapshot` is replaced by `snapshot_reference` with
`scope="same_run_record"` and `json_pointer="/quiescence_evidence/witness"`.
Resolve that pointer relative to the run record, not the page envelope. Every
other `execution_hold` field retains its exact durable value. Substituting the
referenced witness for `snapshot_reference` reconstructs the full durable native
evidence; neither the kernel projection nor storage changes.

The full witness is historical evidence, not a fresh owner-liveness observation.
`native_control.witness_digest` still identifies the full durable native witness
under the existing digest algorithm. `execution_hold.native.digest` remains the
operation request digest, not a digest of the reference or snapshot alone.
Existing `native_control`, `effect_status`, session evidence, authority fences,
receipt/result references, budgets, cancellation and cleanup fields are retained.
The 16 KiB item and 128 KiB page gates still measure serialized bytes and refuse
unrepresentable required identities or topology.

Consumers of the optional nested snapshot must follow `snapshot_reference` or
use the existing `quiescence_evidence.witness` path. No current product-source
consumer uses the removed duplicate path. The schema now declares this retained
control/reference representation, changing its digest. Contract revision 1 is
retained for the first published form of these controls in v0.22.3; it does
not establish compatibility with unreleased snapshots. Native regression covers ordinary public-enqueue generated identities in
pending, held, resumed and completed states, exact witness reconstruction, and
unchanged state. Completion/application success remains separate from unqualified
native aftermath or unknown descendant evidence.
