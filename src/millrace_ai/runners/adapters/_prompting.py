"""Shared Millrace-owned prompt construction for runner adapters."""

from __future__ import annotations

from millrace_ai.runners.requests import StageRunRequest, render_stage_request_context_lines


def legal_terminal_markers(request: StageRunRequest) -> tuple[str, ...]:
    return request.legal_terminal_markers


def build_stage_prompt(request: StageRunRequest) -> str:
    request_context = render_stage_request_context_lines(request)
    legal_markers = ", ".join(f"`{marker}`" for marker in legal_terminal_markers(request))
    return "\n".join(
        (
            "You are executing one Millrace runtime stage request.",
            f"Open `{request.entrypoint_path}` and follow instructions exactly.",
            "",
            "Stage Request Context:",
            *request_context,
            "",
            (
                "When done, print exactly one legal terminal marker defined by the opened "
                "entrypoint contract."
            ),
            f"Legal markers for this stage: {legal_markers}.",
            "Do not invent or rename terminal markers.",
            "Do not print multiple terminal markers.",
        )
    )


__all__ = ["build_stage_prompt", "legal_terminal_markers"]
