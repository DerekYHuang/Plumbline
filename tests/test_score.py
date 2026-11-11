"""Tests for the labelling and summary logic in bench.score."""

import pytest

from bench.execute import ExecResult
from bench.score import label_for, summarize

GOLD = ExecResult(ok=True, rows=[(3,)])


@pytest.mark.parametrize(
    "sql, pred, gold, expected",
    [
        ("SELECT 3", ExecResult(ok=True, rows=[(3,)]), GOLD, "correct"),
        ("SELECT 0", ExecResult(ok=True, rows=[(0,)]), GOLD, "wrong_result"),
        ("SELECT x", ExecResult(ok=False, error="no such column: x"), GOLD, "exec_error"),
        ("SELECT 1", ExecResult(ok=False, error="timeout"), GOLD, "timeout"),
        (None, None, GOLD, "no_sql"),
        # A broken gold query trumps everything: it's not the model's fault.
        ("SELECT 3", ExecResult(ok=True, rows=[(3,)]),
         ExecResult(ok=False, error="boom"), "gold_error"),
    ],
)
def test_label_for(sql, pred, gold, expected):
    assert label_for(sql, pred, gold) == expected


def test_summarize_excludes_gold_errors_from_accuracy():
    rows = [
        {"label": "correct", "difficulty": "simple"},
        {"label": "wrong_result", "difficulty": "simple"},
        {"label": "correct", "difficulty": "challenging"},
        {"label": "gold_error", "difficulty": "challenging"},
    ]
    s = summarize(rows)
    assert s["n_questions"] == 4
    assert s["n_scored"] == 3
    assert s["accuracy_pct"] == pytest.approx(66.67)
    assert s["accuracy_by_difficulty_pct"] == {"challenging": 100.0, "simple": 50.0}
    assert s["label_counts"]["gold_error"] == 1
    assert s["label_counts"]["timeout"] == 0
