# GoalSpec Spec Interview

Use this stage only for the optional GoalSpec `spec_interview` checkpoint.

Goal:
- pressure-test one synthesized queue spec before Spec Review when the runtime asks for interview coverage

Operating rules:
- ask or materialize exactly one active question for the current spec
- prefer repo/codebase evidence over operator interruption whenever the answer is already available
- when the answer requires human judgment, stop after writing one durable pending question
- do not redesign the whole spec family or skip directly to task generation

Artifact family:
- pending questions: `agents/specs/questions/*.json`
- resolved decisions: `agents/specs/decisions/*.json`

Expected outcomes:
- repo-answerable question: resolve into a decision artifact and continue
- operator-needed question: leave one pending question and wait for answer/accept/skip
