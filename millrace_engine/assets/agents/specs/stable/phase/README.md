# Phase Stable Specs

This folder stores immutable phase specs derived from a golden parent:

- Path contract: `agents/specs/stable/phase/<spec_id>__phase-<nn>.md`
- Source template: `agents/specs/templates/phase_spec_template.md`
- Each file should expose a `PHASE_<nn>` key, `phase_priority: P0|P1|P2|P3`, and Req-ID coverage for the phase.
- Each file should include a structured decision log that follows `agents/specs/governance/decision_log_schema.json`.

Phase specs must keep assumptions and interrogation notes explicit so task generation is deterministic.
