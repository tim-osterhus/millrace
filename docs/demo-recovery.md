# Private v0.22.4 owned demo recovery

This recovery profile belongs to the coordinated, unpublished 0.22.4 candidate. The retained Pi dev6/dev5 artifacts are predecessor scripted-provider evidence and do not qualify this demo or the final candidate artifacts.


`demo_owned_runtime.py` supplies an internal write-through SQLite profile for
isolated, command-owned demo stores. It supports the isolated demo command and does not authorize arbitrary packages. The ordinary runtime SQLite and CAS APIs remain unchanged. Ordinary session
events retain their existing WAL and best-effort snapshot behavior.

The command must authenticate its creation receipt and separately retained
registry, acquire its exclusive foreground lock, and supply a callback that
revalidates those authorities at every consumer operation. The expected member
identities come from that authenticated receipt. A handled close retains the
updated identity inventory before releasing only its own lock. A crash,
ambiguous lock, mismatched inventory or incomplete durable write retains evidence
and blocks ordinary resume; age and PID are never deletion authority.

Core runs its existing schema, location, package, plan and session operations on
a SQLite memory connection. Completed transactions are serialized into the
actual owned database before the connection method returns. The implementation
writes a new private file relative to a pinned directory descriptor, fsyncs it,
rechecks the anchored members, atomically replaces the database in that same
directory and fsyncs the directory. The adapter never writes database rows or
artifacts. Core's existing CAS-before-SQLite ordering remains in force; owned
CAS writes also fsync their object and directory before returning.

Every read opens its file relative to no-follow directory descriptors and
validates uid, private mode, file type and retained identity. Database operations
also compare retained bytes before executing SQL. Consumers refuse stale data,
symlinks, directory/file replacement and unsafe modes. CAS retains Core digest
validation. Failed persistence poisons the connection; it cannot report a later
operation as successful. A temporary file before replacement or a mismatched
receipt after replacement requires retained-state inspection, not replay.
Atomic rename establishes an old-or-new database, not an atomic transaction
across the database and command receipt.

This bounded profile requires private process umask 0077, files at most 8 MiB,
rollback-journal headers for both owned SQLite databases, no journal/WAL/shm
files or unfinished `.owned-*`/`.runner-snapshot-*` publications in `.millrace`
or `.millrace/cas/sha256`,
and a single command owner. SQLite byte-range locking detects an active external
SQLite transaction during database capture; it does not discover idle external
connections, protect against hostile same-principal code or support ordinary
concurrent SQLite writers. An external connection in this private demo tree is
unsupported. There is no native-provider owner-death continuation, power-loss
hardware guarantee or new authentication infrastructure.

Recovery uses the admitted run's package/plan and current session generation and
fence. It does not use a newly selected default plan to replace active authority.
Unknown synthetic outcomes remain pending until the originally retained result
is made available; reconciliation cannot create another start or completion.
Cancellation remains a Core interrupted/cancelled outcome, not successful graph
completion. Whole-graph completion and cleanup require the existing owner-derived
wait, closure, session and quiescent-inventory checks.


## Session-event sidecars

The supported owned profile includes the actual
`runtime.sqlite3.runner-session-events.sqlite3` event database and its
`runtime.sqlite3.runner-session-events.sqlite3.public.json` snapshot. They keep
their existing names, schema, event writer/redaction/bounds and snapshot decoder.
`OpenRuntimeContext.open_session_event_store` preserves the ordinary WAL path;
only the private owned context supplies a write-through connection and anchored
snapshot publisher. These owned database bytes use rollback headers. A retained
WAL event database or WAL/journal/shm residue refuses; no WAL upgrade or telemetry
removal is performed.

Authoritative session persistence still happens before `_record_session_event`.
Events remain a best-effort projection, not evidence authorizing a completion or
recovery. The owned event connection commits bytes before snapshot publication.
An owned snapshot publication failure retains prior JSON and unfinished evidence,
poisons that owned context and does not delete arbitrary paths. A later command
must refuse unsettled identity/residue, while inspecting authoritative session
state separately. Normal non-demo lossy snapshot behavior remains unchanged.

Owned event initialization, each database operation, JSON publication and
`session_event_snapshot` read use the same directory descriptors, identity and
mode checks as the primary database and CAS. The snapshot decoder consumes
captured bytes; it does not reopen an unchecked path. Generic public history or
status tools using ordinary paths are not an owned-demo recovery admission path.
The demo coordinator uses its owned context to inspect these sidecars.

## v0.22.4 foreground command integration

The private production command uses the accepted descriptor-relative SQLite,
CAS and session-event write-through boundary for creation and recovery. Retained
result evidence precedes the adapter's successful return; a fresh process
reconciles that same session, generation and fence. Missing terminal evidence
remains unknown and does not authorize another start. The local operator owns
receipt/registry files and the foreground lock; hashes correlate bytes and do
not authenticate an external producer. Hard-death locks require operator review
outside this automatic resume profile. Cleanup verifies retained identities and
contents and retains evidence after incomplete removal. See demo.md for the
candidate installation limits and supported command.

## Setup receipt recovery

Setup evidence acquisition and runtime resume have different identities. Use
`millrace setup --resume-receipt consent:ID` for setup and `millrace demo --resume ID`
for a handled, incomplete owned demo. Unknown setup effects retain their result
receipt and cannot be automatically retried under a new consent. A completed
setup action does not prove that the demo ran.
