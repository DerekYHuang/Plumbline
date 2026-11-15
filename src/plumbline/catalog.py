"""The catalog: what Plumbline knows about a database, saved as one JSON file.

The profiler (profiler.py) fills it in once by scanning the database. The M2 checks then
read *only the catalog*, never the database itself, so a check is fast and the database
can be offline or large.

Shape of the data, top to bottom:

    Catalog                one per database file
      TableProfile         one per table: row count, primary key, foreign keys
        ColumnProfile      one per column: the statistics the checks need

Everything is a plain dataclass so it can be written to JSON and read back without any
custom code beyond `to_dict` / `from_dict`. JSON (not Parquet) because the file is small,
diffable, and readable in any editor -- useful when you want to see *why* a check fired.

Name lookups (`table()`, `column()`) ignore case, because SQLite itself does:
`SELECT fundingtype FROM Schools` works on a column declared as `FundingType`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Bump when the JSON layout changes, so an old catalog file is rejected instead of
# being misread. `load` checks it.
CATALOG_VERSION = 1


@dataclass
class ColumnProfile:
    """Statistics for one column. All counts are exact (full scan), not estimates."""

    name: str
    declared_type: str  # the type written in CREATE TABLE, e.g. "TEXT"; may be ""
    # SQLite is dynamically typed: a column declared INTEGER can still hold text.
    # This records what is *actually* stored, e.g. {"integer": 990, "text": 10}.
    storage_types: dict[str, int]
    row_count: int
    null_count: int
    distinct_count: int  # distinct non-null values
    is_unique: bool  # every row has a value and no value repeats
    is_primary_key: bool
    # Smallest / largest *number* stored (ints and reals only). None if no numbers.
    num_min: float | int | None = None
    num_max: float | int | None = None
    # Smallest / largest *text* value in sort order. For ISO dates ("1996-01-05")
    # sort order is date order, so this doubles as the date range.
    text_min: str | None = None
    text_max: str | None = None
    # Shortest / longest text value, in characters.
    text_len_min: int | None = None
    text_len_max: int | None = None
    # [value, how many rows] pairs, most common first.
    # values_complete=True  -> this is EVERY distinct value (low-cardinality column).
    #                          The value check can say "this value does not exist".
    # values_complete=False -> only the most common ones, kept for repair hints.
    values: list[list[Any]] = field(default_factory=list)
    values_complete: bool = False
    # Text "shapes": each character replaced by its class, runs compressed.
    #   "01611920113902" -> "9{14}"      "1996-01-05" -> "9{4}-9{2}-9{2}"
    #   "Locally funded" -> "Aa{6} a{6}"
    # [shape, share of sampled distinct values], most common first. Lets a check
    # notice a 13-digit code in a column where every code has 14 digits.
    shapes: list[list[Any]] = field(default_factory=list)

    @property
    def null_rate(self) -> float:
        return self.null_count / self.row_count if self.row_count else 0.0


@dataclass
class TableProfile:
    name: str
    row_count: int
    primary_key: list[str]
    # Declared foreign keys: {"columns": [...], "ref_table": "...", "ref_columns": [...]}
    foreign_keys: list[dict[str, Any]]
    columns: list[ColumnProfile]

    def column(self, name: str) -> ColumnProfile | None:
        """Find a column by name, ignoring case (as SQLite does)."""
        low = name.lower()
        return next((c for c in self.columns if c.name.lower() == low), None)


@dataclass
class Catalog:
    database: str  # short name, e.g. "california_schools"
    source_path: str
    source_size: int  # bytes; with source_mtime, lets us notice the file changed
    source_mtime: float
    profiled_at: str  # ISO timestamp, UTC
    elapsed_s: float  # how long profiling took
    settings: dict[str, Any]  # profiler settings used, for reproducibility
    tables: list[TableProfile]
    version: int = CATALOG_VERSION

    # ---- lookups ---------------------------------------------------------
    def table(self, name: str) -> TableProfile | None:
        """Find a table by name, ignoring case."""
        low = name.lower()
        return next((t for t in self.tables if t.name.lower() == low), None)

    def column(self, table: str, column: str) -> ColumnProfile | None:
        t = self.table(table)
        return t.column(column) if t else None

    def is_stale(self) -> bool:
        """True if the database file changed since it was profiled.

        A cheap guard, not a guarantee: size and modification time only.
        """
        p = Path(self.source_path)
        if not p.exists():
            return True
        st = p.stat()
        return st.st_size != self.source_size or st.st_mtime != self.source_mtime

    # ---- JSON in / out ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Catalog:
        if d.get("version") != CATALOG_VERSION:
            raise ValueError(
                f"Catalog version {d.get('version')} is not supported (expected "
                f"{CATALOG_VERSION}). Re-run `plumbline profile` to rebuild it."
            )
        tables = [
            TableProfile(
                name=t["name"],
                row_count=t["row_count"],
                primary_key=t["primary_key"],
                foreign_keys=t["foreign_keys"],
                columns=[ColumnProfile(**c) for c in t["columns"]],
            )
            for t in d["tables"]
        ]
        return cls(**{**d, "tables": tables})

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False keeps accented values readable ("Málaga", not "M\\u00e1laga").
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | str) -> Catalog:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
