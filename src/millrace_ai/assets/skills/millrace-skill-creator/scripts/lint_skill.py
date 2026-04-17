from __future__ import annotations

import argparse
from pathlib import Path

from _shared import ValidationIssue, validate_skill_package


def _render_issue(issue: ValidationIssue) -> str:
    return f"error: {issue.path}: {issue.message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint a local Millrace skill package.")
    parser.add_argument("package_root", nargs="?", type=Path, default=Path.cwd(), help="Package root to lint.")
    args = parser.parse_args(argv)

    issues = validate_skill_package(args.package_root)
    ok = not issues
    print(f"ok: {'true' if ok else 'false'}")
    for issue in issues:
        print(_render_issue(issue))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
