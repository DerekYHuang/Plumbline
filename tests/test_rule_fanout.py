"""Fan-out rule (grainguard adapter): aggregates inflated by a join. 5 pass, 5 fail.

In the fixture, frpm has one row per school (CDSCode is unique) and satscores has two
(rtype S and D), so joining schools -> satscores repeats every school twice.
"""

import pytest

from plumbline import check_sql
from plumbline.rules import fanout

pytestmark = pytest.mark.skipif(not fanout.available(), reason="grainguard not installed")


def fan(sql, cat):
    return [f.rule for f in check_sql(sql, cat).findings if f.rule in ("FANOUT", "CHASM")]


@pytest.mark.parametrize(
    "sql",
    [
        # one-to-one join: no repetition
        "SELECT SUM(f.\"Enrollment (K-12)\") FROM schools s JOIN frpm f ON s.CDSCode = f.CDSCode",
        # counting the many side is what it looks like
        "SELECT COUNT(*) FROM satscores",
        # DISTINCT is immune to duplicates
        "SELECT COUNT(DISTINCT s.CDSCode) FROM schools s JOIN satscores t ON t.cds = s.CDSCode",
        # MAX is immune to duplicates
        "SELECT MAX(f.\"Enrollment (K-12)\") FROM frpm f JOIN satscores t ON t.cds = f.CDSCode",
        # no join at all
        "SELECT County, COUNT(*) FROM schools GROUP BY County",
    ],
)
def test_safe_aggregates_pass(school_catalog, sql):
    assert fan(sql, school_catalog) == []


@pytest.mark.parametrize(
    "sql",
    [
        # the q403 shape from M0: counting patients after joining their lab rows
        "SELECT s.County, COUNT(s.CDSCode) FROM schools s JOIN satscores t "
        "ON t.cds = s.CDSCode GROUP BY s.County",
        "SELECT SUM(f.\"Enrollment (K-12)\") FROM frpm f JOIN satscores t ON t.cds = f.CDSCode",
        "SELECT AVG(f.\"Free Meal Count (K-12)\") FROM frpm f JOIN satscores t "
        "ON t.cds = f.CDSCode",
        "SELECT COUNT(*) FROM schools s JOIN satscores t ON t.cds = s.CDSCode "
        "WHERE s.County = 'Orange'",
        "SELECT SUM(f.\"Enrollment (K-12)\") FROM frpm f LEFT JOIN satscores t "
        "ON t.cds = f.CDSCode WHERE f.CDSCode = '01000000000000'",
    ],
)
def test_inflated_aggregates_fail(school_catalog, sql):
    r = check_sql(sql, school_catalog)
    assert fan(sql, school_catalog), [f.rule for f in r.findings]
    f = next(f for f in r.findings if f.rule == "FANOUT")
    assert f.severity == "error" and f.hint and f.evidence["source"].startswith("grainguard")
    assert not r.passed
