"""Value rule: filters on values the column never holds. 8 must-pass, 10 must-fail queries."""

import pytest

from plumbline import check_sql


def value_findings(sql, cat):
    return [f for f in check_sql(sql, cat).findings if f.rule.startswith(("VALUE", "LIKE"))]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT School FROM schools WHERE FundingType = 'Locally funded'",
        "SELECT School FROM schools WHERE 'Orange' = County",  # literal on the left
        "SELECT School FROM schools WHERE County IN ('Orange', 'Kern')",
        "SELECT School FROM schools WHERE FundingType LIKE 'locally%'",  # LIKE ignores case
        "SELECT School FROM schools WHERE FundingType LIKE 'Locally Funded'",
        "SELECT School FROM schools WHERE CDSCode = '01000000000000'",  # right format
        "SELECT School FROM schools WHERE LOWER(FundingType) = 'locally funded'",  # wrapped
        "SELECT cds FROM satscores WHERE NumTstTakr = 99999",  # too many numbers to judge
    ],
)
def test_plausible_values_pass(school_catalog, sql):
    assert value_findings(sql, school_catalog) == []


def test_wrong_capitalisation(school_catalog):
    sql = "SELECT School FROM schools s WHERE s.FundingType = 'Locally Funded'"
    r = check_sql(sql, school_catalog)
    (f,) = r.findings
    assert (f.rule, f.severity) == ("VALUE_CASE", "error")
    assert f.hint == "Use 'Locally funded'."
    assert f.evidence["actual"] == "Locally funded"
    assert not r.passed


def test_trailing_space_counts_as_case_mismatch(school_catalog):
    (f,) = value_findings("SELECT 1 FROM schools WHERE County = 'Orange '", school_catalog)
    assert f.rule == "VALUE_CASE"


def test_value_that_does_not_exist_suggests_closest(school_catalog):
    (f,) = value_findings("SELECT 1 FROM schools WHERE County = 'Orang'", school_catalog)
    assert (f.rule, f.severity) == ("VALUE_MISSING", "error")
    assert "'Orange'" in f.hint


def test_value_with_nothing_close_lists_real_values(school_catalog):
    (f,) = value_findings("SELECT 1 FROM schools WHERE Virtual = 'Yes'", school_catalog)
    assert f.rule == "VALUE_MISSING"
    assert "'F'" in f.hint and "'N'" in f.hint


def test_each_bad_item_of_in_list(school_catalog):
    sql = "SELECT 1 FROM schools WHERE County IN ('Orange', 'Marin', 'kern')"
    got = sorted(
        (f.rule, f.severity, f.evidence["value"]) for f in value_findings(sql, school_catalog)
    )
    # 'kern' drops the Kern rows: error. 'Marin' just never occurs while 'Orange' does,
    # so the list still works: warning only (the gold-query q496 pattern).
    assert got == [("VALUE_CASE", "error", "kern"), ("VALUE_MISSING", "warning", "Marin")]


def test_in_list_with_no_real_value_is_an_error(school_catalog):
    (f,) = value_findings(
        "SELECT 1 FROM schools WHERE County IN ('Marin', 'Napa')", school_catalog
    )[:1]
    assert (f.rule, f.severity) == ("VALUE_MISSING", "error")


def test_code_missing_leading_zero(school_catalog):
    (f,) = value_findings("SELECT 1 FROM schools WHERE CDSCode = '1000000000000'", school_catalog)
    assert (f.rule, f.severity) == ("VALUE_FORMAT", "error")  # proven: length out of range
    assert "'01000000000000'" in f.hint


def test_numeric_literal_against_text_code(school_catalog):
    (f,) = value_findings("SELECT 1 FROM schools WHERE CDSCode = 1000000000000", school_catalog)
    assert f.rule == "VALUE_FORMAT"


def test_date_in_wrong_format_is_a_warning(school_catalog):
    (f,) = value_findings("SELECT 1 FROM schools WHERE OpenDate = '1999/01/01'", school_catalog)
    assert (f.rule, f.severity) == ("VALUE_FORMAT", "warning")
    assert "9{4}-9{2}-9{2}" in f.message


def test_like_pattern_matching_nothing(school_catalog):
    sql = "SELECT 1 FROM schools WHERE FundingType LIKE '%charter%'"
    (f,) = value_findings(sql, school_catalog)
    assert (f.rule, f.severity) == ("LIKE_NO_MATCH", "error")


def test_not_equal_to_missing_value_is_only_a_warning(school_catalog):
    r = check_sql("SELECT 1 FROM schools WHERE County != 'Marin'", school_catalog)
    (f,) = r.findings
    assert (f.rule, f.severity) == ("VALUE_MISSING", "warning")
    assert "excludes nothing" in f.message
    assert r.passed  # warnings don't fail a query
