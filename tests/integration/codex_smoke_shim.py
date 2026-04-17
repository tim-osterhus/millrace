"""Deterministic Codex-compatible smoke runner for release and integration tests."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _terminal_token_for_prompt(prompt: str) -> str:
    normalized = prompt.lower()
    if "stage: checker" in normalized:
        return "CHECKER_PASS"
    if "stage: updater" in normalized:
        return "UPDATE_COMPLETE"
    return "BUILDER_COMPLETE"


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-c", dest="config", action="append", default=[])
    parser.add_argument("--profile")
    parser.add_argument("--skip-git-repo-check", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--full-auto", action="store_true")
    parser.add_argument("--dangerously-bypass-approvals-and-sandbox", action="store_true")
    parser.add_argument("--sandbox")
    parser.add_argument("--cd")
    parser.add_argument("--output-last-message", required=True)
    parser.add_argument("prompt")
    args, _unknown = parser.parse_known_args()

    if args.cd:
        os.chdir(args.cd)

    token = _terminal_token_for_prompt(args.prompt)
    output = f"### {token}\n"
    Path(args.output_last_message).write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
