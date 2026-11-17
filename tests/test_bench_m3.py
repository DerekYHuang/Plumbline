"""Tests for the M3 benchmark helpers: confidence intervals and mutation operators."""

import pytest
import sqlglot

from bench import mutate
from bench.stats import fmt, rate, wilson


def test_wilson_matches_reported_baseline_interval():
    lo, hi = wilson(46, 500)  # the M0 baseline: 9.2%, reported as 7.0-12.1%
    assert round(100 * lo, 1) == 7.0 and round(100 * hi, 1) == 12.1


def test_wilson_zero_successes_stays_inside_0_100():
    lo, hi = wilson(0, 500)
    assert lo == 0.0 and 0 < hi < 0.01


def test_rate_and_fmt():
    r = rate(68, 308)
    assert r["pct"] == 22.1
    assert fmt(r).startswith("68/308 = 22.1% (")
    assert fmt(rate(0, 0)) == "n/a"


def mutated(op, sql):
    res = getattr(mutate, op)(sqlglot.parse_one(sql, read="sqlite"))
    return None if res is None else (res[0].sql(dialect="sqlite"), res[1])


@pytest.mark.parametrize(
    "op, sql, expect",
    [
        ("case_flip", "SELECT 1 FROM s WHERE f = 'Locally funded'", "'Locally Funded'"),
        ("case_flip", "SELECT 1 FROM s WHERE f = 'Ab Cd'", "'ab cd'"),
        ("value_typo", "SELECT 1 FROM s WHERE f = 'Directly'", "'Diretly'"),
        ("leading_zero", "SELECT 1 FROM s WHERE c = '0123'", "'123'"),
        ("column_typo", "SELECT 1 FROM s WHERE County = 'x'", '"Count"'),
        ("table_typo", "SELECT 1 FROM schools", '"school"'),
        ("drop_distinct", "SELECT COUNT(DISTINCT a.x) FROM a JOIN b ON a.id = b.id", "COUNT(a.x)"),
    ],
)
def test_operators_inject_exactly_the_described_fault(op, sql, expect):
    out, _ = mutated(op, sql)
    assert expect in out


@pytest.mark.parametrize(
    "op, sql",
    [
        ("case_flip", "SELECT 1 FROM s WHERE n = 5"),  # no string filter
        ("leading_zero", "SELECT 1 FROM s WHERE c = '123'"),  # no leading zero
        ("drop_distinct", "SELECT COUNT(DISTINCT x) FROM a"),  # no join: no fan-out
    ],
)
def test_operators_skip_queries_they_dont_fit(op, sql):
    assert mutated(op, sql) is None
