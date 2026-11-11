"""Tests for bench.execute against a throwaway SQLite database."""

import sqlite3

import pytest

from bench.execute import results_match, run_sql


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "toy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE trips (id INTEGER PRIMARY KEY, status TEXT, fare REAL);
        INSERT INTO trips VALUES (1, 'Completed', 10.0), (2, 'Completed', 5.5),
                                 (3, 'Cancelled', 0.0);
        """
    )
    conn.commit()
    conn.close()
    return path


def test_run_sql_returns_rows(db):
    res = run_sql(db, "SELECT id FROM trips WHERE status = 'Completed' ORDER BY id")
    assert res.ok
    assert res.rows == [(1,), (2,)]


def test_wrong_filter_value_runs_fine_but_returns_nothing(db):
    # The exact failure Plumbline exists to catch: valid SQL, wrong answer.
    res = run_sql(db, "SELECT COUNT(*) FROM trips WHERE status = 'Finished'")
    assert res.ok
    assert res.rows == [(0,)]


def test_syntax_error_is_reported_not_raised(db):
    res = run_sql(db, "SELEC id FROM trips")
    assert not res.ok
    assert "syntax error" in res.error


def test_unknown_column_is_reported(db):
    res = run_sql(db, "SELECT price FROM trips")
    assert not res.ok
    assert "no such column" in res.error


def test_database_is_read_only(db):
    res = run_sql(db, "DELETE FROM trips")
    assert not res.ok
    assert "readonly" in res.error.replace(" ", "").lower()
    assert run_sql(db, "SELECT COUNT(*) FROM trips").rows == [(3,)]


def test_timeout_aborts_runaway_query(db):
    # A recursive CTE that would count forever.
    forever = (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
        "SELECT MAX(x) FROM c"
    )
    res = run_sql(db, forever, timeout_s=0.2)
    assert not res.ok
    assert res.timed_out


@pytest.mark.parametrize(
    "pred, gold, expected",
    [
        ([(1,), (2,)], [(2,), (1,)], True),       # order ignored
        ([(1,), (1,), (2,)], [(1,), (2,)], True),  # duplicates ignored
        ([(1, "a")], [("a", 1)], False),           # column order matters
        ([(1, "a")], [(1,)], False),               # extra column is wrong
        ([], [(0,)], False),                       # empty vs zero is wrong
        ([], [], True),
    ],
)
def test_results_match_follows_bird_ex(pred, gold, expected):
    assert results_match(pred, gold) is expected
