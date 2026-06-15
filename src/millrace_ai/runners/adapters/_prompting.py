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
    shared_instruction_lines = _shared_instruction_open_order_lines(request)
    return "\n".join(
        (
            "You are executing one Millrace runtime stage request.",
            "Open these files in this order:",
            *shared_instruction_lines,
            f"{len(shared_instruction_lines) + 1}. `{request.entrypoint_path}`",
            f"{len(shared_instruction_lines) + 2}. Required stage-core skills listed in this request",
            f"{len(shared_instruction_lines) + 3}. Request context artifacts listed in this request",
            "",
            "If instructions conflict, apply this precedence:",
            "1. active user/operator instruction attached to this run",
            "2. runtime request contract and compiled plan authority",
            "3. capability grants, approval gates, and safety constraints",
            "4. stage entrypoint",
            "5. required stage-core skill",
            "6. shared instructions listed in this request",
            "7. workspace-map/wiki/ and other advisory docs",
            "",
            "Stage Request Context:",
            *request_context,
            *rendered_request_context,
            "",
            ("When done, print exactly one legal terminal marker defined by the opened entrypoint contract."),
            f"Legal markers for this stage: {legal_markers}.",
            "Do not invent or rename terminal markers.",
            "Do not print multiple terminal markers.",
            (
                "The final response must be exactly one marker on its own line, with no "
                "surrounding backticks, label, prefix, suffix, or explanation."
            ),
        )
    )


def _shared_instruction_open_order_lines(request: StageRunRequest) -> tuple[str, ...]:
    return tuple(f"{index}. `{path}`" for index, path in enumerate(request.shared_instruction_paths, start=1))


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
