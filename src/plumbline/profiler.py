"""The profiler: scan a SQLite database once and record what each column really holds.

    from plumbline.profiler import profile_database
    catalog = profile_database("california_schools.sqlite")
    catalog.save("california_schools.catalog.json")

Why profile at all? Because the checks need to know the *data*, not just the schema.
The schema says FundingType is TEXT. Only the data says its values are
'Directly funded', 'Locally funded' and 'Not in CS funding model' -- which is what lets
M2 flag a query filtering on 'Locally Funded' (capital F).

How it works, per column (all plain SQL, one full pass per statistic):

    1. COUNT(col), COUNT(DISTINCT col)        -> nulls, distinct values, uniqueness
    2. typeof(col) ... GROUP BY               -> what is actually stored (int/text/...)
    3. MIN/MAX over numbers, MIN/MAX over text -> ranges (dates are text in SQLite)
    4. value list                             -> ALL values if there are few of them,
                                                 otherwise only the most common
    5. shapes of a sample of text values      -> formats such as "9{4}-9{2}-9{2}"

Engine choice: Python's built-in sqlite3, not DuckDB. The blueprint allows either; sqlite3
adds no dependency and every statistic here is a single SQL query, so speed is fine for
BIRD-sized databases (measured: see docs/milestones/m1-review.md).

Safety: the database is opened read-only, so profiling can never change it.
"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from plumbline.catalog import Catalog, ColumnProfile, TableProfile

# A column with at most this many distinct values gets its COMPLETE value list stored.
# Above it, only the TOP_VALUES most common values are kept (for repair hints).
# 200 covers category-like columns (types, statuses, names of a few hundred things)
# while keeping a catalog file to a few MB even for wide databases.
MAX_VALUES = 200
TOP_VALUES = 20
# How many distinct text values to sample when working out a column's shapes.
SHAPE_SAMPLE = 1000
# Keep this many most common shapes per column.
TOP_SHAPES = 5
# Values longer than this are never stored in a value list (long free text is
# useless for matching and would bloat the catalog).
MAX_VALUE_CHARS = 200


def quote(name: str) -> str:
    """Quote an identifier for SQLite. BIRD has columns like `Free Meal Count (K-12)`."""
    return '"' + name.replace('"', '""') + '"'


def value_shape(value: str) -> str:
    """Describe a text value's format: digits -> 9, upper -> A, lower -> a, rest kept.

    Runs of the same class are compressed with a count, so lengths are visible:
        "01611920113902" -> "9{14}"
        "1996-01-05"     -> "9{4}-9{2}-9{2}"
        "TR010"          -> "A{2}9{3}"
        "Locally funded" -> "Aa{6} a{6}"
    """
    out: list[str] = []
    prev, run = "", 0

    def flush() -> None:
        if run:
            out.append(prev if run == 1 else f"{prev}{{{run}}}")

    for ch in value:
        if ch.isdigit():
            cls = "9"
        elif ch.isalpha():
            cls = "A" if ch.isupper() else "a"
        else:
            cls = ch
        if cls == prev:
            run += 1
        else:
            flush()
            prev, run = cls, 1
    flush()
    return "".join(out)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    # mode=ro: SQLite refuses every write. as_posix() keeps the URI valid on Windows.
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _json_safe(value):
    """Catalog values must be JSON: drop BLOBs, keep numbers and text."""
    if isinstance(value, bytes):
        return None
    return value


def profile_column(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    declared_type: str,
    row_count: int,
    is_pk: bool,
    max_values: int = MAX_VALUES,
) -> ColumnProfile:
    t, c = quote(table), quote(name)
    cur = conn.cursor()

    # 1. Nulls, distinct values, uniqueness.
    non_null, distinct = cur.execute(f"SELECT COUNT({c}), COUNT(DISTINCT {c}) FROM {t}").fetchone()
    null_count = row_count - non_null
    is_unique = row_count > 0 and null_count == 0 and distinct == row_count

    # 2. What is really stored. typeof() returns null/integer/real/text/blob.
    storage = {
        kind: n
        for kind, n in cur.execute(f"SELECT typeof({c}), COUNT(*) FROM {t} GROUP BY 1")
        if kind != "null"
    }

    # 3. Ranges, numbers and text separately (SQLite sorts every number before every
    #    string, so a single MIN/MAX over a mixed column would be meaningless).
    num_min = num_max = None
    if storage.get("integer") or storage.get("real"):
        num_min, num_max = cur.execute(
            f"SELECT MIN({c}), MAX({c}) FROM {t} WHERE typeof({c}) IN ('integer', 'real')"
        ).fetchone()
    text_min = text_max = len_min = len_max = None
    if storage.get("text"):
        text_min, text_max, len_min, len_max = cur.execute(
            f"SELECT MIN({c}), MAX({c}), MIN(LENGTH({c})), MAX(LENGTH({c})) "
            f"FROM {t} WHERE typeof({c}) = 'text'"
        ).fetchone()

    # 4. Value list: complete if few distinct values, else just the most common.
    values: list[list] = []
    complete = distinct <= max_values
    if distinct and not storage.get("blob"):
        limit = "" if complete else f"LIMIT {TOP_VALUES}"
        rows = cur.execute(
            f"SELECT {c}, COUNT(*) FROM {t} WHERE {c} IS NOT NULL "
            f"GROUP BY {c} ORDER BY COUNT(*) DESC, {c} {limit}"
        ).fetchall()
        values = [[_json_safe(v), n] for v, n in rows]
        if any(isinstance(v, str) and len(v) > MAX_VALUE_CHARS for v, _ in values):
            # Free text (descriptions, comments): a value list is no use, drop it.
            values, complete = [], False
    else:
        complete = distinct == 0  # an all-NULL column has a complete, empty list

    # 5. Shapes of text values, from a sample of distinct values.
    shapes: list[list] = []
    if storage.get("text"):
        sample = [
            v
            for (v,) in cur.execute(
                f"SELECT DISTINCT {c} FROM {t} WHERE typeof({c}) = 'text' LIMIT {SHAPE_SAMPLE}"
            )
            if len(v) <= MAX_VALUE_CHARS
        ]
        if sample:
            counts = Counter(value_shape(v) for v in sample)
            shapes = [[s, round(n / len(sample), 4)] for s, n in counts.most_common(TOP_SHAPES)]

    return ColumnProfile(
        name=name,
        declared_type=declared_type,
        storage_types=storage,
        row_count=row_count,
        null_count=null_count,
        distinct_count=distinct,
        is_unique=is_unique,
        is_primary_key=is_pk,
        num_min=num_min,
        num_max=num_max,
        text_min=text_min,
        text_max=text_max,
        text_len_min=len_min,
        text_len_max=len_max,
        values=values,
        values_complete=complete,
        shapes=shapes,
    )


def profile_table(
    conn: sqlite3.Connection, table: str, max_values: int = MAX_VALUES
) -> TableProfile:
    cur = conn.cursor()
    (row_count,) = cur.execute(f"SELECT COUNT(*) FROM {quote(table)}").fetchone()

    # PRAGMA table_info rows: (cid, name, type, notnull, default, pk_position)
    info = cur.execute(f"PRAGMA table_info({quote(table)})").fetchall()
    primary_key = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5] > 0]

    # PRAGMA foreign_key_list rows: (id, seq, ref_table, from, to, ...). One id per FK;
    # a multi-column FK spans several rows with the same id.
    fks: dict[int, dict] = {}
    for fk_id, _seq, ref_table, frm, to, *_ in cur.execute(
        f"PRAGMA foreign_key_list({quote(table)})"
    ):
        fk = fks.setdefault(fk_id, {"columns": [], "ref_table": ref_table, "ref_columns": []})
        fk["columns"].append(frm)
        fk["ref_columns"].append(to)  # may be None: "references the parent's primary key"

    columns = [
        profile_column(conn, table, r[1], r[2] or "", row_count, r[1] in primary_key, max_values)
        for r in info
    ]
    return TableProfile(
        name=table,
        row_count=row_count,
        primary_key=primary_key,
        foreign_keys=list(fks.values()),
        columns=columns,
    )


def profile_database(path: Path | str, max_values: int = MAX_VALUES) -> Catalog:
    """Profile every table in a SQLite file. Read-only; returns the catalog (not saved)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    start = time.perf_counter()
    conn = _connect_read_only(path)
    try:
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        profiles = [profile_table(conn, t, max_values) for t in tables]
    finally:
        conn.close()
    st = path.stat()
    return Catalog(
        database=path.stem,
        source_path=str(path.resolve()),
        source_size=st.st_size,
        source_mtime=st.st_mtime,
        profiled_at=datetime.now(UTC).isoformat(timespec="seconds"),
        elapsed_s=round(time.perf_counter() - start, 3),
        settings={
            "max_values": max_values,
            "top_values": TOP_VALUES,
            "shape_sample": SHAPE_SAMPLE,
            "max_value_chars": MAX_VALUE_CHARS,
        },
        tables=profiles,
    )
