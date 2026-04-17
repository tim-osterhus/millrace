from __future__ import annotations

import argparse
from pathlib import Path

from _shared import (
    MILLRACE_OPINIONATED_PROFILE,
    PORTABLE_PROFILE,
    SkillPackageSpec,
    normalize_asset_id,
    scaffold_skill_package,
    title_from_asset_id,
)

INCLUDE_CHOICES = ("references", "scripts", "evals")


def _build_spec(args: argparse.Namespace) -> SkillPackageSpec:
    if args.profile == PORTABLE_PROFILE:
        if any(
            value is not None
            for value in (args.asset_id, args.description, args.capability_type)
        ) or args.recommended_stage or args.forbidden_claim:
            raise ValueError("package-specific metadata requires --profile millrace-opinionated")
        inferred_title = args.title or title_from_asset_id(normalize_asset_id(args.destination.name))
        return SkillPackageSpec(profile=PORTABLE_PROFILE, title=inferred_title)

    if not args.asset_id or not args.description or not args.capability_type:
        raise ValueError("opinionated profile requires --asset-id, --description, and --capability-type")
    if not args.forbidden_claim:
        raise ValueError("opinionated profile requires at least one --forbidden-claim")

    inferred_title = args.title or title_from_asset_id(args.asset_id)
    return SkillPackageSpec(
        profile=MILLRACE_OPINIONATED_PROFILE,
        asset_id=args.asset_id,
        description=args.description,
        capability_type=args.capability_type,
        recommended_stages=tuple(args.recommended_stage),
        forbidden_claims=tuple(args.forbidden_claim),
        title=inferred_title,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold a Millrace skill package.")
    parser.add_argument("destination", type=Path, help="Destination directory for the skill package.")
    parser.add_argument(
        "--profile",
        choices=(PORTABLE_PROFILE, MILLRACE_OPINIONATED_PROFILE),
        default=PORTABLE_PROFILE,
        help="Select the package profile to scaffold.",
    )
    parser.add_argument("--title", help="Optional package title override.")
    parser.add_argument("--asset-id", help="Required for millrace-opinionated packages.")
    parser.add_argument("--description", help="Required for millrace-opinionated packages.")
    parser.add_argument("--capability-type", help="Required for millrace-opinionated packages.")
    parser.add_argument(
        "--recommended-stage",
        action="append",
        default=[],
        help="Optional stage name to include in recommended_for_stages.",
    )
    parser.add_argument(
        "--forbidden-claim",
        action="append",
        default=[],
        help="Required claim to include in forbidden_claims for opinionated packages.",
    )
    parser.add_argument(
        "--include",
        action="append",
        choices=INCLUDE_CHOICES,
        default=[],
        help="Optional support directory to include. Repeat to include multiple directories.",
    )
    args = parser.parse_args(argv)

    try:
        spec = _build_spec(args)
        scaffold_skill_package(args.destination, spec, include=tuple(args.include))
    except ValueError as exc:
        parser.error(str(exc))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
