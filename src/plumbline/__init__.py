"""Plumbline: catch AI-written SQL that runs fine but returns the wrong answer.

The core library (profiler, catalog, rules) lands in milestones M1 and M2.
For M0 the package only holds the version string and the CLI entry point;
the benchmark baseline code lives in the top-level `bench/` package.
"""

__version__ = "0.0.1"
