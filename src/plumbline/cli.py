"""Command-line entry point: the `plumbline` command.

    plumbline profile <db.sqlite> [-o catalog.json]    (M1: build the data catalog)
    plumbline check <db> <query>                        (M2: coming next)

We use argparse (standard library) rather than click/typer to keep the
dependency list short. Each subcommand is one `sub.add_parser(...)` plus a
small function that does the work and returns an exit code.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from plumbline import __version__
from plumbline.profiler import MAX_VALUES, profile_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plumbline",
        description="Catch AI-written SQL that runs fine but returns the wrong answer.",
    )
    parser.add_argument("--version", action="version", version=f"plumbline {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser(
        "profile",
        help="scan a SQLite database once and save its catalog (column statistics)",
    )
    p.add_argument("database", type=Path, help="path to a .sqlite file")
    p.add_argument(
        "-o",
        "--out",
        type=Path,
        help="where to write the catalog (default: <database name>.catalog.json here)",
    )
    p.add_argument(
        "--max-values",
        type=int,
        default=MAX_VALUES,
        help=f"store every value of columns with at most this many distinct values "
        f"(default {MAX_VALUES})",
    )
    p.set_defaults(func=cmd_profile)
    return parser


def cmd_profile(args: argparse.Namespace) -> int:
    if not args.database.exists():
        print(f"error: no such file: {args.database}")
        return 2
    catalog = profile_database(args.database, max_values=args.max_values)
    out = args.out or Path(f"{args.database.stem}.catalog.json")
    catalog.save(out)
    n_cols = sum(len(t.columns) for t in catalog.tables)
    n_rows = sum(t.row_count for t in catalog.tables)
    n_lists = sum(c.values_complete and bool(c.values) for t in catalog.tables for c in t.columns)
    print(
        f"Profiled {catalog.database}: {len(catalog.tables)} tables, {n_cols} columns, "
        f"{n_rows:,} rows in {catalog.elapsed_s:.1f}s\n"
        f"  {n_lists} columns have a complete value list\n"
        f"  catalog -> {out}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code (0 = success)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
