from __future__ import annotations

from typing import Any

import pytest

from millrace.contracts.transition import TransitionContext
from millrace.testing import deterministic_context


def test_production_transition_context_requires_explicit_ids() -> None:
    context_factory: Any = TransitionContext
    with pytest.raises(TypeError):
        context_factory()


@pytest.mark.parametrize(
    "field_name",
    (
        "transition_id",
        "work_item_id",
        "activation_id",
        "run_id",
        "claim_id",
        "fencing_token",
    ),
)
def test_transition_context_rejects_blank_protocol_ids(field_name: str) -> None:
    values = {
        "transition_id": "transition-1",
        "work_item_id": "work-1",
        "activation_id": "activation-1",
        "run_id": "run-1",
        "claim_id": "claim-1",
        "fencing_token": "fence-1",
    }
    values[field_name] = " "

    with pytest.raises(ValueError, match=f"{field_name} must be non-blank"):
        TransitionContext(**values)


def test_deterministic_test_context_owns_placeholder_defaults() -> None:
    context = deterministic_context()

    assert context.transition_id == "transition-1"
    assert context.work_item_id == "work-1"
    assert context.activation_id == "activation-1"
    assert context.run_id == "run-1"
    assert context.claim_id == "claim-1"
    assert context.fencing_token == "fence-1"
