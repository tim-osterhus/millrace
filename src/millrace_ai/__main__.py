"""Module entrypoint for `python -m millrace_ai`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
