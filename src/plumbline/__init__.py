"""Plumbline: catch AI-written SQL that runs fine but returns the wrong answer.

    profiler.py   M1: scan a database once, record what each column holds
    catalog.py    M1: the saved result (one JSON file per database)
    query.py      M2: parse SQL and tie every column to its real table
    rules/        M2: the checks (schema, value, fan-out), which read only the catalog
    check.py      M2: check_sql(), runs every rule on one query
    findings.py   M2: what a check returns
    cli.py        the `plumbline` command

The benchmark code lives in the top-level `bench/` package, not here.
"""

from plumbline.catalog import Catalog
from plumbline.check import check_sql
from plumbline.findings import CheckResult, Finding

__version__ = "0.1.0"
__all__ = ["Catalog", "CheckResult", "Finding", "check_sql", "__version__"]
