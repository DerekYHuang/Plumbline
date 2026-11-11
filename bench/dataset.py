"""Load BIRD Mini-Dev questions and locate each question's SQLite database.

We read `baseline_inference/mini_dev_sqlite.jsonl` from the Hugging Face repo
(birdsql/bird_mini_dev). Each line is one JSON object with these fields:

    question_id   int 0..499
    db_id         which of the 11 databases the question is about
    question      natural-language question
    evidence      optional hint text ("external knowledge"); may be ""
    SQL           the gold (reference) query -- for scoring ONLY, never shown
                  to the model
    difficulty    simple | moderate | challenging
    dialect       "sqlite"
    prompt        the exact, official prompt text BIRD used for its published
                  baselines (schema + question + instructions)

Using the official `prompt` instead of writing our own means our baseline
accuracy is directly comparable to the numbers on the dataset card.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from bench import config


@dataclass(frozen=True)
class Question:
    question_id: int
    db_id: str
    question: str
    evidence: str
    gold_sql: str
    difficulty: str
    prompt: str


def load_questions(path: Path | None = None) -> list[Question]:
    """Read every question from the JSONL file, in question_id order."""
    path = path or config.QUESTIONS_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Questions file not found: {path}\n"
            "Download it first -- see README section 'Get the data'."
        )
    questions = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            questions.append(
                Question(
                    question_id=row["question_id"],
                    db_id=row["db_id"],
                    question=row["question"],
                    evidence=row.get("evidence", ""),
                    gold_sql=row["SQL"],
                    difficulty=row["difficulty"],
                    # The plain data/ file has no prompt; only the
                    # baseline_inference/ file does. Fail loudly if missing.
                    prompt=row["prompt"],
                )
            )
    questions.sort(key=lambda q: q.question_id)
    return questions


@cache
def _index_databases(root: Path) -> dict[str, Path]:
    """Map db_id -> path for every *.sqlite file under root (searched once).

    The official zip nests the databases a few folders deep, and people unzip
    things differently, so we search instead of hard-coding the nesting.
    """
    return {p.stem: p for p in root.rglob("*.sqlite")}


def find_database(db_id: str, root: Path | None = None) -> Path:
    """Return the path to `<db_id>.sqlite` anywhere under the databases folder."""
    root = root or config.DATABASES_DIR
    index = _index_databases(root)
    if db_id not in index:
        raise FileNotFoundError(
            f"No {db_id}.sqlite under {root}. Found: {sorted(index) or 'nothing'}.\n"
            "See README section 'Get the data'."
        )
    return index[db_id]
