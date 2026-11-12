"""Paths and default settings for the benchmark, in one place.

Everything can be overridden with environment variables so nobody has to edit
code to point at their own copy of the data:

    PLUMBLINE_BIRD_DIR   folder holding the BIRD Mini-Dev files
                         (default: <repo>/data/bird_mini_dev)
    OLLAMA_HOST          Ollama server URL (default: http://localhost:11434)

Expected layout under PLUMBLINE_BIRD_DIR (see README "Get the data"):

    mini_dev_sqlite.jsonl          500 questions + official prompts + gold SQL
    dev_databases/                 the 11 SQLite databases, any nesting is fine;
        california_schools/        we search for <db_id>.sqlite recursively
            california_schools.sqlite
        ...
"""

from __future__ import annotations

import os
from pathlib import Path

# bench/config.py -> bench/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent

BIRD_DIR = Path(os.environ.get("PLUMBLINE_BIRD_DIR", REPO_ROOT / "data" / "bird_mini_dev"))
QUESTIONS_FILE = BIRD_DIR / "mini_dev_sqlite.jsonl"
DATABASES_DIR = BIRD_DIR / "dev_databases"

# Raw outputs of each run (gitignored). Each run gets its own subfolder.
RUNS_DIR = REPO_ROOT / "bench" / "runs"
# Small, curated summaries we *do* commit.
RESULTS_DIR = REPO_ROOT / "results"

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Model under test for M0. Pin the exact tag so results are reproducible;
# record `ollama show <model>` output alongside results when publishing.
DEFAULT_MODEL = "qwen2.5-coder:7b"

# Generation settings. temperature=0 + a fixed seed makes runs as repeatable as
# Ollama allows; the blueprint still asks for 3 runs to measure the spread.
DEFAULT_OPTIONS = {
    "temperature": 0.0,
    "seed": 42,
    # BIRD prompts include the full schema text (~2-4k tokens). 8k context
    # leaves room for the model's step-by-step reasoning and the answer.
    "num_ctx": 8192,
    # Hard cap on answer length so a rambling model can't stall the run.
    "num_predict": 1024,
}

# Seconds before a single SQL query is aborted (both gold and predicted).
# Some model-written queries are accidental cross joins that never finish.
# 60 s, not 30: on 2026-11-11 the slowest GOLD query took 28 s on a home PC,
# too close to 30 s -- a busy machine could have timed out a correct answer.
SQL_TIMEOUT_S = 60.0
