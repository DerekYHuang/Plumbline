"""Schema rule: tables and columns that don't exist. 6 must-pass, 6 must-fail queries."""

import pytest

from plumbline import check_sql


def rules(sql, cat):
    return [f.rule for f in check_sql(sql, cat).findings if f.rule.startswith("SCHEMA")]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT School FROM schools",
        "SELECT s.School, f.\"Enrollment (K-12)\" FROM schools s JOIN frpm f "
        "ON s.CDSCode = f.CDSCode",
        "SELECT county FROM SCHOOLS",  # SQLite ignores case, so must we
        "SELECT County, COUNT(*) AS n FROM schools GROUP BY County ORDER BY n DESC",  # alias
        "WITH big AS (SELECT CDSCode AS code FROM frpm WHERE \"Enrollment (K-12)\" > 200) "
        "SELECT code FROM big",  # CTE columns are not table columns
        "SELECT School FROM schools WHERE CDSCode IN (SELECT cds FROM satscores)",
    ],
)
def test_valid_schema_passes(school_catalog, sql):
    assert rules(sql, school_catalog) == []


def test_unknown_table_suggests_closest(school_catalog):
    r = check_sql("SELECT * FROM satscore", school_catalog)
    (f,) = r.findings
    assert f.rule == "SCHEMA_TABLE" and f.severity == "error"
    assert "'satscores'" in f.hint
    assert not r.passed


def test_unknown_column_suggests_closest(school_catalog):
    (f,) = check_sql("SELECT FundingTypes FROM schools", school_catalog).findings
    assert f.rule == "SCHEMA_COLUMN"
    assert "'FundingType'" in f.hint


def test_unknown_prefixed_column(school_catalog):
    sql = "SELECT s.Cnty FROM schools s JOIN frpm f ON s.CDSCode = f.CDSCode"
    (f,) = check_sql(sql, school_catalog).findings
    assert f.rule == "SCHEMA_COLUMN"
    assert "'County'" in f.hint
    assert f.evidence["tables"] == ["schools"]


def test_column_from_unjoined_table_points_to_it(school_catalog):
    (f,) = check_sql("SELECT NumTstTakr FROM schools", school_catalog).findings
    assert f.rule == "SCHEMA_COLUMN"
    assert "satscores" in f.hint and "'NumTstTakr'" in f.hint


def test_unknown_column_in_where(school_catalog):
    assert rules("SELECT School FROM schools WHERE Funding = 'x'", school_catalog) == [
        "SCHEMA_COLUMN"
    ]


def test_unknown_column_in_subquery(school_catalog):
    sql = "SELECT School FROM schools WHERE CDSCode IN (SELECT cds_code FROM satscores)"
    assert rules(sql, school_catalog) == ["SCHEMA_COLUMN"]


def test_outer_column_in_subquery_is_valid_sql(school_catalog):
    # `cdscode` isn't in satscores, but SQL resolves it to the OUTER schools.CDSCode, so
    # this runs. (It is a logic bug -- always true -- but not a schema error.)
    sql = "SELECT School FROM schools WHERE CDSCode IN (SELECT cdscode FROM satscores)"
    assert rules(sql, school_catalog) == []


def test_alias_reused_for_two_tables(school_catalog):
    # M0 q236/q212: SQLite runs this and silently takes each column from one of the two.
    sql = ("SELECT AVG(d.\"Enrollment (K-12)\") FROM schools s "
           "JOIN frpm d ON d.CDSCode = s.CDSCode JOIN satscores d ON d.cds = s.CDSCode")
    found = rules(sql, school_catalog)
    assert found == ["SCHEMA_ALIAS"]  # and no bogus "unknown column" for d."Enrollment..."


def test_same_mistake_reported_once(school_catalog):
    sql = "SELECT Cnty FROM schools WHERE Cnty = 'Orange' ORDER BY Cnty"
    assert rules(sql, school_catalog) == ["SCHEMA_COLUMN"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELEC School FROM",
        "SELECT School FROM schools WHERE County IN ('Orange', 'Kern",  # unterminated string
    ],
)
def test_unparseable_sql_fails_without_findings(school_catalog, sql):
    r = check_sql(sql, school_catalog)
    assert r.parse_error and not r.passed and r.findings == []
