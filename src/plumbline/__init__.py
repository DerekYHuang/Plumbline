"""Plumbline: catch AI-written SQL that runs fine but returns the wrong answer.

    profiler.py   M1: scan a database once, record what each column holds
    catalog.py    M1: the saved result (one JSON file per database)
    rules/        M2: the checks, which read only the catalog
    cli.py        the `plumbline` command

The benchmark code lives in the top-level `bench/` package, not here.
"""

__version__ = "0.0.1"
