"""M1 acceptance check: profile all 11 BIRD databases, time it, and hand-check the numbers.

    python -m bench.profile_all

Does three things, matching the blueprint's "how you verify it" for M1:

1. Profiles every BIRD database into data/catalogs/<db_id>.json (gitignored: the catalogs
   contain the data's values, and BIRD's data is not ours to redistribute).
2. Records how long each one took ("profiling all 11 databases finishes in minutes").
3. Picks 10 columns at random (fixed seed, so it's repeatable) and recomputes their
   statistics with *different* SQL written independently of the profiler, then compares
   ("hand-check 10 columns against direct SQL counts"). The check SQL is deliberately
   phrased differently (SUM(x IS NULL), a DISTINCT subquery, ...) so a bug in one
   phrasing can't hide by appearing in both.

Writes results/<date>_profile_timing.json (committed) and exits 1 if any check fails.
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
import time
from datetime import date

from bench import config
from bench.dataset import _index_databases
from plumbline.profiler import profile_database, quote

CATALOGS_DIR = config.REPO_ROOT / "data" / "catalogs"
N_CHECKS = 10
SEED = 7


def independent_stats(path, table: str, column: str) -> dict:
    """Recompute a column's statistics with SQL that does not share code with the profiler."""
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    t, c = quote(table), quote(column)
    try:
        rows, nulls = conn.execute(f"SELECT COUNT(*), SUM({c} IS NULL) FROM {t}").fetchone()
        (distinct,) = conn.execute(
            f"SELECT COUNT(*) FROM (SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL)"
        ).fetchone()
        (num_max,) = conn.execute(
            f"SELECT {c} FROM {t} WHERE typeof({c}) IN ('integer','real') "
            f"ORDER BY {c} DESC LIMIT 1"
        ).fetchone() or (None,)
    finally:
        conn.close()
    return {"rows": rows, "nulls": nulls or 0, "distinct": distinct, "num_max": num_max}


def value_count(path, table: str, column: str, value) -> int:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        (n,) = conn.execute(
            f"SELECT COUNT(*) FROM {quote(table)} WHERE {quote(column)} = ?", (value,)
        ).fetchone()
    finally:
        conn.close()
    return n


def main() -> int:
    dbs = dict(sorted(_index_databases(config.DATABASES_DIR).items()))
    if not dbs:
        print(f"No databases under {config.DATABASES_DIR}. See README 'Get the data'.")
        return 2

    # 1 + 2: profile and time.
    timings, catalogs = {}, {}
    total_start = time.perf_counter()
    for db_id, path in dbs.items():
        cat = profile_database(path)
        cat.save(CATALOGS_DIR / f"{db_id}.json")
        catalogs[db_id] = (cat, path)
        n_cols = sum(len(t.columns) for t in cat.tables)
        n_rows = sum(t.row_count for t in cat.tables)
        size_kb = (CATALOGS_DIR / f"{db_id}.json").stat().st_size // 1024
        timings[db_id] = {
            "seconds": cat.elapsed_s,
            "tables": len(cat.tables),
            "columns": n_cols,
            "rows": n_rows,
            "db_mb": round(path.stat().st_size / 2**20, 1),
            "catalog_kb": size_kb,
        }
        print(
            f"{db_id:26s} {cat.elapsed_s:7.1f}s  {len(cat.tables):3d} tables "
            f"{n_cols:4d} cols {n_rows:>10,} rows  -> {size_kb:,} KB"
        )
    total = round(time.perf_counter() - total_start, 1)
    print(f"{'TOTAL':26s} {total:7.1f}s")

    # 3: hand-check 10 random columns.
    rng = random.Random(SEED)
    every_column = [
        (db_id, t.name, c) for db_id, (cat, _) in catalogs.items() for t in cat.tables
        for c in t.columns
    ]
    picks = rng.sample(every_column, N_CHECKS)
    checks, failures = [], 0
    print("\nHand-check: profiler vs independent SQL")
    for db_id, table, col in picks:
        path = catalogs[db_id][1]
        ref = independent_stats(path, table, col.name)
        mine = {
            "rows": col.row_count,
            "nulls": col.null_count,
            "distinct": col.distinct_count,
            "num_max": col.num_max,
        }
        # And the most common stored value's count, if the profiler kept one.
        if col.values and col.values[0][0] is not None:
            v, n = col.values[0]
            mine["top_value_count"] = n
            ref["top_value_count"] = value_count(path, table, col.name, v)
        ok = mine == ref
        failures += not ok
        checks.append({"db": db_id, "table": table, "column": col.name, "ok": ok,
                       "profiler": mine, "direct_sql": ref})
        print(f"  {'PASS' if ok else 'FAIL'}  {db_id}.{table}.{col.name}  {mine}"
              + ("" if ok else f"\n        direct SQL says {ref}"))

    out = config.RESULTS_DIR / f"{date.today().isoformat()}_profile_timing.json"
    out.write_text(json.dumps({"total_seconds": total, "databases": timings,
                               "hand_check": checks}, indent=1), encoding="utf-8")
    print(f"\n{N_CHECKS - failures}/{N_CHECKS} columns match. Results -> {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
