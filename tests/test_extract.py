"""Tests for bench.extract.extract_sql on realistic model answers."""

from bench.extract import extract_sql


def test_takes_last_sql_block():
    answer = (
        "First attempt:\n```sql\nSELECT 1\n```\n"
        "Wait, that's wrong. Final:\n```sql\nSELECT name FROM t;\n```"
    )
    assert extract_sql(answer) == "SELECT name FROM t"


def test_strips_comment_lines():
    answer = "```sql\n-- Your SQL query\nSELECT a\nFROM t\n```"
    assert extract_sql(answer) == "SELECT a\nFROM t"


def test_unlabelled_block_that_looks_like_sql():
    assert extract_sql("Here:\n```\nSELECT 1\n```") == "SELECT 1"


def test_ignores_unlabelled_non_sql_block():
    assert extract_sql("```\nprint('hi')\n```") is None


def test_bare_select_without_code_block():
    answer = "The answer is:\nSELECT COUNT(*) FROM t WHERE x = 1;\nDone."
    assert extract_sql(answer) == "SELECT COUNT(*) FROM t WHERE x = 1"


def test_with_cte_is_recognised():
    answer = "```sql\nWITH a AS (SELECT 1) SELECT * FROM a\n```"
    assert extract_sql(answer).startswith("WITH a AS")


def test_nothing_found():
    assert extract_sql("I don't know.") is None
    assert extract_sql("```sql\n-- only a comment\n```") is None
