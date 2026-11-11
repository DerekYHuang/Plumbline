"""Tests for bench.dataset using tiny fake files (no real BIRD data needed)."""

import json
import sqlite3

import pytest

from bench.dataset import find_database, load_questions


def _row(qid, db="toy"):
    return {
        "question_id": qid,
        "db_id": db,
        "question": f"q{qid}?",
        "evidence": "",
        "SQL": "SELECT 1",
        "difficulty": "simple",
        "dialect": "sqlite",
        "prompt": f"PROMPT {qid}",
    }


def test_load_questions_sorted_by_id(tmp_path):
    path = tmp_path / "q.jsonl"
    # Written out of order, with a blank line, to prove we sort and skip blanks.
    path.write_text(
        "\n".join(json.dumps(_row(i)) for i in [2, 0, 1]) + "\n\n", encoding="utf-8"
    )
    qs = load_questions(path)
    assert [q.question_id for q in qs] == [0, 1, 2]
    assert qs[0].gold_sql == "SELECT 1"
    assert qs[0].prompt == "PROMPT 0"


def test_load_questions_missing_file_explains_what_to_do(tmp_path):
    with pytest.raises(FileNotFoundError, match="Get the data"):
        load_questions(tmp_path / "nope.jsonl")


def test_find_database_searches_nested_folders(tmp_path):
    nested = tmp_path / "a" / "b" / "toy"
    nested.mkdir(parents=True)
    sqlite3.connect(nested / "toy.sqlite").close()
    assert find_database("toy", tmp_path) == nested / "toy.sqlite"


def test_find_database_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="toy.sqlite"):
        find_database("toy", tmp_path)
