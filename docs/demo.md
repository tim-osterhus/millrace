# Private v0.22.4 zero-key demo candidate

The current source candidate targets a matched unpublished 0.22.4 Core, Plus,
and meta bundle. The published bundle remains v0.22.3. The demo identity is
fixed to Core and Plus 0.22.4 and to the selected Plus demo package bytes.
Monitor-retained Core 0.22.4.dev6 and Plus 0.22.4.dev5 artifacts are historical
scripted-provider Pi evidence, not this demo bundle. The prior S02 development
bundle is not the current recipe. Final 0.22.4 artifact qualification and
publication remain held.

The command is millrace demo with optional --keep, --auto-confirm, or
--resume ID. Put the global --json before demo. Default operation requires
interactive stdin. Auto-confirm records a distinctly simulated workflow-wait
decision; it does not accept onboarding trust. The demo uses no model keys, OS,
tmux, or Factory backend.

## Private candidate installation

Use a private wheelhouse supplied for the coordinated candidate. It must contain
the exact matched Core, Plus, and meta wheels at 0.22.4, Millforge 0.1.1, and
any required dependencies. Do not mix in the predecessor dev wheels.

    python3.12 -m venv .venv
    . .venv/bin/activate
    python -m pip install --no-index --find-links /absolute/path/to/wheelhouse "millrace==0.22.4"
    millrace setup --interactive --wheelhouse /absolute/path/to/wheelhouse
    millrace demo

This is a private candidate procedure, not a public PyPI install instruction.
The release owner must provide matched candidate artifacts; version numbers and
a successful build alone do not establish release qualification.

Setup asks separately before verifying and retaining the exact installed Core
and Plus wheels under the active environment's share/millrace/install-artifacts
directory, then before accepting the isolated synthetic demo disclosure. The
operator owns this evidence destination. Setup compares retained wheel members
to installed files; the digest correlates bytes but does not authenticate a
publisher. Decline or EOF creates no effect from that decision. The demo does
not acquire missing evidence, install packages, or make network requests.

With verified evidence already present, a text terminal can run millrace demo
directly. It displays the scoped roots, journal effects, synthetic runtime, and
cleanup behavior and asks for explicit acceptance inline. Current accepted
scope is reused without another prompt; changed installation, roots, or
disclosure require renewed acceptance. Corrupt receipts and uncertain setup
actions refuse rather than offering a bypass.

JSON and noninteractive calls never prompt or accept trust. First use the typed
setup consent flow, then run millrace --json demo --auto-confirm. The result
includes the actual trust_acceptance receipt with its selection and disclosure
digests. A missing or stale receipt refuses before workspace mutation.
Auto-confirm applies only to the later synthetic workflow wait.

This candidate does not complete the ordinary-install, one-command,
default-offline journey. The operator must supply the private candidate
wheelhouse and grant separate setup and demo consent. The previous S02 recipe
and predecessor Pi wheel pair do not identify the current matched 0.22.4 demo
candidate.

## Runtime and recovery

Runtime data belongs under ~/Library/Application Support/Millrace/demo. Resume
takes a canonical UUID, never a path, and requires the same installed candidate,
receipt, admitted package and plan, and owned runtime. Handled interruption
retains data. Ambiguous locks are refused; their age or PID never authorizes
removal.

The demo records seven sessions for confirm, or nine for one revise followed by
closure. --keep retains a completed workspace; completed IDs cannot resume.
Ctrl-C is observed at durable boundaries and retains an incomplete owned ID.
SIGTERM requests cancellation of an active synthetic session at its next safe
poll, or records a cancelled command before dispatch or at its operator wait.
A cancelled wait remains unresolved; cancellation is not successful closure.
No automatically expired lock, guessed completion, or restarted stage
establishes recovery. Resume never replaces an active run's admitted plan with
the current default plan.

See [setup](setup.md) for the inspection allowlist, exact evidence checks, and
receipt recovery, and [demo recovery](demo-recovery.md) for the owned runtime
persistence boundary.
