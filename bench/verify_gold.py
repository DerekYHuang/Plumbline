"""M0 step 1: check that all 500 supplied gold queries actually run.

Why bother? Every later number assumes the gold answers are trustworthy. If a
gold query errors, times out or returns nothing on YOUR copy of the databases,
you want to know now -- not after you've blamed the model for "missing" it.

The dataset's manifest says all 500 SQLite gold queries execute with no errors
and non-empty output, so anything else here means your data setup is off
(wrong zip, partial unzip, wrong folder).

Usage (from the repo root):

    python -m bench.verify_gold
    python -m bench.verify_gold --limit 20      # quick smoke test
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

from bench import config
from bench.dataset import find_database, load_questions
from bench.execute import run_sql


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="only check the first N questions")
    args = parser.parse_args(argv)

    questions = load_questions()[: args.limit]
    failures, empties, timings = [], [], []
    start = time.perf_counter()

    for q in questions:
        res = run_sql(find_database(q.db_id), q.gold_sql, config.SQL_TIMEOUT_S)
        timings.append(res.seconds)
        if not res.ok:
            failures.append((q.question_id, q.db_id, res.error))
        elif not res.rows:
            empties.append((q.question_id, q.db_id))

    total = time.perf_counter() - start
    print(f"Checked {len(questions)} gold queries in {total:.1f}s "
          f"(median {statistics.median(timings) * 1000:.0f} ms, "
          f"max {max(timings):.2f} s)")
    print(f"  errors/timeouts: {len(failures)}")
    for qid, db, err in failures:
        print(f"    q{qid} [{db}]: {err}")
    print(f"  empty results:   {len(empties)}")
    for qid, db in empties:
        print(f"    q{qid} [{db}]")

    ok = not failures and not empties
    print("PASS: all gold queries run and return rows." if ok else "FAIL: see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
