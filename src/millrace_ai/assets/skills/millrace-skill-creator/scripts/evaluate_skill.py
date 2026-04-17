from __future__ import annotations

import argparse
from pathlib import Path

from _shared import ValidationIssue, evaluate_skill_package


def _render_issue(issue: ValidationIssue) -> str:
    return f"error: {issue.path}: {issue.message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a local Millrace skill package against fixture files.")
    parser.add_argument("package_root", nargs="?", type=Path, default=Path.cwd(), help="Package root to evaluate.")
    parser.add_argument(
        "--fixtures",
        action="append",
        type=Path,
        default=[],
        help="Fixture file or directory. Repeat to evaluate multiple sources.",
    )
    parser.add_argument("--case-id", help="Run only the named fixture case.")
    args = parser.parse_args(argv)

    try:
        fixture_paths = tuple(args.fixtures) if args.fixtures else None
        case_results = evaluate_skill_package(
            args.package_root,
            fixture_paths=fixture_paths,
            case_id=args.case_id,
        )
    except ValueError as exc:
        parser.error(str(exc))

    ok = all(not issues for _, issues in case_results)
    print(f"ok: {'true' if ok else 'false'}")
    for case_id, issues in case_results:
        status = "PASS" if not issues else "FAIL"
        print(f"case: {case_id} {status}")
        for issue in issues:
            print(f"  {_render_issue(issue)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
