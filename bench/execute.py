"""Run SQL against a SQLite file safely, and compare results the BIRD way.

Two jobs:

1. `run_sql` executes one query and never crashes the benchmark. It opens the
   database READ-ONLY (a model could write `DROP TABLE` ...) and aborts any
   query that runs longer than a timeout (models sometimes write accidental
   cross joins that would run for hours).

2. `results_match` decides "right or wrong". We use BIRD's official
   execution-accuracy (EX) rule exactly: the predicted rows and the gold rows
   are compared as SETS of tuples. That means:
     - row order doesn't matter       (ORDER BY differences are forgiven)
     - duplicate rows don't matter    (DISTINCT differences are forgiven)
     - column order DOES matter       (each row is a tuple)
     - extra or missing columns are WRONG
   Matching the official metric keeps our accuracy comparable to published
   numbers. Don't "improve" it without noting that in the results.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExecResult:
    ok: bool                       # did the query finish without an error?
    rows: list[tuple] = field(default_factory=list)
    error: str | None = None       # error message when ok is False
    seconds: float = 0.0           # wall-clock execution time

    @property
    def timed_out(self) -> bool:
        return self.error == "timeout"


def run_sql(db_path: Path, sql: str, timeout_s: float = 30.0) -> ExecResult:
    """Execute `sql` on a read-only connection; return rows or the error."""
    # "file:...?mode=ro" with uri=True opens read-only: writes raise an error.
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    start = time.perf_counter()
    deadline = start + timeout_s
    conn = sqlite3.connect(uri, uri=True)
    try:
        # SQLite calls this handler every N virtual-machine steps. Returning a
        # non-zero value aborts the running query -- that's our timeout.
        conn.set_progress_handler(lambda: int(time.perf_counter() > deadline), 10_000)
        # BIRD text can contain bytes that aren't valid UTF-8; don't crash on them.
        conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
        rows = conn.execute(sql).fetchall()
        return ExecResult(ok=True, rows=rows, seconds=time.perf_counter() - start)
    except sqlite3.OperationalError as e:
        elapsed = time.perf_counter() - start
        # An aborted query surfaces as "interrupted".
        if "interrupted" in str(e) and elapsed >= timeout_s:
            return ExecResult(ok=False, error="timeout", seconds=elapsed)
        return ExecResult(ok=False, error=str(e), seconds=elapsed)
    except (sqlite3.Error, ValueError) as e:
        # ValueError: e.g. "You can only execute one statement at a time."
        return ExecResult(ok=False, error=str(e), seconds=time.perf_counter() - start)
    finally:
        conn.close()


def results_match(predicted: list[tuple], gold: list[tuple]) -> bool:
    """BIRD's strict EX metric: same set of row tuples."""
    return set(predicted) == set(gold)
