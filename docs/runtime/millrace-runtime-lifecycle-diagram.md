# Millrace Runtime Lifecycle Diagram

This is the dense, implementation-accurate lifecycle chart for the shipped
default runtime configuration:

- mode: `standard_plain`
- planning loop: `planning.standard`
- execution loop: `execution.standard`

The README embeds a simplified version. This file keeps the fuller chart that
tracks startup, scheduling, result application, recovery routing, and Arbiter
activation more faithfully.

```mermaid
flowchart TD
    subgraph startup["Startup and compile"]
        S1["Bootstrap workspace contract"]
        S2["Load runtime config"]
        S3["Acquire workspace lock"]
        S4["Build watcher session"]
        S5["Compile active mode and loops into a frozen plan"]
        S6["Load snapshot and recovery counters"]
        S7["Reconcile stale or impossible state"]
        S8["Persist running snapshot and startup events"]
        S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S7 --> S8
    end

    subgraph tick["Deterministic tick loop"]
        T1["Drain mailbox commands"]
        T2{"Config reload requested?"}
        T3["Rebuild watcher session and recompile frozen plan"]
        T4["Consume watcher events and normalize ideas inbox into queued specs"]
        T5["Refresh queue depths"]
        T6{"Stop requested?"}
        T7{"Paused?"}
        T8["Run reconciliation"]
        T9["Refresh queue depths again"]
        T10{"Active stage already set?"}
        T11{"Claim next work item<br/>incident -> spec -> task precedence"}
        T12{"Completion behavior eligible?<br/>No active stage, nothing claimable,<br/>completion behavior present, closure target ready,<br/>no remaining lineage work"}
        T13{"Active state invalid?"}
        T14{"Active stage now set?"}
        T15["Idle with no_work or paused outcome"]
        T16["Build stage request from active stage"]
        T17["Persist snapshot, status, counters, and events"]
        T18["Reset idle state, release lock, and stop runtime"]

        T1 --> T2
        T2 -- yes --> T3 --> T4
        T2 -- no --> T4
        T4 --> T5 --> T6
        T6 -- yes --> T18
        T6 -- no --> T7
        T7 -- yes --> T15 --> T17
        T7 -- no --> T8 --> T9 --> T10
        T10 -- yes --> T16
        T10 -- no --> T11
        T11 -- nothing claimable --> T12
        T12 -- no --> T13
        T13 -- yes --> A8
        T13 -- no --> T14
        T14 -- yes --> T16
        T14 -- no --> T15 --> T17
    end

    subgraph planning["Planning stages"]
        P1["auditor"]
        P2["planner"]
        P3["manager"]
        P4["mechanic"]
        PR["Runtime applies planning result:<br/>normalize, persist, route, update state"]
        PB{"Mechanic attempts left?"}
    end

    subgraph execution["Execution stages"]
        E1["builder"]
        E2["checker"]
        E3["fixer"]
        E4["doublechecker"]
        E5["troubleshooter"]
        E6["consultant"]
        E7["updater"]
        ER["Runtime applies execution result:<br/>normalize, persist, route, update state"]
        EF{"Fix budget left?"}
        ET{"Troubleshoot attempts left?"}
    end

    subgraph completion["Completion behavior"]
        C1["arbiter<br/>request_kind = closure_target"]
        CR["Runtime applies Arbiter result:<br/>persist verdict paths, update target state, route next action"]
    end

    S8 --> T1

    T11 -- planning incident --> A1["Set active stage: auditor"] --> T16
    T11 -- planning spec --> A2{"Claimed spec is root spec with lineage?"}
    A2 -- yes --> A3["Open closure target and snapshot canonical idea/root-spec contracts"] --> A4["Set active stage: planner"] --> T16
    A2 -- no --> A4
    T11 -- execution task --> A5["Set active stage: builder"] --> T16
    T12 -- yes --> A6["Activate completion behavior"] --> A7["Set active stage: arbiter closure target"] --> T16
    A8["Clear stale active state"] --> T14

    T16 --> DX{"Execute exactly one active stage"}

    DX --> P1
    DX --> P2
    DX --> P3
    DX --> P4
    DX --> E1
    DX --> E2
    DX --> E3
    DX --> E4
    DX --> E5
    DX --> E6
    DX --> E7
    DX --> C1

    P1 --> PR
    P2 --> PR
    P3 --> PR
    P4 --> PR

    PR -- auditor complete --> R1["Set active stage: planner"] --> T17
    PR -- planner complete --> R2["Set active stage: manager"] --> T17
    PR -- manager complete --> R3["Clear active stage and return to idle/claim boundary"] --> T17
    PR -- planner or manager blocked --> PB
    PR -- auditor blocked --> PB
    PR -- mechanic complete --> R4["Set active stage from metadata<br/>default: planner"] --> T17
    PR -- mechanic blocked --> PB
    PB -- yes --> R5["Set active stage: mechanic"] --> T17
    PB -- no --> R6["Persist blocked planning state and clear active stage"] --> T17

    E1 --> ER
    E2 --> ER
    E3 --> ER
    E4 --> ER
    E5 --> ER
    E6 --> ER
    E7 --> ER

    ER -- builder complete --> X1["Set active stage: checker"] --> T17
    ER -- checker pass --> X2["Set active stage: updater"] --> T17
    ER -- fixer complete --> X3["Set active stage: doublechecker"] --> T17
    ER -- doublecheck pass --> X4["Set active stage: updater"] --> T17
    ER -- updater complete --> X5["Clear active stage and return to idle/claim boundary"] --> T17
    ER -- checker fix needed --> EF
    ER -- doublechecker fix needed --> EF
    EF -- yes --> X6["Set active stage: fixer"] --> T17
    EF -- no --> ET
    ER -- builder or checker or fixer or doublechecker or updater blocked --> ET
    ER -- troubleshooter complete --> X7["Set active stage from metadata<br/>default: builder"] --> T17
    ER -- consultant complete --> X8["Set active stage from metadata<br/>default: troubleshooter"] --> T17
    ER -- consultant needs planning --> X9["Enqueue planning incident and clear active stage"] --> T17
    ER -- consultant blocked --> X10["Persist blocked execution state and clear active stage"] --> T17
    ER -- troubleshooter blocked --> ET
    ET -- yes --> X11["Set active stage: troubleshooter"] --> T17
    ET -- no --> X12["Set active stage: consultant"] --> T17

    C1 --> CR
    CR -- arbiter complete --> Y1["Close closure target, stamp closed_at, clear active stage"] --> T17
    CR -- remediation needed --> Y2["Keep target open, persist verdict, enqueue planning incident"] --> T17
    CR -- blocked --> Y3["Keep target open, persist verdict, leave blocked planning state"] --> T17

    T17 --> T1
```

Key invariants preserved by this chart:

- compile happens at startup and again only on explicit config reload
- planning and execution are separate claim domains inside one scheduler, not
  concurrent lanes
- the runtime applies stage results and mutates authoritative state after each
  execution; stages do not own queue mutation directly
- `manager`, `updater`, and successful Arbiter outcomes return the runtime to
  an idle or claim boundary for the next tick
- Arbiter is a completion-behavior activation path, not a normal queued work
  item handoff
