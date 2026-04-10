# Model config

This file is the **only** place you should change model choices for Millrace cycles.
`agents/_orchestrate.md` reads these values and routes each cycle to the right runner.

The shipped values below are real packaged defaults for Codex/OpenAI execution, not setup-only placeholders. A fresh `millrace init` workspace starts with supported model ids such as `gpt-5.3-codex` and `gpt-5.2`, but actual execution still depends on local `codex` runner availability and auth.

## Active config (edit these KEY=value lines)

INTEGRATION_RUNNER=codex
INTEGRATION_MODEL=gpt-5.3-codex

BUILDER_RUNNER=codex
BUILDER_MODEL=gpt-5.3-codex

QA_RUNNER=codex
QA_MODEL=gpt-5.3-codex

HOTFIX_RUNNER=codex
HOTFIX_MODEL=gpt-5.3-codex

DOUBLECHECK_RUNNER=codex
DOUBLECHECK_MODEL=gpt-5.3-codex

# Troubleshoot + Consult escalation cycle keys.
# If omitted, Troubleshoot defaults to BUILDER_* and Consult defaults to TROUBLESHOOT_*.
TROUBLESHOOT_RUNNER=codex
TROUBLESHOOT_MODEL=gpt-5.3-codex
CONSULT_RUNNER=codex
CONSULT_MODEL=gpt-5.3-codex

# Update-on-empty cycle keys (used when RUN_UPDATE_ON_EMPTY=On in workflow config).
# Default is standard codex at medium reasoning effort (effort is set by loop).
UPDATE_RUNNER=codex
UPDATE_MODEL=gpt-5.3-codex

# Research-loop stage keys (used by agents/research_loop.sh).
# Runners must be one of: codex|claude.
#
# Model values may be a single model id or a codex fallback chain using `|`.
# Effort values may also use `|` to match chain position.
# Example: GOAL_INTAKE_MODEL=a|b and GOAL_INTAKE_EFFORT=high|low

GOAL_INTAKE_RUNNER=codex
GOAL_INTAKE_MODEL=gpt-5.3-codex
GOAL_INTAKE_EFFORT=high

OBJECTIVE_PROFILE_SYNC_RUNNER=codex
OBJECTIVE_PROFILE_SYNC_MODEL=gpt-5.3-codex
OBJECTIVE_PROFILE_SYNC_EFFORT=high

SPEC_SYNTHESIS_RUNNER=codex
SPEC_SYNTHESIS_MODEL=gpt-5.2
SPEC_SYNTHESIS_EFFORT=xhigh

SPEC_REVIEW_RUNNER=codex
SPEC_REVIEW_MODEL=gpt-5.3-codex
SPEC_REVIEW_EFFORT=high

TASKMASTER_RUNNER=codex
TASKMASTER_MODEL=gpt-5.3-codex
TASKMASTER_EFFORT=xhigh

TASKAUDIT_RUNNER=codex
TASKAUDIT_MODEL=gpt-5.3-codex
TASKAUDIT_EFFORT=medium

# Compatibility fallback keys for the pre-condense GoalSpec flow.
# Keep these only for transition safety and incident-side interrogation helpers.
ARTICULATE_RUNNER=codex
ARTICULATE_MODEL=gpt-5.3-codex
ARTICULATE_EFFORT=high

ANALYZE_RUNNER=codex
ANALYZE_MODEL=gpt-5.3-codex
ANALYZE_EFFORT=high

CLARIFY_RUNNER=codex
CLARIFY_MODEL=gpt-5.2
CLARIFY_EFFORT=xhigh

# GoalSpec interrogation chains (critic/designer) plus reserved
# research-stage chains for incident and later expansion.
# All keys are parsed with safe defaults.
CRITIC_RUNNER=codex
CRITIC_MODEL=gpt-5.3-codex
CRITIC_EFFORT=high

DESIGNER_RUNNER=codex
DESIGNER_MODEL=gpt-5.3-codex
DESIGNER_EFFORT=high

PHASESPLIT_RUNNER=codex
PHASESPLIT_MODEL=gpt-5.3-codex
PHASESPLIT_EFFORT=high

INCIDENT_INTAKE_RUNNER=codex
INCIDENT_INTAKE_MODEL=gpt-5.3-codex
INCIDENT_INTAKE_EFFORT=high

INCIDENT_RESOLVE_RUNNER=codex
INCIDENT_RESOLVE_MODEL=gpt-5.3-codex
INCIDENT_RESOLVE_EFFORT=high

INCIDENT_ARCHIVE_RUNNER=codex
INCIDENT_ARCHIVE_MODEL=gpt-5.3-codex
INCIDENT_ARCHIVE_EFFORT=medium

AUDIT_INTAKE_RUNNER=codex
AUDIT_INTAKE_MODEL=gpt-5.3-codex
AUDIT_INTAKE_EFFORT=high

AUDIT_VALIDATE_RUNNER=codex
AUDIT_VALIDATE_MODEL=gpt-5.3-codex
AUDIT_VALIDATE_EFFORT=high

AUDIT_GATEKEEPER_RUNNER=codex
AUDIT_GATEKEEPER_MODEL=gpt-5.3-codex
AUDIT_GATEKEEPER_EFFORT=high

# Secondary research fallback cycle (used when deterministic research remediation fails).
MECHANIC_RUNNER=codex
MECHANIC_MODEL=gpt-5.3-codex
MECHANIC_EFFORT=xhigh

# Optional complexity-routing keys (used only when workflow flag
# `COMPLEXITY_ROUTING=On` is set in agents/options/workflow_config.md).
# Defaults stay on standard codex.

MODERATE_BUILDER_MODEL_CHAIN=gpt-5.3-codex
MODERATE_HOTFIX_MODEL_CHAIN=gpt-5.3-codex

INVOLVED_BUILDER_MODEL_CHAIN=gpt-5.3-codex
INVOLVED_HOTFIX_MODEL_CHAIN=gpt-5.3-codex

COMPLEX_BUILDER_MODEL_CHAIN=gpt-5.3-codex
COMPLEX_HOTFIX_MODEL_CHAIN=gpt-5.3-codex

QA_MODERATE_MODEL=gpt-5.3-codex
QA_MODERATE_EFFORT=xhigh
QA_INVOLVED_MODEL=gpt-5.3-codex
QA_INVOLVED_EFFORT=xhigh
QA_COMPLEX_MODEL=gpt-5.3-codex
QA_COMPLEX_EFFORT=xhigh

DOUBLECHECK_MODERATE_MODEL=gpt-5.3-codex
DOUBLECHECK_MODERATE_EFFORT=medium
DOUBLECHECK_INVOLVED_MODEL=gpt-5.3-codex
DOUBLECHECK_INVOLVED_EFFORT=high
DOUBLECHECK_COMPLEX_MODEL=gpt-5.3-codex
DOUBLECHECK_COMPLEX_EFFORT=xhigh

---

## Presets (copy/paste over the Active config section)

### 1) Default (OpenAI models for all cycles)

INTEGRATION_RUNNER=codex
INTEGRATION_MODEL=gpt-5.3-codex

BUILDER_RUNNER=codex
BUILDER_MODEL=gpt-5.3-codex

QA_RUNNER=codex
QA_MODEL=gpt-5.3-codex

HOTFIX_RUNNER=codex
HOTFIX_MODEL=gpt-5.3-codex

DOUBLECHECK_RUNNER=codex
DOUBLECHECK_MODEL=gpt-5.3-codex

UPDATE_RUNNER=codex
UPDATE_MODEL=gpt-5.3-codex

GOAL_INTAKE_RUNNER=codex
GOAL_INTAKE_MODEL=gpt-5.3-codex
GOAL_INTAKE_EFFORT=high

OBJECTIVE_PROFILE_SYNC_RUNNER=codex
OBJECTIVE_PROFILE_SYNC_MODEL=gpt-5.3-codex
OBJECTIVE_PROFILE_SYNC_EFFORT=high

SPEC_SYNTHESIS_RUNNER=codex
SPEC_SYNTHESIS_MODEL=gpt-5.2
SPEC_SYNTHESIS_EFFORT=xhigh

SPEC_REVIEW_RUNNER=codex
SPEC_REVIEW_MODEL=gpt-5.3-codex
SPEC_REVIEW_EFFORT=high

TASKMASTER_RUNNER=codex
TASKMASTER_MODEL=gpt-5.3-codex
TASKMASTER_EFFORT=xhigh

TASKAUDIT_RUNNER=codex
TASKAUDIT_MODEL=gpt-5.3-codex
TASKAUDIT_EFFORT=medium

### 1.5) Hybrid (Codex Integration/Builder/Hotfix, Claude QA/Doublecheck)

INTEGRATION_RUNNER=codex
INTEGRATION_MODEL=gpt-5.3-codex

BUILDER_RUNNER=codex
BUILDER_MODEL=gpt-5.3-codex

QA_RUNNER=claude
QA_MODEL=sonnet

HOTFIX_RUNNER=codex
HOTFIX_MODEL=gpt-5.3-codex

DOUBLECHECK_RUNNER=claude
DOUBLECHECK_MODEL=sonnet

### 2) Hybrid Performance (Codex Integration/Builder/Hotfix, Claude QA/Doublecheck on Opus)

INTEGRATION_RUNNER=codex
INTEGRATION_MODEL=gpt-5.3-codex

BUILDER_RUNNER=codex
BUILDER_MODEL=gpt-5.3-codex

QA_RUNNER=claude
QA_MODEL=opus

HOTFIX_RUNNER=codex
HOTFIX_MODEL=gpt-5.3-codex

DOUBLECHECK_RUNNER=claude
DOUBLECHECK_MODEL=opus

### 3) All Codex

INTEGRATION_RUNNER=codex
INTEGRATION_MODEL=gpt-5.3-codex

BUILDER_RUNNER=codex
BUILDER_MODEL=gpt-5.3-codex

QA_RUNNER=codex
QA_MODEL=gpt-5.3-codex

HOTFIX_RUNNER=codex
HOTFIX_MODEL=gpt-5.3-codex

DOUBLECHECK_RUNNER=codex
DOUBLECHECK_MODEL=gpt-5.3-codex

### 3.5) All Codex Performance (Codex everywhere, higher-reasoning for QA/Doublecheck)

INTEGRATION_RUNNER=codex
INTEGRATION_MODEL=gpt-5.3-codex

BUILDER_RUNNER=codex
BUILDER_MODEL=gpt-5.3-codex

QA_RUNNER=codex
QA_MODEL=gpt-5.2

HOTFIX_RUNNER=codex
HOTFIX_MODEL=gpt-5.3-codex

DOUBLECHECK_RUNNER=codex
DOUBLECHECK_MODEL=gpt-5.2

### 4) All Claude

INTEGRATION_RUNNER=claude
INTEGRATION_MODEL=sonnet

BUILDER_RUNNER=claude
BUILDER_MODEL=sonnet

QA_RUNNER=claude
QA_MODEL=sonnet

HOTFIX_RUNNER=claude
HOTFIX_MODEL=sonnet

DOUBLECHECK_RUNNER=claude
DOUBLECHECK_MODEL=sonnet

### 4.5) All Claude Performance (Claude everywhere on Opus)

INTEGRATION_RUNNER=claude
INTEGRATION_MODEL=opus

BUILDER_RUNNER=claude
BUILDER_MODEL=opus

QA_RUNNER=claude
QA_MODEL=opus

HOTFIX_RUNNER=claude
HOTFIX_MODEL=opus

DOUBLECHECK_RUNNER=claude
DOUBLECHECK_MODEL=opus

### 5) Custom

- Set each `*_RUNNER` to `codex` or `claude`.
- Set each `*_MODEL` to:
  - Codex: a model id (example: `gpt-5.3-codex`)
  - Claude: a model alias/id (example: `sonnet`)
- Optional update-on-empty keys:
  - `UPDATE_RUNNER`, `UPDATE_MODEL`
- Optional escalation keys:
  - `TROUBLESHOOT_RUNNER`, `TROUBLESHOOT_MODEL` (fallback: `BUILDER_*`)
  - `CONSULT_RUNNER`, `CONSULT_MODEL` (fallback: `TROUBLESHOOT_*`)
- Optional (complexity routing mode):
  - Set `MODERATE_*_MODEL_CHAIN`, `INVOLVED_*_MODEL_CHAIN`, `COMPLEX_*_MODEL_CHAIN`
  - Set `QA_*_MODEL`, `QA_*_EFFORT`, `DOUBLECHECK_*_MODEL`, `DOUBLECHECK_*_EFFORT`
- Optional (research loop stages):
  - Active condensed GoalSpec stages: `GOAL_INTAKE_*`, `SPEC_SYNTHESIS_*`, `SPEC_REVIEW_*`, `TASKMASTER_*`, `TASKAUDIT_*`
  - Compatibility fallback keys: `ARTICULATE_*`, `ANALYZE_*`, `CLARIFY_*`
  - Optional reserved chains: `CRITIC_*`, `DESIGNER_*`, `PHASESPLIT_*`, `INCIDENT_*`, `AUDIT_*`
  - `*_MODEL` can be a fallback chain with `|`
  - `*_EFFORT` can be a parallel chain with `|`

---

## Known-good Codex model IDs

These are listed as recommended/alternative models in OpenAI's Codex Models docs:

- gpt-5.3-codex
- gpt-5.1-codex-max
- gpt-5.1-codex-mini
- gpt-5.2
- gpt-5.1
- gpt-5.1-codex
- gpt-5-codex
- gpt-5-codex-mini
- gpt-5

(Availability depends on your Codex authentication + plan.)

## Known-good Claude model aliases / IDs

Claude Code supports model **aliases** (e.g. `sonnet`, `opus`) and full model names.

- Alias: sonnet
- Alias: opus
- Alias: haiku
- Example full id: claude-sonnet-4-5-20250929

If a full id stops working, prefer using `sonnet`/`opus` aliases unless you need a pinned version.

## Sanity checks

- Codex: `codex -m <model> "say hi"` (or run a trivial `codex exec --model <model> ...`)
- Claude: `claude --model <model-or-alias> -p "say hi"`
