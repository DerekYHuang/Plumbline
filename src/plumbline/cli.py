"""Command-line entry point: the `plumbline` command.

Right now it only reports the version. The real subcommands from the blueprint
arrive with their milestones:

    plumbline profile <db>          (M1: build the data catalog)
    plumbline check <db> <query>    (M2: run the rules on one query)

We use argparse (standard library) rather than click/typer to keep the
dependency list short. Subcommands get added with `sub.add_parser(...)`.
"""

from __future__ import annotations

import argparse

from plumbline import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plumbline",
        description="Catch AI-written SQL that runs fine but returns the wrong answer.",
    )
    parser.add_argument("--version", action="version", version=f"plumbline {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code (0 = success)."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
