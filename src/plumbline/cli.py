"""Command-line entry point: the `plumbline` command.

    plumbline profile <db.sqlite> [-o catalog.json]    (M1: build the data catalog)
    plumbline check <db.sqlite | catalog.json> "<SQL>" [--json]   (M2: run the checks)

`check` exit codes: 0 = passed, 1 = an error-level finding, 2 = bad input
(missing file, unparseable SQL). So it drops straight into CI or a shell script.

We use argparse (standard library) rather than click/typer to keep the
dependency list short. Each subcommand is one `sub.add_parser(...)` plus a
small function that does the work and returns an exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from plumbline import __version__
from plumbline.catalog import Catalog
from plumbline.check import check_sql
from plumbline.findings import CheckResult
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

    c = sub.add_parser("check", help="check one SQL query against a database's catalog")
    c.add_argument(
        "target",
        type=Path,
        help="a catalog .json, or a .sqlite file (its catalog is built and cached on first use)",
    )
    c.add_argument("sql", help='the query, "-" to read it from stdin, or @file.sql')
    c.add_argument("--json", action="store_true", help="print the full result as JSON")
    c.set_defaults(func=cmd_check)
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


def _load_catalog(target: Path) -> Catalog:
    """A .json is used as is; a .sqlite gets a cached catalog next to it."""
    if target.suffix.lower() == ".json":
        return Catalog.load(target)
    cached = target.with_name(f"{target.stem}.catalog.json")
    if cached.exists():
        cat = Catalog.load(cached)
        if not cat.is_stale():
            return cat
    print(f"(profiling {target.name} once; catalog cached at {cached})", file=sys.stderr)
    cat = profile_database(target)
    cat.save(cached)
    return cat


def _read_sql(arg: str) -> str:
    if arg == "-":
        return sys.stdin.read()
    if arg.startswith("@"):
        return Path(arg[1:]).read_text(encoding="utf-8")
    return arg


def _print_result(r: CheckResult) -> None:
    if r.parse_error:
        print(f"PARSE ERROR: {r.parse_error}")
        return
    print("PASS" if r.passed else "FAIL", f"({len(r.errors)} errors, {len(r.warnings)} warnings)")
    for f in r.findings:
        print(f"\n  [{f.severity}] {f.rule}: {f.message}")
        if f.snippet:
            print(f"    in:   {f.snippet}")
        if f.hint:
            print(f"    hint: {f.hint}")
    for n in r.notes:
        print(f"\n  note: {n}")


def cmd_check(args: argparse.Namespace) -> int:
    if not args.target.exists():
        print(f"error: no such file: {args.target}")
        return 2
    result = check_sql(_read_sql(args.sql), _load_catalog(args.target))
    if args.json:
        print(json.dumps(result.to_dict(), indent=1, ensure_ascii=False, default=str))
    else:
        _print_result(result)
    if result.parse_error:
        return 2
    return 0 if result.passed else 1


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
