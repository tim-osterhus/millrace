from __future__ import annotations

from millrace_ai.extensions.builtin.blueprint.context import _artifact_contracts_for_request


def test_blueprint_context_rejects_compiled_plan_absent_artifact_contract_fallback() -> None:
    """Runtime context rendering should not use packaged assets as plan authority."""
    import pytest

    with pytest.raises(ValueError, match="compiled plan"):
        _artifact_contracts_for_request(
            None,
            request_compiled_plan_id="compiled-plan-missing",
        )
