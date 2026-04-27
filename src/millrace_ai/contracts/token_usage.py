"""Token usage accounting contracts."""

from __future__ import annotations

from pydantic import model_validator

from .base import ContractModel


class TokenUsage(ContractModel):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0

    @model_validator(mode="after")
    def validate_non_negative_values(self) -> "TokenUsage":
        for field_name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "thinking_tokens",
            "total_tokens",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")
        return self


__all__ = ["TokenUsage"]
