"""Shared Millrace-owned prompt construction for runner adapters."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.runners.requests import StageRunRequest, render_stage_request_context_lines


def legal_terminal_markers(request: StageRunRequest) -> tuple[str, ...]:
    return request.legal_terminal_markers


def build_stage_prompt(request: StageRunRequest) -> str:
    request_context = render_stage_request_context_lines(request)
    legal_markers = ", ".join(f"`{marker}`" for marker in legal_terminal_markers(request))
    rendered_request_context = _rendered_request_context_text(request)
    return "\n".join(
        (
            "You are executing one Millrace runtime stage request.",
            f"Open `{request.entrypoint_path}` and follow instructions exactly.",
            "",
            "Stage Request Context:",
            *request_context,
            *rendered_request_context,
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


def _rendered_request_context_text(request: StageRunRequest) -> tuple[str, ...]:
    if request.rendered_prompt_context_path is None:
        return ()
    path = Path(request.rendered_prompt_context_path)
    text = path.read_text(encoding="utf-8")
    return (
        "",
        "Rendered Request Context:",
        text.rstrip(),
    )


__all__ = ["build_stage_prompt", "legal_terminal_markers"]
