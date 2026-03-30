"""Module entrypoint for `python -m millrace_engine.tui`."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the Millrace Textual operator shell.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("millrace.toml"),
        help="Path to the Millrace config file. Defaults to ./millrace.toml.",
    )
    return parser


def resolve_config_path(config_path: Path) -> Path:
    resolved = config_path.expanduser()
    if not resolved.is_absolute():
        resolved = (Path.cwd() / resolved).resolve()
    if not resolved.exists():
        raise SystemExit(f"Config file not found: {resolved}")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    resolved_config = resolve_config_path(args.config)

    from .app import MillraceTUIApplication

    app = MillraceTUIApplication.from_config_path(resolved_config)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

