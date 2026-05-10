from __future__ import annotations

import pytest

from millrace_ai.recon_packets import parse_recon_packet


def _packet_text(*, decision: str, handoff_target: str, emitted_field: str) -> str:
    return f"""# Recon Packet recon-probe-001

Recon-Packet-ID: recon-probe-001
Probe-ID: probe-001
Decision: {decision}
Confidence: high
Risk-Level: medium
Request-Summary: Research before routing.
Interpreted-Goal: Route this work safely.
Handoff-Target: {handoff_target}
{emitted_field}
Created-At: 2026-04-28T12:00:00Z
Created-By: recon

Relevant-Paths:
- src/example.py | likely behavior owner

Semantic-Invariants:
- Preserve adjacent behavior.
"""


def test_to_planning_packet_rejects_emitted_task_id_with_actionable_message() -> None:
    with pytest.raises(ValueError, match="Emitted-Spec-ID"):
        parse_recon_packet(
            _packet_text(
                decision="to_planning",
                handoff_target="planning",
                emitted_field="Emitted-Task-ID: task-from-probe",
            )
        )


def test_to_execution_packet_rejects_emitted_spec_id_with_actionable_message() -> None:
    with pytest.raises(ValueError, match="Emitted-Task-ID"):
        parse_recon_packet(
            _packet_text(
                decision="to_execution",
                handoff_target="execution",
                emitted_field="Emitted-Spec-ID: spec-from-probe",
            )
        )

